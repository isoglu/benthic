"""Prüft Literaturangaben: Gibt es die Quelle, passt sie zur Angabe, ist sie zurückgezogen?

Gedacht für Literaturlisten aus LLMs. Für jede Referenz aus ``refs.parse``:

1. Kennung auflösen (DOI direkt, arXiv über DataCite, PMID über Europe PMC).
2. Das echte Werk holen (OpenAlex, sonst Crossref, sonst nur der DOI-Resolver).
3. Behauptete Angaben (Titel, Autor:innen, Jahr) mit dem echten Werk vergleichen.
4. Ohne oder mit falscher DOI: per Titel suchen (Crossref und OpenAlex, dann arXiv, Europe PMC mit PubMed,
   Semantic Scholar, DBLP, Open Library, DNB, lobid, K10plus).
5. Rückzüge erkennen (OpenAlex ``is_retracted``, Crossref ``updated-by``).

Urteile: verified, corrected, partial, uncertain, mismatch, not_found, retracted.
"""

from __future__ import annotations

import difflib
import re
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote
import xml.etree.ElementTree as ET

from . import netguard, oa, preprints, quota, refs
from .i18n import each_lang, join_list, note_part, tr, tr_parts

VERDICTS = ("verified", "corrected", "partial", "uncertain", "mismatch", "not_found", "retracted")

VERDICT_LABEL = {
    "verified": "Bestätigt",
    "corrected": "DOI korrigiert",
    "partial": "Weicht ab",
    "uncertain": "Unsicher",
    "mismatch": "Passt nicht",
    "not_found": "Nicht auffindbar",
    "retracted": "Zurückgezogen",
}

# Was zu tun ist, in einfachen Worten: in der App unter „Was tun?“, im Prüfbericht über jeder Gruppe.
VERDICT_ADVICE = {
    "verified": "Nichts zu korrigieren. Lies die Quelle trotzdem: Benthic prüft, ob es sie gibt, nicht, ob sie deine Aussage stützt.",
    "corrected": "Ersetze die DOI in deiner Angabe durch die richtige. Sonst führt der Link ins Leere oder zu einem anderen Werk.",
    "partial": "Vergleiche Jahr, Titel und Autor:innen mit dem Original und gleiche deine Angabe an. Solche Abweichungen entstehen oft beim Abschreiben.",
    "uncertain": "Öffne den ähnlichsten Treffer und vergleiche ihn mit deiner Angabe. Bücher, Praxisberichte und Webseiten stehen oft in keinem Verzeichnis; dann entscheidet der Blick ins Original.",
    "mismatch": "Die DOI gehört zu einem anderen Werk. Suche das gemeinte Werk und seine DOI; findest du es nicht, ist die Angabe vermutlich erfunden.",
    "not_found": "Suche das Werk selbst, etwa im Katalog deiner Bibliothek. Findest du es nicht, streiche die Angabe: Eine erfundene Quelle kann in einer Prüfung als Täuschung gelten.",
    "retracted": "Zitiere die Arbeit nicht als Beleg. Musst du sie erwähnen, nenne den Rückzug ausdrücklich.",
}

# Version des Ergebnis-Datensatzes (Urteil, Angabe, Feldvergleich je Quelle). Siegel, Postausgang und
# Campus-Server lesen ihn; erhöhen, sobald sich ein Feld ändert, das sie lesen. Fehlt sie, gilt 1.
RESULT_SCHEMA = 1

# Schwellen für den Titelvergleich (0 bis 1).
TITLE_OK = 0.85       # gleicher Titel (kleine Abweichungen, Untertitel)
TITLE_FOUND = 0.90    # Suche gilt als eindeutig
TITLE_MAYBE = 0.72    # ähnlichster Treffer, aber unsicher
TITLE_OTHER = 0.60    # darunter: anderes Werk


# --------------------------------------------------------------------------
# HTTP mit kleinem Cache (pro Prozess)
# --------------------------------------------------------------------------
_cache: Dict[str, object] = {}
_cache_lock = threading.Lock()


_FAILED = object()  # Quelle hat nicht geantwortet: nicht zwischenspeichern, später erneut fragen


def _cached(key: str, fn: Callable[[], object]) -> object:
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    value = fn()
    if value is _FAILED:
        return None
    with _cache_lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[key] = value
    return value


# Nachvollziehbarkeit: welche Quellen eine Prüfung gefragt hat und was sie fanden (je Thread)
_trace = threading.local()


def _t(source: str, step: str, **detail) -> None:
    steps = getattr(_trace, "steps", None)
    if steps is not None and len(steps) < 40:
        steps.append({"source": source, "step": step, **{k: v for k, v in detail.items() if v is not None}})


def _mailto(email: str) -> Dict[str, str]:
    """Polite pool von Crossref/OpenAlex, aber nie mit einer Platzhalter-Adresse."""
    if email and "@" in email and not email.endswith(("example.org", "example.com")):
        return {"mailto": email}
    return {}


def crossref_work(doi: str, email: str = "") -> Optional[Dict]:
    def fetch():
        resp = oa._api_get(f"https://api.crossref.org/works/{quote(doi, safe='/')}", params=_mailto(email))
        j = oa._json(resp)
        if j is None and (resp is None or resp.status_code != 404):
            return _FAILED  # keine Antwort: nicht merken
        return (j or {}).get("message")
    found = _cached("cr:" + doi.lower(), fetch)
    _t("Crossref", "doi", found=bool(found))
    return found  # type: ignore[return-value]


def _from_datacite(attr: Dict) -> Dict:
    titles = attr.get("titles") or []
    creators = attr.get("creators") or []
    names = [c.get("familyName") or oa._surname(c.get("name") or "") for c in creators]
    types = attr.get("types") or {}
    return {
        "doi": attr.get("doi"),
        "title": (titles[0].get("title") if titles else "") or "",
        "authors": [n for n in names if n][:12],
        "year": str(attr.get("publicationYear") or "") or None,
        "venue": attr.get("publisher"),
        "type": types.get("resourceTypeGeneral", "dataset").lower(),
        "volume": None, "issue": None, "pages": None,
        "author_count": len(creators),
        "retracted": False, "cited_by": attr.get("citationCount"),
        "is_oa": True, "source": "DataCite",
    }


def datacite_work(doi: str) -> Optional[Dict]:
    def fetch():
        resp = oa._api_get(f"https://api.datacite.org/dois/{quote(doi, safe='/')}", timeout=12.0)
        j = oa._json(resp)
        if j is None and (resp is None or resp.status_code != 404):
            return _FAILED
        attr = ((j or {}).get("data") or {}).get("attributes")
        return _from_datacite(attr) if attr and attr.get("titles") else None
    found = _cached("dc:" + doi.lower(), fetch)
    _t("DataCite", "doi", found=bool(found))
    return found  # type: ignore[return-value]


def doi_exists(doi: str) -> Optional[bool]:
    """Fragt den DOI-Resolver direkt (deckt auch DataCite, Zenodo, arXiv ab)."""
    def fetch():
        resp = oa._api_get(f"https://doi.org/api/handles/{quote(doi, safe='/')}", timeout=12.0)
        if resp is None:
            return None
        try:
            code = resp.json().get("responseCode")
        except ValueError:
            return None
        return True if code == 1 else (False if code == 100 else None)
    return _cached("hdl:" + doi.lower(), fetch)  # type: ignore[return-value]


def europepmc_by_pmid(pmid: str) -> Optional[Dict]:
    def fetch():
        j = oa._json(oa._api_get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": f"EXT_ID:{pmid} AND SRC:MED", "format": "json", "resultType": "lite"},
        ))
        hits = ((j or {}).get("resultList") or {}).get("result") or []
        return hits[0] if hits else None
    return _cached("pmid:" + pmid, fetch)  # type: ignore[return-value]


# arXiv bittet um höchstens eine Anfrage alle drei Sekunden.
_arxiv_lock = threading.Lock()
_arxiv_last = [0.0]


