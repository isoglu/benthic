"""Ganze Arbeiten: Text aus Word, Literaturverzeichnis, Zitate und Abgleich (offline)."""

import io
import unittest
import zipfile

from benthic import document

from tests import ui_client

THESIS = """1 Introduction

Language models invent sources (Smith & Jones, 2019). Earlier work found the same pattern
in student essays (Lee et al., 2021; Müller 2018, S. 12). As Harris et al. (2020) show, array
libraries changed research software. Some claims rest on Brown (2017).

Table of contents mentions References on page 40 too.

References

Harris, C. R., Millman, K. J., & van der Walt, S. J. (2020). Array programming with
NumPy. Nature, 585, 357-362. https://doi.org/10.1038/s41586-020-2649-2
Lee, K., Park, J., & Kim, S. (2021). Citation errors in student essays written with
chat assistants. Computers & Education, 170, 104210.
Müller, A. (2018). Quellenarbeit im Studium. Springer.
Smith, J., & Jones, P. (2019). Invented references in language model output.
Journal of Imaginary Results, 12(3), 45-67.
Taylor, R. (2015). A source nobody cites. Oxford University Press.

Appendix A

Interview guide with questions from 2020 and 2021.
"""

NUMERIC = """Results confirm earlier work [1], [3-4] and contradict [6].

Bibliography
[1] C. R. Harris et al., "Array programming with NumPy," Nature, vol. 585, pp. 357-362,
2020, doi: 10.1038/s41586-020-2649-2.
[2] K. He, X. Zhang, S. Ren, and J. Sun, "Deep residual learning for image recognition,"
in Proc. CVPR, 2016, pp. 770-778.
[3] A. Vaswani et al., "Attention is all you need," in Proc. NeurIPS, 2017.
[4] Y. LeCun, Y. Bengio, and G. Hinton, "Deep learning," Nature, vol. 521, pp. 436-444, 2015.
"""


def docx(paragraphs, footnotes=()):
    import html
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f"<w:p><w:r><w:t>{html.escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    notes = "".join(f"<w:footnote><w:p><w:r><w:t>{html.escape(p)}</w:t></w:r></w:p></w:footnote>" for p in footnotes)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document {w}><w:body>{body}</w:body></w:document>')
        if footnotes:
            z.writestr("word/footnotes.xml", f'<w:footnotes {w}>{notes}</w:footnotes>')
    return buf.getvalue()


