"""Echte Quellen für eine Aussage finden, statt sie von einem Sprachmodell erfinden zu lassen.

Wer eine Aussage belegen will, fragt heute oft einen Chat nach Quellen und bekommt erfundene. Benthic sucht
stattdessen bei OpenAlex nach echten Arbeiten (ohne zurückgezogene, auf Wunsch nur frei lesbare) und zeigt zu
jeder den Satz aus dem Abstract, der der Aussage am nächsten kommt. Das ist ein Vorschlag zum Lesen: Ob eine
Arbeit die Aussage stützt, zeigt erst ihr Text.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from . import oa, support

FIELDS = ("id,doi,title,display_name,publication_year,authorships,primary_location,open_access,"
          "cited_by_count,is_retracted,abstract_inverted_index,type")
_CITE = re.compile(r"\([^()]*\b(?:1[89]|20)\d{2}[a-z]?\b[^()]*\)|\[\d{1,3}(?:\s*[,–-]\s*\d{1,3})*\]")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9À-ɏ])")


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


def find(claim: str, query: Optional[str] = None, open_access_only: bool = False, email: str = "",
         top: int = 6) -> Dict:
    """Bis zu ``top`` echte Arbeiten zur Aussage; ``query`` ist die Suchfassung (etwa übersetzt), sonst die Aussage."""
    text = re.sub(r"\s+", " ", _CITE.sub(" ", query or claim or "")).strip()
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
        passage = _passage(abstract(w.get("abstract_inverted_index")), text)
        oa_info = w.get("open_access") or {}
        out.append({"doi": doi, "title": title, "authors": names[:3], "author_count": len(names),
                    "year": w.get("publication_year"), "type": w.get("type"),
                    "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "",
                    "cited_by": w.get("cited_by_count"), "is_oa": bool(oa_info.get("is_oa")),
                    "oa_url": oa_info.get("oa_url") or "", "passage": passage, "rank": rank_pos})
    # Erst die Arbeiten, deren Abstract die Aussage gut trifft, sonst die Reihenfolge von OpenAlex.
    out.sort(key=lambda s: (-round(((s["passage"] or {}).get("coverage") or 0) * 4), s["rank"]))
    return {"query": text, "suggestions": out[:top]}
