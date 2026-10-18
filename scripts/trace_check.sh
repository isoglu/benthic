#!/usr/bin/env bash
# Prüft, dass nichts Privates oder Fremdes im öffentlichen Baum oder in einem
# gebauten Bundle landet: verbotene Begriffe, persönliche Pfade und Adressen,
# auch in Bild-Metadaten (PNG-Textblöcke, EXIF, Spotlight, erweiterte Attribute)
# und in den Quellpfaden vorkompilierter .pyc-Dateien. Zip-Archive (.mcpb, .zip
# oder Zip-Signatur) werden ausgepackt geprüft, jedes Mitglied mit denselben
# Regeln; Fundstelle dann archiv!mitglied:zeile.
#
# Aufruf:  scripts/trace_check.sh [PFAD ...]
#   ohne Pfad: das ganze Repository (ohne .git, Builds, Caches, venv)
#   Benthic.app: nur Benthics eigene Teile; Laufzeit und Bibliotheken nur auf
#              persönliche Pfade (Begriffe kommen in Fremdcode legitim vor)
#   Benthic.mcpb: das Paket für Claude Desktop, Mitglied für Mitglied
# Exit 0 = sauber, 1 = Treffer (mit Fundstelle), 2 = Aufruffehler.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ "$#" -eq 0 ] && set -- "$ROOT"

exec python3 -I - "$ROOT" "$@" <<'PY'
import io
import os
import re
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

root = Path(sys.argv[1])
targets = [Path(a).resolve() for a in sys.argv[2:]]

# Verbotene Begriffe (ohne Groß-/Kleinschreibung). Der Code dieses Skripts
# setzt sie aus Teilen zusammen, damit es sich nicht selbst meldet.
J = "".join
TERMS = [
    J(["sci", "-hub"]), J(["sci", "hub"]), J(["sci", "_hub"]), J(["sci", " hub"]),
    J(["lib", "gen"]), J(["library ", "genesis"]), J(["z-lib", "rary"]), J(["zlib", "rary"]),
    J(["anna", "s-archive"]), J(["anna's ", "archive"]),
    J(["mir", "ror"]), J(["escal", "at"]), J(["eskal", "ation"]), J(["eskal", "ier"]),
    J(["shadow ", "librar"]), J(["schatten", "bibliothek"]), J(["pira", "t"]),
    J(["wb", "leb"]), J(["legal", "-ausgabe"]), J(["legal ", "edition"]), J(["legal", "_only"]),
    J(["benthic", ".private"]), J(["benthic ", "private"]), J(["pearl", "dive.private"]), J(["pearl", "dive private"]),
    J(["scu", "ba.private"]), J(["scu", "ba private"]),
    J(["master", "-library"]), J(["google ", "drive"]),
]
PATHS = [J(["/Users/", "isoglu"]), J(["/Volumes/", "SINAN"]), J(["SINAN", "DISK"]), J(["isoglu", "sinan"]),
         J(["/private/", "tmp/claude"]), J(["claude", "-501"])]
TERM_RE = re.compile("|".join(re.escape(t) for t in TERMS + PATHS), re.I)
PATH_RE = re.compile("|".join(re.escape(t) for t in PATHS), re.I)

SKIP_DIRS = {".git", ".venv", "venv", "dist", "build", ".swiftcache", ".swifttmp", ".uv-cache",
             "__pycache__", "downloads", "node_modules", ".pytest_cache"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".icns", ".pdf", ".heic"}
ZIP_EXT = {".mcpb", ".zip"}
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")  # lokaler Kopf, leeres Archiv
ZIP_DEPTH = 4  # Zip im Zip im Zip …; tiefer wird nicht geprüft, sondern gemeldet
findings = []


def report(where, what, text):
    m = TERM_RE.search(text) if what != "path" else PATH_RE.search(text)
    if not m:
        return
    start = max(0, m.start() - 40)
    snippet = text[start:m.end() + 40].replace("\n", " ")
    findings.append(f"{where}: {m.group(0)!r} in …{snippet}…")


