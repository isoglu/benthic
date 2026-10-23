"""Ganze Arbeiten prüfen: Text aus PDF oder Word, Literaturverzeichnis und Zitate im Text.

Aus einer Abschlussarbeit (PDF, DOCX, Text oder Markdown) holt dieses Modul

1. das Literaturverzeichnis: die Überschrift (References, Bibliography, Literatur, ...) und
   danach die Einträge, die das PDF oft über mehrere Zeilen umbricht;
2. die Zitate im Text: Autor-Jahr ("Smith, 2020", "Müller et al. (2019, S. 4)") oder Nummern
   ("[3]", "[1-4]"), jeweils mit dem Satz, in dem sie stehen;
3. den Abgleich: welche Zitate im Verzeichnis fehlen und welche Einträge nie zitiert werden.

Ob die Einträge echte Werke sind, prüft danach ``verify`` wie bei jeder Literaturliste.
Dieses Modul arbeitet ohne Netzwerk.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree

from . import integrity, refs

MAX_BYTES = 60 * 1024 * 1024
# Entpackt höchstens so viel aus einer Word-Datei oder einem LaTeX-Archiv: Ein Zip von wenigen KB kann sonst
# Hunderte MB XML oder Text ergeben. Gezählt wird die Größe, die das Archiv angibt; zipfile liest nie mehr.
MAX_UNPACKED = 200 * 1024 * 1024
TYPES = (".pdf", ".docx", ".txt", ".md", ".markdown")

# Überschriften des Literaturverzeichnisses, auf einer eigenen Zeile (auch nummeriert: "7 Literatur").
_HEADINGS = (r"references|reference list|bibliography|works cited|literature cited|literature|sources|"
             r"literaturverzeichnis|literatur|quellenverzeichnis|quellen|bibliographie|bibliografie|"
             r"literatur- und quellenverzeichnis")
_HEADING_RE = re.compile(r"^\s*(?:[0-9IVX]{1,4}\.?\s+)?(" + _HEADINGS + r")\s*:?\s*$", re.IGNORECASE | re.MULTILINE)
# Danach endet das Verzeichnis: Anhang, Erklärung, Lebenslauf.
_END_RE = re.compile(r"^\s*(?:[0-9IVX]{1,4}\.?\s+|[A-Z]\s+)?(appendix|appendices|anhang|anlagen?|"
                     r"eidesstattliche erklärung|ehrenwörtliche erklärung|selbstständigkeitserklärung|"
                     r"declaration of authorship|statutory declaration|curriculum vitae|lebenslauf)\b.*$",
                     re.IGNORECASE | re.MULTILINE)

_PARTICLES = r"(?:[Vv]an|[Vv]on|[Dd]e|[Dd]er|[Dd]en|[Dd]a|[Dd]i|[Dd]u|[Dd]el|[Dd]ella|[Ll]e|[Ll]a|[Tt]en|[Tt]er|[Zz]u)"
_NAME = r"[A-ZÄÖÜÉÈÁÀÓÒÚÍŁŚŻ][\w'’\-]+"
# Ein Eintrag beginnt mit "Nachname, I." oder "Nachname, Vorname" (APA, Harvard, Chicago, DIN).
_ENTRY_AUTHOR = re.compile(r"^\s*(?:" + _PARTICLES + r"\s+)*" + _NAME + r"(?:\s+" + _NAME + r")?,\s+"
                           r"(?:[A-ZÄÖÜ]\.|[A-ZÄÖÜ][a-zäöüß]+)")
_ENTRY_NUMBER = re.compile(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})[.)])\s+")
_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})[a-z]?\b")

_SECONDARY_RE = re.compile(
    r"\((?:[^()]{0,120}?\b(?:zit\.?\s*nach|zitiert\s+nach|cited\s+in|quoted\s+in|as\s+cited\s+in)\b[^()]{0,120})\)",
    re.IGNORECASE,
)
_EMPIRICAL_CLAIM_RE = re.compile(
    r"(?:\b(?:studien\s+(?:zeigen|belegen|weisen\s+darauf\s+hin)|forschung(?:en)?\s+(?:zeigt|belegt)|"
    r"untersuchungen\s+(?:zeigen|ergaben)|laut\s+einer\s+(?:studie|umfrage|erhebung)|"
    r"studies\s+(?:show|demonstrate|indicate|have\s+shown)|research\s+(?:shows|indicates|suggests)|"
    r"empirical\s+evidence\s+(?:shows|suggests)|meta-analys[ei]s\s+show)\b|"
    r"\b\d+(?:[.,]\d+)?\s*(?:%|Prozent|percent|per\s+cent)\b|"
    r"\bp\s*[<=]\s*0?[.,]\d+\b)",
    re.IGNORECASE,
)
_SELF_DATA_RE = re.compile(
    r"\b(?:tabelle|abbildung|kapitel|abschnitt|stichprobe|eigene\s+erhebung|unsere\s+befragung|"
    r"table|figure|section|chapter|our\s+sample|our\s+survey|in\s+this\s+thesis|in\s+dieser\s+arbeit)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Text aus Dateien
# --------------------------------------------------------------------------
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _caps(rpr) -> Optional[bool]:
    """Kapitälchen oder Großbuchstaben in einem w:rPr: True, False (ausdrücklich aus) oder None."""
    if rpr is None:
        return None
    for tag in ("smallCaps", "caps"):
        el = rpr.find(_W + tag)
        if el is not None:
            return el.get(_W + "val", "true").lower() not in ("0", "false", "off")
    return None


def _caps_styles(z: zipfile.ZipFile) -> set:
    """Formatvorlagen (Zeichen und Absatz), die Kapitälchen oder Großbuchstaben setzen, mit Vererbung."""
    if "word/styles.xml" not in z.namelist():
        return set()
    own, parent = {}, {}
    for st in ElementTree.fromstring(z.read("word/styles.xml")).iter(_W + "style"):
        sid = st.get(_W + "styleId")
        if not sid:
            continue
        own[sid] = _caps(st.find(_W + "rPr"))
        based = st.find(_W + "basedOn")
        if based is not None:
            parent[sid] = based.get(_W + "val")
    result = set()
    for sid in own:
        cur, seen = sid, set()
        while cur and cur not in seen and own.get(cur) is None:
            seen.add(cur)
            cur = parent.get(cur)
        if cur and own.get(cur):
            result.add(sid)
    return result


def unpacked_size(z: zipfile.ZipFile, names) -> int:
    """Summe der entpackten Größen dieser Einträge; ValueError("size") über MAX_UNPACKED."""
    infos = {i.filename: i for i in z.infolist()}
    total = sum(infos[n].file_size for n in names if n in infos)
    if total > MAX_UNPACKED:
        raise ValueError("size")
    return total


def _docx_text(data: bytes) -> str:
    """Absätze aus word/document.xml, dazu Fuß- und Endnoten (dort stehen oft die Zitate).

    Die Noten kommen zuerst, damit sie zum Fließtext zählen und nicht hinter dem Verzeichnis landen.
    Text in Kapitälchen oder Großbuchstaben (direkt oder über eine Formatvorlage) kommt so heraus,
    wie Word ihn zeigt, in Großbuchstaben: DIN 1505 setzt Nachnamen so, und Literaturverwaltungen
    schreiben sie meist klein mit dem Format Kapitälchen.
    """
    ns = _W
    parts = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        unpacked_size(z, ("word/styles.xml", "word/footnotes.xml", "word/endnotes.xml", "word/document.xml"))
        caps_styles = _caps_styles(z)
        for name in ("word/footnotes.xml", "word/endnotes.xml", "word/document.xml"):
            if name not in z.namelist():
                continue
            root = ElementTree.fromstring(z.read(name))
            for p in root.iter(ns + "p"):
                ppr = p.find(ns + "pPr")
                pstyle = ppr.find(ns + "pStyle") if ppr is not None else None
                para_caps = pstyle is not None and pstyle.get(ns + "val") in caps_styles
                chunks = []
                for r in p.iter(ns + "r"):
                    rpr = r.find(ns + "rPr")
                    caps = _caps(rpr)
                    if caps is None:
                        rstyle = rpr.find(ns + "rStyle") if rpr is not None else None
                        caps = (rstyle is not None and rstyle.get(ns + "val") in caps_styles) or para_caps
                    for el in r:
                        if el.tag == ns + "t" and el.text:
                            chunks.append(el.text.upper() if caps else el.text)
                        elif el.tag == ns + "tab":
                            chunks.append("\t")
                        elif el.tag in (ns + "br", ns + "cr"):
                            chunks.append("\n")
                parts.append("".join(chunks))
            parts.append("")
    return "\n".join(parts)


def _pdf_text(data: bytes) -> str:
    import logging
    from pypdf import PdfReader  # erst hier: das Prüfen von Listen braucht es nicht
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # Schrift-Hinweise ohne Belang für den Text
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


_MD_LINK_ANGLE = re.compile(r"\[([^\]\n]+)\]\(<[^>\n]*>\)")
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((?:[^()\s]|\([^()\s]*\))*\)")
_WIKI_ALIAS = re.compile(r"\[\[[^\]|\n]+\|([^\]\n]+)\]\]")
_WIKI = re.compile(r"\[\[([^\]\n]+)\]\]")


def looks_like_markdown(text: str) -> bool:
    return bool(_MD_LINK_ANGLE.search(text) or _MD_LINK.search(text) or _WIKI.search(text)
                or re.match(r"\A---\n", text) or "%%" in text or re.search(r"(?m)^#{1,6}\s", text))


def clean_markdown(text: str) -> str:
    """Markdown und Obsidian lesbar machen: Links und Wikilinks zu ihrem Text, Kommentare und YAML weg.

    Zitate als Links ("[Akerlof, 1970](<…/Akerlof (1970).md>)") würden sonst die Klammern im Linkziel
    mitbringen und von der Zitaterkennung nicht gelesen.
    """
    text = re.sub(r"\A---\n.*?\n(?:---|\.\.\.)\s*\n", "\n", text, flags=re.S)  # YAML-Kopf
    text = re.sub(r"%%.*?%%", " ", text, flags=re.S)                             # Obsidian-Kommentare
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = _MD_LINK_ANGLE.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _WIKI_ALIAS.sub(r"\1", text)
    text = _WIKI.sub(lambda m: m.group(1).split("#")[0], text)
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)                                  # Überschriften-Zeichen
    return text


def manuscript(text: str) -> Optional[Dict]:
    """Ist ein eingefügter Text eine ganze Arbeit (Fließtext plus Literaturverzeichnis)? Dann die Analyse.

    Sonst None: eine Literaturliste, eine Chat-Antwort ohne Verzeichnis oder ein kurzer Text bleiben bei der
    normalen Prüfung, die jede erkannte Angabe prüft.
    """
    if len(text) < 3000:
        return None
    if looks_like_markdown(text):
        text = clean_markdown(text)
    body, entries = find_bibliography(text)
    if len(entries) < 3:
        return None
    start = text.find(entries[0]["text"][:40]) if entries else -1
    prose = text[:start] if start > 0 else body
    sentences = len(re.findall(r"[a-zäöüß]{3,}[.!?]\s", prose))
    if len(prose) < 2000 or sentences < 12:
        return None
    return analyse(text)


def extract_text(name: str, data: bytes) -> str:
    """Text einer Datei nach ihrer Endung; ValueError bei unbekanntem Typ oder Lesefehler."""
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix not in TYPES:
        raise ValueError("type")
    if len(data) > MAX_BYTES:
        raise ValueError("size")
    try:
        if suffix == ".pdf":
            return _pdf_text(data)
        if suffix == ".docx":
            return _docx_text(data)
        text = data.decode("utf-8", errors="replace")
        return clean_markdown(text) if suffix in (".md", ".markdown") or looks_like_markdown(text) else text
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError("unreadable") from exc
    except ValueError as exc:
        if str(exc) == "size":  # entpackt zu groß (unpacked_size)
            raise
        raise ValueError("unreadable") from exc
    except Exception as exc:  # pypdf meldet beschädigte Dateien mit vielen eigenen Fehlern
        raise ValueError("unreadable") from exc


# --------------------------------------------------------------------------
# Literaturverzeichnis finden und in Einträge teilen
# --------------------------------------------------------------------------
def _dehyphen(a: str, b: str) -> str:
    if a.endswith("-") and b[:1].islower():
        return a[:-1] + b
    return a + (" " if a and b else "") + b


def _join(lines: List[str]) -> str:
    out = ""
    for ln in lines:
        out = _dehyphen(out, ln)
    out = re.sub(r"\b([A-Z]) \.", r"\1.", out)    # "Y . Bengio": pypdf trennt Initialen vom Punkt
    return out.replace("ﬁ", "fi").replace("ﬂ", "fl")


def _number(line: str) -> Optional[int]:
    m = _ENTRY_NUMBER.match(line)
    return int(m.group(1) or m.group(2)) if m else None


def _numbered_run(lines: List[str]) -> List[int]:
    """Zeilennummern der längsten Folge 1, 2, 3, … (spätere Folgen gewinnen bei Gleichstand)."""
    best: List[int] = []
    for i, ln in enumerate(lines):
        if _number(ln) != 1:
            continue
        run, want = [i], 2
        for j in range(i + 1, len(lines)):
            if _number(lines[j]) == want:
                run.append(j)
                want += 1
            elif j - run[-1] > 15:
                break
        if len(run) >= len(best):
            best = run
    return best


def _last_entry_end(lines: List[str], k: int) -> int:
    """Ende des letzten nummerierten Eintrags: nach der Zeile, die mit Punkt schließt, sobald ein
    Jahr da ist; höchstens fünf Zeilen. Sonst rutscht ein folgender Anhang mit hinein."""
    seen = ""
    for j in range(k, min(len(lines), k + 5)):
        seen = _dehyphen(seen, lines[j])
        if _YEAR.search(seen) and lines[j].rstrip().endswith("."):
            return j + 1
    return min(len(lines), k + 5)


def find_bibliography(text: str) -> Tuple[str, List[Dict]]:
    """(Fließtext, Einträge des Literaturverzeichnisses).

    Erst eine nummerierte Folge (1, 2, 3 … oder [1], [2] …) mit Jahreszahlen, mit oder ohne
    Überschrift davor (Nature und viele Zeitschriften setzen keine). Sonst unter einer
    Überschrift die Einträge, die mit "Nachname, Initial" beginnen. Ein PDF bricht Einträge über
    mehrere Zeilen um; die werden wieder zusammengefügt.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    head = None
    for h in reversed(list(_HEADING_RE.finditer(text))):
        line_no = text.count("\n", 0, h.start())
        if len(_YEAR.findall("\n".join(lines[line_no + 1:line_no + 120]))) >= 2:
            head = line_no
            break
    first = head + 1 if head is not None else 0
    region = lines[first:]

    run = _numbered_run(region)
    if len(run) >= 3 and sum(1 for k in run[:8] if _YEAR.search(" ".join(region[k:k + 3]))) >= min(3, len(run)):
        entries, stop = [], 0
        for n, k in enumerate(run):
            stop = run[n + 1] if n + 1 < len(run) else _last_entry_end(region, k)
            chunk = [ln for ln in region[k:stop] if ln and not re.fullmatch(r"\d{1,4}", ln)]
            entries.append({"text": _join([_ENTRY_NUMBER.sub("", chunk[0], count=1)] + chunk[1:])[:700], "number": n + 1})
        body = lines[:head] if head is not None else lines[:first + run[0]]
        return "\n".join(body + region[stop:]), entries  # ein Anhang dahinter zitiert auch

    if head is None:
        return text, []
    section = "\n".join(region)
    end = _END_RE.search(section)
    tail = ""
    if end:
        region, tail = section[:end.start()].split("\n"), section[end.start():]
    entries, cur = [], []
    for ln in region:
        if not ln or re.fullmatch(r"\d{1,4}", ln):  # Leerzeilen, Seitenzahlen
            continue
        joined = _join(cur)
        if cur and _ENTRY_AUTHOR.match(ln) and _YEAR.search(joined) and not joined.endswith((",", "&", " and", " und")):
            entries.append({"text": joined[:700], "number": None})
            cur = []
        cur.append(ln)
    if cur:
        entries.append({"text": _join(cur)[:700], "number": None})
    entries = [e for e in entries if len(e["text"]) >= 20 and _YEAR.search(e["text"])]
    return "\n".join(lines[:head]) + "\n" + tail, entries


