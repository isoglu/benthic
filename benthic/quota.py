"""Kontingente und Transparenz: jede Anfrage an einen Dienst zählen, Grenzen einhalten, offen zeigen.

Ein Haken in ``requests`` (install) sieht jede ausgehende Anfrage der App. Je Dienst (OpenAlex, Crossref,
Unpaywall, Semantic Scholar, …) zählt Benthic die Anfragen des Tages, liest die Kontingent-Angaben aus den
Antworten (OpenAlex meldet das verbleibende Tagesbudget, Crossref seine Rate), hält den Abstand zwischen
Anfragen ein und pausiert einen Dienst nach "zu viele Anfragen". Ist das Tagesbudget von OpenAlex
aufgebraucht, fragt Benthic OpenAlex bis zum Zurücksetzen gar nicht mehr und prüft mit den anderen
Quellen weiter.

Das Protokoll der letzten Anfragen zeigt Dienst, Adresse und Antwort, ohne E-Mail-Adressen und Schlüssel.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from typing import Dict, List, Optional
from urllib.parse import parse_qsl, urlsplit

SERVICES = {
    "openalex": {"name": "OpenAlex", "hosts": ("api.openalex.org",), "interval": 0.11,
                 "limit": "Ohne eigenen Key ein kleines Tagesbudget je Netz (Zurücksetzen um Mitternacht UTC); mit Key ein eigenes."},
    "crossref": {"name": "Crossref", "hosts": ("api.crossref.org",), "interval": 0.12,
                 "limit": "Etwa 10 Anfragen je Sekunde mit E-Mail-Adresse (höflicher Pool), sonst 5; Listen 3 je Sekunde."},
    "unpaywall": {"name": "Unpaywall", "hosts": ("api.unpaywall.org",), "interval": 0.1,
                  "limit": "100.000 Anfragen am Tag je E-Mail-Adresse."},
    "semanticscholar": {"name": "Semantic Scholar", "hosts": ("api.semanticscholar.org",), "interval": 1.1,
                        "limit": "Ohne Key etwa eine Anfrage je Sekunde, mit allen geteilt; bei Andrang pausiert Benthic."},
    "core": {"name": "CORE", "hosts": ("api.core.ac.uk",), "interval": 1.0,
             "limit": "Mit kostenlosem Key ein Tageskontingent; ohne Key stark begrenzt."},
    "europepmc": {"name": "Europe PMC", "hosts": ("www.ebi.ac.uk",), "interval": 0.1, "limit": "Keine feste Grenze genannt."},
    "arxiv": {"name": "arXiv", "hosts": ("export.arxiv.org", "arxiv.org"), "interval": 1.0,
              "limit": "Höchstens eine Anfrage alle drei Sekunden empfohlen (Benthic: eine je Sekunde, nur bei Bedarf)."},
    "doi": {"name": "doi.org", "hosts": ("doi.org", "dx.doi.org"), "interval": 0.05, "limit": "Keine feste Grenze genannt."},
    "openlibrary": {"name": "Open Library", "hosts": ("openlibrary.org",), "interval": 0.5,
                    "limit": "Keine feste Grenze; Benthic fragt höchstens zwei Mal je Sekunde."},
    "datacite": {"name": "DataCite", "hosts": ("api.datacite.org",), "interval": 0.1,
                 "limit": "3.000 Anfragen je 5 Minuten."},
    "dblp": {"name": "DBLP", "hosts": ("dblp.org",), "interval": 0.5,
             "limit": "Keine feste Grenze; Benthic fragt höchstens zwei Mal je Sekunde."},
    "dnb": {"name": "Deutsche Nationalbibliothek", "hosts": ("services.dnb.de",), "interval": 0.3,
            "limit": "Öffentliche SRU-Schnittstelle ohne Schlüssel."},
    "lobid": {"name": "lobid (hbz)", "hosts": ("lobid.org",), "interval": 0.3,
              "limit": "Öffentliche Schnittstelle ohne Schlüssel."},
    "k10plus": {"name": "K10plus (GBV, SWB)", "hosts": ("sru.k10plus.de",), "interval": 0.3,
                "limit": "Öffentliche SRU-Schnittstelle ohne Schlüssel."},
    "elsevier": {"name": "Elsevier", "hosts": ("api.elsevier.com",), "interval": 0.2, "limit": "Nur mit eigenem Key."},
    "jufo": {"name": "JUFO", "hosts": ("jufo-rest.csc.fi",), "interval": 0.1, "limit": "Keine feste Grenze genannt."},
    "lists": {"name": "DOAJ- und Retraction-Watch-Listen", "hosts": ("docs.google.com", "drive.google.com",
              "doc-0g-0g-sheets.googleusercontent.com"), "interval": 0.0, "limit": "Höchstens einmal pro Woche geladen."},
    "zotero": {"name": "Zotero (auf diesem Mac)", "hosts": ("127.0.0.1:23119",), "interval": 0.0, "limit": "Lokal, ohne Grenze."},
    "updates": {"name": "Update-Prüfung", "hosts": ("benthic.isoglu.com",), "interval": 0.0, "limit": "Eine kleine Datei beim Start."},
}
LOCAL_PORTS = ("127.0.0.1", "localhost")
_SECRET_PARAMS = {"mailto", "email", "api_key", "apikey", "key", "token"}

_lock = threading.RLock()
_tl = threading.local()  # Probleme der laufenden Prüfung (je Thread), siehe track() und problems()
_state: Dict[str, Dict] = {}
_log: deque = deque(maxlen=250)
_last_call: Dict[str, float] = {}
_dirty = 0
_installed = False


def track() -> None:
    """Beginnt die Aufzeichnung für eine Prüfung (im aktuellen Thread)."""
    _tl.problems = []


def problems() -> List[Dict]:
    """Dienste, die während der laufenden Prüfung nicht (vollständig) geantwortet haben."""
    return list(getattr(_tl, "problems", []) or [])


def _problem(service: str, kind: str) -> None:
    items = getattr(_tl, "problems", None)
    if items is not None and not any(p["service"] == service for p in items):
        items.append({"service": service, "name": SERVICES.get(service, {}).get("name", service), "kind": kind})


class QuotaBlocked(Exception):
    """Ein Dienst ist pausiert (Tagesbudget aufgebraucht oder zu viele Anfragen)."""


def service_of(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    netloc = parts.netloc.lower()
    for key, svc in SERVICES.items():
        if host in svc["hosts"] or netloc in svc["hosts"] or any(host.endswith("." + h) for h in svc["hosts"]):
            return key
    if host in LOCAL_PORTS:
        return "local"
    return "web"  # Verlage, Repositorien (PDFs), geprüfte Links


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _path():
    from . import config
    return config.data_dir() / "usage.json"


def _load() -> None:
    global _state
    if _state:
        return
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        _state = data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, Exception):
        _state = {}


def _save(force: bool = False) -> None:
    global _dirty
    _dirty += 1
    if not force and _dirty < 15:
        return
    _dirty = 0
    try:
        p = _path()
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass


def _entry(service: str) -> Dict:
    _load()
    e = _state.setdefault(service, {"days": {}, "total": 0})
    return e


def _clean_url(url: str) -> str:
    """Adresse fürs Protokoll: lesbar, ohne E-Mail-Adressen und Schlüssel."""
    parts = urlsplit(url)
    query = "&".join(f"{k}={'…' if k.lower() in _SECRET_PARAMS else v}"
                     for k, v in parse_qsl(parts.query, keep_blank_values=True))
    return f"{parts.scheme}://{parts.netloc}{parts.path}" + (f"?{query}" if query else "")


def before(url: str) -> None:
    """Vor einer Anfrage: pausierte Dienste sperren, Abstand einhalten."""
    service = service_of(url)
    if service in ("web", "local", "zotero"):
        return
    with _lock:
        e = _entry(service)
        until = float(e.get("blocked_until") or 0)
        short = until - time.time() <= 15 and e.get("blocked_reason") != "budget"
    if until > time.time() and short:
        time.sleep(max(0.0, until - time.time()))  # kurze Pause: warten statt eine Quelle auslassen
    with _lock:
        until = float(e.get("blocked_until") or 0)
        if until > time.time():
            e.setdefault("days", {}).setdefault(_today(), {"requests": 0, "errors": 0, "limited": 0, "skipped": 0})
            e["days"][_today()]["skipped"] = e["days"][_today()].get("skipped", 0) + 1
            _problem(service, "paused")
            raise QuotaBlocked(f"{SERVICES[service]['name']} pausiert bis {time.strftime('%H:%M', time.localtime(until))}")
        wait = SERVICES[service]["interval"] - (time.time() - _last_call.get(service, 0))
        _last_call[service] = time.time() + max(0.0, wait)
    if wait > 0:
        time.sleep(min(wait, 3.0))


def after(url: str, status: Optional[int], headers: Optional[Dict] = None, elapsed: float = 0.0,
          body_start: str = "") -> None:
    """Nach einer Anfrage: zählen, Kontingent lesen, bei Grenzen pausieren, protokollieren."""
    service = service_of(url)
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    now = time.time()
    with _lock:
        if service not in ("web", "local"):
            e = _entry(service)
            day = e["days"].setdefault(_today(), {"requests": 0, "errors": 0, "limited": 0, "skipped": 0})
            day["requests"] += 1
            e["total"] = e.get("total", 0) + 1
            e["last_at"] = now
            e["last_status"] = status
            if status is None or status >= 500:
                day["errors"] += 1
                _problem(service, "error")
            if service == "openalex":
                for key, field in (("x-ratelimit-remaining", "remaining"), ("x-ratelimit-limit", "limit"),
                                   ("x-ratelimit-remaining-usd", "remaining_usd"), ("x-ratelimit-limit-usd", "limit_usd")):
                    if headers.get(key) is not None:
                        try:
                            e[field] = float(headers[key])
                        except ValueError:
                            pass
                e["keyed"] = bool(_openalex_key())
            if service == "crossref" and headers.get("x-rate-limit-limit"):
                e["rate"] = f"{headers.get('x-rate-limit-limit')}/{headers.get('x-rate-limit-interval', '1s')}"
            if status == 429:
                day["limited"] += 1
                _problem(service, "limited")
                try:
                    retry = float(headers.get("retry-after") or 0)
                except ValueError:
                    retry = 0
                budget = "insufficient budget" in body_start.lower()
                pause = retry if budget else min(max(retry, 5.0), 120.0)
                e["blocked_until"] = now + (pause or 60.0)
                e["blocked_reason"] = "budget" if budget else "rate"
            elif status and status < 400 and float(e.get("blocked_until") or 0) <= now:
                e["blocked_until"], e["blocked_reason"] = 0, None
            # nur 14 Tage aufheben
            for old in sorted(e["days"])[:-14]:
                e["days"].pop(old, None)
            _save(force=status == 429)
        _log.appendleft({"at": now, "service": service, "url": _clean_url(url)[:300], "status": status,
                         "ms": int(elapsed * 1000)})


def _openalex_key() -> str:
    try:
        from . import config
        return str(config.load_settings().get("openalex_api_key") or "").strip()
    except Exception:
        return ""


def install() -> None:
    """Hängt sich an requests.Session.send: jede Anfrage der App läuft hier durch."""
    global _installed
    if _installed:
        return
    import requests
    original = requests.sessions.Session.send

    def send(self, request, **kwargs):
        url = request.url or ""
        try:
            before(url)
        except QuotaBlocked as exc:
            after_blocked = requests.exceptions.ConnectionError(str(exc), request=request)
            raise after_blocked
        started = time.time()
        try:
            resp = original(self, request, **kwargs)
        except Exception:
            after(url, None, None, time.time() - started)
            raise
        body = ""
        if resp.status_code == 429 and not kwargs.get("stream"):
            try:
                body = resp.text[:400]
            except Exception:
                body = ""
        after(url, resp.status_code, dict(resp.headers), time.time() - started, body)
        return resp

    requests.sessions.Session.send = send
    _installed = True


def status() -> Dict:
    """Stand je Dienst, mit dem heutigen Verbrauch und Pausen; dazu die letzten Anfragen."""
    now = time.time()
    with _lock:
        _load()
        services = []
        for key, svc in SERVICES.items():
            e = _state.get(key) or {}
            day = (e.get("days") or {}).get(_today()) or {}
            until = float(e.get("blocked_until") or 0)
            services.append({
                "key": key, "name": svc["name"], "limit_text": svc["limit"],
                "today": day.get("requests", 0), "errors": day.get("errors", 0), "limited": day.get("limited", 0),
                "skipped": day.get("skipped", 0), "total": e.get("total", 0), "last_at": e.get("last_at"),
                "last_status": e.get("last_status"), "remaining": e.get("remaining"), "limit": e.get("limit"),
                "remaining_usd": e.get("remaining_usd"), "limit_usd": e.get("limit_usd"), "rate": e.get("rate"),
                "keyed": e.get("keyed"), "paused_until": until if until > now else None,
                "paused_reason": e.get("blocked_reason") if until > now else None,
                "week": sum(d.get("requests", 0) for k, d in (e.get("days") or {}).items() if k >= time.strftime("%Y-%m-%d", time.localtime(now - 6 * 86400))),
            })
        web = [l for l in _log if l["service"] == "web"]
        return {"services": services, "log": list(_log)[:150], "web_today": len(web)}


def paused(service: str) -> Optional[Dict]:
    """Ist ein Dienst gerade pausiert? Für Hinweise im Prüfbericht."""
    with _lock:
        e = _entry(service)
        until = float(e.get("blocked_until") or 0)
        if until > time.time():
            return {"until": until, "reason": e.get("blocked_reason")}
    return None


def estimate(references: int) -> Dict:
    """Grobe Schätzung, wie viele Anfragen eine Prüfung braucht, und ob das OpenAlex-Budget reicht."""
    openalex = references * 2
    e = _state.get("openalex") or {}
    remaining = e.get("remaining")
    return {"references": references, "openalex": openalex, "crossref": references * 2,
            "openalex_remaining": remaining, "enough": remaining is None or remaining >= openalex,
            "openalex_paused": paused("openalex")}


def reset_pause(service: str) -> None:
    with _lock:
        e = _entry(service)
        e["blocked_until"], e["blocked_reason"] = 0, None
        _save(force=True)


def flush() -> None:
    with _lock:
        _save(force=True)