def _arxiv_query(params: Dict[str, str]) -> List[Dict]:
    def fetch():
        with _arxiv_lock:
            wait = 3.0 - (time.monotonic() - _arxiv_last[0])
            if wait > 0:
                time.sleep(wait)
            resp = oa._api_get("https://export.arxiv.org/api/query", params=params, timeout=15.0)
            _arxiv_last[0] = time.monotonic()
        if resp is None or resp.status_code != 200:
            return []
        out = []
        for entry in re.findall(r"<entry>(.*?)</entry>", resp.text, re.S):
            ident = re.search(r"<id>https?://arxiv\.org/abs/([^<]+?)(?:v\d+)?</id>", entry)
            title = re.search(r"<title>(.*?)</title>", entry, re.S)
            year = re.search(r"<published>(\d{4})", entry)
            if not ident or not title:
                continue
            names = re.findall(r"<name>(.*?)</name>", entry)
            out.append({
                "doi": refs.arxiv_doi(ident.group(1)),
                "title": re.sub(r"\s+", " ", title.group(1)).strip(),
                "authors": [refs._surname(n) for n in names][:12],
                "year": year.group(1) if year else None,
                "venue": "arXiv", "type": "preprint", "retracted": False,
                "cited_by": None, "is_oa": True, "source": "arXiv",
            })
        return out
    return _cached("arx:" + repr(sorted(params.items())), fetch)  # type: ignore[return-value]


def arxiv_meta(arxiv_id: str) -> Optional[Dict]:
    hits = _arxiv_query({"id_list": arxiv_id, "max_results": "1"})
    return hits[0] if hits else None


def _search_arxiv(title: str) -> List[Dict]:
    words = re.sub(r"[^\w\s-]", " ", title).split()
    if len(words) < 2:
        return []
    return _arxiv_query({"search_query": 'ti:"' + " ".join(words[:20]) + '"', "max_results": "3"})


# --------------------------------------------------------------------------
# Einheitliche Werk-Darstellung
# --------------------------------------------------------------------------
# Gleicher Titel, aber nicht das Werk: Rückzugsnotiz, Erratum, Kommentar oder Antwort zu einem Artikel. Der
# zurückgezogene Artikel selbst („RETRACTED: …“) bleibt ein Treffer, sonst ginge der Rückzug verloren.
_NOTICE_TITLE = re.compile(r"^\W*(?:notice of retraction|retraction(?:\s+notice)?|erratum|errata|corrigendum|correction|"
                           r"expression of concern|reply|response|rejoinder|comment(?:ary)?)\b\s*(?:to|on|for|regarding|:|[-\u2013])",
                           re.IGNORECASE)
_NOTICE_TYPES = ("erratum", "retraction", "paratext", "peer-review", "component")
# Fassungen neben dem Artikel: Arbeitspapier (NBER), Bericht, Datensatz, Ablage (Zenodo, figshare)
_COPY_TYPES = ("report", "report-component", "report-series", "repository", "dataset", "database")
_COPY_PREFIXES = ("10.3386/", "10.5281/", "10.6084/")


def _notice(title: str, kind: object = None, updates=None) -> bool:
    return (bool(_NOTICE_TITLE.match(title or "")) or str(kind or "").lower() in _NOTICE_TYPES
            or any(str(u.get("type", "")).lower() != "new_version" for u in updates or []))


def _from_openalex(w: Dict) -> Dict:
    names = [((a.get("author") or {}).get("display_name") or "") for a in (w.get("authorships") or [])]
    loc = (w.get("primary_location") or {}).get("source") or {}
    doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
    if not doi:  # z. B. Konferenzfassung ohne DOI, aber mit arXiv-Eintrag
        for l in w.get("locations") or []:
            m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", str(l.get("landing_page_url") or l.get("pdf_url") or ""))
            if m:
                doi = refs.arxiv_doi(m.group(1))
                break
    return {
        "doi": doi,
        "title": w.get("title") or w.get("display_name") or "",
        "authors": [oa._surname(n) for n in names if n][:12],
        "year": str(w.get("publication_year") or "") or None,
        "venue": loc.get("display_name"),
        "type": w.get("type"),
        "volume": (w.get("biblio") or {}).get("volume"),
        "issue": (w.get("biblio") or {}).get("issue"),
        "pages": "–".join(p for p in ((w.get("biblio") or {}).get("first_page"), (w.get("biblio") or {}).get("last_page")) if p) or None,
        "author_count": len(names),
        "retracted": bool(w.get("is_retracted")),
        "cited_by": w.get("cited_by_count"),
        "is_oa": bool((w.get("open_access") or {}).get("is_oa")),
        "source": "OpenAlex",
        "notice": _notice(w.get("title") or w.get("display_name") or "", w.get("type")),
    }


def _from_crossref(m: Dict) -> Dict:
    parts = ((m.get("issued") or m.get("published") or {}).get("date-parts") or [[None]])[0]
    updates = m.get("updated-by") or []
    return {
        "doi": m.get("DOI"),
        "title": ((m.get("title") or [""])[0]) or "",
        "authors": [a.get("family") or a.get("name") or "" for a in (m.get("author") or [])][:12],
        "year": str(parts[0]) if parts and parts[0] else None,
        "venue": (m.get("container-title") or [None])[0],
        "type": m.get("type"),
        "volume": m.get("volume"),
        "issue": m.get("issue"),
        "pages": (m.get("page") or "").replace("-", "–") or None,
        "author_count": len(m.get("author") or []),
        "retracted": any(str(u.get("type", "")).lower() in ("retraction", "removal") for u in updates),
        "cited_by": m.get("is-referenced-by-count"),
        "is_oa": None,
        "source": "Crossref",
        "notice": _notice((m.get("title") or [""])[0] or "", m.get("type"), m.get("update-to")),
    }


def lookup(doi: str, email: str = "") -> Optional[Dict]:
    """Echtes Werk zu einer DOI: OpenAlex zuerst (mit Rückzugs-Flag), dann Crossref."""
    arxiv_id = doi.split(".", 2)[2] if doi.lower().startswith("10.48550/arxiv.") else None
    w = oa.openalex_work(doi, email if _mailto(email) else "")
    _t("OpenAlex", "doi", found=bool(w and (w.get("title") or w.get("display_name"))))
    if arxiv_id:
        # Für arXiv zählt die erste Fassung; OpenAlex datiert manche Einträge neu.
        meta = arxiv_meta(arxiv_id)
        if meta:
            if w and (w.get("title") or w.get("display_name")):
                base = _from_openalex(w)
                return {**base, "year": meta["year"], "authors": base["authors"] or meta["authors"]}
            return meta
    if w and (w.get("title") or w.get("display_name")):
        work = _from_openalex(w)
        if not work["retracted"]:
            m = crossref_work(doi, email)
            if m and _from_crossref(m)["retracted"]:
                work["retracted"] = True
        return work
    m = crossref_work(doi, email)
    if m:
        return _from_crossref(m)
    dc = datacite_work(doi)
    if dc:
        return dc
    return None


# --------------------------------------------------------------------------
# Vergleich
# --------------------------------------------------------------------------
_norm_name = refs.name_key


title_similarity = refs.title_similarity


def compare(claimed: Dict, actual: Dict) -> Tuple[int, List[str], float]:
    """Vergleicht Angabe und Werk. Liefert (Score 0 bis 100, Abweichungen, Titelähnlichkeit)."""
    weights, got, flags = 0.0, 0.0, []
    sim = 1.0
    claim_title = claimed.get("title") or claimed.get("query")
    if claim_title:
        sim = title_similarity(str(claim_title), actual.get("title"))
        weights += 70
        got += 70 * sim
        if claimed.get("title") and sim < TITLE_OK:
            flags.append("title")
    if claimed.get("year") and actual.get("year"):
        weights += 15
        try:
            ok = abs(int(str(claimed["year"])) - int(str(actual["year"]))) <= 1  # Preprint vs. Druck
        except ValueError:
            ok = False
        got += 15 if ok else 0
        if not ok:
            flags.append("year")
    claimed_names = [n for n in (claimed.get("authors") or []) if n]
    if claimed_names and actual.get("authors"):
        weights += 15
        ok = any(refs.same_name(c, a) for c in claimed_names for a in actual["authors"] if a)
        got += 15 if ok else 0
        if not ok:
            flags.append("authors")
    score = round(100 * got / weights) if weights else 100
    return score, flags, sim


def _subsequence(part: List[str], whole: List[str]) -> bool:
    it = iter(whole)
    return all(x in it for x in part)


def _first_num(value: object) -> str:
    m = re.match(r"\s*([A-Za-z]{0,2}\d+)", str(value or ""))
    return m.group(1).lower().lstrip("0") if m else ""


