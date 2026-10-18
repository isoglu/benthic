"""Zotero in beide Richtungen: geprüfte Quellen in die offene Sammlung schicken, die Bibliothek auf Rückzüge prüfen.

Senden geht über den Connector-Server, den Zotero auf 127.0.0.1:23119 öffnet (derselbe Weg wie die
Browser-Erweiterung). Die Einträge landen in der Sammlung, die in Zotero gerade ausgewählt ist.

Prüfen liest die DOIs aus einer Kopie von ``zotero.sqlite`` (Zotero sperrt die Datei, solange es läuft)
und fragt sie wie die Rückzugswache ab. Benthic schreibt nie in die Datenbank von Zotero.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import requests

from . import config, library, oa, watch
from .i18n import tr

PORT = int(os.environ.get("BENTHIC_ZOTERO_PORT", "23119"))
HEADERS = {"X-Zotero-Connector-API-Version": "3", "User-Agent": oa.API_USER_AGENT}
SKIP_TYPES = ("attachment", "note", "annotation")
MAX_ITEMS = 20000


class ZoteroError(Exception):
    """Fehler mit einer Meldung für Menschen; ``status`` ist der passende HTTP-Code für die App."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _url(path: str) -> str:
    return f"http://127.0.0.1:{PORT}{path}"


def _not_running() -> ZoteroError:
    return ZoteroError(tr("Zotero läuft nicht. Öffne Zotero und versuche es noch einmal."), 503)


def status() -> Dict:
    """Läuft Zotero, und welche Sammlung ist gerade ausgewählt?"""
    out: Dict = {"running": False, "version": None, "target": None, "data_dir": None}
    found = data_dir()
    out["data_dir"] = str(found) if found else None
    try:
        r = requests.get(_url("/connector/ping"), headers=HEADERS, timeout=2.5)
    except requests.exceptions.RequestException:
        return out
    if r.status_code != 200:
        return out
    out["running"] = True
    out["version"] = r.headers.get("X-Zotero-Version")
    try:
        sel = requests.post(_url("/connector/getSelectedCollection"), json={}, headers=HEADERS, timeout=4).json()
        out["target"] = {"library": sel.get("libraryName"), "collection": sel.get("name") if sel.get("id") else None,
                         "editable": bool(sel.get("libraryEditable", True))}
    except (requests.exceptions.RequestException, ValueError):
        pass
    return out


def send(text: str) -> Dict:
    """Schickt RIS- oder BibTeX-Text an Zotero; gibt die Zahl der angelegten Einträge zurück."""
    if not text.strip():
        raise ZoteroError(tr("Nichts zu senden."), 400)
    body = text.encode("utf-8")
    try:
        r = requests.post(_url("/connector/import"), params={"session": uuid.uuid4().hex}, data=body,
                          headers={**HEADERS, "Content-Type": "text/plain; charset=utf-8"}, timeout=60)
    except requests.exceptions.RequestException:
        raise _not_running()
    if r.status_code == 201:
        try:
            items = r.json()
        except ValueError:
            items = []
        return {"sent": len(items) if isinstance(items, list) else 0}
    if r.status_code == 500 and "libraryEditable" in r.text:
        raise ZoteroError(tr("Die ausgewählte Bibliothek in Zotero ist schreibgeschützt. Wähle eine eigene Sammlung."), 409)
    if r.status_code == 400:
        raise ZoteroError(tr("Zotero hat das Format nicht erkannt."), 502)
    raise ZoteroError(tr("Zotero hat den Import abgelehnt (HTTP {code}).", code=r.status_code), 502)


# --------------------------------------------------------------------------
# Bibliothek lesen (nur lesen, aus einer Kopie)
# --------------------------------------------------------------------------
def data_dir() -> Optional[Path]:
    """Datenordner von Zotero: eigene Einstellung in prefs.js, sonst ~/Zotero."""
    env = os.environ.get("BENTHIC_ZOTERO_DIR")
    if env:
        return Path(env) if (Path(env) / "zotero.sqlite").exists() else None
    home = Path.home()
    profiles = home / "Library" / "Application Support" / "Zotero" / "Profiles"
    for prefs in sorted(profiles.glob("*/prefs.js")) if profiles.exists() else []:
        try:
            text = prefs.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r'user_pref\("extensions\.zotero\.dataDir",\s*"((?:[^"\\]|\\.)*)"\)', text)
        uses = re.search(r'user_pref\("extensions\.zotero\.useDataDir",\s*true\)', text)
        if m and uses:
            p = Path(m.group(1).encode().decode("unicode_escape"))
            if (p / "zotero.sqlite").exists():
                return p
    default = home / "Zotero"
    return default if (default / "zotero.sqlite").exists() else None


