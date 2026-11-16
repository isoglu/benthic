"""Der Weg zum Download: eingefügte DOI-Listen und die Open-Access-Kaskade (offline)."""

import os
import tempfile
import unittest
from unittest import mock

from benthic import oa, refs

SICI = "10.1002/(sici)1097-0266(199707)18:1+<15::aid-smj916>3.0.co;2-1"
# 17 DOIs wie am 2026-11-01 eingefügt: die SICI-DOI ließ die Liste auf eine einzige DOI zusammenfallen.
DOIS = ["10.1038/s41539-025-00341-2", "10.1073/pnas.2005058117", SICI, "10.1016/S0140-6736(97)11096-0",
        "10.2307/1879431", "10.1257/aer.91.2.73", "10.1086/260062", "10.1287/mnsc.1110.1360",
        "10.1111/j.1540-6261.1970.tb00518.x", "10.1037/0033-295X.84.2.191", "10.1177/0022242918809930",
        "10.1509/jm.15.0420", "10.1145/3292500.3330701", "10.48550/arXiv.1706.03762", "10.1093/qje/qjw013",
        "10.1002/smj.2223", "10.1016/j.jbusres.2019.01.001"]


class PastedList(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name
        from benthic import app as appmod
        self.app = appmod

    def tearDown(self):
        os.environ.pop("BENTHIC_DATA_DIR", None)
        self.tmp.cleanup()

    def test_sici_doi_keeps_the_whole_list(self):
        text = "; ".join(DOIS)
        self.assertEqual(self.app._download_identifiers(text), (DOIS, 0))
        self.assertEqual([r["doi"] for r in refs.parse(text)], DOIS)  # Vorschau und „Prüfen“ lesen dasselbe
        preview = self.app.app.test_client().post("/api/refs/parse", json={"text": text}).get_json()
        self.assertEqual(preview["summary"]["count"], 17)

    def test_one_odd_token_does_not_collapse_the_list(self):
        ids, unresolved = self.app._download_identifiers("; ".join(DOIS[:5] + ["2-1"] + DOIS[5:]))
        self.assertEqual((ids, unresolved), (DOIS, 1))

    def test_separators_and_forms(self):
        cases = {
            "10.1038/a;10.1126/b": ["10.1038/a", "10.1126/b"],
            "doi:10.1038/a, https://doi.org/10.1126/b\n10.1038/a": ["10.1038/a", "10.1126/b"],
            "10.1038/a 10.1126/b": ["10.1038/a", "10.1126/b"],
            "https://x.org/a?b=1;c=2, 10.1126/b": ["https://x.org/a?b=1;c=2", "10.1126/b"],
            "arXiv:1706.03762; arXiv:1512.03385": ["10.48550/arXiv.1706.03762", "10.48550/arXiv.1512.03385"],
        }
        for text, want in cases.items():
            self.assertEqual(self.app._download_identifiers(text), (want, 0), text)

    def test_references_still_go_through_the_parser(self):
        apa = ("Harris, C. R., Millman, K. J., & van der Walt, S. J. (2020). Array programming with NumPy. "
               "Nature, 585, 357-362. https://doi.org/10.1038/s41586-020-2649-2")
        self.assertIsNone(refs.identifier_list(apa))
        bib = "@article{h, title = {Array programming}, doi = {10.1038/s41586-020-2649-2}, year = {2020}}"
        self.assertIsNone(refs.identifier_list(bib))
        ris = "TY  - JOUR\nTI  - Array programming\nDO  - 10.1038/s41586-020-2649-2\nER  -"
        self.assertIsNone(refs.identifier_list(ris))
        r = refs.parse(apa)[0]
        self.assertEqual((r["doi"], r["title"]), ("10.1038/s41586-020-2649-2", "Array programming with NumPy"))
        self.assertEqual(self.app._download_identifiers(apa + "\nVaswani, A. (2017). Attention is all you need."),
                         (["10.1038/s41586-020-2649-2"], 1))


class KeylessCascade(unittest.TestCase):
    def _asked(self, providers, **keys):
        asked = []
        fakes = {n: (lambda doi, ctx, n=n: asked.append(n) or ([], [])) for n in oa.PROVIDERS}
        with mock.patch.dict(oa._PROVIDERS, fakes):
            oa.resolve_candidates("10.1038/s41539-025-00341-2", providers, **keys)
        return asked

    def test_only_keyless_core_falls_back_to_the_full_cascade(self):
        # 2026-11-01: gespeichert war nur CORE, ohne Key (429), also hieß jede DOI "kein Open Access".
        self.assertEqual(self._asked(["core"]), oa.PROVIDERS)
        self.assertEqual(self._asked(["core", "elsevier"]), oa.PROVIDERS)

    def test_a_chosen_cascade_that_can_answer_stays(self):
        self.assertEqual(self._asked(["core"], core_api_key="k"), ["core"])
        self.assertEqual(self._asked(["openalex", "core"]), ["openalex", "core"])


if __name__ == "__main__":
    unittest.main()