# --------------------------------------------------------------------------
# Zitate im Text
# --------------------------------------------------------------------------
def _fold(s: str) -> str:
    return refs.fold(s).strip()


_SKIP_WORDS = {"vgl", "see", "cf", "siehe", "e.g", "z.b", "i.e", "also", "auch", "ebd", "ibid", "and", "und",
               "in", "for", "example", "zum", "beispiel", "u.a", "et", "al", "p", "pp", "s", "chapter", "kap"}
_PAREN = re.compile(r"\(([^()]{0,300}?\b(?:1[89]\d{2}|20\d{2})[a-z]?\b[^()]{0,120})\)")
_NARRATIVE = re.compile(r"((?:" + _PARTICLES + r"\s+)*" + _NAME + r")(?:\s+(?:et al\.|u\. ?a\.|and|und|&)\s*(?:"
                        + _NAME + r")?)?\s*\(((?:1[89]\d{2}|20\d{2})[a-z]?)([,;][^)]{0,60})?\)")
_NUMERIC = re.compile(r"\[(\d{1,3}(?:\s*[,–\-]\s*\d{1,3})*)((?:\s*,\s*(?:pp?\.|S\.|p)\s*\d+[^\]]{0,20})?)\]")
_MONTHS = (r"jan(?:uary|uar)?|feb(?:ruary|ruar)?|m(?:arch|ärz|aerz)|apr(?:il)?|ma[iy]|jun[ei]?|jul[iy]?|aug(?:ust)?|"
           r"sep(?:t(?:ember)?)?|o[ck]t(?:ober)?|nov(?:ember)?|de[cz](?:ember)?")
