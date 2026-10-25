"""Zusatzwege aus Plugins und Update-Prüfung (offline; Manifest von einem lokalen Server)."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from tests import ui_client

ROOT = Path(__file__).resolve().parent.parent

PLUGIN = """
from benthic import routes

class Demo(routes.Route):
    id = "demo"
    label = "Demo-Weg"

    def targets(self):
        return ["demo.example"]

    def fetch(self, identifier, emit):
        emit("try", "demo.example")
        if identifier.endswith("missing"):
            raise routes.RouteMiss("nicht gefunden")
        return routes.Hit(b"%PDF-1.4 demo", "https://demo.example/x.pdf", "demo.example")

def register(api):
    api.add_route(Demo())
"""

BROKEN = "def register(api):\n    raise RuntimeError('kaputt')\n"

CHECK = r"""
import json
from benthic import app as appmod, config, fetcher, oa
oa.resolve_candidates = lambda *a, **k: ([], [])
oa.resolve_metadata = lambda *a, **k: None
c = appmod.app.test_client()
d = lambda on: fetcher.BenthicDownloader(config.data_dir() / "papers", extra_routes=on, skip_existing=False)
PROXY = "https://proxy.example/login?url=https://doi.org/"
p = lambda on: fetcher.BenthicDownloader(config.data_dir() / "papers", extra_routes=on, skip_existing=False,
                                         proxy_prefix=PROXY)
out = {
  "routes": c.get("/api/routes").get_json()["routes"],
  "off": d(False).download_one("10.1/x")["status"],
  "on": {k: v for k, v in d(True).download_one("10.1/x").items() if k in ("status", "channel", "source", "route_label")},
  "miss": d(True).download_one("10.1/missing")["status"],
  "proxy_none": d(False).download_one("10.1234/x")["proxy_url"],
  "proxy_absent": p(False).download_one("10.1234/x")["proxy_url"],
  "proxy_miss": p(True).download_one("10.1234/missing")["proxy_url"],
  "settings": c.post("/api/settings", json={"extra_routes": True}).get_json()["extra_routes"],
}
print(json.dumps(out))
"""

# Einstellungen › Plugins: Zustände, Ordner öffnen, Neustart (Befehle abgefangen, nichts wird geöffnet oder ersetzt).
PLUGINS_CHECK = r"""
import json, os, socket, sys
from benthic import config
folder = config.data_dir() / "plugins"
(folder / "notpkg").mkdir()                      # Ordner ohne __init__.py: Fehler beim Start
from benthic import app as appmod
(folder / "later.py").write_text("def register(api): pass\n")
(folder / "_off.py").write_text("def register(api): pass\n")
(folder / "broken.py").unlink()
c = appmod.app.test_client()
plugins = c.get("/api/plugins").get_json()["plugins"]
calls = []
appmod.subprocess.run = lambda args, **k: calls.append(args)
opened = c.post("/api/plugin-folder", json={}).get_json()["folder"]
class Timer:
    def __init__(self, delay, fn): self.fn = fn
    def start(self): self.fn()
appmod.threading.Timer = Timer
appmod.os.execv = lambda path, argv: calls.append([path == sys.executable, argv == sys.orig_argv])
server = socket.socket()  # wie werkzeugs Server-Socket: vererbbar, Nummer in WERKZEUG_SERVER_FD
server.set_inheritable(True)
os.environ["WERKZEUG_SERVER_FD"] = str(server.fileno())
appmod._jobs["j"] = {"status": "running"}
busy = c.post("/api/restart", json={}).status_code
appmod._jobs["j"]["status"] = "done"
idle = c.post("/api/restart", json={}).status_code
print(json.dumps({"states": {p["name"]: p["state"] for p in plugins},
                  "notpkg": next(p["error"] for p in plugins if p["name"] == "notpkg"),
                  "folder": str(folder), "opened": opened, "calls": calls, "busy": busy, "idle": idle,
                  "port_kept": server.get_inheritable()}))