def _table(con: sqlite3.Connection, combined: str, plain: str) -> str:
    row = con.execute("SELECT name FROM sqlite_master WHERE name = ?", (combined,)).fetchone()
    return combined if row else plain


def library_items(folder: Optional[Path] = None) -> List[Dict]:
    """Einträge mit DOI (Feld DOI oder "DOI: …" in Extra), ohne Anhänge, Notizen und Papierkorb."""
    folder = folder or data_dir()
    if not folder:
        raise ZoteroError(tr("Keine Zotero-Bibliothek gefunden (erwartet unter ~/Zotero)."), 404)
    src = folder / "zotero.sqlite"
    with tempfile.TemporaryDirectory(prefix="benthic-zotero-") as tmp:
        copy = Path(tmp) / "zotero.sqlite"
        try:
            shutil.copy2(src, copy)
            for suffix in ("-wal", "-journal"):
                side = src.with_name(src.name + suffix)
                if side.exists():
                    shutil.copy2(side, copy.with_name(copy.name + suffix))
        except OSError:
            raise ZoteroError(tr("Die Zotero-Datenbank ließ sich nicht lesen."), 500)
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            fields = _table(con, "fieldsCombined", "fields")
            types = _table(con, "itemTypesCombined", "itemTypes")
            has_groups = bool(con.execute("SELECT name FROM sqlite_master WHERE name = 'groups'").fetchone())
            rows = con.execute(f"""
                SELECT i.itemID, i.key, i.libraryID, f.fieldName, v.value
                FROM items i
                JOIN {types} t ON t.itemTypeID = i.itemTypeID
                JOIN itemData d ON d.itemID = i.itemID
                JOIN {fields} f ON f.fieldID = d.fieldID
                JOIN itemDataValues v ON v.valueID = d.valueID
                WHERE t.typeName NOT IN ({",".join("?" * len(SKIP_TYPES))})
                  AND f.fieldName IN ('DOI', 'title', 'extra', 'date')
                  AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
            """, SKIP_TYPES).fetchall()
            groups = dict(con.execute("SELECT libraryID, groupID FROM groups").fetchall()) if has_groups else {}
        except sqlite3.DatabaseError:
            raise ZoteroError(tr("Die Zotero-Datenbank ließ sich nicht lesen."), 500)
        finally:
            con.close()
    items: Dict[int, Dict] = {}
    for item_id, key, lib_id, field, value in rows:
        it = items.setdefault(item_id, {"key": key, "library": lib_id, "doi": None, "title": None, "year": None})
        if field == "DOI":
            it["doi"] = library.normalize_doi(str(value)) or it["doi"]
        elif field == "extra" and not it["doi"]:
            m = re.search(r"^\s*DOI:\s*(\S+)", str(value), re.IGNORECASE | re.MULTILINE)
            it["doi"] = library.normalize_doi(m.group(1)) if m else None
        elif field == "title":
            it["title"] = str(value)
        elif field == "date":
            m = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", str(value))
            it["year"] = m.group(1) if m else None
    out = []
    for it in items.values():
        group = groups.get(it["library"])
        it["link"] = (f"zotero://select/groups/{group}/items/{it['key']}" if group
                      else f"zotero://select/library/items/{it['key']}")
        out.append(it)
    return out[:MAX_ITEMS]


def scan_library(folder: Optional[Path] = None) -> Dict:
    """Prüft alle Einträge mit DOI auf Rückzüge, Bedenken und Korrekturen."""
    items = library_items(folder)
    with_doi = [it for it in items if it["doi"]]
    email = config.load_settings().get("unpaywall_email", "")
    results = watch.scan([it["doi"] for it in with_doi], email) if with_doi else {}
    flagged = []
    for it in with_doi:
        res = results.get(it["doi"].lower())
        if res and res["notices"]:
            flagged.append({**it, "title": it["title"] or res.get("title"), "notices": res["notices"]})
    flagged.sort(key=lambda f: (-max(watch.KINDS[n["type"]][1] for n in f["notices"]), (f["title"] or "").lower()))
    return {
        "items": len(items),
        "with_doi": len(with_doi),
        "checked": sum(1 for r in results.values() if r["ok"]),
        "failed": sum(1 for r in results.values() if not r["ok"]),
        "flagged": flagged,
        "data_dir": str(folder or data_dir() or ""),
    }
