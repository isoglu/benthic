"""Entkommene Fehler (tests/escapes.json): jede Fehlerklasse, die Nutzer:innen erreicht hat, mit dem Test,
der sie jetzt fängt. Die Liste wächst nur; eine Klasse ohne Test bleibt bei jedem Lauf als Warnung sichtbar.
"""

import importlib
import json
import subprocess
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFECTS = json.loads((ROOT / "tests" / "escapes.json").read_text(encoding="utf-8"))["defects"]
CASES = {c["id"]: c for c in json.loads((ROOT / "tests" / "corpus" / "cases.json").read_text(encoding="utf-8"))["cases"]}


def _resolves(owner: str) -> bool:
    module, cls, method = owner.rsplit(".", 2)
    try:
        return callable(getattr(getattr(importlib.import_module(module), cls), method))
    except (ImportError, AttributeError):
        return False


class Entkommen(unittest.TestCase):
    def test_entries(self):
        ids = [d["id"] for d in DEFECTS]
        self.assertEqual(ids, [f"E-{i:03d}" for i in range(1, len(ids) + 1)], "fortlaufend ab E-001")
        for d in DEFECTS:
            with self.subTest(d["id"]):
                for field in ("found", "found_by", "class", "example", "escaped_because"):
                    self.assertTrue(str(d.get(field) or "").strip(), f"{field} fehlt")
                if d["owner"] is None:
                    warnings.warn(f"{d['id']} hat noch keinen Test: {d['class']}")
                else:
                    self.assertTrue(_resolves(d["owner"]), f"owner {d['owner']} gibt es nicht")
                if d.get("case"):
                    case = CASES.get(d["case"])
                    self.assertIsNotNone(case, f"Fall {d['case']} fehlt im Korpus")
                    self.assertEqual(case.get("known") == d["id"], d["owner"] is None,
                                     "known im Fall genau dann, wenn owner null ist")
        for case in CASES.values():
            if case.get("known"):
                self.assertIn(case["known"], ids, case["id"])

    def test_add_only(self):
        old = subprocess.run(["git", "-C", str(ROOT), "show", "HEAD:tests/escapes.json"],
                             capture_output=True, text=True)
        if old.returncode != 0:
            self.skipTest("kein Git-Stand mit tests/escapes.json")
        now = {d["id"]: d for d in DEFECTS}
        for d in json.loads(old.stdout)["defects"]:
            self.assertIn(d["id"], now, f"{d['id']} gelöscht: Einträge bleiben, auch behobene")
            self.assertEqual(now[d["id"]]["class"], d["class"], f"{d['id']}: Klasse umgeschrieben")


if __name__ == "__main__":
    unittest.main()
