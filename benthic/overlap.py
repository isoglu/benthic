"""Übernahmen ohne Anführungszeichen: Sätze einer Arbeit, die Wortlaut aus der zitierten Quelle übernehmen.

Benthic vergleicht jeden Satz, der eine Quelle zitiert, mit dem geladenen Volltext dieser Quelle, ohne die Teile in
Anführungszeichen (gekennzeichnete Zitate prüft quotes.py) und ohne die Zitierklammer selbst. Gemeldet wird nur, was
sich nachprüfen lässt: eine lange wortgleiche Folge oder ein Satz, der fast wörtlich in der Quelle steht, mit Seite
und Stelle. Das ist kein Urteil über Absicht: Fachbegriffe, Definitionen und feste Formeln wiederholen sich, und ob
eine Stelle ein Zitat hätte sein müssen, entscheidet der Mensch, der beide liest. Es braucht keine Sammlung fremder
Arbeiten: verglichen wird nur mit den Quellen, die die Arbeit selbst nennt.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import biblio, quotes, support

NGRAM = 5         # Wortfolgen dieser Länge bilden den Index der Quelle
MIN_RUN = 10      # wortgleiche Folge ab so vielen Wörtern melden
MIN_WORDS = 8     # kürzere Sätze (ohne Zitate und Klammer) nicht prüfen
COVERAGE = 0.7    # oder: so viel des Satzes steht wortgleich in der Quelle
REAL = ("verified", "corrected", "partial")

# Teile in Anführungszeichen (gekennzeichnete Zitate) und die Zitierklammer: nicht mitvergleichen.
_QUOTED = re.compile(r"[\"„“”«»][^\"„“”«»]{2,}?[\"„“”«»]")
_CITE = re.compile(r"\((?:[^()]*?\b(?:1[5-9]|20)\d{2}[a-z]?\b[^()]*?)\)|\[\s*\d[\d,;\s–-]*\]")

_lock = threading.Lock()
_index_cache: Dict[Tuple[str, int], Dict] = {}


def _index(path: Path) -> Dict:
    """Seiten der PDF und die Menge ihrer Wortfolgen, zwischengespeichert, solange die Datei gleich bleibt."""
    key = (str(path), path.stat().st_mtime_ns)
    with _lock:
        if key in _index_cache:
            return _index_cache[key]
    pages = support._pages(path)
    words: List[str] = []
    for _page, text in quotes._page_texts(pages):
        words.extend(w for w, _, _ in quotes._words(text))
    grams = {tuple(words[i:i + NGRAM]) for i in range(len(words) - NGRAM + 1)}
    entry = {"pages": pages, "grams": grams}
    with _lock:
        if len(_index_cache) > 30:
            _index_cache.clear()
        _index_cache[key] = entry
    return entry


def own_words(sentence: str) -> str:
    """Der Satz ohne Teile in Anführungszeichen und ohne Zitierklammern."""
    return re.sub(r"\s+", " ", _CITE.sub(" ", _QUOTED.sub(" ", sentence or ""))).strip()


def compare(sentence: str, grams: Set[Tuple[str, ...]]) -> Tuple[int, float, int]:
    """(längste wortgleiche Folge in Wörtern, Anteil der Wörter in solchen Folgen, Wortzahl)."""
    words = [w for w, _, _ in quotes._words(sentence)]
    covered = [False] * len(words)
    for i in range(len(words) - NGRAM + 1):
        if tuple(words[i:i + NGRAM]) in grams:
            for k in range(i, i + NGRAM):
                covered[k] = True
    run = best = 0
    for c in covered:
        run = run + 1 if c else 0
        best = max(best, run)
    share = sum(covered) / len(words) if words else 0.0
    return best, share, len(words)


def check_document(doc: Dict, results: List[Dict]) -> Dict:
    """Alle zitierenden Sätze einer geprüften Arbeit gegen die geladenen Volltexte ihrer Quellen."""
    by_order = {r.get("order"): r for r in results}
    findings: List[Dict] = []
    missing: Set[str] = set()
    seen: Set[Tuple[object, str]] = set()
    checked = no_source = 0
    for c in doc.get("claims") or []:
        sentence = str(c.get("sentence") or "")
        key = (c.get("entry"), sentence)
        if not sentence or key in seen:
            continue
        seen.add(key)
        r = by_order.get(c.get("entry")) or {}
        doi = r.get("doi") if r.get("verdict") in REAL else None
        if not doi:
            no_source += 1
            continue
        path = biblio.pdf_path(doi)
        if not path:
            missing.add(doi)
            continue
        text = own_words(sentence)
        try:
            idx = _index(path)
        except Exception:  # beschädigte oder verschlüsselte PDF
            continue
        run, share, n = compare(text, idx["grams"])
        checked += 1
        if n < MIN_WORDS or not (run >= MIN_RUN or share >= COVERAGE):
            continue
        loc = quotes.locate(idx["pages"], text)
        if loc.get("status") == "exact" or share >= 0.9:
            kind = "verbatim"
        elif loc.get("status") == "close":
            kind = "close"
        else:
            kind = "partial"
        findings.append({"entry": c.get("entry"), "label": c.get("label"), "doi": doi, "sentence": sentence,
                         "kind": kind, "run": run, "share": round(share, 2), "page": loc.get("page"),
                         "excerpt": loc.get("excerpt") if loc.get("status") in ("exact", "close") or loc.get("nearest") else None})
    order = {"verbatim": 0, "close": 1, "partial": 2}
    findings.sort(key=lambda f: (order[f["kind"]], -f["run"]))
    return {"findings": findings[:200], "checked": checked, "no_source": no_source, "missing_pdfs": sorted(missing)}


def summary(result: Optional[Dict]) -> Dict[str, int]:
    counts = {"verbatim": 0, "close": 0, "partial": 0}
    for f in (result or {}).get("findings") or []:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    return counts
