"""Sprache der Oberfläche: Katalog vollständig, englische Seite ohne deutsche Reste (offline)."""

import ast
import html
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from benthic import i18n

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "benthic"
CATALOG = json.loads((PKG / "locales" / "en.json").read_text(encoding="utf-8"))

# Deutsch erkennen: Umlaute oder häufige Wörter (in englischen Texten kommen sie nicht vor).
GERMAN = re.compile(r"[äöüÄÖÜß]|\b(und|der|die|das|nicht|für|mit|keine?|ist|wird|oder|Quellen?|Datei|"
                    r"Fehler|geladen|gefunden|noch|über|Prüf\w*|Läufe|Bibliothek|Projekte?|Zusatzwege?|"
                    r"Einstellungen|Einrichtung|Speichern|Zurück|Weiter|Abbrechen|Laden)\b")
# Sprachnamen stehen in ihrer eigenen Sprache.
OWN_LANGUAGE = {"Deutsch", "Oberfläche auf Deutsch"}


def placeholders(text):
    return set(re.findall(r"\{(\w+)\}", text))


def python_msgids():
    """Vorlagen aller tr()- und note_part()-Aufrufe (erstes Argument) und style._note() (zweites) mit festem
    Text, auch a if c else b und Nachschlagen in einer Tabelle auf Modulebene wie _CONFLICT[field]."""
    out = []
    for f in sorted(PKG.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        tables = {t.id: [v.value for v in node.value.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
                  for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                  for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(tree):
            func = getattr(node, "func", None)
            name = getattr(func, "id", None) or getattr(func, "attr", None)  # tr(…) oder i18n.note_part(…)
            at = {"tr": 0, "_tr": 0, "note_part": 0, "_note": 1}.get(name) if isinstance(node, ast.Call) else None
            if at is None or len(node.args) <= at:
                continue
            arg = node.args[at]
            cands = [arg.body, arg.orelse] if isinstance(arg, ast.IfExp) else [arg]
            out += [(f.name, c.value) for c in cands if isinstance(c, ast.Constant) and isinstance(c.value, str)]
            if isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name):
                out += [(f.name, s) for s in tables.get(arg.value.id, [])]
    return out


def js_msgids():
    """Texte in T("…") und TN(n, "…", "…") aus app.js und Grimpo (pet.html)."""
    src = "".join((PKG / "static" / f).read_text(encoding="utf-8") for f in ("app.js", "pet.html"))
    lit = r'"((?:[^"\\]|\\.)*)"'
    out = re.findall(r"(?<!function )\bT\(\s*" + lit, src)
    for one, other in re.findall(r"(?<!function )\bTN\([^\"\n]*?" + lit + r",\s*" + lit, src):
        out += [one, other]
    return [json.loads('"' + s + '"') for s in out]


def html_texts(page):
    body = i18n._SKIP.sub("", page)
    texts = [html.unescape(m.group(1)).strip() for m in i18n._TEXT.finditer(body)]
    texts += [html.unescape(m.group(2)).strip() for m in i18n._ATTR.finditer(body)]
    return [t for t in texts if t]


class Katalog(unittest.TestCase):
    def test_entries_are_clean(self):
        for de, en in CATALOG.items():
            self.assertTrue(en.strip(), de)
            self.assertNotIn("—", en, de)                      # keine Geviertstriche
            self.assertEqual(placeholders(de), placeholders(en), de)  # gleiche Platzhalter

    def test_every_python_text_is_translated(self):
        missing = [f"{f}: {t}" for f, t in python_msgids() if t not in CATALOG]
        self.assertEqual(missing, [])

    def test_every_interface_text_is_translated(self):
        ids = js_msgids()
        self.assertGreater(len(ids), 300)
        self.assertEqual([t for t in ids if t not in CATALOG and re.search(r"[A-Za-zÄÖÜäöü]", t)], [])

    def test_english_page_has_no_german_left(self):  # noqa: D102
        page = (PKG / "static" / "index.html").read_text(encoding="utf-8")
        english = i18n.translate_html(page, "en")
        self.assertIn('<html lang="en"', english)
        names = ("FH Münster",)  # Eigennamen mit Umlaut sind kein deutscher Rest
        left = [t for t in html_texts(english)
                if GERMAN.search(_strip(t, names)) and t not in OWN_LANGUAGE]
        self.assertEqual(left, [])

    def test_german_page_stays_as_written(self):
        page = (PKG / "static" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(i18n.translate_html(page, "de"), page)

    def test_translation_keeps_markup_and_escapes(self):
        page = '<html lang="de"><p title="Projekt">Projekte</p><input placeholder="Hier einfügen, zum Beispiel die Literaturliste aus ChatGPT oder Claude.&#10;&#10;Oder eine .bib-, .ris-, .txt- oder .md-Datei hierherziehen."><script>var x = "Projekte";</script>'
        out = i18n.translate_html(page, "en")
        self.assertIn('<p title="Project">Projects</p>', out)
        self.assertIn("ChatGPT or Claude.&#10;&#10;Or drag", out)
        self.assertIn('var x = "Projekte";', out)  # Skripte bleiben unberührt

    def test_script_payload(self):
        js = i18n.script("en")
        self.assertTrue(js.startswith('window.PD_LANG="en";window.PD_LOCALE="en-GB";'))
        self.assertIn('"Projekte":"Projects"', js)
        self.assertIn("window.PD_I18N={};", i18n.script("de"))


class Einstellung(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("BENTHIC_DATA_DIR")
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("BENTHIC_DATA_DIR", None)
        else:
            os.environ["BENTHIC_DATA_DIR"] = self._old
        self.tmp.cleanup()

    def test_new_install_is_english(self):
        from benthic import config
        self.assertEqual(config.load_settings()["language"], "en")
        self.assertEqual(i18n.current(), "en")
        self.assertEqual(i18n.tr("Projekte"), "Projects")

    def test_existing_install_keeps_german(self):
        from benthic import config
        Path(self.tmp.name, "settings.json").write_text(json.dumps({"settings_version": 3, "setup_complete": True}))
        self.assertEqual(config.load_settings()["language"], "de")
        self.assertEqual(i18n.tr("Projekte"), "Projekte")

    def test_switch_and_unknown_values(self):
        from benthic import config
        config.save_settings({"language": "de"})
        self.assertEqual(i18n.tr("Projekt „{name}“ nicht gefunden", name="X"), "Projekt „X“ nicht gefunden")
        config.save_settings({"language": "fr"})
        self.assertEqual(config.load_settings()["language"], "en")
        self.assertEqual(i18n.tr("Projekt „{name}“ nicht gefunden", name="X"), "Project “X” not found")

    def test_pages_follow_the_setting(self):
        from benthic import app as appmod, config
        client = appmod.app.test_client()
        self.assertIn('<html lang="en"', client.get("/").get_data(as_text=True))
        config.save_settings({"language": "de"})
        page = client.get("/").get_data(as_text=True)
        self.assertIn('<html lang="de"', page)
        self.assertIn("Quellen prüfen", page)
        self.assertIn('window.PD_LANG="de"', client.get("/i18n.js").get_data(as_text=True))


class Konfigurationsdateien(unittest.TestCase):
    """Einträge für ChatGPT/Codex und Antigravity; nur in einem Wegwerf-Home."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {k: os.environ.get(k) for k in ("HOME", "CODEX_HOME", "BENTHIC_DATA_DIR")}
        os.environ["HOME"] = self.tmp.name
        os.environ.pop("CODEX_HOME", None)
        os.environ["BENTHIC_DATA_DIR"] = str(Path(self.tmp.name, "data"))
        from benthic import connect
        self.connect = connect
        self.launcher = Path(self.tmp.name, "data", "benthic-mcp.sh")

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_codex_toml_keeps_other_servers_and_replaces_ours(self):
        cfg = Path(self.tmp.name, ".codex", "config.toml")
        cfg.parent.mkdir()
        cfg.write_text('model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "other"\n\n'
                       '[mcp_servers.benthic]\ncommand = "/old/path"\n\n[mcp_servers.benthic.env]\nA = "1"\n\n'
                       '[profiles.x]\nmodel = "y"\n')
        self.assertEqual(self.connect.entry_state("chatgpt", self.launcher), "stale")
        done = self.connect.install_entry("chatgpt", self.launcher)
        import tomllib
        data = tomllib.loads(cfg.read_text())
        self.assertEqual(data["mcp_servers"]["benthic"], {"command": str(self.launcher)})
        self.assertEqual(data["mcp_servers"]["other"]["command"], "other")
        self.assertEqual(data["profiles"]["x"]["model"], "y")
        self.assertEqual(data["model"], "gpt-5")
        self.assertTrue(Path(self.tmp.name, ".codex", "config.toml.benthic-backup").is_file())
        self.assertEqual(done["path"], "~/.codex/config.toml")
        self.assertEqual(self.connect.entry_state("chatgpt", self.launcher), "current")

    def test_codex_toml_created_when_missing(self):
        self.assertEqual(self.connect.entry_state("chatgpt", self.launcher), "missing")
        self.connect.install_entry("chatgpt", self.launcher)
        text = Path(self.tmp.name, ".codex", "config.toml").read_text()
        self.assertTrue(text.startswith("[mcp_servers.benthic]\n"))

    def test_antigravity_json(self):
        cfg = Path(self.tmp.name, ".gemini", "config", "mcp_config.json")
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"mcpServers": {"x": {"command": "x"}, "benthic": {"command": "/old", "disabled": True}}}))
        self.connect.install_entry("antigravity", self.launcher)
        data = json.loads(cfg.read_text())
        self.assertEqual(data["mcpServers"]["x"], {"command": "x"})
        self.assertEqual(data["mcpServers"]["benthic"], {"command": str(self.launcher)})

    def test_unreadable_file_is_left_alone(self):
        cfg = Path(self.tmp.name, ".gemini", "config", "mcp_config.json")
        cfg.parent.mkdir(parents=True)
        cfg.write_text('{ // Kommentar\n "mcpServers": {} }')
        with self.assertRaises(ValueError):
            self.connect.install_entry("antigravity", self.launcher)
        self.assertIn("Kommentar", cfg.read_text())
        self.assertEqual(self.connect.entry_state("antigravity", self.launcher), "unreadable")

    def test_api_installs_known_targets_only(self):
        from benthic import app as appmod
        client = appmod.app.test_client()
        self.assertEqual(client.post("/api/mcp/install", json={"id": "cursor"}).status_code, 400)
        r = client.post("/api/mcp/install", json={"id": "antigravity"}).get_json()
        self.assertEqual(r["state"], "current")
        self.assertEqual(r["path"], "~/.gemini/config/mcp_config.json")


if __name__ == "__main__":
    unittest.main()


def _strip(text, names):
    for n in names:
        text = text.replace(n, "")
    return text