def scan_file(path, rel, mode="all"):
    """Eine Datei auf der Platte; bei Bildern kommen Spotlight und erweiterte Attribute hinzu."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        findings.append(f"{rel}: nicht lesbar ({exc})")
        return
    meta = []
    if mode == "all" and path.suffix.lower() in IMAGE_EXT:
        for cmd in (["xattr", "-l", str(path)], ["mdls", str(path)]):
            try:
                meta.append(subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout)
            except (OSError, subprocess.SubprocessError):
                pass
    scan_data(data, rel, path.name, mode, meta)


def scan_data(data, rel, name, mode="all", meta=(), depth=0):
    """Inhalt nach Art: Zip-Archiv, Bild oder Text. Zip-Mitglieder laufen ebenso hier durch."""
    suffix = Path(name).suffix.lower()
    if suffix in ZIP_EXT or data[:4] in ZIP_MAGIC:
        scan_zip(data, rel, mode, depth)
    elif mode == "all" and suffix in IMAGE_EXT:
        scan_image(data, rel, meta)
    else:
        scan_text(data, rel, mode)


def scan_zip(data, rel, mode="all", depth=0):
    """Jedes Mitglied mit denselben Regeln, Fundstelle archiv!mitglied; Unlesbares ist ein Treffer."""
    if depth >= ZIP_DEPTH:
        findings.append(f"{rel}: Zip zu tief verschachtelt, nicht geprüft")
        return
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
        findings.append(f"{rel}: kein lesbares Zip-Archiv ({exc})")
        return
    with archive:
        report(f"{rel} (Zip-Kommentar)", mode, archive.comment.decode("utf-8", "replace"))
        for info in archive.infolist():
            where = f"{rel}!{info.filename}"
            # Zip speichert Pfade ohne führenden Schrägstrich; ergänzt, damit /Users/… greift.
            report(f"{where} (Dateiname)", mode, "/" + info.filename)
            report(f"{where} (Zip-Kommentar)", mode, info.comment.decode("utf-8", "replace"))
            if info.is_dir():
                continue
            try:
                member = archive.read(info)
            except (RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error, EOFError, OSError) as exc:
                findings.append(f"{where}: nicht lesbar ({exc})")  # z. B. verschlüsselt
                continue
            scan_data(member, where, info.filename, mode, depth=depth + 1)


def scan_text(data, rel, mode="all"):
    text = data.decode("utf-8", errors="replace")
    if mode == "path":
        if PATH_RE.search(text):
            report(rel, "path", text)
        return
    for no, line in enumerate(text.splitlines(), 1):
        if TERM_RE.search(line):
            report(f"{rel}:{no}", "all", line)


def png_text(data):
    """Inhalt der PNG-Textblöcke (tEXt, zTXt, iTXt, eXIf), auch komprimiert."""
    out, i = [], 8
    while i + 8 <= len(data):
        n = int.from_bytes(data[i:i + 4], "big")
        kind = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + n]
        try:
            if kind == b"tEXt":
                out.append(body.decode("latin-1"))
            elif kind == b"zTXt":
                key, _, rest = body.partition(b"\0")
                out.append(key.decode("latin-1") + " " + zlib.decompress(rest[1:]).decode("latin-1"))
            elif kind == b"iTXt":
                key, _, rest = body.partition(b"\0")
                flag, rest = rest[0], rest[2:]
                _lang, _, rest = rest.partition(b"\0")
                _tkey, _, txt = rest.partition(b"\0")
                out.append(key.decode("latin-1") + " " + (zlib.decompress(txt) if flag else txt).decode("utf-8", "replace"))
            elif kind == b"eXIf":
                out.append(body.decode("latin-1"))
        except (zlib.error, IndexError):
            out.append(f"[unlesbarer {kind.decode('latin-1')}-Block]")
        i += 12 + n
    return "\n".join(out)


def scan_image(data, rel, extra=()):
    meta = []
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        meta.append(png_text(data))
    # EXIF/XMP/IPTC und PDF-Infos liegen meist als ASCII im Kopf der Datei.
    strings = re.findall(rb"[\x20-\x7e]{6,}", data[:262144] + data[-65536:])
    meta.append("\n".join(s.decode("ascii") for s in strings))
    meta += extra  # xattr und mdls, nur für Dateien auf der Platte
    blob = "\n".join(meta)
    if TERM_RE.search(blob):
        report(f"{rel} (Metadaten)", "all", blob)


def walk(base):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".app")]
        for f in filenames:
            yield Path(dirpath) / f


def tree_files(base):
    """Im Git-Repository: alles, was ein Commit enthielte (.gitignore gilt), sonst alle Dateien."""
    if (base / ".git").exists():
        out = subprocess.run(["git", "-C", str(base), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, check=True).stdout
        return [base / f for f in out.decode("utf-8").split("\0") if f and (base / f).is_file()]
    return list(walk(base))


def scan_tree(base):
    for p in tree_files(base):
        rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
        scan_file(p, rel)
        if p.suffix.lower() in IMAGE_EXT or p.suffix.lower() == ".svg":
            report(f"{rel} (Dateiname)", "all", p.name)


def scan_app(app):
    res = app / "Contents" / "Resources"
    backend = res / "backend"
    if (backend / "plugins").exists():
        findings.append(f"{app.name}: enthält backend/plugins (Builds mit Plugins nicht weitergeben)")
    own = [app / "Contents" / "Info.plist", *sorted((app / "Contents" / "MacOS").glob("*"))]
    own += [p for p in backend.iterdir() if p.is_file()] if backend.is_dir() else []
    own += list(walk(backend / "benthic")) if (backend / "benthic").is_dir() else []
    for p in own:
        rel = f"{app.name}/{p.relative_to(app)}"
        if p.parent.name == "MacOS" and p.suffix.lower() not in IMAGE_EXT:
            scan_file(p, rel, mode="path")
            strings = "\n".join(s.decode("ascii") for s in re.findall(rb"[\x20-\x7e]{6,}", p.read_bytes()))
            for line in strings.splitlines():
                if TERM_RE.search(line):
                    report(rel, "all", line)
        else:
            scan_file(p, rel)
    # Alles andere (Laufzeit, Bibliotheken, .pyc, Archive): nur persönliche Pfade.
    for dirpath, _dirs, files in os.walk(app):
        for f in files:
            p = Path(dirpath) / f
            if (p.suffix in (".pyc", ".py", ".txt", ".json", ".plist", ".so", ".dylib")
                    or p.suffix.lower() in ZIP_EXT or "MacOS" in p.parts):
                scan_file(p, f"{app.name}/{p.relative_to(app)}", mode="path")


for t in targets:
    if not t.exists():
        print(f"Pfad fehlt: {t}", file=sys.stderr)
        sys.exit(2)
    if t.suffix == ".app":
        scan_app(t)
    elif t.is_dir():
        scan_tree(t)
    else:
        scan_file(t, str(t))

if findings:
    print(f"trace_check: {len(findings)} Treffer")
    for f in findings:
        print("  " + f)
    sys.exit(1)
print(f"trace_check: sauber ({', '.join(str(t) for t in targets)})")
PY
