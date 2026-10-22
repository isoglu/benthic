"""Werte statt nur Felder: Band, Heft, Seiten, Autor:innen, eine DOI je Werk und eindeutige DOI-Vorschläge (offline)."""

import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

from benthic import refs, verify


def text_ref(line):
    return refs.parse(line)[0]


class Feldwerte(unittest.TestCase):
    ACTUAL = {"title": "Invented references in language model output", "authors": ["Smith", "Jones", "Brown"],
              "author_count": 3, "year": "2019", "volume": "12", "issue": "3", "pages": "45–67"}

    def diffs(self, line, **actual):
        r = text_ref(line)
        return verify.field_diffs(verify._claimed(r), {**self.ACTUAL, **actual})

    def test_volume_issue_and_pages_differ(self):
        d = self.diffs("Smith, J., Jones, K., & Brown, L. (2019). Invented references in language model output. "
                       "Journal of Results, 13(4), 145-167.")
        self.assertEqual((d["volume"]["claimed"], d["volume"]["found"]), ("13", "12"))
        self.assertEqual((d["issue"]["claimed"], d["issue"]["found"]), ("4", "3"))
        self.assertEqual((d["pages"]["claimed"], d["pages"]["found"]), ("145–167", "45–67"))
        self.assertTrue(all(d[k]["status"] == "warn" for k in ("volume", "issue", "pages")))

    def test_same_values_in_other_forms_stay_silent(self):
        for line, actual in [
            ("Smith, J., Jones, K., & Brown, L. (2019). Invented references in language model output. Journal, 12(3-4), 45-7.", {}),
            ("[1] J. Smith, K. Jones, and L. Brown, \"Invented references in language model output,\" Journal, vol. 12, "
             "no. 3, pp. 45-67, 2019.", {}),
            ("Smith, J., Jones, K., & Brown, L. (2019). Invented references in language model output. Journal, 12(3), 1-12.",
             {"pages": "104210"}),                                       # Artikelnummer im Werk, Seiten 1-12 in der Angabe
            ("Smith, J., Jones, K., & Brown, L. (2019). Invented references in language model output. Journal, 3(12), 45-67.",
             {}),                                                        # falsch gelesen? Der echte Band 12 steht in der Angabe
        ]:
            d = self.diffs(line, **actual)
            self.assertFalse({"volume", "issue", "pages"} & set(d), line)

    def test_doi_and_pii_are_not_pages(self):
        r = text_ref("Dyer, J. (2002). Title of the paper. European Journal of Operational Research, 137(2), 1-12. "
                     "https://doi.org/10.1016/s0377-2217(01)00049-2")
        self.assertEqual((r["volume"], r["issue"], r["pages"]), ("137", "2", "1–12"))
        bare = text_ref("Dyer, J. (2002). Title of the paper. European Journal of Operational Research. "
                        "https://doi.org/10.1016/s0377-2217(01)00049-2")
        self.assertFalse(bare.get("pages") or bare.get("issue") or bare.get("volume"))

    def test_author_order_first_author_and_count(self):
        d = self.diffs("Jones, K., Smith, J., & Brown, L. (2019). Invented references in language model output.")
        self.assertEqual(d["authors"]["status"], "warn")                  # andere Reihenfolge, andere Erstautorin
        d = self.diffs("Smith, J., Jones, K., Brown, L., et al. (2019). Invented references in language model output.")
        self.assertEqual(d["author_count"]["found"], "3")                 # „et al.“, aber es gibt niemanden mehr
        bib = refs.parse("@article{s, author={Smith, J. and Jones, K.}, title={Invented references in language model "
                         "output}, year={2019}}")[0]
        d = verify.field_diffs(verify._claimed(bib), self.ACTUAL)
        self.assertEqual((d["author_count"]["claimed"], d["author_count"]["found"]), ("2", "3"))

    def test_authors_in_other_spellings_stay_silent(self):
        actual = {**self.ACTUAL, "authors": ["Müller", "van der Walt", "Brown"], "author_count": 5}
        r = text_ref("Mueller, J., Walt, S., & Brown, L. (2019). Invented references in language model output.")
        d = verify.field_diffs(verify._claimed(r), actual)
        self.assertEqual(d["authors"]["status"], "ok")
        r = text_ref("Muller, J., et al. (2019). Invented references in language model output.")
        d = verify.field_diffs(verify._claimed(r), actual)
        self.assertEqual(d["authors"]["status"], "ok")
        self.assertNotIn("author_count", d)
        self.assertEqual(verify.field_diffs({"authors": ["Miller"]}, actual)["authors"]["status"], "fail")
        # Ein Verzeichnis mit nur der ersten Person oder einem Tippfehler im Namen ist kein Fehler der Angabe.
        claimed = verify._claimed(text_ref("Homburg, C., Hoyer, W. D., & Steenkamp, J. (2005). Customers' reactions."))
        for names in (["Homburg"], ["Homburg", "Hoyer", "Steenkamps"]):
            d = verify.field_diffs(claimed, {"authors": names, "author_count": len(names)})
            self.assertEqual(d["authors"]["status"], "ok", names)

    def test_no_value_diffs_against_an_unconfirmed_hit(self):
        claimed = verify._claimed(text_ref("Smith, J. (2019). Invented references in language model output. Journal, 13(4), 145-167."))
        self.assertIn("volume", verify.field_diffs(claimed, self.ACTUAL))
        self.assertFalse({"volume", "issue", "pages"} & set(verify.field_diffs(claimed, self.ACTUAL, values=False)))

    def test_cited_page_outside_the_article(self):
        claimed = {"title": self.ACTUAL["title"], "cited_pages": ["14", "50"]}
        self.assertEqual(verify.field_diffs(claimed, self.ACTUAL)["cited_page"]["claimed"], "14")
        self.assertNotIn("cited_page", verify.field_diffs({**claimed, "cited_pages": ["50"]}, self.ACTUAL))
        self.assertNotIn("cited_page", verify.field_diffs(claimed, {**self.ACTUAL, "pages": None}))

    def test_year_one_off_is_a_diff(self):
        d = verify.field_diffs({"year": "2018"}, self.ACTUAL)
        self.assertEqual(d["year"]["status"], "warn")
        self.assertEqual(verify.field_diffs({"year": "2019"}, self.ACTUAL)["year"]["status"], "ok")


