"""Projekte: eine Forschungsfrage, ihre Quellen, Prüfungen und Downloads.

Ein Projekt sammelt DOIs mit dem, was Benthic über sie weiß (Titel, Jahr,
Urteil der Prüfung, Ergebnis des Downloads). Prüf- und Download-Läufe, die
einem Projekt zugeordnet sind, tragen ihre Ergebnisse automatisch ein.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from . import config, library, style
from .i18n import tr

_lock = threading.RLock()

# Urteile, die eine Quelle als echt ausweisen (vgl. verify.VERDICTS).
REAL = ("verified", "corrected", "partial")


def _path() -> Path:
    return config.data_dir() / "projects.json"


def _read() -> Dict:
    p = _path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("projects"), dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"projects": {}, "active": None}


def _save(data: Dict) -> None:
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32]
    return f"{s or 'projekt'}-{uuid.uuid4().hex[:4]}"


def find(ref: Optional[str]) -> Optional[Dict]:
    """Projekt per ID oder (für LLMs bequem) per Name, ohne Groß-/Kleinschreibung."""
    if not ref:
        return None
    data = _read()
    if ref in data["projects"]:
        return data["projects"][ref]
    low = ref.strip().lower()
    for p in data["projects"].values():
        if p["name"].lower() == low:
            return p
    return None


def _counts(p: Dict) -> Dict[str, int]:
    items = list(p.get("items", {}).values())
    lib = library.load()
    return {
        "count": len(items),
        "verified": sum(1 for i in items if i.get("verdict") in REAL),
        "flagged": sum(1 for i in items if i.get("verdict") in ("mismatch", "not_found", "retracted")),
        "in_library": sum(1 for i in items if i["doi"].lower() in lib or i["doi"] in lib),
    }


def list_projects() -> Dict:
    data = _read()
    out = []
    for p in sorted(data["projects"].values(), key=lambda x: x.get("updated_at", 0), reverse=True):
        out.append({k: p.get(k) for k in ("id", "name", "description", "created_at", "updated_at")} | _counts(p)
                   | {"watch": p.get("watch", True)})
    return {"projects": out, "active": data.get("active")}


def create(name: str, description: str = "") -> Dict:
    name = (name or "").strip()
    if not name:
        raise ValueError(tr("Bitte einen Projektnamen angeben."))
    with _lock:
        data = _read()
        if any(p["name"].lower() == name.lower() for p in data["projects"].values()):
            raise ValueError(tr("Ein Projekt „{name}“ gibt es schon.", name=name))
        now = time.time()
        pid = _slug(name)
        data["projects"][pid] = {"id": pid, "name": name, "description": (description or "").strip(),
                                 "created_at": now, "updated_at": now, "items": {}}
        _save(data)
        return data["projects"][pid]


def update(pid: str, name: Optional[str] = None, description: Optional[str] = None,
           watch: Optional[bool] = None) -> Dict:
    with _lock:
        data = _read()
        p = data["projects"].get(pid)
        if not p:
            raise KeyError(pid)
        if name is not None and name.strip():
            p["name"] = name.strip()
        if description is not None:
            p["description"] = description.strip()
        if watch is not None:
            p["watch"] = bool(watch)  # Rückzugswache für dieses Projekt an oder aus
        p["updated_at"] = time.time()
        _save(data)
        return p


def delete(pid: str) -> bool:
    with _lock:
        data = _read()
        if data["projects"].pop(pid, None) is None:
            return False
        if data.get("active") == pid:
            data["active"] = None
        _save(data)
        return True


def set_active(pid: Optional[str]) -> Optional[str]:
    with _lock:
        data = _read()
        data["active"] = pid if pid in data["projects"] else None
        _save(data)
        return data["active"]


def active() -> Optional[str]:
    return _read().get("active")


def add_items(pid: str, items: List[Dict], source: str = "manual") -> int:
    """Trägt DOIs ein oder ergänzt sie. ``items``: dicts mit mindestens ``doi``."""
    with _lock:
        data = _read()
        p = data["projects"].get(pid)
        if not p:
            raise KeyError(pid)
        now, changed = time.time(), 0
        for it in items:
            doi = library.normalize_doi(str(it.get("doi") or ""))
            if not doi:
                continue
            key = doi.lower()
            cur = p["items"].get(key) or {"doi": doi, "added_at": now, "source": source}
            for field in ("title", "year", "verdict", "score", "status", "channel", "filename", "note"):
                if it.get(field) not in (None, ""):
                    cur[field] = it[field]
            # Wie das Werk zuletzt angegeben war (Titel, Jahr, Band, Heft, Seiten, Datei): nur die letzte Fassung, denn
            # eine korrigierte Angabe, auch in einer neuen Datei (arbeit_v2.docx), ist kein Widerspruch.
            if it.get("printed"):
                cur["printed"] = {**it["printed"], "where": it.get("run") or None}
            cur["updated_at"] = now
            p["items"][key] = cur
            changed += 1
        if changed:
            p["updated_at"] = now
            _save(data)
        return changed


def remove_item(pid: str, doi: str) -> bool:
    with _lock:
        data = _read()
        p = data["projects"].get(pid)
        if not p or p["items"].pop(doi.lower(), None) is None:
            return False
        p["updated_at"] = time.time()
        _save(data)
        return True


def record_results(pid: Optional[str], mode: str, results: List[Dict], source: str = "ui", run: str = "") -> int:
    """Übernimmt die Ergebnisse eines Laufs ins Projekt; ``run`` ist die geprüfte Arbeit (Dateiname, sonst leer)."""
    if not pid or not find(pid):
        return 0
    items = []
    for r in results:
        if mode == "verify":
            found = r.get("found") or {}
            claimed = r.get("claimed") or {}
            if not r.get("doi"):
                continue  # erfundene Quellen ohne DOI bleiben im Prüfbericht, nicht im Projekt
            printed = {k: claimed.get(k) for k in ("title", "journal", "year", "volume", "issue", "pages") if claimed.get(k)}
            items.append({"doi": r["doi"], "title": found.get("title") or claimed.get("title"),
                          "year": found.get("year") or claimed.get("year"), "verdict": r.get("verdict"),
                          "score": r.get("score"), "note": r.get("note"),
                          "printed": printed if r.get("verdict") in style.KNOWN and len(printed) > 1 and source != "project" else None,
                          "run": run})
        else:
            items.append({"doi": r.get("identifier"), "title": r.get("title"), "status": r.get("status"),
                          "channel": r.get("channel"), "filename": r.get("filename")})
    return add_items(find(pid)["id"], items, source=source)  # type: ignore[index]


def detail(pid: str) -> Optional[Dict]:
    p = find(pid)
    if not p:
        return None
    lib = library.load()
    items = []
    for it in sorted(p.get("items", {}).values(), key=lambda x: x.get("added_at", 0)):
        entry = lib.get(it["doi"]) or lib.get(it["doi"].lower()) or {}
        items.append({**it, "in_library": bool(entry), "filename": it.get("filename") or entry.get("filename"),
                      "title": it.get("title") or entry.get("title")})
    entries = [{"doi": it["doi"], **it["printed"]} for it in items
               if isinstance(it.get("printed"), dict) and it["printed"].get("title")]  # ältere Formen: übergehen
    return ({k: p.get(k) for k in ("id", "name", "description", "created_at", "updated_at")} | _counts(p)
            | {"items": items, "watch": p.get("watch", True), "conflicts": style.consistency(entries)[:40]})


# --------------------------------------------------------------------------
# Weitergeben: ein Projekt als Datei, ohne Konto und ohne Server
# --------------------------------------------------------------------------
BUNDLE_FORMAT = "benthic-project"
_SHARED = ("doi", "title", "year", "verdict", "score", "note")


def bundle(pid: str) -> Optional[Dict]:
    """Name, Beschreibung und Quellen mit Urteil; keine PDFs, Dateinamen oder Pfade dieses Macs."""
    p = find(pid)
    if not p:
        return None
    items = [{k: it[k] for k in _SHARED if it.get(k) not in (None, "")}
             for it in sorted(p.get("items", {}).values(), key=lambda x: x.get("added_at", 0))]
    return {"format": BUNDLE_FORMAT, "version": 1, "name": p["name"], "description": p.get("description", ""),
            "exported_at": int(time.time()), "items": items}


def unbundle(data: Dict) -> Dict:
    """Legt aus einer geteilten Datei ein neues Projekt an; bei gleichem Namen mit Nummer dahinter."""
    if not isinstance(data, dict) or data.get("format") != BUNDLE_FORMAT or not isinstance(data.get("items"), list):
        raise ValueError(tr("Das ist keine Projektdatei von Benthic."))
    base = str(data.get("name") or "").strip()[:120] or tr("Geteiltes Projekt")
    names = {p["name"].lower() for p in _read()["projects"].values()}
    name, n = base, 2
    while name.lower() in names:
        name, n = f"{base} ({n})", n + 1
    proj = create(name, str(data.get("description") or "")[:2000])
    items = [{k: it.get(k) for k in _SHARED} for it in data["items"][:5000] if isinstance(it, dict)]
    added = add_items(proj["id"], items, source="import")
    return {"id": proj["id"], "name": name, "added": added}
