"""Wörtliche Zitate prüfen: steht der zitierte Wortlaut wirklich in der Quelle, und auf der angegebenen Seite?

Sprachmodelle erfinden Zitate so leicht wie Quellen. Benthic sucht jedes wörtliche Zitat einer Arbeit
im geladenen Volltext der zitierten Quelle: wortgleich ("exact"), mit kleinen Abweichungen ("close",
etwa andere Satzzeichen, ein ausgelassenes Wort) oder gar nicht ("not_found"). Verglichen werden Wörter
ohne Groß- und Kleinschreibung, Akzente, Ligaturen und Silbentrennung.

Seitenzahlen: Zeitschriften zählen Seiten anders als die PDF (S. 357 ist oft PDF-Seite 1). Die
angegebene Seite gilt als passend, wenn sie der PDF-Seite oder der gedruckten Seite entspricht
(erste Seite des Artikels aus den Metadaten plus Abstand).

Vorher zeigt die PDF, dass sie das zitierte Werk ist (identity.py); eine Datei, die ein anderes Werk ist, wird nicht
durchsucht. Fehlt ein Zitat, nennt Benthic die längste wortgleiche Folge und sucht es in den anderen geladenen Quellen
desselben Laufs: „steht in Jones 2019, zitiert ist Smith 2020“ ist ein anderer Fehler als ein erfundenes Zitat.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from . import identity, support

CLOSE = 0.8
REAL = ("verified", "corrected", "partial")
MIN_ELSEWHERE = 8  # kürzere Zitate („in the context of“) stehen in fast jeder Quelle
_WORD = re.compile(r"\w+", re.UNICODE)
_MARKER = re.compile(r"\[\d{1,3}(?:\s*[–,-]\s*\d{1,3})*\]")  # Verweise wie [16] oder [3, 7] mitten im Satz
# Getrennte Ligaturen („defi nitions“, „signifi cant“): ein Wort auf diese Endung hängt am nächsten kleingeschriebenen.
# Nicht „ff“: off, staff, stuff enden echt so.
_LIGATURE_END = ("fi", "fl", "ffi", "ffl")


def _fold(word: str) -> str:
    s = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _words(text: str, rejoin: bool = True) -> List[Tuple[str, int, int]]:
    """(Normalform, Anfang, Ende) je Wort; Silbentrennung am Zeilenende wird vorher aufgelöst, getrennte Ligaturen hier
    (``rejoin``)."""
    out: List[Tuple[str, int, int]] = []
    for m in _WORD.finditer(text):
        w = _fold(m.group(0))
        if (rejoin and out and out[-1][0].endswith(_LIGATURE_END) and m.group(0)[0].islower()
                and text[out[-1][2]:m.start()].isspace()):
            out[-1] = (out[-1][0] + w, out[-1][1], m.end())
        else:
            out.append((w, m.start(), m.end()))
    return out


def _page_texts(pages: List[Dict]) -> List[Tuple[int, str]]:
    out = []
    for p in pages:
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", p.get("text") or "")
        text = _MARKER.sub(" ", text.replace("­", ""))
        out.append((p["page"], text))
    return out


def locate(pages: List[Dict], quote: str, exact_only: bool = False) -> Dict:
    """Beste Fundstelle des Zitats in den Seiten: status, page, similarity, excerpt. Getrennte Ligaturen werden gefügt;
    weil das auch echte Wörter verbindet („relever le défi de“), zählt sonst die Lesart ohne Fügen."""
    joined = _locate(pages, quote, True, exact_only)
    if joined["status"] in ("exact", "too_short", "no_text"):
        return joined
    plain = _locate(pages, quote, False, exact_only)
    rank = {"exact": 2, "close": 1}
    return max((joined, plain), key=lambda r: (rank.get(r["status"], 0), r.get("similarity") or 0))


def _locate(pages: List[Dict], quote: str, rejoin: bool, exact_only: bool) -> Dict:
    q = [w for w, _, _ in _words(_MARKER.sub(" ", quote), rejoin)]
    if len(q) < 3:
        return {"status": "too_short"}
    texts = _page_texts(pages)
    flat: List[Tuple[str, int, int, int]] = []  # Wort, Seiten-Index, Anfang, Ende
    for idx, (_page, text) in enumerate(texts):
        flat.extend((w, idx, a, b) for w, a, b in _words(text, rejoin))
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
    if exact_only:
        return {"status": "not_found"}

    # Sonst: Fenster um die seltensten Wörter des Zitats vergleichen
    at: Dict[str, List[int]] = {}
    for k, w in enumerate(words):
        at.setdefault(w, []).append(k)
    anchors = sorted({(len(at[w]), i, w) for i, w in enumerate(q) if len(w) > 3 and w in at})[:3]
    best = (0.0, 0, 0)
    seen_starts = set()
    for _f, offset, w in anchors:
        for gi in at[w]:
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
    else:
        out = {"status": "not_found", "similarity": round(best[0], 2)}
    run, gi = _longest(q, words, at)
    if run >= 3:  # „14 von 22 Wörtern, S. 7“: wie weit das Zitat trägt
        out["longest"] = {"words": run, "of": len(q), "page": texts[flat[gi][1]][0]}
    return out


def _longest(q: List[str], words: List[str], at: Dict[str, List[int]]) -> Tuple[int, int]:
    """Längste wortgleiche Folge aus dem Zitat im Text: (Länge, Position im Text)."""
    best = (0, 0)
    for i, w in enumerate(q):
        if len(q) - i <= best[0]:
            break
        for p in at.get(w, ()):
            k = 1
            while i + k < len(q) and p + k < len(words) and q[i + k] == words[p + k]:
                k += 1
            if k > best[0]:
                best = (k, p)
    return best


def first_page(pages_field: object) -> Optional[int]:
    m = re.match(r"\s*[A-Za-z]?(\d{1,6})", str(pages_field or ""))
    return int(m.group(1)) if m else None


def check(doi: str, quote: str, page: Optional[str] = None, start_page: Optional[int] = None,
          title: Optional[str] = None, pages: object = None) -> Dict:
    """Ein Zitat gegen die geladene PDF einer DOI prüfen; ``title`` und ``pages`` aus den Metadaten für den Ausweis."""
    path, ident = identity.held(doi, title, pages) if doi else (None, {})
    if not path:
        return {"status": "no_pdf", "doi": doi}
    if ident.get("status") == identity.CONTRADICTED:
        return {"status": "wrong_pdf", "doi": doi, "file_doi": ident.get("file_doi"), "file_title": ident.get("file_title")}
    try:
        res = locate(support._pages(path), quote)
    except Exception:  # beschädigte oder verschlüsselte PDF
        return {"status": "unreadable", "doi": doi}
    res["doi"] = doi
    res["copy"] = ident.get("copy")
    res["identity"] = ident.get("status")
    if res["status"] == "not_found" and res["copy"] in identity.INCOMPLETE:
        res["status"] = "incomplete"  # nicht in einer Zusammenfassung oder Vorschau: kein Befund über die Quelle
    found = res.get("page") if res.get("status") in ("exact", "close") else None
    printed = start_page + found - 1 if (start_page and found) else None
    res["printed_page"] = printed
    if res.get("longest") and start_page:
        res["longest"]["page"] = start_page + res["longest"]["page"] - 1
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
    others: Optional[List[Tuple[str, str, List[Dict], Optional[int]]]] = None
    out = []
    for q in doc.get("quotes") or []:
        r = by_order.get(q.get("entry")) or {}
        doi = r.get("doi") if r.get("verdict") in REAL else None
        item = {"quote": q["quote"], "entry": q.get("entry"), "label": q.get("label"), "cited_page": q.get("page"),
                "doi": doi, "verdict": r.get("verdict")}
        if not doi:
            item["status"] = "no_source" if r else "pending"
        else:
            found = r.get("found") or {}
            item.update(check(doi, q["quote"], q.get("page"), first_page(found.get("pages")), found.get("title"),
                              found.get("pages")))
            # Nur ein langes Zitat, das in der veröffentlichten Fassung der zitierten Quelle fehlt (Ausweis bestätigt, keine
            # andere Fassung: einem Preprint fehlt mancher Satz der Zeitschrift), und nur wortgleich anderswo
            if (item["status"] == "not_found" and item.get("identity") == identity.VERIFIED and not item.get("copy")
                    and len(_words(_MARKER.sub(" ", q["quote"]))) >= MIN_ELSEWHERE):
                others = _loaded(results) if others is None else others
                item.update(_elsewhere(q["quote"], doi, others))
                if item.get("found_in"):
                    item["status"] = "misattributed"
                    item.pop("longest", None)  # neben der richtigen Quelle ohne Aussage
        out.append(item)
    return out


def _short(r: Dict) -> str:
    """Kurzname einer Quelle wie „Jones 2019“."""
    f = r.get("found") or {}
    who = " ".join(str(x) for x in ((f.get("authors") or [None])[0], f.get("year")) if x)
    return who or str(f.get("title") or r.get("doi") or "")[:60]


def _loaded(results: List[Dict]) -> List[Tuple[str, str, List[Dict], Optional[int]]]:
    """Die geladenen Volltexte des Laufs, die sich als das zitierte Werk ausweisen oder nicht widerlegt sind."""
    out, seen = [], set()
    for r in results:
        doi = r.get("doi") if r.get("verdict") in REAL else None
        if not doi or doi in seen:
            continue
        seen.add(doi)
        f = r.get("found") or {}
        path, ident = identity.held(doi, f.get("title"), f.get("pages"))
        if not path or ident.get("status") == identity.CONTRADICTED:
            continue
        try:
            out.append((_short(r), doi, support._pages(path), first_page(f.get("pages"))))
        except Exception:  # beschädigte oder verschlüsselte PDF
            continue
    return out


def _elsewhere(quote: str, doi: str, others: List[Tuple[str, str, List[Dict], Optional[int]]]) -> Dict:
    """Steht das Zitat wortgleich in einer anderen Quelle des Laufs? Sonst: in wie vielen anderen es auch fehlt.
    Nur wortgleich: ähnlicher Wortlaut in einer fremden Quelle ist kein Beleg dafür, dass er von dort stammt."""
    searched = 0
    for label, other, pages, start in others:
        if other == doi:
            continue
        searched += 1
        loc = locate(pages, quote, exact_only=True)
        if loc["status"] == "exact":
            return {"found_in": {"label": label, "doi": other, "page": start + loc["page"] - 1 if start else loc["page"]}}
    return {"searched": searched}