def work(doi, title, source="Crossref", **kw):
    return {"doi": doi, "title": title, "authors": ["He", "Zhang", "Ren", "Sun"], "year": "2016", "source": source,
            "type": "journal-article", "retracted": False, **kw}


class Suche(unittest.TestCase):
    TITLE = "Deep residual learning for image recognition"
    REF = {"format": "text", "raw": "He, K., Zhang, X., Ren, S., & Sun, J. (2016). Deep residual learning for image recognition.",
           "title": TITLE, "authors": ["He", "Zhang", "Ren", "Sun"], "year": "2016", "doi": None}

    def run_search(self, crossref, openalex=()):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(verify, "_search_crossref", return_value=list(crossref)))
            stack.enter_context(mock.patch.object(verify, "_search_openalex", return_value=list(openalex)))
            stack.enter_context(mock.patch.object(verify, "lookup", return_value=None))
            for name in ("_search_arxiv", "_search_semanticscholar", "_search_dblp", "_search_openlibrary", "_search_dnb"):
                stack.enter_context(mock.patch.object(verify, name, return_value=[]))
            stack.enter_context(mock.patch.object(verify, "_web_source", return_value=None))
            return verify.verify_ref(dict(self.REF))

    def test_notices_are_not_the_work(self):
        notice = verify._from_crossref({"DOI": "10.1/fix", "title": ["Correction to: " + self.TITLE], "type": "journal-article",
                                        "author": [{"family": "He"}], "issued": {"date-parts": [[2016]]},
                                        "update-to": [{"type": "correction", "DOI": "10.1/real"}]})
        self.assertTrue(notice["notice"])
        res = self.run_search([notice, work("10.1/real", self.TITLE)])
        self.assertEqual((res["verdict"], res["doi"]), ("verified", "10.1/real"))
        res = self.run_search([notice])
        self.assertNotEqual(res["doi"], "10.1/fix")
        # Wer die Antwort selbst zitiert, findet sie.
        reply = work("10.1/reply", "Reply to: comments on residual learning", notice=True)
        with mock.patch.object(verify, "_search_crossref", return_value=[reply]), \
                mock.patch.object(verify, "_search_openalex", return_value=[]), \
                mock.patch.object(verify, "lookup", return_value=None):
            best, _s, _sim = verify.search({"title": "Reply to: comments on residual learning"})
        self.assertEqual(best["doi"], "10.1/reply")

    def test_two_published_works_mean_no_doi(self):
        res = self.run_search([work("10.1109/cvpr.2016.90", self.TITLE)], [work("10.9/journal.2016.1", self.TITLE, "OpenAlex")])
        self.assertEqual(res["verdict"], "uncertain")
        self.assertIsNone(res["doi"])
        self.assertIn("10.1109/cvpr.2016.90", res["note_parts"][0]["values"]["dois"])

    def test_preprint_copy_is_not_a_rival(self):
        res = self.run_search([work("10.1109/cvpr.2016.90", self.TITLE)],
                              [work("10.48550/arXiv.1512.03385", self.TITLE, "OpenAlex", type="preprint"),
                               work("10.5281/zenodo.1", self.TITLE, "OpenAlex", type="dataset")])
        self.assertEqual((res["verdict"], res["doi"]), ("verified", "10.1109/cvpr.2016.90"))


