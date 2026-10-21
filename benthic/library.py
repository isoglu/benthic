"""Lernende Bibliothek: DOI-Index mit Deduplikation.

Benthic merkt sich, welche DOIs bereits im System sind. Der Index wächst:

* durch **Downloads** (jede erfolgreiche Beschaffung wird aufgezeichnet) und
* durch **Import** aus einer bestehenden Quellen-Datei (z. B. eine Markdown-Literaturliste).

So muss die wachsende Datenbank nicht bei jedem Lauf neu gescannt werden.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import config, refs
from .i18n import tr

# DOI-Extraktion: URL-Form (https://doi.org/10.x/y) und nackte DOIs.
# ")" gehört zum Treffer: DOIs wie 10.1016/S0140-6736(97)11096-0 enthalten Klammern. Eine schließende
# Klammer ohne öffnende am Ende schneidet refs.clean_doi ab.
_DOI_IN_URL = re.compile(r"doi\.org/(10\.\d{4,9}/[^\s|\]]+)", re.IGNORECASE)
_DOI_BARE = re.compile(r"(?<![A-Za-z0-9])(10\.\d{4,9}/[^\s|\]]+)", re.IGNORECASE)


# Parallele Downloads schreiben gleichzeitig; ohne Lock gehen Einträge verloren.
_lock = threading.RLock()


def _library_path() -> Path:
    return config.data_dir() / "library.json"


def normalize_doi(value: str) -> Optional[str]:
    """Normalisiert eine DOI (aus URL oder Klartext) zu ``10.xxxx/yyyy``."""
    s = (value or "").strip()
    if not s:
        return None
    m = _DOI_IN_URL.search(s) or _DOI_BARE.search(s)
    return refs.clean_doi(m.group(1)) if m else None


def _find_dois(text: str) -> List[str]:
    """Findet alle DOIs in einem Textstück (URL- und nackte Form)."""
    found: List[str] = []
    for m in list(_DOI_IN_URL.finditer(text)) + list(_DOI_BARE.finditer(text)):
        doi = refs.clean_doi(m.group(1))  # schneidet "}", ")" und Satzzeichen sauber ab
        if doi:
            found.append(doi)
    # Reihenfolge + Duplikate entfernen
    seen: set = set()
    out: List[str] = []
    for d in found:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def load() -> Dict[str, Dict]:
    """Lädt den Index (doi -> Metadaten)."""
    p = _library_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("entries"), dict):
                return data["entries"]
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(entries: Dict[str, Dict]) -> None:
    """Schreibt atomar (temporäre Datei + Umbenennen), damit nie eine halbe Datei bleibt."""
    p = _library_path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(
        json.dumps({"entries": entries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, p)


def add(doi: str, title: Optional[str] = None, filename: Optional[str] = None,
        origin: str = "import", **extra) -> bool:
    """Fügt eine DOI hinzu bzw. ergänzt fehlende Angaben. Liefert True bei Änderung."""
    with _lock:
        entries = load()
        changed = _merge(entries, doi, title, filename, origin, extra)
        if changed:
            _save(entries)
        return changed


def _merge(entries: Dict[str, Dict], doi: str, title: Optional[str], filename: Optional[str],
           origin: str, extra: Dict) -> bool:
    """Trägt eine DOI in ``entries`` ein (ohne zu speichern). True = neu oder ergänzt."""
    doi = normalize_doi(doi)
    if not doi:
        return False
    now = time.time()
    existing = entries.get(doi)
    changed = False
    if existing:
        for key, value in (("title", title), ("filename", filename)):
            if not existing.get(key) and value:
                existing[key] = value
                changed = True
    else:
        existing = entries[doi] = {
            "doi": doi,
            "title": title or None,
            "filename": filename or None,
            "origin": origin,
            "added_at": now,
        }
        changed = True
    for k, v in extra.items():
        if v is not None and k not in existing:
            existing[k] = v
            changed = True
    if changed:
        existing["updated_at"] = now
    return changed


def has(doi: str) -> bool:
    return normalize_doi(doi) in load()


def get(doi: str) -> Optional[Dict]:
    return load().get(normalize_doi(doi))


def count() -> int:
    return len(load())


def entries_list() -> List[Dict]:
    """Index als sortierte Liste (neueste zuerst)."""
    items = list(load().values())
    items.sort(key=lambda e: e.get("added_at", 0) or 0, reverse=True)
    return items


def search(query: str = "", limit: int = 25, dois: Optional[List[str]] = None) -> List[Dict]:
    """Sucht im Index nach Titel oder DOI (alle Wörter müssen vorkommen)."""
    words = [w for w in refs.normalize_title(query or "").split() if w]
    allowed = {d.lower() for d in dois} if dois is not None else None
    hits = []
    for e in entries_list():
        if allowed is not None and e["doi"].lower() not in allowed:
            continue
        hay = refs.normalize_title(f"{e.get('title') or ''} {e['doi']}")
        if all(w in hay for w in words):
            hits.append({k: e.get(k) for k in ("doi", "title", "filename", "channel", "origin")})
            if len(hits) >= limit:
                break
    return hits


def import_from_text(text: str) -> Dict:
    """Scannt Text (z. B. Markdown-Tabelle) und importiert gefundene DOIs.

    Liefert ``{"added": n, "found": m}``.
    """
    added = 0
    found = 0
    fmt = refs.detect_format(text or "")
    with _lock:  # ein Lesen, ein Schreiben für den ganzen Import
        entries = load()
        if fmt in ("bibtex", "ris", "endnote_xml"):
            for r in refs.parse(text):
                if r.get("doi"):
                    found += 1
                    if _merge(entries, str(r["doi"]), r.get("title"), None, "import", {}):  # type: ignore[arg-type]
                        added += 1
            if added:
                _save(entries)
            return {"added": added, "found": found, "format": fmt}
        for line in (text or "").splitlines():
            # Titel = erste Tabellenzelle (falls Markdown-Tabelle).
            cells = [c.strip() for c in line.split("|")]
            title = next((c for c in cells if c and not set(c) <= set("-: ")), "")
            title = title if title else None

            for doi in _find_dois(line):
                found += 1
                if _merge(entries, doi, title, None, "import", {}):
                    added += 1
        if added:
            _save(entries)
    return {"added": added, "found": found}


def import_from_file(path: str) -> Dict:
    """Scannt eine Datei und importiert deren DOIs.

    Zotero-Datenbank (``zotero.sqlite``), BibTeX, RIS, Markdown oder Text.
    """
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(tr("Datei nicht gefunden: {path}", path=path))
    if p.suffix.lower() in (".sqlite", ".db"):
        return import_zotero(p)
    if p.suffix.lower() in (".ctv6", ".ctv6archive"):
        return import_citavi(p)
    if p.stat().st_size > 60 * 1024 * 1024:  # Text wird ganz gelesen; eine Literaturliste ist weit kleiner
        raise ValueError(tr("Die Datei ist größer als 60 MB."))
    text = p.read_text(encoding="utf-8", errors="replace")
    return import_from_text(text)


def import_zotero(path: Path) -> Dict:
    """Liest DOIs und Titel aus einer Zotero-Datenbank.

    Zotero sperrt seine Datenbank, solange es läuft. Deshalb wird eine Kopie
    gelesen; die echte Datei bleibt unberührt.
    """
    import shutil
    import sqlite3
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "zotero.sqlite"
        shutil.copy2(path, copy)
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            fields = "fieldsCombined" if "fieldsCombined" in tables else "fields"
            deleted = "AND d.itemID NOT IN (SELECT itemID FROM deletedItems)" if "deletedItems" in tables else ""
            rows = con.execute(f"""
                SELECT d.itemID, f.fieldName, v.value
                FROM itemData d
                JOIN {fields} f ON f.fieldID = d.fieldID
                JOIN itemDataValues v ON v.valueID = d.valueID
                WHERE f.fieldName IN ('DOI', 'title', 'extra', 'url') {deleted}
            """).fetchall()
        finally:
            con.close()

    items: Dict[int, Dict[str, str]] = {}
    for item_id, field, value in rows:
        items.setdefault(item_id, {})[field] = str(value or "")
    added = found = 0
    with _lock:
        entries = load()
        for it in items.values():
            # DOI-Feld zuerst; ältere Einträge haben sie oft unter "Extra" oder in der URL.
            doi = refs.clean_doi(it.get("DOI", "")) or refs.clean_doi(it.get("extra", "")) \
                or refs.clean_doi(it.get("url", ""))
            if not doi:
                continue
            found += 1
            if _merge(entries, doi, it.get("title") or None, None, "zotero", {}):
                added += 1
        if added:
            _save(entries)
    return {"added": added, "found": found, "format": "zotero", "items": len(items)}


def import_citavi(path: Path) -> Dict:
    """Liest DOIs und Titel aus einem Citavi-Projekt (.ctv6 oder .ctv6archive)."""
    import shutil
    import sqlite3
    import tempfile
    import zipfile

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "project.ctv6"
        if path.suffix.lower() == ".ctv6archive" or zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                ctv_name = next((n for n in z.namelist() if n.lower().endswith(".ctv6")), None)
                if not ctv_name:
                    return {"added": 0, "found": 0, "format": "citavi", "items": 0}
                copy.write_bytes(z.read(ctv_name))
        else:
            shutil.copy2(path, copy)
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "Reference" not in tables:
                return {"added": 0, "found": 0, "format": "citavi", "items": 0}
            cols = {r[1] for r in con.execute("PRAGMA table_info(Reference)")}
            sel = [c for c in ("DOI", "Title", "OnlineAddress") if c in cols]
            rows = con.execute(f"SELECT {', '.join(sel)} FROM Reference").fetchall() if sel else []
        finally:
            con.close()

    added = found = 0
    with _lock:
        entries = load()
        for row in rows:
            rec = dict(zip(sel, row))
            doi = refs.clean_doi(str(rec.get("DOI") or "")) or refs.clean_doi(str(rec.get("OnlineAddress") or ""))
            if not doi:
                continue
            found += 1
            if _merge(entries, doi, str(rec.get("Title") or "") or None, None, "citavi", {}):
                added += 1
        if added:
            _save(entries)
    return {"added": added, "found": found, "format": "citavi", "items": len(rows)}


def clear() -> int:
    """Leert den Index und liefert die vorherige Anzahl."""
    with _lock:
        n = count()
        _save({})
        return n
