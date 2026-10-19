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
                     "The issue is missing (7825).", "10.1016/S0140-6736(97)11096-0"):
            self.assertIn(text, page)
        self.assertNotIn("<script>\"", page)
        self.assertIn("&lt;script&gt;", page)          # Titel werden escaped
        self.assertNotIn("print()", page)
        self.assertIn("print()", self.client.get("/report/9907?print=1").get_data(as_text=True))

    def test_document_structure(self):
        page = self.client.get("/report/9907").get_data(as_text=True)
        self.assertIn('class="page cover"', page)            # Deckblatt
        for anchor in ("sec-overview", "sec-contents", "sec-attention", "sec-document", "sec-quotes", "sec-style",
                       "sec-all", "sec-about"):
            self.assertIn(f'id="{anchor}"', page)
        self.assertIn('data-pageof="sec-all"', page)           # Inhaltsverzeichnis mit Seitenzahlen
        self.assertIn('<template id="page-tpl">', page)       # Kopf und Fuß jeder Innenseite
        self.assertNotIn("stop-opacity", page)                # druckt WebKit deckend (report._print_safe)
        self.assertNotIn("<mask", page)

    def test_batch_page(self):
        from benthic import report
        b = {"id": "b1", "name": "Seminar", "folder": "/tmp/x", "finished_at": 1792300000,
             "totals": {"checked": 2, "flagged_files": 1, "retracted_files": 1, "errors": 0},
             "rows": [{"name": "a.pdf", "entries": 10, "retracted": 1, "problems": {"retracted": ["Wakefield 1998"]}},
                      {"name": "b.docx", "state": "error", "error": "no-bibliography"}]}
        page = report.render_batch(b)
        for text in ("a.pdf", "b.docx", "Wakefield 1998", 'class="page cover"', 'id="sec-about"'):
            self.assertIn(text, page)

    def test_unknown(self):
        self.assertEqual(self.client.get("/report/999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
