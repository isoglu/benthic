"""Projekt-Konfiguration für Benthic (Einstellungen, Datenordner, Pfade)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import List, Optional, Tuple

# Projektwurzel = ein Verzeichnis über dem Paketordner
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_settings_lock = threading.Lock()


def data_dir() -> Path:
    """Beschreibbares Datenverzeichnis.

    Im App-Betrieb setzt die native App ``BENTHIC_DATA_DIR`` auf
    ``~/Library/Application Support/Benthic``. Im Dev-Betrieb wird das
    Projektverzeichnis genutzt.
    """
    env = os.environ.get("BENTHIC_DATA_DIR")
    d = Path(env) if env else PROJECT_ROOT
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_download_dir() -> Path:
    d = data_dir() / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# Open-Access-Quellen
# --------------------------------------------------------------------------
ALL_PROVIDERS = ["arxiv", "openalex", "unpaywall", "europepmc", "semantic_scholar", "core", "elsevier", "crossref"]

# --------------------------------------------------------------------------
# Einstellungen
# --------------------------------------------------------------------------
# Version 2: Europe PMC und CORE gehören zur Standard-Kaskade (vorher fehlten sie).
# Version 3: Ersteinrichtung (setup_complete, institution).
# Version 4: Sprache der Oberfläche (language); bis 1.0 gab es nur Deutsch.
SETTINGS_VERSION = 4
LANGUAGES = ("en", "de")

DEFAULT_SETTINGS: dict = {
    "settings_version": SETTINGS_VERSION,
    "setup_complete": False,
    "language": "en",       # Sprache der Oberfläche, siehe i18n.py
    "institution": "",
    "extra_routes": False,  # Zusatzwege aus Plugins (siehe routes.py), nur nach Open Access
    "oa_providers": list(ALL_PROVIDERS),
    "unpaywall_email": "benthic-demo@example.org",
    "core_api_key": "",
    "openalex_api_key": "",  # ohne Key teilt sich jedes Netz ein kleines Tagesbudget bei OpenAlex
    "semanticscholar_api_key": "",  # ohne Key teilen sich alle ein kleines Kontingent bei Semantic Scholar
    "elsevier_api_key": "",
    "concurrency": 3,
    "skip_existing": True,
    "prefer_published": True,
    "download_dir": "",
    "library_sources": [],
    "proxy_prefix": "",
    "requester_name": "",
    "requester_affiliation": "",
    "update_url": "",
    "journal_jufo": True,   # Zeitschriften-Stufen von JUFO (Julkaisufoorumi) abfragen
    "retraction_watch": True,  # Projektquellen täglich auf Rückzüge prüfen (watch.py)
    "journal_lists": True,     # DOAJ-Listen (entfernte Zeitschriften, falsche Angaben), questionable.py
    "hijacked_check": False,   # Retraction Watch Hijacked Journal Checker (Lizenz offen, daher aus)
}


# --------------------------------------------------------------------------
# Einstellungen laden/speichern
# --------------------------------------------------------------------------
def settings_path() -> Path:
    return data_dir() / "settings.json"


def _clean(data: dict) -> dict:
    """Übernimmt bekannte Schlüssel und erzwingt deren Typen."""
    cleaned = json.loads(json.dumps(DEFAULT_SETTINGS))
    for key in DEFAULT_SETTINGS:
        if key in data and data[key] is not None:
            cleaned[key] = data[key]

    providers = [str(p) for p in cleaned["oa_providers"] if str(p) in ALL_PROVIDERS]
    # Reihenfolge immer nach ALL_PROVIDERS, damit die Kaskade stabil bleibt.
    cleaned["oa_providers"] = [p for p in ALL_PROVIDERS if p in providers] or list(DEFAULT_SETTINGS["oa_providers"])
    for key in ("extra_routes", "skip_existing", "prefer_published", "setup_complete", "journal_jufo",
                "retraction_watch", "journal_lists", "hijacked_check"):
        cleaned[key] = bool(cleaned[key])
    for key in ("unpaywall_email", "core_api_key", "openalex_api_key", "semanticscholar_api_key", "elsevier_api_key", "download_dir",
                "proxy_prefix", "requester_name", "requester_affiliation", "institution", "update_url"):
        cleaned[key] = str(cleaned[key]).strip()
    lang = str(cleaned["language"]).strip().lower()[:2]
    cleaned["language"] = lang if lang in LANGUAGES else DEFAULT_SETTINGS["language"]
    cleaned["settings_version"] = SETTINGS_VERSION
    try:
        cleaned["concurrency"] = max(1, min(int(cleaned["concurrency"]), 8))
    except (TypeError, ValueError):
        cleaned["concurrency"] = DEFAULT_SETTINGS["concurrency"]
    sources = cleaned["library_sources"]
    if isinstance(sources, str):
        sources = sources.splitlines()
    cleaned["library_sources"] = [str(s).strip() for s in sources if str(s).strip()]
    return cleaned


def _migrate(data: dict) -> dict:
    """Hebt ältere Einstellungen auf den aktuellen Stand.

    v1 → v2: Europe PMC und CORE fehlten in der Standard-Kaskade, obwohl sie
    implementiert und dokumentiert sind. Sie werden einmalig ergänzt; wer sie
    danach abwählt und speichert, behält diese Wahl (gespeichert als v2).
    """
    if not data:
        return data
    try:
        version = int(data.get("settings_version") or 1)
    except (TypeError, ValueError):
        version = 1
    if version < 2 and isinstance(data.get("oa_providers"), list):
        providers = [str(p) for p in data["oa_providers"]]
        for name in ("europepmc", "core"):
            if name not in providers:
                providers.append(name)
        data = dict(data, oa_providers=providers)
    if version < 3:
        # Bestehende Installationen sind bereits eingerichtet.
        data = dict(data, setup_complete=data.get("setup_complete", True))
    if version < 4:
        # Bis 1.0 war die Oberfläche deutsch; wer sie schon nutzt, behält Deutsch.
        data = dict(data, language=data.get("language") or "de")
    return data


def load_settings() -> dict:
    """Lädt die Einstellungen, Fallback auf Standardwerte."""
    sp = settings_path()
    data: dict = {}
    if sp.exists():
        try:
            raw = json.loads(sp.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (json.JSONDecodeError, OSError):
            pass
    return _clean(_migrate(data))


def save_settings(settings: dict) -> dict:
    """Speichert die Einstellungen und gibt die bereinigte Fassung zurück."""
    with _settings_lock:
        current = load_settings()
        current.update({k: v for k, v in (settings or {}).items() if k in DEFAULT_SETTINGS})
        cleaned = _clean(current)
        settings_path().write_text(
            json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return cleaned


def resolve_download_dir(settings: dict) -> Tuple[Path, Optional[str]]:
    """Liefert den wirksamen Zielordner und ggf. einen Hinweis, warum er abweicht.

    Ein gesetzter Ordner wird genutzt, wenn er existiert, oder angelegt, wenn
    sein Elternordner existiert. Sonst greift der App-eigene Download-Ordner.
    """
    wanted = (settings.get("download_dir") or "").strip()
    if not wanted:
        return app_download_dir(), None
    p = Path(wanted).expanduser()
    if not p.is_absolute():
        return app_download_dir(), _tr("Zielordner ist kein absoluter Pfad: {path}", path=wanted)
    if p.is_dir():
        return p, None
    if p.parent.is_dir():
        try:
            p.mkdir()
            return p, None
        except OSError as exc:
            return app_download_dir(), _tr("Zielordner nicht anlegbar ({exc})", exc=exc)
    return app_download_dir(), _tr("Zielordner nicht erreichbar: {path}", path=wanted)


def _tr(text: str, **values) -> str:
    from .i18n import tr  # i18n liest die Einstellungen aus diesem Modul, daher erst hier
    return tr(text, **values)


def build_update_url() -> str:
    """Beim Bauen hinterlegte Manifest-Adresse (Datei UPDATE_URL im Bundle)."""
    try:
        return (PROJECT_ROOT / "UPDATE_URL").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def update_url(settings: Optional[dict] = None) -> str:
    """Adresse des Release-Manifests: Einstellung, sonst beim Bauen hinterlegt."""
    s = settings if settings is not None else load_settings()
    if s.get("update_url"):
        return str(s["update_url"]).strip()
    return build_update_url()


def default_download_dir() -> Path:
    """Vorschlag für neue Nutzer:innen: ~/Documents/Benthic."""
    return Path.home() / "Documents" / "Benthic"


def zotero_database() -> Optional[Path]:
    """Standardort der Zotero-Datenbank, falls vorhanden."""
    p = Path.home() / "Zotero" / "zotero.sqlite"
    return p if p.is_file() else None


def setup_suggestions() -> dict:
    """Vorschläge für die Ersteinrichtung (erkannte Quellen auf diesem Rechner)."""
    sources = []
    z = zotero_database()
    if z:
        sources.append({"kind": "zotero", "path": str(z), "label": _tr("Zotero-Bibliothek")})
    return {"download_dir": str(default_download_dir()), "sources": sources}


def library_sources() -> List[Path]:
    """Liefert die konfigurierten Bibliotheks-Quellen, die tatsächlich existieren."""
    settings = load_settings()
    out: List[Path] = []
    for raw in settings.get("library_sources", []):
        p = Path(raw).expanduser()
        if p.is_file():
            out.append(p)
    return out
