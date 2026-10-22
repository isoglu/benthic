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
from typing import Dict, List, Optional

from . import refs
from .i18n import each_lang, tr, tr_part

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


_fold = refs.fold


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
    return {"code": code, "text": tr_part({"msg": msg, "values": values}), "msg": msg, "values": values}


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
    bare = refs._IDS.sub(" ", text)  # für Band, Heft und Seiten: eine PII in der DOI ist keine Seitenangabe
    found = found or {}
    g = guess(raw)
    if g and g != style and not (style in ("apa", "harvard") and g in ("apa", "harvard")):
        issues.append(_issue("other_style", value=STYLES.get(g, g)))

    if not _YEAR.search(text):
        issues.append(_issue("no_year"))
    journal = str(found.get("type") or "") in ("journal-article", "article")
    if journal:
        if found.get("volume") and not _VOLUME.search(bare) and not _mentions(text, found["volume"]):
            issues.append(_issue("no_volume", value=found["volume"]))
        if found.get("issue") and not _ISSUE.search(bare) and style != "harvard":
            issues.append(_issue("no_issue", value=found["issue"]))
        if found.get("pages") and not _PAGES.search(bare) and not _mentions(text, found["pages"]):
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
        if found.get("volume") and _VOLUME.search(bare) and not re.search(r"\bvol\.", text):
            issues.append(_issue("ieee_vol"))
        if _PAGES.search(bare) and re.search(r"\d+\s*[-–]\s*\d+", bare) and not re.search(r"\bpp?\.", text):
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


# --------------------------------------------------------------------------
# Eine DOI, ein Werk: in einer Liste, einem Stapel und einem Projekt
# --------------------------------------------------------------------------
_CONFLICT = {"year": "Dieselbe DOI {doi} steht mit verschiedenen Jahren da: {values}.",
             "volume": "Dieselbe DOI {doi} steht mit verschiedenen Bänden da: {values}.",
             "issue": "Dieselbe DOI {doi} steht mit verschiedenen Heften da: {values}.",
             "pages": "Dieselbe DOI {doi} steht mit verschiedenen Seiten da: {values}."}
_DOI_SAYS = {"volume": "{place}: Die DOI {doi} nennt Band {doi_value}, die Angabe Band {value}.",
             "issue": "{place}: Die DOI {doi} nennt Heft {doi_value}, die Angabe Heft {value}.",
             "first_page": "{place}: Die DOI {doi} beginnt auf Seite {doi_value}, die Angabe auf Seite {value}."}
KNOWN = ("verified", "corrected", "partial", "retracted")  # das Werk hinter der DOI steht fest


def _canon(field: str, value: object) -> str:
    v = str(value or "").strip()
    if field == "year":
        return v[:4]
    if field == "pages":
        return str(refs.pages_text(v) or "").lower()
    return re.sub(r"\s+", "", v).lstrip("0").lower()


def _disagree(field: str, values: List[str]) -> bool:
    """Widersprechen sich die Werte einer DOI? Ein Jahr Abstand nicht (online und gedruckt); Seiten und Hefte nach
    den Regeln des Abgleichs mit dem Werk (refs.pages_disagree, refs.issues_disagree)."""
    if field == "year":
        years = [int(v[:4]) for v in values if v[:4].isdigit()]
        return bool(years) and max(years) - min(years) > 1
    differ = {"pages": refs.pages_disagree, "issue": refs.issues_disagree}.get(field)  # dieselben Regeln wie verify
    if differ:
        return any(differ(a, b) for i, a in enumerate(values) for b in values[i + 1:])
    return len({_canon(field, v) for v in values}) > 1


def _same_work(a: Dict, b: Dict) -> bool:
    """Gleiche DOI oder ähnlicher Titel (Untertitel fehlt, Tippfehler). Ohne Titel und ohne zwei DOIs: im Zweifel ja."""
    da, db = str(a.get("doi") or "").lower(), str(b.get("doi") or "").lower()
    if da and da == db:
        return True
    if a.get("title") and b.get("title"):
        return refs.title_similarity(a["title"], b["title"]) >= 0.6
    return not (da and db)


def entries_from_results(results: List[Dict], where=None) -> List[Dict]:
    """Die Angaben eines Laufs für ``consistency``; ``where`` ist der Ort (ein Dateiname), sonst „Nr. 3“."""
    out = []
    for r in results:
        c = r.get("claimed") or {}
        n = int(r.get("order", 0)) + 1
        out.append({"doi": r.get("doi") if r.get("verdict") in KNOWN else r.get("claimed_doi"),
                    "printed_doi": r.get("claimed_doi"), "title": c.get("title"), "journal": c.get("journal") or c.get("venue"),
                    **{k: c.get(k) for k in ("year", "volume", "issue", "pages")},
                    "where": where or each_lang(lambda lang, n=n: tr("Nr. {n}", lang, n=n))})
    return out


