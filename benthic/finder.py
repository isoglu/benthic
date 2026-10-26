"""Echte Quellen für eine Aussage finden, statt sie von einem Sprachmodell erfinden zu lassen.

Wer eine Aussage belegen will, fragt heute oft einen Chat nach Quellen und bekommt erfundene. Benthic sucht
stattdessen bei OpenAlex nach echten Arbeiten (ohne zurückgezogene, auf Wunsch nur frei lesbare) und zeigt zu
jeder den Satz aus dem Abstract, der der Aussage am nächsten kommt. Das ist ein Vorschlag zum Lesen: Ob eine
Arbeit die Aussage stützt, zeigt erst ihr Text.

Statt einer Aussage geht auch ein Suchstring wie in einer Fachdatenbank ("market segmentation" AND churn): Er
geht unverändert an OpenAlex. Jede Suche landet im Suchprotokoll (Datum, Datenbank, Suchstring, Treffer, was
übernommen wurde), damit sich die Recherche im Methodenteil nachvollziehen lässt.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from typing import Dict, List, Optional, Tuple

from . import config, oa, support

FIELDS = ("id,doi,title,display_name,publication_year,authorships,primary_location,open_access,"
          "cited_by_count,is_retracted,abstract_inverted_index,type")
_CITE = re.compile(r"\([^()]*\b(?:1[89]|20)\d{2}[a-z]?\b[^()]*\)|\[\d{1,3}(?:\s*[,–-]\s*\d{1,3})*\]")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9À-ɏ])")
# Suchstring statt Aussage: Operatoren in Großbuchstaben, ein Platzhalter am Wortende oder eine Phrase in
# Anführungszeichen ohne Satzende. Dieselbe Regel steht als SEARCH_STRING in app.js.
_OPERATOR = re.compile(r"\b(?:AND|OR|NOT)\b|\w\*")
_PHRASE = re.compile(r'"[^"]{2,}"')


def abstract(inverted: Optional[Dict[str, List[int]]]) -> str:
    """Abstract aus OpenAlex' invertiertem Index (Wort -> Positionen) wieder zusammensetzen."""
    if not inverted:
        return ""
    slots: Dict[int, str] = {}
    for word, positions in inverted.items():
        for p in positions or []:
            slots[int(p)] = word
    return " ".join(slots[i] for i in sorted(slots))


def _passage(text: str, claim: str) -> Optional[Dict]:
    sentences = [s.strip() for s in _SENT.split(text) if len(s.split()) >= 5]
    best = support.rank(claim, [{"text": s, "page": None} for s in sentences], k=1)
    return {"text": best[0]["text"], "coverage": best[0]["coverage"]} if best else None


def is_search_string(text: str) -> bool:
    text = (text or "").strip()
    return bool(_OPERATOR.search(text)) or (bool(_PHRASE.search(text)) and not text.endswith((".", "!", "?")))


def _search_text(text: str) -> Tuple[str, str]:
    """(an OpenAlex, zum Abgleich der Abstracts): OpenAlex kennt keine Platzhalter, sucht aber ohnehin nach dem
    Wortstamm, also fällt der Stern weg; für den Abgleich zählen nur die Wörter."""
    query = re.sub(r"(\w)\*", r"\1", re.sub(r"\s+", " ", text)).strip()
    return query, re.sub(r'\b(?:AND|OR|NOT)\b|["()]', " ", query)


