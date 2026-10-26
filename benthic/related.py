"""Schlüsselwerke, die fehlen: welche Arbeiten zitieren deine Quellen immer wieder, ohne dass sie in deiner
Liste stehen?

Benthic liest die Literaturverzeichnisse deiner bestätigten Quellen (OpenAlex ``referenced_works``,
gebündelt; sonst die offenen Referenzlisten bei Crossref), zählt, wie viele deiner Quellen ein Werk
zitieren, und zeigt die häufigsten, die nicht schon in deiner Liste stehen. Das ersetzt keine Recherche,
ist aber ein schneller Blick, ob ein Grundlagentext fehlt.

Die andere Richtung des Schneeballverfahrens (citing_works): neuere Arbeiten, die eine Quelle zitieren, die
meistzitierten zuerst. Sie zeigen, wie die Forschung ein Modell seitdem weitergeführt oder kritisiert hat.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from typing import Dict, Iterable, List, Optional

from . import oa

BATCH = 50
MAX_SOURCES = 120


def _mailto(email: str) -> Dict[str, str]:
    if email and "@" in email and not email.endswith(("example.org", "example.com")):
        return {"mailto": email}
    return {}


def _chunks(items: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _openalex_refs(dois: List[str], email: str) -> Dict[str, List[str]]:
    """DOI → OpenAlex-IDs der zitierten Werke."""
    out: Dict[str, List[str]] = {}
    for group in _chunks(dois, BATCH):
        j = oa._json(oa._api_get("https://api.openalex.org/works", params={
            "filter": "doi:" + "|".join(group), "select": "id,doi,referenced_works", "per_page": len(group),
            **_mailto(email)})) or {}
        for w in j.get("results") or []:
            doi = str(w.get("doi") or "").lower().replace("https://doi.org/", "")
            if w.get("referenced_works"):
                out[doi] = [str(r).rsplit("/", 1)[-1] for r in w["referenced_works"]]
    return out


def _openalex_meta(ids: List[str], email: str) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for group in _chunks(ids, BATCH):
        j = oa._json(oa._api_get("https://api.openalex.org/works", params={
            "filter": "openalex:" + "|".join(group), "per_page": len(group), **_mailto(email),
            "select": "id,doi,display_name,publication_year,authorships,cited_by_count,primary_location,type"})) or {}
        for w in j.get("results") or []:
            key = str(w.get("id") or "").rsplit("/", 1)[-1]
            names = [((a.get("author") or {}).get("display_name") or "") for a in w.get("authorships") or []]
            out[key] = {"doi": str(w.get("doi") or "").replace("https://doi.org/", "") or None,
                        "title": w.get("display_name") or "", "year": w.get("publication_year"),
                        "authors": [oa._surname(n) for n in names if n][:3], "author_count": len(names),
                        "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
                        "cited_by": w.get("cited_by_count"), "type": w.get("type")}
    return out


def _crossref_refs(dois: List[str], email: str) -> Dict[str, List[str]]:
    """Fallback: offene Referenzlisten bei Crossref (DOIs der zitierten Werke)."""
    from .verify import crossref_work
    out: Dict[str, List[str]] = {}
    for doi in dois:
        m = crossref_work(doi, email) or {}
        cited = [str(r.get("DOI")).lower() for r in m.get("reference") or [] if r.get("DOI")]
        if cited:
            out[doi] = cited
    return out


def _crossref_meta(dois: List[str], email: str) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for group in _chunks([d for d in dois if "," not in d], 40):
        j = oa._json(oa._api_get("https://api.crossref.org/works", params={
            "filter": ",".join(f"doi:{d}" for d in group), "rows": len(group), **_mailto(email),
            "select": "DOI,title,author,issued,container-title,is-referenced-by-count,type"})) or {}
        for it in (j.get("message") or {}).get("items") or []:
            parts = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
            out[str(it.get("DOI")).lower()] = {
                "doi": it.get("DOI"), "title": ((it.get("title") or [""])[0]), "year": parts[0] if parts else None,
                "authors": [a.get("family") or a.get("name") or "" for a in it.get("author") or []][:3],
                "author_count": len(it.get("author") or []),
                "venue": (it.get("container-title") or [None])[0], "cited_by": it.get("is-referenced-by-count"),
                "type": it.get("type")}
    return out


def missing_works(dois: Iterable[str], email: str = "", top: int = 12) -> Dict:
    """Die Werke, die deine Quellen am häufigsten zitieren und die in deiner Liste fehlen."""
    own = []
    for d in dois:
        d = str(d or "").strip().lower()
        if d and d not in own:
            own.append(d)
    own = own[:MAX_SOURCES]
    source = "openalex"
    refs_by_doi = _openalex_refs(own, email) if own else {}
    if not refs_by_doi and own:
        source, refs_by_doi = "crossref", _crossref_refs(own, email)
    n = len(refs_by_doi)
    if n < 2:
        return {"sources": n, "of": len(own), "source": source, "suggestions": []}
    counts = Counter(r for cited in refs_by_doi.values() for r in set(cited))
    least = max(2, round(0.2 * n)) if n >= 5 else 2
    candidates = [k for k, c in counts.most_common(top * 4) if c >= least]
    meta = _openalex_meta(candidates, email) if source == "openalex" else _crossref_meta(candidates, email)
    out = []
    for key in candidates:
        m = meta.get(key) or meta.get(key.lower())
        if not m or not m.get("title"):
            continue
        if m.get("doi") and str(m["doi"]).lower() in own:
            continue  # steht schon in der Liste
        out.append({**m, "count": counts[key]})
        if len(out) >= top:
            break
    return {"sources": n, "of": len(own), "source": source, "least": least, "suggestions": out}


def works_by_doi(dois: List[str], email: str = "") -> Dict[str, Dict]:
    """Autor:innen, Jahr, Titel, Ort und Art je DOI aus OpenAlex, gebündelt; was fehlt, fehlt (offline: leer)."""
    out: Dict[str, Dict] = {}
    for group in _chunks([d for d in dois if d], BATCH):
        j = oa._json(oa._api_get("https://api.openalex.org/works", params={
            "filter": "doi:" + "|".join(group), "per_page": len(group), **_mailto(email),
            "select": "doi,display_name,publication_year,authorships,primary_location,type"})) or {}
        for w in j.get("results") or []:
            names = [((a.get("author") or {}).get("display_name") or "") for a in w.get("authorships") or []]
            out[str(w.get("doi") or "").lower().replace("https://doi.org/", "")] = {
                "title": w.get("display_name") or "", "year": w.get("publication_year"), "type": w.get("type") or "",
                "authors": [oa._surname(n) for n in names if n],
                "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""}
    return out


def citing_works(doi: str, since: Optional[int] = None, email: str = "", top: int = 8) -> Optional[Dict]:
    """Arbeiten mit DOI, die ``doi`` zitieren, ab ``since`` (sonst die letzten fünf Jahre), meistzitierte zuerst,
    ohne zurückgezogene. None, wenn OpenAlex nicht antwortet oder das Werk nicht kennt."""
    w = oa._json(oa._api_get(f"https://api.openalex.org/works/doi:{doi}", params={"select": "id,cited_by_count",
                                                                                   **_mailto(email)}))
    if not w or not w.get("id"):
        return None
    since = since or time.localtime().tm_year - 4
    wid = str(w["id"]).rsplit("/", 1)[-1]
    j = oa._json(oa._api_get("https://api.openalex.org/works", params={
        "filter": f"cites:{wid},publication_year:>{since - 1},is_retracted:false,has_doi:true",
        "sort": "cited_by_count:desc", "per-page": top, **_mailto(email),
        "select": "id,doi,display_name,publication_year,authorships,cited_by_count,primary_location,type,open_access"}))
    if j is None:
        return None
    out = []
    for c in j.get("results") or []:
        names = [((a.get("author") or {}).get("display_name") or "") for a in c.get("authorships") or []]
        out.append({"doi": str(c.get("doi") or "").replace("https://doi.org/", ""), "title": c.get("display_name") or "",
                    "year": c.get("publication_year"), "authors": [oa._surname(n) for n in names if n][:3],
                    "author_count": len(names), "cited_by": c.get("cited_by_count"), "type": c.get("type"),
                    "venue": ((c.get("primary_location") or {}).get("source") or {}).get("display_name"),
                    "is_oa": bool((c.get("open_access") or {}).get("is_oa"))})
    total = (j.get("meta") or {}).get("count")
    return {"doi": doi, "since": since, "cited_by": w.get("cited_by_count"),
            "total": total if isinstance(total, int) else len(out), "works": [x for x in out if x["doi"] and x["title"]]}


def alternatives(query: str, email: str = "", top: int = 4) -> List[Dict]:
    """Echte Arbeiten zum Thema einer Angabe, die es so nicht gibt (Nicht auffindbar, Passt nicht).

    Benthic sucht mit dem angegebenen Titel (sonst dem ganzen Eintrag) bei OpenAlex und Crossref und nimmt die
    nächsten Treffer mit DOI, ohne zurückgezogene und ohne Errata, Rückzugsnotizen, Kommentare oder Antworten. Das sind Vorschläge zum Lesen, kein Ersatz, den man ungelesen
    zitiert; die App sagt das dazu.
    """
    from . import verify  # verify importiert related nicht; so bleibt die Reihenfolge der Importe frei

    q = re.sub(r"https?://\S+|\b10\.\d{4,9}/\S+", " ", str(query or ""))  # DOIs und Links gehören zu anderen Werken
    q = re.sub(r"^\s*(?:\[\d+\]|\(\d+\)|\d+[.)])\s+", "", re.sub(r"\s+", " ", q)).strip()
    if len(q) < 8:
        return []
    out: List[Dict] = []
    seen = set()
    for hit in verify._search_openalex(q, email) + verify._search_crossref(q, email):
        doi = str(hit.get("doi") or "").lower()
        if not doi or doi in seen or not hit.get("title") or hit.get("retracted") or hit.get("notice"):
            continue
        seen.add(doi)
        out.append({k: hit.get(k) for k in ("doi", "title", "authors", "year", "venue", "type", "is_oa", "cited_by")})
        if len(out) >= top:
            break
    return out
