"""Publikationsorte, Rankinglisten und Gesundheit einer Literaturliste (offline)."""

import os
import tempfile
import unittest

from benthic import journals

from tests import ui_client


class Normalisierung(unittest.TestCase):
    def test_issn(self):
        self.assertEqual(journals.norm_issn("00280836"), "0028-0836")
        self.assertEqual(journals.norm_issn("ISSN 1476-4687 "), "1476-4687")
        self.assertEqual(journals.norm_issn("2331-842x"), "2331-842X")
        self.assertIsNone(journals.norm_issn("12345"))
        self.assertIsNone(journals.norm_issn("X234-5678"))

    def test_title(self):
        self.assertEqual(journals.norm_title("The Journal of Finance"), "journal of finance")
        self.assertEqual(journals.norm_title("Strategic  Management Journal."), "strategic management journal")
        self.assertEqual(journals.norm_title("Économie & Société"), "economie and societe")


class OpenAlex(unittest.TestCase):
    def test_source_mapping(self):
        v = journals.source_from_openalex({
            "id": "https://openalex.org/S137773608", "display_name": "Nature", "issn_l": "0028-0836",
            "issn": ["0028-0836", "1476-4687"], "type": "journal", "is_in_doaj": False, "is_core": True,
            "summary_stats": {"2yr_mean_citedness": 19.3907, "h_index": 1839}, "host_organization_name": "Nature Portfolio"})
        self.assertEqual(v["issn"], ["0028-0836", "1476-4687"])
        self.assertEqual(v["citedness"], 19.39)
        self.assertFalse(v["preprint"])
        self.assertTrue(v["core"])

    def test_arxiv_needs_no_network(self):
        orig = journals._get
        journals._get = lambda *a, **k: self.fail("arXiv-DOIs brauchen keine Anfrage")
        try:
            out = journals.venues_for_dois(["10.48550/arXiv.1706.03762"])
        finally:
            journals._get = orig
        v = out["10.48550/arxiv.1706.03762"]
        self.assertTrue(v["preprint"])
        self.assertEqual(v["name"], "arXiv")


class Rankinglisten(unittest.TestCase):
    CSV = ("Liste des Fachbereichs, Stand 2025\n\n"
           "Journal Title;Print ISSN;Online ISSN;Rating\n"
           "Academy of Management Journal;0001-4273;1948-0989;A*\n"
           "The Journal of Finance;00221082;;A*\n"
           "Kleine Zeitschrift;;;C\n"
           ";;;B\n").encode("utf-8")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("BENTHIC_DATA_DIR", None)
        self.tmp.cleanup()

    def test_parse_skips_preamble_and_empty_titles(self):
        entries = journals.parse_ranking_rows(journals.read_table("abdc.csv", self.CSV))
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["issn"], ["0001-4273", "1948-0989"])
        self.assertEqual(entries[1]["issn"], ["0022-1082"])

    def test_import_match_delete(self):
        meta = journals.import_ranking("ABDC 2022", "abdc.csv", self.CSV)
        self.assertEqual(meta["count"], 3)
        lists = journals.list_rankings(with_entries=True)
        self.assertEqual(len(lists), 1)
        by_issn = journals.match_rankings(lists, ["1948-0989"], "AMJ")
        self.assertEqual(by_issn, [{"list": "ABDC 2022", "rank": "A*"}])
        by_title = journals.match_rankings(lists, [], "Journal of Finance")
        self.assertEqual(by_title[0]["rank"], "A*")
        self.assertEqual(journals.match_rankings(lists, ["1234-5678"], "Unbekannt"), [])
        self.assertTrue(journals.delete_ranking(meta["id"]))
        self.assertEqual(journals.list_rankings(), [])

    def test_rejects_table_without_rank(self):
        with self.assertRaises(ValueError):
            journals.parse_ranking_rows([["Title", "Publisher"], ["Nature", "Springer"]])


JOURNAL = {"type": "journal", "preprint": False, "doaj": False}
PREPRINT = dict(journals.ARXIV_SOURCE)