_DATE = re.compile(r"^\s*(?:\d{1,2}\.?\s*)?(?:" + _MONTHS + r")\.?\s*,?\s*(?:\d{1,2},?\s*)?(?:1[89]|20)\d{2}\s*$", re.IGNORECASE)
_PAGE = re.compile(r"\b(?:pp?\.|S\.|Seite|page|p)\s*(\d{1,5})", re.IGNORECASE)
# Wörtliche Zitate: “…”, „…“, «…», »…«, "…"
_QUOTE = re.compile(r"[“„«»\"]([^“”„\"«»\n]{12,800}?)[”“\"»«]")


# Punkte nach diesen Wörtern beenden keinen Satz: "Harris et al. (2020)", "vgl. S. 12", "z. B."
_ABBREV = {"al", "vgl", "bzw", "ca", "etc", "ebd", "ibid", "e.g", "i.e", "z.b", "u.a", "d.h", "s", "p", "pp", "fig",
           "abb", "tab", "nr", "no", "vol", "bd", "hg", "hrsg", "ed", "eds", "dr", "prof", "inc", "cf", "vs", "ff", "f"}
_END = re.compile(r"[.!?](?=\s)|\n\n")


def _is_end(text: str, i: int) -> bool:
    """Endet bei text[i] ein Satz? (Punkt nach Abkürzung oder Initiale: nein)"""
    if text[i] == "\n":
        return True
    if text[i] != ".":
        return True
    word = re.search(r"([\w.]+)$", text[max(0, i - 12):i])
    w = (word.group(1).lower() if word else "")
    return not (w in _ABBREV or w.rstrip(".") in _ABBREV or (len(w) == 1 and w.isalpha()))


