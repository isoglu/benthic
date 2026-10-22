"""Paste anything: findet Literaturangaben in beliebigem Text.

Erkennt BibTeX, RIS und Freitext (LLM-Antworten, Literaturlisten, Markdown,
reine DOI-Listen). Pro Eintrag entsteht eine Referenz mit den Angaben, die der
Text *behauptet* (Titel, Autor:innen, Jahr, Zeitschrift) und, falls vorhanden,
einer Kennung (DOI, arXiv, PMID). Ob die Angaben stimmen, prüft ``verify``.

Dieses Modul arbeitet ohne Netzwerk.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Dict, List, Optional
from urllib.parse import unquote
from xml.etree import ElementTree

Ref = Dict[str, object]

# --------------------------------------------------------------------------
# Kennungen
# --------------------------------------------------------------------------
# DOI: Präfix 10.NNNN/ und ein Suffix ohne Leerraum und ohne typische Begrenzer.
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>{}|\\^`\[\]]+", re.IGNORECASE)
_DOI_URL_RE = re.compile(r"(?:https?://)?(?:dx\.)?doi\.org/(10\.\d{4,9}/\S+)", re.IGNORECASE)
_ARXIV_RE = re.compile(
    r"(?:arxiv(?:\.org/(?:abs|pdf)/|:\s*|\s+)|10\.48550/arxiv\.)"
    r"((?:\d{4}\.\d{4,5})|(?:[a-z\-]+(?:\.[A-Z]{2})?/\d{7}))(?:v\d+)?",
    re.IGNORECASE,
)
_PMID_RE = re.compile(r"\bPMID:?\s*(\d{5,9})\b", re.IGNORECASE)
_ISBN_RE = re.compile(
    r"\b(?:ISBN(?:-1[03])?:?\s*)?((?:97[89][-\s]?)?(?:\d[-\s]?){9}[\dXx])\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})[a-z]?\b")
_TRAILING = ".,;:'\"”’»)]}>"


def clean_isbn(value: str) -> Optional[str]:
    """Extrahiert und prüft eine ISBN-10 oder ISBN-13 (Prüfziffer muss stimmen, damit Telefonnummern oder Seitenbereiche nicht matchen)."""
    if not value:
        return None
    for m in _ISBN_RE.finditer(value):
        digits = re.sub(r"[^0-9Xx]", "", m.group(1)).upper()
        if len(digits) == 13 and digits.startswith(("978", "979")) and digits.isdigit():
            s = sum((1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(digits[:12]))
            if (10 - (s % 10)) % 10 == int(digits[12]):
                return digits
        elif len(digits) == 10 and digits[:9].isdigit():
            s = sum((10 - i) * (10 if d == "X" else int(d)) for i, d in enumerate(digits))
            if s % 11 == 0:
                return digits
    return None


def clean_doi(value: str) -> Optional[str]:
    """Bereinigt eine DOI aus beliebigem Kontext (URL, BibTeX, Satzende)."""
    if not value:
        return None
    s = unquote(value.strip())
    m = _DOI_RE.search(s)
    if not m:
        return None
    doi = m.group(0)
    # Satzzeichen am Ende entfernen; ")" nur, wenn es keine offene "(" schließt,
    # denn DOIs wie 10.1016/S0140-6736(97)11096-0 enthalten Klammern.
    while doi and doi[-1] in _TRAILING:
        if doi[-1] == ")" and doi.count("(") >= doi.count(")"):
            break
        doi = doi[:-1]
    # Dateiendungen und Anker aus kopierten Links abschneiden.
    doi = re.sub(r"(?:\.pdf|/full|/abstract|/pdf|#.*)$", "", doi, flags=re.IGNORECASE)
    return doi if re.match(r"^10\.\d{4,9}/.+", doi) else None


def arxiv_doi(arxiv_id: str) -> str:
    """arXiv-ID → DataCite-DOI (ohne Versionsnummer)."""
    return "10.48550/arXiv." + re.sub(r"v\d+$", "", arxiv_id)


# --------------------------------------------------------------------------
# Hilfen für Titel und Namen
# --------------------------------------------------------------------------
_FOLD = str.maketrans({"ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "ł": "l", "đ": "d", "ð": "d", "þ": "th", "ı": "i"})


def fold(s: str) -> str:
    """Kleinbuchstaben ohne Akzente, ß als ss, Ligaturen aufgelöst: die eine Faltung für Namen und Titel."""
    s = unicodedata.normalize("NFKD", (s or "").lower().translate(_FOLD))
    return "".join(c for c in s if not unicodedata.combining(c))


_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})


def name_key(name: str, transliterate: bool = False) -> str:
    """Vergleichsschlüssel für Nachnamen: gefaltet, nur Buchstaben, auch chinesische oder kyrillische (王伟 bleibt
    王伟). Mit ``transliterate`` werden ä, ö, ü zu ae, oe, ue; das ist nur der zweite Versuch, damit Xu und Xue
    oder Bauer und Baur zwei Namen bleiben."""
    s = (name or "").lower()
    return "".join(c for c in fold(s.translate(_UMLAUT) if transliterate else s) if c.isalpha())


def same_name(a: str, b: str) -> bool:
    """Gleicher Nachname: genau gleich nach der Faltung (Müller, Muller) oder gleich umschrieben (Müller, Mueller)."""
    ka, kb = name_key(a), name_key(b)
    return bool(ka) and (ka == kb or name_key(a, True) == name_key(b, True))


def normalize_title(title: str) -> str:
    """Kleinbuchstaben, ohne Akzente, Satzzeichen und LaTeX-Reste."""
    s = re.sub(r"\\[a-zA-Z]+\s*", " ", fold(title))
    s = re.sub(r"[^0-9a-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------
# Band, Heft und Seiten, wie die Angabe sie nennt
# --------------------------------------------------------------------------
# DOIs und Links fallen vorher weg: Eine Elsevier-PII wie 10.1016/s0377-2217(01)00049-2 sähe sonst aus wie
# Heft 01 und die Seiten 00049-2.
_IDS = re.compile(r"https?://\S+|doi:\s*\S+|\b10\.\d{4,9}/\S+|\bISBN(?:-1[03])?:?\s*[0-9Xx\-\s]{10,17}|"
                  r"\b(?:arxiv|PMID):?\s*\S+", re.IGNORECASE)
_RANGE = r"([A-Za-z]{0,2}\d{1,7})(?:\s*[-\u2013\u2014]{1,2}\s*([A-Za-z]{0,2}\d{1,7}))?"
_ISSUE_VAL = r"(\d{1,4}(?:\s*[-–/]\s*\d{1,4})?)"
_PRINTED = (  # (Muster, Felder) in der Reihenfolge, in der sie gelten; das erste Muster gewinnt je Feld
    (re.compile(r";\s*(\d{1,4})\s*(?:\(" + _ISSUE_VAL + r"\))?\s*:\s*" + _RANGE), ("volume", "issue", "first", "last")),  # 2015;521(7553):436-44
    (re.compile(r"(?<![\w.])(\d{1,4})\s*\(" + _ISSUE_VAL + r"\)\s*[,:]\s*(?:pp?\.\s*|S\.\s*)?" + _RANGE + r"(?![\w/])"),
     ("volume", "issue", "first", "last")),                                                          # 12(3), 45-67
    (re.compile(r",\s*(\d{1,4})\s*,\s*(?:pp?\.\s*)?([A-Za-z]{0,2}\d{1,7})\s*[-\u2013\u2014]{1,2}\s*([A-Za-z]{0,2}\d{1,7})(?![\w/])"),
     ("volume", "first", "last")),                                                                   # , 585, 357-362
    # „Band“ nur groß und nach einem Komma: „the 5 GHz band 3“ in einem englischen Titel ist kein Band
    (re.compile(r"\b(?:[Vv]ol\.?|[Vv]olume|Bd\.|Jg\.)\s*(\d{1,4})\b|(?:^|[,;:]\s*)(?:Band|Jahrgang)\s+(\d{1,4})\b"),
     ("volume", "volume")),
    (re.compile(r"(?<![\w.])(\d{1,4}),?\s+no\.\s*\d", re.IGNORECASE), ("volume",)),                   # 12, no. 3 (Chicago)
    (re.compile(r"\b(?:no\.|nr\.|issue|heft)\s*" + _ISSUE_VAL + r"\b|,\s*H\.\s*(\d{1,3})\b", re.IGNORECASE), ("issue", "issue")),
    (re.compile(r"(?:\bpp?\.|,\s*S\.|\bSeiten|\bpages)\s*" + _RANGE + r"(?![\w/])", re.IGNORECASE), ("first", "last")),
    (re.compile(r"\bno\.\s*\d{1,4}\s*:\s*" + _RANGE, re.IGNORECASE), ("first", "last")),                # no. 3: 45-67
)


def _last_page(first: str, last: str) -> str:
    """Abgekürzte Endseite ausschreiben: 436-44 → 444."""
    if first.isdigit() and last.isdigit() and len(last) < len(first):
        full = first[:len(first) - len(last)] + last
        return full if int(full) >= int(first) else last
    return last


def pages_text(value: object) -> Optional[str]:
    """Seiten einheitlich: 45--67, 45 - 67 und 45–7 werden zu 45–67."""
    v = str(value or "").strip()
    m = re.fullmatch(_RANGE, v)
    if m:
        return m.group(1) + ("–" + _last_page(m.group(1), m.group(2)) if m.group(2) else "")
    return v or None


def _span(value: object) -> tuple:
    first, _, last = str(pages_text(value) or "").lower().partition("–")
    first, last = first.lstrip("0"), last.lstrip("0")
    return first, ("" if last == first else last)  # OpenAlex: Artikelnummer 2232 als „2232–2232“


def pages_disagree(a: object, b: object) -> bool:
    """Nennen zwei Seitenangaben verschiedene Seiten? Nur wenn sie vergleichbar sind: eine Artikelnummer (e0232930)
    gegen Seitenzahlen nicht, eine Zählung ab Seite 1 (1-15, oft die Seiten im Artikel) nicht, eine erste Seite
    allein gegen den Bereich, der mit ihr beginnt, nicht."""
    (af, al), (bf, bl) = _span(a), _span(b)
    if not af or not bf or af.isdigit() != bf.isdigit() or "1" in (af, bf):
        return False
    return af != bf or bool(al and bl and al != bl)


def issues_disagree(a: object, b: object) -> bool:
    """Verschiedene Hefte? Ein Doppelheft 3-4 enthält Heft 3."""
    na, nb = (set(re.findall(r"\d+", str(v or ""))) for v in (a, b))
    return bool(na and nb) and not na & nb


def printed_fields(text: str) -> Dict[str, str]:
    """Band, Heft und Seiten aus einer Angabe in Freitext (ohne Netzwerk). Nur eindeutige Muster: lieber
    nichts lesen als eine Jahreszahl für einen Band halten."""
    t = re.sub(r"[*_]", "", _IDS.sub(" ", text or ""))  # Markdown-Kursiv: *Organization Science, 13*(3)
    t = re.sub(r"\((?:1[89]|20)\d{2}[a-z]?\b[^)]*\)", " ", t)  # (2020), (2020, März)
    out: Dict[str, str] = {}
    for i, (pattern, names) in enumerate(_PRINTED):
        m = pattern.search(t)
        if not m:
            continue
        got: Dict[str, str] = {}
        for name, value in zip(names, m.groups()):
            if value and name not in got:
                got[name] = value
        if i < 3 and "journal" not in out:  # „… Titel. Journal of X, 12(3), 45-67“: der Teil vor dem Band
            segs = [x.strip(" ,;:") for x in re.split(r"[.?!]\s+", t[:m.start()])]
            segs = [x for x in segs if x and not re.fullmatch(r"[\d\s]+", x)]
            if segs and re.search(r"[^\W\d_]{2}", segs[-1]) and len(segs[-1]) <= 150:
                out["journal"] = segs[-1]
        if "first" in got and "pages" not in out:
            first, last = got.get("first") or "", got.get("last") or ""
            if first and not (not last and re.fullmatch(r"(?:1[89]|20)\d{2}", first)):  # „S. 2021“ ist ein Jahr
                out["pages"] = pages_text(first + ("–" + last if last else ""))
        for key in ("volume", "issue"):
            if got.get(key) and key not in out and not re.fullmatch(r"(?:1[89]|20)\d{2}", got[key]):
                out[key] = re.sub(r"\s+", "", got[key])
    return out


# DOIs, die Band, Heft und erste Seite tragen. Nur Verlage, deren Schema das sicher so meint, jeder mit einem Test:
# AMA 10.1509/jmkr.39.4.421.19114, APA 10.1037/0003-066X.49.12.997, INFORMS 10.1287/mksc.20.1.1.10197,
# Annual Reviews 10.1146/annurev.psych.52.1.1, AEA 10.1257/jep.29.3.3, Endocrine Society 10.1210/jc.87.5.2090,
# dazu die SICI-DOIs (…(199709)18:8<637::AID-SMJ…>). Dieselbe Form anderswo sagt nichts: in 10.1210/jcem.87.5.8477
# ist 8477 eine Manuskriptnummer, das Werk steht auf den Seiten 2033-2041.
_DOI_VIP = re.compile(r"^10\.(?:1509/[a-z]+|1037//?\d{4}-\d{3}[\dx]|1287/[a-z]+|1146/annurev\.[a-z]+|1257/jep|1210/jc)"
                      r"\.(\d{1,4})\.(\d{1,3})\.(\d{1,6})(?:\.\d+)?$", re.IGNORECASE)
_DOI_SICI = re.compile(r"\(SICI\)[\dX-]+\(\d{4,8}\)(\d{1,4}):(\d{1,3})(?:<(\d{1,6})::)?", re.IGNORECASE)


def doi_fields(doi: str) -> Dict[str, str]:
    """Band, Heft und erste Seite, die eine DOI selbst nennt (ohne Netzwerk); sonst {}."""
    m = _DOI_VIP.match(doi or "") or _DOI_SICI.search(doi or "")
    if not m or re.fullmatch(r"(?:1[89]|20)\d{2}", m.group(1)):  # ein Jahr an der Stelle des Bands: im Zweifel nichts
        return {}
    return {k: v for k, v in zip(("volume", "issue", "first_page"), m.groups()) if v}


def title_similarity(a: Optional[str], b: Optional[str]) -> float:
    na, nb = normalize_title(a or ""), normalize_title(b or "")
    if not na or not nb:
        return 0.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    short, long_ = sorted((na, nb), key=len)
    # Untertitel oder ganze Zitatzeile: der kürzere Titel steckt im längeren.
    if len(short) >= 20 and short in long_:
        ratio = max(ratio, 0.95)
    return ratio


def _strip_latex(s: str) -> str:
    s = s.replace("\\&", "&").replace("~", " ").replace("--", "–")
    s = re.sub(r"\\[`'^\"~=.uvHtcdb]\{?([A-Za-z])\}?", r"\1", s)  # {\"o} → o
    s = re.sub(r"\\[a-zA-Z]+\{([^{}]*)\}", r"\1", s)  # \emph{x} → x
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def _surname(name: str) -> str:
    """Nachname aus "Nachname, Vorname" oder "Vorname Nachname"."""
    name = name.strip().strip(".")
    if not name:
        return ""
    if "," in name:
        return name.split(",")[0].strip()
    parts = name.split()
    # Partikel wie "van der" gehören zum Nachnamen.
    i = len(parts) - 1
    while i > 0 and parts[i - 1].lower() in ("van", "von", "der", "den", "de", "da", "di", "del", "la", "le"):
        i -= 1
    return " ".join(parts[i:])


def _surnames_from_text(segment: str) -> List[str]:
    """Nachnamen aus einer Autorenangabe in Freitext (APA, MLA, "et al.")."""
    segment = re.sub(r"\bet al\.?", "", segment)
    found = re.findall(r"((?:(?:[Vv]an|[Vv]on|[Dd]e|[Dd]er|[Dd]en|[Dd]a|[Dd]i|[Ll]e|[Ll]a|[Tt]en|[Tt]er)\s+)*[A-ZÄÖÜ][\w'\-]+),\s*(?:[A-Z]\.\s*-?)+", segment)
    if found:
        return found[:8]
    first = re.match(r"\s*((?:(?:[Vv]an|[Vv]on|[Dd]e|[Dd]er)\s+)*[A-ZÄÖÜ][\w'\-]+)", segment)
    return [first.group(1)] if first else []


def _empty(fmt: str, raw: str) -> Ref:
    return {"format": fmt, "raw": raw.strip()[:400], "doi": None, "arxiv": None, "pmid": None,
            "isbn": None, "title": None, "authors": [], "year": None, "venue": None}


# --------------------------------------------------------------------------
# BibTeX
# --------------------------------------------------------------------------
def _bibtex_entries(text: str) -> List[str]:
    entries, i = [], 0
    while True:
        m = re.search(r"@\s*\w+\s*[{(]", text[i:])
        if not m:
            return entries
        start = i + m.start()
        j, depth = i + m.end(), 1
        while j < len(text) and depth:
            if text[j] in "{(":
                depth += 1
            elif text[j] in "})":
                depth -= 1
            j += 1
        entries.append(text[start:j])
        i = j


def _bibtex_fields(entry: str) -> Dict[str, str]:
    body = entry[entry.find("{") + 1 if "{" in entry else 0:]
    fields, i = {}, 0
    for m in re.finditer(r"(\w[\w\-]*)\s*=\s*", body):
        if m.start() < i:
            continue
        name, j = m.group(1).lower(), m.end()
        if j >= len(body):
            break
        if body[j] == "{":
            depth, k = 1, j + 1
            while k < len(body) and depth:
                depth += {"{": 1, "}": -1}.get(body[k], 0)
                k += 1
            value = body[j + 1:k - 1]
        elif body[j] == '"':
            k = body.find('"', j + 1)
            k = len(body) if k < 0 else k + 1
            value = body[j + 1:k - 1]
        else:
            k = j
            while k < len(body) and body[k] not in ",}\n":
                k += 1
            value = body[j:k]
        fields[name] = _strip_latex(value)
        i = k
    return fields


def parse_bibtex(text: str) -> List[Ref]:
    refs = []
    for entry in _bibtex_entries(text):
        if re.match(r"@\s*(comment|string|preamble)\b", entry, re.IGNORECASE):
            continue
        f = _bibtex_fields(entry)
        r = _empty("bibtex", entry)
        r["title"] = f.get("title") or None
        names = [a for a in re.split(r"\s+and\s+", f.get("author", "")) if a.strip()]
        r["et_al"] = any(a.strip().lower() == "others" for a in names)
        names = [a for a in names if a.strip().lower() != "others"]
        r["authors"] = [_surname(a) for a in names][:8]
        r["author_count"] = len(names)
        r["volume"], r["issue"] = f.get("volume") or None, f.get("number") or f.get("issue") or None
        r["pages"] = pages_text(f.get("pages"))
        year = _YEAR_RE.search(f.get("year", "") or f.get("date", ""))
        r["year"] = year.group(1) if year else None
        r["venue"] = f.get("journal") or f.get("booktitle") or None
        r["doi"] = clean_doi(f.get("doi", "")) or clean_doi(f.get("url", ""))
        eprint = f.get("eprint", "")
        if not r["doi"] and (f.get("archiveprefix", "").lower() == "arxiv" or re.match(r"^\d{4}\.\d{4,5}", eprint)):
            r["arxiv"] = re.sub(r"v\d+$", "", eprint) or None
        if not r["arxiv"] and not r["doi"]:
            m = _ARXIV_RE.search(f.get("url", "") + " " + f.get("journal", ""))
            r["arxiv"] = m.group(1) if m else None
        pmid = f.get("pmid", "")
        r["pmid"] = pmid if pmid.isdigit() else None
        r["isbn"] = clean_isbn(f.get("isbn", ""))
        m_key = re.match(r"@\s*(\w+)\s*[{(]\s*([^,\s]+)\s*,", entry)
        r["bib_type"] = m_key.group(1).lower() if m_key else "article"
        r["bib_key"] = m_key.group(2).strip() if m_key else None
        refs.append(r)
    return refs


# --------------------------------------------------------------------------
# RIS
# --------------------------------------------------------------------------
def parse_ris(text: str) -> List[Ref]:
    refs: List[Ref] = []
    cur: Optional[Ref] = None
    lines: List[str] = []
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])  - ?(.*)$", line.rstrip())
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur, lines = _empty("ris", ""), []
        if cur is None:
            continue
        lines.append(line)
        if tag in ("TI", "T1") and not cur["title"]:
            cur["title"] = val
        elif tag in ("AU", "A1"):
            cur["authors"].append(_surname(val))  # type: ignore[union-attr]
            cur["author_count"] = len(cur["authors"])  # type: ignore[arg-type]
        elif tag in ("VL", "IS") and val:
            cur["volume" if tag == "VL" else "issue"] = val
        elif tag == "SP" and val:
            cur["pages"] = pages_text(val)
        elif tag == "EP" and val and "–" not in str(cur.get("pages") or ""):  # SP 357-362 trägt die Endseite schon
            cur["pages"] = pages_text((str(cur.get("pages") or "") + "–" + val).strip("–"))
        elif tag in ("PY", "Y1", "DA") and not cur["year"]:
            y = _YEAR_RE.search(val)
            cur["year"] = y.group(1) if y else None
        elif tag == "DO":
            cur["doi"] = clean_doi(val)
        elif tag in ("UR", "L1", "L2") and not cur["doi"]:
            cur["doi"] = clean_doi(val)
        elif tag in ("JO", "JF", "T2") and not cur["venue"]:
            cur["venue"] = val
        elif tag == "AN" and val.isdigit():
            cur["pmid"] = val
        elif tag == "SN":
            cur["isbn"] = clean_isbn(val) or cur.get("isbn")
        if tag == "ER":
            cur["raw"] = "\n".join(lines)[:400]
            refs.append(cur)
            cur = None
    return refs


# --------------------------------------------------------------------------
# EndNote XML
# --------------------------------------------------------------------------
def parse_endnote_xml(text: str) -> List[Ref]:
    """Liest Literaturangaben aus EndNote-XML (<records><record>)."""
    if not text or "<record" not in text.lower():
        return []
    try:
        root = ElementTree.fromstring(text)
    except Exception:
        m = re.search(r"<records\b[^>]*>.*?</records>", text, re.DOTALL | re.IGNORECASE)
        if not m:
            return []
        try:
            root = ElementTree.fromstring(m.group(0))
        except Exception:
            return []

    refs: List[Ref] = []
    for rec in root.iter():
        tag = rec.tag.split("}")[-1].lower()
        if tag != "record":
            continue

        r = _empty("endnote_xml", "")
        for el in rec.iter():
            ltag = el.tag.split("}")[-1].lower()
            if ltag == "title" and not r["title"]:
                t_val = "".join(el.itertext()).strip()
                if t_val:
                    r["title"] = t_val
            elif ltag in ("secondary-title", "full-title") and not r["venue"]:
                v_val = "".join(el.itertext()).strip()
                if v_val:
                    r["venue"] = v_val
            elif ltag == "author":
                a_val = "".join(el.itertext()).strip()
                if a_val:
                    r["authors"].append(_surname(a_val))
            elif ltag in ("volume", "number", "pages"):
                key = "issue" if ltag == "number" else ltag
                r[key] = r.get(key) or "".join(el.itertext()).strip() or None
                r["pages"] = pages_text(r.get("pages"))
            elif ltag == "year" and not r["year"]:
                y_val = "".join(el.itertext()).strip()
                ym = _YEAR_RE.search(y_val)
                if ym:
                    r["year"] = ym.group(1)
            elif ltag == "electronic-resource-num":
                val = "".join(el.itertext()).strip()
                r["doi"] = clean_doi(val) or r.get("doi")
                r["isbn"] = clean_isbn(val) or r.get("isbn")
            elif ltag == "isbn" and not r.get("isbn"):
                val = "".join(el.itertext()).strip()
                r["isbn"] = clean_isbn(val)
            elif ltag == "url" and not r.get("doi"):
                val = "".join(el.itertext()).strip()
                r["doi"] = clean_doi(val) or r.get("doi")

        rec_str = ElementTree.tostring(rec, encoding="utf-8").decode("utf-8", errors="replace")
        r["raw"] = rec_str[:400]
        if r.get("title") or r.get("doi") or r.get("isbn") or r.get("authors"):
            refs.append(r)

    return refs


# --------------------------------------------------------------------------
# Freitext: LLM-Antworten, Literaturlisten, Markdown, DOI-Listen
# --------------------------------------------------------------------------
_LIST_PREFIX = re.compile(r"^\s*(?:[-*•–]\s+|\d{1,3}[.)]\s+|\[\d{1,3}\]\s*)")


def _apa_title(line: str, year_match: Optional[re.Match]):
    """(Titel, Endposition) nach dem Jahr im APA-Muster, wenn er nicht kursiv beginnt; sonst None."""
    if not year_match:
        return None
    start = year_match.end()
    lead = re.match(r"^[a-z]?\)?[.,:]?\s*", line[start:])
    start += lead.end() if lead else 0
    seg = re.split(r"\.\s+(?=[A-Z0-9*])|\s+https?://|\s+doi:", line[start:])[0].strip()
    if len(seg.split()) < 2 or seg.startswith("*") or re.match(r"^\d", seg):
        return None
    return seg.rstrip(".,"), start + len(seg)


def _claimed_title(line: str, year_match: Optional[re.Match]) -> Optional[str]:
    """Titel aus einer Zeile: zuerst in Anführungszeichen, sonst APA-Muster."""
    q = re.search(r"[\"“„«‘]([^\"”“»’]{12,300})[\"”“»’]", line)
    if q:
        return q.group(1).strip().rstrip(".,")
    q = re.search(r"\*([^*]{12,300})\*", line)  # Markdown-Kursiv
    if q:
        italic = q.group(1).strip().rstrip(".,")
        apa = _apa_title(line, year_match)
        # APA: kursiv ist bei Artikeln die Zeitschrift ("*Organization Science, 13*(3)"), bei Büchern der Titel.
        journal = re.search(r",\s*\d", italic) or re.match(r"\s*\(\d", line[q.end():q.end() + 3])
        if apa and (q.start() >= apa[1] or journal):
            return apa[0]
        return italic
    if year_match:
        after = line[year_match.end():]
        after = re.sub(r"^[a-z]?\)?[.,:]?\s*", "", after)
        t = re.split(r"\.\s+(?=[A-Z0-9])|\s+https?://|\s+doi:", after)[0].strip()
        if len(t.split()) >= 2 and not re.match(r"^\d", t):
            return t.rstrip(".,")
    return None


def _text_ref(line: str) -> Optional[Ref]:
    raw = line.strip()
    body = _LIST_PREFIX.sub("", raw)
    body = re.sub(r"[*_`]{2,}", "", body)  # Markdown-Fett
    r = _empty("text", raw)
    url_doi = _DOI_URL_RE.search(body)
    r["doi"] = clean_doi(url_doi.group(1)) if url_doi else clean_doi(body)
    if r["doi"] and r["doi"].lower().startswith("10.48550/arxiv."):
        r["arxiv"] = r["doi"].split(".", 2)[2] if r["doi"].count(".") >= 2 else None
    a = _ARXIV_RE.search(body)
    if a and not r["doi"]:
        r["arxiv"] = a.group(1)
    p = _PMID_RE.search(body)
    if p:
        r["pmid"] = p.group(1)
    r["isbn"] = clean_isbn(body)

    # Text ohne Kennungen und URLs: daraus Titel, Jahr und Autor:innen lesen.
    plain = re.sub(r"ISBN(?:-1[03])?:?\s*[0-9Xx\-\s]{10,17}", " ", body, flags=re.IGNORECASE)
    plain = re.sub(r"https?://\S+|doi:\s*\S+|10\.\d{4,9}/\S+|PMID:?\s*\d+|arxiv:\s*\S+", " ", plain, flags=re.IGNORECASE)
    plain = re.sub(r"\s+", " ", plain).strip(" .,;:-–")
    # "Titel. arXiv." (APA für Preprints): der Server ist kein Teil des Titels.
    plain = re.sub(r"[.,]\s*(?:arXiv|bioRxiv|medRxiv|SSRN|PsyArXiv|SocArXiv|Preprints?)(?:\s+preprint)?\s*$", "", plain,
                   flags=re.IGNORECASE)
    year = re.search(r"\((1[89]\d{2}|20\d{2})[a-z]?\)", plain) or _YEAR_RE.search(plain)
    r["year"] = year.group(1) if year else None
    r["title"] = _claimed_title(plain, year)
    italic = re.search(r"\*([^*]{3,200})\*", plain)
    if italic and r["title"] and italic.group(1).strip().rstrip(".,") != r["title"]:
        r["venue"] = re.sub(r",\s*\d.*$", "", italic.group(1)).strip()  # "*Organization Science, 13*" → Zeitschrift
    head = plain[:year.start()] if year else plain.split(".")[0]
    r["authors"] = _surnames_from_text(head) if (year or r["title"]) else []
    r["et_al"] = bool(re.search(r"\bet al\b|\bu\. ?a\.", head))
    r.update(printed_fields(body))
    initials = _initials_first(plain)
    if initials:  # "Y. Bengio, P. Simard, and P. Frasconi. Titel. Zeitschrift, 1994." (IEEE, CVPR)
        r["authors"], title = initials
        if title and not re.search(r"[\"“„«‘]", line):
            r["title"] = title

    has_id = bool(r["doi"] or r["arxiv"] or r["pmid"] or r["isbn"])
    looks_like_ref = bool(r["title"]) or (bool(r["year"]) and len(plain.split()) >= 6)
    if not has_id and not looks_like_ref:
        return None  # Überschrift, Fließtext, Leerzeile
    if not r["title"] and len(plain.split()) >= 4:
        r["query"] = plain[:300]  # ganze Angabe als Suchtext
    return r


_INITIALS_START = re.compile(r"^(?:[A-Z]\.\s?-?){1,4}\s*(?:(?:van|von|de|der|den|da|di|le|la)\s+)*[A-ZÄÖÜ][\w'’\-]+")


def _initials_first(plain: str):
    """(Nachnamen, Titel) bei Angaben, die mit Initialen beginnen, sonst None.

    Getrennt wird an Punkten, die nicht nach einer Initiale stehen: "C. R. Harris et al. Array …".
    """
    if not _INITIALS_START.match(plain):
        return None
    segs = [x.strip() for x in re.split(r"(?<![A-Z])\.\s+", plain) if x.strip()]
    if len(segs) < 2:
        return None
    names = re.findall(r"(?:[A-Z]\.\s?-?)+\s*((?:(?:van|von|de|der|den|da|di|le|la)\s+)*[A-ZÄÖÜ][\w'’\-]+)", segs[0])
    title = segs[1].rstrip(".,") if len(segs[1].split()) >= 2 else None
    return names[:8], title


def parse_text(text: str) -> List[Ref]:
    refs: List[Ref] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        # Reine Listen von Kennungen ("10.1/a; 10.2/b") in Einzelteile zerlegen.
        tokens = [t for t in re.split(r"[;,\s]+", line.strip()) if t]
        if len(tokens) > 1 and all(clean_doi(t) or _ARXIV_RE.search(t) for t in tokens):
            for t in tokens:
                ref = _text_ref(t)
                if ref:
                    refs.append(ref)
            continue
        ref = _text_ref(line)
        if ref:
            refs.append(ref)
    return refs


# --------------------------------------------------------------------------
# Einstieg
# --------------------------------------------------------------------------
def detect_format(text: str) -> str:
    if re.search(r"@\s*(article|book|inproceedings|incollection|misc|phdthesis|techreport|online|report|conference)\s*[{(]", text, re.IGNORECASE):
        return "bibtex"
    if re.search(r"^TY  - ", text, re.MULTILINE) and re.search(r"^ER  -", text, re.MULTILINE):
        return "ris"
    lower_start = (text or "")[:1000].lower()
    if ("<records" in lower_start or "<xml" in lower_start) and "<record" in (text or "").lower():
        return "endnote_xml"
    return "text"


def _key(r: Ref) -> str:
    if r.get("doi"):
        return "doi:" + str(r["doi"]).lower()
    if r.get("arxiv"):
        return "arxiv:" + str(r["arxiv"]).lower()
    if r.get("pmid"):
        return "pmid:" + str(r["pmid"])
    return "t:" + normalize_title(str(r.get("title") or r.get("query") or r.get("raw")))


def parse(text: str) -> List[Ref]:
    """Findet alle Literaturangaben in ``text`` (ohne Duplikate, Reihenfolge bleibt)."""
    fmt = detect_format(text or "")
    if fmt == "bibtex":
        found = parse_bibtex(text)
    elif fmt == "ris":
        found = parse_ris(text)
    elif fmt == "endnote_xml":
        found = parse_endnote_xml(text)
    else:
        found = parse_text(text or "")
    out, seen = [], {}
    for r in found:
        # arXiv-Kennung ohne DOI: die DataCite-DOI ist bekannt und eindeutig.
        if r.get("arxiv") and not r.get("doi"):
            r["doi"] = arxiv_doi(str(r["arxiv"]))
        k = _key(r)
        # Dieselbe DOI mit anderem Jahr, Band oder anderen Seiten bleibt stehen: der Widerspruch ist ein Befund.
        if k in seen and not any(r.get(f) and seen[k].get(f) and str(r[f]) != str(seen[k][f])
                                 for f in ("year", "volume", "pages")):
            continue
        seen.setdefault(k, r)
        r["label"] = label(r)
        out.append(r)
    return out


def label(r: Ref) -> str:
    """Kurze Anzeige für Fortschritt und Protokoll."""
    if r.get("title"):
        return str(r["title"])[:90]
    if r.get("doi"):
        return str(r["doi"])
    if r.get("pmid"):
        return "PMID " + str(r["pmid"])
    return str(r.get("query") or r.get("raw") or "")[:90]


def summary(refs: List[Ref]) -> Dict[str, object]:
    """Zählt, was gefunden wurde (für die Vorschau beim Einfügen)."""
    fmt = refs[0]["format"] if refs else None
    return {
        "count": len(refs),
        "with_doi": sum(1 for r in refs if r.get("doi")),
        "with_pmid": sum(1 for r in refs if r.get("pmid") and not r.get("doi")),
        "title_only": sum(1 for r in refs if not r.get("doi") and not r.get("pmid")),
        "format": fmt,
    }