class Gesundheit(unittest.TestCase):
    """Texte auf Deutsch (Quelltext); die englische Fassung prüft test_english_texts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("BENTHIC_DATA_DIR")
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name
        from benthic import config
        config.save_settings({"language": "de"})

    def tearDown(self):
        if self._old is None:
            os.environ.pop("BENTHIC_DATA_DIR", None)
        else:
            os.environ["BENTHIC_DATA_DIR"] = self._old
        self.tmp.cleanup()

    def dims(self, h):
        return {d["key"]: d for d in h["dimensions"]}

    def test_english_texts(self):
        from benthic import config
        config.save_settings({"language": "en"})
        items = [{"verdict": "retracted", "year": "1998", "venue": JOURNAL},
                 {"verdict": "not_found", "year": "2021"}]
        h = journals.health(items, this_year=2026)
        self.assertEqual(h["label"], "Critical")
        self.assertEqual(self.dims(h)["real"]["headline"], "2 sources cannot be cited")
        self.assertEqual(self.dims(h)["real"]["detail"], "1 retracted, 1 not found.")

    def test_retracted_source_is_critical(self):
        items = [{"verdict": "verified", "year": "2022", "venue": JOURNAL, "has_pdf": True},
                 {"verdict": "retracted", "year": "1998", "venue": JOURNAL},
                 {"verdict": "not_found", "year": "2021"}]
        h = journals.health(items, this_year=2026)
        self.assertEqual(h["state"], "critical")
        self.assertEqual(h["label"], "Kritisch")
        real = self.dims(h)["real"]
        self.assertEqual(real["headline"], "2 Quellen sind nicht zitierfähig")
        self.assertIn("1 zurückgezogen", real["detail"])
        self.assertEqual(self.dims(h)["access"]["headline"], "1 von 3 als PDF in der Bibliothek")

    def test_age_never_critical_only_a_note(self):
        items = [{"verdict": "verified", "year": y, "venue": JOURNAL} for y in ("1953", "1987", "1998", "2010")]
        h = journals.health(items, this_year=2026)
        age = self.dims(h)["age"]
        self.assertEqual(age["state"], "notes")
        self.assertIn("Keine Quelle aus den letzten 5 Jahren", age["detail"])
        self.assertEqual(h["state"], "notes")
        self.assertEqual([b["count"] for b in h["age"]["buckets"]], [0, 0, 1, 3])

    def test_healthy_list(self):
        items = [{"verdict": "verified", "year": y, "venue": JOURNAL, "jufo": 2} for y in ("2016", "2021", "2022", "2025")]
        h = journals.health(items, this_year=2026)
        self.assertEqual(h["state"], "ok")
        self.assertEqual(h["label"], "Gesund")
        self.assertEqual(h["age"]["median"], 4.5)
        self.assertEqual(h["age"]["share_recent"], 75)
        self.assertIn("Median 4,5 Jahre", self.dims(h)["age"]["headline"])

    def test_many_preprints_and_unchecked(self):
        items = [{"verdict": None, "year": "2025", "venue": PREPRINT},
                 {"verdict": "verified", "year": "2024", "venue": PREPRINT},
                 {"verdict": "verified", "year": "2023", "venue": JOURNAL}]
        h = journals.health(items, this_year=2026)
        d = self.dims(h)
        self.assertEqual(d["venues"]["state"], "notes")
        self.assertIn("67 % Preprints", d["venues"]["detail"])
        self.assertEqual(d["real"]["state"], "notes")
        self.assertIn("1 Quelle ist noch nicht geprüft", d["real"]["detail"])
        self.assertEqual(h["venues"]["preprints"], 2)

    def test_jufo_zero_note(self):
        items = [{"verdict": "verified", "year": "2024", "venue": JOURNAL, "jufo": lvl} for lvl in (0, 0, 1, 2)]
        d = self.dims(journals.health(items, this_year=2026))
        self.assertIn("JUFO-Stufe 0", d["venues"]["detail"])

    def test_without_years_or_venues(self):
        h = journals.health([{"verdict": "verified"}], this_year=2026)
        d = self.dims(h)
        self.assertEqual(d["age"]["state"], "unknown")
        self.assertEqual(d["venues"]["state"], "unknown")
        self.assertEqual(h["state"], "ok")


if __name__ == "__main__":
    unittest.main()


class Schnittstelle(unittest.TestCase):
    """/api/health und /api/rankings, ohne Netz (OpenAlex und JUFO ersetzt)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["BENTHIC_DATA_DIR"] = self.tmp.name
        self.orig = (journals.venues_for_dois, journals.jufo_level)
        nature = {"id": "S1", "name": "Nature", "issn": ["0028-0836"], "type": "journal", "preprint": False,
                  "doaj": False, "core": True, "citedness": 19.39, "h_index": 1839, "publisher": "Nature Portfolio"}
        journals.venues_for_dois = lambda dois, email="": {d: (dict(journals.ARXIV_SOURCE) if d.startswith("10.48550/") else nature) for d in dois}
        journals.jufo_level = lambda issns: 3
        from benthic import app as appmod
        self.client = ui_client(appmod.app)

    def tearDown(self):
        journals.venues_for_dois, journals.jufo_level = self.orig
        os.environ.pop("BENTHIC_DATA_DIR", None)
        self.tmp.cleanup()

    def test_health_with_ranking_list(self):
        import io
        csv_data = b"Journal Title,ISSN,Rating\nNature,0028-0836,A+\n"
        r = self.client.post("/api/rankings", data={"name": "VHB 4", "file": (io.BytesIO(csv_data), "vhb.csv")},
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_json())
        items = [{"doi": "10.1038/nature14539", "year": "2015", "verdict": "verified"},
                 {"doi": "10.48550/arXiv.1706.03762", "year": "2017", "verdict": "verified"}]
        j = self.client.post("/api/health", json={"items": items}).get_json()
        self.assertEqual(j["venues"]["10.1038/nature14539"]["jufo"], 3)
        self.assertEqual(j["venues"]["10.1038/nature14539"]["ranks"], [{"list": "VHB 4", "rank": "A+"}])
        self.assertTrue(j["venues"]["10.48550/arxiv.1706.03762"]["preprint"])
        self.assertEqual(j["rankings"], ["VHB 4"])
        keys = [d["key"] for d in j["health"]["dimensions"]]
        self.assertNotIn("access", keys)  # ohne PDF-Angaben keine Zugangs-Zeile
        self.assertEqual(self.client.get("/api/rankings").get_json()["rankings"][0]["count"], 1)

    def test_rejects_bad_input(self):
        self.assertEqual(self.client.post("/api/health", json={"items": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/api/health", json={"project": "fehlt"}).status_code, 404)
        self.assertEqual(self.client.post("/api/rankings", data={}).status_code, 400)
        self.assertEqual(self.client.delete("/api/rankings/fehlt").status_code, 404)