def has_sentence_end(segment: str) -> bool:
    return any(_is_end(segment, m.start()) for m in _END.finditer(segment))


def _sentence(text: str, pos: int) -> str:
    start = -1
    for m in _END.finditer(text, max(0, pos - 1500), pos):
        if _is_end(text, m.start()):
            start = m.end() - 1 if text[m.start()] == "\n" else m.start()
    end = len(text)
    for m in _END.finditer(text, pos):
        if _is_end(text, m.start()):
            end = m.start()
            break
    begin = start + 1 if start >= 0 else 0
    # Überschriften davor (kurze Zeilen ohne Satzzeichen am Ende) gehören nicht zum Satz.
    while True:
        nl = text.find("\n", begin, pos)
        if nl == -1:
            break
        line = text[begin:nl].strip()
        if line and (len(line) >= 70 or re.search(r"[.,;:!?)\]-]$", line)):
            break
        begin = nl + 1
    return re.sub(r"\s+", " ", text[begin:end + 1]).strip()[:500]


def _first_surname(segment: str) -> Optional[str]:
    for word in re.findall(r"(?:" + _PARTICLES + r"\s+)*" + _NAME, segment):
        if _fold(word).rstrip(".") not in _SKIP_WORDS and not word.isupper():
            return word
    return None


