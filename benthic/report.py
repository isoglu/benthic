"""Prüfbericht als druckbare Seite: alles, was Benthic zu einer Arbeit oder Liste weiß, für eine PDF.

Die App druckt die Seite über macOS in eine PDF (ReportBridge in BenthicApp.swift); im Browser öffnet
``?print=1`` den Druckdialog. Die Seite ist eigenständig (eigenes CSS, keine Skripte außer dem Druck).
"""

from __future__ import annotations

import html
import time
from typing import Dict, List, Optional

from . import __version__
from .i18n import current, tr
from .verify import VERDICT_LABEL, VERDICTS

ORDER = ("retracted", "not_found", "mismatch", "uncertain", "partial", "corrected", "verified")
COLOR = {"verified": "ok", "corrected": "ok", "partial": "warn", "uncertain": "warn", "mismatch": "fail",
         "not_found": "fail", "retracted": "fail"}
QUOTE_LABEL = {"exact": "Wortgleich", "close": "Wortlaut weicht ab", "not_found": "Nicht in der Quelle gefunden",
               "no_pdf": "PDF fehlt", "no_source": "Quelle nicht bestätigt", "pending": "Quelle wird noch geprüft"}
QUOTE_COLOR = {"exact": "ok", "close": "warn", "not_found": "fail"}

CSS = """
@page { size: A4; margin: 16mm 15mm; }
* { box-sizing: border-box; }
body { margin: 0; font: 10.5pt/1.45 -apple-system, "SF Pro Text", "Helvetica Neue", Arial, sans-serif; color: #10202e; background: #fff; }
main { max-width: 760px; margin: 0 auto; padding: 24px 8px; }
h1, h2 { font-family: "New York", "Iowan Old Style", Georgia, serif; font-weight: 600; letter-spacing: -.2px; }
h1 { font-size: 22pt; margin: 0 0 2px; }
h2 { font-size: 13.5pt; margin: 22px 0 8px; padding-top: 10px; border-top: 1px solid #d6e0e8; break-after: avoid; }
.sub { color: #50606e; margin: 0 0 14px; }
.facts { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 4px; padding: 0; list-style: none; }
.facts li { padding: 3px 9px; border-radius: 99px; background: #eef3f6; font-weight: 600; font-size: 9.5pt; }
.ok { color: #116149; } .warn { color: #8a5a00; } .fail { color: #a32222; }
li.ok { background: #e3f3ec; } li.warn { background: #fbf0d9; } li.fail { background: #f8e3e3; }
table { width: 100%; border-collapse: collapse; font-size: 9.5pt; }
th { text-align: left; font-weight: 600; color: #50606e; border-bottom: 1px solid #c9d5de; padding: 4px 6px; }
td { vertical-align: top; border-bottom: 1px solid #e3eaf0; padding: 6px; }
tr { break-inside: avoid; }
td.n { width: 28px; color: #50606e; } td.v { width: 104px; font-weight: 600; }
.ref { font-weight: 600; } .meta, .note { color: #50606e; font-size: 9pt; margin-top: 2px; }
.note.fail { color: #a32222; } .note.warn { color: #8a5a00; }
.mono { font-family: "SF Mono", Menlo, monospace; font-size: 8.5pt; }
ul.plain { margin: 4px 0 0; padding-left: 18px; } ul.plain li { margin: 2px 0; }
blockquote { margin: 6px 0; padding: 4px 10px; border-left: 3px solid #c9d5de; font-family: "New York", Georgia, serif; }
footer { margin-top: 26px; padding-top: 8px; border-top: 1px solid #d6e0e8; color: #50606e; font-size: 8.5pt; }
@media screen { body { background: #f3f6f8; } main { background: #fff; padding: 32px 40px; margin: 24px auto; box-shadow: 0 1px 6px rgba(0,0,0,.08); } }
"""


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _when(ts: Optional[float]) -> str:
    t = time.localtime(ts or time.time())
    return time.strftime("%d.%m.%Y, %H:%M" if current() == "de" else "%d %b %Y, %H:%M", t)


def _entry_row(r: Dict, style_notes: List[Dict]) -> str:
    v = r.get("verdict") or "uncertain"
    claimed, found = r.get("claimed") or {}, r.get("found") or {}
    title = claimed.get("title") or found.get("title") or r.get("identifier") or ""
    lines = [f'<div class="ref">{_e(title)}</div>']
    raw = str(r.get("input") or "")
    if raw and raw != title:
        lines.append(f'<div class="meta">{_e(raw[:300])}</div>')
    bits = [b for b in (found.get("venue"), found.get("year"), f'<span class="mono">doi:{_e(r["doi"])}</span>' if r.get("doi") else None) if b]
    if bits:
        lines.append('<div class="meta">' + " · ".join(b if b.startswith("<span") else _e(b) for b in bits) + "</div>")
    if r.get("note"):
        lines.append(f'<div class="note {COLOR.get(v, "")}">{_e(r["note"])}</div>')
    pub = r.get("published_version")
    if pub:
        lines.append('<div class="note">' + _e(tr("Inzwischen veröffentlicht:")) + " "
                     + _e(", ".join(x for x in (pub.get("venue"), str(pub.get("year") or "")) if x))
                     + f' <span class="mono">doi:{_e(pub.get("doi"))}</span></div>')
    if style_notes:
        lines.append('<div class="note warn">' + _e(tr("Stil:")) + " " + _e(" ".join(i["text"] for i in style_notes)) + "</div>")
    return (f'<tr><td class="n">{int(r.get("order", 0)) + 1}</td>'
            f'<td class="v {COLOR.get(v, "")}">{_e(tr(VERDICT_LABEL.get(v, v)))}</td><td>{"".join(lines)}</td></tr>')