def result(order, title, doi=None, verdict="verified", claimed_doi=None, **printed):
    return {"order": order, "verdict": verdict, "doi": doi, "claimed_doi": claimed_doi if claimed_doi is not None else doi,
            "input": title, "claimed": {"title": title, **printed}}


class EineDoiEinWerk(unittest.TestCase):
    def codes(self, results):
        from benthic import style
        return [n["code"] for n in style.consistency(style.entries_from_results(results))]

    def test_same_doi_with_other_year_volume_or_pages(self):
        from benthic import style
        rows = [result(0, "Array programming with NumPy", "10.1038/x", year="2020", volume="585", pages="357-362"),
                result(4, "Array programming with NumPy", "10.1038/X", year="2018", volume="586", pages="358–362")]
        notes = style.check_list(rows)["list"]
        self.assertEqual([n["code"] for n in notes].count("doi_conflict"), 3)
        self.assertIn("2020 (Nr. 1), 2018 (Nr. 5)", " ".join(style.tr_part(n, "de") for n in notes))
        self.assertIn("2020 (No. 1), 2018 (No. 5)", " ".join(style.tr_part(n, "en") for n in notes))

    def test_m5_one_year_apart_or_first_page_only_is_no_conflict(self):
        # Online 2019, gedruckt 2020; „357“ ist der Anfang von „357-362“.
        rows = [result(0, "Array programming with NumPy", "10.1038/x", year="2019", pages="357"),
                result(1, "Array programming with NumPy", "10.1038/x", year="2020", pages="357-362")]
        self.assertEqual(self.codes(rows), [])
        rows[0]["claimed"]["pages"] = "358"
        self.assertEqual(self.codes(rows), ["doi_conflict"])

    def test_two_titles_one_doi_and_one_range_on_two_works(self):
        rows = [result(0, "Deep learning", "10.1038/nature14539"),
                result(1, "Deep learning in neural networks: an overview", "10.1038/nature14539"),
                result(2, "Invented references in language model output", "10.1/a", journal="Journal of Results", volume="12",
                       pages="45-67"),
                result(3, "Fabricated citations in chat answers", "10.1/b", journal="Journal of Results", volume="12",
                       pages="45–67")]
        self.assertEqual(self.codes(rows), ["same_doi", "same_pages"])

    def test_m5_same_pages_in_two_journals_or_without_journal_stay_silent(self):
        rows = [result(0, "Invented references in language model output", "10.1/a", journal="Sustainability", volume="11",
                       pages="1-12"),
                result(1, "Fabricated citations in chat answers", "10.1/b", journal="Scientific Reports", volume="11",
                       pages="1-12"),
                result(2, "Another work entirely", "10.1/c", volume="11", pages="1-12")]
        self.assertEqual(self.codes(rows), [])
        line = "Smith, J. (2019). Invented references. Journal of Results, 12(3), 45-67. https://doi.org/10.1/a"
        self.assertEqual(text_ref(line)["journal"], "Journal of Results")  # gelesen aus dem Text vor dem Band

    def test_doi_that_names_volume_issue_and_first_page(self):
        doi = "10.1509/jmkg.67.4.30.18685"
        self.assertEqual(self.codes([result(0, "Title", doi, volume="68", issue="4", pages="30-45")]), ["doi_says"])
        self.assertEqual(self.codes([result(0, "Title", doi, volume="67", issue="4", pages="30-45")]), [])
        self.assertEqual(self.codes([result(0, "Title", "10.1016/j.jbusres.2019.01.001", volume="98", pages="1-10")]), [])

    def test_consistent_lists_stay_silent(self):
        rows = [result(0, "Array programming with NumPy", "10.1038/x", year="2020", volume="585", pages="357-362"),
                result(1, "Array programming with NumPy.", None, verdict="uncertain", year="2020", volume="585", pages="357–62"),
                result(2, "Thinking, fast and slow", "10.1/book", year="2011"),
                result(3, "Thinking, fast and slow: the book with a long subtitle", "10.1/book", year="2011"),
                result(4, "A paper on page one", "10.1/c", volume="3", pages="1-1"),
                result(5, "Another paper on page one", "10.1/d", volume="3", pages="1-1"),
                result(6, "Some invented work", "10.1038/x", verdict="not_found", claimed_doi="")]  # nächster Treffer, nicht ihre DOI
        self.assertEqual(self.codes(rows), [])

    def test_parse_keeps_a_doi_printed_twice_with_another_year(self):
        text = ("Harris, C. (2020). Array programming with NumPy. Nature, 585, 357-362. https://doi.org/10.1038/s41586-020-2649-2\n"
                "Harris, C. (2019). Array programming with NumPy. Nature, 585, 357-362. https://doi.org/10.1038/s41586-020-2649-2\n"
                "Harris, C. (2020). Array programming with NumPy. Nature, 585, 357-362. https://doi.org/10.1038/s41586-020-2649-2\n")
        self.assertEqual([r["year"] for r in refs.parse(text)], ["2020", "2019"])


