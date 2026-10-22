"""Flask-Webanwendung für Benthic: prüft Literaturangaben und lädt Quellen über Open Access."""

from __future__ import annotations

import io
import itertools
import json
import logging
import os
import re
import signal
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from flask import Flask, Response, has_request_context, jsonify, request, send_file, send_from_directory

from . import __app_name__, __version__, batch, biblio, clips, config, connect, document, export, finder, guide, history, i18n, journals, latex, library, oa, overlap, preprints, projects, questionable, quota, quotes, refs, related, report, routes, stats, style, support, updates, verify, watch, watchfile, zotero
from .i18n import tr
from .fetcher import BenthicDownloader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("benthic")

quota.install()  # jede Anfrage an einen Dienst wird gezählt und im Rahmen gehalten (quota.py)

app = Flask("benthic", static_folder="static", static_url_path="/static")
# Größte Anfrage: eine Arbeit (document.MAX_BYTES) samt Formularfeldern; alles darüber lehnt Flask mit 413 ab.
app.config["MAX_CONTENT_LENGTH"] = document.MAX_BYTES + 4 * 1024 * 1024

# --------------------------------------------------------------------------
# Schutz der lokalen Schnittstelle
# --------------------------------------------------------------------------
# Das Backend hört nur auf 127.0.0.1, aber jede Website im Browser kann Anfragen dorthin schicken.
# Zwei Wege sind damit zu: DNS-Rebinding (eine fremde Domain zeigt plötzlich auf 127.0.0.1, erkennbar
# am Host-Kopf) und einfache Anfragen fremder Seiten (Formulare, fetch ohne Vorabprüfung, erkennbar an
# Origin und Sec-Fetch-Site). Die App, Grimpo, der MCP-Server und das Zotero-Plug-in kommen weiter durch:
# Sie senden den eigenen Host und dazu den eigenen Origin oder JSON. JSON darf eine fremde Seite nur
# nach einer Vorabprüfung (CORS) senden, und die besteht hier keine.
LOCAL_NAMES = ("127.0.0.1", "localhost")
SAFE_METHODS = ("GET", "HEAD")


def _split_host(value: str):
    """("127.0.0.1", "8327") aus einem Host-Kopf; ohne Port gilt 80."""
    value = (value or "").strip().lower()
    name, sep, port = value.rpartition(":")
    if sep and port.isdigit() and ":" not in name:
        return name, port
    return value, "80"


def _own_origins(port: str):
    origins = {f"http://{n}:{port}" for n in LOCAL_NAMES}
    return origins | ({f"http://{n}" for n in LOCAL_NAMES} if port == "80" else set())


def refusal(host: str, port: str, method: str, origin: Optional[str], content_type: str,
            fetch_site: str = "", fetch_mode: str = "", own_port: str = "80") -> Optional[str]:
    """Grund, eine Anfrage abzulehnen ("host", "origin", "site", "type"), oder None."""
    if host not in LOCAL_NAMES or port != own_port:
        return "host"
    json_body = content_type == "application/json"
    own = origin is not None and origin.lower() in _own_origins(port)
    # "null" senden etwa Seiten in einer Sandbox und vielleicht Zotero; mit JSON ist das ungefährlich (siehe oben).
    if origin is not None and not own and not (origin == "null" and json_body):
        return "origin"
    navigation = method == "GET" and fetch_mode == "navigate"
    if fetch_site in ("cross-site", "same-site") and not (navigation or json_body):
        return "site"
    if method in SAFE_METHODS or own or json_body:
        return None
    return "type"


@app.before_request
def _guard():
    host, port = _split_host(request.headers.get("Host", ""))
    reason = refusal(host, port, request.method, request.headers.get("Origin"), request.mimetype,
                     request.headers.get("Sec-Fetch-Site", ""), request.headers.get("Sec-Fetch-Mode", ""),
                     str(request.environ.get("SERVER_PORT") or "80"))
    if reason:
        logger.warning("Anfrage abgelehnt (%s): %s %s, Host %r, Origin %r", reason, request.method, request.path,
                       request.headers.get("Host", "")[:80], (request.headers.get("Origin") or "")[:80])
        return jsonify({"error": tr("Diese Anfrage kam nicht aus Benthic und wurde abgelehnt.")}), 403
    return None


@app.errorhandler(413)
def _too_large(_exc):
    """Über MAX_CONTENT_LENGTH: eine Antwort, die die Oberfläche lesen kann."""
    return jsonify({"error": tr("Die Datei ist größer als 60 MB.")}), 413


# Plugins (Zusatzwege, eigene Endpunkte, UI-Module); ohne Plugins bleibt alles beim Kern.
routes.load_plugins()
for _bp in routes.blueprints():
    app.register_blueprint(_bp)

# --------------------------------------------------------------------------
# Job-Verwaltung (Hintergrund-Threads + Status-Polling)
# --------------------------------------------------------------------------
_jobs: Dict[str, Dict] = {}
_jobs_lock = threading.Lock()
_cancel_events: Dict[str, threading.Event] = {}
_job_ids = None  # itertools.count ab der ersten freien Laufnummer (siehe _new_job_id)

# Wer nutzt Benthic gerade? (LLM-Clients über MCP, für die Anzeige in der App)
_activity: Dict = {"clients": {}, "events": []}
_activity_lock = threading.Lock()


def _note_activity(client: str, tool: str = "", summary: str = "") -> None:
    with _activity_lock:
        now = time.time()
        _activity["clients"][client] = {"client": client, "last_seen": now}
        if tool:
            _activity["events"].append({"ts": now, "client": client, "tool": tool, "summary": summary[:200]})
            del _activity["events"][:-50]


def _new_job_id() -> str:
    """Laufnummern sind eindeutig, auch über Neustarts hinweg (Verlauf zählt weiter)."""
    global _job_ids
    if _job_ids is None:
        _job_ids = itertools.count(history.next_id())
    return str(next(_job_ids))


def start_parent_watchdog() -> None:
    """Beendet das Backend, sobald die native App (Elternprozess) weg ist."""
    raw = os.environ.get("BENTHIC_PARENT_PID")
    if not raw:
        return
    try:
        expected = int(raw)
    except ValueError:
        return

    def watch() -> None:
        while True:
            time.sleep(2)
            try:
                if os.getppid() != expected:
                    logger.info("Eltern-App beendet, Backend fährt herunter.")
                    os._exit(0)
            except OSError:
                os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def stop_on_sigterm() -> None:
    """SIGTERM beendet das Backend sofort, auch wenn der startende Prozess das Signal ignoriert.

    Die App beendet so ein veraltetes Backend, das ein MCP-Server gestartet hat und das ihr
    Beenden überlebt (19.10.2026: ein 1.0.1-Backend hielt nach dem Update auf 1.1.0 den Port,
    die App zeigte weiter 1.0.1). Wie beim Wächter für die Eltern-App ohne Aufräumen, damit
    laufende Aufträge das Ende nicht aufhalten. Signale gehen nur im Haupt-Thread.
    """
    if threading.current_thread() is not threading.main_thread():
        return

    def stop(_signum, _frame) -> None:
        logger.info("SIGTERM erhalten, Backend fährt herunter.")
        os._exit(0)

    signal.signal(signal.SIGTERM, stop)


def _flag_hijacked(res: Dict, ref) -> Dict:
    """Verweist die Angabe auf eine bekannte gekaperte Zeitschriften-Website? (nur mit Einstellung)"""
    try:
        hits = questionable.hijacked_in(str((ref or {}).get("raw") or "") if isinstance(ref, dict) else str(ref or ""))
    except Exception:
        hits = []
    if hits and isinstance(res, dict):
        h = hits[0]
        res["hijacked"] = hits
        part = i18n.note_part(
            "Die angegebene Website {site} ist eine gekaperte Kopie von „{original}“ (Retraction Watch Hijacked Journal Checker).",
            site=h["site"], original=h["original"] or h["title"])
        before = res.get("note_parts") or ([i18n.note_part(str(res["note"]))] if res.get("note") else [])
        res["note_parts"] = list(before) + [part]
        res["note"] = (str(res.get("note") or "") + " " + i18n.tr_part(part)).strip()
    return res


def start_background() -> None:
    """Alles, was neben dem Webserver läuft: Wächter für die Eltern-App, Rückzugswache, beobachtete Dateien."""
    stop_on_sigterm()
    start_parent_watchdog()
    watch.start_scheduler()
    watchfile.start()


def _parse_identifiers(text: str) -> List[str]:
    """Zerlegt pastete Eingaben in Identifier (getrennt durch ; , oder Zeilen)."""
    if not text:
        return []
    tokens = re.split(r"[;,\n]+", text)
    seen = set()
    result = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        doi = library.normalize_doi(token)
        ident = doi if doi else token
        if ident in seen:
            continue
        seen.add(ident)
        result.append(ident)
    return result


def _download_identifiers(text: str):
    """Kennungen für Download und Prüfung, dazu die Zahl der Angaben ohne Kennung.

    Saubere Listen (nur DOIs und Links) bleiben wie bisher. Alles andere
    (LLM-Antwort, BibTeX, RIS, Literaturliste) geht durch den Referenz-Parser.
    """
    tokens = [t.strip() for t in re.split(r"[;,\n]+", text or "") if t.strip()]
    if tokens and all(library.normalize_doi(t) or t.startswith(("http://", "https://")) for t in tokens):
        return _parse_identifiers(text), 0
    items = refs.parse(text or "")
    ids: List[str] = []
    for r in items:
        if r.get("doi") and str(r["doi"]) not in ids:
            ids.append(str(r["doi"]))
    return ids, sum(1 for r in items if not r.get("doi"))


