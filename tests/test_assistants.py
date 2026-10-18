"""Einrichtung für KI-Assistenten: Startdatei und Anleitungen (offline)."""

import os
import shlex
import stat
import subprocess
import tempfile
import unittest


class Assistenten(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name
        from benthic import app as appmod
        self.client = appmod.app.test_client()

    def tearDown(self):
        os.environ.pop("BENTHIC_DATA_DIR", None)
        self.tmp.cleanup()

    def test_launcher_is_executable_and_valid_shell(self):
        from benthic import connect
        path = connect.write_launcher()
        self.assertTrue(path.exists())
        self.assertTrue(path.stat().st_mode & stat.S_IXUSR)
        text = path.read_text(encoding="utf-8")
        self.assertIn(f"export BENTHIC_DATA_DIR={shlex.quote(self.tmp.name)}", text)
        self.assertIn("-m benthic.mcp_server", text)
        self.assertEqual(subprocess.run(["sh", "-n", str(path)]).returncode, 0)

    def test_api_lists_every_assistant_with_steps(self):
        j = self.client.get("/api/mcp").get_json()
        ids = [a["id"] for a in j["assistants"]]
        for want in ("claude-desktop", "claude-code", "codex", "gemini-cli", "antigravity", "grok-build",
                     "cursor", "vscode", "lmstudio", "chat-apps"):
            self.assertIn(want, ids)
        for a in j["assistants"]:
            self.assertTrue(a["steps"], a["id"])
            if a["kind"] in ("command", "file"):
                self.assertIn(j["launcher"], a["snippet"], a["id"])
        codex = next(a for a in j["assistants"] if a["id"] == "codex")
        self.assertTrue(codex["snippet"].startswith("codex mcp add benthic -- "))
        self.assertIn("[mcp_servers.benthic]", codex["alt_snippet"])


if __name__ == "__main__":
    unittest.main()