def render(job: Dict, job_id: str, auto_print: bool = False) -> str:
    results = sorted(job.get("results") or [], key=lambda r: r.get("order", 0))
    doc = job.get("document") or {}
    style = job.get("style") or {}
    counts = {v: sum(1 for r in results if r.get("verdict") == v) for v in VERDICTS}
    lang = current()
    title = doc.get("name") or tr("Prüfbericht #{id}", id=job_id)
    parts = [f'<h1>{_e(tr("Prüfbericht"))}: {_e(title)}</h1>',
             f'<p class="sub">{_e(tr("{n} Quellen geprüft", n=len(results)))} · {_e(_when(job.get("finished_at") or job.get("created_at")))}</p>',
             '<ul class="facts">' + "".join(f'<li class="{COLOR[v]}">{counts[v]} {_e(tr(VERDICT_LABEL[v]))}</li>'
                                            for v in ORDER if counts.get(v)) + "</ul>"]

    if doc:
        parts.append(f'<h2>{_e(tr("Abgleich mit der Arbeit"))}</h2>')
        parts.append(f'<p>{_e(tr("{e} Einträge im Verzeichnis, {c} Zitate im Text.", e=doc.get("entries", 0), c=doc.get("citations", 0)))}</p>')
        missing, uncited = doc.get("missing_in_list") or [], doc.get("uncited") or []
        if not doc.get("cross_check"):
            parts.append(f'<p class="meta">{_e(tr("Die Zitate im Text ließen sich nicht sicher lesen, zum Beispiel hochgestellte Zahlen. Benthic prüft deshalb nur das Verzeichnis."))}</p>')
        if missing:
            parts.append(f'<p class="fail"><b>{_e(tr("Im Text zitiert, im Verzeichnis nicht gefunden"))}</b></p><ul class="plain">'
                         + "".join(f'<li>{_e(m["label"])}{" (" + str(m["count"]) + "×)" if m.get("count", 1) > 1 else ""}'
                                   f'<div class="meta">{_e(m.get("sentence", ""))}</div></li>' for m in missing[:60]) + "</ul>")
        if uncited:
            parts.append(f'<p><b>{_e(tr("Im Verzeichnis, im Text nie zitiert"))}</b></p><ul class="plain">'
                         + "".join(f'<li>{_e(u["label"])}</li>' for u in uncited[:60]) + "</ul>")
        if doc.get("cross_check") and not missing and not uncited:
            parts.append(f'<p class="ok">{_e(tr("Jedes Zitat steht im Verzeichnis, und jeder Eintrag wird zitiert."))}</p>')

    checked = job.get("quote_check") or []
    if checked:
        parts.append(f'<h2>{_e(tr("Wörtliche Zitate"))}</h2>')
        for q in checked:
            status = q.get("status")
            label = tr(QUOTE_LABEL.get(status, "Nicht prüfbar"))
            page = ""
            if q.get("page_ok") is False:
                page = " · " + tr("Angegeben ist S. {stated}, in der Quelle steht es auf S. {found}.",
                                  stated=q.get("stated_page"), found=q.get("printed_page") or q.get("page"))
            parts.append(f'<blockquote>“{_e(q.get("quote"))}”<div class="meta"><span class="{QUOTE_COLOR.get(status, "")}">'
                         f'<b>{_e(label)}</b></span> · {_e(q.get("label"))}{_e(page)}</div>'
                         + (f'<div class="meta">{_e(tr("In der Quelle:"))} {_e(q.get("excerpt"))}</div>' if status == "close" and q.get("excerpt") else "")
                         + "</blockquote>")

    if style.get("applicable"):
        parts.append(f'<h2>{_e(tr("Zitierstil"))}</h2>')
        noted = sum(1 for v in (style.get("entries") or {}).values() if v)
        parts.append(f'<p>{_e(tr("Erkannt: {style}.", style=(style.get("styles") or {}).get(style.get("style"), style.get("style"))))} '
                     f'{_e(tr("{n} Einträge mit Hinweisen.", n=noted))}</p>')
        if style.get("list"):
            parts.append('<ul class="plain">' + "".join(f'<li>{_e(i["text"])}</li>' for i in style["list"]) + "</ul>")

    mw = job.get("missing_works") or {}
    if mw.get("suggestions"):
        parts.append(f'<h2>{_e(tr("Oft zitiert von deinen Quellen"))}</h2><ul class="plain">'
                     + "".join(f'<li>{_e(w.get("title"))} ({_e(w.get("year"))}) · {_e(tr("Von {n} deiner {of} Quellen zitiert", n=w["count"], of=mw.get("sources")))}'
                               + (f' <span class="mono">doi:{_e(w.get("doi"))}</span>' if w.get("doi") else "") + "</li>"
                               for w in mw["suggestions"]) + "</ul>")

    entries = style.get("entries") or {}
    parts.append(f'<h2>{_e(tr("Alle Quellen"))}</h2><table><thead><tr><th>#</th><th>{_e(tr("Urteil"))}</th>'
                 f'<th>{_e(tr("Quelle"))}</th></tr></thead><tbody>'
                 + "".join(_entry_row(r, entries.get(str(r.get("order", 0))) or []) for r in results) + "</tbody></table>")
    parts.append(f'<footer>{_e(tr("Erstellt mit Benthic {version} am {date}.", version=__version__, date=_when(None)))} '
                 f'{_e(tr("Urteile sind begründete Hinweise aus Crossref, OpenAlex, arXiv und doi.org, keine Gutachten. Bei „Weicht ab“ und „Unsicher“ lohnt der Blick ins Original."))}</footer>')
    script = "<script>addEventListener('load',()=>setTimeout(()=>print(),300))</script>" if auto_print else ""
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{_e(tr("Prüfbericht"))}: {_e(title)}</title>'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><style>{CSS}</style></head>'
            f'<body><main>{"".join(parts)}</main>{script}</body></html>')


