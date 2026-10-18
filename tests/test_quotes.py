"""Wörtliche Zitate: finden, im Text auffinden, Seiten vergleichen (offline)."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benthic import document, quotes

PAGES = [
    {"page": 1, "text": "Array programming provides a powerful, compact and expressive syntax for accessing,\n"
                        "manipulating and operating on data in vectors, matrices and higher-dimensional arrays. NumPy is\n"
                        "the primary array programming library for the Py-\nthon language."},
    {"page": 2, "text": "It has an essential role in research analysis pipelines in fields as diverse as physics,\n"
                        "chemistry, astronomy, geoscience, biology, psychology, materials science, engineering, finance"},
    {"page": 3, "text": "and economics. For example, in astronomy, NumPy was an important part of the software stack."},
]

THESIS = """1 Introduction

Harris et al. (2020, p. 357) write that “NumPy is the primary array programming library for the Python language”.
Others claim „NumPy was invented in a garage in Berlin by three students“ (Harris et al., 2020, S. 359).
A short "two words" quote is ignored (Harris et al., 2020). Errors are common (Lee et al., 2021).

References

Harris, C. R., Millman, K. J., & van der Walt, S. J. (2020). Array programming with NumPy. Nature, 585(7825), 357–362. https://doi.org/10.1038/s41586-020-2649-2
Lee, K., Park, J., & Kim, S. (2021). Citation errors in student essays written with chat assistants. Computers & Education, 170, 104210.
"""


class Finden(unittest.TestCase):
    def test_quotes_with_source_and_page(self):
        a = document.analyse(THESIS)
        self.assertEqual([(q["label"], q["page"]) for q in a["quotes"]],
                         [("Harris (2020)", "357"), ("Harris et al., 2020", "359")])
        self.assertTrue(all(q["entry"] == 0 for q in a["quotes"]))


class Auffinden(unittest.TestCase):
    def test_exact_across_hyphenation_and_lines(self):
        r = quotes.locate(PAGES, "NumPy is the primary array programming library for the Python language")
        self.assertEqual((r["status"], r["page"]), ("exact", 1))

    def test_across_a_page_break(self):
        r = quotes.locate(PAGES, "materials science, engineering, finance and economics")
        self.assertEqual(r["status"], "exact")

    def test_altered_wording(self):
        r = quotes.locate(PAGES, "NumPy is the main array programming library for Python")
        self.assertEqual(r["status"], "close")
        self.assertIn("primary", r["excerpt"])

    def test_invented(self):
        self.assertEqual(quotes.locate(PAGES, "NumPy was invented in a garage in Berlin by three students")["status"],
                         "not_found")

    def test_page_check_with_journal_pagination(self):
        with mock.patch("benthic.biblio.pdf_path", return_value=Path("/x.pdf")), \
             mock.patch("benthic.support._pages", return_value=PAGES):
            ok = quotes.check("10.1/x", "the primary array programming library", page="357", start_page=357)
            wrong = quotes.check("10.1/x", "the primary array programming library", page="12", start_page=357)
        self.assertEqual((ok["page"], ok["printed_page"], ok["page_ok"]), (1, 357, True))
        self.assertFalse(wrong["page_ok"])

    def test_without_pdf(self):
        with mock.patch("benthic.biblio.pdf_path", return_value=None):
            self.assertEqual(quotes.check("10.1/x", "some quoted words here")["status"], "no_pdf")


class Route(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()
        from benthic import app as appmod, verify
        fake = lambda ref, email="", on_event=None: {"identifier": ref.get("label"), "verdict": "verified", "status": "verified",
                                                     "note": "", "score": 100, "flags": [], "doi": "10.1038/s41586-020-2649-2",
                                                     "found": {"pages": "357–362"}}
        self.patches = [mock.patch.object(verify, "verify_ref", side_effect=fake),
                        mock.patch("benthic.biblio.pdf_path", return_value=Path("/x.pdf")),
                        mock.patch("benthic.support._pages", return_value=PAGES)]
        for p in self.patches:
            p.start()
        self.appmod = appmod
        self.client = appmod.app.test_client()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_job_quotes(self):
        import time
        r = self.client.post("/api/document", data={"file": (io.BytesIO(THESIS.encode()), "t.txt")},
                             content_type="multipart/form-data")
        job_id = r.get_json()["job_id"]
        for _ in range(100):
            if self.client.get(f"/api/jobs/{job_id}").get_json()["status"] != "running":
                break
            time.sleep(0.05)
        j = self.client.post("/api/quotes", json={"job_id": job_id}).get_json()
        self.assertEqual([q["status"] for q in j["quotes"]], ["exact", "not_found"])  # beide aus Harris
        self.assertTrue(j["quotes"][0]["page_ok"])
        from benthic import history
        self.assertEqual(len(history.get(job_id)["quote_check"]), 2)

    def test_single_quote(self):
        j = self.client.post("/api/quotes", json={"doi": "10.1038/s41586-020-2649-2",
                                                  "quote": "the primary array programming library"}).get_json()
        self.assertEqual(j["status"], "exact")


if __name__ == "__main__":
    unittest.main()