"""


def run_check(plugins=None, data_folder=False, script=CHECK):
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, BENTHIC_DATA_DIR=tmp, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        env.pop("BENTHIC_PLUGINS", None)
        if plugins:
            folder = Path(tmp) / "plugins" if data_folder else Path(tmp)
            folder.mkdir(exist_ok=True)
            paths = []
            for name, code in plugins.items():
                f = folder / f"{name}.py"
                f.write_text(code)
                paths.append(str(f))
            if not data_folder:
                env["BENTHIC_PLUGINS"] = os.pathsep.join(paths)
        res = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        return json.loads(res.stdout.strip().splitlines()[-1])


class ExtraRoutes(unittest.TestCase):
    def test_core_without_plugins(self):
        out = run_check()
        self.assertEqual(out["routes"], [])
        self.assertEqual(out["off"], "absent")
        self.assertEqual(out["on"]["status"], "absent")  # eingeschaltet, aber kein Weg angemeldet
        # Ohne freie Kopie: Link über den Proxy der Hochschule, aber nur, wenn einer eingetragen ist
        self.assertIsNone(out["proxy_none"])
        self.assertEqual(out["proxy_absent"], "https://proxy.example/login?url=https://doi.org/10.1234/x")

    def test_plugin_route_runs_only_when_switched_on(self):
        out = run_check({"demo": PLUGIN, "broken": BROKEN})  # ein defektes Plugin stört nicht
        self.assertEqual(out["routes"], [{"id": "demo", "label": "Demo-Weg", "legal": None, "targets": ["demo.example"]}])
        self.assertEqual(out["off"], "absent")
        self.assertEqual(out["on"], {"status": "ok", "channel": "demo", "source": "demo.example", "route_label": "Demo-Weg"})
        self.assertEqual(out["miss"], "error")
        self.assertEqual(out["proxy_miss"], "https://proxy.example/login?url=https://doi.org/10.1234/missing")
        self.assertTrue(out["settings"])

    def test_plugin_from_data_folder(self):
        out = run_check({"demo": PLUGIN}, data_folder=True)  # eigene Plugins im Datenordner, ohne BENTHIC_PLUGINS
        self.assertEqual([r["id"] for r in out["routes"]], ["demo"])
        self.assertEqual(out["on"]["channel"], "demo")

    def test_plugins_card(self):
        out = run_check({"demo": PLUGIN, "broken": BROKEN}, data_folder=True, script=PLUGINS_CHECK)
        self.assertEqual(out["states"], {"demo": "loaded", "broken": "removed", "notpkg": "error",
                                         "later": "new", "off": "off"})
        self.assertIn("__init__.py", out["notpkg"])
        self.assertEqual(out["opened"], out["folder"])
        self.assertEqual(out["calls"], [["open", out["folder"]], [True, True]])  # Finder, dann genau ein execv
        self.assertEqual((out["busy"], out["idle"]), (409, 200))  # nie mitten in einem Lauf
        self.assertFalse(out["port_kept"])  # sonst belegt der alte Socket den Port des neuen Backends

    def test_tools_never_use_extra_routes(self):
        src = (ROOT / "benthic" / "mcp_server.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('"extra_routes": False'), 2)  # fetch_papers und check_open_access


class Updates(unittest.TestCase):
    def setUp(self):
        manifest = {"version": "9.0.0", "url": "https://example.org/Benthic-9.0.0.dmg", "notes": ["Neu"]}

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(manifest).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name
        from benthic import app as appmod
        self.client = ui_client(appmod.app)

    def tearDown(self):
        self.srv.shutdown()
        os.environ.pop("BENTHIC_DATA_DIR", None)
        self.tmp.cleanup()

    def test_update_available_and_unconfigured(self):
        self.assertFalse(self.client.get("/api/update").get_json()["configured"])
        url = f"http://127.0.0.1:{self.srv.server_port}/release.json"
        self.client.post("/api/settings", json={"update_url": url})
        u = self.client.get("/api/update?force=1").get_json()
        self.assertTrue(u["available"])
        self.assertEqual(u["latest"], "9.0.0")
        self.assertEqual(u["notes"], ["Neu"])

    def test_own_address_only_from_source_or_official_site(self):
        """In der gebauten App zählt eine eigene Update-Adresse nur auf benthic.isoglu.com."""
        from unittest import mock
        from benthic import config
        local = f"http://127.0.0.1:{self.srv.server_port}/release.json"
        official = "https://benthic.isoglu.com/release.json"
        with mock.patch.object(config, "build_update_url", return_value="https://benthic.isoglu.com/release.json"):
            self.assertEqual(config.update_url({"update_url": local}), local)  # Entwicklung aus dem Projekt
            with mock.patch.object(config, "bundled", return_value=True):
                self.assertEqual(config.update_url({"update_url": local}), official)
                self.assertEqual(config.update_url({"update_url": "https://benthic.isoglu.com.evil.example/x"}), official)
                self.assertEqual(config.update_url({"update_url": official + "?beta"}), official + "?beta")
        self.assertFalse(config.bundled())

    def test_signed_message_is_the_same_everywhere(self):
        from benthic import updates
        msg = updates.SIGNED_FORMAT.format(version="1.2.3", sha256="ab")
        self.assertEqual(msg, "Benthic update\nversion 1.2.3\nsha256 ab\n")
        release = (ROOT / "macos" / "make_release.sh").read_text(encoding="utf-8")
        self.assertIn('f"Benthic update\\nversion {version}\\nsha256 {h.hexdigest()}\\n"', release)
        swift = (ROOT / "macos" / "BenthicApp.swift").read_text(encoding="utf-8")
        self.assertIn('"Benthic update\\nversion \\(version)\\nsha256 \\(sha256)\\n"', swift)

    def test_release_key_is_built_in(self):
        """Ohne öffentlichen Schlüssel könnte die App kein Update installieren (build_app.sh bricht dann ab)."""
        import base64
        import re
        swift = (ROOT / "macos" / "BenthicApp.swift").read_text(encoding="utf-8")
        key = re.search(r'^let releaseUpdateKey = "([^"]*)"$', swift, re.M).group(1)
        self.assertEqual(len(base64.b64decode(key, validate=True)), 32)

    def test_version_compare(self):
        from benthic.updates import parse_version
        self.assertGreater(parse_version("1.10.0"), parse_version("1.9.3"))
        self.assertEqual(parse_version("v1.5"), (1, 5))


class UpdateDownload(unittest.TestCase):
    """Aktualisieren aus der App: laden, Prüfsumme, Herkunft (lokaler Server statt benthic.isoglu.com)."""
    PAYLOAD = b"fake dmg " * 5000
    SIGNATURE = "A" * 86 + "=="  # nur die Form; echt prüft sie erst die App (BenthicApp.swift)

    def setUp(self):
        import hashlib
        test = self
        self.manifest = {"version": "9.0.0", "notes": ["Neu"], "size": len(self.PAYLOAD),
                         "sha256": hashlib.sha256(self.PAYLOAD).hexdigest(), "signature": self.SIGNATURE}

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.endswith(".dmg"):
                    body, kind = test.PAYLOAD, "application/x-apple-diskimage"
                else:
                    body, kind = json.dumps(test.manifest).encode(), "application/json"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}/"
        self.manifest["url"] = self.base + "Benthic-9.0.0.dmg"
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name
        from benthic import app as appmod, updates
        updates._set(state="idle")
        self.client = ui_client(appmod.app)
        self.client.post("/api/settings", json={"update_url": self.base + "release.json"})

    def tearDown(self):
        self.srv.shutdown()
        os.environ.pop("BENTHIC_DATA_DIR", None)
        self.tmp.cleanup()

    def wait(self):
        import time
        for _ in range(100):
            st = self.client.get("/api/update/status").get_json()
            if st.get("state") != "downloading":
                return st
            time.sleep(0.05)
        self.fail("download did not finish")

    def test_download_and_verify(self):
        self.client.post("/api/update/download")
        st = self.wait()
        self.assertEqual(st["state"], "ready", st)
        path = Path(st["path"])
        self.assertEqual(path.parent, Path(self.tmp.name) / "updates")
        self.assertEqual(path.read_bytes(), self.PAYLOAD)
        self.assertEqual(st["version"], "9.0.0")
        self.assertEqual(st["signature"], self.SIGNATURE)  # geht an die App, die sie prüft

    def test_unsigned_manifest_is_refused(self):
        for bad in (None, "", "abc", "A" * 88):
            with self.subTest(signature=bad):
                self.manifest.pop("signature", None)
                if bad is not None:
                    self.manifest["signature"] = bad
                st = self.client.post("/api/update/download").get_json()
                self.assertEqual(st["state"], "error")
                self.assertFalse((Path(self.tmp.name) / "updates" / "Benthic-9.0.0.dmg").exists())

    def test_wrong_checksum_is_discarded(self):
        self.manifest["sha256"] = "0" * 64
        self.client.post("/api/update/download")
        st = self.wait()
        self.assertEqual(st["state"], "error")
        self.assertEqual(list((Path(self.tmp.name) / "updates").iterdir()), [])

    def test_dmg_from_another_server_is_refused(self):
        self.manifest["url"] = "https://example.org/Benthic-9.0.0.dmg"
        st = self.client.post("/api/update/download").get_json()
        self.assertEqual(st["state"], "error")
        self.assertFalse((Path(self.tmp.name) / "updates" / "Benthic-9.0.0.dmg").exists())

    def test_cleanup_keeps_only_newer_versions(self):
        from benthic import updates
        d = updates.updates_dir()
        for name in ("Benthic-0.9.0.dmg", "Benthic-9.0.0.dmg.part", "Benthic-9.0.0.dmg"):
            (d / name).write_bytes(b"x")
        updates.cleanup()
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["Benthic-9.0.0.dmg"])


if __name__ == "__main__":
    unittest.main()
