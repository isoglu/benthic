"""Für Lehrende: Export aus Moodle oder ILIAS, dieselbe erfundene Quelle in mehreren Abgaben, Kursübersicht (offline)."""

import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from benthic import batch


def _job(results):
    return {"results": results}


class Kurs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": str(self.root / "data")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_moodle_names(self):
        self.assertEqual(batch.display_name("Ada Lovelace_123456_assignsubmission_file_/essay.pdf"), "Ada Lovelace · essay.pdf")
        self.assertEqual(batch.display_name("Grace Hopper_42_assignsubmission_onlinetext_"), "Grace Hopper")
        self.assertEqual(batch.display_name("gruppe-1/arbeit.docx"), "gruppe-1/arbeit.docx")

    def test_unpack_is_safe(self):
        z = self.root / "export.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("Ada Lovelace_1_assignsubmission_file_/essay.txt", "text")
            f.writestr("../outside.txt", "escape attempt")
            f.writestr("__MACOSX/._essay.txt", "resource fork")
            f.writestr("Ada Lovelace_1_assignsubmission_file_/.DS_Store", "hidden")
        target = self.root / "out"
        batch.unpack(z, target)
        files = sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())
        self.assertEqual(files, ["Ada Lovelace_1_assignsubmission_file_/essay.txt", "outside.txt"])  # „..“ bleibt drinnen
        self.assertFalse((self.root / "outside.txt").exists())

    def test_start_with_zip_unpacks_into_data_folder(self):
        z = self.root / "Seminar WS26.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("Ada Lovelace_1_assignsubmission_file_/essay.txt", "References\nSmith, J. (2019). A title. Journal.")
        with mock.patch.object(batch, "_run"), mock.patch.object(batch.threading, "Thread"):
            b = batch.start(str(z))
        self.assertEqual(b["name"], "Seminar WS26")
        self.assertEqual(b["rows"][0]["name"], "Ada Lovelace · essay.txt")
        self.assertTrue(b["options"]["lms"])
        self.assertTrue(Path(b["rows"][0]["path"]).is_file())
        self.assertIn("data", b["rows"][0]["path"])                   # entpackt im Datenordner, nicht neben dem Export
        with self.assertRaises(ValueError):
            bad = self.root / "bad.zip"
            bad.write_bytes(b"not a zip")
            batch.start(str(bad))

    def test_shared_phantoms_and_summary(self):
        fake = {"verdict": "not_found", "claimed": {"title": "A systematic audit of citation reliability in language models"}}
        b = {"rows": [
            {"name": "Ada", "state": "done", "not_found": 1, "job": _job([fake, {"verdict": "verified"}])},
            {"name": "Ben", "state": "done", "not_found": 1, "style": 2,
             "job": _job([{"verdict": "not_found", "claimed": {"title": "A Systematic Audit of Citation Reliability in Language Models."}}])},
            {"name": "Cem", "state": "done", "job": _job([{"verdict": "verified"}])},
            {"name": "Dia", "state": "error"},
        ]}
        shared = batch.shared_phantoms(b)
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0]["files"], ["Ada", "Ben"])         # gleiche Quelle trotz Groß- und Kleinschreibung
        cs = batch.class_summary(b)
        self.assertEqual(cs["files"], 3)
        self.assertEqual(cs["with"]["not_found"], 2)
        self.assertEqual(cs["with"]["style"], 1)
        pub = batch.public(b)
        self.assertNotIn("job", pub["rows"][0])                        # Läufe bleiben intern
        self.assertEqual(pub["shared_phantoms"][0]["files"], ["Ada", "Ben"])


if __name__ == "__main__":
    unittest.main()
