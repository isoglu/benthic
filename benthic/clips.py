"""Fundstücke: was Grimpo, der Begleiter auf dem Schreibtisch, festhält. Links, Textstellen und Bildschirmfotos
(mit dem Text, den macOS darin erkennt), je mit der DOI, wenn eine darin steht oder die verlinkte Seite eine nennt.

Alles bleibt im Datenordner (``clips.json``, Bilder unter ``clips/``). Wer will, prüft die DOIs danach in Benthic
oder übernimmt sie ins aktive Projekt.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from . import config, oa

MAX = 500
_lock = threading.Lock()
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>()\[\]{},;]+", re.IGNORECASE)
_META_DOI = re.compile(r'<meta[^>]+(?:name|property)=["\'](?:citation_doi|dc\.identifier|prism\.doi|bepress_citation_doi)["\'][^>]*>', re.I)
_CONTENT = re.compile(r'content=["\']([^"\']+)["\']', re.I)


def folder() -> Path:
    d = config.data_dir() / "clips"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path() -> Path:
    return config.data_dir() / "clips.json"


def load() -> List[Dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items: List[Dict]) -> None:
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(items[:MAX], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def dois_in(text: str) -> List[str]:
    """DOIs in einem Text, ohne Satzzeichen am Ende, ohne Doppelte."""
    out: List[str] = []
    for m in _DOI.finditer(text or ""):
        doi = m.group(0).rstrip(".:")
        if doi.lower() not in (d.lower() for d in out):
            out.append(doi)
    return out[:30]


def doi_of_page(url: str) -> Optional[str]:
    """Die DOI, die eine Artikelseite in ihren Metadaten nennt (citation_doi und Verwandte)."""
    import requests
    try:
        resp = requests.get(url, timeout=10.0, stream=True, allow_redirects=True,
                            headers={"User-Agent": oa.PDF_USER_AGENT, "Accept": "text/html,*/*"})
        head = b""
        for chunk in resp.iter_content(16384):
            head += chunk
            if len(head) > 300_000 or b"</head>" in head.lower():
                break
        resp.close()
    except requests.exceptions.RequestException:
        return None
    page = head.decode("utf-8", "replace")
    for m in _META_DOI.finditer(page):
        c = _CONTENT.search(m.group(0))
        found = dois_in(c.group(1)) if c else []
        if found:
            return found[0]
    return None


def add(kind: str, url: str = "", title: str = "", text: str = "", path: str = "", project: Optional[str] = None) -> Dict:
    """Ein Fundstück anlegen. Bilder müssen im Ordner ``clips/`` liegen (die App legt sie dort ab)."""
    if kind not in ("link", "text", "image"):
        raise ValueError("kind")
    if kind == "image":
        p = Path(path).expanduser()
        if not p.is_file() or folder().resolve() not in p.resolve().parents:
            raise ValueError("path")
        path = str(p)
    url, title, text = url.strip()[:2000], title.strip()[:500], text.strip()[:20000]
    if kind == "link" and not re.match(r"https?://", url):
        raise ValueError("url")
    dois = dois_in(" ".join((url, text)))
    if kind == "link" and not dois:
        found = doi_of_page(url)
        dois = [found] if found else []
    item = {"id": uuid.uuid4().hex[:10], "kind": kind, "url": url, "title": title, "text": text, "path": path,
            "dois": dois, "project": project, "created_at": time.time()}
    with _lock:
        items = load()
        items.insert(0, item)
        _save(items)
    return item


def remove(clip_id: str) -> bool:
    with _lock:
        items = load()
        keep = [i for i in items if i.get("id") != clip_id]
        if len(keep) == len(items):
            return False
        gone = next(i for i in items if i.get("id") == clip_id)
        if gone.get("kind") == "image" and gone.get("path"):
            try:
                Path(gone["path"]).unlink()
            except OSError:
                pass
        _save(keep)
        return True
