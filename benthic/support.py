"""Belegstellen finden: welche Passagen eines geladenen Artikels kommen einer Aussage am nächsten?

Benthic urteilt nicht, ob eine Quelle eine Aussage stützt; das bleibt Lesearbeit (für dich oder
deinen KI-Assistenten). Es findet aber die Passagen, die am besten passen, mit Seitenzahl:
BM25 über kurze Abschnitte des Volltexts, dazu ein Bonus für Zahlen, die in Aussage und Passage
vorkommen. "Abdeckung" ist der Anteil der tragenden Wörter der Aussage (nach Seltenheit
gewichtet), der in der Passage steht.

Ohne Netzwerk; die PDF muss in der Bibliothek liegen und darf kein anderes Werk sein (identity.py).
"""

from __future__ import annotations

import math
import re
import threading
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import identity

_STOP = set("""
a an and are as at be been being but by can could did do does for from had has have he her his how i if in
into is it its may might more most no not of on or our she should so such than that the their them then there
these they this those to too was we were what when which while who will with would you your also however thus
therefore within without between among across over under both each other only same very can't cannot
der die das den dem des ein eine einer eines einem einen und oder aber als auch auf aus bei bis da dass
durch für gegen hat haben hatte ich im in ist mit nach nicht noch nur ob sich sie sind so über um und unter
vom von vor war waren was weil wenn wie wir wird werden zu zum zur sowie dabei zudem jedoch sondern
""".split())
_WORD = re.compile(r"[a-zäöüß][a-zäöüß\-]{1,}|\d+(?:[.,]\d+)?%?")
_cache: Dict[Tuple[str, int], List[Dict]] = {}
_lock = threading.Lock()


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


_STOP = {_fold(w) for w in _STOP}


def _stem(w: str) -> str:
    """Sehr einfache Stammform für Englisch und Deutsch (Plural, Endungen), genug für Wortgleichheit."""
    if w[0].isdigit():
        return w.replace(",", ".")
    for suffix in ("ations", "ation", "ments", "ment", "ness", "ities", "ity", "ings", "ing", "ies", "ied",
                   "ungen", "ung", "heiten", "heit", "keiten", "keit", "lichen", "lich", "ischen", "isch",
                   "ers", "ern", "en", "es", "ed", "er", "ly", "s", "e"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def tokens(text: str) -> List[str]:
    # Bindestriche trennen: "ILSVRC-Klassifikationsaufgabe" trifft so auch "ILSVRC" im englischen Text.
    words = (part for w in _WORD.findall(_fold(text)) for part in w.split("-"))
    return [_stem(w) for w in words if w not in _STOP and len(w) > 1]


def _strip_running_headers(pages: List[Dict]) -> List[Dict]:
    """Entfernt wiederkehrende Kopf- und Fußzeilen (sowie Seitenzahlen) aus PDF-Seiten."""
    if len(pages) < 3:
        return pages

    def _norm_pat(line: str) -> str:
        return re.sub(r"\d+", "#", re.sub(r"\s+", " ", line.strip()))

    first_pats: Dict[str, int] = {}
    last_pats: Dict[str, int] = {}
    total = len(pages)

    for p in pages:
        lines = [l.strip() for l in (p.get("text") or "").splitlines() if l.strip()]
        if lines:
            if len(lines[0]) <= 120:
                pat = _norm_pat(lines[0])
                first_pats[pat] = first_pats.get(pat, 0) + 1
            if len(lines[-1]) <= 120:
                pat = _norm_pat(lines[-1])
                last_pats[pat] = last_pats.get(pat, 0) + 1

    threshold = max(2, total * 0.4)
    bad_headers = {pat for pat, cnt in first_pats.items() if cnt >= threshold}
    bad_footers = {pat for pat, cnt in last_pats.items() if cnt >= threshold}

    out = []
    for p in pages:
        text = p.get("text") or ""
        lines = text.splitlines()
        start_idx = 0
        while start_idx < len(lines):
            l = lines[start_idx].strip()
            if not l:
                start_idx += 1
                continue
            if re.match(r"^\d{1,5}$", l) or _norm_pat(l) in bad_headers:
                start_idx += 1
                continue
            break

        end_idx = len(lines)
        while end_idx > start_idx:
            l = lines[end_idx - 1].strip()
            if not l:
                end_idx -= 1
                continue
            if re.match(r"^\d{1,5}$", l) or _norm_pat(l) in bad_footers:
                end_idx -= 1
                continue
            break

        cleaned_text = "\n".join(lines[start_idx:end_idx])
        out.append({"page": p.get("page", 1), "text": cleaned_text})

    return out


def _pages(path: Path) -> List[Dict]:
    """Seiten eines PDFs als Text (zwischengespeichert, solange die Datei gleich bleibt)."""
    key = (str(path), path.stat().st_mtime_ns)
    with _lock:
        if key in _cache:
            return _cache[key]
    import logging
    from pypdf import PdfReader
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(str(path))
    pages = [{"page": i, "text": (p.extract_text() or "")} for i, p in enumerate(reader.pages, start=1)]
    pages = _strip_running_headers(pages)
    with _lock:
        if len(_cache) > 40:
            _cache.clear()
        _cache[key] = pages
    return pages


def passages(pages: List[Dict], size: int = 3) -> List[Dict]:
    """Abschnitte aus je ``size`` Sätzen (überlappend), mit Seitenzahl."""
    out = []
    for p in pages:
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", p["text"])
        text = re.sub(r"\s+", " ", text).strip()
        # Das Literaturverzeichnis des Artikels selbst passt auf fast jede Aussage, gehört aber nicht
        # dazu: Sätze, in deren Umgebung (±300 Zeichen) es nach Verzeichnis aussieht, fallen weg.
        sentences, pos = [], 0
        for part in re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9(\[])", text):
            start = text.find(part, pos)
            pos = start + len(part)
            if len(part) > 20 and not _is_reference_list(text[max(0, start - 300):pos + 300]):
                sentences.append(part)
        for i in range(0, max(1, len(sentences)), max(1, size - 1)):
            chunk = " ".join(sentences[i:i + size]).strip()
            if len(chunk) >= 60:
                out.append({"page": p["page"], "text": chunk[:900]})
    return out


def _is_reference_list(chunk: str) -> bool:
    """Text aus einem Literaturverzeichnis: viele Jahre und Initialen (C. R. Harris, Harris CR) auf engem Raum."""
    years = len(re.findall(r"\b(?:19|20)\d{2}[a-z]?\b", chunk))
    initials = (len(re.findall(r"\b[A-Z]\.\s?(?:[A-Z]|,|and\b|&)", chunk))
                + len(re.findall(r"\b[A-Z][a-z]+ [A-Z]{1,3}[,.]", chunk)))
    return years >= 3 and initials >= 4


def rank(claim: str, chunks: List[Dict], k: int = 3) -> List[Dict]:
    """Die ``k`` Abschnitte, die der Aussage am nächsten kommen (BM25 und Zahlen-Bonus)."""
    # Zitate im Satz selbst ("(He et al., 2015)", "[12]") sind keine Aussage über den Inhalt.
    claim = re.sub(r"\([^()]*\b(?:1[89]|20)\d{2}[a-z]?\b[^()]*\)|\[\d{1,3}(?:\s*[,–-]\s*\d{1,3})*\]", " ", claim)
    query = list(dict.fromkeys(tokens(claim)))
    if not query or not chunks:
        return []
    docs = [tokens(c["text"]) for c in chunks]
    n = len(docs)
    avg = sum(len(d) for d in docs) / n or 1
    df = Counter(t for d in docs for t in set(d))
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in query}
    weight_total = sum(idf.values()) or 1
    numbers = {t for t in query if t[0].isdigit()}
    scored = []
    for c, d in zip(chunks, docs):
        tf = Counter(d)
        score = 0.0
        for t in query:
            if tf[t]:
                score += idf[t] * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avg))
        score += 1.5 * sum(1 for t in numbers if tf[t])
        coverage = sum(idf[t] for t in query if tf[t]) / weight_total
        if score > 0:
            scored.append({**c, "score": round(score, 3), "coverage": round(coverage, 2),
                           "terms": [t for t in query if tf[t]][:12]})
    scored.sort(key=lambda x: -x["score"])
    return scored[:k]