class StapelUndProjekt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_batch_finds_one_doi_with_two_years_across_files(self):
        from benthic import batch
        numpy = "Array programming with NumPy"
        rows = [{"name": "ada.pdf", "job": {"results": [result(0, numpy, "10.1038/x", year="2020", pages="357-362")]}},
                {"name": "bob.pdf", "job": {"results": [result(3, numpy, "10.1038/x", year="2018", pages="357–362")]}},
                {"name": "cy.pdf", "job": {"results": [result(1, numpy, "10.1038/x", year="2020")]}}]
        notes = batch.public({"id": "b", "rows": rows})["doi_conflicts"]
        self.assertEqual([n["code"] for n in notes], ["doi_conflict"])
        self.assertIn("2020 (ada.pdf, cy.pdf), 2018 (bob.pdf)", batch.style.tr_part(notes[0], "de"))
        rows[1]["job"]["results"][0]["claimed"]["year"] = "2019"  # ein Jahr daneben: online und gedruckt
        self.assertEqual(batch.doi_conflicts({"rows": rows}), [])

    def test_project_compares_the_works_of_a_project(self):
        from benthic import projects
        pid = projects.create("Thesis")["id"]
        a = result(0, "Invented references in language model output", "10.1234/a", journal="Journal of Results", volume="12",
                   pages="45-67")
        b = result(0, "Fabricated citations in chat answers", "10.1234/b", journal="Journal of Results", volume="12",
                   pages="45-67")
        projects.record_results(pid, "verify", [a], run="kapitel1.docx")
        projects.record_results(pid, "verify", [b], run="kapitel3.docx")
        notes = projects.detail(pid)["conflicts"]
        self.assertEqual([n["code"] for n in notes], ["same_pages"])                   # ein Bereich, zwei Werke
        self.assertIn("(kapitel1.docx)", projects.style.tr_part(notes[0], "de"))
        self.assertNotIn("printed", projects.bundle(pid)["items"][0])  # bleibt auf diesem Mac

    def test_n2_a_fixed_reference_in_a_new_file_is_no_conflict(self):
        from benthic import projects
        pid = projects.create("Thesis")["id"]
        numpy = "Array programming with NumPy"
        projects.record_results(pid, "verify", [result(0, numpy, "10.1038/x", pages="357-326")], run="thesis_v1.docx")
        projects.record_results(pid, "verify", [result(0, numpy, "10.1038/x", pages="357-362")], run="thesis_v2.docx")
        self.assertEqual(projects.detail(pid)["conflicts"], [])
        # Eine Prüfung aus dem Projekt selbst liest Jahr und Titel aus dem Werk, nicht aus einer Arbeit: sie hinterlässt
        # keine Fassung, die einen Seitenbereich mit einem anderen Werk teilen könnte.
        kw = {"journal": "Journal of Results", "volume": "12", "pages": "45-67"}
        projects.record_results(pid, "verify", [result(0, "Invented references in output", "10.1234/a", **kw)], run="v2.docx")
        projects.record_results(pid, "verify", [result(0, "Fabricated citations in answers", "10.1234/b", **kw)], source="project")
        self.assertEqual(projects.detail(pid)["conflicts"], [])