class Dokument(unittest.TestCase):
    def test_author_year_thesis(self):
        a = document.analyse(THESIS)
        self.assertTrue(a["has_bibliography"])
        self.assertEqual(a["style"], "author-year")
        self.assertEqual(a["entries"], 5)
        self.assertIn("10.1038/s41586-020-2649-2", a["refs_text"])
        self.assertNotIn("Interview guide", a["refs_text"])          # Anhang gehört nicht dazu
        self.assertIn("Array programming with NumPy. Nature", a["refs_text"])  # Umbruch zusammengefügt
        missing = [m["label"] for m in a["missing_in_list"]]
        self.assertEqual(missing, ["Brown (2017)"])
        uncited = [u["label"] for u in a["uncited"]]
        self.assertEqual(len(uncited), 1)
        self.assertIn("nobody cites", uncited[0])
        claim = next(c for c in a["claims"] if c["surname"] == "Smith")
        self.assertIn("Language models invent sources", claim["sentence"])

    def test_year_differs_between_text_and_list(self):
        a = document.analyse(THESIS.replace("(Smith & Jones, 2019)", "(Smith & Jones, 2020)"))
        years = a["year_mismatch"]
        self.assertEqual(len(years), 1)
        self.assertEqual((years[0]["cited_year"], years[0]["list_year"]), ("2020", "2019"))
        self.assertIn("Invented references", years[0]["list_label"] + a["refs_text"])
        self.assertEqual([m["label"] for m in a["missing_in_list"]], ["Brown (2017)"])  # nicht zusätzlich als fehlend
        self.assertTrue(all("Smith" not in u["label"] for u in a["uncited"]))      # und nicht als nie zitiert
        self.assertEqual(document.analyse(THESIS)["year_mismatch"], [])

    def test_year_letters_keep_two_works_apart(self):
        text = ("1 Introduction\n\nFirst finding (Smith, 2020a). Second finding (Smith, 2020b). Lee (2019) agrees.\n\n"
                "References\n\nLee, K. (2019). Citation errors in student essays. Computers & Education, 170, 104210.\n"
                "Smith, J. (2020a). Invented references in language model output. Journal of Results, 12(3), 45-67.\n"
                "Smith, J. (2020b). Fabricated citations in chat answers. Journal of Results, 12(4), 80-99.\n"
                "Taylor, R. (2015). A source nobody cites. Oxford University Press.\n")
        a = document.analyse(text)
        self.assertEqual(sorted((c["label"], c["entry"]) for c in a["claims"] if c["surname"] == "Smith"),
                         [("Smith, 2020a", 1), ("Smith, 2020b", 2)])
        self.assertEqual([u["label"] for u in a["uncited"]], ["A source nobody cites"])
        # Ohne Buchstaben im Verzeichnis (oder im Text) bleibt die Zuordnung wie bisher: kein falsches „fehlt“.
        plain = document.analyse(THESIS.replace("(Smith & Jones, 2019)", "(Smith & Jones, 2019a)"))
        self.assertEqual([m["label"] for m in plain["missing_in_list"]], ["Brown (2017)"])
        self.assertTrue(all("Invented" not in u["label"] for u in plain["uncited"]))

    def test_umlaut_and_transliteration_are_one_name(self):
        a = document.analyse(THESIS.replace("Müller 2018, S. 12", "Mueller 2018, S. 12"))
        self.assertEqual([m["label"] for m in a["missing_in_list"]], ["Brown (2017)"])
        entry = next(r for r in a["refs"] if (r.get("authors") or [""])[0] == "Müller")
        self.assertEqual(entry.get("cited_pages"), ["12"])  # S. 12 geht mit in die Prüfung des Eintrags
        other = document.analyse(THESIS.replace("Müller 2018, S. 12", "Miller 2018, S. 12"))
        self.assertIn("Miller, 2018", [m["label"] for m in other["missing_in_list"]])

    def test_numeric_thesis(self):
        a = document.analyse(NUMERIC)
        self.assertEqual(a["style"], "numeric")
        self.assertEqual(a["entries"], 4)
        self.assertEqual([m["label"] for m in a["missing_in_list"]], ["[6]"])
        self.assertEqual(len(a["uncited"]), 1)
        self.assertIn("Deep residual", a["uncited"][0]["label"])

    def test_docx_with_footnotes(self):
        data = docx(["Benthic checks sources.", "References",
                     "Doe, J. (2020). A study of things. Journal of Things, 1, 1-2.",
                     "Roe, R. (2021). Another study. Journal of Things, 2, 3-4."],
                    footnotes=["Vgl. Doe (2020), S. 4."])
        text = document.extract_text("arbeit.docx", data)
        a = document.analyse(text)
        self.assertEqual(a["entries"], 2)
        self.assertEqual(a["citations"], 1)          # das Zitat steht in der Fußnote
        self.assertEqual(len(a["uncited"]), 1)

    def test_docx_small_caps_read_as_capitals(self):
        """DIN-Nachnamen in Kapitälchen (direkt oder über eine Zeichenvorlage) bleiben als DIN erkennbar."""
        from benthic import style
        w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        def run(text, rpr=""):
            return f'<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>'
        small = "<w:rPr><w:smallCaps/></w:rPr>"
        styled = '<w:rPr><w:rStyle w:val="Autor"/></w:rPr>'
        off = '<w:rPr><w:rStyle w:val="Autor"/><w:smallCaps w:val="0"/></w:rPr>'
        entry = (run("LeCun", small) + run(", Yann ; ") + run("Bengio", styled) + run(", Yoshua ; ")
                 + run("Hinton", small) + run(", Geoffrey: Deep learning. In: ") + run("Nature", off)
                 + run(" Bd. 521 (2015), Nr. 7553, S. 436–444"))
        styles = (f'<w:styles {w}><w:style w:type="character" w:styleId="Kapitaelchen"><w:rPr><w:smallCaps/>'
                  '</w:rPr></w:style><w:style w:type="character" w:styleId="Autor">'
                  '<w:basedOn w:val="Kapitaelchen"/></w:style></w:styles>')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", f"<w:document {w}><w:body><w:p>{run('Literaturverzeichnis')}</w:p>"
                                            f"<w:p>{entry}</w:p></w:body></w:document>")
            z.writestr("word/styles.xml", styles)
        text = document.extract_text("arbeit.docx", buf.getvalue())
        line = text.splitlines()[1]
        self.assertTrue(line.startswith("LECUN, Yann ; BENGIO, Yoshua ; HINTON, Geoffrey: Deep learning. In: Nature"))
        self.assertEqual(style.guess(line), "din")

    def test_rejects_unknown_types(self):
        with self.assertRaises(ValueError):
            document.extract_text("notes.odt", b"x")
        with self.assertRaises(ValueError):
            document.extract_text("broken.docx", b"not a zip")

    def test_without_bibliography(self):
        a = document.analyse("Just some text (Smith, 2020) without a list.")
        self.assertFalse(a["has_bibliography"])
        self.assertEqual(a["entries"], 0)