def find(claim: str, query: Optional[str] = None, open_access_only: bool = False, email: str = "",
         top: int = 6) -> Dict:
    """Bis zu ``top`` echte Arbeiten zur Aussage; ``query`` ist die Suchfassung (etwa übersetzt), sonst die Aussage.
    Ein Suchstring (is_search_string) geht unverändert an OpenAlex."""
    if is_search_string(claim):
        text, plain = _search_text(claim)
        if not plain.split():
            return {"error": "short", "suggestions": []}
    else:
        text = plain = re.sub(r"\s+", " ", _CITE.sub(" ", query or claim or "")).strip()
        if len(text.split()) < 3:
            return {"error": "short", "suggestions": []}
    flt = "is_retracted:false,has_doi:true" + (",is_oa:true" if open_access_only else "")
    params = {"search": text[:300], "filter": flt, "per-page": 15, "select": FIELDS}
    if email and "@" in email and not email.endswith(("example.org", "example.com")):
        params["mailto"] = email
    j = oa._json(oa._api_get("https://api.openalex.org/works", params=params, timeout=20.0))
    if j is None:
        return {"error": "unavailable", "suggestions": []}
    out = []
    for rank_pos, w in enumerate(j.get("results") or []):
        doi = str(w.get("doi") or "").replace("https://doi.org/", "")
        title = w.get("title") or w.get("display_name")
        if not doi or not title or w.get("is_retracted"):
            continue
        names = [((a.get("author") or {}).get("display_name") or "") for a in w.get("authorships") or []]
        names = [n for n in names if n]
        passage = _passage(abstract(w.get("abstract_inverted_index")), plain)
        oa_info = w.get("open_access") or {}
        out.append({"doi": doi, "title": title, "authors": names[:3], "author_count": len(names),
                    "year": w.get("publication_year"), "type": w.get("type"),
                    "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "",
                    "cited_by": w.get("cited_by_count"), "is_oa": bool(oa_info.get("is_oa")),
                    "oa_url": oa_info.get("oa_url") or "", "passage": passage, "rank": rank_pos})
    # Erst die Arbeiten, deren Abstract die Aussage gut trifft, sonst die Reihenfolge von OpenAlex.
    out.sort(key=lambda s: (-round(((s["passage"] or {}).get("coverage") or 0) * 4), s["rank"]))
    total = (j.get("meta") or {}).get("count")
    return {"query": text, "filter": flt, "total": total if isinstance(total, int) else len(out),
            "suggestions": out[:top]}


# ---------------------------------------------------------------------------------------------------------------
# Suchprotokoll: je Suche Datum, Datenbank, exakter Suchstring, Filter, Treffer, angezeigte und übernommene Arbeiten
# ---------------------------------------------------------------------------------------------------------------
LOG_MAX = 1000
_log_lock = threading.RLock()


def _log_path():
    return config.data_dir() / "search_log.json"


def _log_read() -> List[Dict]:
    try:
        data = json.loads(_log_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _log_save(items: List[Dict]) -> None:
    p = _log_path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(items[-LOG_MAX:], indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def log_search(claim: str, result: Dict, project: Optional[str] = None) -> str:
    entry = {"id": uuid.uuid4().hex[:12], "at": time.strftime("%Y-%m-%d %H:%M"), "database": "OpenAlex",
             "claim": claim[:1000], "query": result.get("query", ""), "filter": result.get("filter", ""),
             "total": result.get("total", 0), "shown": [s["doi"] for s in result.get("suggestions") or []],
             "kept": [], "project": project or ""}
    with _log_lock:
        items = _log_read()
        items.append(entry)
        _log_save(items)
    return entry["id"]


def mark_kept(log_id: str, doi: str) -> bool:
    doi = doi.lower()  # DOIs unterscheiden keine Groß- und Kleinschreibung
    with _log_lock:
        items = _log_read()
        for e in items:
            if e.get("id") == log_id:
                if doi and doi not in e["kept"]:
                    e["kept"].append(doi)
                    _log_save(items)
                return True
    return False


LOG_COLUMNS = ["Datum", "Datenbank", "Suchstring", "Filter", "Treffer", "Angezeigt", "Übernommen", "Übernommene DOIs"]


def log_table(project: Optional[str] = None) -> Tuple[List[str], List[Dict]]:
    """Spalten und Zeilen für den Export, älteste Suche zuerst; mit ``project`` nur die Suchen dieses Projekts."""
    rows = []
    for e in _log_read():
        if project and e.get("project") != project:
            continue
        rows.append(dict(zip(LOG_COLUMNS, [e.get("at", ""), e.get("database", ""), e.get("query", ""),
                                            e.get("filter", ""), e.get("total", 0), len(e.get("shown") or []),
                                            len(e.get("kept") or []), " ".join(e.get("kept") or [])])))
    return LOG_COLUMNS, rows
