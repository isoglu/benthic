"""Grimpo, der Begleiter auf dem Schreibtisch: Seite, Fundstücke, Weg durch eine Arbeit (offline)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class Grimpo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()
        from benthic import app as appmod
        self.client = appmod.app.test_client()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_page(self):
        page = self.client.get("/pet").get_data(as_text=True)
        self.assertIn('id="pet"', page)
        self.assertIn("/i18n.js", page)
        self.assertIn("prefers-reduced-motion", page)      # Bewegung abschaltbar
        self.assertNotIn("\u2014", page)

    def test_clips(self):
        from benthic import clips
        j = self.client.post("/api/clips", json={"kind": "text", "text": "See doi:10.1038/nature14539. Also 10.1145/3065386."}).get_json()
        self.assertEqual(j["dois"], ["10.1038/nature14539", "10.1145/3065386"])  # ohne Punkt am Ende
        with mock.patch.object(clips, "doi_of_page", return_value="10.1126/science.1065467") as page:
            j = self.client.post("/api/clips", json={"kind": "link", "url": "https://www.science.org/doi/abs/x"}).get_json()
        self.assertEqual(j["dois"], ["10.1126/science.1065467"])
        page.assert_called_once()
        j = self.client.post("/api/clips", json={"kind": "link", "url": "https://doi.org/10.2307/1879431"}).get_json()
        self.assertEqual(j["dois"], ["10.2307/1879431"])           # DOI in der Adresse: kein Seitenabruf nötig
        self.assertEqual(self.client.post("/api/clips", json={"kind": "link", "url": "ftp://x"}).status_code, 400)
        outside = Path(self.tmp.name) / "elsewhere.png"
        outside.write_bytes(b"png")
        self.assertEqual(self.client.post("/api/clips", json={"kind": "image", "path": str(outside)}).status_code, 400)
        inside = clips.folder() / "grimpo-1.png"
        inside.write_bytes(b"png")
        img = self.client.post("/api/clips", json={"kind": "image", "path": str(inside), "text": "Fig. 2"}).get_json()
        listed = self.client.get("/api/clips").get_json()["clips"]
        self.assertEqual([c["kind"] for c in listed], ["image", "link", "link", "text"])  # neueste zuerst
        self.assertTrue(self.client.delete(f"/api/clips/{img['id']}").get_json()["removed"])
        self.assertFalse(inside.exists())                          # Bild mitgelöscht

    def test_page_doi_from_meta(self):
        from benthic import clips
        resp = mock.Mock()
        resp.iter_content.return_value = [b'<html><head><meta name="citation_doi" content="10.1016/j.cell.2006.07.024"></head>']
        with mock.patch("requests.get", return_value=resp):
            self.assertEqual(clips.doi_of_page("https://www.cell.com/x"), "10.1016/j.cell.2006.07.024")

    def test_guide_per_project(self):
        from benthic import guide, projects
        g = self.client.get("/api/guide").get_json()
        self.assertEqual(len(g["steps"]), len(guide.STEPS))
        self.assertEqual(g["current"], "topic")
        for task in g["steps"][0]["tasks"]:
            g = self.client.post("/api/guide", json={"task": task["id"], "done": True}).get_json()
        self.assertEqual(g["current"], "question")                 # erster Schritt erledigt: weiter
        self.assertTrue(g["steps"][0]["complete"])
        self.assertEqual(g["done"], len(guide.STEPS[0][2]))
        p = projects.create("Bachelorarbeit")
        projects.set_active(p["id"])
        self.assertEqual(self.client.get("/api/guide").get_json()["current"], "topic")  # eigener Stand je Projekt
        self.assertEqual(self.client.post("/api/guide", json={"task": "nope.9", "done": True}).status_code, 400)
        views = {t["view"] for s in g["steps"] for t in s["tasks"] if t["view"]}
        self.assertTrue(views <= {"projects", "find", "verify"})   # nur Ansichten, die es in der App gibt


if __name__ == "__main__":
    unittest.main()
