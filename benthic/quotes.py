"""Wörtliche Zitate prüfen: steht der zitierte Wortlaut wirklich in der Quelle, und auf der angegebenen Seite?

Sprachmodelle erfinden Zitate so leicht wie Quellen. Benthic sucht jedes wörtliche Zitat einer Arbeit
im geladenen Volltext der zitierten Quelle: wortgleich ("exact"), mit kleinen Abweichungen ("close",
etwa andere Satzzeichen, ein ausgelassenes Wort) oder gar nicht ("not_found"). Verglichen werden Wörter
ohne Groß- und Kleinschreibung, Akzente, Ligaturen und Silbentrennung.

Seitenzahlen: Zeitschriften zählen Seiten anders als die PDF (S. 357 ist oft PDF-Seite 1). Die
angegebene Seite gilt als passend, wenn sie der PDF-Seite oder der gedruckten Seite entspricht
(erste Seite des Artikels aus den Metadaten plus Abstand).
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from . import biblio, support

CLOSE = 0.8
_WORD = re.compile(r"\w+", re.UNICODE)


def _fold(word: str) -> str:
    s = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _words(text: str) -> List[Tuple[str, int, int]]:
    """(Normalform, Anfang, Ende) je Wort; Silbentrennung am Zeilenende wird vorher aufgelöst."""
    return [(_fold(m.group(0)), m.start(), m.end()) for m in _WORD.finditer(text)]


def _page_texts(pages: List[Dict]) -> List[Tuple[int, str]]:
    out = []
    for p in pages:
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", p.get("text") or "")
        text = text.replace("­", "")
        out.append((p["page"], text))
    return out


def locate(pages: List[Dict], quote: str) -> Dict:
    """Beste Fundstelle des Zitats in den Seiten: status, page, similarity, excerpt."""
    q = [w for w, _, _ in _words(quote)]
    if len(q) < 3:
        return {"status": "too_short"}
    texts = _page_texts(pages)
    flat: List[Tuple[str, int, int, int]] = []  # Wort, Seiten-Index, Anfang, Ende
    for idx, (_page, text) in enumerate(texts):
        flat.extend((w, idx, a, b) for w, a, b in _words(text))
    if not flat:
        return {"status": "no_text"}
    words = [f[0] for f in flat]

    def excerpt(i: int, j: int) -> str:
        i, j = max(0, i), min(len(flat), j)
        parts = []
        for idx in sorted({flat[k][1] for k in range(i, j)}):
            ks = [k for k in range(i, j) if flat[k][1] == idx]
            text = texts[idx][1]
            parts.append(re.sub(r"\s+", " ", text[flat[ks[0]][2]:flat[ks[-1]][3]]))
        return " ".join(parts)

    def result(status: str, i: int, j: int, sim: float) -> Dict:
        return {"status": status, "page": texts[flat[i][1]][0], "similarity": round(sim, 2), "excerpt": excerpt(i, j)}

    # Wortgleich: als Wortfolge irgendwo im Text (auch über einen Seitenwechsel)
    joined = " " + " ".join(words) + " "
    needle = " " + " ".join(q) + " "
    pos = joined.find(needle)
    if pos >= 0:
        start = joined.count(" ", 0, pos + 1) - 1
        return result("exact", start, start + len(q), 1.0)

    # Sonst: Fenster um die seltensten Wörter des Zitats vergleichen
    freq: Dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    anchors = sorted({(freq.get(w, 0), i, w) for i, w in enumerate(q) if len(w) > 3 and freq.get(w)})[:3]
    best = (0.0, 0, 0)
    seen_starts = set()
    for _f, offset, w in anchors:
        for gi in (k for k, x in enumerate(words) if x == w):
            start = max(0, gi - offset - 2)
            if start in seen_starts:
                continue
            seen_starts.add(start)
            window = words[start:start + len(q) + 4]
            sm = SequenceMatcher(None, q, window, autojunk=False)
            ratio = sm.ratio() * (len(q) + len(window)) / (2 * len(q))  # an der Zitatlänge gemessen
            if ratio > best[0]:
                blocks = [b for b in sm.get_matching_blocks() if b.size]
                i = start + (blocks[0].b if blocks else 0)
                j = start + (blocks[-1].b + blocks[-1].size if blocks else len(q))
                best = (min(ratio, 1.0), i, j)
    if best[0] >= CLOSE:
        return result("close", best[1], best[2], best[0])
    if best[0] >= 0.5:
        out = result("not_found", best[1], best[2], best[0])
        out["nearest"] = True  # die ähnlichste Stelle, zur Ansicht
        return out
    return {"status": "not_found", "similarity": round(best[0], 2)}


def first_page(pages_field: object) -> Optional[int]:
    m = re.match(r"\s*[A-Za-z]?(\d{1,6})", str(pages_field or ""))
    return int(m.group(1)) if m else None


def check(doi: str, quote: str, page: Optional[str] = None, start_page: Optional[int] = None) -> Dict:
    """Ein Zitat gegen die geladene PDF einer DOI prüfen."""
    path = biblio.pdf_path(doi) if doi else None
    if not path:
        return {"status": "no_pdf", "doi": doi}
    try:
        res = locate(support._pages(path), quote)
    except Exception:  # beschädigte oder verschlüsselte PDF
        return {"status": "unreadable", "doi": doi}
    res["doi"] = doi
    found = res.get("page") if res.get("status") in ("exact", "close") else None
    printed = start_page + found - 1 if (start_page and found) else None
    res["printed_page"] = printed
    try:
        stated = int(page) if page else None
    except ValueError:
        stated = None
    res["stated_page"] = stated
    res["page_ok"] = (stated in (found, printed)) if (stated and found) else None
    return res


def check_document(doc: Dict, results: List[Dict]) -> List[Dict]:
    """Alle wörtlichen Zitate einer geprüften Arbeit; ``results`` sind die Ergebnisse des Laufs."""
    by_order = {r.get("order"): r for r in results}
    out = []
    for q in doc.get("quotes") or []:
        r = by_order.get(q.get("entry")) or {}
        doi = r.get("doi") if r.get("verdict") in ("verified", "corrected", "partial") else None
        item = {"quote": q["quote"], "entry": q.get("entry"), "label": q.get("label"), "cited_page": q.get("page"),
                "doi": doi, "verdict": r.get("verdict")}
        if not doi:
            item["status"] = "no_source" if r else "pending"
        else:
            item.update(check(doi, q["quote"], q.get("page"), first_page((r.get("found") or {}).get("pages"))))
        out.append(item)
    return out
