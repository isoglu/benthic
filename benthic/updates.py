"""Update-Prüfung: vergleicht die eigene Version mit einem Release-Manifest.

Manifest (JSON, z. B. neben dem DMG veröffentlicht, erzeugt von
macos/make_release.sh)::

    {"version": "1.0.1", "url": "https://…/Benthic-1.0.1.dmg", "sha256": "…",
     "size": 44000000, "published": "2026-11-02", "minimum_os": "13.0",
     "notes": ["Neue Prüfansicht", "Schnellere Titelsuche"],
     "notes_en": ["New check view", "Faster title search"]}

``notes`` ist deutsch (so lesen es auch Versionen bis 1.0), ``notes_en`` englisch.

Seit 1.3 aktualisiert sich die App selbst: ``download()`` lädt das DMG aus dem
Manifest in ``updates/`` im Datenordner und prüft Größe und SHA-256; die native App
(BenthicApp.swift, ``benthicUpdate``) hängt es ein, prüft Bundle-Kennung, Version
und Signatur, tauscht die App aus und startet sie neu. Im Browser (Entwicklung) bleibt
es beim Link zum DMG.

Sicherheit: Das DMG muss vom selben Server kommen wie das Manifest, über HTTPS (nur
für lokale Tests auch HTTP auf 127.0.0.1 oder localhost), und seine Prüfsumme muss
zur Angabe im Manifest passen. Sonst wird nichts installiert.
"""

from __future__ import annotations

import hashlib
import platform
import re
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

import requests

from . import __version__, config
from .i18n import current as current_lang, tr

_cache: Dict[str, Tuple[float, Optional[Dict]]] = {}
_lock = threading.Lock()
CACHE_SECONDS = 6 * 3600


def parse_version(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3]) or (0,)


def _fetch(url: str, force: bool) -> Optional[Dict]:
    with _lock:
        hit = _cache.get(url)
        if hit and not force and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    data: Optional[Dict] = None
    try:
        r = requests.get(url, timeout=6, headers={"User-Agent": f"Benthic/{__version__} update-check",
                                                  "Cache-Control": "no-cache"})
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, dict) and j.get("version") and j.get("url"):
                data = j
    except (requests.exceptions.RequestException, ValueError):
        data = None
    with _lock:
        _cache[url] = (time.time(), data)
    return data


def check(force: bool = False) -> Dict:
    url = config.update_url()
    out = {"current": __version__, "configured": bool(url), "available": False,
           "default_url": config.build_update_url()}
    if not url:
        return out
    m = _fetch(url, force)
    if m is None:
        return {**out, "error": tr("Update-Server nicht erreichbar")}
    newer = parse_version(m["version"]) > parse_version(__version__)
    notes = (m.get("notes_en") if current_lang() == "en" else None) or m.get("notes") or []
    return {**out, "available": newer, "latest": str(m["version"]), "url": str(m["url"]),
            "published": m.get("published"), "notes": notes if isinstance(notes, list) else [str(notes)],
            "sha256": m.get("sha256"), "minimum_os": m.get("minimum_os")}


# --------------------------------------------------------------------------
# Herunterladen (für das Aktualisieren aus der App)
# --------------------------------------------------------------------------
_dl_lock = threading.Lock()
_dl: Dict = {"state": "idle"}
LOCAL_HOSTS = ("127.0.0.1", "localhost")


def updates_dir() -> Path:
    d = config.data_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def status() -> Dict:
    with _dl_lock:
        return dict(_dl)


def _set(**fields) -> None:
    with _dl_lock:
        _dl.clear()
        _dl.update(fields)


def _allowed(url: str, manifest_url: str) -> bool:
    """Das DMG kommt vom Server des Manifests, über HTTPS (lokale Tests: HTTP auf 127.0.0.1)."""
    u, m = urlparse(url), urlparse(manifest_url)
    if not u.hostname or u.hostname != m.hostname:
        return False
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in LOCAL_HOSTS)


def _os_ok(minimum: Optional[str]) -> bool:
    mac = platform.mac_ver()[0]
    if not minimum or not mac:
        return True
    return parse_version(mac) >= parse_version(minimum)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cleanup() -> None:
    """Geladene DMGs bis zur laufenden Version und halbe Downloads entfernen."""
    d = config.data_dir() / "updates"
    if not d.is_dir():
        return
    for p in d.iterdir():
        m = re.match(r"Benthic-(.+)\.dmg(\.part)?$", p.name)
        if m and (m.group(2) or parse_version(m.group(1)) <= parse_version(__version__)):
            try:
                p.unlink()
            except OSError:
                pass


def download() -> Dict:
    """Neue Version laden und prüfen. Läuft im Hintergrund; Stand über ``status()``."""
    with _dl_lock:
        if _dl.get("state") == "downloading":
            return dict(_dl)
    info = check(force=True)
    manifest_url = config.update_url()
    if not info.get("available"):
        _set(state="error", error=info.get("error") or tr("Keine neuere Version gefunden."))
        return status()
    version, url, sha = info["latest"], info.get("url") or "", str(info.get("sha256") or "").lower()
    if not _allowed(url, manifest_url) or not re.fullmatch(r"[0-9a-f]{64}", sha) \
            or not re.fullmatch(r"[0-9A-Za-z.\-]+", version):
        _set(state="error", version=version, error=tr("Das Update passt nicht zum Update-Server und wurde nicht geladen."))
        return status()
    if not _os_ok(info.get("minimum_os")):
        _set(state="error", version=version,
             error=tr("Benthic {v} braucht macOS {os} oder neuer.", v=version, os=info.get("minimum_os")))
        return status()
    target = updates_dir() / f"Benthic-{version}.dmg"
    if target.is_file() and _sha256(target) == sha:
        _set(state="ready", version=version, path=str(target), received=target.stat().st_size,
             total=target.stat().st_size)
        return status()
    size = int(info.get("size") or 0) if str(info.get("size") or "").isdigit() else 0
    _set(state="downloading", version=version, received=0, total=size)
    threading.Thread(target=_run, args=(url, sha, size, target, version), daemon=True).start()
    return status()


def _run(url: str, sha: str, size: int, target: Path, version: str) -> None:
    part = target.with_name(target.name + ".part")
    h = hashlib.sha256()
    received = 0
    try:
        for old in target.parent.glob("Benthic-*.dmg*"):
            if old != target:
                old.unlink()
        with requests.get(url, stream=True, timeout=(10, 60),
                          headers={"User-Agent": f"Benthic/{__version__} update"}) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0) or size
            with part.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    if not chunk:
                        continue
                    f.write(chunk)
                    h.update(chunk)
                    received += len(chunk)
                    with _dl_lock:
                        _dl.update(received=received, total=total)
        if (size and received != size) or h.hexdigest() != sha:
            part.unlink(missing_ok=True)
            _set(state="error", version=version,
                 error=tr("Die geladene Datei stimmt nicht mit der Prüfsumme überein und wurde verworfen."))
            return
        part.replace(target)
        _set(state="ready", version=version, path=str(target), received=received, total=received)
    except (requests.exceptions.RequestException, OSError):
        part.unlink(missing_ok=True)
        _set(state="error", version=version, error=tr("Das Update ließ sich nicht laden. Bitte später noch einmal versuchen."))
