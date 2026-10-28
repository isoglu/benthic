"""Verlauf abgeschlossener Läufe (Session-Report)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional

from . import config

MAX_ENTRIES = 50

# Läufe enden in Hintergrund-Threads; Lesen-Ändern-Schreiben braucht einen Lock.
_lock = threading.RLock()


def _path() -> Path:
    return config.data_dir() / "history.json"


def _read() -> List[Dict]:
    p = _path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save(items: List[Dict]) -> None:
    """Schreibt atomar (temporäre Datei + Umbenennen), damit nie eine halbe Datei bleibt."""
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _int_id(item: Dict) -> int:
    try:
        return int(item.get("id"))
    except (TypeError, ValueError):
        return 0


def _renumber_duplicates(items: List[Dict]) -> bool:
    """Vergibt eindeutige Nummern, falls ältere Versionen IDs doppelt vergeben haben.

    Früher begann die Zählung bei jedem Start wieder bei 1. Die Liste ist neueste
    zuerst; die älteste Fassung behält ihre Nummer, spätere Dubletten rücken hoch.
    """
    ids = [str(i.get("id")) for i in items]
    if len(ids) == len(set(ids)):
        return False
    seen = set()
    nxt = max((_int_id(i) for i in items), default=0) + 1
    for item in reversed(items):  # älteste zuerst
        key = str(item.get("id"))
        if key in seen:
            item["id"] = str(nxt)
            nxt += 1
        seen.add(str(item.get("id")))
    return True


def load() -> List[Dict]:
    with _lock:
        items = _read()
        if _renumber_duplicates(items):
            _save(items)
        return items


def next_id() -> int:
    """Erste freie Laufnummer (eindeutig auch über Neustarts hinweg)."""
    return max((_int_id(i) for i in load()), default=0) + 1


def append(entry: Dict) -> None:
    with _lock:
        items = load()
        items.insert(0, entry)
        _save(items[:MAX_ENTRIES])


def get(job_id: str) -> Optional[Dict]:
    for item in load():
        if str(item.get("id")) == str(job_id):
            return item
    return None


def _is_blocked(r: Dict) -> bool:
    # Ältere Läufe speicherten "OA bekannt, PDF blockiert" noch als Fehler.
    return r.get("status") == "blocked" or (
        r.get("status") == "error" and "Open Access bekannt" in str(r.get("error") or ""))


def summaries() -> List[Dict]:
    keys = (
        "id", "mode", "created_at", "finished_at", "total", "done",
        "ok", "oa", "routes", "present", "blocked", "absent", "errors", "verdicts", "origin",
    )
    out = []
    for item in load():
        row = {k: item.get(k) for k in keys}
        results = item.get("results")
        if isinstance(results, list) and item.get("mode") != "verify":  # wie die Oberfläche zählen
            row["blocked"] = sum(1 for r in results if _is_blocked(r))
            row["errors"] = sum(1 for r in results if r.get("status") == "error" and not _is_blocked(r))
        out.append(row)
    return out


def stats() -> Dict:
    items = load()
    return {
        "runs": len(items),
        "oa": sum(i.get("oa", 0) or 0 for i in items),
        "routes": sum(i.get("routes", 0) or 0 for i in items),
        "ok": sum(i.get("ok", 0) or 0 for i in items),
    }


def update(job_id: str, fields: Dict) -> bool:
    """Ergänzt einen gespeicherten Lauf (etwa um die Prüfung der Zitate); False, wenn es ihn nicht gibt."""
    with _lock:
        items = load()
        for item in items:
            if str(item.get("id")) == str(job_id):
                item.update(fields)
                _save(items)
                return True
        return False