def _value_diffs(claimed: Dict, actual: Dict) -> Dict[str, Dict]:
    """Band, Heft, Seiten und zitierte Seiten gegen das Werk. Nur, wenn die Angabe einen anderen Wert nennt
    und der Wert des Werks nirgends sonst in der Angabe steht: so macht ein falsch gelesener Wert keinen Fund."""
    out: Dict[str, Dict] = {}
    raw = str(claimed.get("raw") or "")

    def named(value: str) -> bool:
        return bool(value) and bool(re.search(r"(?<![\w.])0*" + re.escape(value) + r"(?!\w)", refs._IDS.sub(" ", raw), re.I))

    cv, av = _first_num(claimed.get("volume")), _first_num(actual.get("volume"))
    if cv and av and cv != av and not named(av):
        out["volume"] = {"claimed": str(claimed["volume"]), "found": str(actual["volume"]), "status": "warn"}
    if refs.issues_disagree(claimed.get("issue"), actual.get("issue")) and not any(
            named(i) for i in re.findall(r"\d+", str(actual.get("issue")))):
        out["issue"] = {"claimed": str(claimed["issue"]), "found": str(actual["issue"]), "status": "warn"}
    af, al = refs._span(actual.get("pages"))
    if refs.pages_disagree(claimed.get("pages"), actual.get("pages")) and not named(af):
        out["pages"] = {"claimed": str(refs.pages_text(claimed["pages"])), "found": str(refs.pages_text(actual["pages"])),
                        "status": "warn"}
    # Beginnt das Werk auf Seite 1, ist das oft die Zählung im Artikel (Artikelnummer-Zeitschriften): kein Vergleich.
    if af.isdigit() and al.isdigit() and af != "1" and 0 < int(al) - int(af) < 2000:
        outside = [p for p in claimed.get("cited_pages") or [] if str(p).isdigit() and not int(af) <= int(p) <= int(al)]
        if outside:
            out["cited_page"] = {"claimed": ", ".join(str(p) for p in outside[:3]), "found": f"{af}–{al}", "status": "warn"}
    return out


# Gruppen als Autor:in (ATLAS Collaboration, The ABC Consortium): keine Person, die in der Angabe fehlen kann
_GROUP = re.compile(r"\b(?:collaborat\w*|consorti\w*|group|team|network|investigators|committee|initiative|alliance|"
                    r"society|association|council|working party)\b", re.IGNORECASE)  # ganze Wörter: Teamson ist eine Person
# Namensteile vor dem Nachnamen, zusammengeschrieben wie im Schlüssel: vander, dela, da
_PARTICLE_RUN = re.compile(r"(?:van|von|der|den|de|del|della|da|das|di|do|dos|du|la|le|ten|ter|zu|y)+")


def _same_name(a: str, b: str) -> bool:
    """Zwei Schlüssel, die nicht gleich sind, aber denselben Namen meinen: „vanderwalt“ und „walt“ (der Rest ist ein
    Namensteil), ein Name von mindestens vier Buchstaben am Ende eines längeren, oder ein Buchstabe Unterschied in
    einem langen Namen (Steenkamp, Steenkamps). Zhou und Hou, Johnson und Son, Miller und Müller bleiben zwei."""
    if not (a and b):
        return False
    short, long_ = sorted((a, b), key=len)
    if long_.endswith(short) and (len(short) >= 4 or _PARTICLE_RUN.fullmatch(long_[:-len(short)] or "-")):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


def field_diffs(claimed: Dict, actual: Optional[Dict], values: bool = True) -> Dict[str, Dict]:
    """Einzelvergleich je Feld (title, authors, year, venue) für die Oberfläche und den Bericht. Band, Heft,
    Seiten, zitierte Seite und die Zahl der Autor:innen stehen nur darin, wenn sie abweichen, und nur mit
    ``values``: gegen einen unsicheren Treffer verglichen wären sie ein Befund über ein anderes Werk."""
    if not actual:
        return {}
    out: Dict[str, Dict] = {}
    ct, at = str(claimed.get("title") or "").strip(), str(actual.get("title") or "").strip()
    if ct and at:
        sim = title_similarity(ct, at)
        out["title"] = {
            "claimed": ct,
            "found": at,
            "status": "ok" if sim >= TITLE_OK else ("warn" if sim >= TITLE_MAYBE else "fail"),
            "similarity": round(sim, 2),
        }
    cy, ay = str(claimed.get("year") or "").strip(), str(actual.get("year") or "").strip()
    if cy and ay:
        try:
            diff = abs(int(cy[:4]) - int(ay[:4]))
            st = "ok" if diff == 0 else ("warn" if diff == 1 else "fail")
        except ValueError:
            st = "ok" if cy == ay else "fail"
        out["year"] = {"claimed": cy, "found": ay, "status": st}
    ca = [str(a) for a in (claimed.get("authors") or []) if a]
    aa = [str(a) for a in (actual.get("authors") or []) if a]
    if ca and aa:
        ak = [_norm_name(n) for n in aa]

        def canon(name: str) -> str:  # erst genau oder umschrieben gleich, dann Namensteile und Tippfehler
            exact = next((k for n, k in zip(aa, ak) if refs.same_name(name, n)), None)
            return exact or next((k for k in ak if _same_name(k, _norm_name(name))), _norm_name(name))
        ck = [canon(n) for n in ca]
        shared = [k for k in ck if k in ak]
        # Fehlt ein Name, zählt das nur, wenn das Werk vollständig und nicht kürzer als die Angabe ist: Verzeichnisse
        # führen manchmal nur die erste Person (Homburg statt Homburg, Hoyer und Koschate).
        complete = len(aa) >= max(int(actual.get("author_count") or 0), len(ca))
        if not shared:
            st = "fail"
        elif (len(set(shared)) < len(set(ck)) and complete) or ck[0] != ak[0] or not _subsequence(shared, ak):
            st = "warn"  # Name fehlt im Werk, andere Erstautor:in oder andere Reihenfolge
        else:
            st = "ok"
        out["authors"] = {"claimed": ", ".join(ca[:4]), "found": ", ".join(aa[:4]), "status": st}
        count = int(actual.get("author_count") or len(aa))
        # „et al.“ ohne weitere Person: nur bei einem vollständigen Werk mit mehr als einer Person; Verzeichnisse
        # führen oft nur die erste.
        if claimed.get("et_al") and 1 < count <= len(ca) and complete:
            out["author_count"] = {"claimed": f"{len(ca)} + et al.", "found": str(count), "status": "warn"}
        elif not claimed.get("et_al") and claimed.get("author_count") and (
                count - sum(1 for n in aa if _GROUP.search(n))) > int(claimed["author_count"]):  # ohne Konsortien
            out["author_count"] = {"claimed": str(claimed["author_count"]), "found": str(count), "status": "warn"}
    cv, av = str(claimed.get("venue") or "").strip(), str(actual.get("venue") or "").strip()
    if cv and av:
        vsim = title_similarity(cv, av)
        out["venue"] = {"claimed": cv, "found": av, "status": "ok" if vsim >= 0.75 else "warn"}
    if values:
        out.update(_value_diffs(claimed, actual))
    else:
        out.pop("author_count", None)
    return out


def _chimera_hint(claimed: Dict, best: Optional[Dict], best_sim: float) -> Optional[Dict]:
    if not best or not best.get("title") or not claimed.get("authors"):
        return None
    ca = {_norm_name(a) for a in (claimed.get("authors") or []) if a}
    ba = {_norm_name(a) for a in (best.get("authors") or []) if a}
    if (ca & ba) and 0.35 <= best_sim < TITLE_MAYBE:
        author_name = claimed["authors"][0]
        title_snippet = str(best.get("title") or "")[:120]
        year_str = str(best.get("year") or "") or "–"
        return note_part(
            "Kein Werk mit diesem Titel gefunden. Von {author} gibt es aber „{title}“ ({year}): Sprachmodelle vermischen oft echte Autor:innen mit einem erfundenen Titel.",
            author=author_name,
            title=title_snippet,
            year=year_str,
        )
    return None


# --------------------------------------------------------------------------
# Suche nach Titel
# --------------------------------------------------------------------------
def _search_crossref(query: str, email: str) -> List[Dict]:
    def fetch():
        params = {"query.bibliographic": query[:300], "rows": 5,
                  "select": "DOI,title,author,issued,published,container-title,type,updated-by,update-to,"
                            "is-referenced-by-count,volume,issue,page"}
        params.update(_mailto(email))
        j = oa._json(oa._api_get("https://api.crossref.org/works", params=params, timeout=15.0))
        if j is None:
            return _FAILED
        return [_from_crossref(m) for m in ((j.get("message") or {}).get("items") or [])]
    hits = _cached("crs:" + query.lower(), fetch)
    _t("Crossref", "search", hits=len(hits) if hits is not None else None, failed=hits is None or None)
    return hits or []  # type: ignore[return-value]


