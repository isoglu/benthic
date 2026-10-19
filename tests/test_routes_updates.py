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


def run_check(plugins=None):
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, BENTHIC_DATA_DIR=tmp, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        env.pop("BENTHIC_PLUGINS", None)
        if plugins:
            paths = []
            for name, code in plugins.items():
                f = Path(tmp) / f"{name}.py"
                f.write_text(code)
                paths.append(str(f))
            env["BENTHIC_PLUGINS"] = os.pathsep.join(paths)
        res = subprocess.run([sys.executable, "-c", CHECK], env=env, capture_output=True, text=True, timeout=60)
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
        self.client = appmod.app.test_client()

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

    def test_version_compare(self):
        from benthic.updates import parse_version
        self.assertGreater(parse_version("1.10.0"), parse_version("1.9.3"))
        self.assertEqual(parse_version("v1.5"), (1, 5))


class UpdateDownload(unittest.TestCase):
    """Aktualisieren aus der App: laden, Prüfsumme, Herkunft (lokaler Server statt benthic.isoglu.com)."""
    PAYLOAD = b"fake dmg " * 5000

    def setUp(self):
        import hashlib
        test = self
        self.manifest = {"version": "9.0.0", "notes": ["Neu"], "size": len(self.PAYLOAD),
                         "sha256": hashlib.sha256(self.PAYLOAD).hexdigest()}

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
        self.client = appmod.app.test_client()
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
