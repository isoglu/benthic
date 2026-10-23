"""Hinweise auf Manipulation im Text einer Arbeit: was ein Mensch nicht sieht, und gequälte Phrasen.

Jeder Fund zeigt die Stelle und lässt sich in Sekunden nachprüfen; er sagt nichts über die Person und ist kein Wert
für sie. Geprüft wird der Text vor dem Literaturverzeichnis.

- Unsichtbare Zeichen mitten in einem Wort (Nullbreite, Richtungsumkehr): Sie zerlegen Wörter für Ähnlichkeitsprüfungen.
- Buchstaben aus einer anderen Schrift, die wie lateinische aussehen (kyrillisches а in „plаgiarism“).
- In Word versteckter, weißer oder winziger Text. Nicht erkannt: weißer Text in PDF-Dateien und Text in Bildern.
- Gequälte Phrasen (tortured phrases): Fachbegriffe, die ein Umschreibprogramm durch Synonyme ersetzt hat. Die Liste
  folgt Tabelle 1 bei Cabanac, Labbé und Magazinov (2021), arXiv:2107.06751; Wendungen, die auch in gewöhnlichem
  Text vorkommen („versatile organization“, „large data“), fehlen absichtlich.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from typing import Dict, List, Optional
from xml.etree import ElementTree

LIMIT = 50  # Funde je Art
_LATIN = "A-Za-zÀ-ÖØ-öø-ɏ"
_INVISIBLE = re.compile(rf"(?<=[{_LATIN}])[​-‍⁠-⁤﻿‭‮]+(?=[{_LATIN}])")
_WORD = re.compile(r"[^\W\d_]+")
_GREEK_LOOKALIKE = set("ΑΒΕΖΗΙΚΜΝΟΡΤΥΧοιν")

# (gequälte Phrase, üblicher Begriff); Leerzeichen stehen für beliebigen Zwischenraum
TORTURED = [
    (r"profound neural organi[sz]ation", "deep neural network"),
    (r"(?:fake|counterfeit) neural organi[sz]ation", "artificial neural network"),
    (r"organi[sz]ation (?:ambush|assault)", "network attack"),
    (r"(?:enormous|immense|colossal) information", "big data"),
    (r"information stockroom", "data warehouse"),
    (r"(?:counterfeit|human-made) consciousness", "artificial intelligence"),
    (r"man-made brainpower", "artificial intelligence"),
    (r"elite figuring", "high performance computing"),
    (r"haze figuring", "fog computing"),
    (r"designs preparing unit", "graphics processing unit"),
    (r"focal preparing unit", "central processing unit"),
    (r"work process motor", "workflow engine"),
    (r"facial acknowledge?ment", "face recognition"),
    (r"discourse acknowledge?ment", "voice recognition"),
    (r"mean square (?:mistake|blunder)", "mean square error"),
    (r"mean (?:outright|supreme) (?:mistake|blunder)", "mean absolute error"),
    (r"(?:motion|flag|indicator|sign|signal) to (?:clamou?r|commotion)", "signal to noise"),
    (r"flag to noise", "signal to noise"),
    (r"worldwide parameters", "global parameters"),
    (r"(?:arbitrary|irregular) get right of passage to", "random access"),
    (r"(?:arbitrary|irregular) (?:backwoods|timberland|lush territory)", "random forest"),
    (r"(?:arbitrary|irregular) esteem", "random value"),
    (r"(?:subterranean insect|underground creepy crawly) (?:state|province|area|region|settlement)", "ant colony"),
    (r"leftover vitality", "remaining energy"),
    (r"territorial normal vitality", "local average energy"),
    (r"motor vitality", "kinetic energy"),
    (r"(?:credulous|innocent|gullible) Bayes", "naïve Bayes"),
    (r"individual computeri[sz]ed collaborator", "personal digital assistant"),
]
_TORTURED = [(re.compile(r"\b" + p.replace(" ", r"\s+") + r"\b", re.IGNORECASE), t) for p, t in TORTURED]


def _around(text: str, start: int, end: int, width: int = 90) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - width):end + width]).strip()


def invisible(text: str) -> List[Dict]:
    out: Dict[str, Dict] = {}
    for m in _INVISIBLE.finditer(text):
        a = m.start() - len(re.search(rf"[{_LATIN}]*$", text[:m.start()]).group(0))
        b = m.end() + len(re.match(rf"[{_LATIN}​-‍⁠-⁤﻿‭‮]*", text[m.end():]).group(0))
        shown = re.sub(r"[​-‍⁠-⁤﻿‭‮]", lambda c: f"⟨U+{ord(c.group(0)):04X}⟩", text[a:b])
        item = out.setdefault(shown, {"word": shown, "count": 0, "context": _around(text, a, b)})
        item["count"] += 1
    return list(out.values())[:LIMIT]


def _script(ch: str) -> str:
    name = unicodedata.name(ch, "")
    return "latin" if name.startswith("LATIN") else "cyrillic" if name.startswith("CYRILLIC") else "greek" if name.startswith("GREEK") else ""


def lookalikes(text: str) -> List[Dict]:
    out: Dict[str, Dict] = {}
    for m in _WORD.finditer(text):
        word = m.group(0)
        if len(word) < 3:
            continue
        scripts = [_script(c) for c in word]
        if "latin" not in scripts:
            continue
        foreign = [c for c, s in zip(word, scripts) if s == "cyrillic" or (s == "greek" and c in _GREEK_LOOKALIKE)]
        if not foreign or any(s == "greek" and c not in _GREEK_LOOKALIKE for c, s in zip(word, scripts)):
            continue  # griechische Formelzeichen (IFNγ, μm) sind kein Täuschungsversuch
        shown = "".join(f"[{c}]" if c in foreign else c for c in word)
        item = out.setdefault(shown, {"word": shown, "count": 0, "letters": sorted({unicodedata.name(c, "") for c in foreign}),
                                      "context": _around(text, m.start(), m.end())})
        item["count"] += 1
    return list(out.values())[:LIMIT]


def tortured(text: str) -> List[Dict]:
    out: Dict[str, Dict] = {}
    for rx, term in _TORTURED:
        for m in rx.finditer(text):
            phrase = re.sub(r"\s+", " ", m.group(0))
            item = out.setdefault(phrase.lower(), {"phrase": phrase, "expected": term, "count": 0,
                                                   "context": _around(text, m.start(), m.end())})
            item["count"] += 1
    return sorted(out.values(), key=lambda t: -t["count"])[:LIMIT]


def scan(text: str) -> Dict[str, List[Dict]]:
    """Alle Prüfungen auf dem Text einer Arbeit (ohne Literaturverzeichnis)."""
    return {"invisible": invisible(text), "lookalike": lookalikes(text), "tortured": tortured(text), "hidden": []}


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _on(el: Optional[ElementTree.Element]) -> bool:
    return el is not None and (el.get(_W + "val") or "true").lower() not in ("0", "false", "off")


def _filled(el: Optional[ElementTree.Element]) -> bool:
    """Hat das Element eine farbige Schattierung? Dann ist weiße Schrift darauf lesbar."""
    shd = el.find(_W + "shd") if el is not None else None
    return shd is not None and (shd.get(_W + "fill") or "auto").lower() not in ("auto", "ffffff")


def docx_hidden(data: bytes) -> List[Dict]:
    """Text in word/document.xml, den Word nicht oder kaum zeigt: ausgeblendet, weiß ohne Hintergrund, unter 2 pt."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if z.getinfo("word/document.xml").file_size > 200 * 1024 * 1024:
                return []
            root = ElementTree.fromstring(z.read("word/document.xml"))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return []
    shaded = {id(r) for tc in root.iter(_W + "tc") if _filled(tc.find(_W + "tcPr")) for r in tc.iter(_W + "r")}
    out: List[Dict] = []
    for p in root.iter(_W + "p"):
        para_filled = _filled(p.find(_W + "pPr"))
        run: Optional[Dict] = None
        for r in p.iter(_W + "r"):
            rpr = r.find(_W + "rPr")
            text = "".join(t.text or "" for t in r.iter(_W + "t"))
            kind = None
            if rpr is not None and text:
                color = rpr.find(_W + "color")
                size = rpr.find(_W + "sz")
                lit = para_filled or id(r) in shaded or _filled(rpr) or rpr.find(_W + "highlight") is not None
                if _on(rpr.find(_W + "vanish")):
                    kind = "hidden"
                elif color is not None and (color.get(_W + "val") or "").lower() == "ffffff" and not lit:
                    kind = "white"
                elif size is not None and (size.get(_W + "val") or "").isdigit() and 0 < int(size.get(_W + "val")) <= 3:
                    kind = "tiny"  # Halbpunkte: 3 sind 1,5 pt
            if kind and run and run["kind"] == kind:
                run["text"] += text
            elif kind:
                run = {"kind": kind, "text": text}
                out.append(run)
            else:
                run = None
    found = [dict(f, text=re.sub(r"\s+", " ", f["text"]).strip()[:300]) for f in out if sum(c.isalpha() for c in f["text"]) >= 3]
    return found[:LIMIT]


def count(found: Optional[Dict]) -> int:
    return sum(len(v) for v in (found or {}).values() if isinstance(v, list))
