"""Benthic mit LLMs verbinden: MCP-Paket für Claude Desktop und Einträge für andere Programme.

Das Paket (``Benthic.mcpb``) ist ein ZIP mit ``manifest.json`` (Spezifikation 0.3),
einem Startskript und dem Icon. Es merkt sich, aus welcher Installation es
erzeugt wurde (Python, Datenordner); wird die App verschoben, sucht das
Startskript sie an den üblichen Orten und über Spotlight. Das Paket zum Release,
ohne Bezug zu einer Installation, schreibt scripts/make_mcpb.py.
"""

from __future__ import annotations

import base64
import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

from . import __version__, config
from .i18n import tr

ROOT = Path(__file__).resolve().parent.parent  # Projektordner bzw. Contents/Resources/backend
DEFAULT_BUNDLE_ID = "com.isoglu.benthic"

LAUNCHER = """#!/bin/sh
# Benthic MCP: startet den Server aus der installierten App.
export PYTHONDONTWRITEBYTECODE=1
run() {
  B="$1/Contents/Resources/backend"
  if [ -x "$B/runtime/bin/python3.12" ]; then
    export PYTHONPATH="$B:$B/site-packages"
    exec "$B/runtime/bin/python3.12" -m benthic.mcp_server
  fi
}
# 1) Die Installation, aus der dieses Paket erzeugt wurde.
if [ -n "$BENTHIC_PYTHON" ] && [ -x "$BENTHIC_PYTHON" ]; then
  exec "$BENTHIC_PYTHON" -m benthic.mcp_server
fi
# 2) Die App an üblichen Orten, dann über Spotlight.
for app in "$BENTHIC_APP" "/Applications/Benthic.app" "$HOME/Applications/Benthic.app"; do
  [ -n "$app" ] && run "$app"
done
for id in "$BENTHIC_BUNDLE_ID" com.isoglu.benthic; do
  [ -n "$id" ] || continue
  found=$(mdfind "kMDItemCFBundleIdentifier == '$id'" 2>/dev/null | head -n 1)
  [ -n "$found" ] && run "$found"
done
echo "Benthic.app was not found. Install Benthic and connect again." >&2
exit 1
"""


def app_bundle() -> Optional[Path]:
    """Pfad zu Benthic.app, wenn dieses Backend aus der App läuft."""
    if ROOT.name == "backend" and ROOT.parent.name == "Resources":
        return ROOT.parent.parent.parent
    return None


def app_info() -> Dict[str, str]:
    """Name und Kennung der App, aus der dieses Backend läuft (sonst die Standardwerte)."""
    info = {"name": "Benthic", "id": DEFAULT_BUNDLE_ID}
    app = app_bundle()
    if app:
        try:
            with open(app / "Contents" / "Info.plist", "rb") as f:
                plist = plistlib.load(f)
            info["name"] = str(plist.get("CFBundleName") or info["name"])
            info["id"] = str(plist.get("CFBundleIdentifier") or info["id"])
        except (OSError, plistlib.InvalidFileException):
            pass
    return info


def server_name() -> str:
    """Kurzname für MCP-Einträge, z. B. "benthic" (aus dem App-Namen abgeleitet)."""
    return re.sub(r"[^a-z0-9]+", "-", app_info()["name"].lower()).strip("-") or "benthic"