class Audit115(unittest.TestCase):
    """Befunde M1 bis M6 aus dem Release-Audit 1.1.5: richtige Angaben bleiben ohne Hinweis."""

    def authors(self, claimed, record, **actual):
        return verify.field_diffs({"authors": claimed}, {"authors": record, "author_count": len(record), **actual})

    def test_m1_different_surnames_stay_apart(self):
        from benthic import document
        text = ("1 Introduction\n\nOne finding (Xu, 2020, p. 3). Another finding (Xue, 2020, p. 9). Lee (2019) agrees.\n\n"
                "References\n\nLee, K. (2019). Citation errors in student essays. Computers & Education, 170, 104210.\n"
                "Xu, W. (2020). Invented references in language model output. Journal of Results, 12(3), 45-67.\n"
                "Xue, L. (2020). Fabricated citations in chat answers. Journal of Results, 12(4), 80-99.\n"
                "Taylor, R. (2015). A source nobody cites. Oxford University Press.\n")
        a = document.analyse(text)
        self.assertEqual([u["label"] for u in a["uncited"]], ["A source nobody cites"])  # Xue nicht „nie zitiert“
        pages = {r["authors"][0]: r.get("cited_pages") for r in a["refs"] if r.get("authors")}
        self.assertEqual((pages["Xu"], pages["Xue"]), (["3"], ["9"]))                    # S. 9 gehört zu Xue
        for one, other in (("Xu", "Xue"), ("Yu", "Yue"), ("Bauer", "Baur"), ("Do", "Doe"), ("王伟", "李娜"), ("Иванов", "Петров")):
            self.assertFalse(refs.same_name(one, other), (one, other))
            self.assertEqual(self.authors([one], [other])["authors"]["status"], "fail", (one, other))
        self.assertEqual(self.authors(["王伟", "李娜"], ["王伟", "李娜"])["authors"]["status"], "ok")
        self.assertTrue(refs.same_name("Müller", "Mueller") and refs.same_name("Müller", "Muller"))  # nur als zweiter Versuch

    def test_m2_identical_author_lists_are_not_reordered(self):
        for names in (["Zhou", "Hou"], ["Wang", "Ng"], ["Zhu", "Hu"], ["Zhang", "Hang"], ["Johnson", "Son"],
                      ["Hansen", "Sen"], ["Chan", "An"]):
            self.assertEqual(self.authors(names, names)["authors"]["status"], "ok", names)
        self.assertEqual(self.authors(["Hou", "Zhou"], ["Zhou", "Hou"])["authors"]["status"], "warn")
        self.assertEqual(self.authors(["Son"], ["Johnson"])["authors"]["status"], "fail")      # kein Namensteil davor
        self.assertEqual(self.authors(["Cruz"], ["de la Cruz"])["authors"]["status"], "ok")    # Namensteil davor

    def test_m3_article_numbers_are_not_page_ranges(self):
        record = {"title": "T", "pages": "2232–2232"}                                      # so speichert OpenAlex sie
        self.assertEqual(verify.field_diffs({"cited_pages": ["5"], "pages": "1-12"}, record), {})
        self.assertEqual(verify.field_diffs({"cited_pages": ["20"], "pages": "1-12"}, {"pages": "1–15"}), {})
        self.assertEqual(verify.field_diffs({"pages": "2233"}, record)["pages"]["found"], "2232–2232")
        self.assertEqual(verify.field_diffs({"cited_pages": ["14"]}, {"pages": "45–67"})["cited_page"]["claimed"], "14")

    def test_m4_a_working_paper_is_no_rival(self):
        search = Suche()
        title = Suche.TITLE
        article = work("10.1287/mnsc.2016.1", title)
        for copy in (work("10.3386/w20001", title, type="report"), work("10.5281/zenodo.9", title, "OpenAlex", type="other"),
                     work("10.6084/m9.figshare.1", title, "OpenAlex", type="repository")):
            res = search.run_search([article], [copy])
            self.assertEqual((res["verdict"], res["doi"]), ("verified", "10.1287/mnsc.2016.1"), copy["doi"])
        rival = work("10.1109/other.2016.2", title, "OpenAlex")
        self.assertEqual(search.run_search([article], [rival])["verdict"], "uncertain")  # zwei Artikel: keine DOI
        # Falsche DOI in der Angabe: korrigiert, nicht „Passt nicht“, auch wenn es ein Arbeitspapier gibt.
        other = {"title": "A study of something else", "doi": "10.9/unrelated", "authors": ["Other"], "year": "2001",
                 "retracted": False}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(verify, "lookup", side_effect=lambda doi, email="": other if doi == other["doi"] else None))
            stack.enter_context(mock.patch.object(verify, "_search_crossref", return_value=[article]))
            stack.enter_context(mock.patch.object(verify, "_search_openalex", return_value=[work("10.3386/w20001", title, type="report")]))
            for name in ("_search_arxiv", "_search_semanticscholar", "_search_dblp", "_search_openlibrary", "_search_dnb"):
                stack.enter_context(mock.patch.object(verify, name, return_value=[]))
            res = verify.verify_ref({**Suche.REF, "doi": "10.9/unrelated"})
        self.assertEqual((res["verdict"], res["doi"]), ("corrected", "10.1287/mnsc.2016.1"))

    def test_m6_et_al_against_a_one_author_record(self):
        claimed = verify._claimed(text_ref("Larkin, I., et al. (2014). The cost of high-powered incentives."))
        self.assertNotIn("author_count", verify.field_diffs(claimed, {"authors": ["Larkin"], "author_count": 1}))
        three = verify._claimed(text_ref("Smith, J., Jones, K., Brown, L., et al. (2019). Invented references."))
        self.assertNotIn("author_count", verify.field_diffs(three, {"authors": ["Smith", "Jones"], "author_count": 2}))
        self.assertEqual(verify.field_diffs(three, {"authors": ["Smith", "Jones", "Brown"], "author_count": 3})
                         ["author_count"]["found"], "3")

    def test_band_in_a_title_and_ris_end_page(self):
        self.assertNotIn("volume", refs.printed_fields("Lee, K. (2020). Radios in the 5 GHz band 3 times faster. Proceedings."))
        self.assertEqual(refs.printed_fields("Meier, K. (2019): Preise. In: Handbuch, Band 3, S. 45-67.")["volume"], "3")
        for sp in ("357-362", "357"):
            ris = f"TY  - JOUR\nTI  - Array programming with NumPy\nSP  - {sp}\nEP  - 362\nER  - \n"
            self.assertEqual(refs.parse(ris)[0]["pages"], "357–362")


