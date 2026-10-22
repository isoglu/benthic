"""Ausweis einer geladenen PDF: ist sie das zitierte Werk, welche Fassung liegt vor (offline, kleine echte PDFs)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benthic import identity

# Der Fall, für den die Prüfung gebaut ist (isoglu.com, 7. September 2026): zitiert ist die Übersicht von Aydin et al.,
# in der Datei steht ihr Neurocomputing-Artikel mit fast gleichem Titelanfang.
CLAIMED_DOI = "10.1080/0013791X.2013.864700"
CLAIMED_TITLE = "Market demand estimation for new product development: A survey"
OTHER_DOI = "10.1016/j.neucom.2014.01.051"
OTHER_TITLE = ("Market demand estimation for new product development by using fuzzy modeling and discrete choice "
               "analysis")


def filler(words=420, seed="demand"):
    base = ("the model estimates {s} for products from survey data and compares the forecasts with observed sales "
            "across several markets while the authors discuss limits of the method in detail").format(s=seed).split()
    return "\n".join(" ".join((base * 40)[i:i + 12]) for i in range(0, words, 12))


def make_pdf(*pages):
    """Eine kleine PDF mit Text (Helvetica, eine Zeile je Zeilenumbruch), die pypdf lesen kann."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    n = len(pages)
    objs = {1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: f"<< /Type /Pages /Kids [{' '.join(f'{4 + 2 * i} 0 R' for i in range(n))}] /Count {n} >>",
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    for i, text in enumerate(pages):
        stream = "BT /F1 9 Tf 11 TL 40 760 Td " + " ".join(f"({esc(l)}) Tj T*" for l in text.split("\n")) + " ET"
        objs[4 + 2 * i] = ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                           f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>")
        objs[5 + 2 * i] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
    out, offsets = b"%PDF-1.4\n", {}
    for k in sorted(objs):
        offsets[k] = len(out)
        out += f"{k} 0 obj\n{objs[k]}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offsets[k]:010d} 00000 n \n".encode() for k in sorted(objs))
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def article(head, pages=9, tail=""):
    return [head + "\n" + filler()] + [filler() for _ in range(pages - 2)] + [filler() + "\n" + tail]


ALIEN = article(f"Neurocomputing 142 (2014) 136-146\n{OTHER_TITLE}\nR. Aydin, C.K. Kwong, P. Ji\n"
                f"http://dx.doi.org/{OTHER_DOI}\nAbstract", tail="References\nSmith (2010) doi:10.1000/ref.1")


