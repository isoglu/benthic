"""Beobachtete Dateien: nur bei geänderten Angaben neu prüfen, neue Probleme melden (offline)."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from benthic import watchfile


def analysis(refs, missing=()):
    return {"entries": len(refs), "refs": [{"raw": r} for r in refs], "claims": [], "quotes": [],
            "missing_in_list": [{"label": m} for m in missing]}


class Beobachten(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()
        self.file = Path(self.tmp.name) / "thesis.docx"
        self.file.write_text("v1")
        self.current = analysis(["A (2020)", "B (2021)"])
        self.jobs = {}
        self.launched = []

        def launch(path, a):
            job_id = str(len(self.launched) + 1)
            self.launched.append(a)
            self.jobs[job_id] = {"status": "running"}
            return job_id
        watchfile.configure(lambda p: self.current, launch, lambda j: self.jobs.get(j))
        watchfile.add(str(self.file))

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def finish(self, job_id, missing=(), bad=()):
        self.jobs[job_id] = {"status": "done", "document": {"missing_in_list": [{"label": m} for m in missing]},
                             "results": [{"verdict": "not_found", "input": b, "claimed": {"title": b}} for b in bad]}

    def save(self, text):
        self.file.write_text(text)
        t = time.time() - 10
        os.utime(self.file, (t, t))

    def test_first_check_then_only_on_reference_changes(self):
        self.save("v1")
        watchfile.tick()
        self.assertEqual(len(self.launched), 1)
        self.finish("1", missing=["Brown, 2021"])
        watchfile.tick()
        f = watchfile.status()["files"][0]
        self.assertEqual([i["label"] for i in f["new"]], ["Brown, 2021"])
        self.assertTrue(f["unseen"])
        self.save("v2: nur Fließtext geändert")
        os.utime(self.file, (time.time() - 5, time.time() - 5))
        watchfile.tick()
        self.assertEqual(len(self.launched), 1)          # Angaben gleich: keine neue Prüfung

        self.current = analysis(["A (2020)", "B (2021)", "C (2022)"])
        self.save("v3")
        watchfile.tick()
        self.assertEqual(len(self.launched), 2)
        self.finish("2", bad=["C (2022)"])
        watchfile.tick()
        f = watchfile.status()["files"][0]
        self.assertEqual([i["kind"] for i in f["new"]], ["not_found"])
        self.assertEqual(f["resolved"], 1)               # Brown ist inzwischen eingetragen

    def test_waits_until_the_file_settles(self):
        self.file.write_text("being written")
        watchfile.tick()
        self.assertEqual(self.launched, [])

    def test_latex_stamp_covers_the_project(self):
        tex = Path(self.tmp.name) / "main.tex"
        bib = Path(self.tmp.name) / "refs.bib"
        tex.write_text("x")
        bib.write_text("y")
        os.utime(tex, (1000, 1000))
        os.utime(bib, (2000, 2000))
        self.assertEqual(watchfile.stamp(tex), 2000)

    def test_limits(self):
        with self.assertRaises(ValueError):
            watchfile.add(str(Path(self.tmp.name) / "missing.pdf"))


if __name__ == "__main__":
    unittest.main()