def _search_openalex(query: str, email: str, year: Optional[str] = None) -> List[Dict]:
    def fetch():
        params = {"search": re.sub(r"[,:;]", " ", query)[:300], "per-page": 5}
        if year and str(year).isdigit():  # Preprint und Druckfassung liegen oft ein Jahr auseinander
            params["filter"] = f"publication_year:{int(year) - 1}-{int(year) + 1}"
        params.update(_mailto(email))
        j = oa._json(oa._api_get("https://api.openalex.org/works", params=params, timeout=15.0))
        if j is None:
            return _FAILED
        return [_from_openalex(w) for w in (j.get("results") or [])]
    hits = _cached(f"oas:{year}:" + query.lower(), fetch)
    _t("OpenAlex", "search", hits=len(hits) if hits is not None else None, failed=hits is None or None)
    return hits or []  # type: ignore[return-value]


def _search_semanticscholar(title: str) -> List[Dict]:
    """Semantic Scholar: auch Magazine (HBR), Buchkapitel und Konferenzbeiträge ohne DOI."""
    def fetch():
        resp = oa._api_get("https://api.semanticscholar.org/graph/v1/paper/search/match", params={
            "query": title[:300], "fields": "title,year,authors,venue,externalIds,url,journal,publicationTypes"},
            timeout=15.0)
        if resp is not None and resp.status_code == 404:
            return []  # kein passender Titel
        j = oa._json(resp)
        if j is None:
            return _FAILED
        out = []
        for paper in j.get("data") or []:
            ext = paper.get("externalIds") or {}
            doi = ext.get("DOI") or (refs.arxiv_doi(ext["ArXiv"]) if ext.get("ArXiv") else None)
            names = [a.get("name") or "" for a in paper.get("authors") or []]
            out.append({"doi": doi, "title": paper.get("title") or "", "authors": [oa._surname(n) for n in names if n][:12],
                        "year": str(paper["year"]) if paper.get("year") else None,
                        "venue": paper.get("venue") or (paper.get("journal") or {}).get("name"),
                        "type": ",".join(paper.get("publicationTypes") or []) or None, "author_count": len(names),
                        "retracted": False, "cited_by": None, "is_oa": None, "source": "Semantic Scholar",
                        "url": paper.get("url")})
        return out
    hits = _cached("s2:" + title.lower(), fetch)
    _t("Semantic Scholar", "search", hits=len(hits) if hits is not None else None, failed=hits is None or None)
    return hits or []  # type: ignore[return-value]


# Gleicher Titel, aber nicht das Werk: Rückzugsnotiz, Erratum oder Leserbrief zu einem Artikel.
_EPMC_NOT_A_WORK = ("retraction notice", "published erratum", "expression of concern", "comment")


def _search_europepmc(title: str) -> List[Dict]:
    """Europe PMC enthält ganz PubMed, auch ältere Artikel ohne DOI. Der Titel geht als Phrase hinein."""
    phrase = " ".join(re.sub(r"[^\w\s-]", " ", title).split())[:300]
    if not phrase:
        return []
    def fetch():
        j = oa._json(oa._api_get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params={
            "query": f'TITLE:"{phrase}"', "format": "json", "resultType": "lite", "pageSize": 5}, timeout=15.0))
        if j is None:
            return _FAILED
        out = []
        for r in ((j.get("resultList") or {}).get("result") or []):
            kind = str(r.get("pubType") or "").lower()
            if any(k in kind for k in _EPMC_NOT_A_WORK):
                continue
            names = [n.strip() for n in str(r.get("authorString") or "").rstrip(".").split(",") if n.strip()]
            pmid = r.get("pmid")
            out.append({"doi": r.get("doi"), "title": str(r.get("title") or "").rstrip(".").strip("[]"),
                        "authors": [re.sub(r"\s+[A-Z]{1,4}$", "", n) for n in names][:12],  # "Haskell WL" -> "Haskell"
                        "year": r.get("pubYear"), "venue": r.get("journalTitle"), "type": kind or None,
                        "volume": r.get("journalVolume"), "issue": r.get("issue"), "pages": r.get("pageInfo"),
                        "author_count": len(names), "retracted": "retracted publication" in kind,
                        "cited_by": r.get("citedByCount"), "is_oa": r.get("isOpenAccess") == "Y", "source": "Europe PMC",
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid
                        else f"https://europepmc.org/article/{r.get('source')}/{r.get('id')}"})
        return out
    hits = _cached("epmc:" + phrase.lower(), fetch)
    _t("Europe PMC", "search", hits=len(hits) if hits is not None else None, failed=hits is None or None)
    return hits or []  # type: ignore[return-value]


def _search_openlibrary(title: str, authors: List[str]) -> List[Dict]:
    """Open Library: Bücher (ohne DOI), etwa Monografien und Sammelbände."""
    def fetch():
        params = {"title": title[:200], "limit": 5, "fields": "key,title,subtitle,author_name,first_publish_year,publisher"}
        if authors:
            params["author"] = authors[0]
        j = oa._json(oa._api_get("https://openlibrary.org/search.json", params=params, timeout=15.0))
        if j is None:
            return _FAILED
        out = []
        for d in j.get("docs") or []:
            full = d.get("title") or ""
            if d.get("subtitle"):
                full += ": " + d["subtitle"]
            names = d.get("author_name") or []
            out.append({"doi": None, "title": full, "authors": [oa._surname(n) for n in names][:12],
                        "year": str(d["first_publish_year"]) if d.get("first_publish_year") else None,
                        "venue": (d.get("publisher") or [None])[0], "type": "book", "author_count": len(names),
                        "retracted": False, "cited_by": None, "is_oa": None, "source": "Open Library",
                        "url": "https://openlibrary.org" + str(d.get("key") or "")})
        return out
    hits = _cached(f"ol:{(authors or [''])[0]}:" + title.lower(), fetch)
    _t("Open Library", "search", hits=len(hits) if hits is not None else None, failed=hits is None or None)
    return hits or []  # type: ignore[return-value]


def _search_dblp(title: str) -> List[Dict]:
    def fetch():
        resp = oa._api_get("https://dblp.org/search/publ/api", params={"q": title[:200], "format": "json", "h": "5"}, timeout=12.0)
        j = oa._json(resp)
        if j is None and (resp is None or resp.status_code != 404):
            return _FAILED
        hits = (((j or {}).get("result") or {}).get("hits") or {}).get("hit") or []
        out = []
        for h in hits:
            info = h.get("info") or {}
            raw_title = str(info.get("title") or "").rstrip(".")
            if not raw_title:
                continue
            authors_val = (info.get("authors") or {}).get("author") or []
            if not isinstance(authors_val, list):
                authors_val = [authors_val]
            authors = []
            for a in authors_val:
                name = (a.get("text") or a.get("#text") or a.get("name") or "") if isinstance(a, dict) else str(a)
                s = refs._surname(name)
                if s:
                    authors.append(s)
            doi = refs.clean_doi(info.get("doi") or "")
            out.append({
                "doi": doi,
                "title": raw_title,
                "authors": authors[:12],
                "year": str(info.get("year") or "") or None,
                "venue": info.get("venue"),
                "type": str(info.get("type") or "article").lower(),
                "volume": None, "issue": None, "pages": None,
                "author_count": len(authors),
                "retracted": False, "cited_by": None,
                "is_oa": None, "source": "DBLP",
                "url": info.get("url"),
            })
        return out
    hits = _cached("dblp:" + title[:100].lower(), fetch)
    _t("DBLP", "search", hits=len(hits) if hits is not None else None, failed=hits is None or None)
    return hits or []  # type: ignore[return-value]


def _parse_dnb_marc21(xml_text, source: str = "Deutsche Nationalbibliothek",
                      url: str = "https://d-nb.info/{id}") -> List[Dict]:
    """MARC21-XML einer SRU-Antwort (DNB, K10plus) als Treffer."""
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []
    records = []
    for rec in root.iter():
        if rec.tag.split("}")[-1] != "record":
            continue
        record_id = None
        title_a, title_b = "", ""
        authors = []
        year = None
        venue = None
        for child in rec:
            tag_name = child.tag.split("}")[-1]
            t = child.attrib.get("tag")
            if tag_name == "controlfield" and t == "001":
                record_id = (child.text or "").strip()
            elif tag_name == "datafield":
                if t == "245":
                    for sf in child:
                        code = sf.attrib.get("code")
                        if code == "a":
                            title_a = (sf.text or "").strip().rstrip(" /:")
                        elif code == "b":
                            title_b = (sf.text or "").strip().rstrip(" /:")
                elif t in ("100", "700"):
                    for sf in child:
                        if sf.attrib.get("code") == "a" and sf.text:
                            authors.append(refs._surname(sf.text.strip().rstrip(",")))
                elif t in ("260", "264"):
                    for sf in child:
                        code = sf.attrib.get("code")
                        if code == "c" and sf.text and not year:
                            m_y = refs._YEAR_RE.search(sf.text)
                            if m_y:
                                year = m_y.group(1)
                        elif code == "b" and sf.text and not venue:
                            venue = sf.text.strip().rstrip(",")
        full_title = f"{title_a}: {title_b}" if (title_a and title_b) else (title_a or title_b)
        if full_title:
            records.append({
                "doi": None,
                "title": full_title,
                "authors": [a for a in authors if a][:12],
                "year": year,
                "venue": venue,
                "type": "book",
                "author_count": len(authors),
                "retracted": False,
                "cited_by": None,
                "is_oa": None,
                "source": source,
                "url": url.format(id=record_id) if record_id else None,
            })
    return records


