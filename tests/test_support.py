"""Belegstellen: Passagen im Volltext, die einer Aussage am nächsten kommen (offline)."""

import os
import tempfile
import unittest
from unittest import mock

from benthic import support

PAGES = [
    {"page": 1, "text": "Deep residual learning. We present a residual learning framework to ease the training of "
                        "networks that are substantially deeper than those used previously. An ensemble of these "
                        "residual nets achieves 3.57% error on the ImageNet test set. This result won the 1st place "
                        "on the ILSVRC 2015 classification task."},
    {"page": 4, "text": "Plain networks. We argue that this optimization difficulty is unlikely to be caused by "
                        "vanishing gradients. The deeper plain network has higher training error throughout the "
                        "whole training procedure. We conjecture that deep plain nets may have exponentially low "
                        "convergence rates."},
    {"page": 9, "text": "References. [1] Y. Bengio, P. Simard, and P. Frasconi. Learning long-term dependencies "
                        "with gradient descent is difficult. 1994. [2] C. M. Bishop. Neural networks for pattern "
                        "recognition. 1995. [3] W. L. Briggs, S. F. McCormick, et al. A Multigrid Tutorial. 2000."},
]


class Passagen(unittest.TestCase):
    def setUp(self):
        self.chunks = support.passages(PAGES)

    def test_reference_list_is_skipped(self):
        self.assertTrue(self.chunks)
        self.assertNotIn(9, {c["page"] for c in self.chunks})

    def test_close_claim_with_numbers(self):
        top = support.rank("Residual nets won ILSVRC 2015 with 3.57% top-5 error.", self.chunks)
        self.assertEqual(top[0]["page"], 1)
        self.assertEqual(support.level(top[0]), "close")
        self.assertIn("3.57%", top[0]["terms"])

    def test_paraphrase_finds_the_right_page(self):
        top = support.rank("Deeper plain networks show a higher training error.", self.chunks)
        self.assertEqual(top[0]["page"], 4)
        self.assertIn(support.level(top[0]), ("close", "partial"))

    def test_unrelated_claim(self):
        top = support.rank("Coffee consumption improves memory in older adults.", self.chunks)
        self.assertIn(support.level(top[0] if top else None), ("weak", "none"))

    def test_stopwords_and_umlauts(self):
        self.assertEqual(support.tokens("Die Übung und the training"), support.tokens("die ubung the training"))
        self.assertEqual(support.rank("the and of", self.chunks), [])


class Sprache(unittest.TestCase):
    def test_language(self):
        self.assertEqual(support.language("Residuale Netze mit 152 Schichten gewannen die Aufgabe, und das ist nicht neu."), "de")
        self.assertEqual(support.language("The deeper plain network has higher training error than the shallow one."), "en")
        self.assertIsNone(support.language("NumPy 2020"))

    def test_translated_claim_is_used(self):
        from pathlib import Path
        with mock.patch("benthic.biblio.pdf_path", return_value=Path("/x.pdf")), \
             mock.patch("benthic.support._pages", return_value=PAGES):
            german = support.find("10.1/x", "Tiefere einfache Netze haben einen höheren Trainingsfehler, das ist nicht neu.")
            english = support.find("10.1/x", "Tiefere einfache Netze haben einen höheren Trainingsfehler, das ist nicht neu.",
                                   translated="Deeper plain networks have a higher training error.")
        self.assertTrue(german["needs_translation"])
        self.assertEqual((german["claim_language"], german["source_language"]), ("de", "en"))
        self.assertFalse(english["needs_translation"])
        self.assertEqual(english["passages"][0]["page"], 4)
        self.assertEqual(english["translated"], "Deeper plain networks have a higher training error.")


class Route(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("BENTHIC_DATA_DIR")
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name
        from benthic import app as appmod
        self.client = appmod.app.test_client()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("BENTHIC_DATA_DIR", None)
        else:
            os.environ["BENTHIC_DATA_DIR"] = self._old
        self.tmp.cleanup()

    def test_without_pdf(self):
        r = self.client.post("/api/support", json={"doi": "10.1234/none", "claim": "Residual nets won the challenge."})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["available"])
        self.assertTrue(r.get_json()["error"])

    def test_with_pdf(self):
        from pathlib import Path
        pdf = Path(self.tmp.name) / "he.pdf"
        pdf.write_bytes(b"%PDF")
        with mock.patch("benthic.biblio.pdf_path", return_value=pdf), \
             mock.patch("benthic.support._pages", return_value=PAGES):
            r = self.client.post("/api/support", json={"doi": "10.1109/CVPR.2016.90",
                                                       "claim": "Deeper plain networks have higher training error."})
        j = r.get_json()
        self.assertTrue(j["available"])
        self.assertEqual(j["passages"][0]["page"], 4)
        self.assertEqual(j["file"], "he.pdf")

    def test_bad_input(self):
        self.assertEqual(self.client.post("/api/support", json={"doi": "x", "claim": "long enough claim"}).status_code, 400)
        self.assertEqual(self.client.post("/api/support", json={"doi": "10.1/x", "claim": "short"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
