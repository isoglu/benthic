"""Hilfen für Studierende: Rat je Urteil, echte Arbeiten zum Thema, korrigiertes Verzeichnis, Kopie erbitten,
Link-Resolver, Projekte als Datei (offline, Netz gemockt)."""

import io
import json
import os
import tempfile
import unittest
from unittest import mock

JOB = {
    "id": "4242", "mode": "verify", "finished_at": 1792300000,
    "results": [
        {"order": 0, "verdict": "verified", "doi": "10.1038/s41586-020-2649-2", "input": "Harris (2020). NumPy."},
        {"order": 1, "verdict": "not_found", "input": "1. Smith (2021). A systematic audit of citation reliability.",
         "claimed": {"title": "A systematic audit of citation reliability"}},
        {"order": 2, "verdict": "retracted", "doi": "10.1016/S0140-6736(97)11096-0", "input": "Wakefield (1998)."},
        {"order": 3, "verdict": "partial", "doi": "10.1145/3065386", "input": "Krizhevsky (2014). ImageNet."},
    ],
}


class Werkzeuge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()
        from benthic import app as appmod, history
        history.append(JOB)
        self.client = appmod.app.test_client()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_advice_for_every_verdict(self):
        from benthic import i18n, verify
        self.assertEqual(set(verify.VERDICT_ADVICE), set(verify.VERDICTS))
        for text in verify.VERDICT_ADVICE.values():
            self.assertIn(text, i18n.catalog("en"))

    def test_report_says_what_to_do(self):
        page = self.client.get("/report/4242").get_data(as_text=True)
        self.assertIn('class="adv"', page)
        self.assertIn("an invented source can count as cheating", page)  # Rat bei „Nicht auffindbar“

    def test_corrected_list_keeps_unconfirmed_apart(self):
        from benthic import biblio
        with mock.patch.object(biblio, "_fetch", side_effect=lambda doi, accept: None if "3065386" in doi else f"Entry for {doi}"):
            j = self.client.get("/api/jobs/4242/corrected?style=apa").get_json()
        self.assertIn("Entry for 10.1038/s41586-020-2649-2", j["text"])
        self.assertNotIn("Wakefield", j["text"])                       # zurückgezogen: nicht übernommen
        self.assertEqual(j["fixed"], 1)
        self.assertEqual([e["n"] for e in j["open"]], [2, 3, 4])       # erfunden, zurückgezogen, doi.org ohne Antwort
        self.assertEqual(self.client.get("/api/jobs/999/corrected").status_code, 404)

    def test_alternatives(self):
        from benthic import verify
        hits = [{"doi": "10.1/a", "title": "Real paper A", "authors": ["Ng"], "year": "2020", "source": "OpenAlex"},
                {"doi": "10.1/r", "title": "Retracted B", "retracted": True, "source": "OpenAlex"},
                {"doi": "10.1/a", "title": "Real paper A", "source": "Crossref"},
                {"doi": "", "title": "No DOI", "source": "Crossref"}]
        with mock.patch.object(verify, "_search_openalex", return_value=hits[:2]) as oa_search, \
                mock.patch.object(verify, "_search_crossref", return_value=hits[2:]):
            j = self.client.post("/api/alternatives", json={"title": "3. A systematic audit (2021) https://doi.org/10.9/x"}).get_json()
        self.assertEqual([s["doi"] for s in j["suggestions"]], ["10.1/a"])
        self.assertNotIn("doi.org", oa_search.call_args[0][0])         # Links und DOIs gehören nicht in die Suche
        self.assertFalse(oa_search.call_args[0][0].startswith("3."))
        self.assertEqual(self.client.post("/api/alternatives", json={"title": "abc"}).status_code, 400)

    def test_request_copy(self):
        from benthic import oa
        work = {"title": "Array programming with NumPy", "publication_year": 2020,
                "primary_location": {"source": {"display_name": "Nature"}},
                "authorships": [{"author": {"display_name": "Charles R. Harris", "orcid": "https://orcid.org/0000-0001"}},
                                {"author": {"display_name": "Travis E. Oliphant"}, "is_corresponding": True}]}
        with mock.patch.object(oa, "openalex_work", return_value=work):
            j = self.client.get("/api/request-copy?doi=10.1038/s41586-020-2649-2").get_json()
        self.assertEqual([c["name"] for c in j["contacts"]], ["Travis E. Oliphant"])  # Kontaktperson zuerst
        self.assertIn("Dear Travis E. Oliphant", j["body"]["en"])
        self.assertIn("Nature, 2020", j["body"]["de"])
        self.assertIn("https://doi.org/10.1038/s41586-020-2649-2", j["body"]["en"])
        self.assertTrue(j["subject"]["en"].startswith("Request for a copy"))
        self.assertEqual(self.client.get("/api/request-copy?doi=kaputt").status_code, 400)

    def test_link_resolver_template(self):
        from benthic.fetcher import BenthicDownloader
        d = BenthicDownloader.__new__(BenthicDownloader)
        d.proxy_prefix = "https://sfx.example.edu/openurl?rft_id=info:doi/{doi}"
        self.assertEqual(d._proxy_url("10.1016/S0140-6736(97)11096-0"),
                         "https://sfx.example.edu/openurl?rft_id=info:doi/10.1016%2FS0140-6736%2897%2911096-0")
        d.proxy_prefix = "https://ezproxy.example.edu/login?url=https://doi.org/"
        self.assertEqual(d._proxy_url("10.1/x"), "https://ezproxy.example.edu/login?url=https://doi.org/10.1/x")

    def test_project_file_round_trip(self):
        from benthic import projects
        p = projects.create("Seminar")
        projects.add_items(p["id"], [{"doi": "10.1038/nature14539", "title": "A", "verdict": "verified", "filename": "/Users/x/a.pdf"}])
        r = self.client.get(f"/api/projects/{p['id']}/bundle")
        self.assertIn("Seminar.benthic.json", r.headers["Content-Disposition"])
        data = json.loads(r.get_data(as_text=True))
        self.assertEqual(data["items"], [{"doi": "10.1038/nature14539", "title": "A", "verdict": "verified"}])  # kein Pfad
        j = self.client.post("/api/projects/import", data={"file": (io.BytesIO(r.get_data()), "s.benthic.json")}).get_json()
        self.assertEqual((j["name"], j["added"]), ("Seminar (2)", 1))
        self.assertEqual(projects.find(j["id"])["items"]["10.1038/nature14539"]["verdict"], "verified")
        bad = self.client.post("/api/projects/import", data={"file": (io.BytesIO(b"{}"), "x.json")})
        self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()
