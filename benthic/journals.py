"""Publikationsorte und Gesundheit einer Literaturliste.

Zwei Bausteine:

1. Kennzahlen je Publikationsort (Zeitschrift, Konferenz, Preprint-Server):
   - OpenAlex (CC0): Art des Orts, DOAJ, „core“-Quelle, 2-Jahres-Zitationsrate
     (entspricht einem Impact-Faktor), h-Index.
   - JUFO (Julkaisufoorumi, Finnland): Stufe 0 bis 3 über die öffentliche API,
     abgefragt je ISSN; die Oberfläche nennt die Quelle.
   - Eigene Rankinglisten (VHB-JOURQUAL, ABDC, AJG, SJR-Export, Listen der
     Hochschule): Nutzer:innen laden sie selbst beim Herausgeber herunter und
     importieren sie. Benthic liefert keine Listen mit und fragt keine
     Ranking-Websites automatisch ab; deren Bedingungen erlauben das meist nicht.

2. health(): bewertet eine Literaturliste in vier Dimensionen (Echtheit,
   Aktualität, Publikationsorte, Zugang), jede mit Zustand und Begründung.
   Alter allein macht eine Quelle nie „kritisch“: Klassiker sind legitim.

Jahreszahlen kommen aus der Prüfung (Crossref, arXiv), nicht aus OpenAlex,
weil OpenAlex bei Preprints teils das Jahr einer späteren Fassung führt.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import statistics
import threading
import time
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import config
from .i18n import current as current_lang, tr

# --------------------------------------------------------------------------
# Schwellen der Bewertung (bewusst einfach und erklärbar)
# --------------------------------------------------------------------------
RECENT_YEARS = 5           # „aktuell“: höchstens so alt
MEDIAN_AGE_NOTE = 15       # Median darüber: Hinweis, nie kritisch
PREPRINT_SHARE_NOTE = 0.30 # mehr Preprints: Hinweis (nicht begutachtet)
UNKNOWN_SHARE_NOTE = 0.30  # mehr Quellen ohne bekannten Publikationsort: Hinweis
JUFO_ZERO_SHARE_NOTE = 0.20

AGE_BUCKETS = [(0, 5, "0–5 Jahre"), (6, 10, "6–10 Jahre"), (11, 20, "11–20 Jahre"), (21, 10_000, "über 20 Jahre")]  # tr() bei Ausgabe

BAD = ("not_found", "mismatch", "retracted")
DOUBT = ("partial", "uncertain")
REAL = ("verified", "corrected", "partial")
ARXIV_PREFIX = "10.48550/"

CACHE_DAYS = 30
_lock = threading.RLock()


# --------------------------------------------------------------------------
# Normalisierung
# --------------------------------------------------------------------------
def norm_issn(value: object) -> Optional[str]:
    """'00280836', '0028-0836 ' oder 'ISSN 0028-0836' -> '0028-0836'."""
    s = re.sub(r"[^0-9Xx]", "", str(value or "")).upper()
    if len(s) != 8 or "X" in s[:7]:
        return None
    return f"{s[:4]}-{s[4:]}"


def norm_title(value: object) -> str:
    """Zeitschriftentitel für den Vergleich: klein, ohne Akzente, Satzzeichen und 'the'."""
    s = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"^the ", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def slug(name: str) -> str:
    s = norm_title(name).replace(" ", "-")
    return s[:48] or "liste"


# --------------------------------------------------------------------------
# Datei-Cache (Kennzahlen ändern sich langsam)
# --------------------------------------------------------------------------
def _cache_path() -> Path:
    return config.data_dir() / "journals-cache.json"


_cache: Optional[Dict[str, Dict]] = None


def _cache_get(key: str) -> Optional[Dict]:
    global _cache
    with _lock:
        if _cache is None:
            try:
                _cache = json.loads(_cache_path().read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                _cache = {}
        hit = _cache.get(key)
        if hit and time.time() - hit.get("t", 0) < CACHE_DAYS * 86400:
            return hit.get("v")
        return None


def _cache_put(entries: Dict[str, Optional[Dict]]) -> None:
    global _cache
    with _lock:
        if _cache is None:
            _cache_get("")
        now = time.time()
        for k, v in entries.items():
            _cache[k] = {"t": now, "v": v}
        p = _cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)


# --------------------------------------------------------------------------
# OpenAlex: Publikationsort je DOI
# --------------------------------------------------------------------------
def source_from_openalex(src: Dict) -> Dict:
    """Ein OpenAlex-Source-Objekt in die Form, die Benthic anzeigt."""
    stats = src.get("summary_stats") or {}
    kind = src.get("type") or ""
    return {
        "id": src.get("id"),
        "name": src.get("display_name"),
        "issn": [i for i in (norm_issn(x) for x in (src.get("issn") or [])) if i] or
                ([norm_issn(src.get("issn_l"))] if norm_issn(src.get("issn_l")) else []),
        "type": kind,
        "preprint": kind == "repository",
        "doaj": bool(src.get("is_in_doaj")),
        "core": bool(src.get("is_core")),
        "citedness": round(float(stats["2yr_mean_citedness"]), 2) if stats.get("2yr_mean_citedness") is not None else None,
        "h_index": stats.get("h_index"),
        "publisher": src.get("host_organization_name"),
    }


ARXIV_SOURCE = {"id": "https://openalex.org/S4306400194", "name": "arXiv", "issn": ["2331-8422"], "type": "repository",
                "preprint": True, "doaj": False, "core": False, "citedness": None, "h_index": None, "publisher": "Cornell University"}


def _get(url: str, params: Optional[Dict] = None):
    """JSON-Antwort (Objekt oder Liste; JUFO antwortet mit Listen) oder None."""
    from . import oa  # spät, damit Tests ohne Netz das Modul laden können
    resp = oa._api_get(url, params=params, timeout=15.0)
    if resp is None or resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def venues_for_dois(dois: Iterable[str], email: str = "") -> Dict[str, Optional[Dict]]:
    """DOI -> Publikationsort (oder None, wenn unbekannt). Gebündelt und zwischengespeichert."""
    dois = [d.strip().lower() for d in dois if d and d.strip()]
    out: Dict[str, Optional[Dict]] = {}
    todo = []
    for d in dict.fromkeys(dois):
        if d.startswith(ARXIV_PREFIX):
            out[d] = dict(ARXIV_SOURCE)
            continue
        hit = _cache_get("doi:" + d)
        if hit is not None:
            out[d] = hit.get("venue")
        else:
            todo.append(d)
    params_base = {"mailto": email} if email and "@" in email else {}
    src_of: Dict[str, Optional[str]] = {}
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        j = _get("https://api.openalex.org/works", {
            **params_base, "per_page": 50, "select": "doi,primary_location",
            "filter": "doi:" + "|".join("https://doi.org/" + d for d in chunk)}) or {}
        for w in j.get("results") or []:
            d = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            if d in chunk:
                src = (w.get("primary_location") or {}).get("source") or {}
                src_of[d] = src.get("id")
    ids = sorted({s for s in src_of.values() if s})
    sources: Dict[str, Dict] = {}
    for sid in ids:
        hit = _cache_get("src:" + sid)
        if hit is not None:
            sources[sid] = hit
    need = [s for s in ids if s not in sources]
    for i in range(0, len(need), 50):
        chunk = need[i:i + 50]
        j = _get("https://api.openalex.org/sources", {
            **params_base, "per_page": 50,
            "select": "id,display_name,issn_l,issn,type,is_in_doaj,is_core,summary_stats,host_organization_name",
            "filter": "openalex:" + "|".join(s.rsplit("/", 1)[-1] for s in chunk)}) or {}
        fresh = {}
        for s in j.get("results") or []:
            sources[s["id"]] = source_from_openalex(s)
            fresh["src:" + s["id"]] = sources[s["id"]]
        if fresh:
            _cache_put(fresh)
    fresh_docs = {}
    for d in todo:
        venue = sources.get(src_of.get(d) or "")
        out[d] = venue
        fresh_docs["doi:" + d] = {"venue": venue}
    if fresh_docs:
        _cache_put(fresh_docs)
    return out


# --------------------------------------------------------------------------
# JUFO (Julkaisufoorumi): Stufe 0 bis 3 je ISSN
# --------------------------------------------------------------------------
JUFO_SEARCH = "https://jufo-rest.csc.fi/v1.1/etsi.php"
JUFO_CHANNEL = "https://jufo-rest.csc.fi/v1.1/kanava/{}"


def jufo_level(issns: List[str]) -> Optional[int]:
    """Aktuelle JUFO-Stufe für die erste ISSN, die JUFO kennt (None: unbekannt)."""
    for issn in issns:
        key = "jufo:" + issn
        hit = _cache_get(key)
        if hit is not None:
            if hit.get("level") is not None:
                return hit["level"]
            continue
        level = None
        found = _get(JUFO_SEARCH, {"issn": issn})
        if isinstance(found, list) and found and found[0].get("Jufo_ID"):
            rec = _get(JUFO_CHANNEL.format(found[0]["Jufo_ID"]))
            rec = rec[0] if isinstance(rec, list) and rec else rec
            if isinstance(rec, dict) and str(rec.get("Level", "")).isdigit():
                level = int(rec["Level"])
        _cache_put({key: {"level": level}})
        if level is not None:
            return level
    return None


# --------------------------------------------------------------------------
# Eigene Rankinglisten (Import durch Nutzer:innen)
# --------------------------------------------------------------------------
_ISSN_COLS = ("issn", "eissn", "e-issn", "print issn", "online issn", "issn online", "issn print", "pissn", "p-issn")
_TITLE_COLS = ("journal title", "title", "journal", "zeitschrift", "source title", "name", "titel")
_RANK_COLS = ("rating", "rank", "ranking", "grade", "quartile", "sjr best quartile", "kategorie", "category",
              "jq4", "jq3", "abdc rating", "ajg", "ajg 2024", "ajg 2021", "abs", "level", "stufe", "bewertung")


def _rankings_dir() -> Path:
    return config.data_dir() / "rankings"


def _pick(header: List[str], names: Iterable[str]) -> List[int]:
    low = [h.strip().lower() for h in header]
    hits = []
    for n in names:
        for i, h in enumerate(low):
            if (h == n or h.startswith(n + " ") or h.endswith(" " + n)) and i not in hits:
                hits.append(i)
    return hits


def parse_ranking_rows(rows: List[List[str]]) -> List[Dict]:
    """Tabellenzeilen (erste Zeile mit Spaltennamen) -> Einträge {issn, title, title_norm, rank}."""
    head_at = next((i for i, r in enumerate(rows[:15]) if _pick(r, _TITLE_COLS) and _pick(r, _RANK_COLS)), None)
    if head_at is None:
        raise ValueError(tr("Keine Spalten für Titel und Rang gefunden (z. B. „Journal Title“ und „Rating“)."))
    header = [str(c or "") for c in rows[head_at]]
    t_col = _pick(header, _TITLE_COLS)[0]
    r_col = _pick(header, _RANK_COLS)[0]
    i_cols = _pick(header, _ISSN_COLS)
    entries = []
    for r in rows[head_at + 1:]:
        cells = [str(c).strip() if c is not None else "" for c in r]
        if len(cells) <= max([t_col, r_col] + i_cols):
            cells += [""] * (max([t_col, r_col] + i_cols) + 1 - len(cells))
        title, rank = cells[t_col], cells[r_col]
        if not title or not rank:
            continue
        issns = sorted({i for i in (norm_issn(cells[c]) for c in i_cols) if i})
        entries.append({"issn": issns, "title": title, "title_norm": norm_title(title), "rank": rank})
    if not entries:
        raise ValueError(tr("Die Liste enthält keine Einträge mit Titel und Rang."))
    return entries


def read_table(filename: str, data: bytes) -> List[List[str]]:
    if filename.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        import zipfile
        from . import document
        with zipfile.ZipFile(io.BytesIO(data)) as z:  # kein Entpacken über document.MAX_UNPACKED
            try:
                document.unpacked_size(z, z.namelist())
            except ValueError:
                raise ValueError(tr("Die Datei ließ sich nicht lesen. CSV oder XLSX?")) from None
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [[c for c in row] for row in ws.iter_rows(values_only=True)]
    text = data.decode("utf-8-sig", errors="replace")
    # Trennzeichen nach Mehrheit der ersten Zeilen: Vorspann wie „Stand, 2025“
    # führt den csv.Sniffer sonst auf das Komma.
    lines = [l for l in text.splitlines() if l.strip()][:50]
    delim = max((";", "\t", ","), key=lambda d: sum(1 for l in lines if l.count(d) >= 2))
    return [row for row in csv.reader(io.StringIO(text), delimiter=delim)]


def import_ranking(name: str, filename: str, data: bytes) -> Dict:
    entries = parse_ranking_rows(read_table(filename, data))
    item = {"id": slug(name), "name": name.strip() or filename, "file": filename,
            "imported_at": time.time(), "count": len(entries), "entries": entries}
    d = _rankings_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (item["id"] + ".tmp")
    tmp.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / (item["id"] + ".json"))
    return {k: v for k, v in item.items() if k != "entries"}


def list_rankings(with_entries: bool = False) -> List[Dict]:
    out = []
    d = _rankings_dir()
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            item = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(item if with_entries else {k: v for k, v in item.items() if k != "entries"})
    return out


def delete_ranking(list_id: str) -> bool:
    p = _rankings_dir() / (slug(list_id) + ".json")
    if p.exists():
        p.unlink()
        return True
    return False


def match_rankings(rankings: List[Dict], issns: List[str], title: Optional[str]) -> List[Dict]:
    """Ränge aus allen importierten Listen: erst über die ISSN, dann über den genauen Titel."""
    t = norm_title(title)
    hits = []
    for lst in rankings:
        found = None
        for e in lst.get("entries") or []:
            if issns and set(e.get("issn") or []) & set(issns):
                found = e
                break
        if not found and t:
            found = next((e for e in lst.get("entries") or [] if e.get("title_norm") == t), None)
        if found:
            hits.append({"list": lst.get("name"), "rank": found.get("rank")})
    return hits


# --------------------------------------------------------------------------
# Gesundheit einer Literaturliste
# --------------------------------------------------------------------------
def _year(v: object) -> Optional[int]:
    m = re.search(r"(1[5-9]\d\d|20\d\d)", str(v or ""))
    return int(m.group(1)) if m else None


def _pct(n: int, total: int) -> int:
    return round(100 * n / total) if total else 0


def _plural(n: int, one: str, many: str) -> str:
    """„{n} Quelle …“ oder „{n} Quellen …“ in der eingestellten Sprache."""
    return tr(one if n == 1 else many, n=n)


def _num(x: float) -> str:
    return f"{x:g}".replace(".", ",") if current_lang() == "de" else f"{x:g}"


# Quellenarten: welche Quelle trägt welche Aussage? Fachartikel und Bücher den Forschungsstand, Berichte und Daten
# Marktzahlen, Webseiten Beispiele. Ein hoher Anteil der letzten beiden ist ein Hinweis, kein Fehler.
MIX_KINDS = ("journal", "book", "preprint", "report", "web", "other")
MIX_NAMES = {"journal": ("{n} Fachartikel", "{n} Fachartikel"), "book": ("{n} Buch", "{n} Bücher"),
             "preprint": ("{n} Preprint", "{n} Preprints"), "report": ("{n} Bericht oder Datensatz", "{n} Berichte oder Daten"),
             "web": ("{n} Webseite", "{n} Webseiten"), "other": ("{n} sonstige", "{n} sonstige")}
GREY_SHARE_NOTE = 0.3


def source_kind(item: Dict) -> Optional[str]:
    """Art einer Quelle aus dem Prüfergebnis (Webseite, Typ des Datensatzes) oder ihrem Publikationsort."""
    if item.get("web"):
        return "web"
    t = str(item.get("type") or "").lower()
    v = item.get("venue") or {}
    if "preprint" in t or t == "posted-content" or v.get("preprint"):
        return "preprint"
    if t.startswith("book") or t in ("monograph", "edited-book", "reference-book") or v.get("type") == "book series":
        return "book"
    if t in ("report", "report-component", "dataset", "standard"):
        return "report"
    if t in ("journal-article", "article", "review", "letter", "editorial") or v.get("type") == "journal":
        return "journal"
    return "other" if t or v else None


def health(items: List[Dict], this_year: Optional[int] = None, course: Optional[Dict] = None) -> Dict:
    """Bewertet eine Literaturliste.

    Jedes Element: {"verdict": Urteil oder None, "year": Jahr, "has_pdf": bool,
    "venue": Publikationsort aus venues_for_dois() oder None, "jufo": Stufe oder None}.
    """
    this_year = this_year or time.localtime().tm_year
    n = len(items)
    verdicts = [i.get("verdict") for i in items]
    count = lambda keys: sum(1 for v in verdicts if v in keys)  # noqa: E731
    dims = []

    # Echtheit
    bad, doubt, unchecked = count(BAD), count(DOUBT), sum(1 for v in verdicts if not v)
    retracted, invented, wrong = count(("retracted",)), count(("not_found",)), count(("mismatch",))
    if bad:
        parts = [p for p in (retracted and _plural(retracted, "{n} zurückgezogen", "{n} zurückgezogen"),
                             invented and _plural(invented, "{n} nicht auffindbar", "{n} nicht auffindbar"),
                             wrong and _plural(wrong, "{n} falsch zugeordnet", "{n} falsch zugeordnet")) if p]
        dims.append({"key": "real", "label": tr("Echtheit"), "state": "critical",
                     "headline": _plural(bad, "{n} Quelle ist nicht zitierfähig", "{n} Quellen sind nicht zitierfähig"),
                     "detail": ", ".join(parts) + "."})
    elif doubt or unchecked:
        bits = [b for b in (doubt and _plural(doubt, "{n} Angabe weicht ab oder ist unsicher", "{n} Angaben weichen ab oder sind unsicher"),
                            unchecked and _plural(unchecked, "{n} Quelle ist noch nicht geprüft", "{n} Quellen sind noch nicht geprüft")) if b]
        dims.append({"key": "real", "label": tr("Echtheit"), "state": "notes",
                     "headline": bits[0][0].upper() + bits[0][1:], "detail": "; ".join(bits) + "."})
    else:
        dims.append({"key": "real", "label": tr("Echtheit"), "state": "ok",
                     "headline": tr("Alle Quellen sind belegt"), "detail": tr("{n} von {n} geprüft und echt.", n=n)})

    # Aktualität
    years = sorted(y for y in (_year(i.get("year")) for i in items) if y and y <= this_year + 1)
    ages = [max(0, this_year - y) for y in years]
    age = {"known": len(years), "buckets": [], "by_year": {}}
    if years:
        med = statistics.median(ages)
        recent = sum(1 for a in ages if a <= RECENT_YEARS)
        age.update({"median": med, "newest": years[-1], "oldest": years[0],
                    "share_recent": _pct(recent, len(ages)),
                    "share_10": _pct(sum(1 for a in ages if a <= 10), len(ages))})
        for lo, hi, label in AGE_BUCKETS:
            age["buckets"].append({"label": tr(label), "count": sum(1 for a in ages if lo <= a <= hi)})
        for y in years:
            age["by_year"][str(y)] = age["by_year"].get(str(y), 0) + 1
        headline = tr("Median {med} Jahre, {share} % aus den letzten {years} Jahren",
                      med=_num(med), share=age["share_recent"], years=RECENT_YEARS)
        if not recent:
            state, detail = "notes", tr("Keine Quelle aus den letzten {years} Jahren; die neueste ist von {newest}.",
                                        years=RECENT_YEARS, newest=years[-1])
        elif med > MEDIAN_AGE_NOTE:
            state, detail = "notes", tr("Die Hälfte der Quellen ist älter als {years} Jahre. In schnellen Feldern lohnt ein Blick auf neuere Arbeiten.",
                                        years=MEDIAN_AGE_NOTE)
        else:
            state, detail = "ok", tr("Von {first} bis {last}.", first=years[0], last=years[-1])
        dims.append({"key": "age", "label": tr("Aktualität"), "state": state, "headline": headline, "detail": detail})
    else:
        dims.append({"key": "age", "label": tr("Aktualität"), "state": "unknown",
                     "headline": tr("Keine Jahresangaben"), "detail": tr("Prüfe die Liste, dann ergänzt Benthic die Jahre.")})

    # Quellenarten
    kinds = [source_kind(i) for i in items]
    mix = {k: kinds.count(k) for k in MIX_KINDS}
    mix["unknown"] = kinds.count(None)
    typed = n - mix["unknown"]
    if typed:
        parts = [_plural(mix[k], *MIX_NAMES[k]) for k in MIX_KINDS if mix[k]]
        grey = mix["web"] + mix["report"]
        if grey / typed > GREY_SHARE_NOTE:
            state, detail = "notes", tr("{pct} % sind Webseiten, Berichte oder Daten. Sie tragen Marktzahlen und Beispiele; "
                                        "für den Forschungsstand braucht es begutachtete Fachliteratur.", pct=_pct(grey, typed))
        else:
            state, detail = "ok", tr("Überwiegend Fachartikel und Bücher.")
        dims.append({"key": "mix", "label": tr("Quellenarten"), "state": state, "headline": ", ".join(parts[:3]),
                     "detail": detail})

    # Kursvorgaben aus den Hausregeln (house.py): Mindestzahl zitierfähiger Quellen, Arten, die der Kurs nicht vorsieht
    if course:
        problems = []
        usable = n - count(BAD)
        if course.get("min_sources") and usable < course["min_sources"]:
            problems.append(tr("{n} zitierfähige Quellen, verlangt sind mindestens {min}", n=usable, min=course["min_sources"]))
        for k in course.get("excluded") or []:
            if mix.get(k):
                problems.append(tr("{what}, die der Kurs nicht vorsieht", what=_plural(mix[k], *MIX_NAMES[k])))
        detail = "; ".join(problems[1:] + ([course["note"]] if course.get("note") else []))
        dims.append({"key": "course", "label": tr("Kursvorgaben"), "state": "notes" if problems else "ok",
                     "headline": problems[0] if problems else tr("Erfüllt"), "detail": detail})

    # Publikationsorte
    venues = [i.get("venue") for i in items]
    known = [v for v in venues if v]
    preprints = sum(1 for v in known if v.get("preprint"))
    journals = sum(1 for v in known if v.get("type") == "journal")
    unknown = n - len(known)
    jufo = [i.get("jufo") for i in items if i.get("jufo") is not None]
    jufo_counts = {str(k): jufo.count(k) for k in (3, 2, 1, 0)}
    ven = {"journals": journals, "preprints": preprints, "other": len(known) - journals - preprints,
           "unknown": unknown, "doaj": sum(1 for v in known if v.get("doaj")), "jufo": jufo_counts,
           "jufo_known": len(jufo)}
    notes = []
    if n and preprints / n > PREPRINT_SHARE_NOTE:
        notes.append(tr("{pct} % Preprints, also noch nicht begutachtet.", pct=_pct(preprints, n)))
    if n and unknown / n > UNKNOWN_SHARE_NOTE:
        notes.append(tr("Für {pct} % der Quellen ist kein Publikationsort bekannt.", pct=_pct(unknown, n)))
    removed = sum(1 for i in items if any(f["kind"] == "doaj_removed" for f in i.get("flags") or []))
    false_claims = sum(1 for i in items if any(f["kind"] == "doaj_false" for f in i.get("flags") or []))
    if removed:
        notes.append(_plural(removed, "{n} Quelle erscheint in einer Zeitschrift, die DOAJ wegen Verstößen gegen gute Praxis entfernt hat.",
                             "{n} Quellen erscheinen in Zeitschriften, die DOAJ wegen Verstößen gegen gute Praxis entfernt hat."))
    if false_claims:
        notes.append(_plural(false_claims, "{n} Quelle erscheint in einer Zeitschrift, die fälschlich angibt, im DOAJ zu stehen.",
                             "{n} Quellen erscheinen in Zeitschriften, die fälschlich angeben, im DOAJ zu stehen."))
    ven["flagged"] = removed + false_claims
    if jufo and jufo.count(0) / len(jufo) >= JUFO_ZERO_SHARE_NOTE:
        notes.append(_plural(jufo.count(0), "{n} Quelle erscheint in Zeitschriften der JUFO-Stufe 0.",
                             "{n} Quellen erscheinen in Zeitschriften der JUFO-Stufe 0."))
    headline = (_plural(journals, "{n} Zeitschrift", "{n} Zeitschriften") + ", "
                + _plural(preprints, "{n} Preprint", "{n} Preprints"))
    if not known:
        dims.append({"key": "venues", "label": tr("Publikationsorte"), "state": "unknown",
                     "headline": tr("Keine Angaben"), "detail": tr("Für keine Quelle ist ein Publikationsort bekannt.")})
    else:
        dims.append({"key": "venues", "label": tr("Publikationsorte"), "state": "notes" if notes else "ok",
                     "headline": headline, "detail": " ".join(notes) or tr("Überwiegend begutachtete Zeitschriften.")})

    # Zugang (Information, fließt nicht ins Gesamturteil ein; nur wenn bekannt)
    if any(i.get("has_pdf") is not None for i in items):
        pdfs = sum(1 for i in items if i.get("has_pdf"))
        dims.append({"key": "access", "label": tr("Zugang"), "state": "info",
                     "headline": tr("{pdfs} von {n} als PDF in der Bibliothek", pdfs=pdfs, n=n),
                     "detail": tr("Fehlende lädt Benthic nach, soweit eine freie Fassung existiert.")})

    states = [d["state"] for d in dims if d["key"] != "access"]
    overall = "critical" if "critical" in states else "notes" if "notes" in states else "ok"
    label = tr({"critical": "Kritisch", "notes": "Mit Hinweisen", "ok": "Gesund"}[overall])
    return {"state": overall, "label": label, "count": n, "dimensions": dims, "age": age, "venues": ven, "mix": mix}


# --------------------------------------------------------------------------
# Alles zusammen: Publikationsorte ergänzen, dann bewerten
# --------------------------------------------------------------------------
def assess(items: List[Dict], email: str = "", use_jufo: bool = True, course: Optional[Dict] = None) -> Dict:
    """items: [{doi, year, verdict, has_pdf?}] -> {"health": ..., "venues": {doi: Ort mit JUFO und Rängen}}."""
    from concurrent.futures import ThreadPoolExecutor
    clean = [i for i in items if isinstance(i, dict)]
    dois = [str(i.get("doi") or "").strip().lower() for i in clean]
    venues = venues_for_dois([d for d in dois if d], email)
    journals_by_id: Dict[str, Dict] = {}
    for v in venues.values():
        if v and v.get("type") == "journal" and v.get("issn"):
            journals_by_id.setdefault(v.get("id") or v["issn"][0], v)
    levels: Dict[str, Optional[int]] = {}
    if use_jufo and journals_by_id:
        keys = list(journals_by_id)
        with ThreadPoolExecutor(max_workers=6) as ex:
            for key, level in zip(keys, ex.map(lambda k: jufo_level(journals_by_id[k]["issn"]), keys)):
                levels[key] = level
    rankings = list_rankings(with_entries=True)
    from . import questionable  # spät: questionable nutzt norm_issn aus diesem Modul
    flags_by_id: Dict[str, List[Dict]] = {}
    for key, v in journals_by_id.items():
        try:
            flags_by_id[key] = questionable.journal_flags(v.get("issn") or [], bool(v.get("doaj")))
        except Exception:  # eine Liste, die nicht lädt, darf die Bewertung nicht verhindern
            flags_by_id[key] = []
    out_venues: Dict[str, Dict] = {}
    enriched = []
    for item, doi in zip(clean, dois):
        v = venues.get(doi) if doi else None
        info = None
        if v:
            key = v.get("id") or (v.get("issn") or [None])[0]
            info = {**v, "jufo": levels.get(key), "ranks": match_rankings(rankings, v.get("issn") or [], v.get("name")),
                    "flags": flags_by_id.get(key) or []}
            out_venues[doi] = info
        enriched.append({"verdict": item.get("verdict"), "year": item.get("year"), "has_pdf": item.get("has_pdf"),
                         "type": item.get("type"), "web": bool(item.get("web")), "venue": v, "jufo": info.get("jufo") if info else None,
                         "flags": info.get("flags") if info else []})
    return {"health": health(enriched, course=course), "venues": out_venues, "rankings": [r.get("name") for r in rankings]}