class Audit115Runde2(unittest.TestCase):
    """Zweite Runde des Audits: Seiten nur aus DOIs, die sie sicher tragen, und keine strengeren Regeln als verify."""

    def test_doi_decoding_only_for_known_schemes(self):
        known = {  # DOI: (Band, Heft, erste Seite) laut Crossref
            "10.1509/jmkr.39.4.421.19114": ("39", "4", "421"),          # AMA, Journal of Marketing Research
            "10.1037/0003-066X.49.12.997": ("49", "12", "997"),         # APA, American Psychologist
            "10.1037//0003-066x.49.12.997": ("49", "12", "997"),        # so schreibt OpenAlex sie
            "10.1287/mksc.20.1.1.10197": ("20", "1", "1"),              # INFORMS, Marketing Science
            "10.1146/annurev.psych.52.1.1": ("52", "1", "1"),           # Annual Review of Psychology
            "10.1257/jep.29.3.3": ("29", "3", "3"),                     # AEA, Journal of Economic Perspectives
            "10.1210/jc.87.5.2090": ("87", "5", "2090"),                # Endocrine Society, neue Form
            "10.1002/(SICI)1097-0266(199709)18:8<637::AID-SMJ914>3.0.CO;2-#": ("18", "8", "637"),
        }
        for doi, (volume, issue, page) in known.items():
            self.assertEqual(refs.doi_fields(doi), {"volume": volume, "issue": issue, "first_page": page}, doi)
        for doi in ("10.1210/jcem.87.5.8477",          # 8477 ist eine Manuskriptnummer, das Werk steht auf S. 2033-2041
                    "10.9999/abc.12.3.45", "10.1016/j.jbusres.2019.01.001", "10.1287/mksc.14.3.G122"):
            self.assertEqual(refs.doi_fields(doi), {}, doi)

    def test_jcem_manuscript_number_is_no_first_page(self):
        from benthic import style
        jcem = [result(0, "Title of the JCEM article", "10.1210/jcem.87.5.8477", volume="87", issue="5", pages="2033-2041")]
        self.assertEqual(style.consistency(style.entries_from_results(jcem)), [])
        jc = [result(0, "Title of the JC article", "10.1210/jc.87.5.2090", volume="87", issue="5", pages="2024-2041")]
        self.assertEqual([n["code"] for n in style.consistency(style.entries_from_results(jc))], ["doi_says"])

    def test_n1_dataset_in_the_title_is_no_copy(self):
        search = Suche()
        title = "A global dataset of crop yields for major crops from 1981 to 2016"
        search.REF = {**Suche.REF, "title": title, "raw": f"Iizumi, T. (2016). {title}. Scientific Data, 7, 97."}
        article = work("10.1038/s41597-020-0433-7", title)
        for copy in (work("10.6084/m9.figshare.11903604", title, "OpenAlex", type="dataset"),
                     work("10.5281/zenodo.123", title, "OpenAlex", type="dataset")):
            res = search.run_search([article], [copy])
            self.assertEqual((res["verdict"], res["doi"]), ("verified", "10.1038/s41597-020-0433-7"), copy["doi"])
        res = search.run_search([article], [work("10.1038/s41598-021-1", title, "OpenAlex")])
        self.assertEqual(res["verdict"], "uncertain")                     # zwei Artikel bleiben mehrdeutig

    def test_n3_same_rules_as_the_record_check(self):
        from benthic import style
        def codes(*rows):
            return [n["code"] for n in style.consistency(style.entries_from_results(list(rows)))]
        plos = "Title of a PLOS paper"
        self.assertEqual(codes(result(0, plos, "10.1371/journal.pone.0232930", pages="e0232930"),
                               result(1, plos, "10.1371/journal.pone.0232930", pages="1-15")), [])
        self.assertEqual(codes(result(0, plos, "10.1234/q", issue="3"), result(1, plos, "10.1234/q", issue="3-4")), [])
        self.assertEqual(codes(result(0, plos, "10.1234/q", issue="3"), result(1, plos, "10.1234/q", issue="4")),
                         ["doi_conflict"])
        self.assertEqual(codes(result(0, plos, "10.1234/q", pages="45-67"), result(1, plos, "10.1234/q", pages="47-67")),
                         ["doi_conflict"])

    def test_m6_a_consortium_is_no_missing_author(self):
        bib = refs.parse("@article{s, author={Smith, J. and Jones, K.}, title={Invented references in language model "
                         "output}, year={2019}}")[0]
        claimed = verify._claimed(bib)
        group = {"authors": ["Smith", "Jones", "The ABC Consortium"], "author_count": 3}
        self.assertNotIn("author_count", verify.field_diffs(claimed, group))
        person = {"authors": ["Smith", "Jones", "Brown"], "author_count": 3}
        self.assertEqual(verify.field_diffs(claimed, person)["author_count"]["found"], "3")


