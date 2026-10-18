"""Ersteinrichtung und Bibliotheks-Import (offline, temporäres Datenverzeichnis)."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


class SetupFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name
        from benthic import app as appmod
        self.client = appmod.app.test_client()

    def tearDown(self):
        os.environ.pop("BENTHIC_DATA_DIR", None)
        self.tmp.cleanup()

    def _zotero(self) -> Path:
        db = Path(self.tmp.name) / "zotero.sqlite"
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE items(itemID INTEGER PRIMARY KEY);
            CREATE TABLE fieldsCombined(fieldID INTEGER PRIMARY KEY, fieldName TEXT);
            CREATE TABLE itemDataValues(valueID INTEGER PRIMARY KEY, value);
            CREATE TABLE itemData(itemID INT, fieldID INT, valueID INT);
            CREATE TABLE deletedItems(itemID INT);
            INSERT INTO fieldsCombined VALUES (1,'title'),(2,'DOI'),(3,'extra'),(4,'url');
            INSERT INTO items VALUES (1),(2),(3),(4);
            INSERT INTO itemDataValues VALUES (1,'Array programming with NumPy'),(2,'10.1038/s41586-020-2649-2'),
                (3,'Old item'),(4,'DOI: 10.1126/science.1065467'),(5,'Deleted item'),(6,'10.9999/deleted'),
                (7,'No DOI at all');
            INSERT INTO itemData VALUES (1,1,1),(1,2,2),(2,1,3),(2,3,4),(3,1,5),(3,2,6),(4,1,7);
            INSERT INTO deletedItems VALUES (3);
        """)
        con.commit()
        con.close()
        return db

    def test_fresh_install_needs_setup_and_has_no_extra_routes(self):
        j = self.client.get("/api/setup").get_json()
        self.assertTrue(j["needed"])
        s = self.client.get("/api/settings").get_json()
        self.assertFalse(s["extra_routes"])

    def test_setup_with_zotero(self):
        folder = Path(self.tmp.name) / "Papers" / "Benthic"
        r = self.client.post("/api/setup", json={
            "name": "Alex Student", "email": "alex@uni.example.de", "institution": "Uni Example",
            "proxy_prefix": "https://ezproxy.uni.example.de/login?url=https://doi.org/",
            "download_dir": str(folder), "library": {"kind": "zotero", "path": str(self._zotero())},
        })
        self.assertEqual(r.status_code, 200, r.get_json())
        j = r.get_json()
        self.assertEqual(j["imported"]["found"], 2)  # gelöschter Eintrag und Eintrag ohne DOI fehlen
        self.assertTrue(folder.is_dir())
        self.assertFalse(self.client.get("/api/setup").get_json()["needed"])
        check = self.client.post("/api/library/check", json={"text": "10.1126/science.1065467"}).get_json()
        self.assertTrue(check["results"][0]["present"])

    def test_setup_validation(self):
        r = self.client.post("/api/setup", json={"email": "not-an-email"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["field"], "email")
        r = self.client.post("/api/setup", json={"download_dir": "relative/path"})
        self.assertEqual(r.get_json()["field"], "folder")

    def test_bibtex_library_import_has_clean_dois(self):
        bib = Path(self.tmp.name) / "refs.bib"
        bib.write_text("@article{a, title={A}, doi={10.1038/s41586-020-2649-2},}\n", encoding="utf-8")
        j = self.client.post("/api/library/import", json={"path": str(bib)}).get_json()
        self.assertEqual(j["found"], 1)
        lib = json.loads((Path(self.tmp.name) / "library.json").read_text())
        self.assertIn("10.1038/s41586-020-2649-2", lib["entries"])

    def test_existing_settings_are_migrated_as_set_up(self):
        (Path(self.tmp.name) / "settings.json").write_text(json.dumps({"oa_providers": ["arxiv"]}))
        self.assertFalse(self.client.get("/api/setup").get_json()["needed"])
        self.assertEqual(self.client.get("/api/settings").get_json()["oa_providers"][0], "arxiv")

    def test_parse_preview(self):
        j = self.client.post("/api/refs/parse", json={"text": "Doe, J. (2020). A study of things. https://doi.org/10.1/x"}).get_json()
        self.assertEqual(j["summary"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
