"""Was KI-Werkzeuge über MCP mit Benthic tun: ein Eintrag je Werkzeugaufruf.

Der MCP-Server meldet jeden Aufruf, wenn er beginnt und wenn er endet (fertig oder Fehler, Dauer, wie viel Text an das
Werkzeug zurückging). Die App und Grimpo zeigen daraus, welches Werkzeug wann was getan hat. Das Protokoll liegt nur auf
diesem Mac im Datenordner (ai-activity.json) und übersteht Neustarts des Backends.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, List

from . import config
from .i18n import tr

KEEP = 200
STALE = 15 * 60  # shortcut: ein Aufruf ohne Ende gilt danach als abgebrochen (MCP-Prozess weg); länger wartet kein Werkzeug

# Anzeigenamen aus dem Namen, den ein Client beim Verbinden nennt (clientInfo.name), etwa "claude-ai" -> Claude.
NAMES = (("claude", "Claude"), ("codex", "Codex"), ("chatgpt", "ChatGPT"), ("antigravity", "Antigravity"),
         ("gemini", "Gemini"), ("cursor", "Cursor"), ("grok", "Grok"), ("devin", "Devin"), ("windsurf", "Windsurf"),
         ("zed", "Zed"), ("goose", "Goose"), ("kiro", "Kiro"), ("perplexity", "Perplexity"), ("raycast", "Raycast"),
         ("lm", "LM Studio"), ("code", "VS Code"))

_lock = threading.Lock()
_seen: Dict[str, float] = {}


def is_ai(client) -> bool:
    return str(client or "").startswith("mcp")


def name(client) -> str:
    """Anzeigename; ein Client ohne Namen heißt neutral „KI-Werkzeug“ (nie geraten)."""
    raw = re.sub(r"^mcp:?", "", str(client or "")).lower()
    if raw in ("", "client"):
        return tr("KI-Werkzeug")
    hit = next((label for key, label in NAMES if key in raw), None)
    return hit or raw[:1].upper() + raw[1:]


def _path() -> Path:
    return config.data_dir() / "ai-activity.json"


def _read() -> List[Dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(calls: List[Dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(calls[-KEEP:], ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def seen(client: str) -> None:
    with _lock:
        _seen[client] = time.time()


def clients() -> List[Dict]:
    now = time.time()
    with _lock:
        return [{"client": c, "name": name(c), "last_seen": t, "connected": now - t < 300} for c, t in _seen.items()]


def note(client: str, data: Dict) -> None:
    """Ein Aufruf beginnt oder ändert sich (gleiche id): Werkzeug, Beschreibung, Stand, Dauer, Textmenge."""
    now = time.time()
    cid = str(data.get("id") or "")[:40]
    with _lock:
        _seen[client] = now
        calls = _read()
        entry = next((c for c in reversed(calls) if cid and c.get("id") == cid and c.get("client") == client), None)
        if entry is None:  # neuer Aufruf; ältere MCP-Server melden ohne id, dann ist jede Meldung ein Eintrag
            entry = {"id": cid or f"{now:.6f}", "ts": now, "client": client, "tool": "", "summary": "", "status": None}
            calls.append(entry)
        for key in ("tool", "summary"):
            if data.get(key):
                entry[key] = str(data[key])[:200]
        if data.get("status") in ("running", "done", "error"):
            entry["status"] = data["status"]
        for key in ("ms", "chars"):
            if isinstance(data.get(key), int):
                entry[key] = data[key]
        entry["updated"] = now
        _save(calls)


def recent(limit: int = 20) -> List[Dict]:
    """Die letzten Aufrufe, neueste zuerst, mit Anzeigename; hängende gelten nach STALE als abgebrochen."""
    now = time.time()
    with _lock:
        calls = _read()[-limit:]
    out = []
    for c in reversed(calls):
        c = dict(c, name=name(c.get("client")))
        if c.get("status") == "running" and now - float(c.get("updated") or c.get("ts") or 0) > STALE:
            c["status"] = "gone"
        out.append(c)
    return out
