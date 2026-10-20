"""Prüfungen je Projekt: neu prüfen, Läufe, Rückzugswache an und aus (Prüfung ersetzt)."""

import os
import tempfile
import time
import unittest
from unittest import mock

from tests import ui_client


class Projektpruefung(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()
        from benthic import app as appmod, projects, verify
        self.seen = []

        def fake(ref, email="", on_event=None):
            self.seen.append(ref)
            return {"identifier": ref.get("label"), "verdict": "verified", "status": "verified", "note": "",
                    "score": 100, "flags": [], "doi": ref.get("doi")}
        self.patch = mock.patch.object(verify, "verify_ref", side_effect=fake)
        self.patch.start()
        self.projects = projects
        p = projects.create("Thesis")
        projects.add_items(p["id"], [{"doi": "10.1038/s41586-020-2649-2", "title": "Array programming with NumPy", "year": "2020"},
                                     {"doi": "10.1038/nature14539"}])
        other = projects.create("Side project")
        projects.add_items(other["id"], [{"doi": "10.5555/side"}])
        self.pid, self.other = p["id"], other["id"]
        self.client = ui_client(appmod.app)

    def tearDown(self):
        self.patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_recheck_and_runs(self):
        j = self.client.post(f"/api/projects/{self.pid}/check").get_json()
        self.assertEqual(j["total"], 2)
        for _ in range(100):
            if self.client.get(f"/api/jobs/{j['job_id']}").get_json()["status"] != "running":
                break
            time.sleep(0.05)
        titles = {r.get("title") for r in self.seen}
        self.assertIn("Array programming with NumPy", titles)   # bekannte Titel werden mitgeprüft
        runs = self.client.get(f"/api/projects/{self.pid}/runs").get_json()["runs"]
        self.assertEqual(runs[0]["id"], j["job_id"])
        self.assertEqual(runs[0]["counts"]["verified"], 2)
        self.assertEqual(self.client.get(f"/api/projects/{self.other}/runs").get_json()["runs"], [])

    def test_watch_switch(self):
        from benthic import watch
        self.assertEqual(len(watch._project_dois()), 3)
        self.client.post(f"/api/projects/{self.other}", json={"watch": False})
        self.assertEqual(len(watch._project_dois()), 2)
        self.assertEqual(len(watch._project_dois(only=self.other)), 1)   # "jetzt prüfen" geht trotzdem
        self.assertFalse(self.client.get(f"/api/projects/{self.other}").get_json()["watch"])


if __name__ == "__main__":
    unittest.main()
