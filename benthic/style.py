"""Zitierstil prüfen: folgt das Literaturverzeichnis APA, Harvard, IEEE, Vancouver, Chicago oder DIN?

Zwei Arten von Hinweisen:

* **Form** aus dem Text der Angabe selbst: Autor:innen im richtigen Format, Jahr an der richtigen
  Stelle, Titel in Anführungszeichen (IEEE), DOI als Link (APA 7), "et al." im Verzeichnis.
* **Vollständigkeit** im Vergleich mit den echten Angaben aus der Prüfung: fehlen Band, Heft,
  Seiten oder die DOI, obwohl das Werk sie hat?

Dazu Hinweise zur ganzen Liste: gemischte Stile, Reihenfolge, Nummerierung, Doppelte.
Ohne Netzwerk; die richtige Fassung einer Angabe holt ``biblio.export`` bei doi.org.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional

from .i18n import tr

STYLES = {"apa": "APA 7", "harvard": "Harvard", "chicago": "Chicago", "ieee": "IEEE",
          "vancouver": "Vancouver", "din": "DIN 1505"}
NUMBERED = ("ieee", "vancouver")
ALPHABETICAL = ("apa", "harvard", "chicago", "din")

_NUM_PREFIX = re.compile(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})[.)])\s*")
_SURNAME_FIRST = re.compile(r"^\s*(?:(?:van|von|de|der|den|da|di|le|la)\s+)*[A-ZÄÖÜ][\w'’\-]+,\s+(?:[A-ZÄÖÜ]\.|[A-ZÄÖÜ][a-zäöüß]+)")
_INITIALS_FIRST = re.compile(r"^\s*(?:[A-Z]\.\s?-?){1,4}\s*[A-ZÄÖÜ]")
_VANCOUVER_AUTH = re.compile(r"^\s*[A-ZÄÖÜ][\w'’\-]+\s+[A-Z]{1,3}[,.]")
_DIN_AUTH = re.compile(r"^\s*[A-ZÄÖÜ]{2,}[A-ZÄÖÜ\-]*,\s")
_YEAR_PAREN = re.compile(r"\((1[89]\d{2}|20\d{2})[a-z]?(?:,\s*[^)]*)?\)")
_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})[a-z]?\b")
_QUOTED = re.compile(r"[\"“„«][^\"”“»]{8,}[\"”“»]")
_DOI_URL = re.compile(r"https?://(?:dx\.)?doi\.org/10\.", re.IGNORECASE)
_DOI_ANY = re.compile(r"\b10\.\d{4,9}/\S+")
_PAGES = re.compile(r"\b(?:pp?\.\s*|S\.\s*)?[A-Za-z]?\d+\s*[-\u2013\u2014]\s*[A-Za-z]?\d+\b|\bpp?\.\s*\d+|\bS\.\s*\d+|\be\d{3,}\b|\b\d{6}\b")
_VOLUME = re.compile(r"\bvol\.?\s*\d+|\bBd\.\s*\d+|\bJg\.\s*\d+|,\s*\d{1,4}\s*\(\d|\b\d{1,4}\s*\(\d{1,4}\)|\b\d{1,4}:\s*\d|;\s*\d{1,4}\s*[(:]|\b\d{1,4},\s*(?:pp?\.\s*)?\d+\s*[-–]")
_ISSUE = re.compile(r"\((?!(?:1[89]|20)\d{2}[a-z]?\))\d{1,4}(?:[-–/]\d{1,4})?\)|\bno\.\s*\d+|\bNr\.\s*\d+|\bH\.\s*\d+|\bHeft\s*\d+|\bissue\s*\d+", re.IGNORECASE)


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def guess(raw: str) -> Optional[str]:
    """Stil einer einzelnen Angabe, so gut der Text es verrät."""
    text = _NUM_PREFIX.sub("", raw)
    numbered = bool(_NUM_PREFIX.match(raw))
    if numbered and _INITIALS_FIRST.match(text) and (_QUOTED.search(text) or re.search(r"\bvol\.|\bpp\.", text)):
        return "ieee"
    if _VANCOUVER_AUTH.match(text) and re.search(r"\.\s*(1[89]\d{2}|20\d{2})\s*[;:]", text):
        return "vancouver"
    if _DIN_AUTH.match(text) and ":" in text[:120]:
        return "din"
    if _SURNAME_FIRST.match(text):
        m = _YEAR_PAREN.search(text[:160])
        if m:
            after = text[m.end():m.end() + 2]
            return "apa" if after.startswith(".") else "harvard"
        if re.search(r"\.\s(1[89]\d{2}|20\d{2})[a-z]?\.\s", text[:200]):
            return "chicago"
    if numbered and _INITIALS_FIRST.match(text):
        return "ieee"
    return None


def detect(raws: List[str]) -> Optional[str]:
    """Häufigster Stil der Liste (mindestens ein Drittel der Angaben)."""
    votes: Dict[str, int] = {}
    for raw in raws:
        g = guess(raw)
        if g:
            votes[g] = votes.get(g, 0) + 1
    if not votes:
        return None
    best = max(votes, key=votes.get)
    return best if votes[best] >= max(1, len(raws) // 3) else None


def _note(code: str, msg: str, **values) -> Dict:
    """Ein Hinweis: Text in der Sprache der Oberfläche, dazu Vorlage und Werte, damit der MCP-Server
    ihn auf Englisch ausgeben kann, egal in welcher Sprache geprüft wurde."""
    return {"code": code, "text": tr(msg, **values), "msg": msg, "values": values}


def _issue(code: str, **values) -> Dict:
    texts = {
        "no_year": "Kein Jahr angegeben.",
        "no_title": "Kein Titel erkennbar.",
        "no_volume": "Der Band fehlt ({value}).",
        "no_issue": "Das Heft fehlt ({value}).",
        "no_pages": "Die Seiten fehlen ({value}).",
        "no_doi": "Die DOI fehlt ({value}).",
        "doi_link": "Die DOI als Link angeben: https://doi.org/…",
        "et_al": "APA 7 nennt im Literaturverzeichnis alle Autor:innen bis 20 statt „et al.“ ({n}).",
        "authors_surname_first": "Autor:innen im Format „Nachname, Initialen“ angeben.",
        "authors_initials_first": "Autor:innen im Format „Initialen Nachname“ angeben.",
        "year_parens": "Das Jahr gehört in Klammern direkt hinter die Autor:innen: (2020).",
        "year_end": "Das Jahr gehört ans Ende, vor die Seiten oder die DOI.",
        "title_quotes": "Den Titel in Anführungszeichen setzen.",
        "ieee_vol": "Den Band als „vol.“ angeben.",
        "ieee_pp": "Die Seiten als „pp.“ angeben.",
        "number": "Der Eintrag ist nicht nummeriert.",
        "other_style": "Diese Angabe folgt einem anderen Stil ({value}).",
    }
    return _note(code, texts[code], **values)


def _mentions(text: str, value: object) -> bool:
    """Steht die Angabe (Band, erste Seite, Artikelnummer) als eigene Zahl in der Angabe, außerhalb von DOI und Jahr?"""
    token = re.split(r"[-\u2013\u2014\s]", str(value or "").strip())[0]
    if not token:
        return False
    plain = re.sub(r"(?:https?://\S+|doi:\s*\S+|10\.\d{4,9}/\S+)", " ", text, flags=re.IGNORECASE)
    plain = re.sub(r"\((?:1[89]|20)\d{2}[a-z]?\)", " ", plain)
    return bool(re.search(r"(?<![\w.])" + re.escape(token) + r"(?![\w])", plain))


def check_entry(raw: str, style: str, found: Optional[Dict] = None) -> List[Dict]:
    """Hinweise zu einer Angabe im gewählten Stil; ``found`` sind die echten Angaben (oder None)."""
    issues: List[Dict] = []
    text = _NUM_PREFIX.sub("", raw).strip()
    found = found or {}
    g = guess(raw)
    if g and g != style and not (style in ("apa", "harvard") and g in ("apa", "harvard")):
        issues.append(_issue("other_style", value=STYLES.get(g, g)))

    if not _YEAR.search(text):
        issues.append(_issue("no_year"))
    journal = str(found.get("type") or "") in ("journal-article", "article")
    if journal:
        if found.get("volume") and not _VOLUME.search(text) and not _mentions(text, found["volume"]):
            issues.append(_issue("no_volume", value=found["volume"]))
        if found.get("issue") and not _ISSUE.search(text) and style != "harvard":
            issues.append(_issue("no_issue", value=found["issue"]))
        if found.get("pages") and not _PAGES.search(text) and not _mentions(text, found["pages"]):
            issues.append(_issue("no_pages", value=found["pages"]))
    doi = found.get("doi")
    if doi and not str(doi).lower().startswith("10.48550/") and not _DOI_ANY.search(text) and style in ("apa", "harvard", "chicago", "vancouver"):
        issues.append(_issue("no_doi", value=doi))
    elif style == "apa" and _DOI_ANY.search(text) and not _DOI_URL.search(text):
        issues.append(_issue("doi_link"))

    if style in ALPHABETICAL:
        if _INITIALS_FIRST.match(text):
            issues.append(_issue("authors_surname_first"))
        count = int(found.get("author_count") or 0)
        if style == "apa" and re.search(r"\bet al\b", text[:200]) and 0 < count <= 20:
            issues.append(_issue("et_al", n=count))
        if style in ("apa", "harvard") and _YEAR.search(text) and not _YEAR_PAREN.search(text[:200]):
            issues.append(_issue("year_parens"))
    if style in NUMBERED:
        if not _NUM_PREFIX.match(raw):
            issues.append(_issue("number"))
    if style == "ieee":
        if _SURNAME_FIRST.match(text):
            issues.append(_issue("authors_initials_first"))
        if not _QUOTED.search(text):
            issues.append(_issue("title_quotes"))
        if found.get("volume") and _VOLUME.search(text) and not re.search(r"\bvol\.", text):
            issues.append(_issue("ieee_vol"))
        if _PAGES.search(text) and re.search(r"\d+\s*[-–]\s*\d+", text) and not re.search(r"\bpp?\.", text):
            issues.append(_issue("ieee_pp"))
        if _YEAR_PAREN.search(text[:160]):
            issues.append(_issue("year_end"))
    return issues


def _sort_key(result: Dict) -> str:
    claimed = result.get("claimed") or {}
    authors = claimed.get("authors") or []
    first = str(authors[0]) if authors else str(claimed.get("title") or result.get("input") or "")
    first = re.sub(r"^(?:van|von|de|der|den|da|di|le|la)\s+", "", first.strip(), flags=re.IGNORECASE)
    return _fold(first)


def check_list(results: List[Dict], style: Optional[str] = None) -> Dict:
    """Stilprüfung für die Ergebnisse eines Prüflaufs (Reihenfolge = Eingabe)."""
    rows = sorted([r for r in results if isinstance(r, dict)], key=lambda r: r.get("order", 0))
    raws = [str(r.get("input") or "") for r in rows]
    structured = sum(1 for raw in raws if raw.lstrip().startswith(("@", "TY  -")))
    detected = detect(raws)
    chosen = style if style in STYLES else (detected or "apa")
    out = {"style": chosen, "detected": detected, "styles": STYLES, "entries": {}, "list": [],
           "applicable": bool(rows) and structured < len(rows) / 2}
    if not out["applicable"]:
        return out
    others = 0
    for r, raw in zip(rows, raws):
        if not raw.strip():
            continue
        issues = check_entry(raw, chosen, r.get("found") if r.get("verdict") not in ("not_found", "mismatch") else None)
        if any(i["code"] == "other_style" for i in issues):
            others += 1
        if issues:
            out["entries"][str(r.get("order", 0))] = issues
    if others:
        out["list"].append(_note("mixed", "Gemischte Zitierstile: {n} von {total} Angaben folgen einem anderen Stil.",
                                 n=others, total=len(rows)))
    if chosen in ALPHABETICAL and len(rows) > 2:
        keys = [_sort_key(r) for r in rows]
        for a, b, ra, rb in zip(keys, keys[1:], rows, rows[1:]):
            if a and b and b < a:
                label = lambda r: (r.get("claimed") or {}).get("authors", [None])[0] if (r.get("claimed") or {}).get("authors") else str(r.get("identifier") or "")[:40]
                out["list"].append(_note("order", "Nicht alphabetisch sortiert: „{a}“ steht vor „{b}“.",
                                         a=label(ra), b=label(rb)))
                break
    if chosen in NUMBERED:
        nums = [int(m.group(1) or m.group(2)) for m in (_NUM_PREFIX.match(raw) for raw in raws) if m]
        for a, b in zip(nums, nums[1:]):
            if b != a + 1:
                out["list"].append(_note("gaps", "Die Nummern sind nicht fortlaufend: nach {a} folgt {b}.", a=a, b=b))
                break
    seen: Dict[str, int] = {}
    for r in rows:
        key = str(r.get("doi") or "").lower() or _fold((r.get("claimed") or {}).get("title") or "")
        if key:
            seen[key] = seen.get(key, 0) + 1
    dups = [k for k, n in seen.items() if n > 1]
    if dups:
        msg = "{n} Angabe steht doppelt im Verzeichnis." if len(dups) == 1 else "{n} Angaben stehen doppelt im Verzeichnis."
        out["list"].append(_note("duplicates", msg, n=len(dups)))
    return out
