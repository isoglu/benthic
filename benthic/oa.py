"""Open-Access-Resolver.

Ermittelt für eine DOI kostenlos und frei verfügbare PDFs über offene
Aggregatoren und Repositorien. Jede Quelle liefert eine Liste von
**Kandidaten** (PDF-URL + Fassung). Der Downloader probiert sie der Reihe nach,
bis ein Link wirklich ein PDF liefert; ein toter Link oder eine Landingpage
führt also nicht zum Abbruch.

Quellen (Standard-Reihenfolge):
  1. arXiv:            DOIs der Form 10.48550/arXiv.<id> direkt
  2. OpenAlex:         alle offenen ``locations`` mit PDF-URL
  3. Unpaywall:        alle ``oa_locations`` mit ``url_for_pdf``
  4. Europe PMC:       Volltext-Links mit Status „Open access“ / „Free“
  5. Semantic Scholar: ``openAccessPdf.url``
  6. CORE:             ``downloadUrl`` (nur bei exakt passender DOI)
  7. Crossref:         PDF-Link nur bei offener Lizenz (Creative Commons)

Fassungen: ``published`` (Verlagsversion), ``accepted`` (akzeptiertes
Manuskript), ``submitted`` (Preprint), ``unknown``.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests

from . import __version__, crossref

API_USER_AGENT = f"Benthic/{__version__} (reference checker; https://benthic.isoglu.com)"

# Browser-UA für PDF-Downloads (manche OA-Server liefern Default-Clients nichts aus).
PDF_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)

DEFAULT_EMAIL = "benthic-demo@example.org"

PROVIDERS: List[str] = [
    "arxiv", "openalex", "unpaywall", "europepmc",
    "semantic_scholar", "core", "elsevier", "crossref",
]

VERSION_RANK = {"published": 0, "accepted": 1, "submitted": 2, "unknown": 3}
VERSION_LABEL = {
    "published": "Verlagsversion",
    "accepted": "akzeptiertes Manuskript",
    "submitted": "Preprint",
    "unknown": "unbekannt",
}

_VERSION_MAP = {
    "publishedversion": "published",
    "acceptedversion": "accepted",
    "submittedversion": "submitted",
}


def _norm_version(value: Optional[str]) -> str:
    return _VERSION_MAP.get((value or "").replace(" ", "").lower(), "unknown")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _api_get(url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None,
             timeout: float = 20.0) -> Optional[requests.Response]:
    """GET mit höflichem Umgang mit Rate-Limits (429/503: kurz warten, max. 2 Wiederholungen)."""
    hdrs = {"User-Agent": API_USER_AGENT}
    if "api.openalex.org" in url:
        key = _openalex_key()
        if key:
            hdrs["Authorization"] = f"Bearer {key}"
    if "api.semanticscholar.org" in url:
        key = _setting("semanticscholar_api_key")
        if key:
            hdrs["x-api-key"] = key
    hdrs.update(headers or {})
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
        except requests.exceptions.RequestException:
            return None
        if resp.status_code not in (429, 503) or attempt == 2:
            return resp
        if "Insufficient budget" in resp.text[:400]:
            return resp  # Tagesbudget von OpenAlex erschöpft: Warten hilft erst nach Mitternacht UTC
        try:
            wait = float(resp.headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            wait = 1.5 * (attempt + 1)
        time.sleep(max(0.5, min(wait, 6.0)))
    return None


def _setting(name: str) -> str:
    try:
        from . import config
        return str(config.load_settings().get(name) or "").strip()
    except Exception:
        return ""


def _openalex_key() -> str:
    """Eigener API-Key für OpenAlex aus den Einstellungen (ohne Key gilt ein geteiltes Tagesbudget je Netz)."""
    try:
        from . import config
        return str(config.load_settings().get("openalex_api_key") or "").strip()
    except Exception:
        return ""


def _json(resp: Optional[requests.Response]) -> Optional[Dict]:
    if resp is None or resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------
# OpenAlex-Werk (Metadaten + Locations), pro DOI zwischengespeichert
# --------------------------------------------------------------------------
_work_cache: Dict[str, Optional[Dict]] = {}
_work_lock = threading.Lock()


def openalex_work(doi: str, email: str = "") -> Optional[Dict]:
    with _work_lock:
        if doi in _work_cache:
            return _work_cache[doi]
    params = {"mailto": email} if email and "@" in email else None
    resp = _api_get(f"https://api.openalex.org/works/doi:{doi}", params=params)
    work = _json(resp)
    if work is None and (resp is None or resp.status_code != 404):
        return None  # OpenAlex hat nicht geantwortet (Budget, Netz): nicht merken, später erneut fragen
    with _work_lock:
        if len(_work_cache) > 500:
            _work_cache.clear()
        _work_cache[doi] = work
    return work


def _surname(display_name: str) -> str:
    """Nachname als letztes Wort eines Anzeigenamens (Heuristik)."""
    parts = [p for p in re.split(r"\s+", (display_name or "").strip()) if p]
    return parts[-1] if parts else ""


def metadata_from_work(work: Dict) -> Dict:
    authorships = work.get("authorships") or []
    names = [((a.get("author") or {}).get("display_name") or "") for a in authorships]
    names = [n for n in names if n]
    corresponding = next(
        ((a.get("author") or {}).get("display_name") for a in authorships if a.get("is_corresponding")),
        None,
    )
    year = work.get("publication_year")
    return {
        "title": work.get("title") or work.get("display_name") or "",
        "authors": [_surname(n) for n in names],
        "author": _surname(names[0]) if names else "",
        "year": str(year) if year else "",
        "contact": corresponding or (names[0] if names else ""),
    }


def resolve_metadata(doi: str, email: str = "") -> Optional[Dict]:
    """Titel, Autoren, Jahr, Kontaktperson: OpenAlex zuerst, Crossref als Fallback."""
    work = openalex_work(doi, email)
    if work and (work.get("title") or work.get("display_name")):
        return metadata_from_work(work)
    return crossref.lookup(doi)


def copy_request(doi: str, email: str = "") -> Optional[Dict]:
    """Bitte um eine Kopie, wenn es keine freie Fassung gibt: Betreff und Text (Englisch und Deutsch) sowie Wege
    zur Kontaktperson (Verlagsseite, ORCID). Viele Autor:innen schicken ihre Arbeit auf Nachfrage; die E-Mail-Adresse
    steht in offenen Daten selten, meist aber auf der Verlagsseite."""
    work = openalex_work(doi, email) or {}
    meta = metadata_from_work(work) if work else (crossref.lookup(doi) or {})
    title = str(meta.get("title") or "").strip()
    if not title:
        return None
    authorships = work.get("authorships") or []
    corresponding = [a for a in authorships if a.get("is_corresponding")]
    people = corresponding or (authorships[:1] + (authorships[-1:] if len(authorships) > 1 else []))
    contacts = []
    for a in people:
        author = a.get("author") or {}
        name = str(author.get("display_name") or "").strip()
        if name.isupper():  # manche Namen stehen bei OpenAlex in Großbuchstaben
            name = name.title()
        if name and all(c["name"] != name for c in contacts):
            contacts.append({"name": name, "orcid": author.get("orcid") or ""})
    name = contacts[0]["name"] if contacts else str(meta.get("contact") or "").strip()
    venue = str(((work.get("primary_location") or {}).get("source") or {}).get("display_name") or "")
    year = str(meta.get("year") or "")
    where = ", ".join(x for x in (venue, year) if x)
    ref = f"\u201c{title}\u201d" + (f" ({where})" if where else "") + f", https://doi.org/{doi}"
    bodies = {
        "en": (f"Dear {name or 'author'},\n\nI am a student and would like to read your work {ref}. "
               "I have no access to it through a library. Would you be able to send me a copy for my own study?\n\n"
               "Thank you very much,\n"),
        "de": (f"Guten Tag{(' ' + name) if name else ''},\n\nich studiere und würde gern Ihre Arbeit {ref} lesen. "
               "Über eine Bibliothek habe ich keinen Zugang. Könnten Sie mir für mein eigenes Studium eine Kopie schicken?\n\n"
               "Vielen Dank und freundliche Grüße\n"),
    }
    subjects = {"en": f"Request for a copy: {title}", "de": f"Bitte um eine Kopie: {title}"}
    return {"doi": doi, "title": title, "contacts": contacts, "landing_url": f"https://doi.org/{doi}",
            "subject": subjects, "body": bodies}


# --------------------------------------------------------------------------
# Quellen: jede liefert (Kandidaten, Landingpages)
# --------------------------------------------------------------------------
Candidate = Dict[str, str]
ProviderResult = Tuple[List[Candidate], List[str]]


def _cand(url: Optional[str], source: str, version: str = "unknown",
          headers: Optional[Dict[str, str]] = None) -> Optional[Candidate]:
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    c = {"url": url, "source": source, "version": version}
    if headers:
        c["headers"] = headers
    return c


def _arxiv(doi: str, ctx: Dict) -> ProviderResult:
    m = re.match(r"^10\.48550/arxiv\.(.+)$", doi, re.IGNORECASE)
    if not m:
        return [], []
    arxiv_id = m.group(1)
    c = _cand(f"https://arxiv.org/pdf/{arxiv_id}", "arxiv", "submitted")
    return ([c] if c else []), [f"https://arxiv.org/abs/{arxiv_id}"]


def _openalex(doi: str, ctx: Dict) -> ProviderResult:
    work = openalex_work(doi, ctx.get("email", ""))
    if not work:
        return [], []
    cands: List[Candidate] = []
    pages: List[str] = []
    for loc in work.get("locations") or []:
        if not loc.get("is_oa"):
            continue
        host = ((loc.get("source") or {}).get("display_name") or "").lower()
        source = "arxiv" if "arxiv" in host else "openalex"
        c = _cand(loc.get("pdf_url"), source, _norm_version(loc.get("version")))
        if c:
            cands.append(c)
        elif loc.get("landing_page_url"):
            # Kein direkter PDF-Link, aber eine OA-Landingpage → als Fallback
            # versuchen (dort steckt oft ein citation_pdf_url).
            cands.append({"url": loc["landing_page_url"], "source": source,
                          "version": _norm_version(loc.get("version")), "landing": True})
        if loc.get("landing_page_url"):
            pages.append(loc["landing_page_url"])
    return cands, pages


def _unpaywall(doi: str, ctx: Dict) -> ProviderResult:
    email = ctx.get("email") or DEFAULT_EMAIL
    j = _json(_api_get(f"https://api.unpaywall.org/v2/{doi}", params={"email": email}))
    if not j or not j.get("is_oa"):
        return [], []
    cands: List[Candidate] = []
    pages: List[str] = []
    for loc in j.get("oa_locations") or []:
        c = _cand(loc.get("url_for_pdf"), "unpaywall", _norm_version(loc.get("version")))
        if c:
            cands.append(c)
        elif loc.get("url_for_landing_page"):
            cands.append({"url": loc["url_for_landing_page"], "source": "unpaywall",
                          "version": _norm_version(loc.get("version")), "landing": True})
        if loc.get("url_for_landing_page"):
            pages.append(loc["url_for_landing_page"])
    return cands, pages


def _europepmc(doi: str, ctx: Dict) -> ProviderResult:
    j = _json(_api_get(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        params={"query": f'DOI:"{doi}"', "resultType": "core", "format": "json", "pageSize": 1},
    ))
    results = ((j or {}).get("resultList") or {}).get("result") or []
    if not results or (results[0].get("doi") or "").lower() != doi.lower():
        return [], []
    rec = results[0]
    version = "accepted" if rec.get("authMan") == "Y" else "published"
    cands: List[Candidate] = []
    pages: List[str] = []
    for ft in (rec.get("fullTextUrlList") or {}).get("fullTextUrl") or []:
        if ft.get("availabilityCode") not in ("OA", "F"):
            continue
        if ft.get("documentStyle") == "pdf":
            c = _cand(ft.get("url"), "europepmc", version if ft.get("site") == "Europe_PMC" else "unknown")
            if c:
                cands.append(c)
        elif ft.get("url"):
            pages.append(ft["url"])
    return cands, pages


def _semantic_scholar(doi: str, ctx: Dict) -> ProviderResult:
    j = _json(_api_get(
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
        params={"fields": "openAccessPdf"},
    ))
    pdf = ((j or {}).get("openAccessPdf") or {}).get("url")
    c = _cand(pdf, "semantic_scholar", "unknown")
    return ([c] if c else []), []


def _core(doi: str, ctx: Dict) -> ProviderResult:
    params = {"q": f'doi:"{doi}"', "limit": 3}
    key = (ctx.get("core_api_key") or "").strip()
    headers = {"Authorization": f"Bearer {key}"} if key else None
    j = _json(_api_get("https://api.core.ac.uk/v3/search/works", params=params, headers=headers))
    results = (j or {}).get("results") or []
    cands: List[Candidate] = []
    pages: List[str] = []
    for rec in results:
        if (rec.get("doi") or "").lower() != doi.lower():
            continue  # CORE kann fremde Treffer liefern → DOI muss exakt passen
        c = _cand(rec.get("downloadUrl"), "core", "unknown")
        if c:
            cands.append(c)
        if rec.get("links"):
            pages.extend(x.get("url") for x in rec["links"] if isinstance(x, dict) and x.get("url"))
    return cands, pages


def _elsevier(doi: str, ctx: Dict) -> ProviderResult:
    """Elsevier/ScienceDirect-PDF über die offizielle API (institutioneller Key)."""
    key = (ctx.get("elsevier_api_key") or "").strip()
    if not key or not doi.lower().startswith("10.1016/"):
        return [], []
    url = f"https://api.elsevier.com/content/article/doi/{doi}?httpAccept=application/pdf"
    c = _cand(url, "elsevier", "published",
              headers={"X-ELS-APIKey": key, "Accept": "application/pdf"})
    return ([c] if c else []), [f"https://doi.org/{doi}"]


def _is_open_license(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in ("creativecommons", "cc-by", "cc0", "open-access", "open access", "public domain"))


def _crossref(doi: str, ctx: Dict) -> ProviderResult:
    j = _json(_api_get(f"https://api.crossref.org/works/{doi}"))
    if not j:
        return [], []
    msg = j.get("message") or {}
    licenses = msg.get("license") or []
    if not any(_is_open_license(l.get("URL", "")) for l in licenses):
        return [], []
    cands: List[Candidate] = []
    for link in msg.get("link") or []:
        url = link.get("URL") or ""
        ct = (link.get("content-type") or "").lower()
        if ct == "application/pdf" or url.lower().endswith(".pdf"):
            c = _cand(url, "crossref", "published")
            if c:
                cands.append(c)
                break
    return cands, []


_PROVIDERS = {
    "arxiv": _arxiv,
    "openalex": _openalex,
    "unpaywall": _unpaywall,
    "europepmc": _europepmc,
    "semantic_scholar": _semantic_scholar,
    "core": _core,
    "elsevier": _elsevier,
    "crossref": _crossref,
}


def resolve_candidates(doi: str, providers: Optional[List[str]] = None, email: str = "",
                       prefer_published: bool = True, core_api_key: str = "",
                       elsevier_api_key: str = "",
                       on_event: Optional[Callable[[Dict], None]] = None) -> Tuple[List[Candidate], List[str]]:
    """Fragt die OA-Quellen der Reihe nach ab und sammelt alle PDF-Kandidaten.

    ``on_event`` wird nach jeder Quelle aufgerufen (Live-Transparenz).
    Rückgabe: ``(candidates, landing_pages)``. Kandidaten sind nach Fassung
    sortiert (Verlagsversion zuerst), Duplikate entfernt.
    """
    names = providers or PROVIDERS
    ctx = {"email": email, "core_api_key": core_api_key, "elsevier_api_key": elsevier_api_key}
    candidates: List[Candidate] = []
    pages: List[str] = []
    seen: set = set()

    def emit(phase: str, source: str, **kw) -> None:
        if on_event is None:
            return
        try:
            on_event({"phase": phase, "identifier": doi, "source": source, **kw})
        except Exception:
            pass

    for name in names:
        fn = _PROVIDERS.get(name)
        if fn is None:
            continue
        emit("provider_start", name)
        try:
            cands, pg = fn(doi, ctx)
        except Exception:
            cands, pg = [], []
        emit("provider_done", name, candidates=len(cands))
        for c in cands:
            if c["url"] not in seen:
                seen.add(c["url"])
                candidates.append(c)
        for p in pg:
            if p and p not in pages:
                pages.append(p)

    if prefer_published:
        # Direkte PDFs zuerst, Landingpage-Fallbacks danach; innerhalb dessen
        # die Verlagsversion vor Manuskript/Preprint.
        candidates.sort(key=lambda c: (1 if c.get("landing") else 0,
                                       VERSION_RANK.get(c["version"], 3)))
    return candidates, pages


_PDF_LINK_RE = re.compile(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', re.IGNORECASE)


def _extract_pdf_url(html: str, base_url: str) -> Optional[str]:
    """Sucht in einer Landingpage nach einem PDF-Link (citation_pdf_url zuerst)."""
    m = re.search(
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
            html, re.IGNORECASE,
        )
    if m:
        return urljoin(base_url, m.group(1))
    m = _PDF_LINK_RE.search(html)
    if m:
        return urljoin(base_url, m.group(1))
    return None


def download_pdf(url: str, headers: Optional[Dict[str, str]] = None,
                 timeout: float = 60.0) -> Optional[bytes]:
    """Lädt ein OA-PDF und validiert es anhand der Magic Bytes (``%PDF-``).

    Ist die Antwort HTML (Landingpage), wird dort ein PDF-Link gesucht
    (``citation_pdf_url`` oder ``.pdf``) und dieser nachgeladen.
    """
    hdrs = {"User-Agent": PDF_USER_AGENT, "Accept": "application/pdf,*/*"}
    hdrs.update(headers or {})
    try:
        resp = requests.get(url, headers=hdrs, timeout=timeout, allow_redirects=True)
    except requests.exceptions.RequestException:
        return None
    if resp.status_code != 200:
        return None
    content = resp.content
    if content.startswith(b"%PDF-"):
        return content
    # Kein PDF → ggf. Landingpage: PDF-Link extrahieren und nachladen.
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "html" in ctype or content[:1] in (b"<",):
        pdf_url = _extract_pdf_url(resp.text, resp.url)
        if pdf_url and pdf_url != url:
            try:
                r2 = requests.get(pdf_url, headers=hdrs, timeout=timeout, allow_redirects=True)
                if r2.status_code == 200 and r2.content.startswith(b"%PDF-"):
                    return r2.content
            except requests.exceptions.RequestException:
                pass
    return None