def _summary(job: Dict, mode: str) -> Dict:
    results = job.get("results") or []
    if mode == "verify":
        counts = verify.summarize(results)
        return {
            "id": job.get("id"), "mode": mode,
            "created_at": job.get("created_at"), "finished_at": job.get("finished_at"),
            "total": job.get("total", len(results)), "done": job.get("done", len(results)),
            "verdicts": counts,
            "ok": counts["verified"] + counts["corrected"],
            "oa": 0, "routes": 0, "present": 0, "blocked": 0, "absent": 0, "errors": 0,
            "results": results,
        }
    return {
        "id": job.get("id"),
        "mode": mode,
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
        "total": job.get("total", len(results)),
        "done": job.get("done", len(results)),
        "ok": sum(1 for r in results if r.get("status") == "ok"),
        "oa": sum(1 for r in results if r.get("status") == "ok" and r.get("channel") == "oa"),
        "routes": sum(1 for r in results if r.get("status") == "ok" and routes.is_route_channel(r.get("channel"))),
        "present": sum(1 for r in results if r.get("status") in ("skipped", "present")),
        "blocked": sum(1 for r in results if r.get("status") == "blocked"),
        "absent": sum(1 for r in results if r.get("status") == "absent"),
        "errors": sum(1 for r in results if r.get("status") == "error"),
        "results": results,
    }


