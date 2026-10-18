"""Benthic.mcpb zum Release (scripts/make_mcpb.py): auf jedem Mac gültig, mit stabiler SHA-256."""

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from benthic import __version__

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "make_mcpb.py"
# Absolute Pfade in ein Benutzerverzeichnis, auf ein Laufwerk oder in einen Temp-Ordner
HOME_PATH = re.compile(r"/(?:Users|Volumes|home|private|var/folders|tmp)/")
# Erlaubte Schlüssel von manifest.json, Spezifikation 0.3 (das Schema lehnt alle anderen ab)
MCPB_KEYS = {"$schema", "dxt_version", "manifest_version", "name", "display_name", "version", "description",
             "long_description", "author", "repository", "homepage", "documentation", "support", "icon", "icons",
             "screenshots", "localization", "server", "tools", "tools_generated", "prompts", "prompts_generated",
             "keywords", "license", "privacy_policies", "compatibility", "user_config", "_meta"}


def plugins():
    spec = importlib.util.spec_from_file_location("make_plugins", ROOT / "scripts" / "make_plugins.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseBundle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        # Gebaut wie aus einer deutschen Installation mit eigenem Datenordner und Port: davon darf
        # nichts im Paket landen.
        data = self.dir / "data"
        data.mkdir()
        (data / "settings.json").write_text(json.dumps({"language": "de"}), encoding="utf-8")
        self.leaky = {"BENTHIC_DATA_DIR": str(data), "BENTHIC_PORT": "8399",
                      "BENTHIC_APP": str(self.dir / "Benthic.app")}
        self.out, self.stdout = self.build("a", **self.leaky)
        self.zip = zipfile.ZipFile(self.out)
        self.manifest_text = self.zip.read("manifest.json").decode("utf-8")
        self.manifest = json.loads(self.manifest_text)
        self.launcher = self.zip.read("server/benthic-mcp.sh").decode("utf-8")

    def tearDown(self):
        self.zip.close()
        self.tmp.cleanup()

    def build(self, name, **env):
        out = self.dir / name / "Benthic.mcpb"
        run = subprocess.run([sys.executable, str(SCRIPT), "--out", str(out)], capture_output=True, text=True,
                             env=dict(os.environ, **env), cwd=self.dir)
        self.assertEqual(run.returncode, 0, run.stderr)
        return out, run.stdout

    def test_no_environment_and_no_absolute_home_path(self):
        self.assertEqual(self.manifest["server"]["mcp_config"],
                         {"command": "/bin/sh", "args": ["${__dirname}/server/benthic-mcp.sh"]})
        self.assertNotIn('"env"', self.manifest_text)
        for text in (self.manifest_text, self.launcher):
            self.assertNotRegex(text, HOME_PATH)
            for leak in [str(Path.home()), str(self.dir), "PYTHONPATH", "BENTHIC_", "8399"]:
                self.assertNotIn(leak, text)

    def test_public_texts_in_english(self):
        from benthic.mcp_server import TOOLS
        m = self.manifest
        self.assertTrue(set(m) <= MCPB_KEYS, set(m) - MCPB_KEYS)
        self.assertEqual(m["manifest_version"], "0.3")
        self.assertEqual(m["version"], __version__)
        self.assertEqual(m["author"]["name"], "Sinan Isoglu")
        self.assertEqual(m["license"], "AGPL-3.0-only")
        self.assertEqual(len(m["tools"]), len(TOOLS))
        self.assertNotRegex(self.manifest_text, "[äöüÄÖÜß„“]")  # gebaut mit deutscher Oberfläche
        self.assertIn("/download/", m["long_description"])  # sagt, dass die App nötig ist

    def test_launcher_starts_the_fixed_command_of_the_app(self):
        self.assertIn(plugins().FINDER, self.launcher)  # dieselbe Suche wie die Plugins
        self.assertIn('exec "$a/Contents/Resources/bin/benthic-mcp"', self.launcher)
        self.assertEqual(subprocess.run(["/bin/sh", "-n", "-c", self.launcher]).returncode, 0)
        mode = self.zip.getinfo("server/benthic-mcp.sh").external_attr >> 16
        self.assertEqual(mode & 0o777, 0o755)

    def test_same_bytes_from_any_build(self):
        other, _ = self.build("b", BENTHIC_DATA_DIR=str(self.dir / "elsewhere"), BENTHIC_PORT="8400")
        self.assertEqual(self.out.read_bytes(), other.read_bytes())
        self.assertEqual(self.zip.namelist(), ["manifest.json", "server/benthic-mcp.sh", "icon.png"])
        for info in self.zip.infolist():
            self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
            self.assertEqual(info.compress_type, zipfile.ZIP_STORED)

    def test_registry_package_block(self):
        block = json.loads(self.stdout[self.stdout.index("{"):])
        package = block["packages"][0]
        self.assertEqual(package["registryType"], "mcpb")
        self.assertEqual(package["identifier"],
                         f"https://github.com/isoglu/benthic/releases/download/v{__version__}/Benthic.mcpb")
        self.assertEqual(package["fileSha256"], hashlib.sha256(self.out.read_bytes()).hexdigest())
        self.assertRegex(package["fileSha256"], "^[a-f0-9]{64}$")
        self.assertEqual(package["transport"], {"type": "stdio"})


if __name__ == "__main__":
    unittest.main()