def _main_title(title: str, authors: Optional[List[str]] = None) -> str:
    """Der Haupttitel vor dem Untertitel ("Marketing: Grundlagen …"): Kataloge suchen Titel als Phrase, und Untertitel
    stehen dort oft anders. Ohne Namen nur, wenn der Haupttitel allein genug Wörter hat."""
    main = re.split(r"\s*[:.]\s+|\s+[–-]\s+", title, maxsplit=1)[0].strip()
    return main if main != title and len(main) >= 5 and (authors or len(main.split()) >= 3) else title


def _search_dnb(title: str = "", authors: Optional[List[str]] = None, isbn: Optional[str] = None) -> List[Dict]:
    authors = authors or []
    if isbn:
        query_str = f"NUM={isbn}"
        cache_key = f"dnb:isbn:{isbn}"
    else:
        clean_t = re.sub(r'["\\]', "", title)[:80].strip()
        if not clean_t:
            return []
        if authors:
            clean_a = re.sub(r'["\\]', "", authors[0]).strip()
            query_str = f'TIT="{clean_t}" and PER="{clean_a}"'
        else:
            query_str = f'TIT="{clean_t}"'
        cache_key = f"dnb:t:{(authors or [''])[0]}:{clean_t.lower()}"

    def fetch():
        resp = oa._api_get(
            "https://services.dnb.de/sru/dnb",
            params={
                "version": "1.1",
                "operation": "searchRetrieve",
                "recordSchema": "MARC21-xml",
                "maximumRecords": "3",
                "query": query_str,
            },
            timeout=12.0,
        )
        if resp is None and (resp is None or resp.status_code != 404):
            return _FAILED
        return _parse_dnb_marc21(resp.text if resp else "")

    hits = _cached(cache_key, fetch)
    _t("Deutsche Nationalbibliothek", "search", hits=len(hits) if hits is not None else None, failed=hits is None or None)
    return hits or []  # type: ignore[return-value]


def _attempts(title: str, authors: List[str], year: Optional[str]) -> List[Tuple[str, Optional[str]]]:
    """Suchfassungen für einen Katalog, die genaueste zuerst: Titel mit Jahr (bei Lehrbüchern mit vielen Auflagen
    trifft das die richtige), Titel ohne Jahr, Haupttitel mit Jahr."""
    out: List[Tuple[str, Optional[str]]] = []
    for t, y in ((title, year), (title, None), (_main_title(title, authors), year)):
        if t and (t, y) not in out:
            out.append((t, y))
    return out


def _catalog(name: str, title: str, authors: List[str], year: Optional[str], isbn: Optional[str],
             query: Callable[[str, Optional[str]], str], isbn_query: str, fetch_one: Callable[[str], object]) -> List[Dict]:
    """Fragt einen Katalog nacheinander mit den Suchfassungen, bis einer etwas findet."""
    queries = [isbn_query] if isbn else [query(t, y) for t, y in _attempts(title, authors, year)]
    for q in queries:
        hits = _cached(f"{name}:{q}", lambda q=q: fetch_one(q))
        _t(name, "search", hits=len(hits) if hits is not None else None, failed=hits is None or None)
        if hits:
            return hits  # type: ignore[return-value]
    return []


def _clean_q(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r'["\\]', "", text)).strip()[:80]


def _search_k10plus(title: str = "", authors: Optional[List[str]] = None, isbn: Optional[str] = None,
                    year: Optional[str] = None) -> List[Dict]:
    """K10plus, der gemeinsame Katalog der Verbünde GBV und SWB (SRU, MARC21)."""
    authors = authors or []

    def query(t: str, y: Optional[str]) -> str:
        first = " ".join(re.sub(r"[^\w\s-]", " ", t).split()[:5])  # die ersten ganzen Wörter, ohne Satzzeichen
        q = f'pica.tit="{first}"'
        if authors:
            q += f' and pica.per="{_clean_q(authors[0])}"'
        return q + (f" and pica.jhr={y}" if y else "")

    def fetch_one(q: str):
        resp = oa._api_get("https://sru.k10plus.de/opac-de-627", params={
            "version": "1.1", "operation": "searchRetrieve", "recordSchema": "marcxml", "maximumRecords": "3",
            "query": q}, timeout=12.0)
        if resp is None:
            return _FAILED
        # K10plus nennt keinen Zeichensatz; die Bytes gehen an den XML-Parser, der UTF-8 aus der Deklaration liest.
        return _parse_dnb_marc21(resp.content, "K10plus (GBV, SWB)", "https://opac.k10plus.de/DB=2.1/PPNSET?PPN={id}")

    if not isbn and not _clean_q(title):
        return []
    return _catalog("K10plus", title, authors, year, isbn, query, f"pica.isb={isbn}", fetch_one)


def _search_lobid(title: str = "", authors: Optional[List[str]] = None, isbn: Optional[str] = None,
                  year: Optional[str] = None) -> List[Dict]:
    """lobid-resources, der Verbundkatalog des hbz (JSON)."""
    authors = authors or []
    words = lambda t: " ".join(re.sub(r"[^\w\s-]", " ", t)[:120].split())  # noqa: E731

    def query(t: str, y: Optional[str]) -> str:
        q = f"title:({words(t)})"
        if authors:
            q += f" AND contribution.agent.label:({words(authors[0])})"
        return q + (f" AND publication.startDate:{y}" if y else "")

    def fetch_one(q: str):
        j = oa._json(oa._api_get("https://lobid.org/resources/search",
                                 params={"q": q, "format": "json", "size": "3"}, timeout=12.0))
        if j is None:
            return _FAILED
        out = []
        for m in j.get("member") or []:
            title_full = str(m.get("title") or "")
            sub = m.get("otherTitleInformation") or []
            if sub and isinstance(sub, list) and title_full and ":" not in title_full:
                title_full += ": " + str(sub[0])
            people = [((c.get("agent") or {}).get("label") or "") for c in m.get("contribution") or []
                      if "Person" in ((c.get("agent") or {}).get("type") or [])]
            pub = (m.get("publication") or [{}])[0] or {}
            found_year = refs._YEAR_RE.search(str(pub.get("startDate") or ""))
            out.append({"doi": None, "title": title_full, "authors": [refs._surname(n.split(",")[0]) for n in people if n][:12],
                        "year": found_year.group(1) if found_year else None, "venue": (pub.get("publishedBy") or [None])[0],
                        "type": "book", "author_count": len(people), "retracted": False, "cited_by": None, "is_oa": None,
                        "source": "lobid (hbz)", "url": str(m.get("id") or "").split("#")[0] or None})
        return [o for o in out if o["title"]]

    if not isbn and not words(title):
        return []
    return _catalog("lobid", title, authors, year, isbn, query, f"isbn:{isbn}", fetch_one)


def _lookup_openlibrary_isbn(isbn: str) -> Optional[Dict]:
    def fetch():
        resp = oa._api_get(
            "https://openlibrary.org/search.json",
            params={"isbn": isbn, "fields": "key,title,subtitle,author_name,first_publish_year,publisher", "limit": "3"},
            timeout=12.0,
        )
        j = oa._json(resp)
        if j is None and (resp is None or resp.status_code != 404):
            return _FAILED
        docs = (j or {}).get("docs") or []
        if not docs:
            return None
        d = docs[0]
        full = d.get("title") or ""
        if d.get("subtitle"):
            full += ": " + d["subtitle"]
        names = d.get("author_name") or []
        return {
            "doi": None,
            "title": full,
            "authors": [refs._surname(n) for n in names][:12],
            "year": str(d["first_publish_year"]) if d.get("first_publish_year") else None,
            "venue": (d.get("publisher") or [None])[0],
            "type": "book",
            "author_count": len(names),
            "retracted": False,
            "cited_by": None,
            "is_oa": None,
            "source": "Open Library",
            "url": "https://openlibrary.org" + str(d.get("key") or ""),
        }
    found = _cached("ol_isbn:" + isbn, fetch)
    _t("Open Library", "isbn", found=bool(found))
    return found  # type: ignore[return-value]