def level(best: Optional[Dict]) -> str:
    """Wie nah die beste Passage kommt: close, partial, weak oder none (kein Urteil über den Inhalt)."""
    if not best:
        return "none"
    if best["coverage"] >= 0.6:
        return "close"
    if best["coverage"] >= 0.35:
        return "partial"
    return "weak"


# Häufige Wörter je Sprache: genug, um Satz und Quelle auseinanderzuhalten (kein allgemeiner Sprachdetektor).
_LANG_WORDS = {
    "en": "the and of to in is that with for are this we be as on by from which was were not have has".split(),
    "de": "der die das und ist nicht mit den von zu sich des auf für eine ein dem wird werden dass sind".split(),
    "fr": "le la les et des est une un du que dans pour pas sur qui avec sont au ce cette".split(),
    "es": "el la los las y de que en un una es por con para se del al como más son".split(),
    "it": "il la che di e è per un una del della non sono con si gli le nel".split(),
    "nl": "de het een en van is dat op te in voor niet met zijn worden door ook".split(),
    "pt": "o a os as de que e do da em um uma para com não é por se dos".split(),
}


def language(text: str) -> Optional[str]:
    """Sprache eines Textes (en, de, fr, es, it, nl, pt) oder None, wenn unsicher."""
    words = re.findall(r"[a-zà-ÿß]+", (text or "").lower())
    if not words:
        return None
    scores = sorted(((sum(1 for w in words if w in set(v)), k) for k, v in _LANG_WORDS.items()), reverse=True)
    (best, code), (second, _) = scores[0], scores[1]
    if best >= 2 and best >= 1.5 * max(second, 1):
        return code
    return None


def find(doi: str, claim: str, k: int = 3, translated: Optional[str] = None) -> Dict:
    """Passagen zu einer Aussage. Ist die Aussage in einer anderen Sprache als die Quelle, meldet das
    ``needs_translation``; mit ``translated`` (der Aussage in der Sprache der Quelle) wird damit gesucht."""
    path, ident = identity.held(doi)
    if not path:
        return {"doi": doi, "available": False}
    if ident.get("status") == identity.CONTRADICTED:
        return {"doi": doi, "available": False, "wrong_file": True, "file_doi": ident.get("file_doi"),
                "file_title": ident.get("file_title"), "error": identity.note(ident)}
    try:
        pages = _pages(path)
        chunks = passages(pages)
    except Exception:  # beschädigte oder verschlüsselte PDF
        return {"doi": doi, "available": False, "unreadable": True}
    sample = " ".join(p["text"] for p in pages[:4])[:20000]
    source_lang, claim_lang = language(sample), language(claim)
    query = translated.strip() if translated and translated.strip() else claim
    top = rank(query, chunks, k)
    return {"doi": doi, "available": True, "file": path.name, "copy": ident.get("copy"), "claim": claim, "passages": top,
            "level": level(top[0] if top else None), "claim_language": claim_lang, "source_language": source_lang,
            "translated": query if query != claim else None,
            "needs_translation": bool(source_lang and claim_lang and source_lang != claim_lang and query == claim)}