class Ausweis(unittest.TestCase):
    def judge(self, pages, doi=CLAIMED_DOI, title=CLAIMED_TITLE):
        return identity.judge(pages, doi, title)["status"]

    def test_wrong_file_is_contradicted(self):
        r = identity.judge(ALIEN, CLAIMED_DOI, CLAIMED_TITLE)
        self.assertEqual(r["status"], identity.CONTRADICTED)
        self.assertEqual(r["dois"], [OTHER_DOI])

    def test_own_doi_verifies_in_any_spelling(self):
        for spelt in (f"https://doi.org/{CLAIMED_DOI.replace('/', '%2F')}", f"DOI: {CLAIMED_DOI.upper()}.",
                      f"doi:{CLAIMED_DOI}Received 3 May"):
            self.assertEqual(self.judge(article(f"The Engineering Economist\n{spelt}")), identity.VERIFIED, spelt)

    def test_title_with_a_stray_page_number_verifies(self):
        title = "International expansion through start-up or acquisition: A learning perspective"
        pages = article("doi:10.2307/256919\nInternational expansion through start up or 7 acquisition a learning "
                        "perspective")
        self.assertEqual(self.judge(pages, "10.5465/257004", title), identity.VERIFIED)

    def test_scattered_title_words_do_not_verify(self):
        pages = article(f"doi:{OTHER_DOI}\nMarket shares\n" + filler(200) + "\nestimation for new product launch\n"
                        + filler(200) + "\ndevelopment teams in a survey")
        self.assertEqual(self.judge(pages), identity.CONTRADICTED)

    def test_unsure_cases_stay_unprovable(self):
        no_doi_on_page_one = article("A survey of demand models (preprint)", tail=f"doi:{OTHER_DOI}")
        claimed_only_at_the_end = article(f"doi:{OTHER_DOI}\nCover sheet", tail=f"This article: doi:{CLAIMED_DOI}")
        self.assertEqual(self.judge(no_doi_on_page_one), identity.UNPROVABLE)
        self.assertEqual(self.judge(claimed_only_at_the_end), identity.UNPROVABLE)
        self.assertEqual(self.judge(ALIEN, title=""), identity.UNPROVABLE)                  # kein Titel bekannt
        short = article(f"doi:{OTHER_DOI}\nDeep learning\nY. LeCun")
        self.assertEqual(self.judge(short, "10.1038/nature14539", "Deep learning"), identity.UNPROVABLE)
        self.assertEqual(self.judge([]), identity.UNPROVABLE)

    def test_other_versions_of_the_work_are_never_contradicted(self):
        published = ("10.1038/s41564-020-0695-z", "Gut bacteria set the pace of host metabolism in germ-free mice")
        biorxiv = article("bioRxiv preprint doi: https://doi.org/10.1101/2020.02.07.937862doi: this version posted\n"
                          "A microbial clock for host metabolism")
        ssrn = article("Electronic copy available at: https://ssrn.com/abstract=3141\ndoi:10.2139/ssrn.3141\nOld title")
        accepted = article("This is the author's accepted manuscript.\nSee also doi:10.1016/j.cell.2020.01.001\nOld title")
        report = article("Technical Report MSR-TR-99-87\ndoi:10.1000/msr.1999.87\nOld title")
        for pages in (biorxiv, ssrn, accepted, report):
            self.assertEqual(self.judge(pages, *published), identity.UNPROVABLE, pages[0][:40])
        journal = article(f"doi:{OTHER_DOI}\n{OTHER_TITLE}")                     # zitiert ist der Preprint
        self.assertEqual(self.judge(journal, "10.1101/2020.02.07.937862", CLAIMED_TITLE), identity.UNPROVABLE)
        with mock.patch("benthic.oa.resolve_metadata", return_value={"title": "A microbial clock for host metabolism"}):
            self.assertEqual(identity.check(make_pdf(*biorxiv), *published)["status"], identity.UNPROVABLE)

    def test_a_doi_label_glued_to_the_doi_is_cut_off(self):
        self.assertEqual(identity._dois("bioRxiv preprint doi: https://doi.org/10.1101/2020.02.07.937862doi: posted"),
                         ["10.1101/2020.02.07.937862"])
        self.assertEqual(identity._dois(f"http://dx.doi.org/{OTHER_DOI}\nAbstract"), [OTHER_DOI])

    def test_title_read_four_ways_verifies(self):
        cases = [("High-Dimensional Data Analysis with Sparse Models", "High-\nDimensional Data Analysis with Sparse Models"),
                 ("Mergers &amp; Acquisitions and the Returns to Bidders", "Mergers & Acquisitions and the Returns to Bidders"),
                 ("Ocean uptake of CO<sub>2</sub> in a warming climate", "Ocean uptake of CO2 in a warming climate"),
                 ("A significant effect of fluoride on enamel", "A signifi cant effect of fl uoride on enamel"),
                 ("Relever le défi climatique en Europe", "Relever le défi climatique en Europe")]
        for title, printed in cases:
            pages = article(f"Data: https://doi.org/10.1000/data.7\n{printed}")
            self.assertEqual(self.judge(pages, "10.1000/claimed.1", title), identity.VERIFIED, title)

    def test_a_doi_on_page_one_is_only_the_work_with_its_title_there(self):
        pages = article("Supplementary material: https://doi.org/10.1000/related.7\nA new title for this paper")
        self.assertEqual(self.judge(pages), identity.CONTRADICTED)                     # vorläufig, aus judge allein
        with mock.patch("benthic.oa.resolve_metadata", return_value={"title": "Results of a related survey of firms"}):
            self.assertEqual(identity.check(make_pdf(*pages), CLAIMED_DOI, CLAIMED_TITLE)["status"], identity.UNPROVABLE)
        with mock.patch("benthic.oa.resolve_metadata", return_value=None):              # ohne Netz kein Nachweis
            self.assertEqual(identity.check(make_pdf(*ALIEN), CLAIMED_DOI, CLAIMED_TITLE)["status"], identity.UNPROVABLE)
        data = article("Data: https://doi.org/10.5281/zenodo.4711\nA new title for this paper")
        self.assertEqual(self.judge(data), identity.UNPROVABLE)

    def test_a_scan_never_reads_page_one_from_the_reference_list(self):
        references = f"References\n{filler(300)}\nAydin R (2014) {OTHER_TITLE}. doi:{OTHER_DOI}"
        self.assertEqual(self.judge(["", " ", "", references, references, references]), identity.UNPROVABLE)

    def test_a_cut_doi_matches_no_other_doi(self):
        claimed = "10.1016/j.neucom.2014.01.051"
        self.assertEqual(self.judge(article("Cited: doi:10.1016/j.\nAbstract"), claimed, ""), identity.UNPROVABLE)
        self.assertEqual(self.judge(article("doi:10.1000/1234\nAbstract"), "10.1000/123", ""), identity.UNPROVABLE)
        self.assertEqual(self.judge(article("doi:10.1016/j.\nneucom.2014.01.051\nAbstract"), claimed, ""),
                         identity.VERIFIED)

    def test_a_whole_book_is_never_another_work_for_its_chapter(self):
        chapter = ("10.1007/978-3-030-12345-6_7", "Spatial econometrics of regional labour markets")
        book = article("Handbook of Regional Science\nSpringer\nhttps://doi.org/10.1007/978-3-030-12345-6")
        self.assertEqual(identity.dnorm(chapter[0]), "10.1007/978-3-030-12345-6_7")
        self.assertEqual(self.judge(book, *chapter), identity.VERIFIED)
        with mock.patch("benthic.oa.resolve_metadata", return_value={"title": "Handbook of Regional Science"}):
            self.assertEqual(identity.check(make_pdf(*book), *chapter)["status"], identity.VERIFIED)
        other = article("Handbook of Urban Studies\nSpringer\nhttps://doi.org/10.1007/978-3-030-99999-1")
        with mock.patch("benthic.oa.resolve_metadata", return_value={"title": "Handbook of Urban Studies"}):
            self.assertEqual(identity.check(make_pdf(*other), *chapter)["status"], identity.CONTRADICTED)
        self.assertFalse(identity._same("10.1007/978-3-030-12345-67", ["10.1007/978-3-030-12345-6"]))

    def test_greek_letters_in_a_title(self):
        title = "TNF-α signalling in inflammatory bowel disease"
        for printed in ("TNF-a signalling", "TNF-alpha signalling", "TNF-α signalling", "TNF-\nα signalling"):
            pages = article(f"doi:{OTHER_DOI}\n{printed} in inflammatory bowel disease")
            self.assertEqual(self.judge(pages, CLAIMED_DOI, title), identity.VERIFIED, printed)
        other = article(f"doi:{OTHER_DOI}\nIL-6 signalling in chronic kidney disease")
        self.assertNotEqual(self.judge(other, CLAIMED_DOI, title), identity.VERIFIED)

    def test_a_footnote_reference_never_proves_another_work(self):
        review = ("10.1000/claimed.review", "Climate change and migration: a review of the evidence")
        black = ("10.1016/j.gloenvcha.2011.10.001", "The effect of environmental change on human migration")
        footnote = (f"1 Black, R. et al. (2011) {black[1]}. Global\nEnvironmental Change 21, S3-S11. "
                    f"https://doi.org/{black[0]}")
        at_the_bottom = article("Climate change and migration\nA. Author and B. Author\nUniversity of Somewhere\n"
                                "Abstract\n" + filler(300) + "\n" + footnote)
        in_the_title_block = article(f"Climate change and migration\n{footnote}")
        with mock.patch("benthic.oa.resolve_metadata", return_value={"title": black[1]}):
            for pages in (at_the_bottom, in_the_title_block):
                self.assertEqual(self.judge(pages, *review), identity.CONTRADICTED)   # vorläufig, aus judge allein
                self.assertEqual(identity.check(make_pdf(*pages), *review)["status"], identity.UNPROVABLE)
        with mock.patch("benthic.oa.resolve_metadata", return_value={"title": OTHER_TITLE}):  # Titel als Titelzeile
            self.assertEqual(identity.check(make_pdf(*ALIEN), CLAIMED_DOI, CLAIMED_TITLE)["status"], identity.CONTRADICTED)

    def test_note_is_empty_without_another_work(self):
        self.assertEqual(identity.note({"status": identity.VERIFIED}), "")
        self.assertEqual(identity.note({"status": identity.UNPROVABLE, "copy": None}), "")

    def test_check_reads_a_pdf_and_names_the_other_work(self):
        with mock.patch("benthic.oa.resolve_metadata", return_value={"title": OTHER_TITLE}):
            r = identity.check(make_pdf(*ALIEN), CLAIMED_DOI, CLAIMED_TITLE, "112–131")
        self.assertEqual((r["status"], r["file_doi"], r["file_title"], r["pdf_pages"]),
                         (identity.CONTRADICTED, OTHER_DOI, OTHER_TITLE, 9))
        self.assertIn(OTHER_DOI, identity.note(r))
        self.assertIn("fuzzy modeling", identity.note(r))

    def test_broken_pdf_is_unprovable(self):
        self.assertEqual(identity.check(b"%PDF-1.4 kaputt", CLAIMED_DOI, CLAIMED_TITLE)["status"], identity.UNPROVABLE)


