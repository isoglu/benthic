"""Projekte und MCP-Protokoll (offline, temporäres Datenverzeichnis, eigener Port)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Projects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name
        from benthic import app as appmod
        self.client = appmod.app.test_client()

    def tearDown(self):
        os.environ.pop("BENTHIC_DATA_DIR", None)
        self.tmp.cleanup()

    def test_lifecycle_and_results(self):
        from benthic import projects
        r = self.client.post("/api/projects", json={"name": "Referat KI", "description": "Test"})
        self.assertEqual(r.status_code, 200)
        pid = r.get_json()["id"]
        self.assertEqual(self.client.get("/api/projects").get_json()["active"], pid)
        self.assertEqual(self.client.post("/api/projects", json={"name": "referat ki"}).status_code, 400)
        projects.record_results(pid, "verify", [
            {"doi": "10.1038/nature14539", "verdict": "corrected", "found": {"title": "Deep learning", "year": "2015"}},
            {"doi": None, "verdict": "not_found", "claimed": {"title": "Invented"}},
            {"doi": "10.1126/science.1065467", "verdict": "mismatch", "found": {"title": "Other"}},
        ])
        d = self.client.get("/api/projects/Referat%20KI").get_json()  # per Name auffindbar
        self.assertEqual(d["count"], 2)
        self.assertEqual(d["verified"], 1)
        self.assertEqual(d["flagged"], 1)
        self.client.post(f"/api/projects/{pid}/items", json={"text": "see https://doi.org/10.1038/s41586-020-2649-2"})
        self.assertEqual(self.client.get(f"/api/projects/{pid}").get_json()["count"], 3)
        self.client.delete(f"/api/projects/{pid}/items/10.1126/science.1065467")
        self.assertEqual(self.client.get(f"/api/projects/{pid}").get_json()["count"], 2)
        self.assertTrue(self.client.delete(f"/api/projects/{pid}").get_json()["deleted"])
        self.assertIsNone(self.client.get("/api/projects").get_json()["active"])

    def test_unknown_project_is_rejected_for_jobs(self):
        r = self.client.post("/api/verify", json={"text": "10.1038/nature14539", "project": "nope"})
        self.assertEqual(r.status_code, 404)

    def test_bundle_manifest(self):
        from benthic import connect
        b = connect.build_bundle(Path(self.tmp.name) / "Benthic.mcpb")
        z = zipfile.ZipFile(b)
        self.assertEqual(sorted(z.namelist()), ["icon.png", "manifest.json", "server/benthic-mcp.sh"])
        m = json.loads(z.read("manifest.json"))
        allowed = {"manifest_version", "name", "display_name", "version", "description", "long_description",
                   "author", "icon", "server", "tools", "keywords", "compatibility"}
        self.assertTrue(set(m) <= allowed, set(m) - allowed)
        self.assertEqual(m["manifest_version"], "0.3")
        self.assertEqual(m["server"]["type"], "binary")
        self.assertEqual(set(m["server"]), {"type", "entry_point", "mcp_config"})
        self.assertEqual(len(m["tools"]), len(__import__("benthic.mcp_server", fromlist=["TOOLS"]).TOOLS))


class McpProtocol(unittest.TestCase):
    PORT = "8335"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, "settings.json").write_text(json.dumps({
            "setup_complete": True, "extra_routes": True, "download_dir": str(Path(self.tmp.name) / "papers")}))
        env = dict(os.environ, BENTHIC_PORT=self.PORT, BENTHIC_DATA_DIR=self.tmp.name, PYTHONPATH=str(ROOT))
        self.p = subprocess.Popen([sys.executable, "-m", "benthic.mcp_server"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env, cwd=tempfile.gettempdir())
        self.mid = 0

    def tearDown(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)
        self.tmp.cleanup()

    def rpc(self, method, params=None):
        self.mid += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.mid, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        while True:
            msg = json.loads(self.p.stdout.readline())
            if msg.get("id") == self.mid:
                return msg

    def test_handshake_tools_and_projects(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "unittest", "version": "1"}})["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertIn("tools", init["capabilities"])
        names = [t["name"] for t in self.rpc("tools/list")["result"]["tools"]]
        self.assertIn("verify_references", names)
        self.assertIn("fetch_papers", names)
        r = self.rpc("tools/call", {"name": "create_project", "arguments": {"name": "MCP Test"}})["result"]
        self.assertFalse(r["isError"], r)
        r = self.rpc("tools/call", {"name": "list_projects", "arguments": {}})["result"]
        self.assertIn("MCP Test", r["content"][0]["text"])
        r = self.rpc("tools/call", {"name": "fetch_papers", "arguments": {"dois": []}})["result"]
        self.assertTrue(r["isError"])
        self.assertEqual(self.rpc("resources/list")["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
