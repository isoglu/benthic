"""Preprints: veröffentlichte Fassung über Crossref-Verknüpfung oder Titelsuche (Netz ersetzt)."""

import unittest
from unittest import mock

from benthic import preprints

CVPR = {"DOI": "10.1109/cvpr.2016.90", "type": "proceedings-article", "title": ["Deep Residual Learning for Image Recognition"],
        "author": [{"family": "He"}, {"family": "Zhang"}], "issued": {"date-parts": [[2016, 6]]},
        "container-title": ["2016 IEEE Conference on Computer Vision and Pattern Recognition (CVPR)"]}
OTHER = {"DOI": "10.1/other", "type": "journal-article", "title": ["Deep residual learning for image recognition in plants"],
         "author": [{"family": "He"}], "issued": {"date-parts": [[2019]]}, "container-title": ["Plants"]}


class Resp:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class Fassung(unittest.TestCase):
    def test_servers(self):
        self.assertEqual(preprints.server("10.48550/arXiv.1512.03385"), "arXiv")
        self.assertEqual(preprints.server("10.1101/2020.01.01.123"), "bioRxiv/medRxiv")
        self.assertTrue(preprints.is_preprint("10.5555/x", {"type": "posted-content"}))
        self.assertFalse(preprints.is_preprint("10.1038/nature14539", {"type": "journal-article"}))

    def test_title_search_picks_the_same_paper(self):
        with mock.patch("benthic.oa._api_get", return_value=Resp({"message": {"items": [OTHER, CVPR]}})):
            pub = preprints.published_version("10.48550/arXiv.1512.03385", "Deep Residual Learning for Image Recognition",
                                              ["He", "Zhang"], "2015")
        self.assertEqual((pub["doi"], pub["year"], pub["via"]), ("10.1109/cvpr.2016.90", "2016", "title"))

    def test_other_author_is_not_accepted(self):
        stranger = {**CVPR, "author": [{"family": "Smith"}]}
        with mock.patch("benthic.oa._api_get", return_value=Resp({"message": {"items": [stranger]}})):
            self.assertIsNone(preprints.published_version("10.48550/arXiv.1", "Deep Residual Learning for Image Recognition",
                                                          ["He"], "2015"))

    def test_crossref_relation(self):
        preprint = {"DOI": "10.1101/2020.1", "type": "posted-content", "title": ["A study"],
                    "relation": {"is-preprint-of": [{"id-type": "doi", "id": "10.1/published"}]}}
        published = {"DOI": "10.1/published", "type": "journal-article", "title": ["A study"],
                     "issued": {"date-parts": [[2021]]}, "container-title": ["Cell"]}
        with mock.patch("benthic.verify.crossref_work", side_effect=lambda d, e="": preprint if d.startswith("10.1101") else published):
            pub = preprints.published_version("10.1101/2020.1", "A study", ["Doe"], "2020")
        self.assertEqual((pub["doi"], pub["venue"], pub["via"]), ("10.1/published", "Cell", "relation"))

    def test_check_results_only_confirmed_preprints(self):
        rows = [{"doi": "10.48550/arXiv.1512.03385", "verdict": "verified", "found": {"title": "Deep Residual Learning for Image Recognition", "authors": ["He"], "year": "2015"}},
                {"doi": "10.48550/arXiv.9", "verdict": "not_found"},
                {"doi": "10.1038/nature14539", "verdict": "verified", "found": {"type": "journal-article"}}]
        with mock.patch("benthic.oa._api_get", return_value=Resp({"message": {"items": [CVPR]}})) as get:
            self.assertEqual(preprints.check_results(rows), 1)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(rows[0]["published_version"]["server"], "arXiv")


if __name__ == "__main__":
    unittest.main()