class Fassung(unittest.TestCase):
    def grade(self, pages, expected=None):
        return identity.grade(pages, len(pages), identity.expected_pages(expected))

    def test_first_page_phrases(self):
        cases = {"Electronic copy available at: https://ssrn.com/abstract=2245324": "ssrn",
                 "NBER WORKING PAPER SERIES": "working_paper",
                 "This is the author's accepted manuscript of an article": "accepted",
                 "arXiv:1512.03385v1 [cs.CV] 10 Dec 2015": "preprint",
                 "This content is reserved for Subscribers": "teaser",
                 "Soundview Executive Book Summaries": "summary"}
        for phrase, kind in cases.items():
            self.assertEqual(self.grade(article(phrase)), kind, phrase)

    def test_thin_copies(self):
        self.assertEqual(self.grade(["", " ", "Figure 1"]), "no_text")
        self.assertEqual(self.grade(article("A condensed version", pages=8), "1-280"), "summary")  # Buch, 8 Seiten
        self.assertEqual(self.grade(article("First pages only", pages=2), "357–376"), "summary")

    def test_reports_commentaries_and_slides(self):
        cases = {"Technical Report MSR-TR-99-87": "report", "CMU-CS-05-187\nSchool of Computer Science": "report",
                 "NEWS & VIEWS\nOcean uptake": "commentary", "Correction to: Array programming with NumPy": "commentary",
                 "Erratum\nIn the article by Harris": "commentary"}
        for phrase, kind in cases.items():
            self.assertEqual(self.grade(article(phrase)), kind, phrase)
        slides = ["Deep residual learning\n" + filler(60, "layers") for _ in range(24)]
        self.assertEqual(self.grade(slides, "770–778"), "thin")
        for phrase in ("Scientific Reports | (2020) 10:1234", "This report describes the results of a survey",
                       "Research Report\nPsychological Science", "We apply a bias correction to the estimator"):
            self.assertIsNone(self.grade(article(phrase)), phrase)

    def test_version_notes_count_only_in_the_title_block_or_footer(self):
        def page_one(mention, end=""):
            return [f"{CLAIMED_TITLE}\n" + filler(120) + f"\n{mention}\n" + filler(300) + end] + article("x")[1:]
        for mention in ("comment on the debate about tariffs and trade policy in the region",
                        "Technical Report 12 of the ministry describes the same survey design",
                        "an earlier draft was posted on medRxiv as a preprint in 2020",
                        "as shown by Card (NBER Working Paper No. 12345) for minimum wages",
                        "see arXiv:2001.01234 for the proof", "the Postprint of this study is also online"):
            self.assertIsNone(self.grade(page_one(mention)), mention)
        footer = page_one("", end="\nElectronic copy available at: https://ssrn.com/abstract=3141")
        self.assertEqual(self.grade(footer), "ssrn")
        self.assertEqual(self.grade(page_one("", end="\nThis is the author's accepted manuscript.")), "accepted")

    def test_a_report_number_below_a_long_author_list(self):
        names = ["Bernhard Schoelkopf", "/?", "John C. Platt", "z", "John Shawe-Taylor", ",", "Alex J. Smola", "y",
                 "Robert C. Williamson"]
        places = ["Microsoft Research Ltd, 1 Guildhall Street, Cambridge, UK",
                  "Microsoft Research, 1 Microsoft Way, Redmond, WA, USA",
                  "Royal Holloway, University of London, Egham, UK", "Australian National University, Canberra"]
        cover = "\n".join(["Estimating the Support of a High-Dimensional Distribution", *names, *places,
                           "27 November 1999", "Technical Report", "MSR-TR-99-87", "Microsoft Research"])
        self.assertEqual(self.grade([cover + "\n" + filler()] + article("x")[1:]), "report")
        marks = "\n".join(["A Short Title for a Report", *names[:7], "Technical Report"])  # 10 Zeilen, 6 mit Inhalt
        self.assertEqual(self.grade([marks + "\n" + filler()] + article("x")[1:]), "report")
        early = "\n".join(["A Short Title", "A. Author", "comment on the debate about tariffs and trade policy"])
        self.assertIsNone(self.grade([early + "\n" + filler()] + article("x")[1:]))
        self.assertEqual(self.grade(article("Comment on: Array programming with NumPy")), "commentary")

    def test_full_article_stays_silent(self):
        self.assertIsNone(self.grade(article("Nature 585, 357–362 (2020)", pages=6), "357–362"))
        self.assertIsNone(self.grade(article("Accepted for publication in a journal", pages=30), "357–362"))
        self.assertIsNone(self.grade(article("No page range known", pages=3)))
        self.assertIsNone(identity.expected_pages("e0123456"))


