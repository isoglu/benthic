"""Kennzahlen der Übersicht aus einem künstlichen Verlauf (offline)."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path


class Stats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name
        now = time.time()
        verify_run = {"id": "2", "mode": "verify", "finished_at": now, "project": "p1", "results": [
            {"doi": "10.1/a", "verdict": "verified", "resolved_by": "doi"},
            {"doi": "10.1/b", "verdict": "corrected", "resolved_by": "title"},
            {"doi": "10.1/c", "verdict": "retracted", "resolved_by": "doi", "claimed": {"title": "Bad"}},
            {"doi": None, "identifier": "Invented", "verdict": "not_found", "claimed": {"title": "Invented"}},
        ]}
        download_run = {"id": "1", "mode": "download", "finished_at": now - 10, "project": "p1", "results": [
            {"identifier": "10.1/a", "status": "ok", "channel": "oa", "source": "openalex"},
            {"identifier": "10.1/b", "status": "blocked", "channel": "oa"},
            {"identifier": "10.1/d", "status": "ok", "channel": "demo", "source": "https://x"},
            {"identifier": "10.1/e", "status": "absent"},
        ]}
        old_run = {"id": "0", "mode": "verify", "finished_at": now - 40 * 86400, "results": [
            {"doi": "10.1/z", "verdict": "verified"}]}
        Path(self.tmp.name, "history.json").write_text(json.dumps([verify_run, download_run, old_run]))
        Path(self.tmp.name, "library.json").write_text(json.dumps({"entries": {
            "10.1/a": {"doi": "10.1/a", "filename": "a.pdf", "channel": "oa"},
            "10.1/d": {"doi": "10.1/d", "filename": "d.pdf", "channel": "demo"}}}))
        Path(self.tmp.name, "projects.json").write_text(json.dumps({"active": "p1", "projects": {"p1": {
            "id": "p1", "name": "Referat", "items": {"10.1/a": {"doi": "10.1/a"}, "10.1/b": {"doi": "10.1/b"}}}}}))
        from benthic import app as appmod
        self.client = appmod.app.test_client()

    def tearDown(self):
        os.environ.pop("BENTHIC_DATA_DIR", None)
        self.tmp.cleanup()

    def test_funnel_and_shares(self):
        s = self.client.get("/api/stats").get_json()
        self.assertEqual([f["value"] for f in s["funnel"]], [5, 3, 2, 1])  # inkl. alter Lauf (10.1/z)
        self.assertEqual(s["kpi"]["retracted"], 1)
        self.assertEqual(s["kpi"]["legal_share"], 50)  # 1 Open Access von 2 geladenen
        self.assertEqual(s["downloads"], {"oa": 1, "library": 0, "blocked": 1, "route": 1, "none": 1})
        self.assertEqual(s["sources"], [{"name": "OpenAlex", "value": 1}])
        self.assertEqual(s["routes"]["title"], 1)
        self.assertEqual({f["verdict"] for f in s["flagged"]}, {"retracted", "not_found"})

    def test_scope_by_time_and_project(self):
        s = self.client.get("/api/stats?days=30").get_json()
        self.assertEqual(s["funnel"][0]["value"], 4)
        p = self.client.get("/api/stats?project=Referat").get_json()
        self.assertEqual(p["scope"]["project_name"], "Referat")
        self.assertEqual(p["library"]["total"], 1)
        self.assertEqual(self.client.get("/api/stats?project=nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()
