"""Rückzugswache: die Quellen aller Projekte regelmäßig auf Rückzüge, Bedenken und Korrekturen prüfen.

Quellen: Crossref ``updated-by`` (Hinweise der Verlage und, seit Januar 2025, der Retraction-Watch-
Datenbank) und OpenAlex ``is_retracted``. Abgefragt werden nur DOIs, gebündelt bis 40 je Anfrage,
im höflichen Pool von Crossref, wenn eine E-Mail-Adresse eingetragen ist.

Der Stand liegt in ``watch.json`` im Datenordner. Ein Fund bleibt "neu", bis die Oberfläche ihn
gezeigt hat; dann meldet die App ihn nicht noch einmal.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, Iterable, List, Optional

from . import config, library, oa, projects
from .i18n import tr

INTERVAL = 24 * 3600          # einmal am Tag
BATCH = 40
PAUSE = 0.35            # Sekunden zwischen gebündelten Anfragen (Crossref: 3 je Sekunde)

# Crossref-Typ der Mitteilung → (Art, Schwere). Neue Fassungen, Ergänzungen und Klarstellungen
# sind keine Warnung wert und bleiben draußen.
KINDS = {
    "retraction": ("retraction", 3),
    "partial_retraction": ("retraction", 3),
    "withdrawal": ("withdrawal", 3),
    "removal": ("withdrawal", 3),
    "expression_of_concern": ("concern", 2),
    "correction": ("correction", 1),
    "corrigendum": ("correction", 1),
    "erratum": ("correction", 1),
}
SEVERE = ("retraction", "withdrawal")

_lock = threading.RLock()
_running = threading.Event()


def _path():
    return config.data_dir() / "watch.json"


def _read() -> Dict:
    p = _path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("dois", {})
                data.setdefault("findings", [])
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"dois": {}, "findings": [], "last_run": None}


def _save(data: Dict) -> None:
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _norm_type(value: object) -> str:
    # Crossref enthält auch "Retraction", "expression-of-concern" und Ähnliches.
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _date(parts: object) -> Optional[str]:
    try:
        p = (parts or {}).get("date-parts")[0]  # type: ignore[union-attr]
        return "-".join(f"{int(x):02d}" if i else str(int(x)) for i, x in enumerate(p[:3]))
    except (TypeError, ValueError, IndexError, AttributeError):
        return None


def notices_from_crossref(item: Dict) -> List[Dict]:
    """Mitteilungen zu einem Crossref-Werk, ohne Doppelte (Verlag und Retraction Watch melden oft beide)."""
    out: Dict[tuple, Dict] = {}
    for u in item.get("updated-by") or []:
        kind = KINDS.get(_norm_type(u.get("type")))
        if not kind:
            continue
        notice = str(u.get("DOI") or "").lower() or None
        key = (notice, kind[0])
        cur = out.get(key)
        entry = {"kind": kind[0], "type": _norm_type(u.get("type")), "notice_doi": notice,
                 "date": _date(u.get("updated")), "source": [str(u.get("source") or "publisher")]}
        if cur:
            cur["source"] = sorted(set(cur["source"]) | set(entry["source"]))
            cur["date"] = cur["date"] or entry["date"]
        else:
            out[key] = entry
    return sorted(out.values(), key=lambda n: (-KINDS[n["type"]][1], n["date"] or ""))


def _chunks(items: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _mailto(email: str) -> Dict[str, str]:
    if email and "@" in email and not email.endswith(("example.org", "example.com")):
        return {"mailto": email}
    return {}


def scan(dois: Iterable[str], email: str = "") -> Dict[str, Dict]:
    """Fragt Crossref und OpenAlex zu den DOIs; liefert {doi_klein: {doi, title, notices, ok}}.

    ``ok`` ist False, wenn keine der beiden Quellen antwortete; dann bleibt der alte Stand gültig.
    """
    wanted = {}
    for d in dois:
        doi = library.normalize_doi(str(d or ""))
        if doi:
            wanted.setdefault(doi.lower(), doi)
    out = {k: {"doi": v, "title": None, "notices": [], "ok": False} for k, v in wanted.items()}
    keys = sorted(wanted)
    # Ein Komma trennt die Filter bei Crossref; solche DOIs (selten) einzeln.
    plain = [k for k in keys if "," not in k]
    for group in _chunks(plain, BATCH):
        j = oa._json(oa._api_get("https://api.crossref.org/works", params={
            "filter": ",".join(f"doi:{d}" for d in group), "select": "DOI,title,updated-by",
            "rows": len(group), **_mailto(email)}))
        for item in ((j or {}).get("message") or {}).get("items") or []:
            key = str(item.get("DOI") or "").lower()
            if key in out:
                out[key].update(title=(item.get("title") or [None])[0], notices=notices_from_crossref(item), ok=True)
        if j is not None:
            for key in group:  # nicht bei Crossref (DataCite, arXiv): gilt trotzdem als geprüft
                out[key]["ok"] = True
        time.sleep(PAUSE)
    for key in (k for k in keys if "," in k):
        from .verify import crossref_work
        m = crossref_work(wanted[key], email)
        if m:
            out[key].update(title=(m.get("title") or [None])[0], notices=notices_from_crossref(m), ok=True)
    for group in _chunks(keys, BATCH):
        j = oa._json(oa._api_get("https://api.openalex.org/works", params={
            "filter": "doi:" + "|".join(group), "select": "doi,title,is_retracted", "per_page": len(group),
            **_mailto(email)}))
        for w in (j or {}).get("results") or []:
            key = str(w.get("doi") or "").lower().replace("https://doi.org/", "")
            if key not in out:
                continue
            out[key]["ok"] = True
            out[key]["title"] = out[key]["title"] or w.get("title")
            if w.get("is_retracted") and not any(n["kind"] in SEVERE for n in out[key]["notices"]):
                # Retraction Watch kennt Rückzüge ohne DOI der Mitteilung; Crossref zeigt die nicht.
                out[key]["notices"].insert(0, {"kind": "retraction", "type": "retraction", "notice_doi": None,
                                               "date": None, "source": ["openalex"]})
        time.sleep(PAUSE)
    return out


def _project_dois(only: Optional[str] = None) -> Dict[str, Dict]:
    """DOIs aus den Projekten (mit eingeschalteter Wache, oder nur ``only``), mit Projektnamen und Titel."""
    out: Dict[str, Dict] = {}
    for p in projects._read()["projects"].values():
        if (only and p["id"] != only) or (not only and p.get("watch") is False):
            continue
        for key, it in (p.get("items") or {}).items():
            cur = out.setdefault(key.lower(), {"doi": it.get("doi") or key, "title": it.get("title"), "projects": []})
            cur["title"] = cur["title"] or it.get("title")
            cur["projects"].append({"id": p["id"], "name": p["name"]})
    return out


def enabled() -> bool:
    return bool(config.load_settings().get("retraction_watch", True))


def due(data: Optional[Dict] = None) -> bool:
    last = (data or _read()).get("last_run")
    return not last or time.time() - float(last) >= INTERVAL


def run(force: bool = False, dois: Optional[Dict[str, Dict]] = None) -> Dict:
    """Prüft die Projektquellen (oder ``dois``) und trägt neue Funde ein. Gibt den Stand zurück."""
    if _running.is_set():
        return status()
    with _lock:
        data = _read()
        if not force and not due(data):
            return status()
        _running.set()
    try:
        watched = dois if dois is not None else _project_dois()
        email = config.load_settings().get("unpaywall_email", "")
        results = scan([w.get("doi") or k for k, w in watched.items()], email) if watched else {}
        now = time.time()
        with _lock:
            data = _read()
            known = {f["id"] for f in data["findings"]}
            new = 0
            for key, res in results.items():
                if not res["ok"]:
                    continue
                meta = watched.get(key) or {}
                data["dois"][key] = {"checked_at": now, "notices": res["notices"]}
                for n in res["notices"]:
                    fid = f"{key}|{n['kind']}|{n['notice_doi'] or ''}"
                    if fid in known:
                        continue
                    known.add(fid)
                    new += 1
                    data["findings"].append({
                        "id": fid, "doi": res["doi"], "title": meta.get("title") or res["title"],
                        **n, "projects": meta.get("projects") or [], "found_at": now, "seen": False})
            if dois is None:  # nur der volle Lauf zählt für den Tagestakt
                data["last_run"] = now
            data["last_checked"] = sum(1 for r in results.values() if r["ok"])
            data["last_failed"] = sum(1 for r in results.values() if not r["ok"])
            _save(data)
        _mark_projects(results)
        return status() | {"new": new}
    finally:
        _running.clear()


def _mark_projects(results: Dict[str, Dict]) -> None:
    """Trägt Mitteilungen bei den Projektquellen ein; ein Rückzug setzt das Urteil auf "retracted"."""
    with projects._lock:
        data = projects._read()
        changed = False
        for p in data["projects"].values():
            for key, it in (p.get("items") or {}).items():
                res = results.get(key.lower())
                if not res or not res["ok"]:
                    continue
                if not it.get("title") and res.get("title"):
                    it["title"] = res["title"]
                    changed = True
                notices = [{k: n[k] for k in ("kind", "notice_doi", "date")} for n in res["notices"]]
                if it.get("notices") != notices:
                    it["notices"] = notices
                    changed = True
                if any(n["kind"] in SEVERE for n in notices) and it.get("verdict") != "retracted":
                    it["verdict"] = "retracted"
                    it["note"] = tr("Zurückgezogen (Rückzugswache).")
                    changed = True
        if changed:
            projects._save(data)


def status() -> Dict:
    data = _read()
    watched = _project_dois()
    findings = sorted(data["findings"], key=lambda f: (-f.get("found_at", 0), -KINDS.get(f.get("type"), (0, 0))[1]))
    for f in findings:  # Projektnamen auf dem neuesten Stand
        cur = watched.get(str(f.get("doi") or "").lower())
        if cur:
            f["projects"] = cur["projects"]
    last = data.get("last_run")
    return {
        "enabled": enabled(),
        "running": _running.is_set(),
        "watched": len(watched),
        "projects": len({p["id"] for w in watched.values() for p in w["projects"]}),
        "last_run": last,
        "next_run": (float(last) + INTERVAL) if last else None,
        "last_checked": data.get("last_checked", 0),
        "last_failed": data.get("last_failed", 0),
        "findings": findings[:300],
        "unseen": sum(1 for f in findings if not f.get("seen")),
    }


def mark_seen(ids: Optional[List[str]] = None) -> int:
    with _lock:
        data = _read()
        n = 0
        for f in data["findings"]:
            if not f.get("seen") and (ids is None or f["id"] in ids):
                f["seen"] = True
                n += 1
        if n:
            _save(data)
        return n


def start_scheduler(first_delay: float = 90.0, every: float = 1800.0) -> None:
    """Hintergrund-Takt: alle 30 Minuten nachsehen, ob die tägliche Prüfung fällig ist."""
    if os.environ.get("BENTHIC_WATCH") == "0":
        return

    def loop() -> None:
        time.sleep(first_delay)
        while True:
            try:
                if enabled() and due():
                    run()
            except Exception:  # Netz weg, Datei gesperrt: beim nächsten Takt erneut
                pass
            time.sleep(every)

    threading.Thread(target=loop, name="retraction-watch", daemon=True).start()
