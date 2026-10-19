"""Formatierte Angaben von doi.org: Stilnamen, leere Felder, Listennummern, DIN-Sprache (offline)."""

import unittest
from unittest import mock

from benthic import biblio

# CSL-Namen, die doi.org am 19. Oktober 2026 für 10.1038/nature14539 formatiert hat
# (Accept: text/x-bibliography; style=…). "vancouver" antwortet dort mit style-not-found.
DOI_ORG_STYLES = {"apa", "harvard-cite-them-right", "chicago-author-date", "modern-language-association",
                  "ieee", "nlm-citation-sequence", "din-1505-2"}

# Antworten von doi.org für 10.1038/nature14539, wörtlich (19. Oktober 2026).
RAW = {
    "harvard_en": ("LeCun, Y., Bengio, Y. and Hinton, G. (2015) ‘Deep learning,’ Nature. Edited by , "
                   "521(7553), pp. 436–444. Available at: https://doi.org/10.1038/nature14539."),
    "harvard_de": ("LeCun, Y., Bengio, Y. und Hinton, G. (2015) „Deep learning“, Nature. Herausgegeben von , "
                   "521(7553), S. 436–444. Verfügbar unter: https://doi.org/10.1038/nature14539."),
    "mla": ("LeCun, Yann, et al. ‘Deep Learning.’ Nature, edited by , vol. 521, no. 7553, May 2015, "
            "pp. 436–44. Crossref, https://doi.org/10.1038/nature14539."),
    "nlm_en": ("1.LeCun Y, Bengio Y, Hinton G. Deep learning. , editor. Nature. 2015 May 27;521(7553):436–44. "
               "doi:10.1038/nature14539"),
    "nlm_de": ("1.LeCun Y, Bengio Y, Hinton G. Deep learning. , Herausgeber. Nature. 27 Mai 2015;"
               "521(7553):436–44. doi:10.1038/nature14539"),
    "din": ("LeCun, Yann ; Bengio, Yoshua ; Hinton, Geoffrey: Deep learning. In: (Hrsg.) Nature Bd. 521, "
            "Springer Science and Business Media LLC (2015), Nr. 7553, S. 436–444"),
    "ieee": ("[1]Y. LeCun, Y. Bengio, and G. Hinton, “Deep learning,” Nature, vol. 521, no. 7553, "
             "pp. 436–444, May 2015, doi: 10.1038/nature14539."),
}


class StyleNamesTest(unittest.TestCase):
    def test_every_short_name_is_a_style_doi_org_knows(self):
        self.assertLessEqual(set(biblio.STYLES.values()), DOI_ORG_STYLES)

    def test_vancouver_is_the_nlm_style(self):
        self.assertEqual(biblio.STYLES["vancouver"], "nlm-citation-sequence")


class PlainTest(unittest.TestCase):
    def test_empty_editor_fields_go(self):
        self.assertEqual(biblio._plain(RAW["harvard_en"]),
                         "LeCun, Y., Bengio, Y. and Hinton, G. (2015) ‘Deep learning,’ Nature, 521(7553), "
                         "pp. 436–444. Available at: https://doi.org/10.1038/nature14539.")
        self.assertIn("„Deep learning“, Nature, 521(7553), S. 436–444", biblio._plain(RAW["harvard_de"]))
        self.assertIn("Nature, vol. 521, no. 7553", biblio._plain(RAW["mla"]))
        self.assertEqual(biblio._plain(RAW["nlm_en"]),
                         "LeCun Y, Bengio Y, Hinton G. Deep learning. Nature. 2015 May 27;521(7553):436–44. "
                         "doi:10.1038/nature14539")
        self.assertIn("Deep learning. Nature. 27 Mai 2015", biblio._plain(RAW["nlm_de"]))
        self.assertIn("Deep learning. In: Nature Bd. 521", biblio._plain(RAW["din"]))

    def test_list_number_of_a_single_entry_goes(self):
        self.assertTrue(biblio._plain(RAW["ieee"]).startswith("Y. LeCun, Y. Bengio, and G. Hinton,"))
        self.assertTrue(biblio._plain(RAW["nlm_en"]).startswith("LeCun Y, Bengio Y"))

    def test_real_editors_and_numbers_stay(self):
        chapter = "Smith, J. (2020) ‘A chapter’, in Brown, A. (ed.) A book. Edited by A. Brown, London: Press, pp. 1–9."
        self.assertEqual(biblio._plain(chapter), chapter)
        din_chapter = "Müller, Anna: Ein Kapitel. In: Schmidt, Eva (Hrsg.): Ein Buch. Berlin : Verlag, 2020, S. 1–9"
        self.assertEqual(biblio._plain(din_chapter), din_chapter)
        self.assertEqual(biblio._plain("3M Company (2020) Annual report."), "3M Company (2020) Annual report.")
        self.assertEqual(biblio._plain("<i>Nature</i> &amp; more"), "Nature & more")


class ExportTest(unittest.TestCase):
    def test_numbered_styles_get_their_numbers_back_in_a_list(self):
        answers = {"10.1/a": "[1]B. Second, “Title b,” 2020.", "10.1/b": "[1]A. First, “Title a,” 2019."}
        with mock.patch.object(biblio, "_fetch", side_effect=lambda d, a: answers[d]):
            text, missing = biblio.export(["10.1/a", "10.1/b"], "text", "ieee")
        self.assertEqual(missing, [])
        self.assertEqual(text, "[1] A. First, “Title a,” 2019.\n\n[2] B. Second, “Title b,” 2020.\n")

    def test_a_single_entry_has_no_number(self):
        with mock.patch.object(biblio, "_fetch", return_value=RAW["nlm_en"]):
            text, _ = biblio.export(["10.1038/nature14539"], "text", "vancouver")
        self.assertTrue(text.startswith("LeCun Y, Bengio Y"))

    def test_din_is_always_formatted_in_german(self):
        seen = []
        with mock.patch.object(biblio, "_fetch", side_effect=lambda d, a: seen.append(a) or RAW["din"]):
            biblio.export(["10.1038/nature14539"], "text", "din", locale="en-GB")
            biblio.export(["10.1038/nature14539"], "text", "apa", locale="en-GB")
        self.assertIn("style=din-1505-2; locale=de-DE", seen[0])
        self.assertIn("style=apa; locale=en-GB", seen[1])


if __name__ == "__main__":
    unittest.main()