def _run_job(job_id: str, identifiers: List[str], output_dir, settings: Dict, mode: str) -> None:
    cancel = _cancel_events.get(job_id) or threading.Event()
    try:
        concurrency = max(1, min(int(settings.get("concurrency", 3)), 8))
    except (TypeError, ValueError):
        concurrency = 3

    with _jobs_lock:
        _jobs[job_id]["total"] = len(identifiers)

    def on_event(ev: Dict) -> None:
        """Live-Events der Kaskade in den Job schreiben (für die Oberfläche)."""
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is None:
                return
            seq = job.get("event_seq", 0) + 1
            job["event_seq"] = seq
            ev = dict(ev)
            ev["seq"] = seq
            ev["ts"] = time.time()
            events = job.setdefault("events", [])
            events.append(ev)
            if len(events) > 200:
                del events[:-200]
            ident = ev.get("identifier")
            if ident:
                active = job.setdefault("active", {})
                if ev.get("phase") == "done":
                    active.pop(ident, None)
                else:
                    active[ident] = {
                        "phase": ev.get("phase"),
                        "source": ev.get("source"),
                        "route": ev.get("route"),
                        "target": ev.get("target"),
                        "version": ev.get("version"),
                    }

    def work(ident):
        if cancel.is_set():
            return None
        if mode == "verify":
            res = verify.verify_ref(ident, settings.get("unpaywall_email", ""), on_event=on_event)
            return _flag_hijacked(res, ident)
        downloader = BenthicDownloader(
            output_dir,
            oa_providers=settings.get("oa_providers"),
            oa_email=settings.get("unpaywall_email"),
            extra_routes=settings.get("extra_routes", False),
            skip_existing=settings.get("skip_existing", True),
            prefer_published=settings.get("prefer_published", True),
            core_api_key=settings.get("core_api_key", ""),
            elsevier_api_key=settings.get("elsevier_api_key", ""),
            proxy_prefix=settings.get("proxy_prefix", ""),
        )
        if mode == "check":
            return downloader.check_one(ident)
        return downloader.download_one(ident, on_event=on_event)

    completed = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        future_map = {ex.submit(work, ident): (i, ident) for i, ident in enumerate(identifiers)}
        for fut in as_completed(future_map):
            order, ident = future_map[fut]
            name = refs.label(ident) if isinstance(ident, dict) else ident
            res = None
            try:
                res = fut.result()
            except CancelledError:
                res = None
            except Exception as exc:  # defekte Einzelquelle darf den Batch nicht stoppen
                res = {"identifier": name, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
                if mode == "verify":
                    res.update(verdict="uncertain", note=tr("Prüfung fehlgeschlagen, bitte erneut versuchen."), score=0, flags=[])
            completed += 1
            with _jobs_lock:
                if res is not None:
                    res["order"] = order  # Eingabereihenfolge (Ergebnisse kommen parallel an)
                    _jobs[job_id]["results"].append(res)
                _jobs[job_id]["done"] = completed
                _jobs[job_id]["current"] = name

    if mode == "verify":
        with _jobs_lock:
            done_results = list(_jobs[job_id]["results"])
        if not cancel.is_set():
            try:  # Preprints, die inzwischen veröffentlicht sind
                preprints.check_results(done_results, settings.get("unpaywall_email", ""))
            except Exception:
                logger.exception("Suche nach veröffentlichten Fassungen fehlgeschlagen")
        try:
            report = style.check_list(done_results)
        except Exception:  # eine Stilfrage darf den Prüfbericht nicht verhindern
            logger.exception("Stilprüfung fehlgeschlagen")
            report = None
        with _jobs_lock:
            _jobs[job_id]["style"] = report

    with _jobs_lock:
        cancelled = cancel.is_set()
        _jobs[job_id]["status"] = "cancelled" if cancelled else "done"
        _jobs[job_id]["current"] = None
        _jobs[job_id]["active"] = {}
        _jobs[job_id]["finished_at"] = time.time()
        job = dict(_jobs[job_id])

    try:
        projects.record_results(job.get("project"), mode, job.get("results") or [], source=job.get("origin", "ui"),
                                run=str((job.get("document") or {}).get("name") or ""))
    except Exception:  # ein Projektfehler darf den Verlauf nicht verhindern
        logger.exception("Projekt-Übernahme fehlgeschlagen")
    summary = _summary(job, mode)
    summary.update({k: job.get(k) for k in ("origin", "project", "project_name", "document", "style",
                                            "quote_check", "missing_works", "docx_path", "docx_name")})
    if not job.get("no_history"):  # Stapelprüfungen speichern ihre Läufe selbst (batch.py)
        history.append(summary)


# --------------------------------------------------------------------------
# Basis-Routen
# --------------------------------------------------------------------------
@app.route("/")
def index():
    # In der eingestellten Sprache ausliefern und immer neu validieren: sonst zeigt die
    # WebView nach einem Update oder Sprachwechsel die alte Seite.
    page = i18n.translate_html((Path(app.static_folder) / "index.html").read_text(encoding="utf-8"))
    if not config.load_settings().get("setup_complete"):
        # Erster Start: die Begrüßung steht schon beim ersten Bild da (das Intro der App landet auf ihr).
        page = re.sub(r'(<html lang="[a-z]{2}")', r'\1 class="first-run"', page, count=1)
    resp = Response(page, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/pet")
def pet_page():
    """Grimpo, der Begleiter auf dem Schreibtisch (static/pet.html), in der Sprache der Oberfläche."""
    page = (Path(app.static_folder) / "pet.html").read_text(encoding="utf-8")
    resp = Response(i18n.translate_html(page), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/clips", methods=["GET", "POST"])
def clip_list():
    """Fundstücke von Grimpo: Links, Textstellen, Bildschirmfotos (clips.py)."""
    if request.method == "GET":
        return jsonify({"clips": clips.load()[:int(request.args.get("limit") or 50)], "folder": str(clips.folder())})
    data = request.get_json(silent=True) or {}
    try:
        item = clips.add(str(data.get("kind") or ""), url=str(data.get("url") or ""), title=str(data.get("title") or ""),
                         text=str(data.get("text") or ""), path=str(data.get("path") or ""), project=projects.active())
    except ValueError as exc:
        return jsonify({"error": {"url": tr("Das ist kein Link."), "path": tr("Das Bild liegt nicht im Ordner für Fundstücke.")}
                        .get(str(exc), tr("Unbekannte Art von Fundstück."))}), 400
    return jsonify(item)


@app.route("/api/clips/<clip_id>", methods=["DELETE"])
def clip_delete(clip_id: str):
    return jsonify({"removed": clips.remove(clip_id)})


@app.route("/api/guide", methods=["GET", "POST"])
def guide_state():
    """Der Weg durch eine Arbeit (guide.py), je aktivem Projekt; POST hakt eine Aufgabe ab oder wieder auf."""
    project = projects.active()
    if request.method == "GET":
        return jsonify(guide.state(project))
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(guide.mark(project, str(data.get("task") or ""), bool(data.get("done"))))
    except ValueError:
        return jsonify({"error": tr("Unbekannte Aufgabe")}), 400


@app.route("/i18n.js")
def i18n_script():
    """Sprache und Katalog für app.js (wird vor app.js geladen)."""
    resp = Response(i18n.script(), mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/health")
def health():
    return jsonify({"app": __app_name__, "version": __version__, "ok": True})


# --------------------------------------------------------------------------
# Zusatzwege (Plugins)
# --------------------------------------------------------------------------
@app.route("/api/routes")
def get_routes():
    """Angemeldete Zusatzwege und UI-Module der Plugins (ohne Plugins: leer)."""
    ui = []
    for name, folder in routes.ui_dirs().items():
        item = {"name": name, "js": f"/plugins/{name}/ui.js" if (folder / "ui.js").is_file() else None}
        if (folder / "ui.css").is_file():
            item["css"] = f"/plugins/{name}/ui.css"
        ui.append(item)
    return jsonify({"routes": routes.describe(), "ui": ui})


@app.route("/plugins/<name>/<path:filename>")
def plugin_asset(name: str, filename: str):
    folder = routes.ui_dirs().get(name)
    if folder is None:
        return jsonify({"error": tr("Unbekanntes Plugin")}), 404
    resp = send_from_directory(folder, filename)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# --------------------------------------------------------------------------
# Ersteinrichtung (für jede:n Student:in, ohne Vorwissen)
# --------------------------------------------------------------------------
@app.route("/api/setup", methods=["GET"])
def get_setup():
    s = config.load_settings()
    return jsonify({
        "needed": not s.get("setup_complete"),
        "current": {k: s.get(k) for k in ("unpaywall_email", "institution",
                                          "proxy_prefix", "download_dir", "library_sources")},
        "suggestions": config.setup_suggestions(),
    })


@app.route("/api/setup", methods=["POST"])
def save_setup():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip()
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": tr("Die E-Mail-Adresse sieht unvollständig aus."), "field": "email"}), 400
    proxy = str(data.get("proxy_prefix") or "").strip()
    if proxy and not proxy.startswith(("http://", "https://")):
        return jsonify({"error": tr("Der Proxy-Link muss mit https:// beginnen."), "field": "proxy"}), 400

    folder = str(data.get("download_dir") or "").strip()
    target = Path(folder).expanduser() if folder else config.default_download_dir()
    if not target.is_absolute():
        return jsonify({"error": tr("Bitte einen vollständigen Ordnerpfad angeben."), "field": "folder"}), 400
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return jsonify({"error": tr("Ordner lässt sich nicht anlegen: {exc}", exc=exc), "field": "folder"}), 400

    lib = data.get("library") or {}
    kind, path = str(lib.get("kind") or "empty"), str(lib.get("path") or "").strip()
    imported, sources = None, []
    if kind in ("file", "zotero"):
        if not path:
            return jsonify({"error": tr("Bitte eine Datei auswählen."), "field": "library"}), 400
        try:
            imported = library.import_from_file(path)
            sources = [path]
        except Exception as exc:  # unlesbare Datei: verständlich melden, nichts speichern
            return jsonify({"error": tr("Bibliothek nicht lesbar: {exc}", exc=exc), "field": "library"}), 400

    settings = config.save_settings({
        "unpaywall_email": email,
        "institution": str(data.get("institution") or "").strip(),
        "proxy_prefix": proxy,
        "download_dir": str(target),
        "library_sources": sources,
        "setup_complete": True,
    })
    return jsonify({"settings": config.masked(settings), "imported": imported, "count": library.count()})


# --------------------------------------------------------------------------
# Einstellungen
# --------------------------------------------------------------------------
@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(config.masked(config.load_settings()))  # API-Keys nur als Platzhalter


@app.route("/api/settings", methods=["POST"])
def set_settings():
    data = request.get_json(silent=True) or {}
    return jsonify(config.masked(config.save_settings(config.without_masks(data))))


# --------------------------------------------------------------------------
# Download / Check
# --------------------------------------------------------------------------
def _start_job(mode: str):
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    unresolved = 0
    extra = None
    if mode == "verify":
        plain = "\n".join(map(str, text)) if isinstance(text, list) else str(text)
        whole = document.manuscript(plain)
        if whole and whole.get("entries"):
            # Eine ganze Arbeit eingefügt: nur ihr Literaturverzeichnis prüfen, nicht jeden Absatz
            identifiers = whole.pop("refs")
            whole.pop("refs_text", None)
            whole.update(name=str(data.get("name") or tr("Eingefügte Arbeit")), characters=len(plain))
            extra = {"document": whole}
        else:
            identifiers = refs.parse(plain)
        if not identifiers:
            return jsonify({"error": tr("Keine Literaturangaben gefunden")}), 400
    elif isinstance(text, list):
        identifiers = [str(i).strip() for i in text if str(i).strip()]
    else:
        identifiers, unresolved = _download_identifiers(str(text))

    if not identifiers:
        msg = tr("Keine DOIs gefunden (Angaben ohne DOI zuerst unter „Prüfen“ auflösen)") if unresolved \
            else tr("Keine DOIs gefunden")
        return jsonify({"error": msg, "unresolved": unresolved}), 400

    settings = config.load_settings()
    if data.get("extra_routes") is False:
        settings["extra_routes"] = False  # z. B. für LLM-Werkzeuge: nie Zusatzwege
    project = data.get("project", projects.active())
    proj = projects.find(project) if project else None
    if project and not proj:
        return jsonify({"error": tr("Projekt „{name}“ nicht gefunden", name=project)}), 404
    job_id, dir_note = _launch_job(mode, identifiers, settings, proj, extra)
    return jsonify({"job_id": job_id, "total": len(identifiers), "mode": mode, "dir_note": dir_note,
                    "unresolved": unresolved, "project": proj["id"] if proj else None,
                    "document": (extra or {}).get("document")})


def _launch_job(mode: str, identifiers: List, settings: Dict, proj, extra: Dict = None):
    """Legt einen Lauf an und startet ihn im Hintergrund; (Lauf-ID, Hinweis zum Zielordner)."""
    output_dir, dir_note = config.resolve_download_dir(settings)
    origin = (extra or {}).get("origin") or (request.headers.get("X-Benthic-Client") if has_request_context() else None) or "ui"
    with _jobs_lock:
        job_id = _new_job_id()
        _jobs[job_id] = {
            "id": job_id,
            "mode": mode,
            "status": "running",
            "total": len(identifiers),
            "done": 0,
            "current": None,
            "results": [],
            "events": [],
            "active": {},
            "event_seq": 0,
            "created_at": time.time(),
            "dir_note": dir_note,
            "origin": origin,
            "project": proj["id"] if proj else None,
            "project_name": proj["name"] if proj else None,
            "extra_routes": bool(settings.get("extra_routes") and routes.registered()),
            **(extra or {}),
        }
    _cancel_events[job_id] = threading.Event()

    threading.Thread(
        target=_run_job,
        args=(job_id, identifiers, output_dir, settings, mode),
        daemon=True,
    ).start()
    return job_id, dir_note


@app.route("/api/download", methods=["POST"])
def start_download():
    return _start_job("download")


@app.route("/api/check", methods=["POST"])
def start_check():
    return _start_job("check")


@app.route("/api/verify", methods=["POST"])
def start_verify():
    """Hallucination-Check: Literaturangaben auflösen und mit echten Werken abgleichen."""
    return _start_job("verify")


@app.route("/api/document", methods=["POST"])
def check_document():
    """Ganze Arbeit prüfen (PDF, Word, Text, LaTeX): Verzeichnis und Zitate abgleichen, Einträge prüfen.

    Aus der App als Datei-Upload (bei LaTeX auch mehrere Dateien oder ein .zip), für KI-Werkzeuge als
    JSON mit dem Pfad einer Datei oder eines LaTeX-Projektordners auf diesem Mac.
    """
    uploads = request.files.getlist("file")
    if uploads:
        items = [(f.filename or "", f.read(document.MAX_BYTES + 1)) for f in uploads]
        project = request.form.get("project", projects.active())
        path = None
    else:
        body = request.get_json(silent=True) or {}
        path = Path(str(body.get("path") or "")).expanduser()
        if not path.exists() or not (path.is_dir() or path.is_file()):
            return jsonify({"error": tr("Datei nicht gefunden: {path}", path=str(path))}), 400
        items = [] if path.is_dir() else [(path.name, b"")]
        project = body.get("project", projects.active())
    try:
        analysis, name, size = _analyse_document(items, path)
    except ValueError as exc:
        reason = str(exc)
        msg = {"type": tr("Bitte eine PDF-, Word-, LaTeX-, Text- oder Markdown-Datei wählen."),
               "size": tr("Die Datei ist größer als 60 MB."),
               "no-tex": tr("Im Projekt steht keine .tex-Datei."),
               "too-many": tr("Im Ordner liegen zu viele Dateien für ein LaTeX-Projekt."),
               }.get(reason, tr("Die Datei ließ sich nicht lesen."))
        return jsonify({"error": msg}), 400
    if not analysis["entries"]:
        msg = (tr("Keine .bib-Datei mit zitierten Einträgen gefunden.") if analysis.get("style") == "latex"
               else tr("In dieser Datei steht kein Literaturverzeichnis, das Benthic erkennt."))
        return jsonify({"error": msg, "document": {k: v for k, v in analysis.items() if k not in ("refs_text", "refs")}}), 400
    identifiers = analysis.pop("refs")  # Reihenfolge = Einträge: Ergebnis "order" ist die Eintragsnummer
    analysis.pop("refs_text", None)
    proj = projects.find(project) if project else None
    analysis.update(name=name, characters=size)
    extra: Dict = {"document": analysis}
    if name.lower().endswith(".docx"):
        extra["docx_name"] = name
        if path is not None and path.is_file():
            try:
                extra["docx_bytes"] = path.read_bytes()
            except Exception:
                pass
            extra["docx_path"] = str(path)
        elif items and items[0][1]:
            extra["docx_bytes"] = items[0][1]
    job_id, dir_note = _launch_job("verify", identifiers, config.load_settings(), proj, extra)
    return jsonify({"job_id": job_id, "total": len(identifiers), "mode": "verify", "document": analysis,
                    "project": proj["id"] if proj else None})


def _analyse_document(items, path=None):
    """(Analyse, Anzeigename, Zeichenzahl) für hochgeladene Dateien oder einen Pfad; ValueError bei Fehlern."""
    names = [n for n, _ in items]
    if path is not None and not path.is_dir():
        # Vor dem Lesen: nur gewöhnliche Dateien passender Art und Größe (kein /dev/zero, keine Pipe).
        if not path.is_file() or path.suffix.lower() not in document.TYPES + (".tex", ".ltx", ".zip"):
            raise ValueError("type")
        if path.stat().st_size > document.MAX_BYTES:
            raise ValueError("size")
    tex_like = path is not None and (path.is_dir() or path.suffix.lower() in (".tex", ".ltx"))
    if not tex_like and any(n.lower().endswith((".tex", ".ltx", ".zip")) for n in names):
        tex_like = True
    if tex_like:
        if path is not None and path.suffix.lower() == ".zip":  # Projekt als .zip (Stapelprüfung, Datei beobachten)
            if path.stat().st_size > document.MAX_BYTES:
                raise ValueError("size")
            files = latex.files_from_upload([(path.name, path.read_bytes())])
        elif path is not None:
            files = latex.files_from_path(path)
        else:
            if sum(len(d) for _, d in items) > document.MAX_BYTES:
                raise ValueError("size")
            files = latex.files_from_upload(items)
        analysis = latex.analyse(files)
        main = analysis.get("main_file") or ""
        name = path.name if path is not None else (next((n for n in names if n.lower().endswith(".zip")), "")
                                                     or Path(main).name or names[0])
        return analysis, name, sum(len(v) for k, v in files.items() if not k.startswith("\x00"))
    name, data = items[0]
    if path is not None:
        data = path.read_bytes()
    text = document.extract_text(name, data)
    return document.analyse(text), name, len(text)


@app.route("/api/style", methods=["POST"])
def restyle():
    """Stilprüfung eines Prüfberichts für einen anderen Zitierstil (aus dem Lauf oder dem Verlauf)."""
    data = request.get_json(silent=True) or {}
    job_id, wanted = str(data.get("job_id") or ""), str(data.get("style") or "")
    with _jobs_lock:
        job = dict(_jobs[job_id]) if job_id in _jobs else None
    results = (job or history.get(job_id) or {}).get("results")
    if results is None:
        return jsonify({"error": tr("Unbekannter Lauf")}), 404
    return jsonify(style.check_list(results, wanted or None))


def _job_record(job_id: str) -> Optional[Dict]:
    """Ein Lauf aus dem Speicher (läuft oder gerade fertig), aus dem Verlauf oder aus einer Stapelprüfung."""
    with _jobs_lock:
        if job_id in _jobs:
            return dict(_jobs[job_id])
    return history.get(job_id) or batch.find_job(job_id)


def _job_store(job_id: str, key: str, value) -> None:
    """Ergänzt einen Lauf um ein Ergebnis, im Speicher und im Verlauf."""
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id][key] = value
    history.update(job_id, {key: value})


def _watch_analyse(path: Path) -> Dict:
    analysis, name, size = _analyse_document([] if path.is_dir() else [(path.name, b"")], path)
    analysis.update(name=name, characters=size)
    return analysis


def _watch_launch(path: Path, analysis: Dict) -> Optional[str]:
    analysis = dict(analysis)
    identifiers = analysis.pop("refs")
    analysis.pop("refs_text", None)
    job_id, _note = _launch_job("verify", identifiers, config.load_settings(), None,
                                {"document": analysis, "origin": "watch"})
    return job_id


watchfile.configure(_watch_analyse, _watch_launch, _job_record)


def _batch_launch(path: Path, analysis: Dict, project_id: Optional[str] = None) -> Optional[str]:
    analysis = dict(analysis)
    identifiers = analysis.pop("refs")
    analysis.pop("refs_text", None)
    job_id, _note = _launch_job("verify", identifiers, config.load_settings(), projects.find(project_id) if project_id else None,
                                {"document": analysis, "origin": "batch", "no_history": True})
    return job_id


def _batch_fetch(job: Dict) -> None:
    """Freie PDFs der Quellen laden, die eine Arbeit wörtlich zitiert (für die Zitatprüfung)."""
    entries = {q.get("entry") for q in (job.get("document") or {}).get("quotes") or []}
    dois = [r["doi"] for r in job.get("results") or [] if r.get("order") in entries and r.get("doi")
            and r.get("verdict") in projects.REAL and not biblio.pdf_path(r["doi"])]
    if not dois:
        return
    job_id, _note = _launch_job("download", sorted(set(dois)), config.load_settings(), None,
                                {"origin": "batch", "no_history": True})
    for _ in range(900):
        with _jobs_lock:
            if _jobs.get(job_id, {}).get("status") != "running":
                return
        time.sleep(1.0)


def _batch_quotes(job_id: str, job: Dict) -> List[Dict]:
    checked = quotes.check_document(job["document"], job.get("results") or [])
    _job_store(job_id, "quote_check", checked)
    return checked


def _batch_project(name: str) -> Optional[str]:
    """Ein neues Projekt für einen Stapel (Name des Ordners, bei Bedarf mit Datum)."""
    for candidate in (name, f"{name} {time.strftime('%Y-%m-%d %H:%M')}"):
        try:
            return projects.create(candidate, tr("Stapelprüfung"))["id"]
        except ValueError:
            continue
    return None


def _stop_job(job_id: str) -> None:
    ev = _cancel_events.get(job_id)
    if ev:
        ev.set()


batch.configure(_watch_analyse, _batch_launch, _job_record, _stop_job, quotes=_batch_quotes, fetch=_batch_fetch,
                project=_batch_project)


@app.route("/api/batch", methods=["GET", "POST"])
def batches():
    """Stapelprüfung: Liste der Stapel, oder einen Ordner mit Abgaben prüfen."""
    if request.method == "GET":
        return jsonify({"batches": batch.list_batches()})
    data = request.get_json(silent=True) or {}
    folder = str(data.get("folder") or "").strip()
    try:
        return jsonify(batch.start(folder, recursive=bool(data.get("recursive")), quotes=bool(data.get("quotes")),
                                   project=bool(data.get("project"))))
    except ValueError as exc:
        msg = {"folder": tr("Ordner nicht gefunden: {path}", path=folder),
               "empty": tr("In diesem Ordner liegen keine Arbeiten (PDF, Word, LaTeX, Text)."),
               "zip": tr("Das .zip ließ sich nicht entpacken."),
               "size": tr("Das .zip ist entpackt größer als 1,5 GB.")}.get(str(exc), str(exc))
        return jsonify({"error": msg}), 400


@app.route("/api/batch/<batch_id>")
def batch_status(batch_id: str):
    b = batch.load(batch_id)
    if not b:
        return jsonify({"error": tr("Unbekannter Stapel")}), 404
    return jsonify(batch.public(b))


@app.route("/api/batch/<batch_id>/rerun", methods=["POST"])
def batch_rerun(batch_id: str):
    names = (request.get_json(silent=True) or {}).get("names")
    try:
        return jsonify(batch.rerun(batch_id, [str(n) for n in names] if isinstance(names, list) else None))
    except ValueError as exc:
        msg = {"unknown": tr("Unbekannter Stapel"), "running": tr("Der Stapel läuft noch.")}.get(str(exc), str(exc))
        return jsonify({"error": msg}), 400


@app.route("/report/batch/<batch_id>")
def batch_report(batch_id: str):
    """Sammelbericht eines Stapels: Übersicht und die Probleme je Arbeit, druckbar."""
    b = batch.load(batch_id)
    if not b:
        return Response(tr("Unbekannter Stapel"), status=404, mimetype="text/plain")
    return Response(report.render_batch(batch.public(b), auto_print=request.args.get("print") == "1"),
                    mimetype="text/html; charset=utf-8")


@app.route("/api/batch/<batch_id>/cancel", methods=["POST"])
def batch_cancel(batch_id: str):
    return jsonify({"cancelled": batch.cancel(batch_id)})


@app.route("/api/batch/<batch_id>/export")
def batch_export(batch_id: str):
    b = batch.load(batch_id)
    if not b:
        return jsonify({"error": tr("Unbekannter Stapel")}), 404
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", b.get("name") or "stapel").strip("-") or "stapel"
    if request.args.get("format") == "xlsx":
        return send_file(io.BytesIO(batch.to_xlsx(b, tr)), as_attachment=True, download_name=f"benthic-{name}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return Response(batch.to_csv(b, tr), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=benthic-{name}.csv"})


@app.route("/api/usage")
def usage():
    """Anfragen je Dienst heute, Kontingente, Pausen und das Protokoll der letzten Anfragen."""
    out = quota.status()
    s = config.load_settings()
    out["keys"] = {"openalex": bool(s.get("openalex_api_key")), "semanticscholar": bool(s.get("semanticscholar_api_key")),
                   "core": bool(s.get("core_api_key")),
                   "email": bool(s.get("unpaywall_email")) and not str(s.get("unpaywall_email")).endswith("example.org")}
    if request.args.get("estimate"):
        out["estimate"] = quota.estimate(int(request.args.get("estimate") or 0))
    return jsonify(out)


@app.route("/api/usage/reset", methods=["POST"])
def usage_reset():
    """Pause eines Dienstes aufheben (etwa nach dem Eintragen eines eigenen Keys)."""
    service = str((request.get_json(silent=True) or {}).get("service") or "")
    if service not in quota.SERVICES:
        return jsonify({"error": tr("Unbekannter Dienst")}), 400
    quota.reset_pause(service)
    return jsonify(quota.status())


@app.route("/api/watch-file", methods=["GET", "POST", "DELETE"])
def watch_file():
    """Eine Arbeit beim Schreiben beobachten (watchfile.py): anlegen, Stand abfragen, beenden."""
    if request.method == "GET":
        return jsonify(watchfile.status())
    data = request.get_json(silent=True) or {}
    path = str(data.get("path") or "").strip()
    if request.method == "DELETE":
        return jsonify(watchfile.remove(path))
    if data.get("seen"):
        watchfile.mark_seen()
        return jsonify(watchfile.status())
    try:
        return jsonify(watchfile.add(path))
    except ValueError as exc:
        msg = {"missing": tr("Datei nicht gefunden: {path}", path=path),
               "too-many": tr("Benthic beobachtet höchstens {n} Dateien.", n=watchfile.MAX_WATCHED)}.get(str(exc), str(exc))
        return jsonify({"error": msg}), 400


@app.route("/report/<job_id>")
def report_page(job_id: str):
    """Prüfbericht als druckbare Seite (für die PDF aus der App oder den Druckdialog im Browser)."""
    job = _job_record(job_id)
    if not job or job.get("mode") not in ("verify", None) or not job.get("results"):
        return Response(tr("Unbekannter Lauf"), status=404, mimetype="text/plain")
    return Response(report.render(job, job_id, auto_print=request.args.get("print") == "1"),
                    mimetype="text/html; charset=utf-8")


@app.route("/api/missing-works", methods=["POST"])
def find_missing_works():
    """Werke, die die bestätigten Quellen eines Laufs, Projekts oder einer DOI-Liste oft zitieren."""
    data = request.get_json(silent=True) or {}
    job_id = str(data.get("job_id") or "")
    if job_id:
        job = _job_record(job_id)
        if not job:
            return jsonify({"error": tr("Unbekannter Lauf")}), 404
        dois = [r["doi"] for r in job.get("results") or [] if r.get("doi") and r.get("verdict") in projects.REAL]
    elif data.get("project"):
        proj = projects.detail(str(data["project"]))
        if not proj:
            return jsonify({"error": tr("Projekt nicht gefunden")}), 404
        dois = [i["doi"] for i in proj["items"] if i.get("verdict") in projects.REAL or not i.get("verdict")]
    else:
        dois = [d for d in (library.normalize_doi(str(x)) for x in data.get("dois") or []) if d]
    if len(dois) < 2:
        return jsonify({"error": tr("Dafür braucht es mindestens zwei bestätigte Quellen.")}), 400
    result = related.missing_works(dois, config.load_settings().get("unpaywall_email", ""),
                                   top=min(max(int(data.get("top") or 12), 1), 30))
    if job_id:
        _job_store(job_id, "missing_works", result)
    return jsonify(result)


@app.route("/api/alternatives", methods=["POST"])
def find_alternatives():
    """Echte Arbeiten zum Thema einer Angabe, die Benthic nicht fand (Vorschläge zum Lesen, related.alternatives)."""
    data = request.get_json(silent=True) or {}
    query = str(data.get("title") or data.get("query") or "").strip()
    if len(query) < 8:
        return jsonify({"error": tr("Zu wenig Text für eine Suche.")}), 400
    return jsonify({"suggestions": related.alternatives(query, config.load_settings().get("unpaywall_email", ""))})


@app.route("/api/find-sources", methods=["POST"])
def find_sources():
    """Echte Arbeiten zu einer Aussage, mit der passendsten Stelle aus dem Abstract (finder.py)."""
    data = request.get_json(silent=True) or {}
    claim = str(data.get("claim") or "").strip()[:1000]
    out = finder.find(claim, str(data.get("query") or "").strip()[:1000] or None, bool(data.get("open_access_only")),
                      config.load_settings().get("unpaywall_email", ""))
    if out.get("error") == "short":
        return jsonify({"error": tr("Schreib die Aussage in einem ganzen Satz.")}), 400
    if out.get("error"):
        return jsonify({"error": tr("OpenAlex hat nicht geantwortet. Später erneut versuchen.")}), 502
    return jsonify(out)


@app.route("/api/request-copy")
def request_copy():
    """Bitte um eine Kopie bei den Autor:innen, wenn es keine freie Fassung gibt (oa.copy_request)."""
    doi = library.normalize_doi(request.args.get("doi", ""))
    if not doi:
        return jsonify({"error": tr("Keine gültige DOI")}), 400
    out = oa.copy_request(doi, config.load_settings().get("unpaywall_email", ""))
    if not out:
        return jsonify({"error": tr("Zu dieser DOI ließen sich keine Angaben laden. Später erneut versuchen.")}), 502
    return jsonify(out)


@app.route("/api/jobs/<job_id>/corrected")
def corrected_list(job_id: str):
    """Das Verzeichnis in einem Zitierstil, gebaut aus den echten Angaben aller gefundenen Quellen; was Benthic
    nicht belegen konnte, steht getrennt daneben, damit nichts Erfundenes unbemerkt mitwandert."""
    job = _job_record(job_id)
    if not job or not job.get("results"):
        return jsonify({"error": tr("Unbekannter Lauf")}), 404
    results = sorted(job["results"], key=lambda r: r.get("order", 0))
    fixed, open_items = [], []
    for r in results:
        doi = r.get("doi") or (r.get("found") or {}).get("doi")
        entry = {"n": int(r.get("order", 0)) + 1, "verdict": r.get("verdict"),
                 "text": str(r.get("input") or (r.get("claimed") or {}).get("title") or r.get("identifier") or "")[:400]}
        if doi and r.get("verdict") in projects.REAL:
            fixed.append((doi, entry))
        else:
            open_items.append(entry)
    lang = "de-DE" if i18n.current() == "de" else "en-GB"
    text, missing = biblio.export([d for d, _ in fixed], "text", request.args.get("style") or "apa", locale=lang)
    gone = {m.lower() for m in missing}
    open_items += [e for d, e in fixed if d.lower() in gone]
    open_items.sort(key=lambda e: e["n"])
    return jsonify({"text": text, "fixed": len(fixed) - len(gone), "open": open_items})


@app.route("/api/quotes", methods=["POST"])
def check_quotes():
    """Wörtliche Zitate einer geprüften Arbeit mit den geladenen Volltexten abgleichen."""
    data = request.get_json(silent=True) or {}
    if data.get("quote"):  # ein einzelnes Zitat, etwa vom KI-Assistenten
        doi = library.normalize_doi(str(data.get("doi") or ""))
        if not doi:
            return jsonify({"error": tr("Keine gültige DOI")}), 400
        return jsonify(quotes.check(doi, str(data["quote"])[:2000], data.get("page")))
    job_id = str(data.get("job_id") or "")
    job = _job_record(job_id)
    if not job or not (job.get("document") or {}).get("quotes"):
        return jsonify({"error": tr("Zu diesem Lauf gibt es keine wörtlichen Zitate.")}), 404
    checked = quotes.check_document(job["document"], job.get("results") or [])
    _job_store(job_id, "quote_check", checked)
    counts: Dict[str, int] = {}
    for q in checked:
        counts[q["status"]] = counts.get(q["status"], 0) + 1
    return jsonify({"quotes": checked, "counts": counts,
                    "missing_pdfs": sorted({q["doi"] for q in checked if q["status"] == "no_pdf" and q.get("doi")})})


@app.route("/api/overlap", methods=["POST"])
def check_overlap():
    """Übernahmen ohne Anführungszeichen: zitierende Sätze gegen die geladenen Volltexte ihrer Quellen (overlap.py)."""
    job_id = str((request.get_json(silent=True) or {}).get("job_id") or "")
    job = _job_record(job_id)
    if not job or not (job.get("document") or {}).get("claims"):
        return jsonify({"error": tr("Zu diesem Lauf gibt es keine Zitate im Text.")}), 404
    result = overlap.check_document(job["document"], job.get("results") or [])
    _job_store(job_id, "overlap_check", result)
    return jsonify({**result, "counts": overlap.summary(result)})


@app.route("/api/support", methods=["POST"])
def find_support():
    """Passagen eines geladenen Artikels, die einer Aussage am nächsten kommen (kein Urteil)."""
    data = request.get_json(silent=True) or {}
    doi = library.normalize_doi(str(data.get("doi") or ""))
    claim = str(data.get("claim") or "").strip()
    if not doi:
        return jsonify({"error": tr("Keine gültige DOI")}), 400
    if len(claim) < 12:
        return jsonify({"error": tr("Bitte eine Aussage eingeben, mindestens ein paar Wörter.")}), 400
    result = support.find(doi, claim[:1000], k=min(max(int(data.get("k") or 3), 1), 8),
                          translated=str(data.get("translated") or "")[:1500] or None)
    if not result["available"] and not result.get("wrong_file"):  # bei einem anderen Werk bleibt dessen Hinweis
        result["error"] = (tr("Das PDF ließ sich nicht lesen.") if result.get("unreadable")
                           else tr("Das PDF zu dieser Quelle liegt nicht in der Bibliothek. Erst laden, dann suchen."))
    return jsonify(result)


@app.route("/api/watch")
def watch_status():
    return jsonify(watch.status())


@app.route("/api/watch/run", methods=["POST"])
def watch_run():
    """Rückzugswache sofort laufen lassen, für alle Projekte oder eines (im Hintergrund)."""
    pid = str((request.get_json(silent=True) or {}).get("project") or "")
    if not watch.status()["running"]:
        kwargs = {"force": True}
        if pid:
            kwargs["dois"] = watch._project_dois(only=pid)
        threading.Thread(target=watch.run, kwargs=kwargs, daemon=True).start()
    return jsonify(watch.status() | {"running": True})


@app.route("/api/watch/seen", methods=["POST"])
def watch_seen():
    ids = (request.get_json(silent=True) or {}).get("ids")
    return jsonify({"marked": watch.mark_seen([str(i) for i in ids] if isinstance(ids, list) else None)})


@app.route("/api/journal-lists")
def journal_lists():
    return jsonify(questionable.status())


@app.route("/api/zotero")
def zotero_status():
    return jsonify(zotero.status())


@app.route("/api/zotero/send", methods=["POST"])
def zotero_send():
    """Geprüfte Quellen (DOIs oder ein Projekt) als RIS an die offene Sammlung in Zotero schicken."""
    data = request.get_json(silent=True) or {}
    if data.get("project"):
        proj = projects.detail(str(data["project"]))
        if not proj:
            return jsonify({"error": tr("Projekt nicht gefunden")}), 404
        dois = [i["doi"] for i in proj["items"] if i.get("verdict") in projects.REAL or not i.get("verdict")]
    else:
        dois = [d for d in (library.normalize_doi(str(x)) for x in data.get("dois") or []) if d]
    dois = list(dict.fromkeys(dois))[:500]
    if not dois:
        return jsonify({"error": tr("Keine bestätigten Quellen zum Senden.")}), 400
    if not zotero.status()["running"]:
        return jsonify({"error": tr("Zotero läuft nicht. Öffne Zotero und versuche es noch einmal.")}), 503
    text, missing = biblio.export(dois, "ris")
    try:
        out = zotero.send(text)
    except zotero.ZoteroError as e:
        return jsonify({"error": str(e)}), e.status
    return jsonify(out | {"missing": missing, "target": zotero.status().get("target")})


@app.route("/api/zotero/scan", methods=["POST"])
def zotero_scan():
    """Zotero-Bibliothek (nur lesend, aus einer Kopie) auf Rückzüge, Bedenken und Korrekturen prüfen."""
    try:
        return jsonify(zotero.scan_library())
    except zotero.ZoteroError as e:
        return jsonify({"error": str(e)}), e.status


@app.route("/api/format")
def format_reference():
    """Die Angabe zu einer DOI, formatiert im gewünschten Zitierstil (über doi.org)."""
    doi = library.normalize_doi(request.args.get("doi", ""))
    if not doi:
        return jsonify({"error": tr("Keine gültige DOI")}), 400
    lang = "de-DE" if i18n.current() == "de" else "en-GB"
    text, missing = biblio.export([doi], "text", request.args.get("style") or "apa", locale=lang)
    if missing or not text.strip():
        return jsonify({"error": tr("doi.org hat keine formatierte Angabe geliefert. Später erneut versuchen.")}), 502
    return jsonify({"doi": doi, "text": text.strip()})


@app.route("/api/refs/parse", methods=["POST"])
def parse_refs():
    """Vorschau beim Einfügen (ohne Netzwerk): was wurde erkannt?"""
    data = request.get_json(silent=True) or {}
    items = refs.parse(str(data.get("text", "")))
    return jsonify({"summary": refs.summary(items), "items": [
        {k: r.get(k) for k in ("label", "doi", "pmid", "title", "year", "authors", "format")} for r in items[:200]
    ]})


@app.route("/api/jobs/<job_id>")
def job_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return jsonify({"error": tr("Unbekannter Lauf")}), 404
        out = dict(job)
        out.pop("docx_bytes", None)
    if out.get("status") == "running":  # pausierte Quellen, für den Hinweis im Fortschritt
        out["paused"] = [{"service": k, "name": quota.SERVICES[k]["name"], **v}
                         for k in ("openalex", "crossref", "semanticscholar", "unpaywall")
                         for v in [quota.paused(k)] if v]
    return jsonify(out)


@app.route("/api/jobs")
def list_jobs():
    """Laufende und letzte Läufe (die App folgt so auch Läufen, die ein LLM startet)."""
    with _jobs_lock:
        items = [{k: j.get(k) for k in ("id", "mode", "status", "origin", "project", "project_name",
                                          "total", "done", "created_at")} for j in _jobs.values()]
    items.sort(key=lambda j: j.get("created_at") or 0, reverse=True)
    return jsonify({"jobs": items[:20]})


@app.before_request
def _track_clients():
    client = request.headers.get("X-Benthic-Client")
    if client:
        _note_activity(client)


@app.route("/api/activity", methods=["GET"])
def get_activity():
    now = time.time()
    with _activity_lock:
        clients = [dict(c, connected=now - c["last_seen"] < 300) for c in _activity["clients"].values()]
        events = list(reversed(_activity["events"][-20:]))
    return jsonify({"clients": clients, "events": events})


@app.route("/api/activity", methods=["POST"])
def post_activity():
    data = request.get_json(silent=True) or {}
    _note_activity(request.headers.get("X-Benthic-Client") or "mcp", str(data.get("tool") or ""),
                   str(data.get("summary") or ""))
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# KI-Anbindung (MCP)
# --------------------------------------------------------------------------
@app.route("/api/mcp")
def mcp_info():
    claude = connect.claude_desktop()
    try:
        launcher = connect.write_launcher()
        assistants = connect.assistants(launcher)
    except OSError:
        launcher, assistants = None, []
    return jsonify({"claude_desktop": str(claude) if claude else None, "configs": connect.client_configs(),
                    "launcher": str(launcher) if launcher else None, "assistants": assistants,
                    "tools": [{"name": t["name"], "title": t["title"]} for t in __import__("benthic.mcp_server", fromlist=["TOOLS"]).TOOLS]})


@app.route("/api/mcp/connect", methods=["POST"])
def mcp_connect():
    """Erzeugt Benthic.mcpb und öffnet es in Claude Desktop (Installationsdialog)."""
    s = config.load_settings()
    bundle = connect.build_bundle()
    opened = connect.open_in_claude(bundle)
    return jsonify({"bundle": str(bundle), "opened": opened,
                    "claude_desktop": bool(connect.claude_desktop())})


@app.route("/api/mcp/install", methods=["POST"])
def mcp_install():
    """Trägt Benthic in die Konfiguration eines Assistenten ein (ChatGPT/Codex, Antigravity, Gemini CLI)."""
    target = str((request.get_json(silent=True) or {}).get("id") or "")
    if target not in connect.TARGETS:
        return jsonify({"error": tr("Diesen Assistenten richtet Benthic nicht selbst ein.")}), 400
    try:
        launcher = connect.write_launcher()
        done = connect.install_entry(target, launcher)
    except (OSError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, **done, "state": connect.entry_state(target, launcher)})


@app.route("/api/mcp/bundle")
def mcp_bundle():
    s = config.load_settings()
    bundle = connect.build_bundle()
    return send_file(str(bundle), as_attachment=True, download_name="Benthic.mcpb",
                     mimetype="application/zip")


# --------------------------------------------------------------------------
# Projekte
# --------------------------------------------------------------------------
@app.route("/api/projects", methods=["GET"])
def get_projects():
    return jsonify(projects.list_projects())


@app.route("/api/projects", methods=["POST"])
def create_project():
    data = request.get_json(silent=True) or {}
    try:
        p = projects.create(str(data.get("name") or ""), str(data.get("description") or ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if data.get("activate", True):
        projects.set_active(p["id"])
    return jsonify(projects.detail(p["id"]))


@app.route("/api/projects/active", methods=["POST"])
def set_active_project():
    data = request.get_json(silent=True) or {}
    return jsonify({"active": projects.set_active(data.get("id"))})


@app.route("/api/projects/<pid>", methods=["GET"])
def get_project(pid: str):
    d = projects.detail(pid)
    return jsonify(d) if d else (jsonify({"error": tr("Projekt nicht gefunden")}), 404)


@app.route("/api/projects/<pid>", methods=["POST"])
def update_project(pid: str):
    data = request.get_json(silent=True) or {}
    try:
        projects.update(pid, data.get("name"), data.get("description"),
                        bool(data["watch"]) if "watch" in data else None)
    except KeyError:
        return jsonify({"error": tr("Projekt nicht gefunden")}), 404
    return jsonify(projects.detail(pid))


@app.route("/api/projects/<pid>/check", methods=["POST"])
def check_project(pid: str):
    """Alle Quellen eines Projekts neu prüfen (mit den bekannten Titeln und Jahren)."""
    proj = projects.detail(pid)
    if not proj:
        return jsonify({"error": tr("Projekt nicht gefunden")}), 404
    identifiers = []
    for it in proj["items"]:
        ref = refs._empty("text", " ".join(str(x) for x in (it.get("title"), it.get("year"), "doi:" + it["doi"]) if x))
        ref.update(doi=it["doi"], title=it.get("title"), year=str(it["year"]) if it.get("year") else None)
        ref["label"] = refs.label(ref)
        identifiers.append(ref)
    if not identifiers:
        return jsonify({"error": tr("Das Projekt hat noch keine Quellen.")}), 400
    job_id, _note = _launch_job("verify", identifiers, config.load_settings(), projects.find(pid),
                                {"origin": "project", "document": None})
    return jsonify({"job_id": job_id, "total": len(identifiers), "estimate": quota.estimate(len(identifiers))})


@app.route("/api/projects/<pid>/runs")
def project_runs(pid: str):
    """Die Läufe eines Projekts (aus dem Verlauf), neueste zuerst."""
    runs = []
    for item in history.load():
        if item.get("project") != pid:
            continue
        results = item.get("results") or []
        runs.append({"id": item.get("id"), "mode": item.get("mode"), "finished_at": item.get("finished_at"),
                     "total": item.get("total") or len(results), "origin": item.get("origin"),
                     "document": (item.get("document") or {}).get("name"),
                     "counts": {v: sum(1 for r in results if r.get("verdict") == v) for v in verify.VERDICTS}})
    return jsonify({"runs": runs[:30]})


@app.route("/api/projects/<pid>", methods=["DELETE"])
def delete_project(pid: str):
    return jsonify({"deleted": projects.delete(pid)})


@app.route("/api/projects/<pid>/items", methods=["POST"])
def add_project_items(pid: str):
    data = request.get_json(silent=True) or {}
    proj = projects.find(pid)
    if not proj:
        return jsonify({"error": tr("Projekt nicht gefunden")}), 404
    dois = data.get("dois")
    if not isinstance(dois, list):
        dois = [str(r["doi"]) for r in refs.parse(str(data.get("text") or "")) if r.get("doi")]
    n = projects.add_items(proj["id"], [{"doi": d} for d in dois], source=request.headers.get("X-Benthic-Client") or "ui")
    return jsonify({"added": n, "project": projects.detail(proj["id"])})


@app.route("/api/health", methods=["POST"])
def reference_health():
    """Gesundheit einer Literaturliste: Echtheit, Aktualität, Publikationsorte, Zugang.

    Body: {"items": [{doi, year, verdict, has_pdf?}]} oder {"project": id}.
    """
    data = request.get_json(silent=True) or {}
    items = data.get("items")
    if data.get("project"):
        proj = projects.detail(str(data["project"]))
        if not proj:
            return jsonify({"error": tr("Projekt nicht gefunden")}), 404
        items = [{"doi": i["doi"], "year": i.get("year"), "verdict": i.get("verdict"),
                  "has_pdf": bool(i.get("filename"))} for i in proj["items"]]
    if not isinstance(items, list):
        return jsonify({"error": "`items` must be a list"}), 400
    s = config.load_settings()
    return jsonify(journals.assess(items[:500], s.get("unpaywall_email", ""), bool(s.get("journal_jufo", True))))


@app.route("/api/rankings", methods=["GET"])
def get_rankings():
    return jsonify({"rankings": journals.list_rankings()})


@app.route("/api/rankings", methods=["POST"])
def add_ranking():
    """Eigene Rankingliste (CSV oder Excel) importieren: Formularfelder `file` und `name`."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": tr("Bitte eine CSV- oder Excel-Datei wählen.")}), 400
    data = f.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        return jsonify({"error": tr("Die Datei ist größer als 8 MB.")}), 400
    name = (request.form.get("name") or Path(f.filename).stem).strip()[:80]
    try:
        meta = journals.import_ranking(name, f.filename, data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:  # noqa: BLE001 - kaputte Excel-Datei o. Ä.
        return jsonify({"error": tr("Die Datei ließ sich nicht lesen. CSV oder XLSX?")}), 400
    return jsonify(meta)


@app.route("/api/rankings/<list_id>", methods=["DELETE"])
def delete_ranking(list_id: str):
    if not journals.delete_ranking(list_id):
        return jsonify({"error": tr("Liste nicht gefunden")}), 404
    return jsonify({"ok": True})


@app.route("/api/projects/<pid>/bibliography")
def project_bibliography(pid: str):
    proj = projects.detail(pid)
    if not proj:
        return jsonify({"error": tr("Projekt nicht gefunden")}), 404
    fmt = request.args.get("format", "bibtex").lower()
    style = request.args.get("style", "apa")
    only_real = request.args.get("only", "verified") == "verified"
    dois = [i["doi"] for i in proj["items"]
            if not only_real or i.get("verdict") in projects.REAL or not i.get("verdict")]
    text, missing = biblio.export(dois, fmt, style, request.args.get("locale", "en-US"))
    if request.args.get("as") == "json":
        return jsonify({"text": text, "missing": missing, "count": len(dois) - len(missing)})
    ext = {"bibtex": "bib", "ris": "ris"}.get(fmt, "txt")
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", proj["name"]).strip("-") or "projekt"
    return Response(text, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={name}.{ext}"})


@app.route("/api/projects/<pid>/bundle")
def project_bundle(pid: str):
    """Ein Projekt als Datei zum Weitergeben (Betreuung, Lerngruppe): Quellen mit Urteil, ohne PDFs und Pfade."""
    out = projects.bundle(pid)
    if not out:
        return jsonify({"error": tr("Projekt nicht gefunden")}), 404
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", out["name"]).strip("-") or "projekt"
    return Response(json.dumps(out, ensure_ascii=False, indent=1), mimetype="application/json; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={name}.benthic.json"})


@app.route("/api/projects/import", methods=["POST"])
def project_import():
    """Ein geteiltes Projekt (Datei aus /api/projects/<pid>/bundle) als neues Projekt anlegen."""
    upload = request.files.get("file")
    try:
        raw = upload.read(5_000_000) if upload else request.get_data(cache=False)[:5_000_000]
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return jsonify({"error": tr("Das ist keine Projektdatei von Benthic.")}), 400
    try:
        return jsonify(projects.unbundle(data))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/update")
def get_update():
    """Gibt es eine neuere Version? (Manifest-Adresse aus Einstellungen oder Build)"""
    if updates.status().get("state") != "downloading":
        updates.cleanup()  # installierte Updates und halbe Downloads wegräumen
    return jsonify(updates.check(force=request.args.get("force") == "1"))


@app.route("/api/update/download", methods=["POST"])
def update_download():
    """Neue Version laden und prüfen (für das Aktualisieren aus der App)."""
    return jsonify(updates.download())


@app.route("/api/update/status")
def update_status():
    return jsonify(updates.status())


@app.route("/api/licenses")
def get_licenses():
    """Lizenz und Drittanbieter-Hinweise (im Bundle als .txt, im Projekt als Original)."""
    parts = []
    for names in (("LICENSE.txt", "LICENSE"), ("THIRD_PARTY_NOTICES.txt", "THIRD_PARTY_NOTICES.md")):
        for name in names:
            p = config.PROJECT_ROOT / name
            if p.is_file():
                parts.append(p.read_text(encoding="utf-8"))
                break
    return jsonify({"text": "\n\n".join(parts) or tr("Nicht gefunden.")})


@app.route("/api/stats")
def get_stats():
    """Kennzahlen für die Übersicht (optional je Projekt und Zeitraum)."""
    try:
        days = int(request.args["days"]) if request.args.get("days") else None
    except ValueError:
        days = None
    try:
        return jsonify(stats.compute(request.args.get("project") or None, days))
    except KeyError:
        return jsonify({"error": tr("Projekt nicht gefunden")}), 404


@app.route("/api/library/search")
def library_search():
    q = request.args.get("q", "")
    try:
        limit = max(1, min(int(request.args.get("limit", 25)), 200))
    except ValueError:
        limit = 25
    dois = None
    if request.args.get("project"):
        proj = projects.detail(request.args["project"])
        if not proj:
            return jsonify({"error": tr("Projekt nicht gefunden")}), 404
        dois = [i["doi"] for i in proj["items"]]
    return jsonify({"results": library.search(q, limit, dois)})


@app.route("/api/fulltext")
def get_fulltext():
    doi = library.normalize_doi(request.args.get("doi", ""))
    if not doi:
        return jsonify({"error": tr("Keine gültige DOI")}), 400
    try:
        start = int(request.args.get("start", 0))
        max_chars = int(request.args.get("max_chars", 30000))
    except ValueError:
        start, max_chars = 0, 30000
    return jsonify(biblio.fulltext(doi, start, max_chars))


@app.route("/api/projects/<pid>/items/<path:doi>", methods=["DELETE"])
def remove_project_item(pid: str, doi: str):
    return jsonify({"removed": projects.remove_item(pid, doi)})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id: str):
    ev = _cancel_events.get(job_id)
    if ev is None:
        return jsonify({"error": tr("Unbekannter Lauf")}), 404
    ev.set()
    return jsonify({"cancelled": True})


@app.route("/api/export", methods=["POST"])
def export_results():
    """Exportiert beliebige (angezeigte) Ergebnisse als CSV/XLSX."""
    data = request.get_json(silent=True) or {}
    fmt = str(data.get("format", "csv")).lower()
    results = data.get("results") or []
    if fmt == "xlsx":
        out = export.to_xlsx(results)
        return send_file(
            io.BytesIO(out),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="benthic-export.xlsx",
        )
    return Response(
        export.to_csv(results),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=benthic-export.csv"},
    )


@app.route("/api/jobs/<job_id>/export")
def export_job(job_id: str):
    fmt = request.args.get("format", "csv").lower()
    with _jobs_lock:
        job = _jobs.get(job_id)
    results = (job or {}).get("results")
    if results is None:
        item = history.get(job_id)
        results = (item or {}).get("results")
    if results is None:
        return jsonify({"error": tr("Unbekannter Lauf")}), 404

    if fmt == "xlsx":
        data = export.to_xlsx(results)
        return send_file(
            io.BytesIO(data),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"benthic-{job_id}.xlsx",
        )
    data = export.to_csv(results)
    return Response(
        data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=benthic-{job_id}.csv"},
    )


def _get_docx_bytes(job: Dict) -> Tuple[Optional[bytes], str]:
    if job.get("docx_bytes"):
        return job["docx_bytes"], job.get("docx_name") or "dokument.docx"
    if job.get("docx_path"):
        p = Path(job["docx_path"])
        if p.is_file():
            try:
                return p.read_bytes(), p.name
            except Exception:
                pass
    doc = job.get("document") or {}
    name = doc.get("name") or "dokument.docx"
    return None, name


@app.route("/api/jobs/<job_id>/cleaned.bib")
def export_cleaned_bib_route(job_id: str):
    """Bereinigte .bib-Datei mit geprüften Metadaten und korrigierten DOIs."""
    job = _job_record(job_id)
    if not job:
        return jsonify({"error": tr("Unbekannter Lauf")}), 404
    results = job.get("results")
    if results is None:
        return jsonify({"error": tr("Unbekannter Lauf")}), 404
    bib_text = latex.export_cleaned_bib(results)
    return Response(
        bib_text,
        mimetype="application/x-bibtex",
        headers={"Content-Disposition": f"attachment; filename=benthic-{job_id}-cleaned.bib"},
    )


def _quote_comment(q: Dict) -> Optional[str]:
    """Randkommentar zu einem wörtlichen Zitat im Word-Export, oder None, wenn es nichts zu sagen gibt."""
    if q.get("status") == "misattributed":
        other = q.get("found_in") or {}
        return tr("Steht nicht in der zitierten Quelle, aber wortgleich in {other} (S. {page}).",
                  other=other.get("label", ""), page=other.get("page", "?"))
    if q.get("status") == "close":
        return tr("Wörtliches Zitat weicht vom Original ab.")
    if q.get("status") == "not_found":
        return tr("Wörtliches Zitat im Volltext nicht gefunden.")
    if q.get("page_ok") is False:
        return tr("Seitenzahl des Zitats weicht ab.")
    return None


@app.route("/api/jobs/<job_id>/annotated.docx")
def export_annotated_docx(job_id: str):
    """Kommentiertes Word-Dokument mit Hinweisen als native Word-Randkommentare."""
    job = _job_record(job_id)
    if not job:
        return jsonify({"error": tr("Unbekannter Lauf")}), 404
    data, name = _get_docx_bytes(job)
    if not data:
        return jsonify({"error": tr("Keine Word-Datei zu diesem Lauf vorhanden.")}), 404

    doc = job.get("document") or {}
    results = {r.get("order"): r for r in (job.get("results") or []) if r.get("order") is not None}
    notes = []
    commented_entries = set()

    for claim in doc.get("claims") or []:
        entry_idx = claim.get("entry")
        res = results.get(entry_idx)
        if res:
            verdict = res.get("verdict")
            if verdict in ("retracted", "not_found", "mismatch", "corrected", "partial"):
                note_text = f"[{verdict.upper()}] {res.get('note') or ''}".strip()
                diffs = res.get("field_diffs") or {}
                if isinstance(diffs, dict):
                    bad_diffs = [
                        f"{k}: {v.get('claimed')} -> {v.get('found')}"
                        for k, v in diffs.items()
                        if isinstance(v, dict) and v.get("status") != "ok"
                    ]
                    if bad_diffs:
                        note_text += f" ({'; '.join(bad_diffs)})"
                notes.append({
                    "needle": claim.get("sentence") or claim.get("label") or "",
                    "text": note_text,
                    "author": "Benthic",
                })
                commented_entries.add(entry_idx)

    for order_idx, res in sorted(results.items()):
        if order_idx in commented_entries:
            continue
        verdict = res.get("verdict")
        if verdict in ("retracted", "not_found", "mismatch", "corrected", "partial"):
            needle = (res.get("claimed") or {}).get("title") or res.get("input") or res.get("identifier") or ""
            if needle:
                notes.append({
                    "needle": str(needle)[:80],
                    "text": f"[{verdict.upper()}] {res.get('note') or ''}".strip(),
                    "author": "Benthic",
                })

    for ym in doc.get("year_mismatch") or []:
        notes.append({
            "needle": ym.get("sentence") or ym.get("label") or "",
            "text": f"Jahresabweichung: Im Text {ym.get('cited_year')}, im Verzeichnis {ym.get('list_year')} ({ym.get('list_label')})",
            "author": "Benthic",
        })

    for m in doc.get("missing_in_list") or []:
        notes.append({
            "needle": m.get("sentence") or m.get("label") or "",
            "text": f"Fehlt im Literaturverzeichnis: {m.get('label')}",
            "author": "Benthic",
        })

    for uc in doc.get("uncited_claims") or []:
        notes.append({
            "needle": uc.get("sentence") or uc.get("claim") or "",
            "text": "Aussage mit Zahlen oder Studienbezug ohne Zitat: Bitte mit einer Quelle belegen.",
            "author": "Benthic",
        })
    for sc in doc.get("secondary_citations") or []:
        notes.append({
            "needle": sc.get("match") or sc.get("sentence") or sc.get("needle") or "",
            "text": f"Zweitquelle ({sc.get('match', '')}): Nach Möglichkeit das Original prüfen und zitieren.",
            "author": "Benthic",
        })
    for q in job.get("quote_check") or []:
        q_note = _quote_comment(q)
        if q_note:
            notes.append({
                "needle": q.get("quote") or "",
                "text": tr("Zitatprüfung ({label}): {note}", label=q.get("label", ""), note=q_note),
                "author": "Benthic",
            })

    annotated = document.annotate_docx(data, notes)
    stem = Path(name).stem or "dokument"
    return send_file(
        io.BytesIO(annotated),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=f"{stem}-benthic-kommentiert.docx",
    )


# --------------------------------------------------------------------------
# Verlauf
# --------------------------------------------------------------------------
@app.route("/api/history")
def get_history():
    return jsonify({"summaries": history.summaries(), "stats": history.stats()})


@app.route("/api/history/<job_id>")
def get_history_item(job_id: str):
    item = history.get(job_id)
    if not item:
        return jsonify({"error": tr("Unbekannter Lauf")}), 404
    return jsonify(item)


# --------------------------------------------------------------------------
# Bibliothek (lernende DOI-Datenbank)
# --------------------------------------------------------------------------
@app.route("/api/library")
def library_info():
    entries = library.entries_list()
    return jsonify({"count": len(entries), "stats": history.stats(), "entries": entries[:300]})


@app.route("/api/library/import", methods=["POST"])
def library_import():
    data = request.get_json(silent=True) or {}
    if data.get("sources"):
        results = []
        for path in config.library_sources():
            try:
                res = library.import_from_file(str(path))
                results.append({"source": str(path), **res})
            except Exception as exc:  # eine defekte Quelle darf den Import nicht stoppen
                results.append({"source": str(path), "error": str(exc)})
        return jsonify({"results": results, "count": library.count()})
    if data.get("path"):
        try:
            res = library.import_from_file(str(data["path"]))
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({**res, "count": library.count()})
    res = library.import_from_text(str(data.get("text", "")))
    return jsonify({**res, "count": library.count()})


@app.route("/api/library/check", methods=["POST"])
def library_check():
    """Schneller Abgleich pasteter DOIs gegen die Bibliothek (ohne Netzwerk)."""
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    identifiers = _parse_identifiers(str(text)) if not isinstance(text, list) else [str(i) for i in text]
    out = []
    for ident in identifiers:
        entry = library.get(ident)
        out.append({
            "identifier": ident,
            "present": bool(entry),
            "title": entry.get("title") if entry else None,
            "filename": entry.get("filename") if entry else None,
        })
    return jsonify({"count": len(out), "results": out})


@app.route("/api/library/clear", methods=["POST"])
def library_clear():
    return jsonify({"cleared": library.clear()})


# --------------------------------------------------------------------------
# Downloads
# --------------------------------------------------------------------------
@app.route("/api/files")
def list_files():
    settings = config.load_settings()
    output_dir, _note = config.resolve_download_dir(settings)
    files = []
    for p in sorted(output_dir.iterdir()):
        if p.is_file() and not p.name.startswith("."):
            st = p.stat()
            files.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
    return jsonify({"files": files, "dir": str(output_dir)})


def create_app():
    return app


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("BENTHIC_PORT", "8327")), debug=False, threaded=True)
