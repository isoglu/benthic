"""Hausregeln einer Hochschule: Datei laden, nur erlaubte Angaben übernehmen, Zitierstil in der Prüfung."""

import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import ui_client
from tests.test_document import THESIS

RULES = {
    "benthic": "house-rules", "version": 1, "name": "Universität Musterstadt",
    "settings": {"institution": "Universität Musterstadt", "proxy_prefix": "https://proxy.example.org/login?url=",
                 "unpaywall_email": "bibliothek@example.org", "openalex_api_key": "key-123",
                 "update_url": "https://evil.example.org/release.json", "download_dir": "/tmp/elsewhere"},
    "style": "ieee",
    "rankings": [{"name": "VHB 4", "csv": "Journal Title,ISSN,Rating\nNature,0028-0836,A+\n"}],
}


class Hausregeln(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()
        from benthic import app as appmod
        self.client = ui_client(appmod.app)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def post(self, rules):
        body = rules if isinstance(rules, bytes) else json.dumps(rules).encode()
        return self.client.post("/api/house-rules", data={"file": (io.BytesIO(body), "rules.json")},
                                content_type="multipart/form-data")

    def test_loads_only_allowed_fields(self):
        from benthic import config, journals
        r = self.post(RULES)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["style"], "IEEE")
        s = config.load_settings()
        self.assertEqual(s["house_rules"], "Universität Musterstadt")
        self.assertEqual(s["house_style"], "ieee")
        self.assertEqual(s["proxy_prefix"], "https://proxy.example.org/login?url=")
        self.assertEqual(s["openalex_api_key"], "key-123")
        self.assertEqual(s["update_url"], "")            # Update-Adresse und Ordner setzt keine Hochschule
        self.assertEqual(s["download_dir"], "")
        self.assertEqual([x["name"] for x in journals.list_rankings()], ["VHB 4"])
        self.assertEqual(self.client.get("/api/settings").get_json()["house_rules"], "Universität Musterstadt")
        self.assertEqual(self.client.delete("/api/house-rules").status_code, 200)
        s = config.load_settings()
        self.assertEqual((s["house_rules"], s["house_style"]), ("", ""))
        self.assertEqual(s["institution"], "Universität Musterstadt")  # übernommene Angaben bleiben

    def test_invalid_file_changes_nothing(self):
        from benthic import config, journals
        broken_list = dict(RULES, rankings=[{"name": "Leer", "csv": "a,b\n1,2\n"}])
        for bad in (b"{nope", {"version": 1}, dict(RULES, version=2), dict(RULES, style="mla"), broken_list,
                    dict(RULES, settings={"proxy_prefix": "ftp://x"})):
            self.assertEqual(self.post(bad).status_code, 400, bad)
        self.assertEqual(config.load_settings()["house_rules"], "")
        self.assertEqual(config.load_settings()["institution"], "")
        self.assertEqual(journals.list_rankings(), [])

    def test_house_style_decides_the_style_check(self):
        from benthic import batch, config, verify
        config.save_settings({"house_style": "ieee"})
        folder = Path(self.tmp.name) / "abgaben"
        folder.mkdir()
        (folder / "anna.txt").write_text(THESIS)
        fake = lambda ref, email="", on_event=None: {"identifier": ref.get("label"), "verdict": "verified", "status": "x",
                                                     "note": "", "score": 100, "flags": [], "input": ref.get("raw")}
        with mock.patch.object(verify, "verify_ref", side_effect=fake):
            b = self.client.post("/api/batch", json={"folder": str(folder)}).get_json()
            for _ in range(200):
                b = self.client.get(f"/api/batch/{b['id']}").get_json()
                if b["status"] != "running":
                    break
                time.sleep(0.05)
        job = batch.find_job(b["rows"][0]["job_id"])
        self.assertEqual(job["style"]["style"], "ieee")
        self.assertNotEqual(job["style"]["detected"], "ieee")


if __name__ == "__main__":
    unittest.main()
