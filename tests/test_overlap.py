"""Übernahmen ohne Anführungszeichen: zitierende Sätze gegen den Volltext der Quelle (offline, PDF gemockt)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benthic import biblio, overlap

SOURCE = [{"page": 1, "text": "Abstract. NumPy is the primary array programming library for the Python language.\n"
                              "It has an essential role in research analysis pipelines in fields as diverse as physics,\n"
                              "chemistry, astronomy, geoscience, biology, psychology, materials science, engineering."},
          {"page": 2, "text": "Array programming provides a powerful, compact and expressive syntax for accessing,\n"
                              "manipulating and operating on data in vectors, matrices and higher-dimensional arrays."}]

DOC = {"claims": [
    # wortgleich, ohne Anführungszeichen
    {"entry": 0, "label": "Harris et al. (2020)",
     "sentence": "NumPy is the primary array programming library for the Python language (Harris et al., 2020)."},
    # fast wortgleich: zwei Wörter ersetzt
    {"entry": 0, "label": "Harris et al. (2020)",
     "sentence": "Array programming offers a powerful, compact and expressive syntax for accessing, manipulating and "
                 "operating on data in vectors and matrices (Harris et al., 2020)."},
    # lange wortgleiche Folge in einem eigenen Satz
    {"entry": 0, "label": "Harris et al. (2020)",
     "sentence": "In our lab, it has an essential role in research analysis pipelines in fields as diverse as physics, "
                 "which we extend here (Harris et al., 2020)."},
    # in Anführungszeichen: gekennzeichnet, kein Fund
    {"entry": 0, "label": "Harris et al. (2020)",
     "sentence": "They call it “the primary array programming library for the Python language” (Harris et al., 2020)."},
    # eigene Worte
    {"entry": 0, "label": "Harris et al. (2020)",
     "sentence": "Our own survey of students found that most use spreadsheets before they learn any programming (Harris et al., 2020)."},
    # Quelle ohne PDF, und eine nicht bestätigte Quelle
    {"entry": 1, "label": "Lee (2021)", "sentence": "Students rarely check their sources before they submit (Lee, 2021)."},
    {"entry": 2, "label": "Smith (2021)", "sentence": "An invented audit found many fabricated references (Smith, 2021)."},
]}
RESULTS = [{"order": 0, "verdict": "verified", "doi": "10.1038/s41586-020-2649-2"},
           {"order": 1, "verdict": "verified", "doi": "10.1016/j.compedu.2021.104210"},
           {"order": 2, "verdict": "not_found"}]


class Uebernahmen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pdf = Path(self.tmp.name) / "harris.pdf"
        self.pdf.write_bytes(b"%PDF")
        overlap._index_cache.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self):
        with mock.patch.object(biblio, "pdf_path", side_effect=lambda d: self.pdf if d.startswith("10.1038") else None), \
                mock.patch.object(overlap.support, "_pages", return_value=SOURCE):
            return overlap.check_document(DOC, RESULTS)

    def test_findings(self):
        r = self.run_check()
        kinds = [f["kind"] for f in r["findings"]]
        self.assertEqual(kinds, ["verbatim", "close", "partial"])
        self.assertEqual(r["findings"][0]["page"], 1)
        self.assertIn("primary array programming library", r["findings"][0]["excerpt"])
        self.assertEqual(r["checked"], 5)                       # die fünf Sätze zur geladenen Quelle
        self.assertEqual(r["missing_pdfs"], ["10.1016/j.compedu.2021.104210"])
        self.assertEqual(r["no_source"], 1)                      # erfundene Quelle: nichts zu vergleichen
        self.assertEqual(overlap.summary(r), {"verbatim": 1, "close": 1, "partial": 1, "translated": 0})

    def test_own_words_drop_quotes_and_citations(self):
        self.assertEqual(overlap.own_words("They call it “the library” (Harris et al., 2020) and [3]."),
                         "They call it and .")

    def test_endpoint_stores_result(self):
        env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        env.start()
        try:
            from benthic import app as appmod, history
            history.append({"id": "77", "mode": "verify", "results": RESULTS, "document": DOC})
            client = appmod.app.test_client()
            with mock.patch.object(biblio, "pdf_path", side_effect=lambda d: self.pdf if d.startswith("10.1038") else None), \
                    mock.patch.object(overlap.support, "_pages", return_value=SOURCE):
                j = client.post("/api/overlap", json={"job_id": "77"}).get_json()
            self.assertEqual(j["counts"]["verbatim"], 1)
            self.assertEqual(history.get("77")["overlap_check"]["checked"], 5)
            page = client.get("/report/77").get_data(as_text=True)
            self.assertIn('id="sec-overlap"', page)
            self.assertEqual(client.post("/api/overlap", json={"job_id": "nope"}).status_code, 404)
        finally:
            env.stop()


GERMAN = {"claims": [
    # Wort für Wort übersetzt, ohne Anführungszeichen
    {"entry": 0, "label": "Harris et al. (2020)",
     "sentence": "Die Array-Programmierung bietet eine leistungsfähige, kompakte und ausdrucksstarke Syntax für den Zugriff "
                 "auf Daten in Vektoren, Matrizen und höherdimensionalen Arrays und deren Bearbeitung (Harris et al., 2020)."},
    # eigene Worte zum selben Thema
    {"entry": 0, "label": "Harris et al. (2020)",
     "sentence": "In unserem Kurs lernen die Studierenden zuerst Tabellen und erst später eine eigene Programmiersprache "
                 "kennen (Harris et al., 2020)."},
]}
# So würde der Mac die beiden Sätze ins Englische übersetzen.
TRANSLATIONS = {
    GERMAN["claims"][0]["sentence"]: "Array programming offers a powerful, compact and expressive syntax for accessing data "
                                     "in vectors, matrices and higher-dimensional arrays and manipulating them.",
    GERMAN["claims"][1]["sentence"]: "In our course, students first learn spreadsheets and only later their own programming language.",
}


class Uebersetzt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pdf = Path(self.tmp.name) / "harris.pdf"
        self.pdf.write_bytes(b"%PDF")
        overlap._index_cache.clear()
        overlap._sentence_cache.clear()
        self.patches = [mock.patch.object(biblio, "pdf_path", side_effect=lambda d: self.pdf if d.startswith("10.1038") else None),
                        mock.patch.object(overlap.support, "_pages", return_value=SOURCE)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_pending_only_across_languages(self):
        pending = overlap.translation_pending(GERMAN, RESULTS)
        self.assertEqual([(x["source"], x["target"]) for x in pending], [("de", "en"), ("de", "en")])
        self.assertNotIn("Harris", pending[0]["text"])                    # ohne Zitierklammer übersetzen
        self.assertEqual(overlap.translation_pending(DOC, RESULTS), [])  # englisch zu englisch: der normale Abgleich

    def test_translated_sentence_is_found(self):
        found = overlap.check_translated(GERMAN, RESULTS, TRANSLATIONS)
        self.assertEqual([f["kind"] for f in found], ["translated"])
        self.assertEqual(found[0]["page"], 2)
        self.assertIn("expressive syntax", found[0]["excerpt"])
        self.assertEqual(overlap.check_translated(GERMAN, RESULTS, {}), [])


if __name__ == "__main__":
    unittest.main()
