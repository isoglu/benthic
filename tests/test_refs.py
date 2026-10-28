"""Tests für den Referenz-Parser (offline). Aufruf: .venv/bin/python -m unittest"""

import unittest

from benthic import refs


class CleanDoi(unittest.TestCase):
    def test_trailing_punctuation_and_braces(self):
        self.assertEqual(refs.clean_doi("10.1038/s41586-020-2649-2},"), "10.1038/s41586-020-2649-2")
        self.assertEqual(refs.clean_doi("see 10.1126/science.1065467."), "10.1126/science.1065467")
        self.assertEqual(refs.clean_doi("(https://doi.org/10.1038/nature12373)"), "10.1038/nature12373")

    def test_keeps_balanced_parentheses(self):
        self.assertEqual(refs.clean_doi("10.1016/S0140-6736(97)11096-0."), "10.1016/S0140-6736(97)11096-0")

    def test_preprint_server_is_not_part_of_the_title(self):
        r = refs.parse("He, K., Zhang, X., Ren, S., & Sun, J. (2015). Deep residual learning for image recognition. "
                       "arXiv. https://doi.org/10.48550/arXiv.1512.03385")[0]
        self.assertEqual(r["title"], "Deep residual learning for image recognition")

    def test_library_keeps_parentheses(self):
        from benthic import library
        self.assertEqual(library.normalize_doi("https://doi.org/10.1016/S0140-6736(97)11096-0"),
                         "10.1016/S0140-6736(97)11096-0")
        self.assertEqual(library.normalize_doi("(doi:10.1038/nature14539)."), "10.1038/nature14539")

    def test_url_encoded_and_pdf_suffix(self):
        self.assertEqual(refs.clean_doi("https://doi.org/10.1000%2Fxyz123"), "10.1000/xyz123")
        self.assertEqual(refs.clean_doi("https://x.org/doi/10.1371/journal.pone.0000308.pdf"), "10.1371/journal.pone.0000308")

    def test_rejects_non_doi(self):
        self.assertIsNone(refs.clean_doi("no identifier here"))


class CleanIsbn(unittest.TestCase):
    def test_valid_isbn10(self):
        self.assertEqual(refs.clean_isbn("0-306-40615-2"), "0306406152")
        self.assertEqual(refs.clean_isbn("ISBN 0-8044-2957-X"), "080442957X")

    def test_valid_isbn13(self):
        self.assertEqual(refs.clean_isbn("978-0-306-40615-7"), "9780306406157")
        self.assertEqual(refs.clean_isbn("ISBN-13: 978-3-16-148410-0"), "9783161484100")

    def test_rejects_invalid_checksum(self):
        self.assertIsNone(refs.clean_isbn("0-306-40615-3"))
        self.assertIsNone(refs.clean_isbn("978-0-306-40615-1"))
        self.assertIsNone(refs.clean_isbn("1234567890"))
        self.assertIsNone(refs.clean_isbn(""))


class Bibtex(unittest.TestCase):
    BIB = r"""
@article{harris2020,
  title = {Array programming with {NumPy}},
  author = {Harris, Charles R. and Millman, K. Jarrod and van der Walt, St{\'e}fan J.},
  journal = {Nature}, year = {2020},
  doi = {10.1038/s41586-020-2649-2},
}
@misc{vaswani2017,
  title = "Attention Is All You Need",
  author = {Ashish Vaswani and Noam Shazeer},
  year = 2017, eprint = {1706.03762v7}, archivePrefix = {arXiv},
}
@comment{ignored}
"""

    def test_entries_fields_and_ids(self):
        out = refs.parse(self.BIB)
        self.assertEqual(len(out), 2)
        a, b = out
        self.assertEqual(a["doi"], "10.1038/s41586-020-2649-2")
        self.assertEqual(a["title"], "Array programming with NumPy")
        self.assertEqual(a["authors"][:3], ["Harris", "Millman", "van der Walt"])
        self.assertEqual(a["year"], "2020")
        self.assertEqual(a["bib_key"], "harris2020")
        self.assertEqual(a["bib_type"], "article")
        self.assertEqual(b["arxiv"], "1706.03762")
        self.assertEqual(b["doi"], "10.48550/arXiv.1706.03762")
        self.assertEqual(b["authors"], ["Vaswani", "Shazeer"])
        self.assertEqual(b["bib_key"], "vaswani2017")

    def test_bibtex_isbn(self):
        bib = """@book{knuth1984,
  title = {The TeXbook},
  author = {Donald E. Knuth},
  year = {1984},
  isbn = {0-201-13447-0},
}"""
        r = refs.parse(bib)[0]
        self.assertEqual(r["isbn"], "0201134470")
        self.assertEqual(r["title"], "The TeXbook")


class Ris(unittest.TestCase):
    def test_records(self):
        ris = "TY  - JOUR\nTI  - Some title here\nAU  - Doe, Jane\nPY  - 2019/05/01\nDO  - 10.1126/science.1065467\nER  - \nTY  - JOUR\nT1  - Second\nUR  - https://doi.org/10.1038/nature12373\nER  -\n"
        out = refs.parse(ris)
        self.assertEqual([r["doi"] for r in out], ["10.1126/science.1065467", "10.1038/nature12373"])
        self.assertEqual(out[0]["authors"], ["Doe"])
        self.assertEqual(out[0]["year"], "2019")

    def test_ris_isbn(self):
        ris = "TY  - BOOK\nTI  - Deep Learning\nAU  - Goodfellow, Ian\nPY  - 2016\nSN  - 978-0-262-03561-3\nER  -\n"
        r = refs.parse(ris)[0]
        self.assertEqual(r["isbn"], "9780262035613")
        self.assertEqual(r["title"], "Deep Learning")


