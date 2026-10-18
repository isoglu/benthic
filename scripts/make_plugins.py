#!/usr/bin/env python3
"""Plugin- und Erweiterungsdateien für KI-Assistenten, aus einer Quelle erzeugt.

Damit installiert man Benthic mit einem Befehl in Claude Code, Codex, Gemini CLI und VS Code,
direkt aus diesem Repository:

    claude plugin marketplace add isoglu/benthic && claude plugin install benthic@benthic
    codex plugin marketplace add isoglu/benthic && codex plugin add benthic@benthic
    gemini extensions install https://github.com/isoglu/benthic

Alle Dateien starten denselben Befehl: er sucht die installierte App (Programme-Ordner, eigener
Programme-Ordner, Spotlight) und ruft deren festen Startbefehl Contents/Resources/bin/benthic-mcp
auf (siehe macos/build_app.sh). Ohne App erklärt er, wo es sie gibt. Dazu kommt
server.json für die offizielle MCP-Registry, mit derselben Version.

    python3 scripts/make_plugins.py           schreibt die Dateien
    python3 scripts/make_plugins.py --check   prüft, ob sie aktuell sind (tests/test_version.py)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "benthic" / "__init__.py").read_text()).group(1)

NAME = "benthic"
SITE = "https://benthic.isoglu.com"
REPO = "https://github.com/isoglu/benthic"
LICENSE = "AGPL-3.0-only"
AUTHOR = {"name": "Sinan Isoglu", "url": "https://isoglu.com"}
DESCRIPTION = ("Check reference lists from language models for invented, misattributed and retracted sources, "
               "and fetch the real papers through open access, with the Benthic app for macOS.")
KEYWORDS = ["references", "citations", "doi", "open-access", "retractions", "research", "mcp"]
# Offizielle MCP-Registry: höchstens 100 Zeichen; ein Eintrag ist unveränderlich, korrigiert wird
# nur mit einer neuen Version.
REGISTRY_NAME = "io.github.isoglu/benthic"
REGISTRY_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
REGISTRY_DESCRIPTION = ("Checks references for invented, misattributed and retracted sources "
                        "and fetches open-access papers.")

# Sucht die App und startet ihren MCP-Server. Ein Inline-Skript statt einer Datei, weil nicht jeder
# Client eine Variable für den Plugin-Ordner kennt.
FINDER = (
    'for a in /Applications/Benthic.app "$HOME/Applications/Benthic.app" '
    '"$(mdfind "kMDItemCFBundleIdentifier == \'com.isoglu.benthic\'" 2>/dev/null | head -n 1)"; do '
    '[ -n "$a" ] && [ -x "$a/Contents/Resources/bin/benthic-mcp" ] && exec "$a/Contents/Resources/bin/benthic-mcp"; '
    'done; '
    f'echo "Benthic is not installed. Download it from {SITE}/download/" >&2; exit 1'
)
SERVER = {"command": "/bin/sh", "args": ["-c", FINDER]}


def files() -> dict:
    plugin = {"name": NAME, "description": DESCRIPTION, "version": VERSION, "author": AUTHOR,
              "homepage": SITE, "repository": REPO, "license": LICENSE, "keywords": KEYWORDS}
    return {
        ".mcp.json": {"mcpServers": {NAME: SERVER}},
        # Claude Code: Marktplatz und Plugin im selben Repository
        ".claude-plugin/marketplace.json": {
            "$schema": "https://code.claude.com/schemas/marketplace.json",
            "name": NAME,
            "description": DESCRIPTION,
            "owner": AUTHOR,
            "plugins": [{**plugin, "source": "./"}],
        },
        ".claude-plugin/plugin.json": {**plugin, "mcpServers": "./.mcp.json"},
        # Codex (ChatGPT-App, CLI, IDE-Erweiterung)
        ".agents/plugins/marketplace.json": {
            "name": NAME,
            "interface": {"displayName": "Benthic"},
            "plugins": [{"name": NAME, "source": {"source": "url", "url": REPO + ".git"},
                         "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                         "category": "Productivity"}],
        },
        ".codex-plugin/plugin.json": {
            **plugin,
            "skills": "./skills/",
            "mcpServers": "./.mcp.json",
            "interface": {
                "displayName": "Benthic",
                "shortDescription": "Check references, fetch open-access papers",
                "longDescription": DESCRIPTION + " Benthic runs on your Mac; the app must be installed.",
                "developerName": "Sinan Isoglu",
                "category": "Productivity",
                "capabilities": ["Interactive", "Write"],
                "websiteURL": SITE,
                "privacyPolicyURL": "https://isoglu.com/privacy/",
                "defaultPrompt": [
                    "Check the sources in your last answer with Benthic",
                    "Fetch the open-access PDFs for these DOIs",
                    "Write the bibliography of my Benthic project in APA",
                ],
                "brandColor": "#11507F",
                "composerIcon": "./macos/design/glyph.svg",
                "logo": "./macos/design/glyph.svg",
                "screenshots": [],
            },
        },
        # VS Code (Agent-Plugins: „Chat: Install Plugin From Source“)
        "plugin.json": {"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", **plugin},
        # Gemini CLI: gemini extensions install <Repository>
        "gemini-extension.json": {"name": NAME, "version": VERSION, "description": DESCRIPTION,
                                  "mcpServers": {NAME: SERVER}},
        # Offizielle MCP-Registry (mcp-publisher publish): der Server steckt in der App, es gibt kein
        # Paket; websiteUrl erklärt die Installation („Server with Custom Installation Path“).
        "server.json": {
            "$schema": REGISTRY_SCHEMA,
            "name": REGISTRY_NAME,
            "title": "Benthic",
            "description": REGISTRY_DESCRIPTION,
            "version": VERSION,
            "websiteUrl": SITE + "/download/",
            "repository": {"url": REPO, "source": "github"},
        },
    }


def render(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    check = "--check" in sys.argv
    stale = []
    for rel, data in files().items():
        path = ROOT / rel
        text = render(data)
        if check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                stale.append(rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if check and stale:
        print("make_plugins: veraltet (python3 scripts/make_plugins.py): " + ", ".join(stale), file=sys.stderr)
        return 1
    if not check:
        print(f"make_plugins: {len(files())} Dateien für {VERSION} geschrieben")
    return 0


if __name__ == "__main__":
    sys.exit(main())
