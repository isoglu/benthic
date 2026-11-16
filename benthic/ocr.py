"""Texterkennung für gescannte Seiten mit Unlimited-OCR, wenn es auf diesem Mac installiert ist (optional).

Benthic liest PDFs über ihre Textebene (pypdf). Ein Scan hat keine, seine Seiten bleiben leer. Ist in den
Einstellungen ``ocr_scans`` an und liegt die Laufzeit von baidu/Unlimited-OCR (MIT) unter ``UNLIMITED_OCR_HOME``
(Standard ``~/unlimited-ocr``, auch als Verweis auf ein anderes Laufwerk: ein venv ``.venv-mlx`` mit mlx-vlm und
PyMuPDF, das Modell in ``models/Unlimited-OCR-2-mlx-bf16``), liest Benthic solche Seiten damit. Benthic bringt weder Modell noch MLX mit:
Ohne die Laufzeit bleibt alles wie bisher, und die Seite heißt Scan.

Eine Seite dauert 40 bis 60 Sekunden, länger als ein Aufruf warten darf. Die Erkennung läuft deshalb im Hintergrund,
eine PDF nach der anderen, und ihr Ergebnis liegt nach dem Inhalt der Datei (SHA-256) im Datenordner, wo der nächste
Aufruf es findet. Das Modell belegt etwa 11 GB: Es lädt nur, wenn kein anderer Lauf ``gpu.lock`` hält.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

from . import config

MODEL_ID = "baidu/Unlimited-OCR-2"
SCAN_WORDS = 40          # weniger Wörter in der Textebene und ein Bild: Die Seite ist ein Scan
SECONDS_PER_PAGE = 50    # M4 mit 16 GB, gemessen am 2026-10-31; dazu einmal etwa 30 s, bis das Modell geladen ist
BUSY = 75                # Exit-Code der Erkennung, wenn ein anderer Lauf das Modell hält

# Läuft im Python der Laufzeit (-I: ohne Benthics Pfade), nicht in Benthic: Nur dort gibt es MLX und PyMuPDF.
_ENGINE = r'''
import fcntl, json, os, sys
home, pdf, pages = sys.argv[1], sys.argv[2], [int(n) for n in sys.argv[3].split(",")]
lock = open(os.path.join(home, "gpu.lock"), "w")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(75)
import fitz
from mlx_vlm import generate, load
from mlx_vlm.prompt_utils import apply_chat_template
model, processor = load(os.path.join(home, "models", "Unlimited-OCR-2-mlx-bf16"))
prompt = apply_chat_template(processor, model.config, "Multi page parsing.", num_images=1)
os.makedirs(os.path.join(home, "tmp"), exist_ok=True)
img = os.path.join(home, "tmp", "benthic-%d.png" % os.getpid())
with fitz.open(pdf) as doc:
    for n in pages:
        doc[n - 1].get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), alpha=False).save(img)
        r = generate(model, processor, prompt, image=[img], max_tokens=6144, temperature=0.0, verbose=False)
        print(json.dumps({"page": n, "raw": r.text}), flush=True)
os.unlink(img)
'''

_lock = threading.Lock()
_queue: Dict[str, tuple] = {}     # SHA-256 → (PDF, Seiten, Kopie nur für diesen Lauf)
_running: Optional[str] = None
_failed: set = set()              # bis zum Neustart nicht noch einmal versuchen
_thread: Optional[threading.Thread] = None


def home() -> Path:
    # Die App erbt keine Variablen der Shell: Ohne UNLIMITED_OCR_HOME gilt der feste Ort im Benutzerordner.
    return Path(os.environ.get("UNLIMITED_OCR_HOME") or Path.home() / "unlimited-ocr")


def status() -> str:
    """``off`` (Einstellung aus), ``missing`` (Laufzeit oder Modell fehlt) oder ``ready``."""
    if not config.load_settings().get("ocr_scans"):
        return "off"
    h = home()
    if not (h / ".venv-mlx" / "bin" / "python").is_file() or not (h / "models" / "Unlimited-OCR-2-mlx-bf16" / "config.json").is_file():
        return "missing"
    return "ready"


def is_scan(page, text: str) -> bool:
    """Kaum Text in der Textebene und ein Bild auf der Seite."""
    if len(text.split()) >= SCAN_WORDS:
        return False
    try:
        return len(page.images) > 0
    except Exception:  # defekte Bildobjekte: lieber kein Scan als ein Abbruch
        return False


def pages(reader, source: Union[Path, bytes], picks: Optional[List[int]] = None, start: bool = True) -> List[Dict]:
    """Text je Seite (``page`` ab 1, ``text``) aus der Textebene, bei einem Scan aus der Erkennung, sobald sie vorliegt.

    ``picks`` sind Seitenindizes ab 0 (Standard: alle). Erkannte Seiten tragen ``ocr``, Scanseiten noch ohne Text
    ``scan`` mit dem Stand (off, missing, running, failed, ready). ``start`` stößt die Erkennung fehlender Seiten an;
    ohne liest es nur, was schon erkannt ist."""
    picks = list(range(len(reader.pages))) if picks is None else picks
    out = [{"page": i + 1, "text": reader.pages[i].extract_text() or ""} for i in picks]
    scans = [d for d, i in zip(out, picks) if is_scan(reader.pages[i], d["text"])]
    if not scans:
        return out
    state = status()
    if state == "ready":
        digest = hashlib.sha256(source if isinstance(source, bytes) else Path(source).read_bytes()).hexdigest()
        done = _cached(digest)
        for d in scans:
            if d["page"] in done:
                d.update(text=done[d["page"]], ocr=True)
        scans = [d for d in scans if not d.get("ocr")]
        if scans:
            state = _request(digest, source, [d["page"] for d in scans], start)
    for d in scans:
        d["scan"] = state
    return out


def summary(pages: List[Dict]) -> Dict:
    """Felder für ein Ergebnis: ``scan_pages`` und ``ocr`` (Stand) für Scanseiten ohne Text, ``ocr_pages``."""
    out: Dict = {}
    scans = [d for d in pages if d.get("scan")]
    if scans:
        out.update(scan_pages=[d["page"] for d in scans], ocr=scans[0]["scan"])
    read = [d["page"] for d in pages if d.get("ocr")]
    if read:
        out["ocr_pages"] = read
    return out


def _ranges(nums: List[int]) -> str:
    spans: List[List[int]] = []
    for n in nums:
        if spans and n == spans[-1][1] + 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return ", ".join(f"{a}–{b}" if b > a else str(a) for a, b in spans)


def note(scan_pages: List[int], state: Optional[str], lang: Optional[str] = None) -> str:
    """Was der Mensch über Scanseiten ohne Text liest; leer, wenn es keine gibt."""
    if not scan_pages:
        return ""
    from .i18n import tr
    head = tr("Scan ohne Textebene: Seite {pages}.", lang, pages=_ranges(scan_pages))
    minutes = max(1, round((30 + len(scan_pages) * SECONDS_PER_PAGE) / 60))
    tail = {"off": tr("Die Texterkennung für Scans ist aus (Einstellungen).", lang),
            "missing": tr("Unlimited-OCR ist auf diesem Mac nicht installiert ({home}).", lang, home=str(home())),
            "running": tr("Unlimited-OCR liest sie im Hintergrund, etwa {minutes} Min.; danach noch einmal abrufen.",
                          lang, minutes=minutes),
            "failed": tr("Unlimited-OCR konnte sie nicht lesen.", lang)}.get(state or "", "")
    return f"{head} {tail}".strip()


# --------------------------------------------------------------------------
# Ablage und Hintergrundlauf
# --------------------------------------------------------------------------
def _store(digest: str) -> Path:
    return config.data_dir() / "ocr" / f"{digest}.json"


def _cached(digest: str) -> Dict[int, str]:
    try:
        data = json.loads(_store(digest).read_text(encoding="utf-8"))
        return {int(k): str(v) for k, v in (data.get("pages") or {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def _save(digest: str, page: int, text: str) -> None:
    with _lock:
        done = _cached(digest)
        done[page] = text
        path = _store(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"model": MODEL_ID, "pages": {str(k): done[k] for k in sorted(done)}},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def _request(digest: str, source: Union[Path, bytes], todo: List[int], start: bool) -> str:
    """Stand der Erkennung für diese Datei; mit ``start`` reiht es sie ein, wenn sie noch nicht dran war."""
    global _thread
    with _lock:
        if digest == _running or digest in _queue:
            return "running"
        if digest in _failed:
            return "failed"
        if not start:
            return "ready"
        if isinstance(source, bytes):  # hochgeladene Arbeit: eine Kopie für den Lauf, danach weg
            path = config.data_dir() / "ocr" / f"{digest}.pdf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source)
            _queue[digest] = (path, todo, True)
        else:
            _queue[digest] = (Path(source), todo, False)
        if _thread is None:
            _thread = threading.Thread(target=_work, name="benthic-ocr", daemon=True)
            _thread.start()
    return "running"


def _work() -> None:
    global _running, _thread
    while True:
        with _lock:
            if not _queue:
                _running = _thread = None
                return
            digest = next(iter(_queue))
            item = _queue.pop(digest)
            _running = digest
        _run(digest, *item)


def _run(digest: str, path: Path, todo: List[int], temp: bool) -> None:
    h = home()
    cmd = [str(h / ".venv-mlx" / "bin" / "python"), "-I", "-c", _ENGINE, str(h), str(path), ",".join(map(str, todo))]
    log = config.data_dir() / "ocr" / "engine.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log, "w", encoding="utf-8") as err:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True)
            for line in proc.stdout:
                rec = json.loads(line)
                _save(digest, int(rec["page"]), clean(str(rec["raw"])))
            code = proc.wait()
    except (OSError, ValueError, KeyError):
        code = 1
    if code == BUSY:  # ein anderer Lauf hält das Modell: später noch einmal, Erkanntes bleibt
        time.sleep(60)
        done = _cached(digest)
        with _lock:
            _queue.setdefault(digest, (path, [n for n in todo if n not in done], temp))
        return
    if code:
        with _lock:
            _failed.add(digest)
    if temp:
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Ausgabe des Modells zu Text
# --------------------------------------------------------------------------
_BLOCK = re.compile(r"<BLOCK>([A-Za-z_]+)(?:<LEVEL\d+>)?\[[^\]]*\]<FORK>")
_IMAGE_LABELS = {"image", "figure", "picture", "chart", "img", "seal"}


def clean(raw: str) -> str:
    """Blöcke des Modells (``<BLOCK>label[x0,y0,x1,y1]<FORK>text``) zu Text: ein Absatz je Block, Abbildungen weg,
    Tabellenzellen mit `` | `` verbunden, alle übrigen Steuerzeichen entfernt."""
    raw = raw.replace("<PAGE>", "")
    parts = _BLOCK.split(raw)
    blocks = [("text", parts[0])] if parts[0].strip() else []
    blocks += list(zip(parts[1::2], parts[2::2]))
    out = []
    for label, body in blocks:
        if re.sub(r"^(cross_|append_)", "", label.lower()) in _IMAGE_LABELS:
            continue
        if "<t" in body:
            body = re.sub(r"</t[dh]>\s*<t[dh][^>]*>", " | ", body)
            body = re.sub(r"</tr>", "\n", body)
            body = re.sub(r"<[^>]+>", "", body)
        body = re.sub(r"<[|｜]?/?[A-Za-z_]+[|｜]?>", "", body)
        body = html.unescape(body).strip()
        if body:
            out.append(body)
    return "\n\n".join(out)
