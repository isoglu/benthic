"""Stapelprüfung: Ordner lesen, Arbeiten nacheinander prüfen, Übersicht und Berichte (Prüfung ersetzt)."""

import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.test_document import THESIS
from tests.test_latex import BIB, MAIN


class Stapel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data = root / "data"
        self.folder = root / "abgaben"
        self.folder.mkdir()
        (self.folder / "anna.txt").write_text(THESIS)
        (self.folder / "ben.txt").write_text("No references here at all.")
        (self.folder / "~$temp.docx").write_text("office lock file")
        proj = self.folder / "carla"
        proj.mkdir()
        (proj / "main.tex").write_text(MAIN.replace(r"\input{chapters/two}", ""))
        (proj / "refs.bib").write_text(BIB)
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": str(self.data)})
        self.env.start()
        from benthic import app as appmod, verify
        fake = lambda ref, email="", on_event=None: {"identifier": ref.get("label"), "verdict": "not_found" if "Taylor" in str(ref.get("raw")) else "verified",
                                                     "status": "x", "note": "", "score": 100, "flags": [], "input": ref.get("raw")}
        self.patch = mock.patch.object(verify, "verify_ref", side_effect=fake)
        self.patch.start()
        self.client = appmod.app.test_client()

    def tearDown(self):
        self.patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_latex_project_as_zip(self):
        """Ein LaTeX-Projekt als .zip im Ordner wird aus dem Archiv gelesen, nicht aus dem Ordner daneben."""
        import zipfile
        from benthic import app as appmod
        archive = self.folder / "dora.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("dora/main.tex", MAIN.replace(r"\input{chapters/two}", ""))
            z.writestr("dora/refs.bib", BIB)
        analysis, name, size = appmod._analyse_document([(archive.name, b"")], archive)
        self.assertEqual(name, "dora.zip")
        self.assertEqual(analysis["style"], "latex")
        self.assertEqual(analysis.get("main_file"), "dora/main.tex")
        self.assertTrue(analysis["entries"])
        self.assertGreater(size, 0)

    def wait(self, batch_id):
        for _ in range(200):
            b = self.client.get(f"/api/batch/{batch_id}").get_json()
            if b["status"] != "running":
                return b
            time.sleep(0.05)
        self.fail("batch did not finish")

    def test_folder(self):
        from benthic import batch, history
        self.assertEqual([p.name for p in batch.submissions(self.folder)], ["anna.txt", "ben.txt", "carla"])
        r = self.client.post("/api/batch", json={"folder": str(self.folder)})
        self.assertEqual(r.status_code, 200, r.get_json())
        b = self.wait(r.get_json()["id"])
        rows = {row["name"]: row for row in b["rows"]}
        self.assertEqual(rows["anna.txt"]["missing"], 1)
        self.assertEqual(rows["anna.txt"]["not_found"], 1)
        self.assertEqual(rows["ben.txt"]["error"], "no-bibliography")
        self.assertEqual(rows["carla"]["entries"], 3)
        self.assertEqual(batch.find_job(rows["anna.txt"]["job_id"])["schema"], 1)  # Version des Ergebnis-Datensatzes
        self.assertEqual(history.load(), [])                       # Stapel füllen den Verlauf nicht
        report = self.client.get(f"/report/{rows['anna.txt']['job_id']}")
        self.assertEqual(report.status_code, 200)                   # Bericht aus dem Stapel
        csv = self.client.get(f"/api/batch/{b['id']}/export?format=csv").get_data(as_text=True)
        self.assertIn("anna.txt", csv)
        xlsx = self.client.get(f"/api/batch/{b['id']}/export?format=xlsx")
        self.assertEqual(xlsx.status_code, 200)
        self.assertEqual(self.client.get("/api/batch").get_json()["batches"][0]["id"], b["id"])

    def test_subfolders_rerun_totals_and_collective_report(self):
        from benthic import batch
        sub = self.folder / "group-b" / "dora"
        sub.mkdir(parents=True)
        (sub / "dora.txt").write_text(THESIS)
        self.assertNotIn("dora.txt", [p.name for p in batch.submissions(self.folder)])
        self.assertIn("dora.txt", [p.name for p in batch.submissions(self.folder, recursive=True)])
        r = self.client.post("/api/batch", json={"folder": str(self.folder), "recursive": True, "project": True})
        b = self.wait(r.get_json()["id"])
        self.assertEqual(b["totals"]["files"], 4)
        self.assertEqual(b["totals"]["errors"], 1)
        self.assertEqual(b["totals"]["flagged_files"], 3)            # anna, dora (Brown fehlt), carla (nobody2021)
        anna = next(row for row in b["rows"] if row["name"] == "anna.txt")
        self.assertEqual(anna["problems"]["missing"], ["Brown (2017)"])
        self.assertTrue(b["project"])
        from benthic import projects
        self.assertEqual(projects.detail(b["project"])["name"], "abgaben")  # Projekt nach dem Ordner
        report = self.client.get(f"/report/batch/{b['id']}").get_data(as_text=True)
        self.assertIn("Brown (2017)", report)
        again = self.client.post(f"/api/batch/{b['id']}/rerun", json={"names": ["anna.txt"]}).get_json()
        self.assertEqual(next(row for row in again["rows"] if row["name"] == "anna.txt")["state"], "queued")
        b = self.wait(b["id"])
        self.assertEqual(next(row for row in b["rows"] if row["name"] == "anna.txt")["state"], "done")

    def test_errors(self):
        self.assertEqual(self.client.post("/api/batch", json={"folder": "/nonexistent"}).status_code, 400)
        empty = Path(self.tmp.name) / "leer"
        empty.mkdir()
        self.assertEqual(self.client.post("/api/batch", json={"folder": str(empty)}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
