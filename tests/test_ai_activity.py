"""KI-Werkzeuge über MCP: jeder Aufruf erscheint in der App und in Grimpo, mit Stand, Dauer und Textmenge (offline)."""

import inspect
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import ui_client


class AiActivity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()
        from benthic import app as appmod
        self.appmod = appmod
        self.client = ui_client(appmod.app)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def post(self, client, **body):
        return self.client.post("/api/activity", json=body, headers={"X-Benthic-Client": client})

    def events(self):
        return self.client.get("/api/activity").get_json()["events"]

    def test_call_from_start_to_end(self):
        from benthic import activity
        self.post("mcp:claude-ai", id="7-1", tool="search_library", status="running")
        self.post("mcp:claude-ai", id="7-1", tool="search_library", summary="durchsucht deine Bibliothek", status="running")
        e = self.events()
        self.assertEqual([(x["name"], x["status"], x["summary"]) for x in e], [("Claude", "running", "durchsucht deine Bibliothek")])
        self.client.get("/api/health", headers={"X-Benthic-Client": "zotero"})
        live = self.client.get("/api/live").get_json()
        self.assertEqual(live["calls"][0]["status"], "running")                 # Grimpo sieht den laufenden Aufruf
        self.assertEqual([c["name"] for c in live["clients"]], ["Claude"])      # Zotero ist kein KI-Werkzeug
        self.post("mcp:claude-ai", id="7-1", tool="search_library", status="done", ms=120, chars=900)
        e = self.events()
        self.assertEqual([(x["status"], x["ms"], x["chars"]) for x in e], [("done", 120, 900)])  # ein Eintrag je Aufruf
        self.assertTrue((Path(self.tmp.name) / "ai-activity.json").exists())     # übersteht einen Neustart
        self.post("mcp", tool="verify_references", summary="prüft eine Literaturliste")  # älterer MCP-Server
        self.assertEqual((self.events()[0]["status"], self.events()[0]["name"]), (None, activity.name("mcp")))
        self.assertEqual(activity.name("mcp:client"), activity.name("mcp"))     # ohne Namen nie „Claude“ geraten
        self.assertEqual(activity.name("mcp:cursor-vscode"), "Cursor")
        self.post("mcp:claude-ai", id="9-1", tool="get_job", status="running")
        with mock.patch("time.time", return_value=time.time() + activity.STALE + 5):
            self.assertEqual(activity.recent(1)[0]["status"], "gone")           # MCP-Prozess weg: nicht ewig „läuft“

    def test_runs_started_by_ai_are_marked(self):
        from benthic import history
        history.append({"id": "5", "mode": "verify", "origin": "mcp:claude-ai"})
        history.append({"id": "6", "mode": "verify", "origin": "ui"})
        rows = self.client.get("/api/history").get_json()["summaries"]
        self.assertEqual({r["id"]: r["origin_name"] for r in rows}, {"5": "Claude", "6": None})
        job = {"id": "j1", "mode": "verify", "status": "running", "origin": "mcp:claude-ai", "total": 2, "done": 0, "active": {}}
        with mock.patch.dict(self.appmod._jobs, {"j1": job}):
            self.assertEqual(self.client.get("/api/jobs").get_json()["jobs"][0]["origin_name"], "Claude")
            self.assertEqual(self.client.get("/api/live").get_json()["jobs"][0]["who"], "Claude")

    def test_every_mcp_call_is_reported(self):
        from benthic import mcp_server
        posted = []

        def backend(method, path, body=None, timeout=30.0):
            if path == "/api/activity":
                posted.append(body)
                return 200, {"ok": True}
            return (200, {"projects": [], "active": None}) if path == "/api/projects" else (404, {"error": "nope"})

        with mock.patch.object(mcp_server, "_http", side_effect=backend), \
                mock.patch.object(mcp_server, "ensure_backend", return_value={}), mock.patch.object(mcp_server, "_send"):
            mcp_server._call_tool(1, {"name": "list_projects", "arguments": {}})
            mcp_server._call_tool(2, {"name": "project_status", "arguments": {"project": "x"}})
        first, second = posted[:3], posted[3:]
        self.assertEqual([p["status"] for p in first], ["running", "running", "done"])  # Start, was es tut, Ende
        self.assertEqual(len({p["id"] for p in first}), 1)
        self.assertTrue(first[1]["summary"])
        self.assertIsInstance(first[2]["chars"], int)
        self.assertEqual(second[-1]["status"], "error")
        self.assertNotEqual(first[0]["id"], second[0]["id"])

    def test_every_tool_says_what_it_does(self):
        from benthic import mcp_server
        silent = [t["name"] for t in mcp_server.TOOLS if "_activity(" not in inspect.getsource(t["handler"])]
        self.assertEqual(silent, [])


if __name__ == "__main__":
    unittest.main()
