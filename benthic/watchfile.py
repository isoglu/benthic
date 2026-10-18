"""Eine Arbeit beim Schreiben beobachten: bei jedem Sichern neu prüfen und neue Probleme melden.

Beobachtet wird eine Datei (Word, PDF, Text, Markdown) oder ein LaTeX-Projekt (alle .tex- und .bib-Dateien
im Ordner der Hauptdatei). Nach dem Sichern wartet Benthic, bis die Datei einige Sekunden ruht, liest sie
und prüft nur dann neu, wenn sich Literaturverzeichnis, Zitate oder wörtliche Zitate geändert haben.
Gemeldet wird, was seit der letzten Prüfung neu dazugekommen ist: Zitate ohne Eintrag, nicht auffindbare,
falsch zugeordnete und zurückgezogene Quellen.

Prüfen und Lesen übernimmt die App (configure): so laufen die Prüfungen als normale Läufe mit Fortschritt
und Verlauf.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import config

POLL = 4.0          # Sekunden zwischen zwei Blicken auf die Dateien
SETTLE = 3.0        # so lange muss eine Datei nach dem Sichern ruhen
MAX_WATCHED = 5
BAD = ("not_found", "mismatch", "retracted")

_lock = threading.RLock()
_analyse: Optional[Callable[[Path], Dict]] = None
_launch: Optional[Callable[[Path, Dict], Optional[str]]] = None
_job: Optional[Callable[[str], Optional[Dict]]] = None
_started = False


def configure(analyse: Callable[[Path], Dict], launch: Callable[[Path, Dict], Optional[str]],
              job: Callable[[str], Optional[Dict]]) -> None:
    global _analyse, _launch, _job
    _analyse, _launch, _job = analyse, launch, job


def _path() -> Path:
    return config.data_dir() / "watched.json"


def _read() -> Dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"files": {}}


def _save(data: Dict) -> None:
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def is_latex(path: Path) -> bool:
    return path.suffix.lower() in (".tex", ".ltx")


def stamp(path: Path) -> Optional[float]:
    """Letzte Änderung: bei LaTeX die jüngste .tex- oder .bib-Datei im Projektordner."""
    try:
        if not is_latex(path):
            return path.stat().st_mtime
        files = itertools.islice(path.parent.rglob("*"), 5000)  # nie den ganzen Benutzerordner durchgehen
        times = [p.stat().st_mtime for p in files if p.suffix.lower() in (".tex", ".bib", ".ltx") and p.is_file()]
        return max(times) if times else None
    except OSError:
        return None


def fingerprint(analysis: Dict) -> str:
    """Was eine neue Prüfung nötig macht: Einträge, Zitate, wörtliche Zitate."""
    parts = [r.get("raw") or r.get("label") or "" for r in analysis.get("refs") or []]
    parts += sorted(m["label"] for m in analysis.get("missing_in_list") or [])
    parts += sorted(f"{c.get('label')}|{c.get('page')}" for c in analysis.get("claims") or [])
    parts += [q.get("quote", "") for q in analysis.get("quotes") or []]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def issues(job: Dict) -> List[Dict]:
    """Probleme eines fertigen Laufs, mit Schlüsseln zum Vergleichen."""
    out = []
    for m in (job.get("document") or {}).get("missing_in_list") or []:
        out.append({"key": "missing:" + m["label"], "kind": "missing", "label": m["label"]})
    for r in job.get("results") or []:
        if r.get("verdict") in BAD:
            label = (r.get("claimed") or {}).get("title") or r.get("identifier") or ""
            out.append({"key": f"{r['verdict']}:{(r.get('input') or label)[:120]}", "kind": r["verdict"], "label": label})
    return out


def add(path: str) -> Dict:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError("missing")
    with _lock:
        data = _read()
        if str(p) not in data["files"] and len(data["files"]) >= MAX_WATCHED:
            raise ValueError("too-many")
        data["files"][str(p)] = {"path": str(p), "name": p.name, "added_at": time.time(), "stamp": None,
                                 "fingerprint": None, "job": None, "checking": False, "issues": [], "new": [],
                                 "resolved": 0, "last_check": None, "error": None, "unseen": False}
        _save(data)
    return status()


def remove(path: str) -> Dict:
    with _lock:
        data = _read()
        data["files"].pop(str(Path(path).expanduser()), None)
        _save(data)
    return status()


def mark_seen() -> None:
    with _lock:
        data = _read()
        for f in data["files"].values():
            f["unseen"] = False
        _save(data)


def status() -> Dict:
    data = _read()
    return {"files": sorted(data["files"].values(), key=lambda f: f.get("added_at", 0))}


def tick(now: Optional[float] = None) -> None:
    """Ein Durchgang: geänderte Dateien lesen und prüfen lassen, fertige Prüfungen auswerten."""
    if not (_analyse and _launch and _job):
        return
    now = now or time.time()
    with _lock:
        data = _read()
        changed = False
        for key, f in data["files"].items():
            path = Path(f["path"])
            if f.get("job") and f.get("checking"):
                job = _job(f["job"])
                if job is None:  # Lauf ging verloren (App neu gestartet): beim nächsten Durchgang neu prüfen
                    f["checking"], f["stamp"], f["fingerprint"], changed = False, None, None, True
                    continue
                if job.get("status") != "running":
                    current = issues(job)
                    before = {i["key"] for i in f.get("issues") or []}
                    now_keys = {i["key"] for i in current}
                    f["new"] = [i for i in current if i["key"] not in before] if f.get("last_check") else current
                    f["resolved"] = len(before - now_keys) if f.get("last_check") else 0
                    f["issues"] = current
                    f["checking"] = False
                    f["last_check"] = now
                    f["unseen"] = bool(f["new"] or f["resolved"])
                    changed = True
                continue
            mtime = stamp(path)
            if mtime is None:
                if f.get("error") != "missing":
                    f["error"], changed = "missing", True
                continue
            if f.get("stamp") == mtime or now - mtime < SETTLE:
                continue
            try:
                analysis = _analyse(path)
            except Exception:  # halb gesicherte Datei: beim nächsten Durchgang erneut
                continue
            f["stamp"], f["error"], changed = mtime, None, True
            fp = fingerprint(analysis)
            if fp == f.get("fingerprint"):
                continue  # nur der Fließtext hat sich geändert
            f["fingerprint"] = fp
            if not analysis.get("entries"):
                f["error"] = "no-bibliography"
                continue
            job_id = _launch(path, analysis)
            if job_id:
                f["job"], f["checking"] = job_id, True
        if changed:
            _save(data)


def start() -> None:
    """Hintergrund-Takt (einmal je Prozess)."""
    global _started
    if _started or os.environ.get("BENTHIC_WATCH") == "0":
        return
    _started = True

    def loop() -> None:
        while True:
            try:
                if _read()["files"]:
                    tick()
            except Exception:
                pass
            time.sleep(POLL)

    threading.Thread(target=loop, name="file-watch", daemon=True).start()