def server_env() -> Dict[str, str]:
    """Umgebung, mit der ein MCP-Client den Server starten soll (wie dieses Backend)."""
    paths = [str(ROOT)] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p and p != str(ROOT)]
    env = {
        "PYTHONPATH": os.pathsep.join(dict.fromkeys(paths)),
        "BENTHIC_DATA_DIR": str(config.data_dir()),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if os.environ.get("BENTHIC_PORT"):
        env["BENTHIC_PORT"] = os.environ["BENTHIC_PORT"]
    return env


def client_configs() -> Dict:
    """Einträge zum Kopieren für Claude Code, Cursor, LM Studio, VS Code und Claude Desktop."""
    env = server_env()
    cmd, args = sys.executable, ["-m", "benthic.mcp_server"]
    entry = {"command": cmd, "args": args, "env": env}
    env_flags = " ".join(f"--env {k}={shlex.quote(v)}" for k, v in env.items())
    name = server_name()
    return {
        "claude_code": f"claude mcp add {name} --scope user {env_flags} -- {shlex.quote(cmd)} -m benthic.mcp_server",
        "mcp_json": json.dumps({"mcpServers": {name: entry}}, indent=2, ensure_ascii=False),
        "vscode_json": json.dumps({"servers": {name: {"type": "stdio", **entry}}}, indent=2, ensure_ascii=False),
        "targets": {
            "cursor": "~/.cursor/mcp.json",
            "lmstudio": "~/.lmstudio/mcp.json",
            "vscode": tr(".vscode/mcp.json im Projektordner"),
            "claude_desktop": "~/Library/Application Support/Claude/claude_desktop_config.json",
        },
    }


def launcher_env() -> Dict[str, str]:
    """Umgebung für den MCP-Start: wie dieses Backend, mit der installierten App als Rückfall."""
    env = dict(server_env(), BENTHIC_PYTHON=sys.executable)
    app = app_bundle()
    if app:
        env["BENTHIC_APP"] = str(app)
        env["BENTHIC_BUNDLE_ID"] = app_info()["id"]
    return env


def write_launcher() -> Path:
    """Schreibt den Startbefehl für alle KI-Assistenten in den Datenordner.

    Ein Pfad statt eines langen Befehls mit Umgebungsvariablen: die Datei setzt die Umgebung
    dieser Installation und findet die App auch, wenn sie verschoben wurde (wie das Paket für
    Claude Desktop). Sie wird neu geschrieben, sobald die App die KI-Anbindung zeigt.
    """
    path = config.data_dir() / f"{server_name()}-mcp.sh"
    exports = "\n".join(f"export {k}={shlex.quote(v)}" for k, v in launcher_env().items())
    body = LAUNCHER.split("\n", 1)[1]
    text = ("#!/bin/sh\n# Von Benthic erzeugt: startet den MCP-Server dieser Installation.\n"
            + exports + "\n" + body)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)
    return path


CHAT_PROMPT = ("Liste alle Quellen deiner letzten Antwort einzeln auf, eine pro Zeile: "
               "Autor:innen, Jahr, Titel, Zeitschrift oder Verlag und die DOI, falls bekannt. "
               "Erfinde keine DOI; lass sie weg, wenn du sie nicht sicher weißt.")

# Apps, die Benthic erkennt (Bundle-ID überlebt Umbenennungen wie Codex → ChatGPT).
APP_IDS = {
    "claude-desktop": ("com.anthropic.claudefordesktop", "Claude.app"),
    "chatgpt": ("com.openai.codex", "ChatGPT.app"),
    "antigravity": ("com.google.antigravity", "Antigravity.app"),
    "lmstudio": ("ai.elementlabs.lmstudio", "LM Studio.app"),
    "cursor": ("com.todesktop.230313mzl4w4u92", "Cursor.app"),
    "vscode": ("com.microsoft.VSCode", "Visual Studio Code.app"),
}
_app_cache: Dict[str, tuple] = {}


