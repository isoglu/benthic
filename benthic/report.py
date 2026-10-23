"""Prüfbericht als gestaltetes A4-Dokument: Deckblatt, Überblick, Inhalt, Listen und Nachweis mit Siegel.

Die App druckt die Seite über macOS in eine PDF (ReportBridge in BenthicApp.swift, ohne Ränder); im Browser
öffnet ``?print=1`` den Druckdialog. Die Seite ist eigenständig: eigenes CSS, das Zeichen als SVG, ein kleines
Skript, das den Inhalt auf A4-Seiten verteilt (laufende Kopf- und Fußzeile, Seitenzahlen, Inhaltsverzeichnis)
und danach gegebenenfalls druckt. Das Siegel (seal.py) steht als QR-Code auf dem Deckblatt und auf der letzten
Seite, seine Nummer im Fuß jeder Seite.
"""

from __future__ import annotations

import base64
import html
import math
import re
import struct
import time
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import __version__, identity, integrity, seal
from .i18n import current, tr
from .verify import VERDICT_ADVICE, VERDICT_LABEL, VERDICTS

ORDER = ("retracted", "not_found", "mismatch", "uncertain", "partial", "corrected", "verified")
COLOR = {"verified": "ok", "corrected": "ok", "partial": "warn", "uncertain": "warn", "mismatch": "fail",
         "not_found": "fail", "retracted": "fail"}
OK, WARN, BAD = ("verified", "corrected"), ("partial", "uncertain"), ("mismatch", "not_found", "retracted")
ATTENTION = ("retracted", "not_found", "mismatch", "uncertain", "partial", "corrected")
# Füllfarben für Balken und Quellenkarte (Familien wie in der App: Petrol, Bernstein, Rot).
FILL = {"verified": "#08697d", "corrected": "#58a7b5", "partial": "#d9ab45", "uncertain": "#b07d0e",
        "mismatch": "#e2705d", "not_found": "#c23b2c", "retracted": "#7a1b17"}
EXPLAIN = {
    "verified": "Quelle existiert und passt zur Angabe",
    "corrected": "Quelle existiert, die angegebene DOI war falsch oder fehlte",
    "partial": "Quelle existiert, aber Jahr, Titel oder Autor:innen weichen ab",
    "uncertain": "Ähnlicher Treffer, bitte selbst prüfen",
    "mismatch": "Die DOI gehört zu einem anderen Werk",
    "not_found": "Kein passendes Werk gefunden, vermutlich erfunden",
    "retracted": "Die Arbeit wurde zurückgezogen",
}
QUOTE_LABEL = {"exact": "Wortgleich", "close": "Wortlaut weicht ab", "not_found": "Nicht in der Quelle gefunden",
               "no_pdf": "PDF fehlt", "no_source": "Quelle nicht bestätigt", "pending": "Quelle wird noch geprüft",
               "wrong_pdf": "Falsches PDF", "incomplete": "Nicht in dieser Fassung",
               "misattributed": "Steht in einer anderen Quelle"}
QUOTE_COLOR = {"exact": "ok", "close": "warn", "not_found": "fail", "wrong_pdf": "warn", "misattributed": "warn"}
MONTHS = {"de": ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
                 "November", "Dezember"),
          "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                 "November", "December")}
STATIC = Path(__file__).resolve().parent / "static"


def _web(url: object) -> str:
    """Links aus fremden Daten nur mit http oder https (kein javascript:, file: oder Schema einer App)."""
    text = str(url or "").strip()
    return text if re.match(r"(?i)https?://", text) else ""


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _when(ts: Optional[float]) -> str:
    t = time.localtime(ts or time.time())
    return time.strftime("%d.%m.%Y, %H:%M" if current() == "de" else "%d %b %Y, %H:%M", t)


def _date(ts: Optional[float], clock: bool = True) -> str:
    t = time.localtime(ts or time.time())
    month = MONTHS[current()][t.tm_mon - 1]
    day = f"{t.tm_mday}. {month} {t.tm_year}" if current() == "de" else f"{t.tm_mday} {month} {t.tm_year}"
    return day + (f", {t.tm_hour:02d}:{t.tm_min:02d}" if clock else "")


def _pct(part: int, whole: int) -> str:
    if not whole:
        return ""
    value = round(100 * part / whole)
    return f"{value} %" if current() == "de" else f"{value}%"


def _num(n: int) -> str:
    text = f"{int(n):,}"
    return text.replace(",", ".") if current() == "de" else text


def _sources(n: int) -> str:
    return tr("1 Quelle") if n == 1 else tr("{n} Quellen", n=n)


# --------------------------------------------------------------------------
# Zeichen und Isobathen
# --------------------------------------------------------------------------
_GRADIENT = re.compile(r'<radialGradient\b([^>]*\bid="([^"]+)"[^>]*)>(.*?)</radialGradient>', re.S)


def _hex(color: str) -> Tuple[int, int, int]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


@lru_cache(maxsize=64)
def _radial_png(stops: Tuple[Tuple[float, Tuple[int, int, int], float], ...], size: int = 96) -> str:
    """Radialer Verlauf als RGBA-PNG (data:-Adresse), Mitte im Bild, Radius = halbe Kantenlänge."""
    rows = []
    half = size / 2
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            t = min(1.0, math.hypot(x + 0.5 - half, y + 0.5 - half) / half)
            for i in range(len(stops) - 1):
                (o0, c0, a0), (o1, c1, a1) = stops[i], stops[i + 1]
                if t <= o1 or i == len(stops) - 2:
                    k = 0.0 if o1 <= o0 else max(0.0, min(1.0, (t - o0) / (o1 - o0)))
                    break
            if t < stops[0][0]:
                (o0, c0, a0), (o1, c1, a1), k = stops[0], stops[0], 0.0
            row += bytes(round(c0[j] + (c1[j] - c0[j]) * k) for j in range(3))
            row.append(round(255 * (a0 + (a1 - a0) * k)))
        rows.append(bytes(row))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _print_safe(svg: str) -> str:
    """WebKit druckt Verläufe mit stop-opacity deckend und verschiebt SVG-Masken. Daher wird jeder radiale
    Verlauf mit Transparenz zu einem kleinen PNG (das druckt richtig), beschnitten auf die Form, die ihn nutzte."""
    found: Dict[str, Dict] = {}

    def collect(m: re.Match) -> str:
        attrs, gid, body = m.groups()
        if "stop-opacity" not in body or 'gradientUnits="userSpaceOnUse"' not in attrs:
            return m.group(0)
        num = lambda name, default: float((re.search(rf'\b{name}="([-\d.]+)"', attrs) or [None, default])[1])
        stops = []
        for st in re.findall(r"<stop\b[^>]*>", body):
            off = float((re.search(r'offset="([-\d.]+)"', st) or [None, 0])[1])
            col = _hex((re.search(r'stop-color="(#[0-9A-Fa-f]{3,6})"', st) or [None, "#000"])[1])
            alpha = float((re.search(r'stop-opacity="([-\d.]+)"', st) or [None, 1])[1])
            stops.append((off, col, alpha))
        found[gid] = {"cx": num("cx", 0), "cy": num("cy", 0), "r": num("r", 1), "stops": tuple(stops)}
        return ""

    svg = _GRADIENT.sub(collect, svg)
    clips = []
    for gid, g in found.items():
        uri = _radial_png(g["stops"])
        x, y, d = g["cx"] - g["r"], g["cy"] - g["r"], 2 * g["r"]

        def use(m: re.Match, gid=gid, uri=uri, x=x, y=y, d=d) -> str:
            tag, before, after = m.groups()
            cid = f"{gid}-c{len(clips)}"
            clips.append(f'<clipPath id="{cid}"><{tag}{before}{after}/></clipPath>')
            return (f'<g clip-path="url(#{cid})"><image href="{uri}" x="{x:.2f}" y="{y:.2f}" width="{d:.2f}" '
                    f'height="{d:.2f}" preserveAspectRatio="none"/></g>')

        svg = re.sub(rf'<(rect|circle|path)\b([^>]*?)\s*fill="url\(#{re.escape(gid)}\)"([^>]*?)\s*/>', use, svg)
    if clips:
        svg = re.sub(r"<defs>", "<defs>" + "".join(clips), svg, count=1) if "<defs>" in svg else svg
    return svg


def _sprite() -> str:
    """Das Benthic-Zeichen (static/favicon.svg) als Symbol #bm, damit jede Seite es mit <use> zeigt."""
    try:
        src = (STATIC / "favicon.svg").read_text(encoding="utf-8")
    except OSError:
        return ""
    defs = re.search(r"<defs>(.*?)</defs>", src, re.S)
    body = re.search(r"</defs>(.*)</svg>", src, re.S)
    if not defs or not body:
        return ""
    return _print_safe(f'<svg class="sprite" width="0" height="0" aria-hidden="true"><defs>{defs.group(1)}</defs>'
                       f'<symbol id="bm" viewBox="0 0 64 64">{body.group(1)}</symbol></svg>')


