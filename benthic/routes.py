"""Zusatzwege: optionale Download-Wege nach Open Access (Erweiterungspunkt).

Benthic selbst bringt keine Zusatzwege mit. Ein Plugin kann welche anmelden, etwa
den Link-Resolver einer Hochschule oder den Dateiserver einer Arbeitsgruppe. Sie
laufen nur, wenn keine Open-Access-Kopie bekannt ist und die Einstellung
„Zusatzwege“ eingeschaltet ist; Werkzeuge über MCP nutzen sie nie.

Plugins werden aus drei Orten geladen:

* dem Ordner ``plugins`` neben dem Paket (``build_app.sh --plugin DIR`` kopiert
  ihn ins App-Bundle, wo er mitsigniert wird),
* dem Ordner ``plugins`` im Datenordner (eigene Plugins der Nutzer),
* den Pfaden in der Umgebungsvariable ``BENTHIC_PLUGINS`` (Entwicklung).

Ein Plugin ist ein Python-Paket (oder eine einzelne ``.py``-Datei) mit einer
Funktion ``register(api)``; ``api`` ist eine :class:`PluginAPI`. Siehe
docs/extending.md.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import config
from .i18n import tr

logger = logging.getLogger("benthic")

# Kanäle, die Benthic selbst vergibt; jeder andere Kanal ist die ID eines Zusatzwegs.
CORE_CHANNELS = ("oa", "library")


class RouteMiss(Exception):
    """Der Weg hat keine Kopie geliefert. ``retryable``: später erneut versuchen lohnt sich."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Hit:
    """Ergebnis eines erfolgreichen Abrufs."""

    content: bytes
    url: str
    source: str  # was die Oberfläche als Quelle zeigt, z. B. ein Hostname


class Route:
    """Basisklasse für einen Zusatzweg.

    ``fetch`` liefert einen :class:`Hit` oder wirft :class:`RouteMiss`. Über
    ``emit(phase, target, reason=None)`` meldet der Weg Zwischenschritte für die
    Live-Anzeige (``phase``: ``"try"`` oder ``"fail"``).
    """

    id: str = ""
    label: str = ""
    # Sind Kopien dieses Wegs frei verfügbar (Open Access)? None = unbekannt (Spalte „Open Access“ im Export).
    legal: Optional[bool] = None

    def targets(self) -> List[str]:
        """Namen der Ziele für die Anzeige (z. B. Hostnamen), in Reihenfolge."""
        return []

    def fetch(self, identifier: str, emit: Callable[..., None]) -> Hit:
        raise NotImplementedError


class PluginAPI:
    """Was ein Plugin in ``register(api)`` anmelden kann."""

    def __init__(self, name: str):
        self.name = name

    @property
    def data_dir(self) -> Path:
        """Beschreibbarer Ordner für eigene Daten des Plugins."""
        d = config.data_dir() / "plugin-data" / self.name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def add_route(self, route: Route) -> None:
        if not route.id or route.id in CORE_CHANNELS or route.id in _routes:
            raise ValueError(f"Ungültige oder doppelte Weg-ID: {route.id!r}")
        _routes[route.id] = route

    def add_blueprint(self, blueprint) -> None:
        """Eigene Endpunkte (Flask-Blueprint), am besten unter /api/plugins/<name>/."""
        _blueprints.append(blueprint)

    def add_ui(self, directory) -> None:
        """Ordner mit ``ui.js`` (und optional ``ui.css``); die Oberfläche lädt beides."""
        _ui[self.name] = Path(directory)


_routes: Dict[str, Route] = {}
_blueprints: List = []
_ui: Dict[str, Path] = {}
_status: Dict[str, Optional[str]] = {}  # Pfad -> None (geladen) oder Fehlertext
_loaded = False


def user_folder() -> Path:
    """Ordner für eigene Plugins (in der App ~/Library/Application Support/Benthic/plugins)."""
    return config.data_dir() / "plugins"


def plugin_paths() -> List[Path]:
    """Plugin-Kandidaten: Ordner ``plugins`` im Paket und im Datenordner, dann ``BENTHIC_PLUGINS``."""
    out: List[Path] = []
    # Im Dev-Betrieb ist der Datenordner das Projektverzeichnis: denselben Ordner nur einmal laden.
    for folder in dict.fromkeys((config.PROJECT_ROOT / "plugins", user_folder())):
        if folder.is_dir():
            out.extend(sorted(p for p in folder.iterdir() if not p.name.startswith((".", "_"))))
    for raw in os.environ.get("BENTHIC_PLUGINS", "").split(os.pathsep):
        if raw.strip():
            out.append(Path(raw.strip()).expanduser())
    return out


def _import(path: Path):
    name = path.stem if path.is_file() else path.name
    if path.is_dir():
        init = path / "__init__.py"
        if not init.is_file():
            raise ImportError(tr("{name} ist kein Python-Paket (__init__.py fehlt)", name=path.name))
        spec = importlib.util.spec_from_file_location(
            f"benthic_plugin_{name}", init, submodule_search_locations=[str(path)])
    elif path.suffix == ".py":
        spec = importlib.util.spec_from_file_location(f"benthic_plugin_{name}", path)
    else:
        return None, name
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, name


def load_plugins() -> None:
    """Lädt alle Plugins einmal. Ein defektes Plugin hält Benthic nicht auf."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    for path in plugin_paths():
        try:
            module, name = _import(path)
            if module is None:
                continue
            register = getattr(module, "register", None)
            if not callable(register):
                logger.warning("Plugin %s hat keine Funktion register(api)", path)
                _status[str(path)] = tr("Keine Funktion register(api)")
                continue
            register(PluginAPI(name))
            _status[str(path)] = None
            logger.info("Plugin geladen: %s", name)
        except Exception as exc:
            logger.exception("Plugin %s konnte nicht geladen werden", path)
            _status[str(path)] = f"{type(exc).__name__}: {exc}"


def _plugin_name(path: Path) -> str:
    return path.stem if path.suffix == ".py" else path.name


def overview() -> List[Dict]:
    """Plugins für die Einstellungen: beim Start geladen oder gescheitert, seither entfernt, neu oder aus.

    Neue und entfernte Plugins wirken erst nach einem Neustart (Flask nimmt danach keine Endpunkte mehr an).
    """
    out = []
    for key, error in _status.items():
        path = Path(key)
        state = "removed" if not path.exists() else "error" if error else "loaded"
        out.append({"name": _plugin_name(path), "state": state, "error": error})
    folder = user_folder()
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if str(path) in _status or path.name.startswith((".", "__")) or not (path.suffix == ".py" or path.is_dir()):
                continue
            off = path.name.startswith("_")
            out.append({"name": _plugin_name(path).lstrip("_"), "state": "off" if off else "new", "error": None})
    return out


def registered() -> List[Route]:
    return list(_routes.values())


def get(route_id: str) -> Optional[Route]:
    return _routes.get(route_id)


def blueprints() -> List:
    return list(_blueprints)


def ui_dirs() -> Dict[str, Path]:
    return dict(_ui)


def is_route_channel(channel) -> bool:
    """Stammt ein Ergebnis aus einem Zusatzweg (und nicht aus Benthic selbst)?"""
    return bool(channel) and channel not in CORE_CHANNELS


def describe() -> List[Dict]:
    """Angemeldete Wege für die Oberfläche."""
    out = []
    for r in _routes.values():
        try:
            targets = list(r.targets())
        except Exception:
            targets = []
        out.append({"id": r.id, "label": r.label or r.id, "legal": r.legal, "targets": targets})
    return out