_COPY_WORDS = re.compile(r"\b(?:arxiv|biorxiv|medrxiv|ssrn|preprint|working paper|discussion paper|nber|technical report|"
                         r"zenodo|figshare|data ?set)\b")


def _names_copy(claimed: Dict) -> bool:
    """Nennt die Angabe außerhalb ihres Titels eine Fassung (Preprint, Arbeitspapier, Datensatz)? „A global dataset of
    crop yields“ ist ein Artikel über einen Datensatz. Verglichen werden Wörter nach refs.normalize_title, so stören
    Klammern ({GDHY}), LaTeX, Groß- und Kleinschreibung oder ein abgeschnittener Titel nicht."""
    title = set(refs.normalize_title(str(claimed.get("title") or "")).split())
    rest = [w for w in refs.normalize_title(str(claimed.get("raw") or "")).split() if w not in title]
    return bool(_COPY_WORDS.search(" ".join(rest)))


def search(claimed: Dict, email: str = "") -> Tuple[Optional[Dict], int, float]:
    """Bester Treffer für eine Angabe ohne (passende) DOI: (Werk, Score, Titelähnlichkeit)."""
    query = str(claimed.get("title") or claimed.get("query") or claimed.get("raw") or "").strip()
    if len(query) < 8:
        return None, 0, 0.0
    full = query
    if claimed.get("title"):
        extra = " ".join([*(claimed.get("authors") or [])[:2], str(claimed.get("year") or "")]).strip()
        full = f"{query} {extra}".strip()
    best, best_score, best_sim = None, -1, 0.0
    scored: List[Tuple[int, float, Dict]] = []
    wants_notice = bool(_NOTICE_TITLE.match(query))  # wer ein Erratum zitiert, soll es auch finden
    wants_copy = _names_copy(claimed)

    def is_copy(c: Dict) -> bool:
        doi = str(c.get("doi") or "").lower()
        return str(c.get("type") or "") in _COPY_TYPES or doi.startswith(_COPY_PREFIXES) or preprints.is_preprint(doi, c)

    def primary(c: Optional[Dict]) -> bool:
        """Ein veröffentlichtes Werk. Preprints, Arbeitspapiere, Berichte und Datensätze nur, wenn die Angabe selbst
        so etwas nennt; Gutachten und Bestandteile (Abbildungen, Anhänge) nie."""
        if not c or str(c.get("type") or "") in ("component", "peer-review"):
            return False
        return wants_copy or not is_copy(c)
    candidates = _search_crossref(full, email) + _search_openalex(query, email)
    if claimed.get("year"):
        candidates += _search_openalex(query, email, str(claimed["year"]))
    def pick(cands: List[Dict]) -> None:
        nonlocal best, best_score, best_sim
        for cand in cands:
            if not cand.get("title") or not (cand.get("doi") or cand.get("url")):
                continue
            if cand.get("notice") and not wants_notice:
                continue
            score, _flags, sim = compare(claimed, cand)
            scored.append((score, sim, cand))
            # Bei Gleichstand gewinnt das veröffentlichte Werk vor Preprint und Datensatz, dann der Treffer mit
            # Rückzugs-Information (OpenAlex).
            tie = score == best_score and (primary(cand), cand["source"] == "OpenAlex") >= (primary(best), True)
            if score > best_score or tie:
                best, best_score, best_sim = cand, score, sim

    pick(candidates)
    # Preprints (Informatik, Physik): arXiv selbst fragen, wenn nichts eindeutig passt.
    if claimed.get("title") and (best is None or best_sim < TITLE_FOUND or best_score < 90):
        pick(_search_arxiv(str(claimed["title"])))
    # Medizin ohne DOI, etwa ältere PubMed-Artikel: Europe PMC.
    if claimed.get("title") and (best is None or best_sim < TITLE_FOUND or best_score < 90):
        pick(_search_europepmc(str(claimed["title"])))
    # Magazine (HBR), Buchkapitel, Konferenzen ohne DOI: Semantic Scholar; Bücher: Open Library, DNB; Informatik: DBLP
    if claimed.get("title") and (best is None or best_sim < TITLE_FOUND or best_score < 90):
        pick(_search_semanticscholar(str(claimed["title"])))
    if claimed.get("title") and (best is None or best_sim < TITLE_FOUND or best_score < 90):
        pick(_search_dblp(str(claimed["title"])))
    if claimed.get("title") and (best is None or best_sim < TITLE_FOUND or best_score < 90):
        pick(_search_openlibrary(str(claimed["title"]), list(claimed.get("authors") or [])))
    if claimed.get("title") and (best is None or best_sim < TITLE_FOUND or best_score < 90):
        pick(_search_dnb(str(claimed["title"]), list(claimed.get("authors") or [])))
    # Bücher, die weder Open Library noch die DNB kennen: die Verbundkataloge (hbz, GBV und SWB).
    if claimed.get("title") and (best is None or best_sim < TITLE_FOUND or best_score < 90):
        pick(_search_lobid(str(claimed["title"]), list(claimed.get("authors") or []), year=claimed.get("year")))
    if claimed.get("title") and (best is None or best_sim < TITLE_FOUND or best_score < 90):
        pick(_search_k10plus(str(claimed["title"]), list(claimed.get("authors") or []), year=claimed.get("year")))
    if best:
        _t(best.get("source") or "", "best", title=best.get("title"), similarity=round(best_sim, 2), score=max(best_score, 0))
        # Eine DOI gibt es nur, wenn ein Werk klar vorn liegt. Preprints, Datensätze und Gutachten zählen nicht als
        # zweites Werk; zwei veröffentlichte Werke mit gleichem Titel, Jahr und Namen schon.
        rivals = {str(c["doi"]).lower() for sc, sm, c in scored  # eine Kopie ist nie Rivalin eines Artikels
                  if c.get("doi") and sm >= TITLE_FOUND and sc >= best_score - 5 and primary(c)
                  and (is_copy(best) or not is_copy(c))}
        if len(rivals) > 1:
            best = {**best, "ambiguous": sorted(rivals)[:4]}
    if best and best["source"] == "Crossref":
        richer = lookup(best["doi"], email)  # Rückzugs-Flag und Open-Access-Status nachladen
        if richer:
            best = {**best, "retracted": richer["retracted"] or best["retracted"], "is_oa": richer.get("is_oa")}
    return best, max(best_score, 0), best_sim


# --------------------------------------------------------------------------
# Urteil für eine Referenz
# --------------------------------------------------------------------------
# Satzteile für Abweichungen; eigene Tabelle, weil „Titel“ im Katalog die Spaltenüberschrift ist.
_FLAG_TEXT = {"de": {"title": "Titel", "year": "Jahr", "authors": "Autor:innen"},
              "en": {"title": "title", "year": "year", "authors": "authors"}}


def _diff_note(flags: List[str], via_title: bool = False) -> Dict:
    """„Werk gefunden, aber Titel und Jahr weichen von der Angabe ab.“, die Aufzählung in jeder Sprache."""
    diffs = each_lang(lambda lang: join_list([_FLAG_TEXT.get(lang, _FLAG_TEXT["en"])[f] for f in flags], lang))
    plural = len(flags) > 1 or flags == ["authors"]
    if via_title:
        return (note_part("Über den Titel gefunden, aber {diffs} weichen ab.", diffs=diffs) if plural
                else note_part("Über den Titel gefunden, aber {diffs} weicht ab.", diffs=diffs))
    return (note_part("Werk gefunden, aber {diffs} weichen von der Angabe ab.", diffs=diffs) if plural
            else note_part("Werk gefunden, aber {diffs} weicht von der Angabe ab.", diffs=diffs))


def _ambiguous_note(best: Dict) -> Dict:
    return note_part("Mehrere Werke passen zu dieser Angabe ({dois}). Benthic schlägt deshalb keine DOI vor; bitte im Original prüfen.",
                     dois=", ".join(best["ambiguous"]))


def _claimed(ref: Dict) -> Dict:
    return {k: ref.get(k) for k in ("title", "authors", "year", "venue", "query", "raw", "volume", "issue", "pages",
                                    "journal", "et_al", "author_count", "cited_pages")}


