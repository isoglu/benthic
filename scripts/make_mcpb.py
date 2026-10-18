#!/usr/bin/env python3
"""Benthic.mcpb zum Anhängen an ein GitHub-Release (offizielle MCP-Registry, Smithery).

Das Paket, das die App für Claude Desktop schreibt (benthic/connect.py, build_bundle), gilt nur
für die Installation, die es erzeugt hat: es trägt deren Python, Datenordner und Port als Umgebung
ein, dazu den Namen der Person und die Sprache der Oberfläche. Auf einem anderen Mac zeigt der
Datenordner in ein fremdes Benutzerverzeichnis. Dieses Paket gilt auf jedem Mac mit Benthic:

- keine Umgebung: das Startskript sucht die App wie die Plugins (FINDER in make_plugins.py) und
  ruft ihren festen Startbefehl Contents/Resources/bin/benthic-mcp auf, der Datenordner und
  Port selbst aus Info.plist und $HOME bestimmt;
- englische Texte, Autor Sinan Isoglu, Version aus benthic/__init__.py;
- ZIP ohne Kompression, mit festen Zeitstempeln und Rechten: gleiche Quellen ergeben dieselben
  Bytes, auch mit einer anderen zlib oder Python-Version. Die SHA-256 (fileSha256 in server.json)
  bleibt also stabil, solange sich am Inhalt nichts ändert.

    python3 scripts/make_mcpb.py              schreibt dist/Benthic.mcpb
    python3 scripts/make_mcpb.py --out PFAD   schreibt das Paket nach PFAD

Danach stehen Größe, SHA-256 und der packages-Block für server.json in der Ausgabe.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import make_plugins as plugins  # noqa: E402
from benthic import __version__, connect  # noqa: E402

NAME = "Benthic.mcpb"
ICON = ROOT / "benthic" / "static" / "mcp-icon.png"
STAMP = (1980, 1, 1, 0, 0, 0)  # frühestes Datum, das ZIP kennt

LAUNCHER = (
    "#!/bin/sh\n"
    "# Benthic MCP: finds the installed Benthic app and starts its MCP server\n"
    "# over stdio. The app sets its own data folder and port, so nothing here belongs to one Mac.\n"
    + plugins.FINDER + "\n"
)
REQUIRES = (" This bundle starts the MCP server of the Benthic app for macOS, "
            f"which must be installed: {plugins.SITE}/download/")


def manifest() -> dict:
    """Wie das Paket der App, ohne Umgebung, auf Englisch und mit den Angaben der Plugins."""
    m = connect.manifest(lang="en", env={})
    m.update({
        "description": plugins.DESCRIPTION,
        "long_description": m["long_description"] + REQUIRES,
        "author": plugins.AUTHOR,
        "repository": {"type": "git", "url": plugins.REPO},
        "homepage": plugins.SITE,
        "support": plugins.REPO + "/issues",
        "license": plugins.LICENSE,
        "keywords": plugins.KEYWORDS,
    })
    return m


def entries() -> list:
    """(Pfad im Paket, Inhalt, Rechte) in fester Reihenfolge, wie im Paket der App."""
    text = json.dumps(manifest(), indent=2, ensure_ascii=False) + "\n"
    return [("manifest.json", text.encode("utf-8"), 0o644),
            ("server/benthic-mcp.sh", LAUNCHER.encode("utf-8"), 0o755),
            ("icon.png", ICON.read_bytes(), 0o644)]


def bundle() -> bytes:
    """Das Paket als Bytes, ohne Datei: so lässt sich die SHA-256 auch aus den Quellen allein berechnen."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
        for name, data, mode in entries():
            info = zipfile.ZipInfo(name, date_time=STAMP)
            info.create_system = 3  # Unix, damit die Rechte gelten
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, data)
    return buf.getvalue()


def package(data: bytes) -> dict:
    """Eintrag für "packages" in server.json; gilt erst, wenn genau diese Bytes am Release hängen."""
    return {
        "registryType": "mcpb",
        "identifier": f"{plugins.REPO}/releases/download/v{__version__}/{NAME}",
        "fileSha256": hashlib.sha256(data).hexdigest(),
        "transport": {"type": "stdio"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benthic.mcpb for a GitHub release")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / NAME)
    out, data = parser.parse_args().out.resolve(), bundle()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out)
    shown = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
    print(f"make_mcpb: {shown} (Benthic {__version__}, {len(data)} Bytes)")
    print(f"sha256 {hashlib.sha256(data).hexdigest()}")
    print(f"packages für server.json, sobald die Datei an Release v{__version__} hängt:")
    print(json.dumps({"packages": [package(data)]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