class FreeText(unittest.TestCase):
    LLM = """Here are the key sources for your topic:

1. Harris, C. R., Millman, K. J., & van der Walt, S. J. (2020). Array programming with NumPy. Nature, 585, 357-362. https://doi.org/10.1038/s41586-020-2649-2
2. Vaswani, A., Shazeer, N., et al. (2017). Attention is all you need. Advances in Neural Information Processing Systems.
3. **Smith et al. (2021)**, "A completely invented paper about LLM citations", Journal of Imaginary Results.
- arXiv:1512.03385
- PMID: 31452104

These should give you a solid start."""

    def test_llm_answer(self):
        out = refs.parse(self.LLM)
        self.assertEqual(len(out), 5)
        harris, vaswani, smith, resnet, pm = out
        self.assertEqual(harris["doi"], "10.1038/s41586-020-2649-2")
        self.assertEqual(harris["title"], "Array programming with NumPy")
        self.assertEqual(harris["year"], "2020")
        self.assertIn("Harris", harris["authors"])
        self.assertIsNone(vaswani["doi"])
        self.assertEqual(vaswani["title"], "Attention is all you need")
        self.assertEqual(vaswani["year"], "2017")
        self.assertEqual(smith["title"], "A completely invented paper about LLM citations")
        self.assertEqual(smith["authors"], ["Smith"])
        self.assertEqual(resnet["doi"], "10.48550/arXiv.1512.03385")
        self.assertEqual(pm["pmid"], "31452104")

    def test_plain_doi_list_and_duplicates(self):
        out = refs.parse("10.1038/s41586-020-2649-2; 10.1126/science.1065467\nhttps://doi.org/10.1038/s41586-020-2649-2")
        self.assertEqual([r["doi"] for r in out], ["10.1038/s41586-020-2649-2", "10.1126/science.1065467"])

    def test_ignores_prose(self):
        self.assertEqual(refs.parse("These should give you a solid start.\nThanks!"), [])

    def test_text_isbn(self):
        line = "Knuth, D. E. (1984). The TeXbook. Addison-Wesley. ISBN 0-201-13447-0."
        r = refs.parse(line)[0]
        self.assertEqual(r["isbn"], "0201134470")
        self.assertEqual(r["year"], "1984")
        self.assertIn("Knuth", r["authors"])

    def test_initials_first_entries(self):
        """IEEE und CVPR: "Y. Bengio, P. Simard, and P. Frasconi. Titel. Zeitschrift, 1994." """
        cases = {
            "Y. Bengio, P. Simard, and P. Frasconi. Learning long-term dependencies with gradient descent is difficult. "
            "IEEE Transactions on Neural Networks, 5(2):157-166, 1994.":
                (["Bengio", "Simard", "Frasconi"], "Learning long-term dependencies with gradient descent is difficult", "1994"),
            "C. R. Harris et al. Array programming with NumPy. Nature, 585:357-362, 2020.":
                (["Harris"], "Array programming with NumPy", "2020"),
            '[1] C. R. Harris et al., "Array programming with NumPy," Nature, vol. 585, pp. 357-362, 2020.':
                (["Harris"], "Array programming with NumPy", "2020"),
        }
        for line, (authors, title, year) in cases.items():
            r = refs.parse_text(line)[0]
            self.assertEqual((r["authors"], r["title"], r["year"]), (authors, title, year), line)

    def test_summary(self):
        s = refs.summary(refs.parse(self.LLM))
        self.assertEqual(s["count"], 5)
        self.assertEqual(s["with_doi"], 2)  # Harris, ResNet (über arXiv)
        self.assertEqual(s["with_pmid"], 1)
        self.assertEqual(s["title_only"], 2)

    def test_parse_endnote_xml(self):
        xml_data = """<?xml version="1.0" encoding="UTF-8" ?>
<xml>
  <records>
    <record>
      <contributors>
        <authors>
          <author><style face="normal">Harris, C. R.</style></author>
          <author><style face="normal">Millman, K. J.</style></author>
        </authors>
        <secondary-authors>
          <author><style face="normal">Editor, E.</style></author>
        </secondary-authors>
      </contributors>
      <titles>
        <title><style face="normal">Array programming with NumPy</style></title>
        <secondary-title><style face="normal">Nature</style></secondary-title>
      </titles>
      <dates>
        <year><style face="normal">2020</style></year>
      </dates>
      <electronic-resource-num><style face="normal">10.1038/s41586-020-2649-2</style></electronic-resource-num>
    </record>
  </records>
</xml>"""
        parsed = refs.parse(xml_data)
        self.assertEqual(len(parsed), 1)
        r = parsed[0]
        self.assertEqual(r["title"], "Array programming with NumPy")
        self.assertEqual(r["authors"], ["Harris", "Millman"])
        self.assertEqual(r["year"], "2020")
        self.assertEqual(r["venue"], "Nature")
        self.assertEqual(r["doi"], "10.1038/s41586-020-2649-2")
        self.assertEqual(r["format"], "endnote_xml")


if __name__ == "__main__":
    unittest.main()