def verify_ref(ref: Dict, email: str = "", on_event: Optional[Callable[[Dict], None]] = None) -> Dict:
    label = str(ref.get("label") or refs.label(ref))

    def emit(phase: str, **kw) -> None:
        if on_event:
            try:
                on_event({"phase": phase, "identifier": label, **kw})
            except Exception:
                pass

    claimed = _claimed(ref)
    result: Dict = {
        "identifier": label,
        "input": str(ref.get("raw") or "")[:400],
        "format": ref.get("format"),
        "bib_key": ref.get("bib_key") or ref.get("key"),
        "bib_type": ref.get("bib_type"),
        "claimed": {k: claimed.get(k) for k in ("title", "authors", "year", "venue", "volume", "issue", "pages", "journal")},
        "doi": ref.get("doi"),
        "claimed_doi": ref.get("doi"),
        "resolved_by": None,
        "found": None,
        "verdict": "not_found",
        "score": 0,
        "flags": [],
        "note": "",
        "note_parts": [],  # derselbe Hinweis als Sätze (i18n.note_part), für die englische MCP-Antwort
    }
    emit("verify_start")
    _trace.steps = []
    quota.track()
    doi = ref.get("doi")
    if ref.get("arxiv") and doi:
        result["resolved_by"] = "arxiv"
    elif doi:
        result["resolved_by"] = "doi"

    # PMID ohne DOI: über Europe PMC auflösen.
    if not doi and ref.get("pmid"):
        emit("lookup", source="europepmc")
        hit = europepmc_by_pmid(str(ref["pmid"]))
        if hit and hit.get("doi"):
            doi = hit["doi"]
            result.update(doi=doi, resolved_by="pmid")
            if not claimed.get("title"):
                claimed["title"] = hit.get("title")

    def finish(verdict: str, note=None, found: Optional[Dict] = None, score: int = 0, flags=None) -> Dict:
        # note: ein Satz aus note_part; ein fertiger Text (graue Literatur) gilt als fester Satz.
        parts = [note if isinstance(note, dict) else note_part(str(note))] if note else []
        if found and found.get("retracted"):
            verdict, parts = "retracted", [note_part("Zurückgezogen.")] + parts
        failed = quota.problems()
        if verdict == "not_found" and failed:
            names = each_lang(lambda lang: join_list([f["name"] for f in failed], lang))
            if any(f["service"] == "crossref" for f in failed):
                # Die wichtigste Quelle hat nicht geantwortet: kein Urteil "erfunden" auf dieser Grundlage
                verdict = "uncertain"
                parts = [note_part("Nicht gefunden, aber {names} hat nicht geantwortet. Bitte später erneut prüfen.", names=names)]
            else:
                parts.append(note_part("{names} war nicht verfügbar; geprüft mit den übrigen Quellen.", names=names))
        result.update(verdict=verdict, note=tr_parts(parts), note_parts=parts, found=found, score=score, flags=flags or [])
        result["field_diffs"] = field_diffs(claimed, found, verdict in ("verified", "corrected", "partial", "retracted")) if found else {}
        result["trace"] = list(getattr(_trace, "steps", []) or [])
        result["sources_failed"] = failed
        if found and found.get("doi"):
            same = str(found["doi"]).lower() == str(result.get("doi") or "").lower()
            result["doi"] = result["doi"] if same else found["doi"]  # Schreibweise der Angabe behalten
        result["status"] = verdict
        emit("done", status=verdict)
        return result

    # ISBN ohne DOI: über Open Library, DNB und die Verbundkataloge auflösen.
    if not doi and ref.get("isbn"):
        isbn = str(ref["isbn"])
        result["isbn"] = isbn
        emit("lookup", source="openlibrary")
        hit = _lookup_openlibrary_isbn(isbn)
        if not hit:
            emit("lookup", source="dnb")
            dnb_hits = _search_dnb(isbn=isbn) or _search_lobid(isbn=isbn) or _search_k10plus(isbn=isbn)
            if dnb_hits:
                hit = dnb_hits[0]
        if hit:
            score, flags, sim = compare(claimed, hit)
            if sim >= TITLE_FOUND and "year" not in compare(claimed, hit)[1]:
                result["resolved_by"] = "isbn"
                if flags:
                    return finish("partial", _diff_note(flags, via_title=True), hit, score, flags)
                src_label = each_lang(lambda l: tr(hit.get("source") or "Open Library", l)) if hit.get("source") in ("Open Library", "Deutsche Nationalbibliothek") else (hit.get("source") or "Open Library")
                return finish("verified", note_part("Über die ISBN gefunden ({source}).", source=src_label), hit, score)
            elif sim >= TITLE_MAYBE:
                result["resolved_by"] = "isbn"
                return finish("uncertain", note_part("Ähnlichster Treffer zur ISBN: „{title}“ ({year}). Bitte prüfen.",
                                                     title=hit["title"],
                                                     year=hit.get("year") or each_lang(lambda lang: tr("o. J.", lang))),
                              hit, score)

    if doi:
        emit("lookup", source="openalex")
        actual = lookup(str(doi), email)
        if actual:
            score, flags, sim = compare(claimed, actual)
            if claimed.get("title") and sim < TITLE_OTHER:
                emit("search")
                best, bscore, bsim = search(claimed, email)
                if (best and bsim >= TITLE_FOUND and best.get("doi") and best["doi"].lower() != str(doi).lower()
                        and not best.get("ambiguous")):
                    result["pattern"] = "wrong_doi"
                    return finish("corrected",
                                  note_part("Die angegebene DOI gehört zu „{title}“. Das genannte Werk hat die DOI {doi}.",
                                            title=actual["title"], doi=best["doi"]), best, bscore)
                result["pattern"] = "wrong_doi"
                return finish("mismatch", note_part("Die DOI existiert, gehört aber zu „{title}“.", title=actual["title"]),
                              actual, score, flags)
            if flags:
                return finish("partial", _diff_note(flags), actual, score, flags)
            return finish("verified", "", actual, score)
        # Keine Metadaten: wenigstens prüfen, ob die DOI registriert ist.
        exists = doi_exists(str(doi))
        if exists:
            if claimed.get("title"):
                return finish("uncertain", note_part("Die DOI ist registriert, die Angaben lassen sich aber nicht abgleichen."))
            return finish("verified", note_part("DOI registriert (ohne abrufbare Metadaten)."), {"doi": doi, "title": ""}, 100)
        if exists is None and not claimed.get("title"):
            return finish("uncertain", note_part("Der DOI-Dienst hat nicht geantwortet. Später erneut prüfen."))
        # DOI existiert nicht: vielleicht stimmt der Titel trotzdem.
        if claimed.get("title") or claimed.get("query"):
            emit("search")
            best, bscore, bsim = search(claimed, email)
            if best and bsim >= TITLE_FOUND and best.get("ambiguous"):
                return finish("uncertain", _ambiguous_note(best))
            if best and bsim >= TITLE_FOUND:
                return finish("corrected", note_part("Die angegebene DOI existiert nicht. Das Werk hat die DOI {doi}.", doi=best["doi"]),
                              best, bscore)
        if (claimed.get("title") or claimed.get("query")) and best:
            ch_hint = _chimera_hint(claimed, best, bsim)
            if ch_hint:
                result["pattern"] = "chimera"
                return finish("not_found", ch_hint, found=best)
        return finish("not_found", note_part("Die DOI existiert nicht und kein passendes Werk wurde gefunden. Vermutlich erfunden."))

    # Keine Kennung: über den Titel bzw. die ganze Angabe suchen.
    emit("search")
    best, bscore, bsim = search(claimed, email)
    if best and bsim >= TITLE_FOUND and "year" not in compare(claimed, best)[1]:
        result["resolved_by"] = "title"
        if best.get("ambiguous"):
            return finish("uncertain", _ambiguous_note(best), {**best, "doi": None}, bscore)
        _score, flags, _sim = compare(claimed, best)
        if flags:
            return finish("partial", _diff_note(flags, via_title=True), best, bscore, flags)
        known_sources = ("Semantic Scholar", "Europe PMC", "Open Library", "DBLP", "DataCite", "Deutsche Nationalbibliothek",
                         "lobid (hbz)", "K10plus (GBV, SWB)")
        via = each_lang(lambda l: tr(best.get("source"), l)) if best.get("source") in ("Open Library", "Deutsche Nationalbibliothek") else best.get("source") if best.get("source") in known_sources else None
        return finish("verified", note_part("Über den Titel gefunden ({source}).", source=via) if via
                      else note_part("Über den Titel gefunden."), best, bscore)
    if best and bsim >= TITLE_MAYBE:
        result["resolved_by"] = "title"
        return finish("uncertain", note_part("Ähnlichster Treffer: „{title}“ ({year}). Bitte prüfen.", title=best["title"],
                                             year=best.get("year") or each_lang(lambda lang: tr("o. J.", lang))),
                      best, bscore)
    web = _web_source(str(ref.get("raw") or ""), str(claimed.get("title") or ""))
    if web:
        result["web"] = web.pop("web")
        return finish(web["verdict"], web["note"])
    grey = _grey_literature(str(ref.get("raw") or ""))
    if grey:
        return finish(*grey)
    ch_hint = _chimera_hint(claimed, best, bsim)
    if ch_hint:
        result["pattern"] = "chimera"
        return finish("not_found", ch_hint, found=best)
    return finish("not_found", note_part("Kein passendes Werk gefunden. Vermutlich erfunden oder sehr ungenau angegeben."))


