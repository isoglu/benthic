"""Fragwürdige Zeitschriften: öffentliche Listen, die Benthic zur Laufzeit lädt und lokal vorhält.

- DOAJ, "Journals withdrawn" (Change Log seit März 2014): Zeitschriften, die DOAJ aus dem Verzeichnis
  genommen hat. Als Warnung zählt nur die Entfernung wegen Verstößen gegen gute Praxis ("not adhering to
  best practice"); eingestellte Zeitschriften oder tote Websites sagen nichts über die Qualität. Steht die
  Zeitschrift laut OpenAlex heute wieder im DOAJ, entfällt die Warnung. Daten: DOAJ, CC BY-SA 4.0.
- DOAJ, "Journals and publishers that falsely claim they are indexed in DOAJ" (CC0).
- Retraction Watch Hijacked Journal Checker: Websites, die eine echte Zeitschrift kopieren. Nur, wenn die
  Einstellung an ist; Abgleich über die Adresse der Website, nie über die ISSN (die Kopien führen die ISSN
  des Originals).

Jede Liste wird höchstens einmal pro Woche neu geladen; ohne Netz gilt der letzte Stand.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

from . import config
from .journals import norm_issn

_SHEET = "https://docs.google.com/spreadsheets/d/{id}/export?format=csv&gid={gid}"
LISTS = {
    "doaj_withdrawn": {
        "urls": [_SHEET.format(id="1Kv3MbgFSgtSDnEGkA2JacrSjunRu0umHeZCtcMeqO5E", gid="2104690845"),
                 _SHEET.format(id="183mRBRqs2jOyP0qZWXN8dUd02D4vL0Mov_kgYF8HORM", gid="1650882189")],
        "credit": "DOAJ, CC BY-SA 4.0", "page": "https://doaj.org/",
    },
    "doaj_false": {
        "urls": [_SHEET.format(id="1Y_Sza4rPDkf-NNX9kwiErGrKeNTM75md9B63A_gVpaQ", gid="0")],
        "credit": "DOAJ, CC0", "page": "https://doaj.org/",
    },
    "hijacked": {
        "urls": [_SHEET.format(id="1ak985WGOgGbJRJbZFanoktAN_UFeExpE", gid="5255084")],
        "credit": "Retraction Watch Hijacked Journal Checker",
        "page": "https://retractionwatch.com/the-retraction-watch-hijacked-journal-checker/",
    },
}
MAX_AGE = 7 * 24 * 3600
RETRY_AFTER = 6 * 3600       # nach einem Fehlschlag nicht bei jeder Prüfung erneut versuchen
_ISSN = re.compile(r"\b\d{4}-?\d{3}[\dXx]\b")
_MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                         "september", "october", "november", "december"), start=1)}
_lock = threading.Lock()
_memory: Dict[str, Dict] = {}


# --------------------------------------------------------------------------
# Laden und Zwischenspeichern
# --------------------------------------------------------------------------
def _path(name: str):
    folder = config.data_dir() / "lists"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{name}.json"


def _download(url: str) -> Optional[List[List[str]]]:
    import requests
    from . import oa
    try:
        r = requests.get(url, headers={"User-Agent": oa.API_USER_AGENT}, timeout=30, allow_redirects=True)
    except requests.exceptions.RequestException:
        return None
    if r.status_code != 200 or "text/html" in r.headers.get("Content-Type", ""):
        return None
    r.encoding = "utf-8"
    return list(csv.reader(io.StringIO(r.text)))


def _date(raw: str) -> Optional[str]:
    """"15-October-2026", "15 October 2026" oder "15/10/2026" → "2026-10-15"."""
    raw = (raw or "").strip()
    m = re.match(r"(\d{1,2})[\s\-/.]+([A-Za-z]+|\d{1,2})[\s\-/.]+(\d{4})", raw)
    if not m:
        q = re.match(r"Q([1-4]),?\s*(\d{4})", raw)  # "Q1, 2014"
        return f"{q.group(2)}-{int(q.group(1)) * 3 - 2:02d}" if q else None
    day, month, year = m.groups()
    month_no = int(month) if month.isdigit() else next(
        (n for name, n in _MONTHS.items() if len(month) >= 3 and name.startswith(month.lower())), None)
    if not month_no or not 1 <= month_no <= 12:
        return None
    return f"{year}-{month_no:02d}-{int(day):02d}"


def _header(rows: List[List[str]], first: str) -> int:
    for i, row in enumerate(rows):
        cells = [c.strip().lower() for c in row]
        if first in cells and any("issn" in c for c in cells):
            return i
    return -1


def parse_withdrawn(rows: List[List[str]]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    start = _header(rows, "journal title")
    if start < 0:
        return out
    for row in rows[start + 1:]:
        if len(row) < 4 or not row[0].strip():
            continue
        title, issns, date, reason = (c.strip() for c in row[:4])
        entry = {"title": title, "date": _date(date), "reason": reason,
                 "best_practice": "best practice" in reason.lower()}
        for issn in {norm_issn(x) for x in _ISSN.findall(issns)} - {None}:
            cur = out.get(issn)
            if not cur or (entry["date"] or "") > (cur["date"] or ""):
                out[issn] = entry
    return out


def parse_false(rows: List[List[str]]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    start = _header(rows, "journal title")
    for row in rows[start + 1:] if start >= 0 else []:
        if len(row) < 2 or not row[0].strip():
            continue
        for issn in {norm_issn(x) for x in _ISSN.findall(row[1])} - {None}:
            out[issn] = {"title": row[0].strip()}
    return out


def _site(url: str) -> Optional[Dict]:
    url = url.strip()
    if not url:
        return None
    p = urlparse(url if "://" in url else "http://" + url)
    host = (p.hostname or "").lower()
    if not host or "." not in host:
        return None
    host = host[4:] if host.startswith("www.") else host
    path = p.path.rstrip("/").lower()
    path = re.sub(r"/index(?:\.html?)?$", "", path)
    path = re.sub(r"/index\.php$", "", path)
    return {"host": host, "path": path}


def parse_hijacked(rows: List[List[str]]) -> Dict[str, List[Dict]]:
    """Host → [{path, title, original, original_url}]; ein Host kann mehrere gekaperte Pfade haben."""
    out: Dict[str, List[Dict]] = {}
    start = next((i for i, row in enumerate(rows) if any("hijacked journal title" in c.lower() for c in row)), -1)
    if start < 0:
        return out
    head = [c.strip().lower() for c in rows[start]]
    col = lambda name: next((i for i, c in enumerate(head) if c == name), -1)  # noqa: E731
    c_title, c_url, c_orig, c_orig_url = (col("hijacked journal title"), col("url (hijacked)"),
                                          col("original journal"), col("url (original journal)"))
    if c_url < 0:
        return out
    for row in rows[start + 1:]:
        if len(row) <= c_url:
            continue
        for raw in re.split(r"[\s,;]+", row[c_url]):
            site = _site(raw)
            if not site:
                continue
            original = row[c_orig].strip() if 0 <= c_orig < len(row) else ""
            out.setdefault(site["host"], []).append({
                "path": site["path"], "title": row[c_title].strip() if 0 <= c_title < len(row) else "",
                # "Original journal" enthält manchmal einen Kommentar statt eines Namens.
                "original": original if len(original) <= 160 else "",
                "original_url": row[c_orig_url].strip() if 0 <= c_orig_url < len(row) else ""})
    return out


_PARSERS = {"doaj_withdrawn": parse_withdrawn, "doaj_false": parse_false, "hijacked": parse_hijacked}


def load(name: str, refresh: bool = True) -> Dict:
    """Die Liste ``name`` aus dem Speicher, der Datei oder (wenn älter als eine Woche) frisch geladen."""
    with _lock:
        cached = _memory.get(name)
        if cached is None:
            try:
                cached = json.loads(_path(name).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = {"fetched_at": 0, "tried_at": 0, "data": {}}
            _memory[name] = cached
        now = time.time()
        stale = now - cached.get("fetched_at", 0) > MAX_AGE
        if not (refresh and stale and now - cached.get("tried_at", 0) > RETRY_AFTER) or os.environ.get("BENTHIC_OFFLINE"):
            return cached
        cached["tried_at"] = now
    data: Dict = {}
    ok = True
    for url in LISTS[name]["urls"]:
        rows = _download(url)
        if rows is None:
            ok = False
            break
        for key, value in _PARSERS[name](rows).items():
            if isinstance(value, list):
                data.setdefault(key, []).extend(value)
            elif key not in data or (value.get("date") or "") > (data[key].get("date") or ""):
                data[key] = value
    with _lock:
        if ok and data:
            cached = {"fetched_at": time.time(), "tried_at": time.time(), "data": data}
            _memory[name] = cached
            p = _path(name)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)
        return _memory[name]


# --------------------------------------------------------------------------
# Abgleich
# --------------------------------------------------------------------------
def enabled() -> Dict[str, bool]:
    s = config.load_settings()
    return {"doaj": bool(s.get("journal_lists", True)), "hijacked": bool(s.get("hijacked_check", False))}


def journal_flags(issns: List[str], in_doaj_now: bool = False) -> List[Dict]:
    """Warnungen zu einer Zeitschrift (über ihre ISSNs)."""
    if not issns or not enabled()["doaj"]:
        return []
    withdrawn = load("doaj_withdrawn")["data"]
    false = load("doaj_false")["data"]
    flags: Dict[str, Dict] = {}
    for issn in (norm_issn(i) for i in issns):
        w = withdrawn.get(issn or "")
        if w and w.get("best_practice") and not in_doaj_now and "doaj_removed" not in flags:
            flags["doaj_removed"] = {"kind": "doaj_removed", "date": w.get("date"), "reason": w.get("reason"),
                                     "credit": LISTS["doaj_withdrawn"]["credit"]}
        if issn in false and "doaj_false" not in flags:
            flags["doaj_false"] = {"kind": "doaj_false", "credit": LISTS["doaj_false"]["credit"]}
    return list(flags.values())


_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'()\[\]{}]+", re.IGNORECASE)


def hijacked_in(text: str) -> List[Dict]:
    """Gekaperte Zeitschriften-Websites, auf die ``text`` verweist (nur mit eingeschalteter Einstellung)."""
    if not text or not enabled()["hijacked"]:
        return []
    sites = load("hijacked")["data"]
    hits, seen = [], set()
    for m in _URL.finditer(text):
        site = _site(m.group(0).rstrip(".,;:"))
        if not site or "doi.org" in site["host"]:
            continue
        parts = site["host"].split(".")
        for i in range(len(parts) - 1):  # auch Subdomains der gekaperten Adresse
            for entry in sites.get(".".join(parts[i:]), []):
                if entry["path"] and not (site["path"] + "/").startswith(entry["path"] + "/"):
                    continue
                key = (site["host"], entry["title"])
                if key not in seen:
                    seen.add(key)
                    hits.append({"site": site["host"] + (entry["path"] or ""), **entry,
                                 "credit": LISTS["hijacked"]["credit"]})
    return hits


def status() -> Dict:
    out = {}
    for name in LISTS:
        cached = load(name, refresh=False)
        out[name] = {"count": len(cached.get("data") or {}), "fetched_at": cached.get("fetched_at") or None,
                     "credit": LISTS[name]["credit"], "page": LISTS[name]["page"]}
    return {"enabled": enabled(), "lists": out}
