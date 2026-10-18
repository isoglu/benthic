"""Sprache der Oberfläche: Englisch (Standard) oder Deutsch.

Der Quelltext ist deutsch geschrieben. ``locales/en.json`` ordnet jedem deutschen Text seine
englische Fassung zu, wie ein gettext-Katalog mit der deutschen Fassung als Schlüssel. Derselbe
Katalog übersetzt die Meldungen des Backends (``tr``), die Weboberfläche (``T`` in app.js, über
``/i18n.js``) und index.html beim Ausliefern (``translate_html``). Fehlt ein Eintrag, erscheint
der deutsche Text; tests/test_i18n.py verhindert das.
"""

from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from . import config

LANGUAGES = {"en": "English", "de": "Deutsch"}
DEFAULT = "en"
LOCALES = Path(__file__).resolve().parent / "locales"

_state = {"stamp": object(), "lang": DEFAULT}


@lru_cache(maxsize=None)
def catalog(lang: str = "en") -> Dict[str, str]:
    """Deutsch → Zielsprache. Für Deutsch leer: der Quelltext ist schon deutsch."""
    if lang not in LANGUAGES or lang == "de":
        return {}
    try:
        data = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str) and v}


def normalize(lang: Optional[str]) -> str:
    lang = str(lang or "").strip().lower()[:2]
    return lang if lang in LANGUAGES else DEFAULT


def current() -> str:
    """Eingestellte Sprache. Liest die Einstellungen nur neu, wenn sich die Datei geändert hat."""
    path = config.settings_path()
    try:
        st = path.stat()
        stamp = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = (str(path), None)
    if stamp != _state["stamp"]:
        _state["lang"] = normalize(config.load_settings().get("language"))
        _state["stamp"] = stamp
    return _state["lang"]


def tr(text: str, lang: Optional[str] = None, **values) -> str:
    """Übersetzt einen deutschen Text; Platzhalter wie {n} werden danach gefüllt."""
    lang = normalize(lang) if lang else current()
    out = catalog(lang).get(text, text) if lang != "de" else text
    return out.format(**values) if values else out


def locale_tag(lang: Optional[str] = None) -> str:
    """Sprachkennung für Zahlen und Datumsangaben in der Oberfläche."""
    return {"de": "de-DE", "en": "en-GB"}[normalize(lang) if lang else current()]


# --------------------------------------------------------------------------
# index.html beim Ausliefern übersetzen
# --------------------------------------------------------------------------
_TEXT = re.compile(r">([^<]+)<")
_ATTR = re.compile(r'(\s(?:title|placeholder|aria-label|alt|data-tip)=")([^"]*)(")')
_SKIP = re.compile(r"<!--.*?-->|<(?P<tag>script|style)\b[^>]*>.*?</(?P=tag)>", re.S)


def _swap(raw: str, table: Dict[str, str], attr: bool) -> str:
    text = html.unescape(raw)
    core = text.strip()
    if not core or core not in table:
        return raw
    lead, trail = text[: len(text) - len(text.lstrip())], text[len(text.rstrip()):]
    out = html.escape(table[core], quote=attr)
    if attr:
        out = out.replace("\n", "&#10;")
    return lead + out + trail


def translate_html(page: str, lang: Optional[str] = None) -> str:
    """Ersetzt Textknoten und lesbare Attribute, die im Katalog stehen; Skripte bleiben unberührt."""
    lang = normalize(lang) if lang else current()
    page = re.sub(r'<html lang="[a-z]{2}"', f'<html lang="{lang}"', page, count=1)
    table = catalog(lang)
    if not table:
        return page
    parts, last = [], 0
    for m in _SKIP.finditer(page):
        parts.append((page[last:m.start()], True))
        parts.append((m.group(0), False))
        last = m.end()
    parts.append((page[last:], True))
    out = []
    for chunk, translate in parts:
        if translate:
            chunk = _TEXT.sub(lambda m: ">" + _swap(m.group(1), table, False) + "<", chunk)
            chunk = _ATTR.sub(lambda m: m.group(1) + _swap(m.group(2), table, True) + m.group(3), chunk)
        out.append(chunk)
    return "".join(out)


def script(lang: Optional[str] = None) -> str:
    """JavaScript für die Oberfläche: Sprache, Zahlenformat und Katalog vor app.js."""
    lang = normalize(lang) if lang else current()
    payload = json.dumps(catalog(lang), ensure_ascii=False, separators=(",", ":"))
    return (f"window.PD_LANG={json.dumps(lang)};window.PD_LOCALE={json.dumps(locale_tag(lang))};"
            f"window.PD_I18N={payload};\n")


def join_list(items, lang: Optional[str] = None) -> str:
    """„Titel, Jahr und Autor:innen“ bzw. „title, year and authors“."""
    items = [str(i) for i in items if str(i)]
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + tr(" und ", lang) + items[-1]