def find_app(key: str) -> Optional[Path]:
    """Installierte App zu einem Eintrag aus APP_IDS (eine Minute zwischengespeichert)."""
    if key not in APP_IDS or sys.platform != "darwin":
        return None
    hit = _app_cache.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    bundle_id, name = APP_IDS[key]
    found = next((p for p in (Path("/Applications") / name, Path.home() / "Applications" / name) if p.is_dir()), None)
    if not found:
        try:
            out = subprocess.run(["mdfind", f"kMDItemCFBundleIdentifier == '{bundle_id}'"],
                                 capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
            found = Path(out[0]) if out else None
        except (OSError, subprocess.SubprocessError):
            found = None
    _app_cache[key] = (time.time(), found)
    return found


# --------------------------------------------------------------------------
# Einträge in fremde Konfigurationsdateien schreiben (nur auf Klick)
# --------------------------------------------------------------------------
def codex_config() -> Path:
    """Gemeinsame Datei der ChatGPT-App (Codex), der Codex-CLI und der IDE-Erweiterung."""
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"


# id -> (Datei, Format, Schlüssel der Serverliste)
TARGETS = {
    "chatgpt": (codex_config, "toml", "mcp_servers"),
    "antigravity": (lambda: Path.home() / ".gemini" / "config" / "mcp_config.json", "json", "mcpServers"),
    "gemini-cli": (lambda: Path.home() / ".gemini" / "settings.json", "json", "mcpServers"),
}


def _toml():
    try:
        import tomllib  # Python 3.11+ (die App bringt 3.12 mit)
        return tomllib
    except ImportError:
        return None


def _read_servers(path: Path, fmt: str, key: str) -> Optional[Dict]:
    """Serverliste aus einer Konfigurationsdatei; {} ohne Datei, None wenn unlesbar."""
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        if fmt == "toml":
            lib = _toml()
            if lib is None:
                return None
            data = lib.loads(text)
        else:
            data = json.loads(text)
    except ValueError:
        return None
    servers = data.get(key, {}) if isinstance(data, dict) else None
    return servers if isinstance(servers, dict) else None


def entry_state(target: str, launcher: Path) -> str:
    """"current" (eingetragen), "stale" (anderer Befehl), "missing" oder "unreadable"."""
    path_fn, fmt, key = TARGETS[target]
    servers = _read_servers(path_fn(), fmt, key)
    if servers is None:
        return "unreadable"
    entry = servers.get(server_name())
    if not isinstance(entry, dict):
        return "missing"
    return "current" if entry.get("command") == str(launcher) else "stale"


def _toml_block(name: str, command: str) -> str:
    # JSON-Strings sind gültige TOML-Basic-Strings (gleiche Escapes).
    return f"[mcp_servers.{name}]\ncommand = {json.dumps(command)}\n"


def _replace_toml_table(text: str, name: str, block: str) -> str:
    """Ersetzt [mcp_servers.<name>] samt Untertabellen oder hängt den Block an."""
    head = re.compile(r'^\s*\[\s*mcp_servers\s*\.\s*(?:"%s"|%s)\s*(?:\]|\.)' % (re.escape(name), re.escape(name)))
    lines = text.splitlines(keepends=True)
    out, i, done = [], 0, False
    while i < len(lines):
        if head.match(lines[i]):
            if not done:
                out.append(block)
                done = True
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("["):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    result = "".join(out)
    if not done:
        result = result.rstrip("\n") + ("\n\n" if result.strip() else "") + block
    return result


def install_entry(target: str, launcher: Path) -> Dict:
    """Trägt Benthic in die Konfiguration eines Assistenten ein (Sicherung daneben).

    Bestehende Einträge anderer Server bleiben unberührt. Eine Datei, die sich nicht lesen
    lässt (z. B. JSON mit Kommentaren), fasst Benthic nicht an.
    """
    path_fn, fmt, key = TARGETS[target]
    path, name, command = path_fn(), server_name(), str(launcher)
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    if old.strip() and _read_servers(path, fmt, key) is None:
        raise ValueError(tr("{path} lässt sich nicht sicher lesen. Bitte den Eintrag von Hand ergänzen.", path=_home(path)))
    if fmt == "toml":
        lib = _toml()
        if lib is None:
            raise ValueError(tr("Dafür fehlt Python 3.11 oder neuer. Bitte den Eintrag von Hand ergänzen."))
        new = _replace_toml_table(old, name, _toml_block(name, command))
        try:
            check = lib.loads(new)[key][name]["command"]
        except (ValueError, KeyError, TypeError):
            check = None
        if check != command:
            raise ValueError(tr("{path} lässt sich nicht sicher lesen. Bitte den Eintrag von Hand ergänzen.", path=_home(path)))
    else:
        data = json.loads(old) if old.strip() else {}
        servers = data.setdefault(key, {})
        entry = dict(servers.get(name) or {}) if isinstance(servers.get(name), dict) else {}
        entry.pop("disabled", None)
        entry.update(command=command)
        servers[name] = entry
        new = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    backup = None
    if old:
        backup = path.with_name(path.name + ".benthic-backup")
        backup.write_text(old, encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".benthic-tmp")
    tmp.write_text(new, encoding="utf-8")
    if path.exists():
        os.chmod(tmp, path.stat().st_mode & 0o777)
    os.replace(tmp, path)
    return {"path": _home(path), "backup": _home(backup) if backup else None}


def _home(path: Optional[Path]) -> str:
    if path is None:
        return ""
    home = str(Path.home())
    return "~" + str(path)[len(home):] if str(path).startswith(home) else str(path)


def _b64(obj: Dict) -> str:
    return base64.b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode()


def assistants(launcher: Path) -> List[Dict]:
    """Einrichtung je KI-Assistent: Schritte, Befehl, Eintrag oder Verbinden-Knopf, Anleitung.

    kind: "oneclick" (Paket für Claude Desktop), "auto" (Benthic schreibt den Eintrag auf Klick),
    "deeplink" (die App fragt selbst nach), "command", "file", "paste" (Chat im Browser).
    """
    name = server_name()
    cmd = str(launcher)
    q = shlex.quote(cmd)
    std = json.dumps({"mcpServers": {name: {"command": cmd}}}, indent=2, ensure_ascii=False)
    toml = _toml_block(name, cmd)
    apps, dev, web = tr("Desktop-Apps"), tr("Programmierumgebungen und CLIs"), tr("Chat im Browser")

    def state(target: str) -> str:
        try:
            return entry_state(target, launcher)
        except OSError:
            return "unreadable"

    out = [
        {"id": "claude-desktop", "name": "Claude Desktop", "group": apps, "kind": "oneclick",
         "installed": bool(find_app("claude-desktop")),
         "steps": [tr("Auf „Mit Claude Desktop verbinden“ klicken."),
                   tr("In Claude Desktop „Installieren“ bestätigen. Danach steht Benthic als Werkzeug bereit.")],
         "docs": "https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop"},
        {"id": "chatgpt", "name": tr("ChatGPT mit Codex"), "group": apps, "kind": "auto", "app": "ChatGPT",
         "installed": bool(find_app("chatgpt")), "state": state("chatgpt"), "file": _home(codex_config()),
         "snippet": cmd, "snippet_label": tr("Befehl für „Server hinzufügen“ (STDIO)"),
         "alt_file": _home(codex_config()), "alt_snippet": toml,
         "steps": [tr("Auf „Mit ChatGPT verbinden“ klicken. Benthic trägt sich in {file} ein; die ChatGPT-App, "
                      "die Codex-CLI und die IDE-Erweiterung lesen diese Datei gemeinsam.", file=_home(codex_config())),
                   tr("ChatGPT neu starten und in den Codex-Modus wechseln. Mit /mcp im Eingabefeld prüfen, ob „{name}“ "
                      "verbunden ist.", name=name),
                   tr("Von Hand geht es auch: in ChatGPT unter Einstellungen › MCP-Server › Server hinzufügen „STDIO“ "
                      "wählen und den Befehl darunter eintragen.")],
         "docs": "https://learn.chatgpt.com/docs/extend/mcp"},
        {"id": "antigravity", "name": "Antigravity", "group": apps, "kind": "auto", "app": "Antigravity",
         "installed": bool(find_app("antigravity")), "state": state("antigravity"),
         "file": _home(TARGETS["antigravity"][0]()), "snippet": std,
         "steps": [tr("Auf „Mit Antigravity verbinden“ klicken. Benthic trägt sich in {file} ein; die Antigravity-App, "
                      "die IDE und die CLI lesen diese Datei gemeinsam.", file=_home(TARGETS["antigravity"][0]())),
                   tr("In Antigravity unter Einstellungen › Customizations › Installed MCP Servers aktualisieren "
                      "oder Antigravity neu starten."),
                   tr("In der Antigravity-IDE steht die Datei unter „…“ im Agent-Bereich › MCP Servers › "
                      "Manage MCP Servers › View raw config.")],
         "docs": "https://antigravity.google/docs/mcp"},
        {"id": "lmstudio", "name": "LM Studio", "group": apps, "kind": "deeplink", "app": "LM Studio",
         "installed": bool(find_app("lmstudio")),
         "link": f"lmstudio://add_mcp?name={name}&config={_b64({'command': cmd})}",
         "file": "mcp.json", "snippet": std,
         "steps": [tr("Auf „Mit LM Studio verbinden“ klicken und in LM Studio bestätigen."),
                   tr("Benthic danach im Chat einschalten.")],
         "docs": "https://lmstudio.ai/docs/app/mcp"},
        {"id": "claude-code", "name": "Claude Code", "group": dev, "kind": "command",
         "snippet": f"claude mcp add {name} --scope user -- {q}",
         "alt_label": tr("Oder als Plugin (sucht die App selbst)"),
         "alt_snippet": "claude plugin marketplace add isoglu/benthic\nclaude plugin install benthic@benthic",
         "steps": [tr("Den Befehl im Terminal ausführen."), tr("In Claude Code mit /mcp prüfen, ob „{name}“ verbunden ist.", name=name)],
         "docs": "https://docs.claude.com/en/docs/claude-code/mcp"},
        {"id": "codex", "name": "Codex CLI", "group": dev, "kind": "command",
         "snippet": f"codex mcp add {name} -- {q}", "state": state("chatgpt"),
         "alt_file": _home(codex_config()), "alt_snippet": toml,
         "steps": [tr("Den Befehl im Terminal ausführen. Er gilt auch für die ChatGPT-App und die IDE-Erweiterung."),
                   tr("Oder den Eintrag darunter in {file} ergänzen.", file=_home(codex_config()))],
         "docs": "https://learn.chatgpt.com/docs/extend/mcp"},
        {"id": "gemini-cli", "name": "Gemini CLI", "group": dev, "kind": "auto", "app": "Gemini CLI",
         "installed": True, "state": state("gemini-cli"), "file": _home(TARGETS["gemini-cli"][0]()), "snippet": std,
         "steps": [tr("Auf „Mit Gemini CLI verbinden“ klicken oder den Eintrag unter „mcpServers“ in {file} ergänzen.",
                      file=_home(TARGETS["gemini-cli"][0]())),
                   tr("Gemini CLI neu starten und mit /mcp prüfen.")],
         "docs": "https://geminicli.com/docs/tools/mcp-server/"},
        {"id": "cursor", "name": "Cursor", "group": dev, "kind": "deeplink", "app": "Cursor",
         "installed": bool(find_app("cursor")),
         "link": f"cursor://anysphere.cursor-deeplink/mcp/install?name={name}&config={_b64({'command': cmd})}",
         "file": "~/.cursor/mcp.json", "snippet": std,
         "steps": [tr("Auf „Mit Cursor verbinden“ klicken und in Cursor „Installieren“ bestätigen."),
                   tr("Oder den Eintrag unter „mcpServers“ in ~/.cursor/mcp.json ergänzen und Cursor neu laden.")],
         "docs": "https://cursor.com/docs/mcp"},
        {"id": "vscode", "name": "VS Code", "group": dev, "kind": "deeplink", "app": "VS Code",
         "installed": bool(find_app("vscode")),
         "link": "vscode:mcp/install?" + quote(json.dumps({"name": name, "command": cmd}, separators=(",", ":"))),
         "file": tr(".vscode/mcp.json im Projektordner"),
         "snippet": json.dumps({"servers": {name: {"type": "stdio", "command": cmd}}}, indent=2, ensure_ascii=False),
         "steps": [tr("Auf „Mit VS Code verbinden“ klicken und in VS Code bestätigen."),
                   tr("Im Chat den Agent-Modus wählen; Benthic erscheint unter den Werkzeugen.")],
         "docs": "https://code.visualstudio.com/docs/agent-customization/mcp-servers"},
        {"id": "grok-build", "name": "Grok Build (xAI)", "group": dev, "kind": "command",
         "snippet": q,
         "steps": [tr("Grok Build startet lokale MCP-Server. Diesen Befehl als stdio-Server mit dem Namen „{name}“ "
                      "eintragen, zum Beispiel mit „grok mcp add“ (Aufbau mit „grok mcp add --help“ prüfen).", name=name)],
         "docs": "https://x.ai/build"},
        {"id": "chat-apps", "name": tr("ChatGPT, Gemini oder Grok im Browser"), "group": web, "kind": "paste",
         "snippet": tr(CHAT_PROMPT),
         "steps": [tr("Chats im Browser können keine Werkzeuge auf deinem Mac starten."),
                   tr("Den Assistenten mit dem Text darunter um eine saubere Quellenliste bitten."),
                   tr("Die Antwort kopieren und in Benthic unter „Prüfen“ einfügen.")]},
    ]
    return out


def manifest(author: str = "", lang: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> Dict:
    """manifest.json für Benthic.mcpb.

    Ohne ``env`` startet das Paket diese Installation (launcher_env), in der Sprache der Oberfläche.
    Das Paket zum Release (scripts/make_mcpb.py) übergibt ``lang="en"`` und ``env={}``: dann fehlt
    die Umgebung ganz, und das Startskript sucht die App selbst.
    """
    from .mcp_server import TOOLS  # erst hier, damit der Import billig bleibt

    env = launcher_env() if env is None else env
    info = app_info()
    mcp_config = {"command": "/bin/sh", "args": ["${__dirname}/server/benthic-mcp.sh"]}
    if env:
        mcp_config["env"] = env
    return {
        "manifest_version": "0.3",
        "name": server_name(),
        "display_name": info["name"],
        "version": __version__,
        "description": tr("Prüft Literaturangaben aus LLMs auf erfundene Quellen, falsche DOIs und Rückzüge "
                          "und lädt echte Quellen über Open Access in deine Bibliothek.", lang=lang),
        "long_description": tr(
            "Benthic verbindet Claude mit deiner Literaturbibliothek. Claude kann Literaturlisten prüfen "
            "(gibt es die Quelle, passt sie zur Angabe, ist sie zurückgezogen?), echte Quellen als Open "
            "Access laden, heruntergeladene Artikel lesen, Projekte führen und Literaturverzeichnisse "
            "erzeugen. Benthic lädt dabei nur frei verfügbare Fassungen.", lang=lang
        ),
        "author": {"name": author or "Sinan Isoglu"},
        "icon": "icon.png",
        "server": {
            "type": "binary",
            "entry_point": "server/benthic-mcp.sh",
            "mcp_config": mcp_config,
        },
        "tools": [{"name": t["name"], "description": t["description"]} for t in TOOLS],
        "keywords": ["literature", "doi", "citations", "open access", "hallucination", "research"],
        "compatibility": {"platforms": ["darwin"]},
    }


def build_bundle(out: Optional[Path] = None, author: str = "") -> Path:
    out = out or (config.data_dir() / "Benthic.mcpb")
    tmp = out.with_name(out.name + ".tmp")
    icon = Path(__file__).resolve().parent / "static" / "mcp-icon.png"
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("manifest.json", json.dumps(manifest(author), indent=2, ensure_ascii=False) + "\n")
        info = zipfile.ZipInfo("server/benthic-mcp.sh")
        info.external_attr = 0o100755 << 16  # ausführbar, falls der Client das Skript direkt startet
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, LAUNCHER)
        if icon.is_file():
            z.write(icon, "icon.png")
    os.replace(tmp, out)
    return out


def claude_desktop() -> Optional[Path]:
    return find_app("claude-desktop")


def open_in_claude(bundle: Path) -> bool:
    """Öffnet das Paket in Claude Desktop (zeigt dort den Installationsdialog)."""
    app = claude_desktop()
    if sys.platform != "darwin" or not app:
        return False
    return subprocess.run(["open", "-a", str(app), str(bundle)], timeout=15).returncode == 0
