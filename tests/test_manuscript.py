"""Manuskripte in Markdown (Obsidian): Zitate als Links, Kursiv in APA, Namenszusätze, graue Literatur."""

import unittest
from unittest import mock

from benthic import document, refs, verify

MD = """---
status: draft
window: Fri 11 Dec 2026
---
%%Notes for myself (Author, 2026) and the debrief (January, 2027).%%

# Introduction

""" + "This chapter reviews the literature that the present study draws upon. " * 40 + """
Adverse selection shapes deals ([Akerlof, 1970](<../80 Reference/notes/Akerlof (1970).md>)).
Identities compete ([Ashforth & Mael, 1989](<../notes/Ashforth & Mael (1989).md>); [[Van Dick et al. (2006)|Van Dick et al., 2006]]).
Identification after a merger matters (Van Knippenberg et al., 2002). The framing paper goes to a workshop (Lisbon, December 2026).

## 7. References

1. Akerlof, G. A. (1970). The market for "lemons": Quality uncertainty and the market mechanism. *Quarterly Journal of Economics, 84*(3), 488–500.
2. Ashforth, B. E., & Mael, F. (1989). Social identity theory and the organization. *Academy of Management Review, 14*(1), 20–39.
3. Van Dick, R., Ullrich, J., & Tissington, P. A. (2006). Working under a black cloud: How to sustain organizational identification after a merger. *British Journal of Management, 17*(S1), S69–S79.
4. Van Knippenberg, D., van Knippenberg, B., Monden, L., & de Lima, F. (2002). Organizational identification after a merger: A social identity perspective. *British Journal of Social Psychology, 41*(2), 233–252.
"""


class Markdown(unittest.TestCase):
    def test_links_comments_and_front_matter(self):
        a = document.analyse(document.extract_text("manuscript.md", MD.encode()))
        self.assertEqual(a["entries"], 4)
        self.assertEqual(a["missing_in_list"], [])      # weder "Author, 2026" noch "Lisbon, December 2026"
        self.assertEqual(a["uncited"], [])              # Links und Wikilinks zählen als Zitate

    def test_pasted_manuscript_is_recognised(self):
        self.assertEqual(document.manuscript(MD)["entries"], 4)
        self.assertIsNone(document.manuscript("1. Akerlof, G. A. (1970). The market for lemons. QJE.\n" * 3))


class Angaben(unittest.TestCase):
    def test_italic_journal_is_not_the_title(self):
        r = refs.parse("58. Williamson, O. E. (1981). The economics of organization: The transaction cost approach. "
                       "*American Journal of Sociology, 87*(3), 548–577.")[0]
        self.assertEqual(r["title"], "The economics of organization: The transaction cost approach")
        self.assertEqual(r["venue"], "American Journal of Sociology")

    def test_italic_book_title_stays(self):
        self.assertEqual(refs.parse("Weick, K. E. (1995). *Sensemaking in organizations*. Sage.")[0]["title"],
                         "Sensemaking in organizations")

    def test_capitalised_particles(self):
        r = refs.parse("Van Dick, R., Ullrich, J., & Tissington, P. A. (2006). Working under a black cloud. *BJM, 17*.")[0]
        self.assertEqual(r["authors"][0], "Van Dick")


class GraueLiteratur(unittest.TestCase):
    def test_chapter_and_magazine(self):
        chapter = verify._grey_literature("Ashforth, B. E. (2001). Which hat to wear? In M. A. Hogg & D. J. Terry (Eds.), "
                                          "Social identity processes (pp. 31–48). Psychology Press.")
        self.assertEqual(chapter[0], "uncertain")
        self.assertEqual(verify._grey_literature("Bekier (2002). Keeping your sales force. McKinsey Quarterly, 4.")[0],
                         "uncertain")
        self.assertIsNone(verify._grey_literature("Smith, J. (2020). An invented article. Journal of Things, 3."))

    def test_links(self):
        raw = "OECD (2025). Education at a Glance 2025. https://www.oecd.org/eag-2025.html."
        snap = {"url": "https://web.archive.org/web/20250910/https://www.oecd.org/eag-2025.html", "date": "2025-09-10"}
        with mock.patch.object(verify, "_fetch_page", return_value=(True, "Education at a Glance 2025 | OECD")), \
                mock.patch.object(verify, "wayback", return_value=snap):
            r = verify._web_source(raw, "Education at a Glance 2025")
        self.assertEqual(r["verdict"], "verified")                      # Seite da, Titel passt
        self.assertEqual(r["web"]["url"], "https://www.oecd.org/eag-2025.html")  # ohne Satzzeichen am Ende
        self.assertEqual(r["web"]["archive_date"], "2025-09-10")
        self.assertTrue(r["web"]["accessed"])
        with mock.patch.object(verify, "_fetch_page", return_value=(True, "Cookie settings")), \
                mock.patch.object(verify, "wayback", return_value=None):
            r = verify._web_source(raw, "Education at a Glance 2025")
        self.assertEqual(r["verdict"], "uncertain")
        self.assertIn("Cookie settings", r["note"])                     # nennt den echten Seitentitel
        with mock.patch.object(verify, "_fetch_page", return_value=(False, "")), \
                mock.patch.object(verify, "wayback", return_value=snap):
            r = verify._web_source(raw, "Education at a Glance 2025")
        self.assertEqual(r["verdict"], "uncertain")                     # umgezogen, nicht erfunden
        self.assertIn("Internet Archive", r["note"])
        with mock.patch.object(verify, "_fetch_page", return_value=(False, "")), \
                mock.patch.object(verify, "wayback", return_value=None):
            self.assertEqual(verify._web_source(raw, "")["verdict"], "not_found")
        self.assertIsNone(verify._web_source("Smith (2020). No link here.", "x"))

    def test_page_title(self):
        page = '<html><head><title>Home | Site</title><meta property="og:title" content="The &amp; Report"></head>'
        self.assertEqual(verify._page_title(page), "The & Report")
        self.assertEqual(verify._page_title("<title>\n  Plain  title </title>"), "Plain title")


if __name__ == "__main__":
    unittest.main()
