"""Preprints und ihre veröffentlichte Fassung: wurde ein zitierter Preprint inzwischen in einer Zeitschrift
oder einem Tagungsband veröffentlicht?

Zuerst die Verknüpfung, die der Preprint-Server bei Crossref hinterlegt (``relation.is-preprint-of``,
etwa bei bioRxiv und medRxiv); sonst eine Suche bei Crossref nach Titel, Erstautor:in und Jahr, mit
strengem Titelvergleich. arXiv-DOIs liegen bei DataCite, dort hilft nur die Suche.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Dict, List, Optional

from . import oa, refs

PREFIXES = {
    "10.48550": "arXiv", "10.1101": "bioRxiv/medRxiv", "10.2139": "SSRN", "10.21203": "Research Square",
    "10.20944": "Preprints.org", "10.31234": "PsyArXiv", "10.31235": "SocArXiv", "10.31219": "OSF Preprints",
    "10.36227": "TechRxiv", "10.26434": "ChemRxiv", "10.31222": "MetaArXiv", "10.32942": "EcoEvoRxiv",
}
PREPRINT_TYPES = ("posted-content", "preprint", "repository")
SIMILAR = 0.93


def server(doi: str) -> Optional[str]:
    prefix = (doi or "").split("/", 1)[0]
    return PREFIXES.get(prefix)


def is_preprint(doi: str, found: Optional[Dict] = None) -> bool:
    return bool(server(doi)) or str((found or {}).get("type") or "") in PREPRINT_TYPES


def _mailto(email: str) -> Dict[str, str]:
    if email and "@" in email and not email.endswith(("example.org", "example.com")):
        return {"mailto": email}
    return {}


def _summary(item: Dict, via: str) -> Dict:
    parts = ((item.get("issued") or item.get("published") or {}).get("date-parts") or [[None]])[0]
    return {"doi": item.get("DOI"), "title": ((item.get("title") or [""])[0]) or "",
            "venue": (item.get("container-title") or [None])[0], "year": str(parts[0]) if parts and parts[0] else None,
            "type": item.get("type"), "via": via}


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, refs.normalize_title(a), refs.normalize_title(b)).ratio()


def published_version(doi: str, title: str, authors: List[str], year: Optional[str], email: str = "") -> Optional[Dict]:
    """Die veröffentlichte Fassung eines Preprints, oder None."""
    from .verify import crossref_work
    if doi and not doi.lower().startswith("10.48550/"):
        m = crossref_work(doi, email) or {}
        for rel in ((m.get("relation") or {}).get("is-preprint-of") or []):
            if rel.get("id-type") == "doi" and rel.get("id"):
                pub = crossref_work(rel["id"], email)
                if pub and str(pub.get("type")) not in PREPRINT_TYPES:
                    return _summary(pub, "relation")
        title = title or ((m.get("title") or [""])[0])
    if not title or len(title.split()) < 3:
        return None
    first = refs.normalize_title(authors[0]).split()[-1] if authors else ""
    params = {"query.bibliographic": title[:300], "rows": 5,
              "filter": "type:journal-article,type:proceedings-article,type:book-chapter"
                        + (f",from-pub-date:{int(year) - 1}" if year and str(year).isdigit() else ""),
              "select": "DOI,title,author,issued,published,container-title,type", **_mailto(email)}
    if first:
        params["query.author"] = first
    j = oa._json(oa._api_get("https://api.crossref.org/works", params=params)) or {}
    best = None
    for item in (j.get("message") or {}).get("items") or []:
        if str(item.get("DOI", "")).lower() == str(doi).lower():
            continue
        cand_title = (item.get("title") or [""])[0]
        sim = _similar(title, cand_title)
        families = [refs.normalize_title(a.get("family") or "") for a in item.get("author") or []]
        if sim < SIMILAR or (first and first not in " ".join(families).split()):
            continue
        if not best or sim > best[0]:
            best = (sim, item)
    return _summary(best[1], "title") if best else None


def check_results(results: List[Dict], email: str = "") -> int:
    """Ergänzt bestätigte Preprints im Prüfbericht um ``published_version``; gibt die Zahl der Funde zurück."""
    from concurrent.futures import ThreadPoolExecutor
    todo = [r for r in results if r.get("doi") and r.get("verdict") in ("verified", "corrected", "partial")
            and is_preprint(r["doi"], r.get("found"))]

    def one(r: Dict) -> None:
        found, claimed = r.get("found") or {}, r.get("claimed") or {}
        try:
            pub = published_version(r["doi"], found.get("title") or claimed.get("title") or "",
                                    found.get("authors") or claimed.get("authors") or [],
                                    found.get("year") or claimed.get("year"), email)
        except Exception:
            pub = None
        if pub:
            r["published_version"] = {**pub, "server": server(r["doi"]) or "Preprint"}
    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(one, todo))
    return sum(1 for r in todo if r.get("published_version"))
