"""Literaturverzeichnisse und Volltexte.

* Bibliografie über die DOI-Inhaltsaushandlung (doi.org liefert BibTeX, RIS
  oder fertig formatierte Zitate in jedem CSL-Stil, für Crossref- wie für
  DataCite-DOIs, also auch arXiv).
* Volltext aus heruntergeladenen PDFs (pypdf), z. B. für ein LLM über MCP.
"""

from __future__ import annotations

import html
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

from . import __version__, config, library
from .i18n import tr

USER_AGENT = f"Benthic/{__version__} (reference checker; https://benthic.isoglu.com)"

FORMATS = {
    "bibtex": "application/x-bibtex",
    "ris": "application/x-research-info-systems",
}
# Kurznamen für gängige CSL-Stile (jeder andere CSL-Name funktioniert auch). Jeder Name muss in der
# Stilliste von doi.org stehen (citation.doi.org/styles): "vancouver" fehlt dort, Vancouver ist der
# NLM-Stil "nlm-citation-sequence". tests/test_biblio.py hält die Liste fest.
STYLES = {
    "apa": "apa",
    "harvard": "harvard-cite-them-right",
    "chicago": "chicago-author-date",
    "mla": "modern-language-association",
    "ieee": "ieee",
    "vancouver": "nlm-citation-sequence",
    "din": "din-1505-2",
}
NUMBERED = {"ieee": "[{n}] ", "nlm-citation-sequence": "{n}. "}  # Stile mit nummerierter Liste

# Was der Formatierer von doi.org bei Zeitschriftenartikeln ohne Herausgeber:in übrig lässt
# (Stand Oktober 2026, z. B. für 10.1038/nature14539): leere Herausgeberfelder und die Nummer
# einer Liste mit nur diesem einen Eintrag.
_STRAY = [
    (re.compile(r"^\s*(?:\[\d+\]|\d+\.(?=\S))\s*"), ""),                      # "[1]Y. LeCun", "1.LeCun Y"
    (re.compile(r"\.\s+(?:Edited by|Herausgegeben von)\s*,"), ","),                 # Harvard
    (re.compile(r",\s+(?:edited by|herausgegeben von)\s*,"), ","),                   # MLA
    (re.compile(r"\s+,\s*(?:editors?|Herausgeber(?:innen)?)\.(?=\s|$)"), ""),       # NLM/Vancouver
    (re.compile(r"\bIn: \((?:eds?\.|Hrsg\.)\)\s+"), "In: "),                         # DIN 1505
]

_cache: Dict[Tuple[str, str], Optional[str]] = {}
_lock = threading.Lock()


def _fetch(doi: str, accept: str) -> Optional[str]:
    key = (doi.lower(), accept)
    with _lock:
        if key in _cache:
            return _cache[key]
    text: Optional[str] = None
    for attempt in range(2):  # ein zweiter Versuch fängt kurze Aussetzer ab
        try:
            r = requests.get(f"https://doi.org/{quote(doi, safe='/')}", timeout=20, allow_redirects=True,
                             headers={"Accept": accept, "User-Agent": USER_AGENT})
            if r.status_code == 200 and r.text.strip() and "<html" not in r.text[:200].lower():
                r.encoding = "utf-8"
                text = r.text.strip()
                break
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.8)
    if text:  # nur Treffer merken, damit ein Aussetzer nicht hängen bleibt
        with _lock:
            _cache[key] = text
    return text


def _plain(entry: str) -> str:
    """Formatiertes Zitat ohne HTML (Kursivschrift, Entities), ohne leere Felder und Listennummer."""
    text = html.unescape(re.sub(r"<[^>]+>", "", entry)).strip()
    for pattern, repl in _STRAY:
        text = pattern.sub(repl, text)
    return text.strip()


def export(dois: List[str], fmt: str = "bibtex", style: str = "apa", locale: str = "en-US") -> Tuple[str, List[str]]:
    """Liefert (Text, DOIs ohne Ergebnis). ``fmt``: bibtex, ris oder text (formatiert)."""
    dois = [d for d in dict.fromkeys(dois) if d]
    csl = STYLES.get(style, style)
    if csl == "din-1505-2":
        locale = "de-DE"  # eine deutsche Norm: "Bd.", "S.", "Hrsg." auch in einer englischen Oberfläche
    if fmt in FORMATS:
        accept = FORMATS[fmt]
    else:
        accept = f"text/x-bibliography; style={csl}; locale={locale}"
    with ThreadPoolExecutor(max_workers=6) as ex:
        entries = list(ex.map(lambda d: _fetch(d, accept), dois))
    missing = [d for d, e in zip(dois, entries) if not e]
    found = [e for e in entries if e]
    if fmt in FORMATS:
        return "\n\n".join(found) + ("\n" if found else ""), missing
    lines = sorted((_plain(e) for e in found), key=lambda s: s.lower())  # Literaturverzeichnis: alphabetisch
    if csl in NUMBERED and len(lines) > 1:  # eine einzelne Angabe bleibt ohne Nummer
        lines = [NUMBERED[csl].format(n=i) + line for i, line in enumerate(lines, start=1)]
    return "\n\n".join(lines) + ("\n" if lines else ""), missing


# --------------------------------------------------------------------------
# Volltext
# --------------------------------------------------------------------------
def pdf_path(doi: str) -> Optional[Path]:
    """Ort der heruntergeladenen PDF zu einer DOI (Zielordner oder App-Ordner)."""
    entry = library.get(doi)
    name = (entry or {}).get("filename")
    if not name:
        return None
    settings = config.load_settings()
    folders = [config.resolve_download_dir(settings)[0], config.app_download_dir()]
    for folder in folders:
        p = Path(folder) / name
        if p.is_file():
            return p
    return None


def fulltext(doi: str, start: int = 0, max_chars: int = 30000) -> Dict:
    """Text einer heruntergeladenen PDF, seitenweise markiert und gekürzt."""
    p = pdf_path(doi)
    if not p:
        return {"doi": doi, "available": False,
                "error": "No downloaded PDF for this DOI. Fetch it first (fetch_papers)."}
    from pypdf import PdfReader  # erst hier: nur nötig, wenn jemand Volltext will

    try:
        reader = PdfReader(str(p))
        parts = []
        for i, page in enumerate(reader.pages, start=1):
            parts.append(f"[Page {i}]\n{(page.extract_text() or '').strip()}")
        text = "\n\n".join(parts)
    except Exception as exc:  # beschädigte oder verschlüsselte PDF
        return {"doi": doi, "available": False, "error": tr("PDF nicht lesbar: {exc}", exc=exc)}
    chunk = text[max(0, start):max(0, start) + max(1000, min(max_chars, 120000))]
    return {
        "doi": doi, "available": True, "file": p.name, "pages": len(reader.pages),
        "total_chars": len(text), "start": start, "end": start + len(chunk),
        "truncated": start + len(chunk) < len(text), "text": chunk,
    }
