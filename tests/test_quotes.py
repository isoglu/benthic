"""Wörtliche Zitate: finden, im Text auffinden, Seiten vergleichen (offline)."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benthic import document, quotes

from tests import ui_client

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

    def test_strip_running_headers(self):
        from benthic import support
        test_pages = [
            {"page": 1, "text": "Nature Reviews 2024 · Page 1\nIntroduction to methods.\n1"},
            {"page": 2, "text": "Nature Reviews 2024 · Page 2\nSecond page text here.\n2"},
            {"page": 3, "text": "Nature Reviews 2024 · Page 3\nThird page conclusion.\n3"},
        ]
        stripped = support._strip_running_headers(test_pages)
        self.assertEqual(stripped[0]["text"], "Introduction to methods.")
        self.assertEqual(stripped[1]["text"], "Second page text here.")
        self.assertEqual(stripped[2]["text"], "Third page conclusion.")


SMITH = [{"page": 1, "text": "Firms that plan post-acquisition integration [16], and focusing on the\n"
                             "defi nitions of signifi cant synergies early, keep more of their staff members."}]
JONES = [{"page": 1, "text": "Introduction."},
         {"page": 2, "text": "Most mergers fail because managers overestimate the synergies they can capture."}]


class Fehlstellen(unittest.TestCase):
    def test_markers_and_split_ligatures(self):
        r = quotes.locate(SMITH, "post-acquisition integration, and focusing on the definitions of significant synergies")
        self.assertEqual(r["status"], "exact")
        self.assertIn("defi nitions", r["excerpt"])                     # die Stelle, wie sie in der PDF steht

    def test_real_word_endings_stay_apart(self):
        self.assertEqual([w for w, _, _ in quotes._words("staff members off the record")],
                         ["staff", "members", "off", "the", "record"])
        self.assertEqual(quotes.locate(SMITH, "keep more of their staff members")["status"], "exact")

    def test_longest_run(self):
        r = quotes.locate(SMITH, "firms that plan post acquisition integration always double their market value")
        self.assertEqual(r["status"], "not_found")
        self.assertEqual(r["longest"], {"words": 6, "of": 11, "page": 1})
        self.assertNotIn("longest", quotes.locate(SMITH, "nothing here resembles the source text at all"))

    def test_a_real_word_that_ends_like_a_ligature(self):
        fr = [{"page": 1, "text": "Pour cette raison, la France doit relever le défi de la transition énergétique."}]
        self.assertEqual(quotes.locate(fr, "la France doit relever le défi")["status"], "exact")
        self.assertEqual(quotes.locate(fr, "la France doit relever le défi climatique")["status"], "close")
        self.assertEqual(quotes.locate(fr, "la France refuse toute transition énergétique")["status"], "not_found")

    def run_document(self, quotes_, held=None):
        doc = {"quotes": [{"quote": q, "entry": 0, "label": "Smith (2020)"} for q in quotes_]}
        results = [{"order": 0, "verdict": "verified", "doi": "10.1/smith", "found": {"authors": ["Smith"], "year": "2020"}},
                   {"order": 1, "verdict": "verified", "doi": "10.1/jones",
                    "found": {"authors": ["Jones"], "year": "2019", "pages": "41–42"}}]
        held = held or {}
        published = {"status": "verified", "copy": None}
        with mock.patch("benthic.identity.held", side_effect=lambda d, *a: (Path(f"/{d[5:]}.pdf"), held.get(d, published))), \
             mock.patch("benthic.support._pages", side_effect=lambda p: SMITH if "smith" in str(p) else JONES):
            return quotes.check_document(doc, results)

    def test_found_word_for_word_in_another_source_of_the_run(self):
        moved, invented = self.run_document(["most mergers fail because managers overestimate the synergies they can capture",
                                             "every merger creates lasting value for all of its shareholders"])
        self.assertEqual(moved["status"], "misattributed")
        self.assertEqual(moved["found_in"], {"label": "Jones 2019", "doi": "10.1/jones", "page": 42})
        self.assertNotIn("longest", moved)                                 # neben der richtigen Quelle ohne Aussage
        self.assertEqual((invented["status"], invented["searched"]), ("not_found", 1))
        self.assertNotIn("found_in", invented)

    def test_short_or_similar_quotes_name_no_other_source(self):
        short, similar = self.run_document(["managers overestimate the synergies they can capture",  # 7 Wörter
                                            "most mergers fail because managers overestimate the gains they can capture"])
        self.assertEqual((short["status"], short.get("found_in"), short.get("searched")), ("not_found", None, None))
        self.assertEqual((similar["status"], similar.get("found_in"), similar["searched"]), ("not_found", None, 1))

    def test_no_search_from_an_incomplete_or_wrong_copy(self):
        quote = ["most mergers fail because managers overestimate the synergies they can capture"]
        for info, status in (({"status": "unprovable", "copy": "summary"}, "incomplete"),
                             ({"status": "contradicted", "file_doi": "10.1/x", "file_title": "X"}, "wrong_pdf"),
                             ({"status": "verified", "copy": "preprint"}, "not_found"),     # dem Preprint fehlt ein Satz
                             ({"status": "unprovable", "copy": None}, "not_found")):       # ob es die Quelle ist, offen
            item, = self.run_document(quote, {"10.1/smith": info})
            self.assertEqual((item["status"], item.get("found_in"), item.get("searched")), (status, None, None))

    def test_incomplete_copy_is_no_finding(self):
        held = (Path("/x.pdf"), {"status": "unprovable", "copy": "summary"})
        with mock.patch("benthic.identity.held", return_value=held), \
             mock.patch("benthic.support._pages", return_value=PAGES):
            missing = quotes.check("10.1/x", "NumPy was invented in a garage in Berlin by three students")
            present = quotes.check("10.1/x", "the primary array programming library")
        self.assertEqual((missing["status"], missing["copy"]), ("incomplete", "summary"))
        self.assertEqual((present["status"], present["copy"]), ("exact", "summary"))


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
        self.client = ui_client(appmod.app)

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
