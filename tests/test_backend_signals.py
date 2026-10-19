"""Ein veraltetes Backend geht auf SIGTERM sofort, auch wenn sein Elternprozess das Signal ignoriert (offline).

Die App beendet so ein Backend, das ein MCP-Server gestartet hat und das ein Update überlebt hat
(macos/BenthicApp.swift, StaleBackend).
"""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from benthic import __version__
from benthic import app as backend

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StopOnSigterm(unittest.TestCase):
    def test_start_background_installs_the_handler(self):
        with mock.patch.object(backend, "start_parent_watchdog"), \
                mock.patch.object(backend.watch, "start_scheduler"), \
                mock.patch.object(backend.watchfile, "start"), \
                mock.patch.object(backend.signal, "signal") as sig:
            backend.start_background()
        sig.assert_called_once()
        self.assertEqual(sig.call_args.args[0], signal.SIGTERM)

    def test_only_from_the_main_thread(self):
        with mock.patch.object(backend.signal, "signal") as sig:
            t = threading.Thread(target=backend.stop_on_sigterm)
            t.start()
            t.join()
        sig.assert_not_called()

    def test_backend_exits_on_sigterm_even_if_ignored(self):
        """Startet ein Backend wie der MCP-Server, mit geerbtem SIG_IGN für SIGTERM, und beendet es."""
        port = free_port()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = dict(os.environ, BENTHIC_DATA_DIR=tmp.name, BENTHIC_WATCH="0", PYTHONPATH=str(ROOT),
                   PYTHONDONTWRITEBYTECODE="1")
        env.pop("BENTHIC_PARENT_PID", None)
        code = ("from benthic.app import app, stop_on_sigterm; stop_on_sigterm(); "
                f"app.run(host='127.0.0.1', port={port}, threaded=True)")
        proc = subprocess.Popen([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                preexec_fn=lambda: signal.signal(signal.SIGTERM, signal.SIG_IGN))
        self.addCleanup(lambda: proc.poll() is None and (proc.kill(), proc.wait()))

        health = None
        deadline = time.monotonic() + 30
        while health is None and time.monotonic() < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as resp:
                    health = json.loads(resp.read())
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)
        self.assertEqual(health, {"app": "Benthic", "version": __version__, "ok": True})

        started = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=5), 0)
        self.assertLess(time.monotonic() - started, 3)


if __name__ == "__main__":
    unittest.main()
