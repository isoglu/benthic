"""Hausregeln einer Hochschule: eine JSON-Datei, die Benthic einliest.

Sie setzt nur, was eine Hochschule vorgeben kann: Name, Proxy-Link, Kontaktadresse für den höflichen Pool,
OpenAlex-Key, den verlangten Zitierstil, eigene Rankinglisten und Kursvorgaben (Mindestzahl der Quellen, Quellenarten,
die der Kurs nicht vorsieht, ein Satz der Lehrenden); die Gesundheit der Literaturliste meldet dann, was davon abweicht. Update-Adresse, Ordner, Bibliotheken und
Zusatzwege bleiben bei der Person, die Benthic nutzt. Jede:r kann die Datei von Hand laden; ein Dienst der
Hochschule verteilt später nur dieselbe Datei.

    {"benthic": "house-rules", "version": 1, "name": "Universität Musterstadt",
     "settings": {"institution": "...", "proxy_prefix": "https://...", "unpaywall_email": "...",
                  "openalex_api_key": "..."},
     "style": "apa",
     "rankings": [{"name": "VHB-JOURQUAL 4", "csv": "Journal Title,ISSN,Rating\\nNature,0028-0836,A+\\n"}],
     "course": {"min_sources": 15, "excluded": ["web"], "note": "Mindestens 15 Fachquellen, keine Blogs."}}
"""

from __future__ import annotations

import json
import re
from typing import Dict

from . import config, journals, style
from .i18n import tr

KIND = "house-rules"
VERSION = 1
SETTINGS = ("institution", "proxy_prefix", "unpaywall_email", "openalex_api_key")
MAX_RANKINGS = 20
EXCLUDABLE = tuple(k for k in journals.MIX_KINDS if k != "journal")


def _course(given) -> Dict:
    """Kursvorgaben prüfen: Mindestzahl (1 bis 500), ausgeschlossene Quellenarten, ein kurzer Satz."""
    bad = ValueError(tr("Die Kursvorgaben in der Datei sind unvollständig."))
    if not isinstance(given, dict):
        raise bad
    out: Dict = {}
    if given.get("min_sources") is not None:
        try:
            out["min_sources"] = int(given["min_sources"])
        except (TypeError, ValueError):
            raise bad from None
        if not 1 <= out["min_sources"] <= 500:
            raise bad
    excluded = given.get("excluded") or []
    if not isinstance(excluded, list) or any(k not in EXCLUDABLE for k in excluded):
        raise bad
    if excluded:
        out["excluded"] = sorted(set(excluded))
    if str(given.get("note") or "").strip():
        out["note"] = str(given["note"]).strip()[:300]
    return out


def apply(data: bytes) -> Dict:
    """Prüft eine Hausregel-Datei vollständig, übernimmt sie dann und sagt, was sie gesetzt hat (ValueError, wenn ungültig)."""
    try:
        rules = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(tr("Die Datei ist kein gültiges JSON."))
    if not isinstance(rules, dict) or rules.get("benthic") != KIND or not isinstance(rules.get("version"), int):
        raise ValueError(tr("Das ist keine Hausregel-Datei für Benthic."))
    if rules["version"] > VERSION:
        raise ValueError(tr("Diese Hausregeln brauchen eine neuere Version von Benthic."))

    given = rules.get("settings") or {}
    if not isinstance(given, dict):
        raise ValueError(tr("Das ist keine Hausregel-Datei für Benthic."))
    values = {k: str(given[k]).strip() for k in SETTINGS if given.get(k) is not None}
    email, proxy = values.get("unpaywall_email"), values.get("proxy_prefix")
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValueError(tr("Die E-Mail-Adresse sieht unvollständig aus."))
    if proxy and not proxy.startswith(("http://", "https://")):
        raise ValueError(tr("Der Proxy-Link muss mit https:// beginnen."))
    wanted = str(rules.get("style") or "").strip().lower()
    if wanted and wanted not in style.STYLES:
        raise ValueError(tr("Unbekannter Zitierstil: {style}", style=wanted))

    course = _course(rules.get("course") or {})

    lists = rules.get("rankings") or []
    if not isinstance(lists, list) or len(lists) > MAX_RANKINGS:
        raise ValueError(tr("Die Rankinglisten in der Datei sind unvollständig."))
    tables = []
    for item in lists:
        name = str(item.get("name") or "").strip()[:80] if isinstance(item, dict) else ""
        if not name or not isinstance(item.get("csv"), str):
            raise ValueError(tr("Die Rankinglisten in der Datei sind unvollständig."))
        raw = item["csv"].encode("utf-8")
        try:  # erst alles prüfen, dann übernehmen
            journals.parse_ranking_rows(journals.read_table(name + ".csv", raw))
        except ValueError as exc:
            raise ValueError(f"{name}: {exc}") from None
        tables.append((name, raw))

    for name, raw in tables:
        journals.import_ranking(name, name + ".csv", raw)
    title = str(rules.get("name") or "").strip()[:120] or tr("Hausregeln")
    config.save_settings({**values, "house_rules": title, "house_style": wanted, "house_course": course})
    return {"name": title, "settings": sorted(values), "style": style.STYLES.get(wanted, ""),
            "rankings": [name for name, _ in tables], "course": course}


def remove() -> None:
    """Hausregeln abwählen: Name und Zitierstil fallen weg; übernommene Angaben und Listen bleiben änderbar."""
    config.save_settings({"house_rules": "", "house_style": "", "house_course": {}})
