"""Kontingente: zählen, Kontingent lesen, pausieren, Protokoll ohne Geheimnisse (offline)."""

import os
import tempfile
import time
import unittest
from unittest import mock

from benthic import quota


class Kontingent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()
        quota._state.clear()
        quota._log.clear()
        quota._last_call.clear()

    def tearDown(self):
        quota._state.clear()
        self.env.stop()
        self.tmp.cleanup()

    def test_services(self):
        self.assertEqual(quota.service_of("https://api.openalex.org/works?filter=x"), "openalex")
        self.assertEqual(quota.service_of("https://api.crossref.org/works/10.1/x"), "crossref")
        self.assertEqual(quota.service_of("http://127.0.0.1:23119/connector/ping"), "zotero")
        self.assertEqual(quota.service_of("https://www.nature.com/articles/x.pdf"), "web")

    def test_counts_remaining_and_log_without_secrets(self):
        url = "https://api.openalex.org/works?filter=doi:10.1/x&mailto=me@uni.de&api_key=SECRET"
        quota.after(url, 200, {"X-RateLimit-Remaining": "812", "X-RateLimit-Limit": "1000"}, 0.2)
        st = quota.status()
        oa = next(s for s in st["services"] if s["key"] == "openalex")
        self.assertEqual((oa["today"], oa["remaining"], oa["limit"]), (1, 812.0, 1000.0))
        logged = st["log"][0]["url"]
        self.assertNotIn("me@uni.de", logged)
        self.assertNotIn("SECRET", logged)
        self.assertIn("filter=doi:10.1/x", logged)

    def test_budget_exhausted_blocks_until_reset(self):
        url = "https://api.openalex.org/works?filter=x"
        quota.after(url, 429, {"Retry-After": "3600"}, 0.1, body_start='{"error":"Rate limit exceeded","message":"Insufficient budget."}')
        with self.assertRaises(quota.QuotaBlocked):
            quota.before(url)
        self.assertEqual(quota.paused("openalex")["reason"], "budget")
        quota.reset_pause("openalex")
        quota.before(url)  # wieder frei

    def test_short_rate_pause_waits_instead_of_skipping(self):
        url = "https://api.crossref.org/works?query=x"
        quota.after(url, 429, {"Retry-After": "0"}, 0.1)
        quota._state["crossref"]["blocked_until"] = time.time() + 0.3
        started = time.time()
        quota.before(url)  # wartet, statt Crossref auszulassen
        self.assertGreaterEqual(time.time() - started, 0.25)

    def test_problems_are_tracked_per_check(self):
        quota.track()
        quota.after("https://api.crossref.org/works?query=x", None, None, 0.1)
        self.assertEqual([p["service"] for p in quota.problems()], ["crossref"])
        quota.track()
        self.assertEqual(quota.problems(), [])


class Urteil(unittest.TestCase):
    def test_crossref_down_means_uncertain_not_invented(self):
        from benthic import refs, verify

        def down(url, params=None, headers=None, timeout=20.0):
            quota.after(url, None, None, 0.1)
            return None
        with mock.patch("benthic.oa._api_get", side_effect=down), \
             mock.patch("benthic.verify._search_arxiv", return_value=[]):
            r = verify.verify_ref(refs.parse("Doe, J. (2020). A study nobody can find right now. Journal of Things, 1, 1-2.")[0])
        self.assertEqual(r["verdict"], "uncertain")
        self.assertIn("Crossref", r["note"])


if __name__ == "__main__":
    unittest.main()
