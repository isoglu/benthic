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
        with mock.patch.object(verify, "_url_reachable", return_value=True):
            self.assertEqual(verify._grey_literature("Report (2025). https://example.org/report")[0], "uncertain")
        with mock.patch.object(verify, "_url_reachable", return_value=False):
            self.assertEqual(verify._grey_literature("Report (2025). https://example.org/report")[0], "not_found")


if __name__ == "__main__":
    unittest.main()
