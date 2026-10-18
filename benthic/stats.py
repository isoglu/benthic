"""Kennzahlen für die Übersicht: Prüf-Trichter, Open-Access-Anteil, Quellen.

Alles wird aus dem Verlauf (die letzten 50 Läufe) und der Bibliothek berechnet,
optional eingeschränkt auf ein Projekt und einen Zeitraum. Pro Quelle zählt der
jüngste Befund (der Verlauf ist neueste zuerst).
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from . import history, library, projects
from .i18n import tr
from .routes import is_route_channel

REAL = ("verified", "corrected", "partial")
FLAGGED = ("retracted", "not_found", "mismatch")
VERDICTS = ("verified", "corrected", "partial", "uncertain", "mismatch", "not_found", "retracted")
SOURCE_LABELS = {
    "arxiv": "arXiv", "openalex": "OpenAlex", "unpaywall": "Unpaywall", "europepmc": "Europe PMC",
    "semantic_scholar": "Semantic Scholar", "core": "CORE", "elsevier": "Elsevier", "crossref": "Crossref",
}


def _key(r: Dict) -> str:
    return str(r.get("doi") or r.get("identifier") or r.get("input") or "").lower()


def _download_kind(r: Dict) -> Optional[str]:
    st, ch = r.get("status"), r.get("channel")
    if st == "ok":
        return "route" if is_route_channel(ch) else "oa"
    if st in ("skipped", "present"):
        return "library"
    if st == "blocked" or (st == "error" and "Open Access bekannt" in str(r.get("error") or "")):
        return "blocked"
    if st in ("absent", "error"):
        return "none"
    return None


def compute(project: Optional[str] = None, days: Optional[int] = None) -> Dict:
    runs = history.load()
    if days:
        cutoff = time.time() - days * 86400
        runs = [r for r in runs if (r.get("finished_at") or r.get("created_at") or 0) >= cutoff]
    proj = projects.find(project) if project else None
    if project and not proj:
        raise KeyError(project)
    if proj:
        runs = [r for r in runs if r.get("project") == proj["id"]]

    refs: Dict[str, Dict] = {}
    loads: Dict[str, Dict] = {}
    for run in runs:  # neueste zuerst: der erste Befund gewinnt
        for r in run.get("results") or []:
            k = _key(r)
            if not k:
                continue
            if run.get("mode") == "verify":
                refs.setdefault(k, r)
            elif run.get("mode") in ("download", "check"):
                loads.setdefault(k, {**r, "_mode": run.get("mode")})

    lib = library.load()
    lib_lower = {k.lower(): v for k, v in lib.items()}

    # Trichter: genannt → echt → frei verfügbar → in der Bibliothek (über Open Access)
    verdicts = {v: 0 for v in VERDICTS}
    routes: Dict[str, int] = {}
    real_dois = set()
    for r in refs.values():
        v = r.get("verdict")
        if v in verdicts:
            verdicts[v] += 1
        if r.get("resolved_by"):
            routes[r["resolved_by"]] = routes.get(r["resolved_by"], 0) + 1
        if v in REAL and r.get("doi"):
            real_dois.add(str(r["doi"]).lower())

    def legal_in_library(d: str) -> bool:
        e = lib_lower.get(d)
        return bool(e) and not is_route_channel(e.get("channel"))

    def legal_available(d: str) -> bool:
        if legal_in_library(d):
            return True
        l = loads.get(d)
        if not l:
            return False
        return _download_kind(l) in ("oa", "library", "blocked") or l.get("status") == "oa"

    funnel = [
        {"key": "claimed", "label": tr("Genannt"), "value": len(refs)},
        {"key": "real", "label": tr("Echt"), "value": len(real_dois)},
        {"key": "available", "label": tr("Frei verfügbar"), "value": sum(1 for d in real_dois if legal_available(d))},
        {"key": "library", "label": tr("In der Bibliothek"), "value": sum(1 for d in real_dois if legal_in_library(d))},
    ]

    # Open-Access-Anteil: wie die Download-Läufe ausgingen
    dl = {"oa": 0, "library": 0, "blocked": 0, "route": 0, "none": 0}
    sources: Dict[str, int] = {}
    for l in loads.values():
        if l.get("_mode") != "download":
            continue
        kind = _download_kind(l)
        if kind:
            dl[kind] += 1
        if kind == "oa":
            name = SOURCE_LABELS.get(str(l.get("source")), str(l.get("source") or "unbekannt"))
            sources[name] = sources.get(name, 0) + 1
    loaded = dl["oa"] + dl["route"]

    flagged = [{"title": (r.get("claimed") or {}).get("title") or (r.get("found") or {}).get("title") or r.get("identifier"),
                "doi": r.get("doi"), "verdict": r.get("verdict"), "note": r.get("note")}
               for r in refs.values() if r.get("verdict") in FLAGGED][:8]

    in_scope = [e for k, e in lib_lower.items() if not proj or k in {i.lower() for i in (proj.get("items") or {})}]
    return {
        "scope": {"project": proj["id"] if proj else None, "project_name": proj["name"] if proj else None,
                  "days": days, "runs": len(runs)},
        "kpi": {
            "checked": len(refs),
            "real_share": round(100 * len(real_dois) / len(refs)) if refs else None,
            "retracted": verdicts["retracted"],
            "flagged": sum(verdicts[v] for v in FLAGGED),
            "legal_share": round(100 * dl["oa"] / loaded) if loaded else None,
            "pdfs": sum(1 for e in in_scope if e.get("filename")),
        },
        "funnel": funnel,
        "verdicts": verdicts,
        "routes": routes,
        "downloads": dl,
        "sources": sorted(({"name": k, "value": v} for k, v in sources.items()), key=lambda x: -x["value"]),
        "flagged": flagged,
        "library": {"total": len(in_scope), "with_pdf": sum(1 for e in in_scope if e.get("filename"))},
    }
