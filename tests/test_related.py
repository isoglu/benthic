"""Schlüsselwerke, die fehlen: Zählen über die Literaturverzeichnisse der Quellen (Netz ersetzt)."""

import unittest
from unittest import mock

from benthic import related


class Resp:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


WORKS = {"results": [
    {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/a", "referenced_works": ["https://openalex.org/W9", "https://openalex.org/W8", "https://openalex.org/W2"]},
    {"id": "https://openalex.org/W2", "doi": "https://doi.org/10.1/b", "referenced_works": ["https://openalex.org/W9", "https://openalex.org/W7"]},
    {"id": "https://openalex.org/W3", "doi": "https://doi.org/10.1/c", "referenced_works": ["https://openalex.org/W9", "https://openalex.org/W8"]},
]}
META = {"results": [
    {"id": "https://openalex.org/W9", "doi": "https://doi.org/10.1/classic", "display_name": "A classic", "publication_year": 1997,
     "authorships": [{"author": {"display_name": "Sepp Hochreiter"}}], "cited_by_count": 90000,
     "primary_location": {"source": {"display_name": "Neural Computation"}}},
    {"id": "https://openalex.org/W8", "doi": "https://doi.org/10.1/second", "display_name": "Second", "publication_year": 2010,
     "authorships": [], "cited_by_count": 10, "primary_location": None},
    {"id": "https://openalex.org/W2", "doi": "https://doi.org/10.1/b", "display_name": "Already in the list", "publication_year": 2020,
     "authorships": [], "cited_by_count": 5, "primary_location": None},
]}


def fake_get(url, params=None, headers=None, timeout=20.0):
    return Resp(META if params["filter"].startswith("openalex:") else WORKS)


class Fehlend(unittest.TestCase):
    def test_counts_and_excludes_own(self):
        with mock.patch("benthic.oa._api_get", side_effect=fake_get):
            r = related.missing_works(["10.1/a", "10.1/b", "10.1/c"])
        self.assertEqual((r["source"], r["sources"]), ("openalex", 3))
        self.assertEqual([(w["title"], w["count"]) for w in r["suggestions"]], [("A classic", 3), ("Second", 2)])
        self.assertEqual(r["suggestions"][0]["authors"], ["Hochreiter"])

    def test_crossref_fallback(self):
        empty = Resp({"results": []})
        refs = {"10.1/a": {"reference": [{"DOI": "10.1/X"}, {"DOI": "10.1/y"}]}, "10.1/b": {"reference": [{"DOI": "10.1/x"}]}}
        meta = Resp({"message": {"items": [{"DOI": "10.1/x", "title": ["Shared"], "author": [{"family": "Doe"}],
                                            "issued": {"date-parts": [[2001]]}}]}})
        with mock.patch("benthic.oa._api_get", side_effect=lambda url, params=None, **k: empty if "openalex" in url else meta), \
             mock.patch("benthic.verify.crossref_work", side_effect=lambda d, e="": refs.get(d)):
            r = related.missing_works(["10.1/a", "10.1/b"])
        self.assertEqual(r["source"], "crossref")
        self.assertEqual([(w["title"], w["count"]) for w in r["suggestions"]], [("Shared", 2)])

    def test_too_few_sources(self):
        with mock.patch("benthic.oa._api_get", return_value=Resp({"results": []})), \
             mock.patch("benthic.verify.crossref_work", return_value=None):
            self.assertEqual(related.missing_works(["10.1/a"])["suggestions"], [])


if __name__ == "__main__":
    unittest.main()