class Route(unittest.TestCase):
    """/api/document: Upload, Abgleich im Lauf, Fehlermeldungen (die Prüfung selbst ist ersetzt)."""

    def setUp(self):
        import os
        import tempfile
        from unittest import mock
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("BENTHIC_DATA_DIR")
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name
        from benthic import app as appmod, verify
        self.appmod = appmod
        fake = lambda ref, email="", on_event=None: {"identifier": ref.get("label"), "verdict": "verified",
                                                     "status": "verified", "note": "", "score": 100, "flags": []}
        self.patch = mock.patch.object(verify, "verify_ref", side_effect=fake)
        self.patch.start()
        self.client = ui_client(appmod.app)

    def tearDown(self):
        import os
        self.patch.stop()
        if self._old is None:
            os.environ.pop("BENTHIC_DATA_DIR", None)
        else:
            os.environ["BENTHIC_DATA_DIR"] = self._old
        self.tmp.cleanup()

    def wait(self, job_id):
        import time
        for _ in range(100):
            job = self.client.get(f"/api/jobs/{job_id}").get_json()
            if job["status"] != "running":
                return job
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_upload_starts_a_check_with_the_cross_check(self):
        data = {"file": (io.BytesIO(THESIS.encode()), "thesis.txt")}
        r = self.client.post("/api/document", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_json())
        j = r.get_json()
        self.assertEqual(j["total"], 5)
        self.assertEqual(j["document"]["name"], "thesis.txt")
        job = self.wait(j["job_id"])
        self.assertEqual([m["label"] for m in job["document"]["missing_in_list"]], ["Brown (2017)"])
        self.assertEqual(sorted(r["order"] for r in job["results"]), [0, 1, 2, 3, 4])  # Eintragsnummern

    def test_annotated_docx_route(self):
        from unittest import mock
        from benthic import verify

        def fake_with_diff(ref, email="", on_event=None):
            label = ref.get("label") or ""
            if "Invented" in label:
                return {
                    "identifier": label,
                    "verdict": "mismatch",
                    "status": "mismatch",
                    "note": "Jahr weicht ab.",
                    "score": 40,
                    "flags": ["year_mismatch"],
                    "field_diffs": {"year": {"claimed": "2019", "found": "2020", "status": "diff"}},
                }
            return {"identifier": label, "verdict": "verified", "status": "verified", "note": "", "score": 100, "flags": []}

        raw_docx = docx(THESIS.splitlines())
        with mock.patch.object(verify, "verify_ref", side_effect=fake_with_diff):
            r = self.client.post("/api/document", data={"file": (io.BytesIO(raw_docx), "thesis.docx")},
                                 content_type="multipart/form-data")
            self.assertEqual(r.status_code, 200, r.get_json())
            job_id = r.get_json()["job_id"]
            self.wait(job_id)
        resp = self.client.get(f"/api/jobs/{job_id}/annotated.docx")
        self.assertEqual(resp.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(resp.data)) as z:
            self.assertIn("word/comments.xml", z.namelist())
            comments = z.read("word/comments.xml").decode("utf-8")
            self.assertIn("Brown (2017)", comments)
            self.assertIn("year: 2019 -&gt; 2020", comments)

    def test_errors(self):
        r = self.client.post("/api/document", data={"file": (io.BytesIO(b"x"), "a.odt")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/document", data={"file": (io.BytesIO(b"No references here."), "a.txt")},
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/document", json={"path": "/nonexistent/thesis.pdf"})
        self.assertEqual(r.status_code, 400)


class WritingSignals(unittest.TestCase):
    def test_secondary_citations(self):
        text = "This phenomenon was observed early (Müller, 2010, zit. nach Schmidt, 2020). Later work confirmed this."
        signals = document.find_writing_signals(text, [])
        self.assertEqual(len(signals["secondary_citations"]), 1)
        self.assertIn("zit. nach", signals["secondary_citations"][0]["match"])

    def test_uncited_claims(self):
        # 1. Uncited empirical sentence with percentages
        body = "Studies show that over 65% of students rely on language assistants. They find them helpful."
        signals = document.find_writing_signals(body, [])
        self.assertEqual(len(signals["uncited_claims"]), 1)
        self.assertIn("65%", signals["uncited_claims"][0]["sentence"])

        # 2. Self data should be ignored
        body_self = "In Table 3 our survey shows that 65% of students agreed with the statement."
        signals_self = document.find_writing_signals(body_self, [])
        self.assertEqual(len(signals_self["uncited_claims"]), 0)

        # 3. Cited sentence should not be flagged
        body_cited = "Studies show that 65% of students agree (Smith, 2020). Other results differ."
        cites = [{"kind": "author-year", "surname": "Smith", "year": "2020", "pos": 41}]
        signals_cited = document.find_writing_signals(body_cited, cites)
        self.assertEqual(len(signals_cited["uncited_claims"]), 0)

    def test_annotate_docx(self):
        doc_bytes = docx([
            "First paragraph with an empirical claim.",
            "Second paragraph mentioning Lee et al. (2021).",
        ])
        notes = [
            {"needle": "Lee et al. (2021)", "text": "[RETRACTED] This paper was retracted.", "author": "Benthic"},
            {"needle": "empirical claim", "text": "Missing citation", "author": "Benthic"},
        ]
        annotated = document.annotate_docx(doc_bytes, notes)
        self.assertGreater(len(annotated), len(doc_bytes))
        with zipfile.ZipFile(io.BytesIO(annotated)) as z:
            self.assertIn("word/comments.xml", z.namelist())
            comments_xml = z.read("word/comments.xml").decode("utf-8")
            self.assertIn("This paper was retracted", comments_xml)
            self.assertIn("Missing citation", comments_xml)
            doc_xml = z.read("word/document.xml").decode("utf-8")
            self.assertIn("commentRangeStart", doc_xml)
            self.assertIn("commentReference", doc_xml)


if __name__ == "__main__":
    unittest.main()
