"""Der Weg durch eine Arbeit, vom Thema bis zur Abgabe: neun Schritte mit Aufgaben und einem Rat, abgehakt je Projekt.

Grimpo, der Begleiter auf dem Schreibtisch, zeigt den aktuellen Schritt und die nächste offene Aufgabe. Einige
Aufgaben führen direkt in Benthic (``action`` ist eine Ansicht der App). Der Stand liegt in ``guide.json`` im
Datenordner, ohne Text der Arbeit.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import config
from .i18n import tr

# (Schlüssel, Titel, [(Aufgabe, Ansicht der App oder None)], Rat)
STEPS = [
    ("topic", "Thema eingrenzen", [
        ("Ein Thema wählen, das dich interessiert und das sich in der Zeit bearbeiten lässt", None),
        ("Mit der Betreuung klären, was erwartet wird: Umfang, Abgabe, Zitierstil", None),
        ("Ein Projekt in Benthic anlegen", "projects"),
    ], "Eng ist besser als breit: Ein kleines, klares Thema trägt weiter als ein großes, ungefähres."),
    ("question", "Forschungsfrage", [
        ("Die Frage in einem Satz aufschreiben", None),
        ("Prüfen, ob sie sich mit Literatur oder Daten beantworten lässt", None),
        ("Zwei, drei Suchbegriffe festlegen, auf Deutsch und auf Englisch", None),
    ], "Eine gute Frage beginnt oft mit „Wie“ oder „Warum“ und lässt mehr zu als ein Ja oder Nein."),
    ("search", "Literatur suchen", [
        ("Mit „Quellen finden“ echte Arbeiten zu deinen Kernaussagen suchen", "find"),
        ("Überblicksarbeiten (Reviews) zuerst lesen", None),
        ("Wichtige Quellen ins Projekt übernehmen", "projects"),
        ("„Oft zitiert von deinen Quellen“ nutzen, um Grundlagentexte zu finden", "projects"),
    ], "Frag keinen Chat nach Quellen: Er erfindet sie oft. Such sie selbst, Benthic hilft dabei."),
    ("read", "Lesen und festhalten", [
        ("Die frei lesbaren PDFs laden", "projects"),
        ("Beim Lesen Seitenzahlen und wörtliche Zitate notieren", None),
        ("Wichtige Stellen mit Grimpo als Bildschirmfoto festhalten", None),
        ("Links zu Webquellen bei Grimpo ablegen", None),
    ], "Schreib zu jeder Notiz die Quelle und die Seite. Sonst suchst du später Stunden."),
    ("outline", "Gliederung", [
        ("Einleitung, Hauptteil und Schluss grob planen", None),
        ("Jedem Abschnitt eine Aufgabe für die Forschungsfrage geben", None),
        ("Die Quellen den Abschnitten zuordnen", None),
    ], "Die Gliederung darf sich beim Schreiben ändern. Sie ist ein Werkzeug, kein Vertrag."),
    ("draft", "Schreiben", [
        ("Die Arbeit in Benthic beobachten lassen, damit Quellen gleich geprüft werden", "verify"),
        ("Jede Aussage belegen, die nicht von dir stammt", None),
        ("Wörtliche Zitate in Anführungszeichen setzen, mit Seite", None),
    ], "Erst schreiben, dann glätten: Ein holpriger Entwurf ist leichter zu verbessern als eine leere Seite."),
    ("cite", "Zitieren", [
        ("Einen Zitierstil durchgehend verwenden", None),
        ("Das korrigierte Verzeichnis aus Benthic übernehmen", "verify"),
        ("Sicherstellen, dass jedes Zitat im Verzeichnis steht und umgekehrt", "verify"),
    ], "Was du übernimmst, kennzeichnest du: wörtlich mit Anführungszeichen, sinngemäß mit der Quelle."),
    ("check", "Prüfen", [
        ("Die ganze Arbeit in Benthic prüfen", "verify"),
        ("Wörtliche Zitate und Übernahmen mit den Volltexten abgleichen", "verify"),
        ("Alles unter „Handlungsbedarf“ klären", "verify"),
        ("Die Arbeit laut lesen oder lesen lassen", None),
    ], "Plane für die Prüfung einen ganzen Tag ein, nicht die letzte Stunde."),
    ("submit", "Abgeben", [
        ("Den Prüfbericht als PDF sichern", "verify"),
        ("Formalien prüfen: Deckblatt, Erklärung, Ränder, Dateiformat", None),
        ("Rechtzeitig abgeben und eine Kopie behalten", None),
    ], "Geschafft. Gönn dir eine Pause, bevor die nächste Arbeit beginnt."),
]

_lock = threading.Lock()


def _path() -> Path:
    return config.data_dir() / "guide.json"


def _load() -> Dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: Dict) -> None:
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def state(project: Optional[str]) -> Dict:
    """Die Schritte in der Sprache der Oberfläche, mit dem Stand für ein Projekt (oder ohne Projekt)."""
    done = (_load().get(project or "_") or {}).get("done") or {}
    steps, current = [], None
    for key, title, tasks, tip in STEPS:
        items = [{"id": f"{key}.{i}", "text": tr(text), "view": view, "done": bool(done.get(f"{key}.{i}"))}
                 for i, (text, view) in enumerate(tasks)]
        complete = all(t["done"] for t in items)
        if current is None and not complete:
            current = key
        steps.append({"key": key, "title": tr(title), "tip": tr(tip), "tasks": items, "complete": complete})
    total = sum(len(s["tasks"]) for s in steps)
    finished = sum(1 for s in steps for t in s["tasks"] if t["done"])
    return {"project": project, "steps": steps, "current": current, "done": finished, "total": total}


def mark(project: Optional[str], task_id: str, done: bool) -> Dict:
    keys = {f"{key}.{i}" for key, _t, tasks, _tip in STEPS for i in range(len(tasks))}
    if task_id not in keys:
        raise ValueError("task")
    with _lock:
        data = _load()
        entry = data.setdefault(project or "_", {"done": {}})
        if done:
            entry["done"][task_id] = time.time()
        else:
            entry["done"].pop(task_id, None)
        _save(data)
    return state(project)