def find_citations(body: str) -> List[Dict]:
    """Zitate mit Schlüssel (Nachname, Jahr) oder Nummer, Seite, Position und dem umgebenden Satz."""
    text = _normalised(body)
    found: List[Dict] = []
    seen = set()

    def add(item: Dict, pos: int, page: Optional[str] = None) -> None:
        key = (item.get("surname"), item.get("year"), item.get("letter"), item.get("number"), pos // 40)
        if key in seen:
            return
        seen.add(key)
        item["sentence"] = _sentence(text, pos)
        item["pos"] = pos
        item["page"] = page
        found.append(item)

    def page_of(segment: str) -> Optional[str]:
        m = _PAGE.search(segment or "")
        return m.group(1) if m else None

    for m in _PAREN.finditer(text):
        for part in m.group(1).split(";"):
            if _DATE.match(part):
                continue  # "(January 2027)", "(12. März 2026)": ein Datum, kein Zitat
            years = re.findall(r"\b(1[89]\d{2}|20\d{2})([a-z]?)\b", part)
            name = _first_surname(part.split(",")[0] if "," in part else re.split(r"\b(?:1[89]|20)\d{2}", part)[0])
            if not years or not name:
                continue
            etal = " et al." if re.search(r"et al|u\. ?a\.", part) else ""
            for y, letter in years:
                if re.search(r"(?:" + _MONTHS + r")\.?\s*,?\s*(?:\d{1,2},?\s*)?" + y, part, re.IGNORECASE):
                    continue  # "(Lisbon, December 2026)": Ort und Datum, kein Zitat
                add({"kind": "author-year", "surname": name, "year": y, "letter": letter, "label": f"{name}{etal}, {y}{letter}"},
                    m.start(), page_of(part.split(y + letter, 1)[-1]))
    for m in _NARRATIVE.finditer(text):
        name, year, letter = m.group(1), m.group(2)[:4], m.group(2)[4:]
        if _fold(name).rstrip(".") in _SKIP_WORDS:
            continue
        add({"kind": "author-year", "surname": name, "year": year, "letter": letter, "label": f"{name} ({year}{letter})"},
            m.start(), page_of(m.group(3)))
    for m in _NUMERIC.finditer(text):
        for chunk in re.split(r"\s*,\s*", m.group(1)):
            rng = re.split(r"\s*[–\-]\s*", chunk)
            try:
                lo, hi = int(rng[0]), int(rng[-1])
            except ValueError:
                continue
            for n in range(lo, min(hi, lo + 30) + 1):
                add({"kind": "number", "number": n, "label": f"[{n}]"}, m.start(), page_of(m.group(2)))
    return found


def _normalised(body: str) -> str:
    """Derselbe Text, in dem find_citations Positionen zählt."""
    text = re.sub(r"[ \t]+", " ", body.replace("\r", ""))
    return re.sub(r"(\w)-\n(\w)", r"\1\2", text)


def find_quotes(text: str, citations: List[Dict]) -> List[Dict]:
    """Wörtliche Zitate mit der Quelle, die direkt dahinter (oder davor im selben Satz) zitiert wird."""
    cites = sorted((c for c in citations if c.get("entry") is not None and "pos" in c), key=lambda c: c["pos"])
    out: List[Dict] = []
    for m in _QUOTE.finditer(text):
        quote = re.sub(r"\s+", " ", m.group(1)).strip()
        if len(quote.split()) < 6:  # kürzere sind meist Begriffe oder Interviewfragen
            continue
        after = [c for c in cites if m.end() <= c["pos"] <= m.end() + 160
                 and not has_sentence_end(text[m.end():c["pos"]])]
        cite = after[0] if after else None
        if cite is None:
            before = [c for c in cites if m.start() - 220 <= c["pos"] < m.start()
                      and not has_sentence_end(text[c["pos"]:m.start()])]
            cite = before[-1] if before else None
        if cite is None:
            continue
        out.append({"quote": quote, "entry": cite["entry"], "label": cite["label"], "page": cite.get("page")})
    return out[:300]


def find_writing_signals(body: str, citations: List[Dict]) -> Dict[str, List[Dict]]:
    """Findet Zweitzitate und empirische/statistische Aussagen ohne Beleg."""
    text = _normalised(body)
    secondary: List[Dict] = []
    seen_sec = set()
    for m in _SECONDARY_RE.finditer(text):
        s = _sentence(text, m.start())
        if s not in seen_sec:
            seen_sec.add(s)
            secondary.append({"match": m.group(0), "sentence": s})
        if len(secondary) >= 50:
            break

    uncited: List[Dict] = []
    seen_uncited = set()
    cite_positions = [c["pos"] for c in citations if "pos" in c]

    cur_start = 0
    for m in _END.finditer(text):
        if not _is_end(text, m.start()):
            continue
        cur_end = m.start()
        sent = text[cur_start:cur_end + 1].strip()
        sent_clean = re.sub(r"\s+", " ", sent)
        if len(sent_clean.split()) >= 6 and _EMPIRICAL_CLAIM_RE.search(sent_clean):
            if not _SELF_DATA_RE.search(sent_clean):
                has_cite = any(cur_start - 20 <= pos <= cur_end + 120 for pos in cite_positions)
                if not has_cite and sent_clean not in seen_uncited:
                    seen_uncited.add(sent_clean)
                    uncited.append({"sentence": sent_clean[:400]})
                    if len(uncited) >= 30:
                        break
        cur_start = m.end()

    return {
        "secondary_citations": secondary,
        "uncited_claims": uncited,
    }


# --------------------------------------------------------------------------
# Abgleich
# --------------------------------------------------------------------------
def _entry_key(ref: Dict, transliterate: bool = False) -> Tuple[str, str]:
    authors = ref.get("authors") or []
    surname = str(authors[0]) if authors else ""
    if not surname:
        head = re.split(r"[,(.]", str(ref.get("raw") or ""), maxsplit=1)[0]
        surname = head.split()[-1] if head.split() else ""
    return refs.name_key(surname.split()[-1] if surname else "", transliterate), str(ref.get("year") or "")


def _year_letter(ref: Dict) -> str:
    """Der Buchstabe hinter dem Jahr (Smith 2020a, 2020b): ohne ihn fielen zwei Werke auf einen Schlüssel."""
    year = str(ref.get("year") or "")
    m = re.search(r"\b" + re.escape(year) + r"([a-z])\b", str(ref.get("raw") or "")) if year else None
    return m.group(1) if m else ""


def analyse(text: str) -> Dict:
    """Literaturverzeichnis, Zitate und Abgleich einer ganzen Arbeit."""
    body, entries = find_bibliography(text)
    parsed = []
    for i, e in enumerate(entries):
        r = refs.parse_text(e["text"])
        ref = r[0] if r else refs._empty("text", e["text"])
        if ref.get("arxiv") and not ref.get("doi"):
            ref["doi"] = refs.arxiv_doi(str(ref["arxiv"]))
        ref["entry"] = i
        ref["number"] = e["number"]
        ref["label"] = refs.label(ref)
        parsed.append(ref)
    citations = find_citations(body)
    numeric = sum(1 for c in citations if c["kind"] == "number") > sum(1 for c in citations if c["kind"] == "author-year")
    style = "numeric" if numeric else "author-year"
    keys = {}
    for transliterate in (False, True):  # erst der genaue Name, dann umschrieben (Müller = Mueller)
        for ref in parsed:
            surname, year = _entry_key(ref, transliterate)
            keys.setdefault((surname, year + _year_letter(ref)), ref["entry"])
            keys.setdefault((surname, year), ref["entry"])  # Zitat ohne Buchstaben oder Verzeichnis ohne
    by_number = {ref["number"] if ref.get("number") else ref["entry"] + 1: ref["entry"] for ref in parsed}
    highest = max(by_number) if by_number else 0

    cited_entries = set()
    missing: Dict[str, Dict] = {}
    for c in citations:
        if (c["kind"] == "number") != numeric:
            continue
        if c["kind"] == "number" and not 1 <= c["number"] <= highest + 2:
            continue  # Zahlen in Klammern, die keine Zitate sind: [256], [0]
        if c["kind"] == "number":
            entry = by_number.get(c["number"])
        else:
            entry = None
            for transliterate in (False, True):
                surname = refs.name_key(c["surname"].split()[-1], transliterate)
                entry = keys.get((surname, c["year"] + c.get("letter", "")), keys.get((surname, c["year"])))
                if entry is not None:
                    break
        c["entry"] = entry
        if entry is None:
            m = missing.setdefault(c["label"], {"label": c["label"], "count": 0, "sentence": c["sentence"],
                                                "surname": refs.name_key(c["surname"].split()[-1]) if c.get("surname") else "",
                                                "year": str(c.get("year") or "")})
            m["count"] += 1
        else:
            cited_entries.add(entry)
    # Gleicher Name, anderes Jahr: im Text (Smith, 2019), im Verzeichnis Smith 2018. Das ist dieselbe Quelle mit
    # einem Tippfehler im Jahr, nicht ein fehlender und ein nie zitierter Eintrag.
    year_mismatch = []
    if not numeric:
        open_by_name: Dict[str, List[Dict]] = {}
        for r in parsed:
            if r["entry"] not in cited_entries:
                open_by_name.setdefault(_entry_key(r)[0], []).append(r)
        for label, m in list(missing.items()):
            cands = open_by_name.get(m.get("surname") or "") or []
            if len(cands) != 1 or not cands[0].get("year") or not m.get("year"):
                continue
            r = cands[0]
            if str(r["year"])[:4] == m["year"][:4]:
                continue
            year_mismatch.append({"label": label, "cited_year": m["year"], "list_year": str(r["year"]), "entry": r["entry"],
                                  "list_label": refs.label(r) or r["raw"][:90], "count": m["count"], "sentence": m["sentence"]})
            del missing[label]
            cited_entries.add(r["entry"])
            open_by_name[m["surname"]] = []
    for m in missing.values():
        m.pop("surname", None)
        m.pop("year", None)
    uncited = [{"entry": r["entry"], "label": refs.label(r) or r["raw"][:90]} for r in parsed if r["entry"] not in cited_entries]
    for c in citations:  # zitierte Seiten (S. 14) je Eintrag: verify vergleicht sie mit den Seiten des Werks
        if c.get("page") and c.get("entry") is not None:
            pages = parsed[c["entry"]].setdefault("cited_pages", [])
            if c["page"] not in pages:
                pages.append(c["page"])
    counted = [c for c in citations if (c["kind"] == "number") == numeric and "entry" in c]
    # Hochgestellte Zahlen (Nature, Science) liest kein PDF-Text als Zitat; dann lieber nichts
    # behaupten als fast jeden Eintrag "nie zitiert" nennen.
    readable = bool(parsed) and len(cited_entries) >= max(1, round(0.3 * len(parsed)))
    signals = find_writing_signals(body, citations) if readable else {"secondary_citations": [], "uncited_claims": []}
    return {
        "has_bibliography": bool(entries),
        "style": style,
        "entries": len(parsed),
        "citations": len(counted),
        "cross_check": readable,
        "missing_in_list": sorted(missing.values(), key=lambda m: -m["count"])[:200] if readable else [],
        "year_mismatch": year_mismatch[:200] if readable else [],
        "uncited": uncited[:200] if readable else [],
        "secondary_citations": signals["secondary_citations"],
        "uncited_claims": signals["uncited_claims"],
        "integrity": integrity.scan(body),  # unsichtbare Zeichen, fremde Buchstaben, gequälte Phrasen
        "claims": [c for c in citations if c.get("entry") is not None][:600],
        "quotes": find_quotes(_normalised(body), citations),
        "refs_text": "\n".join(e["text"] for e in entries),
        "refs": parsed,  # ein Eintrag je Zeile des Verzeichnisses, in dieser Reihenfolge geprüft
    }


def annotate_docx(data: bytes, notes: List[Dict[str, str]]) -> bytes:
    """Fügt Word-Randkommentare in eine .docx-Datei ein, ohne Formatierungen zu ändern.

    Jede Notiz in ``notes`` hat die Felder ``needle`` (Suchtext im Fließtext)
    und ``text`` (Inhalt des Kommentars), optional ``author`` (Standard: "Benthic").
    """
    if not data or not notes:
        return data
    try:
        if not zipfile.is_zipfile(io.BytesIO(data)):
            return data
    except Exception:
        return data

    ElementTree.register_namespace("w", "http://schemas.openxmlformats.org/wordprocessingml/2006/main")
    ElementTree.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")

    try:
        in_zip = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return data

    namelist = in_zip.namelist()
    if "word/document.xml" not in namelist:
        return data

    try:
        doc_root = ElementTree.fromstring(in_zip.read("word/document.xml"))
    except Exception:
        return data

    has_comments_file = "word/comments.xml" in namelist
    if has_comments_file:
        try:
            comments_root = ElementTree.fromstring(in_zip.read("word/comments.xml"))
        except Exception:
            comments_root = ElementTree.Element(_W + "comments")
            has_comments_file = False
    else:
        comments_root = ElementTree.Element(_W + "comments")

    existing_ids = []
    for c in comments_root.findall(_W + "comment"):
        cid = c.get(_W + "id")
        if cid and cid.isdigit():
            existing_ids.append(int(cid))
    next_id = max(existing_ids, default=-1) + 1

    paras = list(doc_root.iter(_W + "p"))
    para_texts = []
    for p in paras:
        txt = "".join(el.text for el in p.iter(_W + "t") if el.text)
        para_texts.append(re.sub(r"\s+", " ", txt).lower())

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    added = 0

    for note in notes:
        needle = (note.get("needle") or "").strip()
        text = (note.get("text") or "").strip()
        author = note.get("author") or "Benthic"
        if not needle or not text:
            continue
        needle_clean = re.sub(r"\s+", " ", needle).lower()
        if not needle_clean:
            continue

        matched_idx = -1
        for idx, pt in enumerate(para_texts):
            if needle_clean in pt or (len(needle_clean) > 30 and needle_clean[:30] in pt):
                matched_idx = idx
                break

        if matched_idx < 0:
            continue

        p = paras[matched_idx]
        cid = str(next_id)
        next_id += 1
        added += 1

        insert_idx = 1 if len(p) > 0 and p[0].tag == (_W + "pPr") else 0
        range_start = ElementTree.Element(_W + "commentRangeStart", {_W + "id": cid})
        p.insert(insert_idx, range_start)

        range_end = ElementTree.Element(_W + "commentRangeEnd", {_W + "id": cid})
        p.append(range_end)

        run = ElementTree.Element(_W + "r")
        comment_ref = ElementTree.Element(_W + "commentReference", {_W + "id": cid})
        run.append(comment_ref)
        p.append(run)

        c_elem = ElementTree.SubElement(comments_root, _W + "comment", {
            _W + "id": cid,
            _W + "author": author,
            _W + "date": now_iso,
        })
        cp = ElementTree.SubElement(c_elem, _W + "p")
        cr = ElementTree.SubElement(cp, _W + "r")
        ct = ElementTree.SubElement(cr, _W + "t")
        ct.text = text

    if not added:
        return data

    content_types_xml = None
    if not has_comments_file and "[Content_Types].xml" in namelist:
        try:
            ct_root = ElementTree.fromstring(in_zip.read("[Content_Types].xml"))
            override_tag = "{http://schemas.openxmlformats.org/package/2006/content-types}Override"
            if not any(el.get("PartName") == "/word/comments.xml" for el in ct_root.iter(override_tag)):
                ct_root.append(ElementTree.Element(override_tag, {
                    "PartName": "/word/comments.xml",
                    "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
                }))
                content_types_xml = ElementTree.tostring(ct_root, encoding="utf-8", xml_declaration=True)
        except Exception:
            pass

    rels_xml = None
    rels_path = "word/_rels/document.xml.rels"
    if not has_comments_file:
        if rels_path in namelist:
            try:
                rels_root = ElementTree.fromstring(in_zip.read(rels_path))
                rel_tag = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
                has_rel = any("comments" in el.get("Type", "") for el in rels_root.iter(rel_tag))
                if not has_rel:
                    rids = []
                    for el in rels_root.iter(rel_tag):
                        m = re.match(r"^rId(\d+)$", el.get("Id", ""))
                        if m:
                            rids.append(int(m.group(1)))
                    next_rid = f"rId{max(rids, default=0) + 1}"
                    rels_root.append(ElementTree.Element(rel_tag, {
                        "Id": next_rid,
                        "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
                        "Target": "comments.xml",
                    }))
                    rels_xml = ElementTree.tostring(rels_root, encoding="utf-8", xml_declaration=True)
            except Exception:
                pass
        else:
            rels_xml = (
                b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                b'  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/>\n'
                b'</Relationships>'
            )

    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w", compression=zipfile.ZIP_DEFLATED) as out_zip:
        for item in in_zip.infolist():
            if item.filename == "word/document.xml":
                out_zip.writestr(item, ElementTree.tostring(doc_root, encoding="utf-8", xml_declaration=True))
            elif item.filename == "word/comments.xml":
                out_zip.writestr(item, ElementTree.tostring(comments_root, encoding="utf-8", xml_declaration=True))
            elif item.filename == "[Content_Types].xml" and content_types_xml:
                out_zip.writestr(item, content_types_xml)
            elif item.filename == rels_path and rels_xml:
                out_zip.writestr(item, rels_xml)
            else:
                out_zip.writestr(item, in_zip.read(item.filename))

        if not has_comments_file:
            out_zip.writestr("word/comments.xml", ElementTree.tostring(comments_root, encoding="utf-8", xml_declaration=True))
            if rels_path not in namelist and rels_xml:
                out_zip.writestr(rels_path, rels_xml)

    return out_buf.getvalue()
