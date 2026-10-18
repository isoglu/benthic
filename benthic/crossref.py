"""DOI-Metadaten-Abfrage über die offene Crossref-API (für lesbare Dateinamen)."""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, Optional

import requests

from . import __version__

CROSSREF_API = "https://api.crossref.org/works/{doi}"

# Höflicher User-Agent; Crossref verlangt einen identifizierbaren UA.
USER_AGENT = f"Benthic/{__version__} (reference checker; https://benthic.isoglu.com)"


def lookup(doi: str, timeout: float = 12.0) -> Optional[Dict[str, str]]:
    """Holt Titel, Erstautor und Jahr zu einer DOI.

    Liefert ein Dict mit den Schlüsseln ``title``, ``author`` und ``year``
    oder ``None``, wenn nichts gefunden wurde.
    """
    doi = doi.strip()
    if not doi:
        return None

    url = CROSSREF_API.format(doi=requests.utils.quote(doi, safe=""))
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
    except requests.exceptions.RequestException:
        return None

    if resp.status_code != 200:
        return None

    try:
        msg = resp.json().get("message", {})
    except ValueError:
        return None

    title = (msg.get("title") or [""])[0] or ""
    authors = msg.get("author") or []
    first_author = ""
    if authors:
        first_author = authors[0].get("family") or authors[0].get("given") or ""

    year = None
    issued = msg.get("issued", {}).get("date-parts", [[None]])
    if issued and issued[0]:
        year = issued[0][0]
    if not year:
        year = None

    return {
        "title": title,
        "author": first_author,
        "year": str(year) if year else "",
    }


def slugify(text: str, max_len: int = 80) -> str:
    """Erzeugt einen dateisystem-tauglichen Slug aus beliebigem Text."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    text = re.sub(r"_+", "_", text)
    return text[:max_len].rstrip("_")


def build_filename(doi: str, metadata: Optional[Dict[str, str]]) -> str:
    """Baut einen lesbaren Dateinamen aus Crossref-Metadaten bzw. DOI."""
    if metadata:
        parts = [p for p in [metadata.get("author", ""), metadata.get("year", ""), slugify(metadata.get("title", ""))] if p]
        if parts:
            stem = "_".join(parts)
            if stem:
                return f"{stem}.pdf"

    # Fallback: DOI als Basis
    return f"{slugify(doi)}.pdf"
