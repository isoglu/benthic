"""MCP-Server nach einem Update: Hinweis, wenn Prozess, Dateien und Backend verschiedene Versionen haben (offline)."""

import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from benthic import __version__, mcp_server


def fake_http(backend_version):
    """_http ohne Netz: /api/health meldet die gegebene Version, alles andere ist ein leeres OK."""
    def http(method, path, body=None, timeout=30.0):
        if method == "GET" and path == "/api/health":
            return 200, {"app": "Benthic", "version": backend_version, "ok": True}
        return 200, {}
    return http


class VersionGuard(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.tmp = tempfile.TemporaryDirectory()
        for p in (mock.patch.object(mcp_server, "_result", lambda mid, result: self.sent.append(result)),
                  mock.patch.object(mcp_server, "_noticed", set()),
                  mock.patch.object(mcp_server, "log"),
                  mock.patch.object(mcp_server.subprocess, "Popen", side_effect=AssertionError("kein Backend starten")),
                  mock.patch.dict(mcp_server._HANDLERS, {"probe": lambda a: "probe result"})):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def installed(self, version):
        """Simuliert die Dateien auf der Platte: ein __init__.py mit dieser Version."""
        init = Path(self.tmp.name) / "__init__.py"
        init.write_text(f'"""Benthic."""\n\n__version__ = "{version}"\n__app_name__ = "Benthic"\n', encoding="utf-8")
        p = mock.patch.object(mcp_server, "_INIT", init)
        p.start()
        self.addCleanup(p.stop)

    def call(self, name="probe", backend=__version__):
        with mock.patch.object(mcp_server, "_http", side_effect=fake_http(backend)):
            mcp_server._call_tool(1, {"name": name, "arguments": {}})
        self.assertEqual(len(self.sent), 1)
        result = self.sent.pop()
        return result["content"][0]["text"], result["isError"]

    def test_real_init_matches_running_version(self):
        self.assertEqual(mcp_server._disk_version(), __version__)

    def test_disk_version_unreadable(self):
        with mock.patch.object(mcp_server, "_INIT", Path(self.tmp.name) / "fehlt.py"):
            self.assertIsNone(mcp_server._disk_version())
        broken = Path(self.tmp.name) / "kaputt.py"
        broken.write_bytes(b"\xff\xfe kein Python")
        with mock.patch.object(mcp_server, "_INIT", broken):
            self.assertIsNone(mcp_server._disk_version())

    def test_ensure_backend_returns_health_without_starting_one(self):
        with mock.patch.object(mcp_server, "_http", side_effect=fake_http("1.0.1")):
            self.assertEqual(mcp_server.ensure_backend()["version"], "1.0.1")

    def test_no_notice_when_versions_match(self):
        self.installed(__version__)
        self.assertEqual(self.call(), ("probe result", False))
        mcp_server.log.assert_not_called()

    def test_update_while_client_runs(self):
        """Der Fall vom 19.10.2026: MCP 1.0.0 seit dem Morgen, App und Backend seit Mittag 1.0.1."""
        self.installed("1.0.1")
        with mock.patch.object(mcp_server, "__version__", "1.0.0"):
            text, failed = self.call(backend="1.0.1")
        notice, rest = text.split("\n\n", 1)
        self.assertFalse(failed)
        self.assertEqual(rest, "probe result")
        self.assertNotIn("\n", notice)
        self.assertIn("updated to 1.0.1", notice)
        self.assertIn("still runs 1.0.0", notice)
        self.assertIn("Quit and reopen the app you are chatting in", notice)

    def test_updated_files_win_over_an_old_backend(self):
        """Auch ein noch altes Backend ändert nichts daran: dieser Prozess muss neu starten."""
        self.installed("1.0.1")
        with mock.patch.object(mcp_server, "__version__", "1.0.0"):
            text, _ = self.call(backend="1.0.0")
        self.assertIn("updated to 1.0.1", text.split("\n")[0])

    def test_backend_on_another_version(self):
        self.installed(__version__)
        text, failed = self.call(backend="9.9.9")
        notice = text.split("\n\n", 1)[0]
        self.assertFalse(failed)
        self.assertNotIn("\n", notice)
        self.assertIn("runs version 9.9.9", notice)
        self.assertIn(f"this connection runs {__version__}", notice)
        self.assertIn("Quit and reopen Benthic", notice)

    def test_notice_also_on_tool_errors(self):
        self.installed("1.0.1")

        def broken(_a):
            raise ValueError("unknown job_id")

        with mock.patch.dict(mcp_server._HANDLERS, {"probe": broken}), \
                mock.patch.object(mcp_server, "__version__", "1.0.0"):
            text, failed = self.call(backend="1.0.1")
        self.assertTrue(failed)
        self.assertIn("updated to 1.0.1", text)
        self.assertTrue(text.endswith("Error: unknown job_id"))

    def test_notice_when_backend_is_unreachable(self):
        """Startet kein Backend, verraten die Dateien trotzdem das Update."""
        self.installed("1.0.1")
        with mock.patch.object(mcp_server, "ensure_backend", side_effect=RuntimeError("did not start")), \
                mock.patch.object(mcp_server, "__version__", "1.0.0"):
            mcp_server._call_tool(1, {"name": "probe", "arguments": {}})
        result = self.sent.pop()
        self.assertTrue(result["isError"])
        self.assertIn("updated to 1.0.1", result["content"][0]["text"])

    def test_health_errors_mean_no_backend(self):
        with mock.patch.object(mcp_server, "_http", side_effect=urllib.error.URLError("refused")):
            self.assertIsNone(mcp_server._health())
        with mock.patch.object(mcp_server, "_http", return_value=(200, "<html>")):
            self.assertIsNone(mcp_server._health())
        with mock.patch.object(mcp_server, "_http", return_value=(200, {"ok": False, "version": "1.0.1"})):
            self.assertIsNone(mcp_server._health())

    def test_logged_once_per_mismatch(self):
        self.installed("1.0.1")
        with mock.patch.object(mcp_server, "__version__", "1.0.0"):
            self.call(backend="1.0.1")
            self.call(backend="1.0.1")
        self.assertEqual(mcp_server.log.call_count, 1)
        self.assertIn("MCP 1.0.0, Dateien 1.0.1, Backend 1.0.1", mcp_server.log.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
