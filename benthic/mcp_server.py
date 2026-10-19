"""MCP-Server für Benthic: LLMs prüfen, laden und lesen Quellen, nur über Open Access.

Claude Desktop, Claude Code, Cursor oder LM Studio starten diesen Prozess und
sprechen über stdio mit ihm (JSON-RPC 2.0, eine Nachricht pro Zeile). Der
Server ist ein schlanker Client des Benthic-Backends (HTTP auf 127.0.0.1) und
startet es bei Bedarf selbst. So bleibt es bei einem Prozess, der Bibliothek,
Projekte und Verlauf schreibt, und die App zeigt live, was das LLM tut.

Werkzeuge nutzen nie Zusatzwege aus Plugins: Downloads laufen immer mit
``extra_routes: false``. Aufruf: ``python -m benthic.mcp_server`` (nur Standardbibliothek).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .i18n import catalog, tr, tr_part

PORT = int(os.environ.get("BENTHIC_PORT", "8327"))
BASE = f"http://127.0.0.1:{PORT}"
PROTOCOLS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
DEFAULT_DATA = Path.home() / "Library" / "Application Support" / "Benthic"

_client = "mcp"
_out_lock = threading.Lock()
_backend: Optional[subprocess.Popen] = None
_backend_lock = threading.Lock()
_INIT = Path(__file__).with_name("__init__.py")
_VERSION_RE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.M)
_noticed: set = set()

INSTRUCTIONS = (
    "Benthic checks and fetches academic sources. Typical workflow: (1) call verify_references on any "
    "reading list, LLM answer, BibTeX or RIS text to find out which sources are real, which DOIs are "
    "wrong and which papers were retracted (check_document does the same for a whole thesis as PDF or "
    "Word or a LaTeX project, compares the bibliography with the citations in the text, and check_quotes then "
    "compares its direct quotations with the sources); (2) call fetch_papers with the "
    "DOIs of real sources to download open-access PDFs into the user's library; (3) call get_fulltext to "
    "read a downloaded paper, or find_support to find the passage that comes closest to a claim; (4) use "
    "projects to keep sources per research question, export_bibliography for BibTeX, RIS or formatted "
    "references, and send_to_zotero for the user's Zotero. retraction_watch lists sources of the user's "
    "projects that were retracted or corrected since; check_zotero_library does this for the Zotero "
    "library. Benthic only uses open access and the user's own library proxy. Never present a "
    "reference as real unless verify_references confirmed it."
)


# --------------------------------------------------------------------------
# Backend
# --------------------------------------------------------------------------
def log(msg: str) -> None:
    sys.stderr.write(f"[benthic-mcp] {msg}\n")
    sys.stderr.flush()


def _http(method: str, path: str, body: Optional[Dict] = None, timeout: float = 30.0) -> Tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={
        "Content-Type": "application/json", "X-Benthic-Client": _client})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if "json" in resp.headers.get("Content-Type", ""):
                return resp.status, json.loads(raw)
            return resp.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"error": raw.decode("utf-8", "replace")[:300]}


def _health() -> Optional[Dict]:
    """Antwort von GET /api/health (mit der Version des Backends) oder None, wenn keines läuft."""
    try:
        status, data = _http("GET", "/api/health", timeout=2)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return data if status == 200 and isinstance(data, dict) and data.get("ok") else None


def ensure_backend() -> Dict:
    """Nutzt ein laufendes Benthic-Backend oder startet eines (es endet mit diesem Prozess).

    Gibt die Antwort von /api/health zurück, damit _call_tool die Versionen vergleichen kann.
    """
    global _backend
    with _backend_lock:
        health = _health()
        if health:
            return health
        if _backend is None or _backend.poll() is not None:
            env = dict(os.environ)
            env.setdefault("BENTHIC_DATA_DIR", str(DEFAULT_DATA))
            env["BENTHIC_PARENT_PID"] = str(os.getpid())
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            root = str(Path(__file__).resolve().parent.parent)
            env["PYTHONPATH"] = os.pathsep.join(p for p in (root, env.get("PYTHONPATH", "")) if p)
            code = ("from benthic.app import app, start_background; start_background(); "
                    f"app.run(host='127.0.0.1', port={PORT}, threaded=True)")
            log(f"starte Benthic-Backend auf Port {PORT}")
            _backend = subprocess.Popen([sys.executable, "-c", code], cwd=root, env=env,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
        for _ in range(60):
            health = _health()
            if health:
                return health
            time.sleep(0.25)
    raise RuntimeError("The Benthic backend did not start. Is Benthic installed?")


def _disk_version() -> Optional[str]:
    """Version der Dateien neben diesem Modul; nach einem Update von Benthic.app nicht mehr __version__."""
    try:
        match = _VERSION_RE.search(_INIT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return match.group(1) if match else None


def _version_notice(backend: Optional[str]) -> Optional[str]:
    """Eine Zeile für das LLM, wenn dieser Prozess und das installierte Benthic auseinanderlaufen.

    Der MCP-Prozess lebt so lange wie der Client. Nach einem Update der App bedient er weiter
    den alten Code, das Backend kommt aber aus den neuen Dateien, und die Antworten mischen
    beide Stände (19.10.2026: MCP 1.0.0 vor Backend 1.0.1 gab deutsche Stilhinweise aus).
    Sich selbst neu zu starten (os.execv) wäre bei stdio nicht sicher: Zeilen, die sys.stdin
    schon gepuffert hat, und laufende Werkzeug-Threads gingen verloren, und der Client behält
    Werkzeugliste und Anweisungen aus dem ersten initialize (listChanged: false). Darum nur
    ein Hinweis; der Neustart des Clients lädt alles sauber.
    """
    disk = _disk_version()
    if disk and disk != __version__:
        notice = (f"Note for the user: Benthic was updated to {disk}, but this connection still runs {__version__}. "
                  "Quit and reopen the app you are chatting in (for example Claude Desktop) to load the new tools; "
                  "until then answers may mix both versions.")
    elif backend and backend != __version__:
        notice = (f"Note for the user: the Benthic app runs version {backend}, but this connection runs {__version__}, "
                  "so answers may mix both versions. Quit and reopen Benthic and the app you are chatting in.")
    else:
        return None
    if notice not in _noticed:
        _noticed.add(notice)
        log(f"Versionen passen nicht zusammen: MCP {__version__}, Dateien {disk or '?'}, Backend {backend or '?'}")
    return notice


def _activity(tool: str, summary: str) -> None:
    try:
        _http("POST", "/api/activity", {"tool": tool, "summary": summary}, timeout=3)
    except (urllib.error.URLError, OSError):
        pass


def _wait_job(job_id: str, wait: float) -> Dict:
    deadline = time.monotonic() + max(1.0, min(wait, 110.0))
    while True:
        _status, job = _http("GET", f"/api/jobs/{job_id}")
        if not isinstance(job, dict) or job.get("status") in ("done", "cancelled") or time.monotonic() > deadline:
            return job if isinstance(job, dict) else {}
        time.sleep(0.7)


# --------------------------------------------------------------------------
# Ergebnis-Texte (für das LLM knapp und eindeutig)
# --------------------------------------------------------------------------
VERDICT_EN = {
    "verified": "VERIFIED", "corrected": "REAL, DOI CORRECTED", "partial": "REAL, DETAILS DIFFER",
    "uncertain": "UNCERTAIN", "mismatch": "DOI BELONGS TO ANOTHER WORK", "not_found": "NOT FOUND (likely fabricated)",
    "retracted": "RETRACTED",
}


def _fmt_verify(job: Dict) -> str:
    results = sorted(job.get("results") or [], key=lambda r: r.get("order", 0))
    counts: Dict[str, int] = {}
    lines = []
    for r in results:
        v = r.get("verdict", "uncertain")
        counts[v] = counts.get(v, 0) + 1
        found = r.get("found") or {}
        claimed = r.get("claimed") or {}
        title = found.get("title") or claimed.get("title") or r.get("identifier")
        bits = [f"{(r.get('order') or 0) + 1}. [{VERDICT_EN.get(v, v)}] {title}"]
        if found.get("year"):
            bits.append(f"({found['year']})")
        if r.get("doi"):
            bits.append(f"doi:{r['doi']}")
        line = " ".join(bits)
        if r.get("note"):
            line += f"\n   Note: {_en(r.get('note_parts') or r['note'])}"
        pub = r.get("published_version")
        if pub:
            line += (f"\n   Published since: doi:{pub['doi']} ({pub.get('venue') or pub.get('type')}, {pub.get('year')}). "
                     "Prefer citing the published version.")
        style_notes = ((job.get("style") or {}).get("entries") or {}).get(str(r.get("order", 0))) or []
        if style_notes:
            line += "\n   Citation style: " + " ".join(_en(i) for i in style_notes)
        lines.append(line)
    head = ", ".join(f"{n} {VERDICT_EN.get(v, v).lower()}" for v, n in counts.items())
    status = job.get("status")
    out = [f"Checked {len(results)} of {job.get('total', len(results))} references: {head}."]
    if status not in ("done", "cancelled"):
        out.append(f"Still running. Call get_job with job_id {job.get('id')} for the rest.")
    if job.get("project_name"):
        out.append(f"Saved to project “{job['project_name']}”.")
    st = job.get("style") or {}
    if st.get("applicable"):
        name = (st.get("styles") or {}).get(st.get("style"), st.get("style"))
        out.append(f"Citation style: {name}" + (" (detected)." if st.get("detected") == st.get("style") else ".")
                   + "".join(" " + _en(i) for i in st.get("list") or []))
    return "\n".join(out + [""] + lines)


def _en(note) -> str:
    """Ein Hinweis aus einem Lauf auf Englisch, wie der Rest dieser Ausgabe.

    Prüfhinweise (note_parts, eine Liste aus i18n.note_part) und Stilhinweise (style._note) tragen
    Vorlage und Werte. Andere Texte und ältere Läufe ohne note_parts wurden beim Prüfen in der Sprache
    der Oberfläche geschrieben und werden übersetzt, wenn sie ein fester Satz sind.
    """
    if isinstance(note, list):
        return " ".join(t for t in (_en(p) for p in note) if t)
    if isinstance(note, dict):
        if note.get("msg"):
            try:
                return tr_part(note, "en")
            except (KeyError, IndexError, ValueError):
                pass
        note = note.get("text") or ""
    text = str(note or "")
    return catalog("en").get(text, text)


STATUS_EN = {"ok": "DOWNLOADED", "skipped": "ALREADY IN LIBRARY", "blocked": "FREE COPY KNOWN, OPEN MANUALLY",
             "absent": "NO OPEN-ACCESS COPY FOUND", "error": "FAILED", "present": "ALREADY IN LIBRARY", "oa": "OPEN ACCESS AVAILABLE"}


def _fmt_download(job: Dict) -> str:
    results = job.get("results") or []
    lines = []
    for r in results:
        s = STATUS_EN.get(str(r.get("status")), str(r.get("status")).upper())
        line = f"- {r.get('identifier')}: {s}"
        if r.get("status") == "ok":
            line += f" via {r.get('source')}" + (f" ({r.get('version')})" if r.get("version") else "")
            if r.get("filename"):
                line += f", file {r['filename']}"
        if r.get("status") in ("blocked", "absent", "error") and (r.get("landing_url") or r.get("proxy_url")):
            line += f". Link: {r.get('proxy_url') or r.get('landing_url')}"
        if r.get("status") in ("absent", "error") and r.get("error"):
            line += f" ({_en(r['error'])})"
        lines.append(line)
    out = [f"{len(results)} of {job.get('total', len(results))} done (open access only)."]
    if job.get("status") not in ("done", "cancelled"):
        out.append(f"Still running. Call get_job with job_id {job.get('id')} for the rest.")
    if job.get("project_name"):
        out.append(f"Saved to project “{job['project_name']}”.")
    return "\n".join(out + [""] + lines)


# --------------------------------------------------------------------------
# Werkzeuge
# --------------------------------------------------------------------------
def _project_arg(args: Dict) -> Dict:
    return {"project": args["project"]} if args.get("project") else {}


def _start(path: str, body: Dict, wait: float) -> Dict:
    status, data = _http("POST", path, body)
    if status != 200:
        raise ValueError((data or {}).get("error") if isinstance(data, dict) else str(data))
    return _wait_job(str(data["job_id"]), wait)


def t_verify_references(a: Dict) -> str:
    text = str(a.get("text") or "").strip()
    if not text:
        raise ValueError("text is empty")
    _activity("verify_references", tr("prüft eine Literaturliste"))
    job = _start("/api/verify", {"text": text, **_project_arg(a)}, float(a.get("wait_seconds", 50)))
    return _fmt_verify(job)


def _fmt_document(job: Dict) -> str:
    d = job.get("document") or {}
    latex_doc = d.get("style") == "latex"
    head = [f"Document {d.get('name')}: {d.get('entries')} entries in the reference list, "
            f"{d.get('citations')} citations in the text."
            + (f" LaTeX project, {d.get('bib_entries')} entries in the .bib files." if latex_doc else "")]
    if not d.get("cross_check"):
        head.append("The in-text citations could not be read reliably (for example superscript numbers), "
                    "so they were not cross-checked against the list.")
    else:
        missing, uncited = d.get("missing_in_list") or [], d.get("uncited") or []
        years = d.get("year_mismatch") or []
        if years:
            head.append("Same author, different year in the text and in the list (probably a typo in one of them): "
                        + "; ".join(f"{y['label']} in the text, {y['list_year']} in the list" for y in years[:25]))
        if not missing and not uncited and not years:
            head.append("Every citation is in the reference list, and every entry is cited.")
        if missing:
            head.append("Cited in the text but missing from the reference list: "
                        + "; ".join(f"{m['label']} ({m['count']}x)" for m in missing[:25]))
        if uncited:
            head.append(("In the .bib files but not cited (they will not appear in the bibliography): " if latex_doc
                         else "In the reference list but never cited: ") + "; ".join(u["label"] for u in uncited[:25]))
    quotes = d.get("quotes") or []
    if quotes:
        head.append(f"{len(quotes)} direct quotations with a cited source; call check_quotes with job_id "
                    f"{job.get('id')} to compare them with the downloaded full texts.")
    return "\n".join(head) + "\n\n" + _fmt_verify(job)


def t_check_document(a: Dict) -> str:
    path = str(a.get("path") or "").strip()
    if not path:
        raise ValueError("path is empty")
    _activity("check_document", tr("prüft eine Arbeit: {name}", name=os.path.basename(path)))
    job = _start("/api/document", {"path": path, **_project_arg(a)}, float(a.get("wait_seconds", 90)))
    return _fmt_document(job)


QUOTE_EN = {"exact": "word for word", "close": "WORDING DIFFERS", "not_found": "NOT FOUND in the source",
            "no_pdf": "PDF not in the library", "no_source": "source not confirmed", "pending": "source not checked yet",
            "unreadable": "PDF unreadable", "too_short": "too short to check", "no_text": "PDF has no text layer"}


def _fmt_quote(q: Dict) -> str:
    line = f"- \"{q.get('quote', '')[:200]}\" ({q.get('label') or q.get('doi') or ''}"
    line += f", cited p. {q['cited_page']})" if q.get("cited_page") else ")"
    line += f": {QUOTE_EN.get(q.get('status'), q.get('status'))}"
    if q.get("status") in ("exact", "close"):
        where = q.get("printed_page") or q.get("page")
        line += f", found on p. {where}"
        if q.get("page_ok") is False:
            line += f" (cited page {q.get('stated_page')} is WRONG)"
    if q.get("status") == "close" and q.get("excerpt"):
        line += f". Source reads: \"{q['excerpt'][:300]}\""
    return line


def t_check_quotes(a: Dict) -> str:
    if a.get("quote"):
        status, r = _http("POST", "/api/quotes", {"doi": a.get("doi"), "quote": a.get("quote"), "page": a.get("page")})
        if status != 200:
            raise ValueError((r or {}).get("error") or "check failed")
        return _fmt_quote({**r, "quote": a.get("quote"), "cited_page": a.get("page")})
    job_id = str(a.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("give a job_id from check_document, or a doi and a quote")
    _activity("check_quotes", tr("prüft wörtliche Zitate"))
    status, r = _http("POST", "/api/quotes", {"job_id": job_id}, timeout=180.0)
    if status != 200:
        raise ValueError((r or {}).get("error") or "check failed")
    lines = [f"{len(r['quotes'])} direct quotations checked against the downloaded full texts:"]
    lines += [_fmt_quote(q) for q in r["quotes"]]
    if r.get("missing_pdfs"):
        lines.append("PDFs missing for: " + ", ".join(r["missing_pdfs"]) + ". Call fetch_papers with them, then check again.")
    return "\n".join(lines)


def t_suggest_missing_sources(a: Dict) -> str:
    if a.get("job_id"):
        body = {"job_id": str(a["job_id"])}
    elif a.get("project"):
        body = {"project": str(a["project"])}
    elif a.get("dois"):
        body = {"dois": list(a["dois"])}
    else:
        raise ValueError("give a project, a job_id or a list of DOIs")
    _activity("suggest_missing_sources", tr("sucht oft zitierte Werke, die fehlen"))
    status, r = _http("POST", "/api/missing-works", body, timeout=180.0)
    if status != 200:
        raise ValueError((r or {}).get("error") or "search failed")
    if not r.get("suggestions"):
        return (f"Read the reference lists of {r.get('sources', 0)} of {r.get('of', 0)} sources; no work is cited "
                "by several of them that is missing from the list.")
    lines = [f"Works cited most often by {r['sources']} of the user's sources but missing from the list "
             f"(source: {r.get('source')}). These are candidates to look at, not automatic additions:"]
    for w in r["suggestions"]:
        who = ", ".join(w.get("authors") or []) + (" et al." if (w.get("author_count") or 0) > 3 else "")
        lines.append(f"- cited by {w['count']}/{r['sources']}: {who} ({w.get('year')}). {w.get('title')}. "
                     f"{w.get('venue') or ''} doi:{w.get('doi')}")
    return "\n".join(lines)


def t_check_folder(a: Dict) -> str:
    batch_id = str(a.get("batch_id") or "").strip()
    if not batch_id:
        folder = str(a.get("folder") or "").strip()
        if not folder:
            raise ValueError("give a folder (or the batch_id of a running batch)")
        _activity("check_folder", tr("prüft einen Ordner mit Abgaben: {name}", name=os.path.basename(folder.rstrip("/"))))
        status, b = _http("POST", "/api/batch", {"folder": folder})
        if status != 200:
            raise ValueError((b or {}).get("error") or "could not start")
        batch_id = b["id"]
    deadline = time.time() + float(min(max(a.get("wait_seconds") or 120, 5), 600))
    while True:
        status, b = _http("GET", f"/api/batch/{urllib.parse.quote(batch_id)}")
        if status != 200:
            raise ValueError((b or {}).get("error") or "unknown batch")
        if b.get("status") != "running" or time.time() > deadline:
            break
        time.sleep(2)
    head = f"Batch {batch_id} ({b.get('name')}): {b.get('done')} of {b.get('total')} submissions checked, status {b.get('status')}."
    if b.get("status") == "running":
        head += " Still running; call check_folder again with this batch_id."
    rows = []
    for r in b.get("rows") or []:
        if r.get("error"):
            rows.append(f"- {r['name']}: {_en(r['error'])}")
            continue
        rows.append(f"- {r['name']}: {r.get('entries')} entries; {r.get('missing')} citations without an entry, "
                    f"{r.get('uncited')} entries never cited; {r.get('not_found')} not found, {r.get('mismatch')} "
                    f"mismatched, {r.get('retracted')} retracted; {r.get('style')} style notes"
                    + (f" (details: get_job {r['job_id']})" if r.get("job_id") else ""))
    shared = b.get("shared_phantoms") or []
    if shared:
        rows.append("Invented or misattributed references that appear in several submissions (a hint at a shared "
                    "chatbot answer or copied lists, not proof):")
        rows += [f"- \"{g['title']}\" in {len(g['files'])} submissions: {', '.join(g['files'])}" for g in shared[:20]]
    return head + ("\n" + "\n".join(rows) if rows else "")


def t_find_support(a: Dict) -> str:
    doi, claim = str(a.get("doi") or "").strip(), str(a.get("claim") or "").strip()
    if not doi or not claim:
        raise ValueError("doi and claim are required")
    _activity("find_support", tr("sucht Belegstellen in {doi}", doi=doi))
    status, data = _http("POST", "/api/support", {"doi": doi, "claim": claim, "k": int(a.get("k") or 3)})
    if status != 200 or not isinstance(data, dict):
        raise ValueError((data or {}).get("error") if isinstance(data, dict) else "request failed")
    if not data.get("available"):
        return f"No passages: {data.get('error') or 'the PDF is not in the library'}. Call fetch_papers first."
    lines = [f"Closest passages in {data.get('file')} for the claim (Benthic ranks by wording; it does not judge "
             f"whether a passage supports the claim; read them):"]
    for i, p in enumerate(data.get("passages") or [], start=1):
        lines.append(f"{i}. Page {p['page']} (coverage {round(p['coverage'] * 100)} % of the claim's key words): {p['text']}")
    if not data.get("passages"):
        lines.append("No passage shares the claim's key words. The paper may not make this claim, or uses other words.")
    if data.get("needs_translation"):
        lines.append(f"Note: the claim looks {data.get('claim_language')} and the paper {data.get('source_language')}. "
                     f"Matching is by wording, so translate the claim into the paper's language and call find_support "
                     f"again for better passages.")
    return "\n".join(lines)


def _dois(a: Dict) -> List[str]:
    dois = a.get("dois")
    if isinstance(dois, str):
        dois = [d for d in dois.replace(",", "\n").split() if d]
    if not isinstance(dois, list) or not dois:
        raise ValueError("dois must be a non-empty list")
    return [str(d).strip() for d in dois if str(d).strip()][:200]


def t_fetch_papers(a: Dict) -> str:
    dois = _dois(a)
    _activity("fetch_papers", tr("lädt {n} Quelle" if len(dois) == 1 else "lädt {n} Quellen", n=len(dois)))
    job = _start("/api/download", {"text": "\n".join(dois), "extra_routes": False, **_project_arg(a)},
                 float(a.get("wait_seconds", 60)))
    return _fmt_download(job)


def t_check_open_access(a: Dict) -> str:
    dois = _dois(a)
    _activity("check_open_access", tr("prüft Open Access für {n} DOI" if len(dois) == 1 else "prüft Open Access für {n} DOIs", n=len(dois)))
    job = _start("/api/check", {"text": "\n".join(dois), "extra_routes": False, "project": None},
                 float(a.get("wait_seconds", 50)))
    return _fmt_download(job)


def t_get_job(a: Dict) -> str:
    status, job = _http("GET", f"/api/jobs/{urllib.parse.quote(str(a.get('job_id', '')))}")
    if status != 200 or not isinstance(job, dict):
        raise ValueError("unknown job_id")
    if job.get("mode") == "verify":
        return _fmt_document(job) if job.get("document") else _fmt_verify(job)
    return _fmt_download(job)


def t_search_library(a: Dict) -> str:
    q = {"q": str(a.get("query") or ""), "limit": int(a.get("limit") or 25)}
    if a.get("project"):
        q["project"] = str(a["project"])
    status, data = _http("GET", "/api/library/search?" + urllib.parse.urlencode(q))
    if status != 200:
        raise ValueError(data.get("error") if isinstance(data, dict) else "search failed")
    hits = data.get("results") or []
    if not hits:
        return "No matching papers in the library."
    rows = [f"- {h.get('title') or '(no title)'} doi:{h['doi']}" + (" [PDF available]" if h.get("filename") else "")
            for h in hits]
    return f"{len(hits)} paper(s) in the library:\n" + "\n".join(rows)


def t_get_fulltext(a: Dict) -> str:
    q = {"doi": str(a.get("doi") or ""), "start": int(a.get("start") or 0), "max_chars": int(a.get("max_chars") or 30000)}
    _activity("get_fulltext", tr("liest {doi}", doi=q["doi"]))
    _status, data = _http("GET", "/api/fulltext?" + urllib.parse.urlencode(q), timeout=60)
    if not isinstance(data, dict) or not data.get("available"):
        raise ValueError((data or {}).get("error") if isinstance(data, dict) else "not available")
    tail = (f"\n\n[Truncated at character {data['end']} of {data['total_chars']}. "
            f"Call again with start={data['end']} for more.]" if data.get("truncated") else "")
    return f"{data['file']} ({data['pages']} pages)\n\n{data['text']}{tail}"


def t_list_projects(a: Dict) -> str:
    _s, data = _http("GET", "/api/projects")
    items = data.get("projects") or []
    if not items:
        return "No projects yet. Use create_project."
    active = data.get("active")
    rows = [f"- {p['name']} (id {p['id']}){' [active]' if p['id'] == active else ''}: "
            f"{p['count']} sources, {p['verified']} verified, {p['flagged']} flagged, {p['in_library']} in library"
            for p in items]
    return "\n".join(rows)


def t_create_project(a: Dict) -> str:
    status, data = _http("POST", "/api/projects", {"name": a.get("name"), "description": a.get("description", ""),
                                                   "activate": a.get("activate", True)})
    if status != 200:
        raise ValueError(data.get("error") if isinstance(data, dict) else "could not create project")
    _activity("create_project", tr("legt Projekt „{name}“ an", name=data["name"]))
    return f"Created project “{data['name']}” (id {data['id']})" + (" and made it active." if a.get("activate", True) else ".")


def t_project_status(a: Dict) -> str:
    status, p = _http("GET", f"/api/projects/{urllib.parse.quote(str(a.get('project') or ''), safe='')}")
    if status != 200:
        raise ValueError("project not found")
    rows = []
    for i in p.get("items", []):
        v = VERDICT_EN.get(i.get("verdict") or "", "not checked")
        notes = "".join(f" [{WATCH_EN.get(n['kind'], n['kind'])}{' ' + n['date'] if n.get('date') else ''}]"
                        for n in i.get("notices") or [])
        rows.append(f"- {i.get('title') or '(no title)'} doi:{i['doi']} [{v}]{notes}" + (" [PDF]" if i.get("filename") else ""))
    head = (f"Project “{p['name']}”: {p['count']} sources, {p['verified']} verified, "
            f"{p['flagged']} flagged, {p['in_library']} in library.")
    return head + ("\n" + "\n".join(rows) if rows else "")


WATCH_EN = {"retraction": "retracted", "withdrawal": "withdrawn", "concern": "expression of concern",
            "correction": "correction"}


def t_retraction_watch(a: Dict) -> str:
    if a.get("check_now"):
        _activity("retraction_watch", tr("prüft die Projektquellen auf Rückzüge"))
        _http("POST", "/api/watch/run", {})
        for _ in range(int(min(max(a.get("wait_seconds") or 60, 5), 180))):
            time.sleep(1)
            _s, st = _http("GET", "/api/watch")
            if not st.get("running"):
                break
    _s, st = _http("GET", "/api/watch")
    when = (time.strftime("%Y-%m-%d %H:%M", time.localtime(st["last_run"])) if st.get("last_run") else "never")
    head = (f"Retraction watch {'on' if st.get('enabled') else 'off'}: {st.get('watched', 0)} sources in "
            f"{st.get('projects', 0)} projects, last check {when}"
            + (" (still running)" if st.get("running") else "") + ".")
    rows = []
    for f in st.get("findings") or []:
        where = ", ".join(p["name"] for p in f.get("projects") or [])
        notice = f" notice doi:{f['notice_doi']}" if f.get("notice_doi") else ""
        rows.append(f"- {WATCH_EN.get(f['kind'], f['kind']).upper()}{' ' + f['date'] if f.get('date') else ''}: "
                    f"{f.get('title') or '(no title)'} doi:{f['doi']}{notice}" + (f" (in {where})" if where else ""))
    if not rows:
        return head + " No retractions, expressions of concern or corrections found."
    return head + "\n" + "\n".join(rows)


def t_send_to_zotero(a: Dict) -> str:
    body = {"project": str(a["project"])} if a.get("project") else {"dois": list(a.get("dois") or [])}
    if not body.get("project") and not body.get("dois"):
        raise ValueError("give a project or a list of DOIs")
    _activity("send_to_zotero", tr("schickt Quellen an Zotero"))
    status, r = _http("POST", "/api/zotero/send", body, timeout=120.0)
    if status != 200:
        raise ValueError((r or {}).get("error") or "Zotero did not accept the items")
    target = r.get("target") or {}
    where = target.get("collection") or target.get("library") or "the selected library"
    miss = f" No citation data for: {', '.join(r['missing'])}." if r.get("missing") else ""
    return f"Sent {r.get('sent', 0)} items to Zotero ({where}).{miss}"


def t_check_zotero_library(a: Dict) -> str:
    _activity("check_zotero_library", tr("prüft die Zotero-Bibliothek auf Rückzüge"))
    status, r = _http("POST", "/api/zotero/scan", {}, timeout=300.0)
    if status != 200:
        raise ValueError((r or {}).get("error") or "could not read the Zotero library")
    head = (f"Zotero library: {r['items']} items, {r['with_doi']} with a DOI, {r['checked']} checked"
            + (f", {r['failed']} could not be checked" if r.get("failed") else "") + ".")
    rows = []
    for f in r.get("flagged") or []:
        kinds = ", ".join(f"{WATCH_EN.get(n['kind'], n['kind'])}{' ' + n['date'] if n.get('date') else ''}"
                          for n in f["notices"])
        rows.append(f"- {f.get('title') or '(no title)'} doi:{f['doi']} [{kinds}] open in Zotero: {f['link']}")
    if not rows:
        return head + " No retractions, expressions of concern or corrections found."
    return head + "\n" + "\n".join(rows)


def t_assess_references(a: Dict) -> str:
    if a.get("project"):
        body = {"project": str(a["project"])}
    elif a.get("dois"):
        body = {"items": [{"doi": d} for d in a["dois"]]}
    else:
        raise ValueError("give a project or a list of DOIs")
    status, r = _http("POST", "/api/health", body, timeout=120.0)
    if status != 200:
        raise ValueError((r or {}).get("error") or "assessment failed")
    h = r["health"]
    state_en = {"ok": "OK", "notes": "NOTE", "critical": "CRITICAL", "info": "INFO", "unknown": "UNKNOWN"}
    out = [f"Reference list health: {h['label']} ({h['count']} sources). Verdicts in German as shown in the app."]
    for d in h["dimensions"]:
        out.append(f"- {d['label']} [{state_en.get(d['state'], d['state'])}]: {d['headline']}. {d['detail']}")
    age = h.get("age") or {}
    if age.get("buckets"):
        out.append("Age distribution: " + ", ".join(f"{b['label']}: {b['count']}" for b in age["buckets"]))
    rows = []
    for doi, v in (r.get("venues") or {}).items():
        bits = [v.get("name") or "?"]
        if v.get("preprint"):
            bits.append("preprint server")
        if v.get("jufo") is not None:
            bits.append(f"JUFO {v['jufo']}")
        if v.get("citedness") is not None:
            bits.append(f"{v['citedness']} citations per article (2 years)")
        bits += [f"{x['list']}: {x['rank']}" for x in v.get("ranks") or []]
        rows.append(f"- doi:{doi}: " + ", ".join(bits))
    if rows:
        out.append("Venues (OpenAlex, JUFO, user ranking lists):\n" + "\n".join(rows))
    return "\n".join(out)


def t_export_bibliography(a: Dict) -> str:
    q = {"format": a.get("format", "bibtex"), "style": a.get("style", "apa"), "as": "json",
         "only": "verified" if a.get("only_verified", True) else "all"}
    pid = urllib.parse.quote(str(a.get("project") or ""), safe="")
    status, data = _http("GET", f"/api/projects/{pid}/bibliography?" + urllib.parse.urlencode(q), timeout=90)
    if status != 200:
        raise ValueError(data.get("error") if isinstance(data, dict) else "export failed")
    miss = f"\n\n[No metadata for: {', '.join(data['missing'])}]" if data.get("missing") else ""
    return (data.get("text") or "(empty)") + miss


def t_find_sources(a: Dict) -> str:
    claim = str(a.get("claim") or "").strip()
    if len(claim.split()) < 3:
        raise ValueError("give the claim as a sentence")
    _activity("find_sources", tr("sucht echte Quellen zu einer Aussage"))
    status, r = _http("POST", "/api/find-sources", {"claim": claim, "open_access_only": bool(a.get("open_access_only"))},
                      timeout=60.0)
    if status != 200:
        raise ValueError((r or {}).get("error") or "search failed")
    if not r.get("suggestions"):
        return "OpenAlex found no paper for this claim. Try other words, or English terms."
    lines = ["Real papers from OpenAlex for the claim (retracted ones excluded), each with the abstract sentence "
             "closest to it. They are candidates to read: recommend one only after its text shows it supports the "
             "claim, and never cite one the user has not read."]
    for w in r["suggestions"]:
        who = ", ".join(w.get("authors") or []) + (" et al." if (w.get("author_count") or 0) > 3 else "")
        oa = f" Open access: {w['oa_url']}" if w.get("oa_url") else ""
        lines.append(f"- {who} ({w.get('year')}). {w.get('title')}. {w.get('venue') or ''} doi:{w.get('doi')}.{oa}")
        if w.get("passage"):
            lines.append(f"  Abstract: \"{w['passage']['text']}\"")
    return "\n".join(lines)


def t_check_overlap(a: Dict) -> str:
    job_id = str(a.get("job_id") or "")
    if not job_id:
        raise ValueError("give the job_id of a check_document run")
    _activity("check_overlap", tr("sucht Übernahmen ohne Anführungszeichen"))
    status, r = _http("POST", "/api/overlap", {"job_id": job_id}, timeout=300.0)
    if status != 200:
        raise ValueError((r or {}).get("error") or "failed")
    kinds = {"verbatim": "word for word", "close": "almost word for word", "partial": "a long identical run of words"}
    lines = [f"Compared {r.get('checked', 0)} citing sentences with the downloaded full texts of their sources "
             "(text in quotation marks excluded). Findings are passages to read side by side, not a verdict on intent."]
    for f in r.get("findings") or []:
        lines.append(f"- {kinds[f['kind']]} from {f.get('label')} (doi:{f.get('doi')}, p. {f.get('page') or '?'}): "
                     f"\"{f['sentence']}\"" + (f"\n  Source: \"{f['excerpt']}\"" if f.get("excerpt") else ""))
    if not r.get("findings"):
        lines.append("No sentence takes over the wording of its source without quotation marks.")
    if r.get("missing_pdfs"):
        lines.append(f"Not checked, PDF not in the library ({len(r['missing_pdfs'])}): " + ", ".join(r["missing_pdfs"][:20])
                     + ". fetch_papers can download open-access copies.")
    return "\n".join(lines)


def t_suggest_alternatives(a: Dict) -> str:
    query = str(a.get("reference") or a.get("title") or "").strip()
    if len(query) < 8:
        raise ValueError("give the reference (or at least its title)")
    _activity("suggest_alternatives", tr("sucht echte Arbeiten zum Thema"))
    status, r = _http("POST", "/api/alternatives", {"title": query}, timeout=60.0)
    if status != 200:
        raise ValueError((r or {}).get("error") or "search failed")
    if not r.get("suggestions"):
        return "No real paper close to this reference was found in OpenAlex or Crossref."
    lines = ["Real papers closest to the reference (OpenAlex, Crossref; retracted ones left out). These are reading "
             "suggestions on the same topic, not the work that was cited: only recommend one after checking that it "
             "supports the user's statement."]
    for w in r["suggestions"]:
        who = (w.get("authors") or [""])[0] + (" et al." if len(w.get("authors") or []) > 1 else "")
        oa = " (open access)" if w.get("is_oa") else ""
        lines.append(f"- {who} ({w.get('year') or 'n.d.'}). {w.get('title')}. {w.get('venue') or ''} doi:{w.get('doi')}{oa}")
    return "\n".join(lines)


def t_corrected_bibliography(a: Dict) -> str:
    job_id = urllib.parse.quote(str(a.get("job_id") or ""), safe="")
    if not job_id:
        raise ValueError("give the job_id of a verify_references or check_document run")
    q = urllib.parse.urlencode({"style": a.get("style", "apa")})
    status, r = _http("GET", f"/api/jobs/{job_id}/corrected?{q}", timeout=120.0)
    if status != 200:
        raise ValueError((r or {}).get("error") or "failed")
    out = [f"Corrected reference list ({r.get('fixed', 0)} entries rebuilt from the real metadata):", "", r.get("text") or "(empty)"]
    if r.get("open"):
        out += ["", "Left out because Benthic could not confirm them; the user must check or remove these:"]
        out += [f"- {e['n']}. [{e.get('verdict')}] {e.get('text')}" for e in r["open"]]
    return "\n".join(out)


def t_request_copy(a: Dict) -> str:
    doi = str(a.get("doi") or "").strip()
    if not doi:
        raise ValueError("give a DOI")
    status, r = _http("GET", "/api/request-copy?" + urllib.parse.urlencode({"doi": doi}), timeout=30.0)
    if status != 200:
        raise ValueError((r or {}).get("error") or "failed")
    lang = "de" if str(a.get("language") or "en").lower().startswith("de") else "en"
    people = ", ".join(c["name"] + (f" ({c['orcid']})" if c.get("orcid") else "") for c in r.get("contacts") or []) or "unknown"
    return (f"Contact: {people}. The e-mail address is usually on the publisher's page: {r.get('landing_url')}\n\n"
            f"Subject: {r['subject'][lang]}\n\n{r['body'][lang]}[name]")


_S = {"type": "string"}
_PROJECT = {"type": "string", "description": "Project name or id. Optional; defaults to the active project in Benthic."}
_WAIT = {"type": "integer", "minimum": 1, "maximum": 110,
         "description": "Seconds to wait for completion before returning partial results with a job_id."}
_DOIS = {"type": "array", "items": _S, "minItems": 1, "maxItems": 200, "description": "DOIs, e.g. 10.1038/nature14539"}

TOOLS: List[Dict] = [
    {"name": "verify_references", "title": "Verify references",
     "description": "Check a reading list for hallucinated or wrong references. Accepts any text: an LLM answer, "
                    "a reference list, BibTeX, RIS or plain DOIs. For each reference: does it exist, do title, "
                    "authors and year match, is the DOI correct (and which is the right one), was it retracted.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "description": "The references."},
                                                      "project": _PROJECT, "wait_seconds": _WAIT},
                     "required": ["text"]},
     "annotations": {"readOnlyHint": False, "openWorldHint": True}, "handler": t_verify_references},
    {"name": "check_document", "title": "Check a whole paper or thesis",
     "description": "Check a whole manuscript on the user's Mac (PDF, Word .docx, text, Markdown, or a LaTeX "
                    "project: the main .tex file or its folder, with the .bib files it uses): find its reference "
                    "list, cross-check it with the citations in the text (cited but missing from the list, listed "
                    "but never cited), list direct quotations with their source, then verify every entry like "
                    "verify_references.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Absolute path or ~/… path of the file (or LaTeX project folder) on this Mac."},
         "project": _PROJECT, "wait_seconds": _WAIT}, "required": ["path"]},
     "annotations": {"readOnlyHint": False, "openWorldHint": True}, "handler": t_check_document},
    {"name": "check_quotes", "title": "Check direct quotations",
     "description": "Check whether direct quotations really appear word for word in the cited source, and on the "
                    "cited page. Give the job_id of a check_document run to check every quotation of a thesis, or "
                    "a doi with one quote (and optionally the page) to check a single quotation, for example one "
                    "you are about to write. Needs the PDF in the library (fetch_papers).",
     "inputSchema": {"type": "object", "properties": {
         "job_id": _S, "doi": _S, "quote": {"type": "string", "description": "The quoted words, without quotation marks."},
         "page": _S}},
     "annotations": {"readOnlyHint": True}, "handler": t_check_quotes},
    {"name": "suggest_missing_sources", "title": "Suggest missing key sources",
     "description": "Find works that the user's confirmed sources cite again and again but that are missing from "
                    "the user's list (from the reference lists of the sources, via OpenAlex or Crossref). Give a "
                    "project, the job_id of a check, or DOIs. Verify any suggestion before recommending it.",
     "inputSchema": {"type": "object", "properties": {"project": _S, "job_id": _S, "dois": _DOIS}},
     "annotations": {"readOnlyHint": True, "openWorldHint": True}, "handler": t_suggest_missing_sources},
    {"name": "check_folder", "title": "Check a folder of submissions",
     "description": "For lecturers: check every submission in a folder on the user's Mac (PDF, Word, text, LaTeX as "
                    ".zip or project folder) like check_document, one after another, and return one summary line "
                    "per submission. Long batches keep running; call again with the batch_id to see progress.",
     "inputSchema": {"type": "object", "properties": {
         "folder": {"type": "string", "description": "Absolute path or ~/… path of the folder, or of an assignment's "
                                                        ".zip export from Moodle or ILIAS."},
         "batch_id": _S, "wait_seconds": {"type": "integer", "minimum": 5, "maximum": 600}}},
     "annotations": {"readOnlyHint": False, "openWorldHint": True}, "handler": t_check_folder},
    {"name": "find_support", "title": "Find supporting passages",
     "description": "In a downloaded paper (by DOI), find the passages that come closest to a claim, with page "
                    "numbers, so you can check whether the source really supports the sentence that cites it. "
                    "Ranking is by wording, not a judgement: read the passages before relying on them. Write the "
                    "claim in the language of the paper (translate it first if needed).",
     "inputSchema": {"type": "object", "properties": {
         "doi": _S, "claim": {"type": "string", "description": "The sentence or claim that cites the paper."},
         "k": {"type": "integer", "minimum": 1, "maximum": 8}}, "required": ["doi", "claim"]},
     "annotations": {"readOnlyHint": True}, "handler": t_find_support},
    {"name": "fetch_papers", "title": "Fetch papers",
     "description": "Download open-access PDFs for DOIs into the user's library, only through open access "
                    "(arXiv, OpenAlex, Unpaywall, Europe PMC, Semantic Scholar, CORE, publisher APIs). Papers "
                    "without an open-access copy are reported with a library or publisher link.",
     "inputSchema": {"type": "object", "properties": {"dois": _DOIS, "project": _PROJECT, "wait_seconds": _WAIT},
                     "required": ["dois"]},
     "annotations": {"readOnlyHint": False, "openWorldHint": True}, "handler": t_fetch_papers},
    {"name": "check_open_access", "title": "Check open access",
     "description": "Without downloading: is each DOI already in the library, available as open access, or not?",
     "inputSchema": {"type": "object", "properties": {"dois": _DOIS, "wait_seconds": _WAIT}, "required": ["dois"]},
     "annotations": {"readOnlyHint": True, "openWorldHint": True}, "handler": t_check_open_access},
    {"name": "get_job", "title": "Get job status",
     "description": "Status and results of a verification or download job that was still running.",
     "inputSchema": {"type": "object", "properties": {"job_id": _S}, "required": ["job_id"]},
     "annotations": {"readOnlyHint": True}, "handler": t_get_job},
    {"name": "search_library", "title": "Search library",
     "description": "Search the user's Benthic library by title words or DOI, optionally within a project.",
     "inputSchema": {"type": "object", "properties": {"query": _S, "project": _PROJECT,
                                                      "limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
     "annotations": {"readOnlyHint": True}, "handler": t_search_library},
    {"name": "get_fulltext", "title": "Read a paper",
     "description": "Full text of a downloaded paper (by DOI), with page markers. Long papers are returned in "
                    "chunks; use start to continue.",
     "inputSchema": {"type": "object", "properties": {"doi": _S, "start": {"type": "integer", "minimum": 0},
                                                      "max_chars": {"type": "integer", "minimum": 1000, "maximum": 120000}},
                     "required": ["doi"]},
     "annotations": {"readOnlyHint": True}, "handler": t_get_fulltext},
    {"name": "list_projects", "title": "List projects",
     "description": "Research projects in Benthic with their source counts and the active project.",
     "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"readOnlyHint": True}, "handler": t_list_projects},
    {"name": "create_project", "title": "Create project",
     "description": "Create a research project (one per research question). New checks and downloads are "
                    "saved to the active project.",
     "inputSchema": {"type": "object", "properties": {"name": _S, "description": _S, "activate": {"type": "boolean"}},
                     "required": ["name"]},
     "annotations": {"readOnlyHint": False}, "handler": t_create_project},
    {"name": "project_status", "title": "Project status",
     "description": "All sources of a project with their verification verdict and whether the PDF is in the library.",
     "inputSchema": {"type": "object", "properties": {"project": _S}, "required": ["project"]},
     "annotations": {"readOnlyHint": True}, "handler": t_project_status},
    {"name": "retraction_watch", "title": "Retraction watch",
     "description": "Benthic rechecks the sources of all projects once a day (Crossref notices including the "
                    "Retraction Watch database, and OpenAlex). This lists retractions, withdrawals, expressions of "
                    "concern and corrections found so far; set check_now to recheck immediately.",
     "inputSchema": {"type": "object", "properties": {"check_now": {"type": "boolean"},
                                                      "wait_seconds": {"type": "integer", "minimum": 5, "maximum": 180}}},
     "annotations": {"readOnlyHint": False, "openWorldHint": True}, "handler": t_retraction_watch},
    {"name": "send_to_zotero", "title": "Send to Zotero",
     "description": "Send verified sources (a project, or DOIs) into the collection currently selected in the "
                    "Zotero desktop app. Zotero must be running. Retracted, invented and mismatched sources of a "
                    "project are left out.",
     "inputSchema": {"type": "object", "properties": {"project": _S, "dois": _DOIS}},
     "annotations": {"readOnlyHint": False, "openWorldHint": True}, "handler": t_send_to_zotero},
    {"name": "check_zotero_library", "title": "Check Zotero library",
     "description": "Read the user's Zotero library (read-only, from a copy of its database) and check every item "
                    "with a DOI for retractions, withdrawals, expressions of concern and corrections. Returns the "
                    "flagged items with zotero:// links that open them in Zotero.",
     "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"readOnlyHint": True, "openWorldHint": True}, "handler": t_check_zotero_library},
    {"name": "export_bibliography", "title": "Export bibliography",
     "description": "Bibliography of a project as BibTeX, RIS or formatted text in a citation style "
                    "(apa, harvard, chicago, mla, ieee, vancouver, din, or any CSL style name). "
                    "By default only verified sources are included.",
     "inputSchema": {"type": "object", "properties": {
         "project": _S, "format": {"type": "string", "enum": ["bibtex", "ris", "text"]},
         "style": _S, "only_verified": {"type": "boolean"}}, "required": ["project"]},
     "annotations": {"readOnlyHint": True, "openWorldHint": True}, "handler": t_export_bibliography},
    {"name": "find_sources", "title": "Find real sources for a claim",
     "description": "Instead of naming sources from memory (which invents references): search OpenAlex for real "
                    "papers on a claim, retracted ones excluded, optionally open access only, each with the abstract "
                    "sentence closest to the claim. Use English wording for the claim where possible.",
     "inputSchema": {"type": "object", "properties": {
         "claim": {"type": "string", "description": "The statement that needs a source, as a sentence."},
         "open_access_only": {"type": "boolean"}}, "required": ["claim"]},
     "annotations": {"readOnlyHint": True, "openWorldHint": True}, "handler": t_find_sources},
    {"name": "check_overlap", "title": "Find wording taken over without quotation marks",
     "description": "For a check_document run (job_id): compare every sentence that cites a source with that "
                    "source's downloaded full text, outside quotation marks, and list passages taken over word for "
                    "word, almost word for word, or as a long identical run of words, with page and source passage. "
                    "Needs the PDFs in the library (fetch_papers). Present findings as passages to compare, not as "
                    "proof of plagiarism.",
     "inputSchema": {"type": "object", "properties": {"job_id": _S}, "required": ["job_id"]},
     "annotations": {"readOnlyHint": True}, "handler": t_check_overlap},
    {"name": "suggest_alternatives", "title": "Suggest real papers for an invented reference",
     "description": "For a reference that Benthic marked Not found or Mismatch: the real papers closest to its title "
                    "(OpenAlex, Crossref, without retracted ones). Reading suggestions on the same topic, not a "
                    "replacement to cite unread.",
     "inputSchema": {"type": "object", "properties": {
         "reference": {"type": "string", "description": "The reference as cited, or its title."}}, "required": ["reference"]},
     "annotations": {"readOnlyHint": True, "openWorldHint": True}, "handler": t_suggest_alternatives},
    {"name": "corrected_bibliography", "title": "Corrected reference list",
     "description": "The reference list of a check (job_id from verify_references or check_document) rebuilt from "
                    "the real metadata in a citation style, ready to paste. Retracted, invented and unconfirmed "
                    "entries are listed separately, never included.",
     "inputSchema": {"type": "object", "properties": {
         "job_id": _S, "style": {"type": "string", "enum": ["apa", "harvard", "chicago", "ieee", "vancouver", "din"]}},
         "required": ["job_id"]},
     "annotations": {"readOnlyHint": True, "openWorldHint": True}, "handler": t_corrected_bibliography},
    {"name": "request_copy", "title": "Draft a request for a copy",
     "description": "When a paper has no open-access copy: a polite request to its corresponding author (English or "
                    "German), with the author's ORCID and the publisher's page, where the e-mail address usually is. "
                    "The user sends it; never send it yourself.",
     "inputSchema": {"type": "object", "properties": {
         "doi": _S, "language": {"type": "string", "enum": ["en", "de"]}}, "required": ["doi"]},
     "annotations": {"readOnlyHint": True, "openWorldHint": True}, "handler": t_request_copy},
    {"name": "assess_references", "title": "Assess reference list",
     "description": "Health of a reference list: are the sources real, how old are they (median age, share from "
                    "the last 5 years), where were they published (journal or preprint, journal metrics from "
                    "OpenAlex, Finnish JUFO level, ranks from ranking lists the user imported) and how many PDFs "
                    "are in the library. Give a project, or DOIs for a quick look without verdicts.",
     "inputSchema": {"type": "object", "properties": {"project": _S, "dois": _DOIS}},
     "annotations": {"readOnlyHint": True, "openWorldHint": True}, "handler": t_assess_references},
]
_HANDLERS = {t["name"]: t["handler"] for t in TOOLS}


def tool_list() -> List[Dict]:
    return [{k: v for k, v in t.items() if k != "handler"} for t in TOOLS]


# --------------------------------------------------------------------------
# JSON-RPC über stdio
# --------------------------------------------------------------------------
def _send(msg: Dict) -> None:
    line = json.dumps(msg, ensure_ascii=False)
    with _out_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _result(mid: Any, result: Dict) -> None:
    _send({"jsonrpc": "2.0", "id": mid, "result": result})


def _error(mid: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def _call_tool(mid: Any, params: Dict) -> None:
    name = params.get("name")
    handler = _HANDLERS.get(name)
    if not handler:
        _error(mid, -32602, f"Unknown tool: {name}")
        return
    health: Dict = {}
    try:
        health = ensure_backend()
        text, failed = handler(params.get("arguments") or {}), False
    except Exception as exc:  # Werkzeugfehler gehen als Ergebnis zurück, damit das LLM reagieren kann
        log(f"{name}: {exc}")
        text, failed = f"Error: {exc}", True
    notice = _version_notice(str(health.get("version") or "") or None)
    if notice:
        text = f"{notice}\n\n{text}"
    _result(mid, {"content": [{"type": "text", "text": text}], "isError": failed})


def handle(msg: Dict) -> None:
    global _client
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method is None:
        return  # Antwort auf eine Anfrage von uns (wir stellen keine)
    if method == "initialize":
        info = params.get("clientInfo") or {}
        _client = "mcp:" + str(info.get("name") or "client")[:40]
        wanted = params.get("protocolVersion")
        _result(mid, {
            "protocolVersion": wanted if wanted in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "benthic", "title": "Benthic", "version": __version__},
            "instructions": INSTRUCTIONS,
        })
    elif method == "ping":
        _result(mid, {})
    elif method == "tools/list":
        _result(mid, {"tools": tool_list()})
    elif method == "tools/call":
        threading.Thread(target=_call_tool, args=(mid, params), daemon=True).start()
    elif method.startswith("notifications/"):
        return
    elif mid is not None:
        _error(mid, -32601, f"Method not found: {method}")


def main() -> None:
    log(f"Benthic MCP {__version__} bereit (Backend {BASE})")
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        for m in (msg if isinstance(msg, list) else [msg]):
            if isinstance(m, dict):
                handle(m)


if __name__ == "__main__":
    main()
