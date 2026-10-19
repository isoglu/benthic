"""Quellen finden: echte Arbeiten zu einer Aussage, mit dem passendsten Satz aus dem Abstract (offline)."""

import unittest
from unittest import mock

from benthic import finder

WORKS = {"results": [
    {"doi": "https://doi.org/10.1/general", "title": "A general overview of chatbots", "publication_year": 2023,
     "authorships": [{"author": {"display_name": "A. One"}}], "open_access": {"is_oa": False},
     "abstract_inverted_index": {"Chatbots": [0], "are": [1], "popular.": [2], "They": [3], "answer": [4], "questions": [5], "quickly.": [6]}},
    {"doi": "https://doi.org/10.1/refs", "title": "Fabricated references in chatbot answers", "publication_year": 2024,
     "authorships": [{"author": {"display_name": "B. Two"}}], "open_access": {"is_oa": True, "oa_url": "https://x.org/p.pdf"},
     "abstract_inverted_index": {"We": [0], "show": [1], "that": [2], "language": [3], "models": [4], "often": [5],
                                 "fabricate": [6], "bibliographic": [7], "references.": [8]}},
    {"doi": None, "title": "No DOI"},
    {"doi": "https://doi.org/10.1/retracted", "title": "Retracted one", "is_retracted": True},
]}


class Finder(unittest.TestCase):
    def test_abstract(self):
        self.assertEqual(finder.abstract({"b": [1], "a": [0], "c": [2, 3]}), "a b c c")
        self.assertEqual(finder.abstract(None), "")

    def test_find_ranks_by_abstract_match(self):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = WORKS
        with mock.patch.object(finder.oa, "_api_get", return_value=resp) as get:
            r = finder.find("Language models often fabricate bibliographic references (Walters, 2023).", open_access_only=True)
        params = get.call_args.kwargs["params"]
        self.assertIn("is_oa:true", params["filter"])
        self.assertIn("is_retracted:false", params["filter"])
        self.assertNotIn("Walters", params["search"])                  # Zitierklammer nicht mitsuchen
        self.assertEqual([s["doi"] for s in r["suggestions"]], ["10.1/refs", "10.1/general"])  # passender Abstract zuerst
        self.assertIn("fabricate bibliographic", r["suggestions"][0]["passage"]["text"])
        self.assertEqual(r["suggestions"][0]["oa_url"], "https://x.org/p.pdf")

    def test_short_or_unavailable(self):
        self.assertEqual(finder.find("too short")["error"], "short")
        with mock.patch.object(finder.oa, "_api_get", return_value=None):
            self.assertEqual(finder.find("language models invent many references")["error"], "unavailable")


if __name__ == "__main__":
    unittest.main()