# Graue Literatur: echte Quellen, die Crossref, OpenAlex und arXiv nicht verzeichnen. Ohne Fund lautet das
# Urteil dann "Unsicher" statt "Nicht auffindbar", mit einem Hinweis, was zu prüfen ist. Erfundene Links
# bleiben "Nicht auffindbar": der Link wird aufgerufen.
_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_CHAPTER = re.compile(r"\bIn\b[^.]{0,160}\((?:Eds?|Hrsg|Hg|eds?)\.?\)|\bIn:\s|\(Hrsg\.\)|\(Eds?\.\)", re.IGNORECASE)
_MAGAZINES = re.compile(r"McKinsey|Harvard Business Review|MIT Sloan Management Review|Boston Consulting|\bBCG\b|"
                        r"\bBain\b|Deloitte|PwC|PricewaterhouseCoopers|KPMG|Ernst & Young|\bEY\b|Gartner|Forrester|"
                        r"Forbes|The Economist|Financial Times|Wall Street Journal|Handelsblatt|strategy\+business",
                        re.IGNORECASE)


def _fetch_page(url: str) -> Tuple[Optional[bool], str]:
    """(erreichbar, Seitentitel). True: die Seite gibt es (auch wenn sie Programme aussperrt); False: Adresse falsch;
    None: unklar. Gelesen werden höchstens 300 kB, genug für den Kopf einer Seite.

    Nur Adressen im öffentlichen Internet (netguard): Ein Link auf diesen Mac oder ins eigene Netz bleibt
    unklar, wird nicht aufgerufen und geht auch nicht ans Internet Archive.
    """
    import requests
    url = url.rstrip(".,;")
    if not netguard.url_allowed(url):
        return None, ""
    with netguard.public_only(url) as guard:
        try:
            resp = requests.get(url, timeout=10.0, allow_redirects=True, stream=True,
                                headers={"User-Agent": oa.PDF_USER_AGENT, "Accept": "text/html,*/*"})
        except requests.exceptions.ConnectionError:
            # Domain gibt es nicht oder nimmt keine Verbindung an; eine Weiterleitung ins eigene Netz bleibt unklar
            return (None if guard["blocked"] else False), ""
        except requests.exceptions.RequestException:
            return None, ""
    title = ""
    try:
        if resp.status_code < 400 and "html" in str(resp.headers.get("Content-Type") or "").lower():
            head = b""
            for chunk in resp.iter_content(16384):
                head += chunk
                if len(head) > 300_000 or b"</head>" in head.lower():
                    break
            title = _page_title(head.decode(resp.encoding or "utf-8", "replace"))
    except requests.exceptions.RequestException:
        pass
    finally:
        resp.close()
    if resp.status_code in (404, 410):
        return False, ""
    return resp.status_code < 400 or resp.status_code in (401, 403, 429), title  # gesperrt heißt: es gibt die Seite


def _url_reachable(url: str) -> Optional[bool]:
    return _fetch_page(url)[0]


_META_TITLE = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:citation_title|og:title|dc\.title|twitter:title)["\'][^>]*>', re.I)
_CONTENT = re.compile(r'content=["\']([^"\']+)["\']', re.I)
_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _page_title(page: str) -> str:
    """Titel einer Webseite: erst Metadaten für Zitate (citation_title, og:title), sonst <title>."""
    import html as _html
    for m in _META_TITLE.finditer(page):
        c = _CONTENT.search(m.group(0))
        if c and c.group(1).strip():
            return _html.unescape(c.group(1)).strip()[:300]
    m = _TITLE_TAG.search(page)
    return re.sub(r"\s+", " ", _html.unescape(m.group(1))).strip()[:300] if m else ""


def wayback(url: str) -> Optional[Dict[str, str]]:
    """Nächster Schnappschuss im Internet Archive (archive.org/wayback/available): Adresse und Datum, sonst None."""
    def fetch():
        j = oa._json(oa._api_get("https://archive.org/wayback/available", params={"url": url}, timeout=12.0))
        if j is None:
            return _FAILED
        snap = ((j.get("archived_snapshots") or {}).get("closest") or {})
        if not snap.get("available") or not snap.get("url"):
            return None
        ts = str(snap.get("timestamp") or "")
        return {"url": str(snap["url"]).replace("http://", "https://", 1),
                "date": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else ""}
    hit = _cached("wb:" + url, fetch)
    return hit if isinstance(hit, dict) else None


def _web_source(raw: str, title: str) -> Optional[Dict]:
    """Webseiten und graue Literatur mit Link: Ist die Seite erreichbar, passt ihr Titel zur Angabe, gibt es einen
    Schnappschuss im Internet Archive? Ein toter Link mit Schnappschuss ist eine echte, umgezogene Quelle, nicht
    erfunden. ``web`` hält Link, Seitentitel, Schnappschuss und Abrufdatum für die Angabe fest."""
    url = next((u.rstrip(".,;") for u in _URL.findall(raw) if "doi.org" not in u.lower()), None)
    if not url:
        return None
    reachable, page_title = _fetch_page(url)
    snap = wayback(url) if reachable is not None else None
    web = {"url": url, "reachable": reachable, "page_title": page_title, "accessed": time.strftime("%Y-%m-%d"),
           "archive_url": (snap or {}).get("url", ""), "archive_date": (snap or {}).get("date", "")}
    # Seitentitel tragen oft den Namen der Website („… | OECD“, „… - Wikipedia“): jeden Teil einzeln vergleichen.
    parts = [page_title] + [p.strip() for p in re.split(r"\s+[|\u2013\u2014-]\s+|\s+::\s+", page_title) if p.strip()]
    sim = max((title_similarity(title, p) for p in parts), default=0.0) if title and page_title else 0.0
    if reachable and sim >= TITLE_FOUND:
        return {"verdict": "verified", "web": web,
                "note": tr("Webquelle: Die Seite ist erreichbar, und ihr Titel passt zur Angabe. Inhalt bitte selbst lesen.")}
    if reachable:
        note = (tr("Webquelle: Der Link ist erreichbar, die Seite heißt aber „{page}“. Bitte prüfen, ob es die gemeinte Seite ist.", page=page_title)
                if page_title and title and sim < TITLE_MAYBE else
                tr("Webseite oder graue Literatur: nicht in den wissenschaftlichen Verzeichnissen, der Link ist aber erreichbar. Inhalt bitte selbst prüfen."))
        return {"verdict": "uncertain", "note": note, "web": web}
    if snap:
        return {"verdict": "uncertain", "web": web,
                "note": tr("Der Link ist nicht mehr erreichbar, die Seite ist aber im Internet Archive gesichert ({date}). Zitiere besser den Schnappschuss.", date=snap.get("date") or "")}
    if reachable is False:
        return {"verdict": "not_found", "web": web,
                "note": tr("Nicht in den Verzeichnissen, und der angegebene Link ist nicht erreichbar.")}
    return None


def _grey_literature(raw: str) -> Optional[tuple]:
    if _CHAPTER.search(raw):
        return ("uncertain", tr("Buchkapitel: oft nicht in Crossref oder OpenAlex verzeichnet. Bitte im Katalog "
                                "einer Bibliothek prüfen."))
    if _MAGAZINES.search(raw):
        return ("uncertain", tr("Beratungs- oder Wirtschaftsquelle: meist nicht in den wissenschaftlichen "
                                "Verzeichnissen. Bitte beim Herausgeber prüfen."))
    return None


def summarize(results: List[Dict]) -> Dict[str, int]:
    counts = {v: 0 for v in VERDICTS}
    for r in results:
        v = r.get("verdict")
        if v in counts:
            counts[v] += 1
    return counts