class Gehalten(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pdf = Path(self.tmp.name) / "aydin.pdf"
        self.pdf.write_bytes(make_pdf(*ALIEN))
        identity._cache.clear()
        self.patches = [mock.patch("benthic.biblio.pdf_path", return_value=self.pdf),
                        mock.patch("benthic.library.get", return_value={"title": CLAIMED_TITLE}),
                        mock.patch("benthic.oa.resolve_metadata", return_value={"title": OTHER_TITLE})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_quote_support_and_overlap_do_not_use_the_wrong_file(self):
        from benthic import overlap, quotes, support
        q = quotes.check(CLAIMED_DOI, "the model estimates demand for products from survey data")
        self.assertEqual((q["status"], q["file_doi"]), ("wrong_pdf", OTHER_DOI))
        s = support.find(CLAIMED_DOI, "survey data predicts sales")
        self.assertFalse(s["available"])
        self.assertIn(OTHER_DOI, s["error"])
        doc = {"claims": [{"entry": 0, "label": "Aydin (2014)",
                           "sentence": "The model estimates demand for products from survey data and compares the "
                                       "forecasts with observed sales (Aydin, 2014)."}]}
        r = overlap.check_document(doc, [{"order": 0, "verdict": "verified", "doi": CLAIMED_DOI}])
        self.assertEqual((r["findings"], r["wrong_pdfs"]), ([], [CLAIMED_DOI]))

    def test_right_file_is_used(self):
        from benthic import quotes
        self.pdf.write_bytes(make_pdf(*article(f"The Engineering Economist 59(2)\n{CLAIMED_TITLE}")))
        os.utime(self.pdf, ns=(1, 1))  # neue Datei, neuer Ausweis
        q = quotes.check(CLAIMED_DOI, "the model estimates demand for products from survey data")
        self.assertEqual((q["status"], q["copy"]), ("exact", None))


class Bericht(unittest.TestCase):
    def test_report_names_file_copy_run_and_other_source(self):
        from benthic import report
        quotes = [{"quote": "a quote from the wrong file", "label": "Aydin (2014)", "status": "wrong_pdf",
                   "file_doi": OTHER_DOI, "file_title": OTHER_TITLE},
                  {"quote": "a quote found elsewhere", "label": "Smith (2020)", "status": "not_found", "copy": "preprint",
                   "longest": {"words": 14, "of": 22, "page": 7}, "found_in": {"label": "Jones 2019", "page": 42}}]
        page = report.render({"mode": "verify", "results": [], "quote_check": quotes, "created_at": 0}, "1")
        from benthic.i18n import tr  # in der Sprache, die gerade eingestellt ist
        for text in (identity.note(quotes[0]), tr("Falsches PDF"), tr("Fassung: {copy}", copy=tr("Preprint")),
                     tr("Längste wortgleiche Folge: {n} von {total} Wörtern, S. {page}.", n=14, total=22, page=7),
                     tr("Steht in {other} (S. {page}), zitiert ist {cited}.", other="Jones 2019", page=42,
                        cited="Smith (2020)")):
            self.assertIn(report._e(text), page)

    def test_report_marks_a_misattributed_quote(self):
        from benthic import report
        quotes = [{"quote": "a quote found elsewhere", "label": "Smith (2020)", "status": "misattributed",
                   "found_in": {"label": "Jones 2019", "page": 42}},
                  {"quote": "a quote found nowhere", "label": "Smith (2020)", "status": "not_found", "searched": 1}]
        page = report.render({"mode": "verify", "results": [], "quote_check": quotes, "created_at": 0}, "1")
        from benthic.i18n import tr
        self.assertIn(report._e(tr("Auch nicht in der anderen geladenen Quelle.")), page)
        self.assertEqual(page.count(report._e(tr("Steht in einer anderen Quelle"))), 2)  # Zeile und Überblick

    def test_wrong_pdf_without_a_title_leaves_no_empty_line(self):
        from benthic import report
        bare = {"quote": "a quote", "label": "Aydin (2014)", "status": "wrong_pdf", "file_doi": OTHER_DOI}
        self.assertEqual(report._quote_notes(bare), [])
        self.assertEqual(report._quote_notes({**bare, "file_title": OTHER_TITLE}), [identity.note({"file_title": OTHER_TITLE,
                                                                                                 "file_doi": OTHER_DOI})])

    def test_word_export_comments_on_a_misattributed_quote(self):
        from benthic import app as appmod
        from benthic.i18n import tr
        moved = {"status": "misattributed", "found_in": {"label": "Jones 2019", "page": 42}}
        self.assertEqual(appmod._quote_comment(moved),
                         tr("Steht nicht in der zitierten Quelle, aber wortgleich in {other} (S. {page}).",
                            other="Jones 2019", page=42))
        self.assertIsNone(appmod._quote_comment({"status": "exact", "page_ok": True}))
        self.assertEqual(appmod._quote_comment({"status": "exact", "page_ok": False}), tr("Seitenzahl des Zitats weicht ab."))


class Download(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def fetch(self, files):
        from benthic import fetcher
        cands = [{"url": url, "source": "Test", "version": "publishedVersion"} for url in files]
        with mock.patch("benthic.oa.resolve_candidates", return_value=(cands, ["https://example.org/landing"])), \
                mock.patch("benthic.oa.download_pdf", side_effect=lambda url, headers=None: files[url]), \
                mock.patch("benthic.oa.resolve_metadata",
                           side_effect=lambda d, *a: {"title": CLAIMED_TITLE if d == CLAIMED_DOI else OTHER_TITLE,
                                                      "pages": "112–131"}):
            out = Path(self.tmp.name) / "papers"
            return fetcher.BenthicDownloader(out, skip_existing=False).download_one(CLAIMED_DOI), out

    def test_wrong_file_is_skipped_for_the_next_candidate(self):
        right = make_pdf(*article(f"doi:{CLAIMED_DOI}\n{CLAIMED_TITLE}", pages=20))
        r, out = self.fetch({"https://a.example/1.pdf": make_pdf(*ALIEN), "https://b.example/2.pdf": right})
        self.assertEqual((r["status"], r["identity"], r["copy"]), ("ok", identity.VERIFIED, None))
        self.assertEqual([p.read_bytes() for p in out.iterdir()], [right])

    def test_only_wrong_files_are_not_saved(self):
        r, out = self.fetch({"https://a.example/1.pdf": make_pdf(*ALIEN)})
        self.assertEqual((r["status"], r["wrong_file"], r["file_doi"]), ("blocked", True, OTHER_DOI))
        self.assertIn(OTHER_DOI, r["error"])
        self.assertEqual(list(out.iterdir()), [])
        from benthic import mcp_server
        line = mcp_server._fmt_download({"results": [r], "status": "done"}).splitlines()[-1]
        self.assertIn(f"{CLAIMED_DOI}: DIFFERENT WORK, NOT SAVED: the free copy is doi:{OTHER_DOI}", line)
        self.assertNotIn("OPEN MANUALLY", line)
        ok = {"identifier": CLAIMED_DOI, "status": "blocked", "landing_url": "https://example.org/landing"}
        line = mcp_server._fmt_download({"results": [ok], "status": "done"}).splitlines()[-1]
        self.assertIn("FREE COPY KNOWN, OPEN MANUALLY", line)
        self.assertNotIn("DIFFERENT WORK", line)

    def test_failed_extra_routes_are_reported_in_each_language(self):
        from benthic import fetcher, mcp_server, routes
        from benthic.i18n import tr_part

        class Missing(routes.Route):
            id, label = "missing", "Weg A"

            def fetch(self, identifier, emit):
                raise routes.RouteMiss("no copy")

        class Alien(routes.Route):
            id, label = "alien", "Weg B"

            def fetch(self, identifier, emit):
                return routes.Hit(make_pdf(*ALIEN), "https://b.example/x.pdf", "b.example")

        with mock.patch("benthic.oa.resolve_candidates", return_value=([], [])), \
                mock.patch("benthic.oa.resolve_metadata",
                           side_effect=lambda d, *a: {"title": CLAIMED_TITLE if d == CLAIMED_DOI else OTHER_TITLE}):
            out = Path(self.tmp.name) / "papers"
            r = fetcher.BenthicDownloader(out, skip_existing=False, route_list=[Missing(), Alien()]).download_one(CLAIMED_DOI)
        self.assertEqual(r["status"], "error")
        en, de = tr_part(r["error_parts"][0], "en"), tr_part(r["error_parts"][0], "de")
        self.assertIn("No open-access copy; extra routes without success: Weg A: no copy; Weg B: The PDF is a different "
                      f"work: DOI {OTHER_DOI}", en)
        self.assertNotIn("Zusatzwege", en)
        self.assertIn(f"Zusatzwege ohne Erfolg: Weg A: no copy; Weg B: Das PDF ist ein anderes Werk: DOI {OTHER_DOI}", de)
        self.assertIn("extra routes without success", mcp_server._fmt_download({"results": [r], "status": "done"}))
        self.assertEqual(list(out.iterdir()), [])


class Assistent(unittest.TestCase):
    def find_support(self, copy):
        from benthic import mcp_server
        data = {"available": True, "file": "x.pdf", "copy": copy, "level": "weak", "passages": []}
        with mock.patch.object(mcp_server, "_http", return_value=(200, data)), mock.patch.object(mcp_server, "_activity"):
            return mcp_server.t_find_support({"doi": CLAIMED_DOI, "claim": "demand can be estimated from surveys"})

    def test_find_support_names_an_incomplete_copy_or_another_version(self):
        self.assertIn("Note: searched an incomplete copy (summary or excerpt", self.find_support("summary"))
        for copy in ("preprint", "working_paper", "accepted", "report"):
            self.assertIn("Note: searched a version other than the published one", self.find_support(copy), copy)
        self.assertNotIn("Note:", self.find_support(None))

    def test_quote_names_the_version_like_the_app(self):
        from benthic import mcp_server
        self.assertIn("Version: preprint", mcp_server._fmt_quote({"quote": "q", "status": "exact", "copy": "preprint"}))
        self.assertNotIn("Version:", mcp_server._fmt_quote({"quote": "q", "status": "exact", "copy": None}))

    def test_one_other_source_reads_in_the_singular(self):
        from benthic import mcp_server
        line = mcp_server._fmt_quote({"quote": "q", "status": "not_found", "searched": 1})
        self.assertIn("Not in the other downloaded source either", line)
        self.assertIn("any of the 2 other", mcp_server._fmt_quote({"quote": "q", "status": "not_found", "searched": 2}))


if __name__ == "__main__":
    unittest.main()
