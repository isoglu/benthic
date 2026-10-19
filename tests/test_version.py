"""Eine Versionsnummer überall: Paket, Info.plist und beide Changelogs."""
import plistlib
import re
import unittest
from pathlib import Path

from benthic import __version__

ROOT = Path(__file__).resolve().parents[1]


def latest(changelog: str) -> str:
    match = re.search(r"^## (\S+)", (ROOT / changelog).read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else ""


class Version(unittest.TestCase):
    def test_one_version_everywhere(self):
        with open(ROOT / "macos" / "Info.plist", "rb") as f:
            plist = plistlib.load(f)
        self.assertEqual(plist["CFBundleShortVersionString"], __version__)
        self.assertEqual(plist["CFBundleVersion"], __version__)
        self.assertEqual(latest("CHANGELOG.md"), __version__)
        self.assertEqual(latest("CHANGELOG.de.md"), __version__)

    def test_changelogs_list_the_same_versions(self):
        versions = lambda name: re.findall(r"^## (\S+)", (ROOT / name).read_text(encoding="utf-8"), re.M)
        self.assertEqual(versions("CHANGELOG.md"), versions("CHANGELOG.de.md"))

    def test_plugin_files_are_current(self):
        """Plugins für Claude Code, Codex, VS Code, Gemini CLI und die MCP-Registry: aus scripts/make_plugins.py, gleiche Version."""
        import json
        import subprocess
        import sys
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_plugins.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        self.assertEqual(server["version"], __version__)
        self.assertLessEqual(len(server["description"]), 100)  # Grenze der Registry

    def test_static_files_carry_the_version(self):
        html = (ROOT / "benthic" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r"\?v=([0-9.]+)", html)), {__version__})


    def test_zotero_plugin(self):
        """Zotero-Plug-in: gleiche Version, Zotero 7 und 8, alle Einstiegspunkte (scripts/make_zotero_plugin.py)."""
        import json
        import subprocess
        import sys
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_zotero_plugin.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        m = json.loads((ROOT / "zotero" / "manifest.json").read_text(encoding="utf-8"))
        z = m["applications"]["zotero"]
        self.assertEqual((m["manifest_version"], m["version"], z["id"]), (2, __version__, "benthic@isoglu.com"))
        self.assertEqual(z["update_url"], "https://benthic.isoglu.com/zotero/updates.json")
        self.assertTrue(z["strict_min_version"].startswith("6.999"))
        for icon in m["icons"].values():
            self.assertTrue((ROOT / "zotero" / icon).is_file())
        js = (ROOT / "zotero" / "bootstrap.js").read_text(encoding="utf-8")
        for fn in ("startup", "shutdown", "install", "uninstall", "onMainWindowLoad", "onMainWindowUnload"):
            self.assertRegex(js, rf"(?m)^function {fn}\(", fn)
        self.assertIn("127.0.0.1:8327", js)
        self.assertNotIn("\u2014", js)


if __name__ == "__main__":
    unittest.main()