def _smooth(pts: List[Tuple[float, float]], segments: bool = False):
    """Geschlossene, glatte Kurve durch die Punkte (Catmull-Rom als kubische Bézierkurven). Mit ``segments``:
    (Startpunkt, Liste der C-Stücke, Mittelpunkte der Stücke) statt eines fertigen Pfads."""
    n = len(pts)
    f = lambda p: f"{p[0]:.2f} {p[1]:.2f}"
    segs, mids = [], []
    for i in range(n):
        p0, p1, p2, p3 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        segs.append(f"C{f(c1)} {f(c2)} {f(p2)}")
        mids.append(((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2))
    if segments:
        return f(pts[0]), segs, mids
    return f"M{f(pts[0])}" + "".join(segs) + "Z"


def _isobaths(cx: float, cy: float, count: int, r0: float, step: float, grow: float, sy: float = 0.9,
              first: int = 0, segments: bool = False) -> List:
    """Tiefenlinien wie im App-Symbol und im Installationsfenster: dieselbe Familie, nach außen weiter."""
    rings = []
    for k in range(first, first + count):
        r = r0 + step * k + grow * k * k + 0.25 * step * math.sin(k * 1.3)
        kk = k * 0.55
        ox, oy = cx + 0.1 * r, cy - 0.06 * r
        pts = []
        for i in range(44):
            t = i / 44 * 2 * math.pi
            w = 1 + 0.95 * (0.085 * math.sin(2 * t + 0.6 + 0.12 * kk) + 0.06 * math.sin(3 * t + 1.9 - 0.1 * kk)
                            + 0.035 * math.sin(5 * t + 0.3 + 0.2 * kk))
            pts.append((ox + r * w * math.cos(t), oy + r * w * sy * math.sin(t)))
        rings.append(_smooth(pts, segments) if segments else _smooth(pts))
    return rings


def _sea() -> str:
    """Hintergrund des Deckblatts (mm): tiefes Wasser, Licht von oben, ein Becken aus Tiefenlinien. Hinter Titel
    und Zusammenfassung werden die Linien leiser, wie Seekarten Linien unter Beschriftungen zurücknehmen; die
    Linien sind dafür in Stücke mit eigener Deckkraft geteilt (Masken druckt WebKit nicht zuverlässig)."""
    cx, cy = 150.0, 112.0
    rings = _isobaths(cx, cy, 26, 5.0, 6.4, 0.2, segments=True)
    bands = ["#0c3560", "#0b2f57", "#0a2a4f", "#092546", "#08203e", "#071b36", "#06172f", "#051329"]
    fills = "".join(f'<path d="M{start}{"".join(segs)}Z" fill="{bands[i]}" fill-opacity="{0.35 + 0.08 * i:.2f}"/>'
                    for i, (start, segs, _) in enumerate(rings[:8][::-1]))  # innen dunkler: das Becken

    def smooth(a: float, b: float, v: float) -> float:
        t = max(0.0, min(1.0, (v - a) / (b - a)))
        return t * t * (3 - 2 * t)

    def fade(x: float, y: float) -> float:
        fx = 0.34 + 0.66 * smooth(12, 128, x)
        fy = 1 - 0.74 * smooth(136, 184, y)
        return fx * fy

    lines = []
    for k, (start, segs, mids) in enumerate(rings):
        heavy = k % 5 == 4
        base = min(0.85, max(0.12, 0.66 - k * 0.02) * (1.3 if heavy else 1))
        width = 0.42 if heavy else 0.22
        run, run_alpha, pen = [], None, start
        for seg, (mx, my), end in zip(segs, mids, [s.rsplit(" ", 2)[-2:] for s in segs]):
            alpha = round(base * fade(mx, my), 2)
            if alpha != run_alpha and run:
                lines.append(f'<path d="M{run[0]}{"".join(run[1:])}" stroke-opacity="{run_alpha}" stroke-width="{width}"/>')
                run = []
            if not run:
                run = [pen]
            run.append(seg)
            run_alpha = alpha
            pen = " ".join(end)
        if run:
            lines.append(f'<path d="M{run[0]}{"".join(run[1:])}" stroke-opacity="{run_alpha}" stroke-width="{width}"/>')
    return _print_safe(f"""<svg class="sea" viewBox="0 0 210 297" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
<defs>
<linearGradient id="sea-g" x1="0" y1="0" x2="0" y2="297" gradientUnits="userSpaceOnUse">
<stop offset="0" stop-color="#0f3f70"/><stop offset=".45" stop-color="#0a2b50"/><stop offset=".78" stop-color="#061631"/><stop offset="1" stop-color="#040f20"/></linearGradient>
<radialGradient id="sea-a" cx="20" cy="-14" r="160" gradientUnits="userSpaceOnUse">
<stop offset="0" stop-color="#3cc2ee" stop-opacity=".40"/><stop offset="1" stop-color="#3cc2ee" stop-opacity="0"/></radialGradient>
<radialGradient id="sea-b" cx="205" cy="-24" r="135" gradientUnits="userSpaceOnUse">
<stop offset="0" stop-color="#8f7cff" stop-opacity=".30"/><stop offset="1" stop-color="#8f7cff" stop-opacity="0"/></radialGradient>
<radialGradient id="sea-lit" cx="{cx}" cy="{cy}" r="17" gradientUnits="userSpaceOnUse">
<stop offset="0" stop-color="#e8fbff" stop-opacity=".95"/><stop offset=".22" stop-color="#8fe3ff" stop-opacity=".55"/><stop offset="1" stop-color="#5cd3f2" stop-opacity="0"/></radialGradient>
</defs>
<rect width="210" height="297" fill="url(#sea-g)"/>
<rect width="210" height="297" fill="url(#sea-a)"/><rect width="210" height="297" fill="url(#sea-b)"/>
{fills}<g fill="none" stroke="#9fe6ff">{"".join(lines)}</g>
<circle cx="{cx}" cy="{cy}" r="17" fill="url(#sea-lit)"/><circle cx="{cx}" cy="{cy}" r="1.25" fill="#ffffff"/>
</svg>""")


def _corner() -> str:
    """Ein Ausschnitt derselben Tiefenlinien oben rechts auf jeder Innenseite."""
    rings = _isobaths(214, -6, 9, 7, 5.2, 0.16)
    paths = "".join(f'<path d="{d}" stroke-opacity="{0.34 - i * 0.026:.3f}" stroke-width="{0.32 if i % 5 == 4 else 0.2}"/>'
                    for i, d in enumerate(rings))
    return (f'<svg class="corner" viewBox="130 0 80 46" aria-hidden="true"><g fill="none" stroke="#2e9fd3">{paths}</g>'
            f'<circle cx="214" cy="-6" r="1" fill="#2e9fd3" fill-opacity=".5"/></svg>')


# --------------------------------------------------------------------------
# Bausteine
# --------------------------------------------------------------------------
def _bar(counts: Dict[str, int], total: int, cls: str = "bar") -> str:
    segs = "".join(f'<i style="flex:{counts[v]} 1 0;background:{FILL[v]}" title="{_e(tr(VERDICT_LABEL[v]))}"></i>'
                   for v in VERDICTS if counts.get(v))
    return f'<div class="{cls}">{segs}</div>' if total else ""


def _legend(counts: Dict[str, int], total: int, dark: bool = False) -> str:
    items = []
    for v in VERDICTS:
        n = counts.get(v, 0)
        if not n and dark:
            continue
        items.append(f'<li class="{"zero" if not n else ""}"><i style="background:{FILL[v]}"></i>'
                     f'<b>{n}</b> {_e(tr(VERDICT_LABEL[v]))}'
                     + (f'<span class="pc">{_pct(n, total)}</span>' if n and not dark else "") + "</li>")
    return f'<ul class="legend{" dark" if dark else ""}">' + "".join(items) + "</ul>"


def _source_map(results: List[Dict]) -> str:
    """Jede Quelle ein Feld, in der Reihenfolge des Verzeichnisses; Probleme in voller Farbe."""
    n = len(results)
    if not n:
        return ""
    width, height, gap = 169.0, 52.0, 0.9  # Innenbreite der Fläche (178 mm minus Polster)
    size = max(3.4, min(8.0, math.floor(math.sqrt(width * height / n) * 10) / 10 - gap))
    cols = max(1, int((width + gap) // (size + gap)))
    cells = []
    for r in results:
        v = r.get("verdict") or "uncertain"
        num = int(r.get("order", 0)) + 1
        solid = v not in OK
        style = f"background:{FILL[v]}" if solid else ""
        label = str(num) if size >= 5 else ""
        cells.append(f'<a class="cell{" solid" if solid else ""} {v}" href="#reg-{num}" style="{style}" '
                     f'title="{num}: {_e(tr(VERDICT_LABEL.get(v, v)))}">{label}</a>')
    font = max(4.6, min(7.0, size * 0.95))
    return (f'<div class="map" style="grid-template-columns:repeat({cols},{size}mm);grid-auto-rows:{size}mm;'
            f'gap:{gap}mm;font-size:{font:.1f}pt">' + "".join(cells) + "</div>")


def _title_of(r: Dict) -> str:
    claimed, found = r.get("claimed") or {}, r.get("found") or {}
    if r.get("verdict") in ("verified", "corrected", "partial") and found.get("title"):
        return str(found["title"])
    return str(claimed.get("title") or found.get("title") or r.get("identifier") or r.get("input") or "")


def _byline(r: Dict) -> str:
    claimed, found = r.get("claimed") or {}, r.get("found") or {}
    src = found if found.get("title") and r.get("verdict") not in ("not_found",) else claimed
    authors = [a for a in (src.get("authors") or claimed.get("authors") or []) if a]
    who = ""
    if authors:
        who = str(authors[0]) + (" " + tr("u. a.") if len(authors) > 1 or (src.get("author_count") or 0) > 1 else "")
    return " · ".join(x for x in (who, str(src.get("venue") or ""), str(src.get("year") or claimed.get("year") or "")) if x)


def _doi(doi: Optional[str]) -> str:
    return f'<span class="mono">{_e(doi)}</span>' if doi else ""


def _diffs(r: Dict) -> List[tuple]:
    """Abweichende Felder als (Eintrag, „Band: 13 → 12“); bei „Nicht auffindbar“ ist der Treffer ein anderes Werk."""
    if r.get("verdict") == "not_found":
        return []
    labels = {"title": tr("Titel"), "authors": tr("Autor:innen"), "year": tr("Jahr"), "venue": tr("Zeitschrift"),
              "volume": tr("Band"), "issue": tr("Heft"), "pages": tr("Seiten"), "author_count": tr("Anzahl Autor:innen"),
              "cited_page": tr("Zitierte Seite")}
    return [(d, f'{labels.get(field, field)}: {d.get("claimed", "")} → {d.get("found", "")}')
            for field, d in (r.get("field_diffs") or {}).items() if isinstance(d, dict) and d.get("status") != "ok"]


def _attention_card(r: Dict, shared_note: Optional[str] = None) -> str:
    v = r.get("verdict") or "uncertain"
    num = int(r.get("order", 0)) + 1
    claimed, found = r.get("claimed") or {}, r.get("found") or {}
    raw = re.sub(r"^\s*(?:\[\d+\]|\(\d+\)|\d+[.)])\s+", "", str(r.get("input") or claimed.get("title") or r.get("identifier") or ""))
    lines = [f'<p class="cited">{_e(raw[:360])}</p>']
    if found.get("title") and v != "not_found":
        bits = [b for b in (str(found.get("venue") or ""), str(found.get("year") or "")) if b]
        lines.append(f'<p class="found"><span class="k">{_e(tr("Gefunden"))}</span> {_e(found["title"])}'
                     + (f' <span class="m">{_e(" · ".join(bits))}</span>' if bits else "")
                     + (f' {_doi(r.get("doi"))}' if r.get("doi") else "") + "</p>")
    elif r.get("doi"):
        lines.append(f'<p class="found">{_doi(r.get("doi"))}</p>')
    web = r.get("web") or {}
    if web.get("url"):
        bits = [f'<span class="mono">{_e(web["url"])}</span>']
        if _web(web.get("archive_url")):
            bits.append(f'<a href="{_e(_web(web["archive_url"]))}">{_e(tr("Im Internet Archive ({date})", date=web.get("archive_date") or ""))}</a>')
        try:
            accessed = time.mktime(time.strptime(str(web.get("accessed") or ""), "%Y-%m-%d"))
            bits.append(_e(tr("Abgerufen am {date}", date=_date(accessed, clock=False))))
        except ValueError:
            pass
        lines.append('<p class="found">' + " · ".join(bits) + "</p>")
    if r.get("note") and r.get("note") != shared_note:
        lines.append(f'<p class="note {COLOR.get(v, "")}">{_e(r["note"])}</p>')
    diff_pills = [f'<span class="pill-diff {d.get("status")}">{_e(text)}</span>' for d, text in _diffs(r)]
    if diff_pills:
        lines.append(f'<p class="field-diffs">{" ".join(diff_pills)}</p>')
    if r.get("pattern") == "chimera":
        lines.append(f'<p class="chimera-tag"><span class="pill fail">{_e(tr("Mischangabe (echte Autor:innen, erfundener Titel)"))}</span></p>')
    pub = r.get("published_version")
    if pub:
        lines.append('<p class="note">' + _e(tr("Inzwischen veröffentlicht:")) + " "
                     + _e(", ".join(x for x in (pub.get("venue"), str(pub.get("year") or "")) if x))
                     + f' {_doi(pub.get("doi"))}</p>')
    return (f'<article class="row card {v}" id="src-{num}"><span class="num">{num}</span>'
            f'<div class="main">{"".join(lines)}</div></article>')


def _attention(attention: List[Dict]) -> List[str]:
    """Handlungsbedarf, nach Urteil gruppiert; ein Hinweis, den die meisten Einträge einer Gruppe teilen, steht
    einmal über der Gruppe statt in jeder Zeile. Lange Listen werden kompakt (zwei Zeilen je Quelle)."""
    out = []
    compact = len(attention) > 12
    for v in ATTENTION:
        group = [r for r in attention if r.get("verdict") == v]
        if not group:
            continue
        notes: Dict[str, int] = {}
        for r in group:
            if r.get("note"):
                notes[r["note"]] = notes.get(r["note"], 0) + 1
        common = max(notes.items(), key=lambda kv: kv[1]) if notes else (None, 0)
        shared = common[0] if common[1] >= 3 and common[1] * 2 > len(group) else None
        solid = " solid" if v == "retracted" else ""
        out.append(f'<div class="h grp"><div class="gh"><h3><span class="pill {COLOR[v]}{solid}">{_e(tr(VERDICT_LABEL[v]))}</span>'
                   f'<span class="cnt">{len(group)}</span></h3><p>{_e(shared or tr(EXPLAIN[v]))}</p></div>'
                   f'<p class="adv"><b>{_e(tr("Was tun:"))}</b> {_e(tr(VERDICT_ADVICE[v]))}</p></div>')
        out.append(f'<div class="split cards{" compact" if compact else ""}">'
                   + "".join(_attention_card(r, shared) for r in group) + "</div>")
    return out


def _register_row(r: Dict) -> str:
    v = r.get("verdict") or "uncertain"
    num = int(r.get("order", 0)) + 1
    by = _byline(r)
    diffs = "; ".join(text for _d, text in _diffs(r)) if v not in ATTENTION else ""  # Handlungsbedarf zeigt sie schon
    return (f'<div class="row reg {v}" id="reg-{num}"><span class="n">{num}</span>'
            f'<span class="v {COLOR.get(v, "")}"><i style="background:{FILL.get(v, "#999")}"></i>{_e(tr(VERDICT_LABEL.get(v, v)))}</span>'
            f'<span class="ref"><b>{_e(_title_of(r)[:300])}</b>' + (f' <span class="m">{_e(by)}</span>' if by else "")
            + (f' <span class="m warn">{_e(diffs)}</span>' if diffs else "")
            + f'</span><span class="doi">{_e(r.get("doi") or (r.get("found") or {}).get("url") or "")}</span></div>')


def _heading(anchor: str, title: str, lede: str = "") -> str:
    return (f'<div class="h" id="{anchor}"><h2>{_e(title)}</h2>'
            + (f'<p class="lede">{_e(lede)}</p>' if lede else "") + "</div>")


def _sub(title: str) -> str:
    return f'<div class="h sub"><h3>{_e(title)}</h3></div>'


# --------------------------------------------------------------------------
# Hülle: CSS, Seitenvorlage, Umbruch
# --------------------------------------------------------------------------
CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
html, body { margin: 0; padding: 0; }
body { font: 8.8pt/1.42 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
       color: #0b1830; background: #fff; -webkit-font-smoothing: antialiased; font-kerning: normal; }
.sprite { position: absolute; width: 0; height: 0; overflow: hidden; }
a { color: inherit; text-decoration: none; }
.mono, .doi { font-family: ui-monospace, "SF Mono", SFMono-Regular, Menlo, monospace; font-size: .86em; letter-spacing: -.01em; }
.ok { color: #08697d; } .warn { color: #8a5a00; } .fail { color: #b3302a; }
#flow { display: none; }

/* Seite */
.page { position: relative; width: 210mm; height: 297mm; overflow: hidden; background: #fff;
        break-after: page; page-break-after: always; }
#book > .page:last-child { break-after: auto; page-break-after: auto; }
.inner .body { position: absolute; left: 16mm; right: 16mm; top: 21mm; bottom: 17mm; overflow: hidden; }
.corner { position: absolute; top: 0; right: 0; width: 80mm; height: 46mm; }
header.run { position: absolute; left: 16mm; right: 16mm; top: 9.5mm; display: flex; align-items: center; gap: 2.4mm;
             font-size: 7.4pt; color: #4a5a75; }
header.run svg { width: 4.6mm; height: 4.6mm; }
header.run .wm { font-weight: 650; color: #0b1830; letter-spacing: -.01em; font-size: 8.4pt; }
header.run .sep { width: .25mm; height: 3mm; background: #c3cfdd; }
header.run .doc { margin-left: auto; max-width: 92mm; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
footer.run { position: absolute; left: 16mm; right: 16mm; bottom: 8.5mm; display: flex; align-items: center; gap: 3mm;
             font-size: 6.8pt; color: #7a889e; border-top: .2mm solid #dde5ef; padding-top: 2mm; }
footer.run .pno { margin-left: auto; color: #4a5a75; font-variant-numeric: tabular-nums; font-weight: 600; }

/* Überschriften */
.h { margin: 0 0 3mm; }
.h h2 { font: 650 19pt/1.12 -apple-system, BlinkMacSystemFont, "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
        letter-spacing: -.022em; margin: 0; color: #0b1830; }
.h .lede { margin: 1.2mm 0 0; color: #4a5a75; font-size: 9pt; }
.body > .h:not(:first-child) { margin-top: 8mm; }
.body > .h.sub:not(:first-child) { margin-top: 5mm; }
.h + .h { margin-top: 0; }
.h.sub { margin: 5mm 0 2mm; }
.h.sub h3, .panel h3, .block h3 { font: 650 10pt/1.25 -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
        letter-spacing: -.01em; margin: 0 0 2mm; color: #0b1830; }
.gap { height: 6mm; }

/* Deckblatt */
.cover { background: #061a33; color: #eaf2ff; }
.cover .sea { position: absolute; inset: 0; width: 100%; height: 100%; }
.cover .top { position: absolute; left: 18mm; right: 18mm; top: 17mm; display: flex; align-items: center; }
.cover .brand { display: flex; align-items: center; gap: 3mm; }
.cover .brand svg { width: 10mm; height: 10mm; }
.cover .brand span { font: 650 17pt/1 -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif; letter-spacing: -.024em; color: #fff; }
.cover .meta { margin-left: auto; text-align: right; font-size: 8pt; line-height: 1.5; color: #a9c3e0; }
.cover .meta b { color: #eaf2ff; font-weight: 600; }
.cover .title { position: absolute; left: 18mm; right: 18mm; top: 74mm; }
.cover h1 { font: 700 46pt/1 -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif; letter-spacing: -.035em; margin: 0;
            color: #fff; }
.cover .docname { font: italic 400 17pt/1.22 ui-serif, "New York", "Iowan Old Style", Georgia, serif; margin: 5mm 0 0;
                  max-width: 128mm; color: #d6ecff; overflow-wrap: anywhere; }
.cover .docname.long { font-size: 13pt; }
.cover .docname .ext { font-style: normal; font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: .58em; color: #8fb3d6; margin-left: 1mm; }
.cover .summary { position: absolute; left: 18mm; right: 18mm; top: 172mm; }
.cover .verdict { font: 600 15pt/1.25 -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif; letter-spacing: -.015em;
                  margin: 0 0 4.5mm; color: #fff; max-width: 150mm; }
.cover .bar { height: 3.2mm; border-radius: 2mm; overflow: hidden; display: flex; gap: .5mm; background: rgba(255,255,255,.08);
              box-shadow: 0 0 0 .3mm rgba(255,255,255,.14); }
.cover .bar i { display: block; height: 100%; }
.legend { list-style: none; margin: 3mm 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: 1.6mm 5mm; font-size: 8pt; color: #4a5a75; }
.legend li { display: flex; align-items: center; gap: 1.4mm; white-space: nowrap; }
.legend li i { width: 2.4mm; height: 2.4mm; border-radius: .7mm; display: inline-block; }
.legend li b { color: #0b1830; font-weight: 650; font-variant-numeric: tabular-nums; }
.legend li.zero { opacity: .45; }
.legend .pc { color: #7a889e; margin-left: .6mm; font-variant-numeric: tabular-nums; }
.legend.dark { color: #a9c3e0; } .legend.dark b { color: #fff; }
.cover .seal { position: absolute; left: 14mm; right: 14mm; bottom: 20mm; display: flex; gap: 6mm; align-items: center;
               padding: 5mm; border-radius: 5mm; background: rgba(9, 28, 54, .62);
               box-shadow: inset 0 .3mm 0 rgba(255,255,255,.16), 0 0 0 .25mm rgba(160,190,255,.22); }
.cover .seal .tile { flex: none; background: #fff; border-radius: 2.6mm; padding: 1.2mm; line-height: 0; }
.cover .seal h2 { font: 650 12pt/1.2 -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif; margin: 0 0 1.6mm; color: #fff; letter-spacing: -.01em; }
.cover .seal p { margin: 0 0 1.6mm; font-size: 8.4pt; line-height: 1.45; color: #c4d6ec; }
.cover .seal p.id { margin: 2.4mm 0 0; font-size: 7.6pt; color: #8fe3ff; }
.cover .seal p.id span { color: #7e98b8; font-family: -apple-system, sans-serif; letter-spacing: 0; }
.cover .foot { position: absolute; left: 18mm; right: 18mm; bottom: 9mm; display: flex; font-size: 7pt; color: #7e98b8; }
.cover .foot span:last-child { margin-left: auto; }

/* Überblick */
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 3mm; margin: 0 0 4mm; }
.kpi { border-radius: 3.2mm; padding: 3.4mm 3.6mm 3mm; background: #f4f7fb; box-shadow: inset 0 0 0 .2mm #dde5ef; }
.kpi .big { font: 650 25pt/1 -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif; letter-spacing: -.03em; font-variant-numeric: tabular-nums; }
.kpi .lbl { margin-top: 1.4mm; font-weight: 600; font-size: 8.4pt; }
.kpi .sm { color: #4a5a75; font-size: 7.4pt; margin-top: .4mm; line-height: 1.3; }
.kpi.lead { background: linear-gradient(160deg, #0d3b69, #071a2a); color: #fff; box-shadow: none; }
.kpi.lead .sm { color: #a9c3e0; }
.kpi.ok .big { color: #08697d; } .kpi.warn .big { color: #8a5a00; } .kpi.fail .big { color: #b3302a; }
.kpi.zero .big { color: #9aa8bb; }
.panel { border-radius: 3.2mm; padding: 3.6mm 4mm; box-shadow: inset 0 0 0 .2mm #dde5ef; margin: 0 0 4mm; }
.panel .hint { margin: -1.2mm 0 2.4mm; color: #4a5a75; font-size: 7.6pt; }
.dash .bar { height: 5mm; border-radius: 1.6mm; overflow: hidden; display: flex; gap: .5mm; }
.dash .bar i { display: block; height: 100%; }
.map { display: grid; justify-content: start; }
.map .cell { display: flex; align-items: center; justify-content: center; border-radius: .8mm; background: #e1f0f3;
             color: #08697d; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1; }
.map .cell.corrected { background: #eef6f8; box-shadow: inset 0 0 0 .25mm #58a7b5; }
.map .cell.solid { color: #fff; }
.trio { display: grid; grid-template-columns: repeat(3, 1fr); gap: 3mm; margin: 0 0 4mm; }
.trio .panel { margin: 0; }
.stat { display: flex; justify-content: space-between; gap: 2mm; padding: .9mm 0; border-bottom: .2mm solid #eef2f7; font-size: 8pt; }
.stat:last-child { border-bottom: 0; }
.stat b { font-variant-numeric: tabular-nums; white-space: nowrap; }
.stat b.zero { color: #94a3b8; font-weight: 500; }
.stat .fail, .stat .warn, .stat .ok { font-weight: 650; }
.muted { color: #4a5a75; }
.todo { list-style: none; margin: 0; padding: 0; }
.todo li { display: grid; grid-template-columns: 2.4mm 8mm 1fr auto; gap: 2mm; align-items: baseline; padding: 1.5mm 0;
           border-bottom: .2mm solid #eef2f7; }
.todo li:last-child { border-bottom: 0; }
.todo li i { width: 2.4mm; height: 2.4mm; border-radius: 50%; display: inline-block; transform: translateY(.2mm); }
.todo .n { color: #4a5a75; font-variant-numeric: tabular-nums; font-weight: 600; }
.todo .t { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.todo .t small { color: #4a5a75; font-size: 7.4pt; margin-left: 1.2mm; }
.todo .pg { color: #4a5a75; font-size: 7.4pt; white-space: nowrap; }
.allgood { display: flex; gap: 3mm; align-items: center; color: #08697d; font-weight: 600; }

/* Inhalt */
.toc { list-style: none; margin: 2mm 0 8mm; padding: 0; counter-reset: toc; }
.toc li { counter-increment: toc; }
.toc a { display: flex; align-items: baseline; gap: 2.4mm; padding: 2.6mm 0; border-bottom: .2mm solid #e3eaf2; }
.toc a::before { content: counter(toc, decimal-leading-zero); font: 600 8pt/1 ui-monospace, "SF Mono", Menlo, monospace; color: #2e9fd3; width: 7mm; }
.toc .t { font: 600 12pt/1.2 -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif; letter-spacing: -.012em; }
.toc .c { color: #4a5a75; font-size: 8.4pt; }
.toc .dots { flex: 1; border-bottom: .3mm dotted #c3cfdd; transform: translateY(-1mm); }
.toc .p { font: 650 11pt/1 -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif; font-variant-numeric: tabular-nums; min-width: 6mm; text-align: right; }
.verdicts { display: grid; grid-template-columns: 1fr 1fr; gap: 0 6mm; margin: 0 0 6mm; }
.verdicts div { display: grid; grid-template-columns: 30mm 1fr; gap: 2mm; padding: 1.8mm 0; border-bottom: .2mm solid #eef2f7; align-items: baseline; }
.verdicts p { margin: 0; color: #4a5a75; font-size: 8pt; }
.pill { display: inline-block; font-size: 7.2pt; font-weight: 650; padding: .5mm 1.8mm; border-radius: 3mm; white-space: nowrap; }
.pill.ok { background: rgba(8,105,125,.10); } .pill.warn { background: rgba(138,90,0,.10); } .pill.fail { background: rgba(179,48,42,.09); }
.retracted .pill.fail, .pill.solid { background: #7a1b17; color: #fff; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 6mm; }
.block p { margin: 0 0 1.6mm; color: #26354d; }
.block ul { margin: 0; padding-left: 4mm; color: #26354d; } .block li { margin: .6mm 0; }

/* Handlungsbedarf */
.h.grp { margin: 4.5mm 0 1.6mm; padding-bottom: 1.6mm; border-bottom: .3mm solid #c3cfdd; }
.h.grp .gh { display: flex; align-items: baseline; gap: 3mm; }
.h.grp p.adv { margin: 1.2mm 0 0; color: #26354d; font-size: 8pt; line-height: 1.4; }
.h.grp p.adv b { font-weight: 650; color: #0b1830; }
.body > .h.grp:not(:first-child) { margin-top: 5mm; }
.h.grp h3 { display: flex; align-items: center; gap: 1.8mm; margin: 0; font: 650 10pt/1.2 -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif; }
.h.grp .cnt { font-variant-numeric: tabular-nums; color: #0b1830; }
.h.grp p { margin: 0; color: #4a5a75; font-size: 8pt; }
.cards .card { display: grid; grid-template-columns: 9mm 1fr; gap: 2.4mm; padding: 2.2mm 0; border-bottom: .2mm solid #e3eaf2; }
.cards .card:last-child { border-bottom: 0; }
.card .num { font: 650 11pt/1.25 -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif; color: #0b1830; font-variant-numeric: tabular-nums; text-align: right; }
.card .main p { margin: 0 0 .7mm; }
.card .main p:last-child { margin-bottom: 0; }
.card .cited { color: #0b1830; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; }
.card .found { color: #26354d; font-size: 8pt; }
.card .found .k { color: #4a5a75; font-weight: 600; margin-right: .6mm; }
.card .found .m { color: #4a5a75; }
.card .note { font-size: 8pt; font-weight: 500; }
.card .note:not(.ok):not(.warn):not(.fail) { color: #4a5a75; }
.cards.compact .card { padding: 1.3mm 0; gap: 2mm; font-size: 8pt; line-height: 1.35; }
.cards.compact .card .num { font-size: 8.6pt; font-weight: 600; color: #4a5a75; }
.cards.compact .card .cited { -webkit-line-clamp: 2; }
.cards.compact .card .main p { margin-bottom: .3mm; }
.cards.compact .card .found, .cards.compact .card .note { font-size: 7.4pt; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.card .field-diffs { display: flex; flex-wrap: wrap; gap: 1.4mm; margin-top: .8mm; font-size: 7.2pt; }
.card .pill-diff { display: inline-flex; align-items: center; padding: .1mm 1.6mm; border-radius: 2mm; font-size: 7.2pt; }
.card .pill-diff.warn { background: #fdf2dc; color: #8a5a00; border: .2mm solid #e5c378; }
.card .pill-diff.fail { background: #fbeeed; color: #b3302a; border: .2mm solid #f0a8a5; }
.card .pill-diff.ok { background: #eaf6f8; color: #08697d; border: .2mm solid #a8d5dc; }
.card .chimera-tag { margin-top: .6mm; }

/* Listen */
.list .row { display: grid; grid-template-columns: 1fr 1fr; gap: 0 6mm; }
.list .item { padding: 1.4mm 0; border-bottom: .2mm solid #eef2f7; }
.list .item .meta { color: #4a5a75; font-size: 7.6pt; margin-top: .3mm; }
.list.one .row { grid-template-columns: 1fr; }
.quote { padding: 2mm 0; border-bottom: .2mm solid #e3eaf2; }
.quote q { font: italic 9.4pt/1.42 ui-serif, "New York", "Iowan Old Style", Georgia, serif; color: #0b1830; quotes: "\\201C" "\\201D"; }
.quote .meta { color: #4a5a75; font-size: 7.6pt; margin-top: .8mm; }
.quote .meta b { font-weight: 650; }
.snotes .row { display: grid; grid-template-columns: 1fr 1fr; gap: 0 6mm; }
.snotes .sn { display: grid; grid-template-columns: 6mm 1fr; gap: 1.6mm; padding: 1mm 0; border-bottom: .2mm solid #eef2f7; font-size: 7.8pt; line-height: 1.35; }
.snotes .sn .n { color: #4a5a75; font-weight: 600; font-variant-numeric: tabular-nums; text-align: right; }

/* Alle Quellen */
.regs .reg { display: grid; grid-template-columns: 7mm 25mm 1fr 41mm; gap: 2.2mm; padding: 1.25mm 0; border-bottom: .2mm solid #eef2f7;
             align-items: baseline; font-size: 8pt; line-height: 1.34; }
.regs .split-head { color: #4a5a75; font-weight: 600; font-size: 7pt; border-bottom: .3mm solid #c3cfdd; padding-top: 0; }
.regs .reg .n { color: #4a5a75; font-variant-numeric: tabular-nums; font-weight: 600; text-align: right; }
.regs .reg .v { font-weight: 600; font-size: 7.4pt; white-space: nowrap; display: flex; align-items: center; gap: 1.2mm; }
.regs .reg .v i { width: 2mm; height: 2mm; border-radius: 50%; display: inline-block; flex: none; }
.regs .reg .ref b { font-weight: 600; }
.regs .reg .ref .m { color: #4a5a75; }
.regs .reg .doi { color: #26354d; font-size: 7pt; overflow-wrap: anywhere; }

/* Nachweis */
.proof { display: grid; grid-template-columns: 52mm 1fr; gap: 8mm; align-items: start; margin: 2mm 0 7mm; }
.proof .tile { border-radius: 4mm; padding: 2mm; box-shadow: inset 0 0 0 .25mm #c3cfdd; line-height: 0; background: #fff; }
.proof ol { margin: 0; padding-left: 4.6mm; } .proof li { margin: 0 0 2.2mm; color: #26354d; }
.proof li b { color: #0b1830; }
.btn { display: inline-flex; align-items: center; gap: 2mm; margin-top: 2mm; padding: 2.2mm 4mm; border-radius: 5mm;
       background: linear-gradient(180deg, #13284a, #0b1830); color: #fff; font-weight: 600; font-size: 8.6pt; }
.btn svg { width: 3.4mm; height: 3.4mm; }
.facts { width: 100%; border-collapse: collapse; margin: 0 0 7mm; }
.facts th { text-align: left; font-weight: 600; color: #4a5a75; width: 42mm; vertical-align: top; padding: 1.8mm 0; border-bottom: .2mm solid #eef2f7; }
.facts td { padding: 1.8mm 0; border-bottom: .2mm solid #eef2f7; overflow-wrap: anywhere; }
.fine { color: #4a5a75; font-size: 7.6pt; line-height: 1.5; max-width: 150mm; }
.fine.tipv { margin: -3mm 0 6mm; font-size: 8pt; color: #26354d; }
.fine.tipv b { color: #08697d; }

@media screen {
  body { background: #e6ebf2; }
  #book { padding: 8mm 0; }
  #book > .page { margin: 0 auto 8mm; box-shadow: 0 1px 2px rgba(11,24,48,.08), 0 12px 40px rgba(20,60,140,.14); }
}
"""

PAGINATE = r"""
(() => {
  // Verteilt die Blöcke aus #flow auf A4-Seiten. .h bleibt beim folgenden Block; .split teilt sich zeilenweise
  // (.split-head wiederholt sich auf jeder Seite); data-break="page" beginnt eine neue Seite.
  const book = document.getElementById("book");
  const tpl = document.getElementById("page-tpl");
  const flow = document.getElementById("flow");
  let body = null;
  const fits = () => body.scrollHeight <= body.clientHeight + 1;
  function newPage() {
    const prev = body;
    const page = tpl.content.firstElementChild.cloneNode(true);
    book.appendChild(page);
    body = page.querySelector(".body");
    if (prev) {
      const heads = [];
      let last = prev.lastElementChild;
      while (last && last.classList.contains("h") && last.previousElementSibling) {
        heads.unshift(last);
        last = last.previousElementSibling;
      }
      heads.forEach((h) => body.appendChild(h));
    }
  }
  function atomic(el) {
    body.appendChild(el);
    if (!fits() && body.children.length > 1) {
      body.removeChild(el);
      newPage();
      body.appendChild(el);
    }
  }
  function split(src) {
    const head = src.querySelector(":scope > .split-head");
    const rows = [...src.children].filter((c) => c !== head);
    const shell = (cont) => {
      const box = src.cloneNode(false);
      box.removeAttribute("id");
      if (cont) box.classList.add("cont");
      if (head) box.appendChild(head.cloneNode(true));
      return box;
    };
    let box = shell(false), count = 0;
    body.appendChild(box);
    for (const row of rows) {
      box.appendChild(row);
      if (fits()) { count++; continue; }
      box.removeChild(row);
      body.removeChild(box);
      if (count === 0 && body.children.length === 0) { body.appendChild(box); box.appendChild(row); count = 1; continue; }
      if (count) body.appendChild(box);
      newPage();
      box = shell(count > 0);
      body.appendChild(box);
      box.appendChild(row);
      count = 1;
    }
  }
  for (const sec of [...flow.children]) {
    if (!body || (sec.dataset.break === "page" && body.children.length)) newPage();
    for (const el of [...sec.children]) (el.classList.contains("split") ? split : atomic)(el);
  }
  flow.remove();
  const pages = [...book.children].filter((p) => p.classList.contains("page"));
  pages.forEach((p, i) => p.querySelectorAll(".pno").forEach((n) => { n.textContent = `${i + 1} / ${pages.length}`; }));
  document.querySelectorAll("[data-pageof]").forEach((el) => {
    const target = document.getElementById(el.dataset.pageof);
    const i = target ? pages.indexOf(target.closest(".page")) : -1;
    el.textContent = i >= 0 ? String(i + 1) : "";
    if (i < 0 && el.closest(".pg")) el.closest(".pg").remove();
  });
  document.documentElement.dataset.pages = String(pages.length);
})();
"""

LINK_ICON = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
             'stroke-linejoin="round"><path d="M9 3h4v4M13 3 7.5 8.5M12 9.5V13H3V4h3.5"/></svg>')
CHECK_ICON = ('<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="#08697d" stroke-width="1.8" '
              'stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6.5"/><path d="m5.2 8.2 2 2 3.8-4.2"/></svg>')


SOURCES = ("Crossref", "OpenAlex", "Europe PMC (PubMed)", "Semantic Scholar", "DataCite", "arXiv", "DBLP",
           "Open Library", "Deutsche Nationalbibliothek", "Retraction Watch")


def _shell(title: str, run_title: str, cover: str, flow: str, sealed: Optional[Dict], auto_print: bool) -> str:
    lang = current()
    seal_line = (f'<span class="mono">{_e(tr("Siegel"))} {_e(sealed["number"])}</span><span>benthic.isoglu.com/v</span>'
                 if sealed else f"<span>{_e(tr('Erstellt mit Benthic {version}', version=__version__))}</span>")
    template = (f'<template id="page-tpl"><section class="page inner">{_corner()}'
                f'<header class="run"><svg><use href="#bm"/></svg><span class="wm">Benthic</span><span class="sep"></span>'
                f'<span>{_e(run_title)}</span><span class="doc">{_e(title)}</span></header>'
                f'<div class="body"></div><footer class="run">{seal_line}<span class="pno"></span></footer></section></template>')
    printer = "addEventListener('load',()=>setTimeout(()=>print(),300));" if auto_print else ""
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            f'<title>{_e(run_title)}: {_e(title)}</title><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<style>{CSS}</style></head><body>{_sprite()}{template}'
            f'<div id="book">{cover}</div><div id="flow">{flow}</div>'
            f'<script>{PAGINATE}{printer}</script></body></html>')


def _quote_notes(q: Dict) -> List[str]:
    """Was zu einem Zitat außer dem Befund zu sagen ist: welche Datei, welche Fassung, wie weit es trägt, wo es sonst steht."""
    out = []
    if q.get("status") == "wrong_pdf" and identity.note(q):
        out.append(identity.note(q))
    if q.get("longest"):
        out.append(tr("Längste wortgleiche Folge: {n} von {total} Wörtern, S. {page}.", n=q["longest"]["words"],
                      total=q["longest"]["of"], page=q["longest"]["page"]))
    if q.get("found_in"):
        out.append(tr("Steht in {other} (S. {page}), zitiert ist {cited}.", other=q["found_in"]["label"],
                      page=q["found_in"]["page"], cited=q.get("label") or ""))
    elif q.get("searched"):
        out.append(tr("Auch nicht in der anderen geladenen Quelle." if q["searched"] == 1
                      else "Auch in keiner der {n} anderen geladenen Quellen.", n=q["searched"]))
    if q.get("copy") in identity.LABELS:
        out.append(tr("Fassung: {copy}", copy=tr(identity.LABELS[q["copy"]])))
    if q.get("status") in ("not_found", "incomplete") and q.get("identity") == identity.UNPROVABLE:
        out.append(tr("Ob das PDF das zitierte Werk ist, ließ sich nicht zeigen: Weder DOI noch Titel stehen am Anfang."))
    return out


def _cover(kind_label: str, docname: str, number: str, when: float, verdict_line: str, bar: str, legend: str,
           sealed: Optional[Dict], batch: bool = False) -> str:
    stem, ext = docname, ""
    m = re.match(r"^(.*?)(\.(?:pdf|docx?|tex|ltx|zip|txt|md|bib|ris))$", docname, re.I)
    if m:
        stem, ext = m.group(1), m.group(2)
    long = len(stem) > 70
    seal_html = ""
    if sealed:
        seal_html = (f'<div class="seal"><a class="tile" href="{_e(sealed["full_url"])}">{seal.qr_svg(sealed["qr_url"], 31)}</a>'
                     f'<div><h2>{_e(tr("Echtheit prüfen"))}</h2>'
                     f'<p>{_e(tr("Code scannen: Die Prüfseite zeigt, was dieser Bericht sagen muss, und prüft die Signatur.") if batch else tr("Code scannen: Die Prüfseite zeigt, was dieser Bericht sagen muss, und prüft die Signatur. In der PDF führt ein Klick auf den Code zur vollständigen Prüfung, die jede DOI selbst bei Crossref nachfragt."))}</p>'
                     f'<p class="id mono">{_e(sealed["number"])} <span>· {_e(tr("Schlüssel"))}</span> {_e(sealed["key_id"])}</p></div></div>')
    return (f'<section class="page cover">{_sea()}'
            f'<div class="top"><div class="brand"><svg><use href="#bm"/></svg><span>Benthic</span></div>'
            f'<div class="meta"><b>{_e(kind_label)} {_e(number)}</b><br>{_e(_date(when))}</div></div>'
            f'<div class="title"><h1>{_e(kind_label)}</h1>'
            f'<p class="docname{" long" if long else ""}">{_e(stem)}'
            + (f'<span class="ext">{_e(ext)}</span>' if ext else "") + "</p></div>"
            f'<div class="summary"><p class="verdict">{_e(verdict_line)}</p>{bar}{legend}</div>'
            f'{seal_html}<div class="foot"><span>{_e(tr("Erstellt mit Benthic {version}", version=__version__))}</span>'
            f'<span>benthic.isoglu.com</span></div></section>')


def _proof(sealed: Optional[Dict], rows: List[Tuple[str, str]], fine: str, batch: bool = False) -> str:
    parts = [_heading("sec-proof", tr("Nachweis"),
                      tr("Wie sich prüfen lässt, dass dieser Bericht echt und unverändert ist."))]
    if sealed:
        parts.append(
            f'<div class="proof">'
            f'<a class="tile" href="{_e(sealed["full_url"])}">{seal.qr_svg(sealed["qr_url"], 48)}</a><div><ol>'
            f'<li><b>{_e(tr("Code scannen."))}</b> {_e(tr("Die Prüfseite zeigt Arbeit, Datum und Zählungen, die dieser Bericht nennen muss, und ob die Signatur gültig ist. Weicht etwas vom Bericht ab, wurde er verändert."))}</li>'
            + (f'<li><b>{_e(tr("Den ganzen Datensatz prüfen."))}</b> {_e(tr("Ein Klick auf den Code oder den Knopf darunter öffnet die Prüfseite mit den Zählungen jeder Arbeit; sie rechnet die Prüfsumme nach. Die Quellen einer Arbeit belegt ihr eigener Prüfbericht."))}</li>'
               if batch else
               f'<li><b>{_e(tr("Alle Quellen nachprüfen."))}</b> {_e(tr("Ein Klick auf den Code oder den Knopf darunter öffnet die Prüfseite mit dem ganzen Datensatz; alternativ diese PDF auf die Prüfseite ziehen. Die Seite rechnet die Prüfsumme nach und fragt jede DOI selbst bei Crossref und DataCite ab, im Browser der prüfenden Person."))}</li>'
               f'<li><b>{_e(tr("Die Arbeit vergleichen."))}</b> {_e(tr("Wer die Datei der Arbeit auf die Prüfseite zieht, sieht, ob ihr Fingerabdruck zu diesem Bericht passt. Die Datei verlässt dabei den Rechner nicht."))}</li>')
            + f'</ol><a class="btn" href="{_e(sealed["full_url"])}">{LINK_ICON}'
            + _e(tr("Bericht online prüfen") if batch else tr("Alle Quellen online prüfen")) + '</a></div></div>')
        rows = [(tr("Siegelnummer"), f'<span class="mono">{_e(sealed["digest"])}</span>'),
                (tr("Schlüssel dieser Installation"), f'<span class="mono">{_e(sealed["key"][:32])}</span>')] + rows
    parts.append('<table class="facts">' + "".join(f"<tr><th>{_e(k)}</th><td>{v}</td></tr>" for k, v in rows) + "</table>")
    parts.append(f'<p class="fine">{_e(fine)}</p>')
    if sealed:
        parts.append(f'<p class="fine">{_e(tr("Das Siegel steht nur im Bericht: Die Prüfseite liest es aus der Adresse, die der Browser nie an einen Server schickt. Eine Signatur belegt, dass der Bericht seit dem Erstellen unverändert ist.") if batch else tr("Das Siegel steht nur im Bericht: Die Prüfseite liest es aus der Adresse, die der Browser nie an einen Server schickt. Eine Signatur belegt, dass der Bericht seit dem Erstellen unverändert ist; dass die Quellen stimmen, belegt erst die Nachprüfung bei Crossref."))}</p>')
    return '<section data-break="page">' + "".join(f'<div class="keep">{p}</div>' if not p.startswith('<div class="h"') else p
                                                 for p in parts) + "</section>"


def _make_seal(record: Dict, name: str, counts: List[int]) -> Optional[Dict]:
    try:
        return seal.make(record, name, counts)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# Prüfbericht
# --------------------------------------------------------------------------
def render(job: Dict, job_id: str, auto_print: bool = False) -> str:
    results = sorted(job.get("results") or [], key=lambda r: r.get("order", 0))
    doc = job.get("document") or {}
    style = job.get("style") or {}
    counts = {v: sum(1 for r in results if r.get("verdict") == v) for v in VERDICTS}
    total = len(results)
    ok, warn, bad = (sum(counts[v] for v in group) for group in (OK, WARN, BAD))
    when = job.get("finished_at") or job.get("created_at")
    title = doc.get("name") or job.get("project_name") or tr("Literaturliste vom {date}", date=_date(when, clock=False))
    record = seal.report_record(job, job_id)
    sealed = _make_seal(record, doc.get("name") or job.get("project_name") or "", [counts[v] for v in seal.REPORT_COUNTS])

    if bad:
        line = tr("{ok} von {n} Quellen bestätigt, {bad} mit Problemen.", ok=ok, n=total, bad=bad)
    elif warn:
        line = tr("{ok} von {n} Quellen bestätigt, {warn} zum Nachsehen.", ok=ok, n=total, warn=warn)
    else:
        line = tr("Alle {n} Quellen bestätigt.", n=total) if total != 1 else tr("Die Quelle ist bestätigt.")
    cover = _cover(tr("Prüfbericht"), title, f"#{job_id}", when, line, _bar(counts, total), _legend(counts, total, dark=True), sealed)

    attention = sorted((r for r in results if r.get("verdict") in ATTENTION),
                       key=lambda r: (ATTENTION.index(r.get("verdict")), r.get("order", 0)))
    missing, uncited = doc.get("missing_in_list") or [], doc.get("uncited") or []
    years = doc.get("year_mismatch") or []
    quotes = job.get("quote_check") or []
    overlaps = (job.get("overlap_check") or {}).get("findings") or []
    mw = job.get("missing_works") or {}
    noted = {k: v for k, v in (style.get("entries") or {}).items() if v}
    integ = doc.get("integrity") or {}
    n_integ = integrity.count(integ)

    # Inhalt (Abschnitte, die es gibt)
    toc = [("sec-overview", tr("Auf einen Blick"), tr("Zahlen, Quellenkarte, was zuerst zu tun ist"))]
    if attention:
        toc.append(("sec-attention", tr("Handlungsbedarf"), _sources(len(attention))))
    if doc:
        toc.append(("sec-document", tr("Abgleich mit der Arbeit"),
                    tr("{m} ohne Eintrag, {u} nie zitiert", m=len(missing), u=len(uncited))
                    + (", " + tr("{n} mit anderem Jahr", n=len(years)) if years else "")))
    if n_integ:
        toc.append(("sec-integrity", tr("Hinweise auf Manipulation"), tr("1 Stelle") if n_integ == 1 else tr("{n} Stellen", n=n_integ)))
    if quotes:
        toc.append(("sec-quotes", tr("Wörtliche Zitate"), tr("{n} geprüft", n=len(quotes))))
    if job.get("overlap_check"):
        toc.append(("sec-overlap", tr("Übernahmen ohne Anführungszeichen"), (tr("1 Stelle") if len(overlaps) == 1 else tr("{n} Stellen", n=len(overlaps))) if overlaps else tr("keine")))
    if style.get("applicable"):
        toc.append(("sec-style", tr("Zitierstil"), tr("{n} Einträge mit Hinweisen", n=len(noted))))
    elif style.get("list"):
        toc.append(("sec-style", tr("Hinweise zur Liste"),
                    tr("1 Widerspruch") if len(style["list"]) == 1 else tr("{n} Widersprüche", n=len(style["list"]))))
    if mw.get("suggestions"):
        toc.append(("sec-missing", tr("Oft zitiert von deinen Quellen"), tr("{n} Vorschläge", n=len(mw["suggestions"]))))
    toc.append(("sec-all", tr("Alle Quellen"), _sources(total)))
    toc.append(("sec-proof", tr("Nachweis"), tr("Siegel, QR-Code und Prüfung")))

    flow = [_overview(results, counts, total, ok, warn, bad, doc, quotes, style, noted, attention, missing),
            _contents(toc)]

    sec = []
    if attention:
        sec.append(_heading("sec-attention", tr("Handlungsbedarf"),
                            tr("{n}, die nach Dringlichkeit geordnet einen Blick brauchen.", n=_sources(len(attention)))))
        sec.extend(_attention(attention))
    if doc:
        sec.append(_heading("sec-document", tr("Abgleich mit der Arbeit"),
                            tr("{e} Einträge im Verzeichnis, {c} Zitate im Text.", e=doc.get("entries", 0), c=doc.get("citations", 0))))
        if not doc.get("cross_check"):
            sec.append(f'<div class="keep"><p class="muted">{_e(tr("Die Zitate im Text ließen sich nicht sicher lesen, zum Beispiel hochgestellte Zahlen. Benthic prüft deshalb nur das Verzeichnis."))}</p></div>')
        if years:
            sec.append(_sub(tr("Jahr im Text und im Verzeichnis verschieden")))
            sec.append('<div class="split list one">' + "".join(
                f'<div class="row"><div class="item"><b class="warn">{_e(tr("Im Text {cited}, im Verzeichnis {listed}", cited=y["label"], listed=y["list_year"]))}</b>'
                f'<div class="meta">{_e(y.get("list_label", ""))}</div></div></div>' for y in years[:200]) + "</div>")
        if missing:
            sec.append(_sub(tr("Im Text zitiert, im Verzeichnis nicht gefunden")))
            sec.append('<div class="split list one">' + "".join(
                f'<div class="row"><div class="item"><b class="fail">{_e(m["label"])}</b>'
                + (f' <span class="muted">({int(m["count"])}×)</span>' if m.get("count", 1) > 1 else "")
                + (f'<div class="meta">{_e(m.get("sentence", ""))}</div>' if m.get("sentence") else "") + "</div></div>"
                for m in missing[:200]) + "</div>")
        if uncited:
            sec.append(_sub(tr("Im Verzeichnis, im Text nie zitiert")))
            pairs = [uncited[i:i + 2] for i in range(0, len(uncited[:200]), 2)]
            sec.append('<div class="split list">' + "".join(
                '<div class="row">' + "".join(f'<div class="item">{_e(u["label"])}</div>' for u in pair) + "</div>"
                for pair in pairs) + "</div>")
        sec_cites = doc.get("secondary_citations") or []
        if sec_cites:
            sec.append(_sub(tr("Zitate aus zweiter Hand (zit. nach / cited in)")))
            sec.append('<div class="split list one">' + "".join(
                f'<div class="row"><div class="item"><b class="warn">{_e(s["match"])}</b>'
                f'<div class="meta">{_e(s.get("sentence", ""))}</div></div></div>' for s in sec_cites[:20]) + "</div>")
        unclaims = doc.get("uncited_claims") or []
        if unclaims:
            sec.append(_sub(tr("Aussagen mit Zahlen oder Studienbezug ohne Zitat")))
            sec.append('<div class="split list one">' + "".join(
                f'<div class="row"><div class="item"><b class="warn">{_e(u["sentence"])}</b></div></div>' for u in unclaims[:20]) + "</div>")
        if doc.get("cross_check") and not missing and not uncited and not years and not sec_cites and not unclaims:
            sec.append(f'<div class="keep"><p class="allgood">{CHECK_ICON}{_e(tr("Jedes Zitat steht im Verzeichnis, und jeder Eintrag wird zitiert."))}</p></div>')
    if n_integ:
        sec.append(_heading("sec-integrity", tr("Hinweise auf Manipulation"),
                            tr("Jeder Fund zeigt die Stelle. Prüfe ihn in der Datei; ein einzelner kann Zufall sein.")))
        hidden = {"hidden": tr("In Word ausgeblendeter Text"), "white": tr("Weiße Schrift ohne Hintergrund"),
                  "tiny": tr("Winzige Schrift (unter 2 pt)")}
        times = lambda f: f' <span class="muted">({int(f["count"])}×)</span>' if f.get("count", 1) > 1 else ""
        rows = [(hidden.get(f["kind"], ""), f["text"], "", "") for f in integ.get("hidden") or []]
        rows += [(tr("Unsichtbares Zeichen im Wort"), f["word"], times(f), f["context"]) for f in integ.get("invisible") or []]
        rows += [(tr("Buchstabe aus einer anderen Schrift"), f["word"], times(f), f["context"]) for f in integ.get("lookalike") or []]
        rows += [(tr("Ungewöhnliche Wendung, üblich ist „{term}“", term=f["expected"]), f["phrase"], times(f), f["context"])
                 for f in integ.get("tortured") or []]
        sec.append('<div class="split list one">' + "".join(
            f'<div class="row"><div class="item"><b class="warn">{_e(label)}:</b> {_e(what)}{n}'
            + (f'<div class="meta">{_e(ctx)}</div>' if ctx else "") + "</div></div>" for label, what, n, ctx in rows) + "</div>")
    if quotes:
        sec.append(_heading("sec-quotes", tr("Wörtliche Zitate"), tr("{n} geprüft", n=len(quotes))))
        rows = []
        for q in quotes:
            status = q.get("status")
            page = ""
            if q.get("page_ok") is False:
                page = " · " + tr("Angegeben ist S. {stated}, in der Quelle steht es auf S. {found}.",
                                  stated=q.get("stated_page"), found=q.get("printed_page") or q.get("page"))
            rows.append(f'<div class="row quote"><q>{_e(q.get("quote"))}</q><div class="meta"><b class="{QUOTE_COLOR.get(status, "")}">'
                        f'{_e(tr(QUOTE_LABEL.get(status, "Nicht prüfbar")))}</b> · {_e(q.get("label"))}{_e(page)}</div>'
                        + (f'<div class="meta">{_e(tr("In der Quelle:"))} {_e(q.get("excerpt"))}</div>' if status == "close" and q.get("excerpt") else "")
                        + "".join(f'<div class="meta">{_e(line)}</div>' for line in _quote_notes(q))
                        + "</div>")
        sec.append('<div class="split quotes">' + "".join(rows) + "</div>")
    if job.get("overlap_check"):
        oc = job["overlap_check"]
        sec.append(_heading("sec-overlap", tr("Übernahmen ohne Anführungszeichen"),
                            tr("{n} zitierende Sätze mit den Volltexten ihrer Quellen verglichen, ohne die Teile in Anführungszeichen.", n=oc.get("checked", 0))))
        kinds = {"verbatim": tr("Wortgleich aus der Quelle"), "close": tr("Fast wortgleich aus der Quelle"),
                 "partial": tr("Lange wortgleiche Passage")}
        if overlaps:
            sec.append('<div class="split quotes">' + "".join(
                f'<div class="row quote"><div class="meta"><b class="{"warn" if f["kind"] == "partial" else "fail"}">{_e(kinds[f["kind"]])}</b>'
                f' · {_e(f.get("label"))}' + (f' · {_e(tr("S."))} {_e(f["page"])}' if f.get("page") else "") + "</div>"
                f'<div>{_e(f["sentence"])}</div>'
                + (f'<div class="meta">{_e(tr("In der Quelle:"))} <q>{_e(f["excerpt"])}</q></div>' if f.get("excerpt") else "")
                + "</div>" for f in overlaps) + "</div>")
        else:
            sec.append(f'<div class="keep"><p class="allgood">{CHECK_ICON}{_e(tr("Kein Satz übernimmt Wortlaut seiner Quelle ohne Anführungszeichen."))}</p></div>')
        if oc.get("missing_pdfs"):
            sec.append(f'<div class="keep"><p class="muted">{_e(tr("Nicht verglichen (PDF fehlt): {n}", n=len(oc["missing_pdfs"])))}</p></div>')
        if oc.get("wrong_pdfs"):
            sec.append(f'<div class="keep"><p class="muted">{_e(tr("Nicht verglichen (PDF ist ein anderes Werk): {n}", n=len(oc["wrong_pdfs"])))}</p></div>')
    if style.get("applicable"):
        name = (style.get("styles") or {}).get(style.get("style"), style.get("style"))
        sec.append(_heading("sec-style", tr("Zitierstil"),
                            tr("Erkannt: {style}.", style=name) + " " + tr("{n} Einträge mit Hinweisen.", n=len(noted))))
        if style.get("list"):
            sec.append('<div class="keep block"><ul>' + "".join(f"<li>{_e(i['text'])}</li>" for i in style["list"]) + "</ul></div>")
        if noted:
            cells = [f'<div class="sn"><span class="n">{int(k) + 1}</span><span>{_e(" ".join(i["text"] for i in v))}</span></div>'
                     for k, v in sorted(noted.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0)]
            sec.append('<div class="split snotes">' + "".join(f'<div class="row">{"".join(cells[i:i + 2])}</div>'
                                                              for i in range(0, len(cells), 2)) + "</div>")
    elif style.get("list"):  # BibTeX oder RIS: kein Zitierstil, aber Widersprüche in der Liste
        sec.append(_heading("sec-style", tr("Hinweise zur Liste")))
        sec.append('<div class="keep block"><ul>' + "".join(f"<li>{_e(i['text'])}</li>" for i in style["list"]) + "</ul></div>")
    if mw.get("suggestions"):
        sec.append(_heading("sec-missing", tr("Oft zitiert von deinen Quellen"),
                            tr("Werke, die mehrere deiner bestätigten Quellen zitieren, die aber nicht im Verzeichnis stehen.")))
        sec.append('<div class="split list one">' + "".join(
            f'<div class="row"><div class="item"><b>{_e(w.get("title"))}</b> <span class="muted">({_e(w.get("year"))})</span>'
            f'<div class="meta">{_e(tr("Von {n} deiner {of} Quellen zitiert", n=w["count"], of=mw.get("sources")))}'
            + (f' · <span class="mono">{_e(w.get("doi"))}</span>' if w.get("doi") else "") + "</div></div></div>"
            for w in mw["suggestions"]) + "</div>")
    sec.append(_heading("sec-all", tr("Alle Quellen"), tr("{n} in der Reihenfolge des Verzeichnisses.", n=_sources(total))))
    sec.append(f'<div class="split regs"><div class="split-head reg"><span class="n">#</span><span>{_e(tr("Urteil"))}</span>'
               f'<span>{_e(tr("Quelle"))}</span><span>DOI</span></div>' + "".join(_register_row(r) for r in results) + "</div>")
    flow.append('<section data-break="page">' + "".join(sec) + "</section>")

    facts = [(tr("Bericht"), _e(f"#{job_id} · {_date(when)}")), (tr("Erstellt mit"), _e(f"Benthic {__version__}")),
             (tr("Geprüft gegen"), _e(", ".join(SOURCES)))]
    if doc.get("name"):
        facts.insert(1, (tr("Arbeit"), _e(doc["name"]) + (f'<br><span class="mono">SHA-256 {_e(doc["sha256"])}</span>' if doc.get("sha256") else "")))
    flow.append(_proof(sealed, facts, tr("Urteile sind begründete Hinweise aus den Verzeichnissen unter „Geprüft gegen“, keine Gutachten. Bei „Weicht ab“ und „Unsicher“ lohnt der Blick ins Original.")))
    return _shell(title, tr("Prüfbericht"), cover, "".join(flow), sealed, auto_print)


def _overview(results, counts, total, ok, warn, bad, doc, quotes, style, noted, attention, missing) -> str:
    kpi = lambda cls, big, lbl, sm: (f'<div class="kpi {cls}"><div class="big">{big}</div><div class="lbl">{_e(lbl)}</div>'
                                     f'<div class="sm">{_e(sm)}</div></div>')
    kpis = ('<div class="kpis">'
            + kpi("lead", total, tr("Quellen geprüft"), tr("gegen Crossref, OpenAlex und weitere"))
            + kpi("ok" if ok else "zero", ok, tr("Bestätigt"), _pct(ok, total) + " · " + tr("DOI korrigiert eingeschlossen"))
            + kpi("warn" if warn else "zero", warn, tr("Zum Nachsehen"), tr("Weicht ab oder unsicher"))
            + kpi("fail" if bad else "zero", bad, tr("Mit Problemen"), tr("Passt nicht, nicht auffindbar, zurückgezogen"))
            + "</div>")
    verdicts = (f'<div class="panel"><h3>{_e(tr("Urteile"))}</h3>{_bar(counts, total)}{_legend(counts, total)}</div>')
    srcmap = (f'<div class="panel"><h3>{_e(tr("Quellenkarte"))}</h3><p class="hint">'
              f'{_e(tr("Jedes Feld eine Quelle, in der Reihenfolge des Verzeichnisses. Volle Farbe heißt: ansehen."))}</p>'
              f'{_source_map(results)}</div>')

    def stat(label, value, cls=""):
        return f'<div class="stat"><span>{_e(label)}</span><b class="{cls}">{_e(value)}</b></div>'

    panels = []
    if doc:
        panels.append(f'<div class="panel"><h3>{_e(tr("Abgleich mit der Arbeit"))}</h3>'
                      + stat(tr("Einträge im Verzeichnis"), doc.get("entries", 0))
                      + stat(tr("Zitate im Text"), doc.get("citations", 0))
                      + stat(tr("Zitate ohne Eintrag"), len(missing), "fail" if missing else "ok")
                      + stat(tr("Einträge ohne Zitat"), len(doc.get("uncited") or []), "warn" if doc.get("uncited") else "ok")
                      + (stat(tr("Jahr verschieden"), len(doc["year_mismatch"]), "warn") if doc.get("year_mismatch") else "")
                      + "</div>")
    if quotes:
        qc = {s: sum(1 for q in quotes if q.get("status") == s) for s in QUOTE_LABEL}
        panels.append(f'<div class="panel"><h3>{_e(tr("Wörtliche Zitate"))}</h3>'
                      + stat(tr("Geprüft"), len(quotes))
                      + "".join(stat(tr(QUOTE_LABEL[s]), n, QUOTE_COLOR.get(s, "") if n else "")
                                for s, n in qc.items() if n or s in ("exact", "close", "not_found")) + "</div>")
    if style.get("applicable"):
        name = (style.get("styles") or {}).get(style.get("style"), style.get("style"))
        panels.append(f'<div class="panel"><h3>{_e(tr("Zitierstil"))}</h3>'
                      + stat(tr("Erkannt"), name or "")
                      + stat(tr("Einträge mit Hinweisen"), len(noted), "warn" if noted else "ok")
                      + stat(tr("Hinweise zur Liste"), len(style.get("list") or []), "warn" if style.get("list") else "ok") + "</div>")
    with_doi = sum(1 for r in results if r.get("doi"))
    panels.append(f'<div class="panel"><h3>{_e(tr("Kennungen"))}</h3>'
                  + stat(tr("Mit DOI"), with_doi) + stat(tr("Ohne DOI"), total - with_doi, "warn" if total - with_doi else "")
                  + stat(tr("DOI korrigiert"), counts.get("corrected", 0), "ok" if counts.get("corrected") else "") + "</div>")
    years = sorted(int(y) for y in ((r.get("found") or {}).get("year") or (r.get("claimed") or {}).get("year") for r in results)
                   if str(y or "").isdigit() and 1500 < int(y) < 2200)
    if years:
        recent = sum(1 for y in years if y >= time.localtime().tm_year - 5)
        panels.append(f'<div class="panel"><h3>{_e(tr("Jahrgänge"))}</h3>'
                      + stat(tr("Älteste Quelle"), years[0]) + stat(tr("Neueste Quelle"), years[-1])
                      + stat(tr("Median"), years[len(years) // 2]) + stat(tr("Aus den letzten fünf Jahren"), recent) + "</div>")
    cited = [(int((r.get("found") or {}).get("cited_by") or 0), int(r.get("order", 0)) + 1) for r in results
             if isinstance((r.get("found") or {}).get("cited_by"), int)]
    if cited:
        top = max(cited)
        counts_only = sorted(c for c, _ in cited)
        panels.append(f'<div class="panel"><h3>{_e(tr("Wirkung"))}</h3>'
                      + stat(tr("Frei zugänglich"), sum(1 for r in results if (r.get("found") or {}).get("is_oa")))
                      + stat(tr("Am häufigsten zitiert"), f"#{top[1]} · {_num(top[0])}×")
                      + stat(tr("Zitationen im Median"), _num(counts_only[len(counts_only) // 2])) + "</div>")
    panels = panels[:3]
    trio = f'<div class="trio" style="grid-template-columns:repeat({len(panels)},1fr)">{"".join(panels)}</div>' if panels else ""

    todo = []
    for r in attention:
        if r.get("verdict") in ("retracted", "not_found", "mismatch"):
            todo.append(r)
    lines = []
    for r in todo[:7]:
        v, num = r.get("verdict"), int(r.get("order", 0)) + 1
        lines.append(f'<li><i style="background:{FILL[v]}"></i><span class="n">{num}</span>'
                     f'<span class="t">{_e(_title_of(r)[:160])}<small>{_e(tr(VERDICT_LABEL[v]))}</small></span>'
                     f'<a class="pg" href="#src-{num}">{_e(tr("S."))} <span data-pageof="src-{num}"></span></a></li>')
    for m in missing[: max(0, 7 - len(lines))]:
        lines.append(f'<li><i style="background:#c23b2c"></i><span class="n">·</span>'
                     f'<span class="t">{_e(m["label"])}<small>{_e(tr("Im Text zitiert, im Verzeichnis nicht gefunden"))}</small></span>'
                     f'<a class="pg" href="#sec-document">{_e(tr("S."))} <span data-pageof="sec-document"></span></a></li>')
    for r in [r for r in attention if r not in todo][: max(0, 7 - len(lines))]:
        v, num = r.get("verdict"), int(r.get("order", 0)) + 1
        lines.append(f'<li><i style="background:{FILL[v]}"></i><span class="n">{num}</span>'
                     f'<span class="t">{_e(_title_of(r)[:160])}<small>{_e(tr(VERDICT_LABEL[v]))}</small></span>'
                     f'<a class="pg" href="#src-{num}">{_e(tr("S."))} <span data-pageof="src-{num}"></span></a></li>')
    rest = len(attention) + len(missing) - len(lines)
    if lines:
        first = (f'<div class="panel"><h3>{_e(tr("Zuerst ansehen"))}</h3><ul class="todo">{"".join(lines)}</ul>'
                 + (f'<p class="hint" style="margin:2mm 0 0">{_e(tr("Dazu {n} weitere im Abschnitt Handlungsbedarf.", n=rest))}</p>' if rest > 0 else "")
                 + "</div>")
    else:
        first = (f'<div class="panel"><p class="allgood">{CHECK_ICON}'
                 f'{_e(tr("Nichts zu tun: Jede Quelle ist bestätigt."))}</p></div>')
    return ('<section data-break="page">' + _heading("sec-overview", tr("Auf einen Blick"))
            + f'<div class="keep dash">{kpis}{verdicts}{srcmap}{trio}{first}</div></section>')


def _contents(toc: List[Tuple[str, str, str]]) -> str:
    items = "".join(f'<li><a href="#{a}"><span class="t">{_e(t)}</span><span class="c">{_e(c)}</span>'
                    f'<span class="dots"></span><span class="p" data-pageof="{a}"></span></a></li>' for a, t, c in toc)
    legend = "".join(f'<div><span><span class="pill {COLOR[v]}{" solid" if v == "retracted" else ""}">{_e(tr(VERDICT_LABEL[v]))}</span></span>'
                     f'<p>{_e(tr(EXPLAIN[v]))}</p></div>' for v in VERDICTS)
    method = (f'<div class="cols"><div class="block"><h3>{_e(tr("Wie Benthic prüft"))}</h3>'
              f'<p>{_e(tr("Jede Angabe wird über ihre DOI oder ihren Titel gesucht, bei OpenAlex, Crossref, arXiv, Semantic Scholar und Open Library, und mit dem gefundenen Werk verglichen: Titel, Autor:innen, Jahr. Rückzüge melden OpenAlex und Crossref, dessen Daten die Retraction-Watch-Datenbank einschließen."))}</p></div>'
              f'<div class="block"><h3>{_e(tr("Wie sich der Bericht prüfen lässt"))}</h3>'
              f'<p>{_e(tr("Der QR-Code auf dem Deckblatt führt zur Prüfseite. Sie zeigt, was dieser Bericht sagen muss, und prüft die Signatur; aus der PDF heraus fragt sie zusätzlich jede Quelle selbst nach. Mehr dazu im Abschnitt Nachweis."))}</p></div></div>')
    return ('<section data-break="page">' + _heading("sec-contents", tr("Inhalt"))
            + f'<div class="keep"><ol class="toc">{items}</ol></div>'
            + f'<div class="h sub"><h3>{_e(tr("Die Urteile"))}</h3></div><div class="keep verdicts">{legend}</div>'
            + f'<div class="keep"><p class="fine tipv"><b>{_e(tr(VERDICT_LABEL["verified"]))}:</b> {_e(tr(VERDICT_ADVICE["verified"]))}</p></div>'
            + f'<div class="keep">{method}</div></section>')


# --------------------------------------------------------------------------
# Stapelprüfung
# --------------------------------------------------------------------------
def render_batch(b: Dict, auto_print: bool = False) -> str:
    """Sammelbericht einer Stapelprüfung: Deckblatt, Übersicht je Arbeit, die Probleme im Einzelnen, Nachweis."""
    t = b.get("totals") or {}
    when = b.get("finished_at") or b.get("created_at")
    name = str(b.get("name") or b.get("folder") or "")
    record = seal.batch_record(b)
    sealed = _make_seal(record, name, seal.batch_counts(record))
    rows = b.get("rows") or []
    checked, flagged, errors = t.get("checked", 0), t.get("flagged_files", 0), t.get("errors", 0)
    clean = max(0, checked - flagged)
    line = (tr("{n} Arbeiten geprüft, {f} mit Problemen.", n=checked, f=flagged) if flagged
            else tr("{n} Arbeiten geprüft, keine mit Problemen.", n=checked))
    bar = ('<div class="bar">' + (f'<i style="flex:{clean} 1 0;background:{FILL["verified"]}"></i>' if clean else "")
           + (f'<i style="flex:{flagged} 1 0;background:{FILL["not_found"]}"></i>' if flagged else "")
           + (f'<i style="flex:{errors} 1 0;background:{FILL["uncertain"]}"></i>' if errors else "") + "</div>") if checked or errors else ""
    legend = ('<ul class="legend dark">'
              + f'<li><i style="background:{FILL["verified"]}"></i><b>{clean}</b> {_e(tr("ohne Probleme"))}</li>'
              + f'<li><i style="background:{FILL["not_found"]}"></i><b>{flagged}</b> {_e(tr("mit Problemen"))}</li>'
              + (f'<li><i style="background:{FILL["uncertain"]}"></i><b>{errors}</b> {_e(tr("nicht lesbar"))}</li>' if errors else "")
              + "</ul>")
    cover = _cover(tr("Stapelprüfung"), name, f"#{b.get('id', '')}", when, line, bar, legend, sealed, batch=True)

    cols = [("entries", "Einträge"), ("missing", "Zitate ohne Eintrag"), ("not_found", "Nicht auffindbar"),
            ("mismatch", "Passt nicht"), ("retracted", "Zurückgezogen"), ("quotes_bad", "Zitate auffällig"), ("style", "Stilhinweise")]
    bad_cols = ("missing", "not_found", "mismatch", "retracted", "quotes_bad")
    kpi = lambda cls, big, lbl: f'<div class="kpi {cls}"><div class="big">{big}</div><div class="lbl">{_e(lbl)}</div></div>'
    kpis = ('<div class="kpis">' + kpi("lead", checked, tr("Arbeiten geprüft"))
            + kpi("ok" if clean else "zero", clean, tr("ohne Probleme"))
            + kpi("fail" if flagged else "zero", flagged, tr("mit Problemen"))
            + kpi("fail" if t.get("retracted_files") else "zero", t.get("retracted_files", 0), tr("mit zurückgezogenen Quellen")) + "</div>")
    head = "".join(f"<span>{_e(tr(label))}</span>" for _, label in cols)
    table = []
    for r in rows:
        if r.get("state") == "error":
            msg = tr("Kein Literaturverzeichnis erkannt") if r.get("error") == "no-bibliography" else tr("Nicht lesbar")
            table.append(f'<div class="row brow"><span class="f">{_e(r.get("name"))}</span><span class="warn wide">{_e(msg)}</span></div>')
            continue
        cells = "".join(f'<span class="{"fail" if k in bad_cols and r.get(k) else ("zero" if not r.get(k) else "")}">{_e(r.get(k, 0))}</span>'
                        for k, _ in cols)
        table.append(f'<div class="row brow"><span class="f">{_e(r.get("name"))}</span>{cells}</div>')
    cs = b.get("class_summary") or {}
    course = ""
    if cs.get("files"):
        labels = [("not_found", "mit erfundenen Quellen"), ("mismatch", "mit falsch zugeordneten DOIs"),
                  ("retracted", "mit zurückgezogenen Quellen"), ("missing", "mit Zitaten ohne Eintrag"),
                  ("quotes_bad", "mit auffälligen wörtlichen Zitaten"), ("style", "mit Stilhinweisen")]
        course = (_sub(tr("Für den Kurs")) + '<div class="keep"><div class="panel"><div class="trio" style="grid-template-columns:repeat(3,1fr);margin:0">'
                  + "".join(f'<div class="stat"><span>{_e(tr(label))}</span><b class="{"warn" if (cs.get("with") or {}).get(k) else "zero"}">'
                            f'{(cs.get("with") or {}).get(k, 0)} {_e(tr("von {total}", total=cs["files"]))}</b></div>' for k, label in labels)
                  + f'</div><p class="hint" style="margin:2mm 0 0">{_e(tr("Ohne Namen, für die Lehre: Was viele betrifft, lohnt eine Erklärung im Kurs."))}</p></div></div>')
    shared = b.get("shared_phantoms") or []
    if shared:
        course += (_sub(tr("Dieselbe erfundene Quelle in mehreren Abgaben"))
                   + f'<div class="keep"><p class="muted">{_e(tr("Ein Hinweis auf dieselbe Chat-Antwort oder abgeschriebene Listen, kein Beweis. Nachprüfbar ist nur: Diese Quelle gibt es so nicht."))}</p></div>'
                   + '<div class="split list one">' + "".join(
                       f'<div class="row"><div class="item"><b>{_e(g["title"])}</b><div class="meta">'
                       f'{_e(tr("In {n} Abgaben: {files}", n=len(g["files"]), files=", ".join(g["files"])))}</div></div></div>'
                       for g in shared) + "</div>")
    conflicts = b.get("doi_conflicts") or []
    if conflicts:
        course += (_sub(tr("Widersprüche zwischen den Abgaben"))
                   + f'<div class="keep"><p class="muted">{_e(tr("Eine DOI ist ein Werk: Wo sie mit anderem Jahr, Band oder anderen Seiten steht, stimmt höchstens eine der Angaben."))}</p>'
                   + '<ul>' + "".join(f"<li>{_e(c['text'])}</li>" for c in conflicts) + "</ul></div>")
    overview = ('<section data-break="page">' + _heading("sec-overview", tr("Auf einen Blick"), str(b.get("folder") or ""))
                + f'<div class="keep">{kpis}</div>' + course
                + _sub(tr("Übersicht je Arbeit"))
                + f'<div class="split btable"><div class="split-head brow"><span class="f">{_e(tr("Datei"))}</span>{head}</div>{"".join(table)}</div></section>')
    labels = {"missing": "Zitate ohne Eintrag", "not_found": "Nicht auffindbar", "mismatch": "Passt nicht",
              "retracted": "Zurückgezogen", "quotes": "Auffällige wörtliche Zitate"}
    sec, first = [], True
    for i, r in enumerate(rows):
        problems = {k: v for k, v in (r.get("problems") or {}).items() if v}
        if not problems:
            continue
        sec.append(_heading(f"file-{i}" if not first else "sec-files", str(r.get("name") or ""),
                            tr("{n} Einträge", n=r.get("entries", 0))))
        first = False
        for k, items in problems.items():
            sec.append(_sub(tr(labels.get(k, k))))
            sec.append('<div class="split list one">' + "".join(f'<div class="row"><div class="item">{_e(x)}</div></div>' for x in items) + "</div>")
    flow = [overview]
    if sec:
        flow.append('<section data-break="page">' + "".join(sec) + "</section>")
    facts = [(tr("Stapel"), _e(f"#{b.get('id', '')} · {_date(when)}")), (tr("Ordner"), _e(b.get("folder") or "")),
             (tr("Erstellt mit"), _e(f"Benthic {__version__}"))]
    flow.append(_proof(sealed, facts, tr("Die Arbeiten blieben auf dem Mac; geprüft wurden nur die Literaturangaben."), batch=True))
    css = ("<style>.btable .brow{display:grid;grid-template-columns:1fr repeat(7,15mm);gap:1.6mm;padding:1.3mm 0;"
           "border-bottom:.2mm solid #eef2f7;font-size:7.8pt;align-items:baseline}.btable .split-head{color:#4a5a75;font-weight:600;"
           "font-size:6.8pt;border-bottom:.3mm solid #c3cfdd;line-height:1.2}.btable .brow>span:not(.f){text-align:right;"
           "font-variant-numeric:tabular-nums}.btable .brow .f{font-weight:600;overflow-wrap:anywhere}.btable .brow .wide{grid-column:2/-1;text-align:left!important}"
           ".btable .zero{color:#b4bfcd}</style>")
    out = _shell(name, tr("Stapelprüfung"), cover, "".join(flow), sealed, auto_print)
    return out.replace("</head>", css + "</head>", 1)
