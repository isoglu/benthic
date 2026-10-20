"""Stapelprüfung für Lehrende: einen Ordner mit Abgaben prüfen, eine Übersicht je Arbeit, ein Bericht je Datei.

Benthic geht die Dateien nacheinander durch (PDF, Word, Text, Markdown, LaTeX als .zip oder als Ordner
mit einer .tex-Datei), prüft jede wie eine einzelne Arbeit und schreibt die Ergebnisse in
``batches/<id>.json`` im Datenordner. Die vollständigen Läufe liegen dort mit, damit der Bericht je Datei
auch dann erhalten bleibt, wenn der Verlauf (50 Läufe) weiterrückt.

Optionen: Unterordner einbeziehen, wörtliche Zitate prüfen (lädt dafür die frei verfügbaren PDFs der
zitierten Quellen), Ergebnisse in ein neues Projekt übernehmen. Jede Datei steht von Anfang an in der
Liste (wartet, prüft, fertig, Fehler), mit Dauer und einer Schätzung der Restzeit.

Datenschutz: Die Arbeiten verlassen den Mac nicht. Wie bei jeder Prüfung gehen nur die Literaturangaben
(DOI, Titel, Autor:innen) an Crossref, OpenAlex, Semantic Scholar und arXiv.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import config

TYPES = (".pdf", ".docx", ".txt", ".md", ".markdown", ".tex", ".ltx", ".zip")
MAX_FILES = 300
BAD = ("not_found", "mismatch", "retracted")

_lock = threading.RLock()
_cancel: Dict[str, threading.Event] = {}
_hooks: Dict[str, Callable] = {}


def configure(analyse, launch, job, stop_job, quotes=None, fetch=None, project=None) -> None:
    """Prüfen, Läufe und Zusatzschritte übernimmt die App (keine Kreisimporte)."""
    _hooks.update(analyse=analyse, launch=launch, job=job, stop_job=stop_job, quotes=quotes, fetch=fetch,
                  project=project)


def _dir() -> Path:
    d = config.data_dir() / "batches"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(batch_id: str) -> Path:
    return _dir() / f"{batch_id}.json"


def load(batch_id: str) -> Optional[Dict]:
    if not batch_id or "/" in batch_id or ".." in batch_id:
        return None
    try:
        return json.loads(_file(batch_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save(batch: Dict) -> None:
    p = _file(batch["id"])
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(batch, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _is_project(folder: Path) -> bool:
    return any(q.suffix.lower() in (".tex", ".ltx") for q in folder.iterdir() if q.is_file())


def submissions(folder: Path, recursive: bool = False) -> List[Path]:
    """Die Arbeiten in einem Ordner: Dateien der bekannten Typen und Ordner mit einer .tex-Datei.

    Mit ``recursive`` auch in Unterordnern (ein Ordner je Abgabe ist häufig); ein Ordner mit .tex-Datei
    gilt als ein LaTeX-Projekt und wird nicht weiter zerlegt.
    """
    out: List[Path] = []

    def walk(d: Path, depth: int) -> None:
        for p in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            if len(out) >= MAX_FILES or p.name.startswith((".", "~$", "__MACOSX")):
                continue
            if p.is_file() and p.suffix.lower() in TYPES:
                if p.suffix.lower() in (".tex", ".ltx"):
                    continue  # .tex-Dateien zählen über ihren Ordner
                out.append(p)
            elif p.is_dir():
                if _is_project(p):
                    out.append(p)
                elif recursive and depth < 4:
                    walk(p, depth + 1)
    if _is_project(folder):
        return [folder]
    walk(folder, 0)
    return out


def summary_row(name: str, job: Dict) -> Dict:
    """Eine Zeile der Übersicht aus einem fertigen Lauf, mit den Problemen im Einzelnen."""
    doc = job.get("document") or {}
    results = job.get("results") or []
    count = lambda v: sum(1 for r in results if r.get("verdict") == v)  # noqa: E731
    style = job.get("style") or {}
    style_notes = sum(1 for v in (style.get("entries") or {}).values() if v) if style.get("applicable") else 0
    quotes = job.get("quote_check") or []
    row = {"name": name, "entries": doc.get("entries", len(results)), "citations": doc.get("citations", 0),
           "missing": len(doc.get("missing_in_list") or []), "uncited": len(doc.get("uncited") or []),
           "verified": sum(1 for r in results if r.get("verdict") in ("verified", "corrected")),
           "partial": count("partial") + count("uncertain"), "not_found": count("not_found"),
           "mismatch": count("mismatch"), "retracted": count("retracted"), "style": style_notes,
           "quotes": len(doc.get("quotes") or []), "quotes_bad": sum(1 for q in quotes if q.get("status") in ("not_found", "close")),
           "cross_check": bool(doc.get("cross_check")), "style_name": (style.get("styles") or {}).get(style.get("style"))}
    row["flagged"] = row["missing"] + row["not_found"] + row["mismatch"] + row["retracted"] + row["quotes_bad"]
    label = lambda r: (r.get("claimed") or {}).get("title") or r.get("identifier") or (r.get("input") or "")[:90]  # noqa: E731
    row["problems"] = {
        "missing": [m["label"] for m in (doc.get("missing_in_list") or [])][:12],
        "not_found": [label(r) for r in results if r.get("verdict") == "not_found"][:12],
        "mismatch": [label(r) for r in results if r.get("verdict") == "mismatch"][:12],
        "retracted": [label(r) for r in results if r.get("verdict") == "retracted"][:12],
        "quotes": [q.get("quote", "")[:120] for q in quotes if q.get("status") in ("not_found", "close")][:8],
    }
    return row


# --------------------------------------------------------------------------
# Export einer Aufgabe aus Moodle oder ILIAS (ein .zip mit einem Ordner je Abgabe)
# --------------------------------------------------------------------------
# Moodle: "Vorname Nachname_123456_assignsubmission_file_/arbeit.pdf"
_MOODLE = re.compile(r"^(?P<name>.+?)_\d+_assign(?:submission|feedback)_[a-z]+_?$", re.IGNORECASE)
MAX_UNPACKED = 1_500_000_000  # Bytes: ein Kurs mit vielen PDFs, aber keine Zip-Bombe


def unpack(zip_path: Path, target: Path) -> None:
    """Ein LMS-Export in den Datenordner entpacken, ohne Pfade außerhalb des Ziels und ohne versteckte Dateien."""
    total = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            parts = [p for p in Path(info.filename.replace("\\", "/")).parts if p not in ("", ".", "..", "/")]
            if info.is_dir() or not parts or parts[0] == "__MACOSX" or any(p.startswith(".") for p in parts):
                continue
            total += info.file_size
            if total > MAX_UNPACKED:
                raise ValueError("size")
            dest = target.joinpath(*parts)
            if target.resolve() not in dest.resolve().parents:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)


def display_name(rel: str) -> str:
    """Moodle-Ordner als Name der Person: „Ada Lovelace · arbeit.pdf“; sonst der Pfad im Export."""
    parts = Path(rel).parts
    m = _MOODLE.match(parts[0]) if parts else None
    if m:
        return " · ".join([m.group("name"), *parts[1:]]) if len(parts) > 1 else m.group("name")
    return rel


def start(folder: str, recursive: bool = False, quotes: bool = False, project: bool = False) -> Dict:
    root = Path(folder).expanduser()
    batch_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    source = root
    if root.is_file() and root.suffix.lower() == ".zip":  # Export einer Aufgabe aus Moodle oder ILIAS
        target = _dir() / f"{batch_id}-files"
        try:
            unpack(root, target)
        except (zipfile.BadZipFile, OSError):
            shutil.rmtree(target, ignore_errors=True)
            raise ValueError("zip")
        root, recursive = target, True
    if not root.is_dir():
        raise ValueError("folder")
    files = submissions(root, recursive)
    if not files:
        raise ValueError("empty")
    batch = {"id": batch_id, "folder": str(source),
             "name": source.stem if source.is_file() else root.name, "created_at": time.time(), "status": "running",
             "total": len(files), "done": 0, "current": None,
             "options": {"recursive": recursive, "quotes": quotes, "project": project, "lms": source.is_file()},
             "project": None,
             "rows": [{"name": display_name(str(p.relative_to(root))), "path": str(p), "state": "queued"} for p in files]}
    if project and _hooks.get("project"):
        batch["project"] = _hooks["project"](batch["name"])
    _save(batch)
    _cancel[batch["id"]] = threading.Event()
    threading.Thread(target=_run, args=(batch["id"], [i for i in range(len(files))]), name="batch", daemon=True).start()
    return public(batch)


def _update_row(batch_id: str, index: int, **fields) -> None:
    with _lock:
        batch = load(batch_id) or {}
        rows = batch.get("rows") or []
        if 0 <= index < len(rows):
            rows[index].update(fields)
        batch["done"] = sum(1 for r in rows if r.get("state") in ("done", "error", "skipped"))
        _save(batch)


def _check_one(batch: Dict, index: int, cancel: threading.Event) -> None:
    row = batch["rows"][index]
    path = Path(row["path"])
    started = time.time()
    _update_row(batch["id"], index, state="checking", started_at=started, error=None)
    with _lock:
        b = load(batch["id"]) or {}
        b["current"] = row["name"]
        _save(b)
    result: Dict = {}
    try:
        analysis = _hooks["analyse"](path)
        if not analysis.get("entries"):
            result["error"] = "no-bibliography"
        else:
            job_id = _hooks["launch"](path, analysis, batch.get("project"))
            job = None
            while job_id:
                job = _hooks["job"](job_id)
                if not job or job.get("status") != "running":
                    break
                if cancel.is_set() and _hooks.get("stop_job"):
                    _hooks["stop_job"](job_id)
                time.sleep(1.0)
            if job and (batch.get("options") or {}).get("quotes") and (job.get("document") or {}).get("quotes"):
                _update_row(batch["id"], index, step="quotes")
                if _hooks.get("fetch"):
                    _hooks["fetch"](job)  # freie PDFs der zitierten Quellen laden
                if _hooks.get("quotes"):
                    job = {**job, "quote_check": _hooks["quotes"](job_id, job)}
            if job:
                result.update(summary_row(row["name"], job))
                result["job_id"] = job_id
                result["job"] = {k: job.get(k) for k in ("id", "mode", "status", "results", "document", "style",
                                                         "quote_check", "created_at", "finished_at", "origin")}
    except ValueError as exc:
        result["error"] = str(exc) or "unreadable"
    except Exception:
        result["error"] = "unreadable"
    _update_row(batch["id"], index, state="error" if result.get("error") else "done", seconds=round(time.time() - started, 1),
                step=None, **result)


def _run(batch_id: str, indexes: List[int]) -> None:
    cancel = _cancel[batch_id]
    for index in indexes:
        if cancel.is_set():
            _update_row(batch_id, index, state="skipped")
            continue
        _check_one(load(batch_id) or {}, index, cancel)
    with _lock:
        batch = load(batch_id) or {}
        batch["status"] = "cancelled" if cancel.is_set() else "done"
        batch["current"] = None
        batch["finished_at"] = time.time()
        _save(batch)
    _cancel.pop(batch_id, None)


def rerun(batch_id: str, names: Optional[List[str]] = None) -> Dict:
    """Einzelne Dateien (oder alle mit Fehler) erneut prüfen."""
    batch = load(batch_id)
    if not batch:
        raise ValueError("unknown")
    if batch.get("status") == "running":
        raise ValueError("running")
    rows = batch.get("rows") or []
    indexes = [i for i, r in enumerate(rows) if (names and r["name"] in names) or (not names and r.get("state") == "error")]
    if not indexes:
        return public(batch)
    for i in indexes:
        rows[i] = {"name": rows[i]["name"], "path": rows[i]["path"], "state": "queued"}
    batch.update(status="running", finished_at=None)
    _save(batch)
    _cancel[batch_id] = threading.Event()
    threading.Thread(target=_run, args=(batch_id, indexes), name="batch", daemon=True).start()
    return public(batch)


def cancel(batch_id: str) -> bool:
    ev = _cancel.get(batch_id)
    if ev:
        ev.set()
    return bool(ev)


def totals(batch: Dict) -> Dict:
    rows = [r for r in batch.get("rows") or [] if r.get("state") == "done"]
    keys = ("entries", "citations", "missing", "uncited", "verified", "partial", "not_found", "mismatch", "retracted",
            "style", "quotes", "quotes_bad")
    out = {k: sum(int(r.get(k) or 0) for r in rows) for k in keys}
    out["files"] = len(batch.get("rows") or [])
    out["checked"] = len(rows)
    out["errors"] = sum(1 for r in batch.get("rows") or [] if r.get("state") == "error")
    out["flagged_files"] = sum(1 for r in rows if r.get("flagged"))
    out["retracted_files"] = sum(1 for r in rows if r.get("retracted"))
    return out


def _title_key(title: str) -> str:
    s = unicodedata.normalize("NFKD", str(title or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(w for w in re.findall(r"[a-z0-9]+", s) if len(w) > 2)


def shared_phantoms(batch: Dict) -> List[Dict]:
    """Erfundene oder falsch zugeordnete Quellen, die in mehreren Abgaben stehen: ein Hinweis auf dieselbe
    Chat-Antwort oder auf abgeschriebene Listen. Jeder Fund lässt sich nachprüfen (die Quelle gibt es nicht)."""
    groups: Dict[str, Dict] = {}
    for r in batch.get("rows") or []:
        for res in (r.get("job") or {}).get("results") or []:
            if res.get("verdict") not in ("not_found", "mismatch"):
                continue
            title = str((res.get("claimed") or {}).get("title") or res.get("input") or "")
            key = _title_key(title)
            if len(key) < 15:
                continue
            g = groups.setdefault(key, {"title": title[:200], "verdict": res["verdict"], "files": []})
            if r["name"] not in g["files"]:
                g["files"].append(r["name"])
    shared = [g for g in groups.values() if len(g["files"]) >= 2]
    return sorted(shared, key=lambda g: (-len(g["files"]), g["title"]))[:40]


def class_summary(batch: Dict) -> Dict:
    """Wie viele Abgaben welches Problem haben, ohne Namen: für die Lehre, nicht für Einzelfälle."""
    rows = [r for r in batch.get("rows") or [] if r.get("state") == "done"]
    keys = ("not_found", "mismatch", "retracted", "missing", "quotes_bad", "style")
    return {"files": len(rows), "with": {k: sum(1 for r in rows if r.get(k)) for k in keys}}


def public(batch: Dict) -> Dict:
    """Ohne die vollständigen Läufe (die holt der Bericht je Datei); mit Summen, Restzeit und Signalen für den Kurs."""
    rows = [{k: v for k, v in r.items() if k != "job"} for r in batch.get("rows") or []]
    durations = [r["seconds"] for r in rows if r.get("seconds")]
    waiting = sum(1 for r in rows if r.get("state") in ("queued", "checking"))
    eta = round(sum(durations) / len(durations) * waiting) if durations and batch.get("status") == "running" else None
    return {**{k: v for k, v in batch.items() if k != "rows"}, "rows": rows, "totals": totals(batch), "eta_seconds": eta,
            "shared_phantoms": shared_phantoms(batch), "class_summary": class_summary(batch)}


def list_batches() -> List[Dict]:
    out = []
    for p in sorted(_dir().glob("*.json"), reverse=True)[:30]:
        b = load(p.stem)
        if b:
            out.append({k: b.get(k) for k in ("id", "name", "folder", "created_at", "status", "total", "done")})
    return out


def find_job(job_id: str) -> Optional[Dict]:
    """Ein Lauf aus einem Stapel (für den Bericht je Datei)."""
    for p in sorted(_dir().glob("*.json"), reverse=True):
        b = load(p.stem) or {}
        for r in b.get("rows") or []:
            if str(r.get("job_id")) == str(job_id) and r.get("job"):
                return r["job"]
    return None


COLUMNS = [("name", "Datei"), ("state", "Stand"), ("entries", "Einträge"), ("citations", "Zitate im Text"),
           ("missing", "Zitate ohne Eintrag"), ("uncited", "Einträge ohne Zitat"), ("verified", "Bestätigt"),
           ("partial", "Abweichend oder unsicher"), ("not_found", "Nicht auffindbar"), ("mismatch", "Passt nicht"),
           ("retracted", "Zurückgezogen"), ("style", "Stilhinweise"), ("style_name", "Zitierstil"),
           ("quotes", "Wörtliche Zitate"), ("quotes_bad", "Zitate abweichend oder nicht gefunden"),
           ("seconds", "Dauer (s)"), ("error", "Fehler")]
PROBLEM_COLUMNS = [("missing", "Zitate ohne Eintrag (Liste)"), ("not_found", "Nicht auffindbar (Liste)"),
                   ("mismatch", "Passt nicht (Liste)"), ("retracted", "Zurückgezogen (Liste)"),
                   ("quotes", "Auffällige Zitate (Liste)")]


def table(batch: Dict, tr: Callable[[str], str]) -> List[List[object]]:
    rows = [[tr(label) for _, label in COLUMNS + PROBLEM_COLUMNS]]
    errors = {"no-bibliography": tr("Kein Literaturverzeichnis erkannt"), "unreadable": tr("Nicht lesbar")}
    states = {"done": tr("geprüft"), "error": tr("Fehler"), "queued": tr("wartet"), "checking": tr("prüft"), "skipped": tr("ausgelassen")}
    for r in batch.get("rows") or []:
        line = []
        for k, _ in COLUMNS:
            v = r.get(k, "")
            line.append(errors.get(v, v) if k == "error" else states.get(v, v) if k == "state" else v)
        for k, _ in PROBLEM_COLUMNS:
            line.append("; ".join((r.get("problems") or {}).get(k) or []))
        rows.append(line)
    return rows


def to_csv(batch: Dict, tr: Callable[[str], str]) -> str:
    from .export import safe_cell
    buf = io.StringIO()
    csv.writer(buf, delimiter=";").writerows([safe_cell(v) for v in row] for row in table(batch, tr))
    return "﻿" + buf.getvalue()  # BOM: Excel erkennt UTF-8


def to_xlsx(batch: Dict, tr: Callable[[str], str]) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = (batch.get("name") or "Benthic")[:31]
    from .export import safe_cell
    for row in table(batch, tr):
        ws.append([safe_cell(v) for v in row])
    ws.freeze_panes = "B2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
