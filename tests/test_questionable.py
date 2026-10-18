"""Fragwürdige Zeitschriften: DOAJ-Listen und gekaperte Websites lesen und abgleichen (offline)."""

import os
import tempfile
import unittest
from unittest import mock

from benthic import config, questionable as q

WITHDRAWN = [
    ["", "", "This data is licensed with a CC BY-SA 4.0 license", ""],
    ["A change log showing journals withdrawn from DOAJ", "", "", ""],
    ["Journal Title", "ISSN", "Date Removed (dd/mm/yyyy)", "Reason"],
    ["", "", "", ""],
    ["New Proposals", "1715-6718", "13-October-2026", "Journal not adhering to Best practice"],
    ["Organic Materials", "2625-1825", "13-October-2026", "Ceased publishing"],
    ["Old Journal", "1234-5679", "18-Jun-2015", "journal not adhering to best practice"],
    ["Quarterly", "2222-3333", "Q1, 2014", "Inactive"],
]
FALSE = [["", ""], ["Journals and publishers that falsely claim they are indexed in DOAJ", ""],
         ["Journal Title", "ISSN"], ["Abjad", "3030-9522"], ["Two ISSNs", "1111-2222, 3333-444X"]]
HIJACKED = [
    ["First created: May 30, 2022", "donation", "", "", "", "", ""],
    ["", "Hijacked Journal Title", "URL (Hijacked)", "ISSN (Hijacked)", "Original journal", "ISSN (Original)", " URL (Original Journal)"],
    ["1", "Academy of Management Annals", "https://aomannals.com/", "1941-6520", "Academy of Management Annals", "1941-6520",
     "https://journals.aom.org/journal/annals"],
    ["2", "Acta Scientiae", "http://www.periodicos.ulbra.org/index.php/acta/index", "1517-4492", "Acta Scientiae", "", ""],
]


class Listen(unittest.TestCase):
    def test_withdrawn(self):
        w = q.parse_withdrawn(WITHDRAWN)
        self.assertTrue(w["1715-6718"]["best_practice"])
        self.assertFalse(w["2625-1825"]["best_practice"])
        self.assertEqual(w["1234-5679"]["date"], "2015-06-18")
        self.assertEqual(w["2222-3333"]["date"], "2014-01")

    def test_false_claims(self):
        f = q.parse_false(FALSE)
        self.assertEqual(set(f), {"3030-9522", "1111-2222", "3333-444X"})

    def test_hijacked_hosts_and_paths(self):
        h = q.parse_hijacked(HIJACKED)
        self.assertEqual(h["aomannals.com"][0]["path"], "")
        self.assertEqual(h["periodicos.ulbra.org"][0]["path"], "/index.php/acta")


class Abgleich(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name, "BENTHIC_OFFLINE": "1"})
        self.env.start()
        q._memory.clear()
        q._memory.update({
            "doaj_withdrawn": {"fetched_at": 1, "data": q.parse_withdrawn(WITHDRAWN)},
            "doaj_false": {"fetched_at": 1, "data": q.parse_false(FALSE)},
            "hijacked": {"fetched_at": 1, "data": q.parse_hijacked(HIJACKED)},
        })

    def tearDown(self):
        q._memory.clear()
        self.env.stop()
        self.tmp.cleanup()

    def test_journal_flags(self):
        self.assertEqual([f["kind"] for f in q.journal_flags(["1715-6718"])], ["doaj_removed"])
        self.assertEqual(q.journal_flags(["1715-6718"], in_doaj_now=True), [])  # wieder aufgenommen
        self.assertEqual(q.journal_flags(["2625-1825"]), [])                      # eingestellt, kein Urteil
        self.assertEqual([f["kind"] for f in q.journal_flags(["3333-444x"])], ["doaj_false"])
        config.save_settings({"journal_lists": False})
        self.assertEqual(q.journal_flags(["1715-6718"]), [])

    def test_hijacked_only_when_enabled(self):
        ref = "Smith, J. (2021). Leadership. Academy of Management Annals. https://www.aomannals.com/article/view/12"
        self.assertEqual(q.hijacked_in(ref), [])
        config.save_settings({"hijacked_check": True})
        hits = q.hijacked_in(ref)
        self.assertEqual(hits[0]["original"], "Academy of Management Annals")
        self.assertEqual(q.hijacked_in("See https://journals.aom.org/journal/annals"), [])
        self.assertTrue(q.hijacked_in("http://periodicos.ulbra.org/index.php/acta/article/view/5"))
        self.assertEqual(q.hijacked_in("http://periodicos.ulbra.org/index.php/other/article/view/5"), [])
        self.assertEqual(q.hijacked_in("https://doi.org/10.1/aomannals.com"), [])

    def test_health_note(self):
        from benthic import journals
        venue = {"type": "journal", "issn": ["1715-6718"], "name": "New Proposals"}
        h = journals.health([{"verdict": "verified", "year": 2024, "venue": venue,
                              "flags": q.journal_flags(venue["issn"])}])
        dim = next(d for d in h["dimensions"] if d["key"] == "venues")
        self.assertEqual(dim["state"], "notes")
        self.assertIn("DOAJ", dim["detail"])


if __name__ == "__main__":
    unittest.main()
