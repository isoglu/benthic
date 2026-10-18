"""Prüfbericht als druckbare Seite (offline)."""

import os
import tempfile
import unittest
from unittest import mock

JOB = {
    "id": "9907", "mode": "verify", "finished_at": 1792300000,
    "document": {"name": "thesis.docx", "entries": 2, "citations": 3, "cross_check": True,
                 "missing_in_list": [{"label": "Brown, 2021", "count": 1, "sentence": "Students rarely check (Brown, 2021)."}],
                 "uncited": []},
    "results": [
        {"order": 0, "verdict": "verified", "doi": "10.1038/s41586-020-2649-2", "claimed": {"title": "Array programming with NumPy"},
         "found": {"venue": "Nature", "year": "2020"}, "input": "Harris (2020). Array programming with NumPy."},
        {"order": 1, "verdict": "retracted", "doi": "10.1016/S0140-6736(97)11096-0", "note": "Retracted.",
         "claimed": {"title": "Ileal-lymphoid-nodular hyperplasia <script>"}, "published_version": None},
    ],
    "quote_check": [{"quote": "NumPy is the primary array programming library", "status": "exact", "label": "Harris (2020)"}],
    "style": {"applicable": True, "style": "apa", "styles": {"apa": "APA 7"}, "entries": {"0": [{"code": "no_issue", "text": "The issue is missing (7825)."}]}, "list": []},
}


class Bericht(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()
        from benthic import app as appmod, history
        history.append(JOB)
        self.client = appmod.app.test_client()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_page(self):
        r = self.client.get("/report/9907")
        self.assertEqual(r.status_code, 200)
        page = r.get_data(as_text=True)
        for text in ("thesis.docx", "Brown, 2021", "NumPy is the primary array programming library", "APA 7",
                     "The issue is missing (7825).", "doi:10.1016/S0140-6736(97)11096-0"):
            self.assertIn(text, page)
        self.assertNotIn("<script>\"", page)
        self.assertIn("&lt;script&gt;", page)          # Titel werden escaped
        self.assertNotIn("print()", page)
        self.assertIn("print()", self.client.get("/report/9907?print=1").get_data(as_text=True))

    def test_unknown(self):
        self.assertEqual(self.client.get("/report/999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
