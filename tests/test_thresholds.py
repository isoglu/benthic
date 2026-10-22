"""Die Schwellen der Prüfungen stehen in tests/thresholds.json, mit der Richtung, in die sie strenger werden.

Eine Konstante zu ändern ist ein Tastendruck und sieht im Diff aus wie Feinabstimmung. Deshalb scheitert
dieser Test bei jeder Abweichung und sagt, was die Änderung bewirkt; die Tabelle wird erst angepasst,
wenn das Korpus (python -m tests.test_corpus --score) vorher und nachher gelaufen ist.
"""

import importlib
import json
import unittest
from pathlib import Path

TABLE = json.loads((Path(__file__).resolve().parent / "thresholds.json").read_text(encoding="utf-8"))["constants"]


class Schwellen(unittest.TestCase):
    def test_every_threshold_is_as_declared(self):
        for name, spec in TABLE.items():
            with self.subTest(name):
                module, const = name.rsplit(".", 1)
                value = getattr(importlib.import_module(module), const, None)
                self.assertIsNotNone(value, f"{name} gibt es nicht mehr: Tabelle anpassen")
                if value == spec["value"]:
                    continue
                stricter = (value > spec["value"]) == (spec["stricter"] == "higher")
                effect = ("strenger: mehr Fehlalarme bei echten Arbeiten" if stricter
                          else "lockerer: echte Probleme rutschen durch")
                self.fail(f"{name} ist {value} statt {spec['value']} ({effect}). Korpus vorher und nachher laufen "
                          f"lassen, dann tests/thresholds.json anpassen.")

    def test_table_is_well_formed(self):
        for name, spec in TABLE.items():
            self.assertIn(spec["stricter"], ("higher", "lower"), name)
            self.assertGreaterEqual(len(spec["why"]), 30, f"{name}: warum diese Schwelle?")


if __name__ == "__main__":
    unittest.main()
