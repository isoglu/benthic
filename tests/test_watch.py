"""Rückzugswache: Mitteilungen lesen, Funde merken, Projektquellen markieren (Netz ersetzt)."""

import os
import tempfile
import unittest
from unittest import mock

WAKEFIELD = "10.1016/S0140-6736(97)11096-0"
NUMPY = "10.1038/s41586-020-2649-2"
QUIET = "10.1234/only-openalex"

CROSSREF = {"message": {"items": [
    {"DOI": WAKEFIELD.lower(), "title": ["Ileal-lymphoid-nodular hyperplasia"], "updated-by": [
        {"DOI": "10.1016/s0140-6736(04)15715-2", "type": "correction", "source": "retraction-watch",
         "updated": {"date-parts": [[2004, 3, 6]]}},
        {"DOI": "10.1016/s0140-6736(10)60175-4", "type": "retraction", "source": "retraction-watch",
         "updated": {"date-parts": [[2010, 2, 6]]}},
        {"DOI": "10.1016/S0140-6736(10)60175-4", "type": "Retraction", "source": "publisher",
         "updated": {"date-parts": [[2010, 2, 6]]}},
        {"DOI": "10.1016/x-new-version", "type": "new_version", "source": "publisher"}]},
    {"DOI": NUMPY, "title": ["Array programming with NumPy"]},
]}}
OPENALEX = {"results": [
    {"doi": "https://doi.org/" + WAKEFIELD.lower(), "is_retracted": True},
    {"doi": "https://doi.org/" + NUMPY, "is_retracted": False},
    {"doi": "https://doi.org/" + QUIET, "is_retracted": True, "title": "Quietly retracted"},
]}


class Resp:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def fake_get(url, params=None, headers=None, timeout=20.0):
    return Resp(CROSSREF if "crossref" in url else OPENALEX)


class Wache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("BENTHIC_DATA_DIR")
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name
        from benthic import projects, watch
        self.projects, self.watch = projects, watch
        watch.PAUSE = 0
        self.patch = mock.patch("benthic.oa._api_get", side_effect=fake_get)
        self.patch.start()
        p = projects.create("Impfdebatte")
        projects.add_items(p["id"], [{"doi": WAKEFIELD, "title": "Wakefield 1998", "verdict": "verified"},
                                     {"doi": NUMPY, "verdict": "verified"}, {"doi": QUIET}])
        self.pid = p["id"]

    def tearDown(self):
        self.patch.stop()
        if self._old is None:
            os.environ.pop("BENTHIC_DATA_DIR", None)
        else:
            os.environ["BENTHIC_DATA_DIR"] = self._old
        self.tmp.cleanup()

    def test_notices_are_deduplicated_and_filtered(self):
        from benthic import watch
        notices = watch.notices_from_crossref(CROSSREF["message"]["items"][0])
        self.assertEqual([n["kind"] for n in notices], ["retraction", "correction"])
        self.assertEqual(notices[0]["source"], ["publisher", "retraction-watch"])
        self.assertEqual(notices[0]["date"], "2010-02-06")

    def test_run_records_findings_once(self):
        st = self.watch.run(force=True)
        self.assertEqual(st["new"], 3)  # Rückzug und Korrektur (Wakefield), Rückzug nur bei OpenAlex
        kinds = sorted((f["doi"].lower(), f["kind"]) for f in st["findings"])
        self.assertIn((QUIET, "retraction"), kinds)
        self.assertNotIn(NUMPY, [d for d, _ in kinds])
        self.assertEqual(st["unseen"], 3)
        self.assertEqual(self.watch.run(force=True)["new"], 0)  # schon bekannt
        self.assertEqual(self.watch.mark_seen(), 3)
        self.assertEqual(self.watch.status()["unseen"], 0)

    def test_project_items_are_marked(self):
        self.watch.run(force=True)
        items = {i["doi"].lower(): i for i in self.projects.detail(self.pid)["items"]}
        self.assertEqual(items[WAKEFIELD.lower()]["verdict"], "retracted")
        self.assertEqual([n["kind"] for n in items[WAKEFIELD.lower()]["notices"]], ["retraction", "correction"])
        self.assertEqual(items[NUMPY]["verdict"], "verified")
        self.assertEqual(items[NUMPY]["notices"], [])

    def test_not_due_twice_a_day(self):
        self.watch.run(force=True)
        with mock.patch.object(self.watch, "scan") as scan:
            self.watch.run()
            scan.assert_not_called()

    def test_failed_lookup_keeps_state(self):
        with mock.patch("benthic.oa._api_get", return_value=None):
            st = self.watch.run(force=True)
        self.assertEqual(st["new"], 0)
        self.assertEqual(st["last_failed"], 3)

    def test_routes(self):
        from benthic import app as appmod
        client = appmod.app.test_client()
        self.assertEqual(client.get("/api/watch").get_json()["watched"], 3)
        self.watch.run(force=True)
        self.assertEqual(client.post("/api/watch/seen", json={}).get_json()["marked"], 3)


if __name__ == "__main__":
    unittest.main()