def render_batch(b: Dict, auto_print: bool = False) -> str:
    """Sammelbericht einer Stapelprüfung: Summen, eine Zeile je Arbeit, die Probleme im Einzelnen."""
    t = b.get("totals") or {}
    lang = current()
    parts = [f'<h1>{_e(tr("Stapelprüfung"))}: {_e(b.get("name"))}</h1>',
             f'<p class="sub">{_e(b.get("folder"))} · {_e(_when(b.get("finished_at") or b.get("created_at")))}</p>',
             '<ul class="facts">'
             f'<li>{_e(tr("{n} Arbeiten geprüft", n=t.get("checked", 0)))}</li>'
             f'<li class="{"fail" if t.get("flagged_files") else "ok"}">{_e(tr("{n} mit Problemen", n=t.get("flagged_files", 0)))}</li>'
             + (f'<li class="fail">{_e(tr("{n} mit zurückgezogenen Quellen", n=t.get("retracted_files", 0)))}</li>' if t.get("retracted_files") else "")
             + (f'<li class="warn">{_e(tr("{n} nicht lesbar", n=t.get("errors", 0)))}</li>' if t.get("errors") else "")
             + "</ul>"]
    cols = [("entries", "Einträge"), ("missing", "Zitate ohne Eintrag"), ("not_found", "Nicht auffindbar"),
            ("mismatch", "Passt nicht"), ("retracted", "Zurückgezogen"), ("quotes_bad", "Zitate auffällig"), ("style", "Stilhinweise")]
    head = "".join(f"<th>{_e(tr(label))}</th>" for _, label in cols)
    body = []
    for r in b.get("rows") or []:
        if r.get("state") == "error":
            body.append(f'<tr><td class="ref">{_e(r["name"])}</td><td colspan="{len(cols)}" class="note warn">'
                        f'{_e(tr("Kein Literaturverzeichnis erkannt") if r.get("error") == "no-bibliography" else tr("Nicht lesbar"))}</td></tr>')
            continue
        cells = "".join(f'<td class="{"fail" if k in ("missing", "not_found", "mismatch", "retracted", "quotes_bad") and r.get(k) else ""}">{_e(r.get(k, ""))}</td>' for k, _ in cols)
        body.append(f'<tr><td class="ref">{_e(r["name"])}</td>{cells}</tr>')
    parts.append(f'<h2>{_e(tr("Übersicht"))}</h2><table><thead><tr><th>{_e(tr("Datei"))}</th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>')
    labels = {"missing": "Zitate ohne Eintrag", "not_found": "Nicht auffindbar", "mismatch": "Passt nicht",
              "retracted": "Zurückgezogen", "quotes": "Auffällige wörtliche Zitate"}
    for r in b.get("rows") or []:
        problems = {k: v for k, v in (r.get("problems") or {}).items() if v}
        if not problems:
            continue
        parts.append(f'<h2>{_e(r["name"])}</h2>')
        for k, items in problems.items():
            parts.append(f'<p><b>{_e(tr(labels[k]))}</b></p><ul class="plain">' + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>")
    parts.append(f'<footer>{_e(tr("Erstellt mit Benthic {version} am {date}.", version=__version__, date=_when(None)))} '
                 f'{_e(tr("Die Arbeiten blieben auf dem Mac; geprüft wurden nur die Literaturangaben."))}</footer>')
    script = "<script>addEventListener('load',()=>setTimeout(()=>print(),300))</script>" if auto_print else ""
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{_e(tr("Stapelprüfung"))}: {_e(b.get("name"))}</title>'
            f'<style>{CSS}</style></head><body><main>{"".join(parts)}</main>{script}</body></html>')