class Audit115Runde3(unittest.TestCase):
    """Dritte Runde: ein BibTeX-Titel in Klammern verrät keinen Datensatz, und eine Gruppe zählt nur als ganzes Wort."""
    BIB = ("@article{iizumi2020dataset, author = {Iizumi, Toshichika and Sakai, Toru}, title = {{GDHY}: A global dataset "
           "of historical yields for major crops 1981--2016}, journal = {Scientific Data}, year = {2020}, volume = {7}, "
           "pages = {97}}")
    TITLE = "GDHY: A global dataset of historical yields for major crops 1981–2016"

    def hit(self, doi, **kw):
        return {"doi": doi, "title": self.TITLE, "authors": ["Iizumi", "Sakai"], "year": "2020", "source": "Crossref",
                "type": "journal-article", "retracted": False, **kw}

    def test_braced_bibtex_title_with_dataset_is_verified(self):
        search = Suche()
        search.REF = refs.parse(self.BIB)[0]
        figshare = self.hit("10.6084/m9.figshare.11903604", type="dataset")      # Crossref nennt ihn zuerst
        res = search.run_search([figshare, self.hit("10.1038/s41597-020-0433-7")])
        self.assertEqual((res["verdict"], res["doi"]), ("verified", "10.1038/s41597-020-0433-7"))

    def test_copy_words_count_only_outside_the_title(self):
        self.assertFalse(verify._names_copy(verify._claimed(refs.parse(self.BIB)[0])))
        self.assertFalse(verify._names_copy(verify._claimed(text_ref(
            "Iizumi, T. (2020). A global dataset of historical yields. Scientific Data, 7, 97."))))
        named = self.BIB.replace("journal = {Scientific Data}", "publisher = {figshare}, note = {Data set}")
        self.assertTrue(verify._names_copy(verify._claimed(refs.parse(named)[0])))
        self.assertTrue(verify._names_copy(verify._claimed(text_ref(
            "Iizumi, T. (2020). A global dataset of historical yields [Data set]. Zenodo. https://doi.org/10.5281/zenodo.1"))))

    def test_group_only_as_a_whole_word(self):
        claimed = verify._claimed(refs.parse("@article{s, author={Smith, J. and Jones, K.}, title={Invented "
                                             "references in language model output}, year={2019}}")[0])
        self.assertNotIn("author_count", verify.field_diffs(claimed, {"authors": ["Smith", "Jones", "ATLAS Collaboration"],
                                                                      "author_count": 3}))
        self.assertEqual(verify.field_diffs(claimed, {"authors": ["Smith", "Jones", "Teamson"], "author_count": 3})
                         ["author_count"]["found"], "3")


if __name__ == "__main__":
    unittest.main()