def consistency(entries: List[Dict]) -> List[Dict]:
    """Hinweise, wo sich Angaben widersprechen: eine DOI mit verschiedenem Jahr, Band, Heft oder Seiten, zwei
    verschiedene Titel mit einer DOI, ein Seitenbereich bei zwei Werken, eine DOI, die selbst einen anderen Band
    oder eine andere erste Seite nennt. ``entries``: doi, printed_doi, title, journal, year, volume, issue, pages,
    where (each_lang, ein Text oder None)."""
    def place(w, lang: str) -> str:
        return str((w.get(lang) if isinstance(w, dict) else w) or "")

    def listing(groups: Dict[str, List]) -> Dict[str, str]:
        return each_lang(lambda lang: ", ".join(
            v + (" (" + ", ".join(dict.fromkeys(place(w, lang) for w in ws if w)) + ")" if any(ws) else "")
            for v, ws in groups.items()))

    def works(group: List[Dict]) -> Dict[str, str]:
        return each_lang(lambda lang: "; ".join(
            str(e.get("title") or e.get("doi") or "")[:70] + (f" ({place(e.get('where'), lang)})" if e.get("where") else "")
            for e in group))

    notes: List[Dict] = []
    by_doi: Dict[str, List[Dict]] = {}
    for e in entries:
        if e.get("doi"):
            by_doi.setdefault(str(e["doi"]).lower(), []).append(e)
    for doi, group in by_doi.items():
        for field in ("year", "volume", "issue", "pages"):
            seen: Dict[str, List] = {}
            for e in group:
                if _canon(field, e.get(field)):
                    seen.setdefault(str(refs.pages_text(e[field]) if field == "pages" else e[field]).strip(), []).append(e.get("where"))
            if _disagree(field, list(seen)):
                notes.append(_note("doi_conflict", _CONFLICT[field], doi=doi, values=listing(seen)))
        titled = [e for e in group if e.get("title")]
        pair = next(((a, b) for i, a in enumerate(titled) for b in titled[i + 1:]
                     if refs.title_similarity(a["title"], b["title"]) < 0.6), None)
        if pair:
            notes.append(_note("same_doi", "Zwei verschiedene Titel führen zur selben DOI {doi}: {works}. Eine der Angaben stimmt nicht.",
                               doi=doi, works=works(list(pair))))
    by_pages: Dict[tuple, List[Dict]] = {}
    for e in entries:  # nur mit Zeitschrift: „11, 1-12“ steht in vielen Zeitschriften
        first, _, last = _canon("pages", e.get("pages")).partition("–")
        journal = refs.normalize_title(str(e.get("journal") or ""))
        if journal and _canon("volume", e.get("volume")) and first and last and first != last:
            by_pages.setdefault((journal, _canon("volume", e["volume"]), first, last), []).append(e)
    for (_journal, volume, first, last), group in by_pages.items():
        distinct: List[Dict] = []
        for e in group:
            if not any(_same_work(e, k) for k in distinct):
                distinct.append(e)
        if len(distinct) > 1:
            notes.append(_note("same_pages", "Dieselben Seiten {pages} in Band {volume} stehen bei zwei verschiedenen Werken: {works}. "
                                             "Eine der Angaben stimmt nicht.", pages=f"{first}–{last}", volume=volume,
                               works=works(distinct[:2])))
    for e in entries:
        said = refs.doi_fields(str(e.get("printed_doi") or "")) if e.get("where") else {}
        printed = {"volume": e.get("volume"), "issue": e.get("issue"),
                   "first_page": _canon("pages", e.get("pages")).partition("–")[0]}
        for field, value in said.items():
            mine = _canon(field, printed[field])
            numbers = {n.lstrip("0") for n in re.findall(r"\d+", mine)} if field == "issue" else {mine.lstrip("0")}
            if mine and (field == "issue" or mine.isdigit()) and value.lstrip("0") not in numbers:
                notes.append(_note("doi_says", _DOI_SAYS[field], place=e["where"], doi=e["printed_doi"],
                                   doi_value=value, value=str(printed[field])))
    return notes


def check_list(results: List[Dict], style: Optional[str] = None) -> Dict:
    """Stilprüfung für die Ergebnisse eines Prüflaufs (Reihenfolge = Eingabe)."""
    rows = sorted([r for r in results if isinstance(r, dict)], key=lambda r: r.get("order", 0))
    raws = [str(r.get("input") or "") for r in rows]
    structured = sum(1 for raw in raws if raw.lstrip().startswith(("@", "TY  -")))
    detected = detect(raws)
    chosen = style if style in STYLES else (detected or "apa")
    out = {"style": chosen, "detected": detected, "styles": STYLES, "entries": {},
           "list": consistency(entries_from_results(rows)), "applicable": bool(rows) and structured < len(rows) / 2}
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
