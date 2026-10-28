"""Hilfen für Studierende: Rat je Urteil, echte Arbeiten zum Thema, korrigiertes Verzeichnis, Kopie erbitten,
Link-Resolver, Projekte als Datei (offline, Netz gemockt)."""

import io
import json
import os
import tempfile
import unittest
from unittest import mock

from tests import ui_client

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
        self.client = ui_client(appmod.app)

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


class RegistryCoverage(unittest.TestCase):
    def test_datacite_work(self):
        from benthic import verify
        fake_json = {
            "data": {
                "attributes": {
                    "doi": "10.5281/zenodo.12345",
                    "titles": [{"title": "Open Source Research Dataset"}],
                    "creators": [{"name": "Smith, Alice", "familyName": "Smith"}],
                    "publicationYear": 2023,
                    "publisher": "Zenodo",
                    "types": {"resourceTypeGeneral": "Dataset"},
                    "citationCount": 5,
                }
            }
        }
        resp = mock.Mock(status_code=200, json=lambda: fake_json)
        with mock.patch("benthic.oa._api_get", return_value=resp):
            work = verify.datacite_work("10.5281/zenodo.12345")
        self.assertIsNotNone(work)
        self.assertEqual(work["title"], "Open Source Research Dataset")
        self.assertEqual(work["year"], "2023")
        self.assertEqual(work["source"], "DataCite")
        self.assertEqual(work["authors"], ["Smith"])

    def test_search_dblp(self):
        from benthic import verify
        fake_json = {
            "result": {
                "hits": {
                    "hit": [{
                        "info": {
                            "title": "Deep Learning at Scale.",
                            "authors": {"author": [{"text": "Yann LeCun"}, {"text": "Yoshua Bengio"}]},
                            "year": "2015",
                            "venue": "Nature",
                            "doi": "10.1038/nature14539",
                            "url": "https://dblp.org/rec/journals/nature/LeCunBH15",
                        }
                    }]
                }
            }
        }
        resp = mock.Mock(status_code=200, json=lambda: fake_json)
        with mock.patch("benthic.oa._api_get", return_value=resp):
            hits = verify._search_dblp("Deep Learning at Scale")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["title"], "Deep Learning at Scale")
        self.assertEqual(hits[0]["source"], "DBLP")
        self.assertEqual(hits[0]["authors"], ["LeCun", "Bengio"])

    # Antworten im Format der Europe-PMC-REST-Suche (resultType=lite), gekürzt.
    EPMC_TITLE = "Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children."
    EPMC_JSON = {"hitCount": 3, "resultList": {"result": [
        {"id": "20137807", "source": "MED", "pmid": "20137807", "doi": "10.1016/s0140-6736(10)60175-4",
         "title": "Retraction--" + EPMC_TITLE, "journalTitle": "Lancet", "pubYear": "2010", "pubType": "retraction notice"},
        {"id": "9683237", "source": "MED", "pmid": "9683237", "doi": "10.1016/s0140-6736(05)77837-5", "title": EPMC_TITLE,
         "authorString": "Sabra A, Bellanti JA, Colón AR.", "pubYear": "1998", "pubType": "letter; comment"},
        {"id": "9500320", "source": "MED", "pmid": "9500320", "doi": "10.1016/s0140-6736(97)11096-0", "title": EPMC_TITLE,
         "authorString": "Wakefield AJ, Murch SH, Walker-Smith JA.", "journalTitle": "Lancet", "journalVolume": "351",
         "issue": "9103", "pageInfo": "637-641", "pubYear": "1998",
         "pubType": "retracted publication; research support, non-u.s. gov't; journal article"},
    ]}}

    def test_search_europepmc(self):
        from benthic import verify
        resp = mock.Mock(status_code=200, json=lambda: self.EPMC_JSON)
        with mock.patch("benthic.oa._api_get", return_value=resp) as get:
            hits = verify._search_europepmc("Ileal-lymphoid-nodular hyperplasia, non-specific colitis (1998)")
        self.assertEqual(get.call_args.kwargs["params"]["query"],
                         'TITLE:"Ileal-lymphoid-nodular hyperplasia non-specific colitis 1998"')
        # Rückzugsnotiz und Leserbrief tragen denselben Titel, sind aber nicht das Werk.
        self.assertEqual([h["doi"] for h in hits], ["10.1016/s0140-6736(97)11096-0"])
        hit = hits[0]
        self.assertEqual(hit["authors"], ["Wakefield", "Murch", "Walker-Smith"])
        self.assertTrue(hit["retracted"])
        self.assertEqual((hit["source"], hit["year"], hit["pages"]), ("Europe PMC", "1998", "637-641"))
        self.assertFalse(hit["title"].endswith("."))

    def test_title_search_falls_back_to_europepmc(self):
        """Ein PubMed-Artikel ohne DOI, den Crossref und OpenAlex nicht finden: Europe PMC bestätigt ihn."""
        from benthic import refs, verify
        epmc = {"resultList": {"result": [
            {"id": "4123456", "source": "MED", "pmid": "4123456", "title": "Serum zinc in elderly patients with hip fracture.",
             "authorString": "Doe J, van der Berg A.", "journalTitle": "Geriatrics", "pubYear": "1974",
             "pubType": "journal article"}]}}

        def api(url, params=None, headers=None, timeout=20.0):
            self.assertIn("ebi.ac.uk/europepmc", url)  # sonst keine andere Quelle in diesem Test
            return mock.Mock(status_code=200, json=lambda: epmc)
        ref = refs.parse("Doe, J., & van der Berg, A. (1974). Serum zinc in elderly patients with hip fracture. "
                         "Geriatrics, 29, 1-4.")[0]
        with mock.patch.object(verify, "_search_crossref", return_value=[]), \
                mock.patch.object(verify, "_search_openalex", return_value=[]), \
                mock.patch.object(verify, "_search_arxiv", return_value=[]), \
                mock.patch("benthic.oa._api_get", side_effect=api):
            res = verify.verify_ref(ref)
        self.assertEqual(res["verdict"], "verified")
        self.assertEqual(res["found"]["source"], "Europe PMC")
        self.assertEqual(res["found"]["url"], "https://pubmed.ncbi.nlm.nih.gov/4123456/")

    def test_no_europepmc_call_after_a_clear_hit(self):
        from benthic import verify
        hit = {"doi": "10.1000/xyz", "title": "Serum copper in elderly patients with hip fracture",
               "authors": ["Doe"], "year": "1975", "retracted": False, "source": "Crossref"}
        claimed = {"title": "Serum copper in elderly patients with hip fracture", "authors": ["Doe"], "year": "1975"}
        with mock.patch.object(verify, "_search_crossref", return_value=[hit]), \
                mock.patch.object(verify, "_search_openalex", return_value=[]), \
                mock.patch.object(verify, "lookup", return_value=None), \
                mock.patch.object(verify, "_search_europepmc") as epmc:
            best, _score, _sim = verify.search(claimed)
        self.assertEqual(best["doi"], "10.1000/xyz")
        epmc.assert_not_called()

    def test_dnb_marc21_parsing(self):
        from benthic import verify
        sample_marc = """<?xml version="1.0" encoding="UTF-8"?>
<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <records>
    <record>
      <recordData>
        <record xmlns="http://www.loc.gov/MARC21/slim">
          <controlfield tag="001">123456789</controlfield>
          <datafield tag="100" ind1="1" ind2=" ">
            <subfield code="a">Müller, Hans</subfield>
          </datafield>
          <datafield tag="245" ind1="1" ind2="0">
            <subfield code="a">Einführung in die Informatik</subfield>
            <subfield code="b">Ein Lehrbuch</subfield>
          </datafield>
          <datafield tag="264" ind1=" " ind2="1">
            <subfield code="b">Springer</subfield>
            <subfield code="c">2021</subfield>
          </datafield>
        </record>
      </recordData>
    </record>
  </records>
</searchRetrieveResponse>"""
        parsed = verify._parse_dnb_marc21(sample_marc)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["title"], "Einführung in die Informatik: Ein Lehrbuch")
        self.assertEqual(parsed[0]["authors"], ["Müller"])
        self.assertEqual(parsed[0]["year"], "2021")
        self.assertEqual(parsed[0]["source"], "Deutsche Nationalbibliothek")
        self.assertEqual(parsed[0]["url"], "https://d-nb.info/123456789")

    def test_verify_ref_with_isbn(self):
        from benthic import verify
        ref = {
            "format": "text",
            "raw": "Knuth, D. E. (1984). The TeXbook. Addison-Wesley. ISBN 0-201-13447-0.",
            "title": "The TeXbook",
            "authors": ["Knuth"],
            "year": "1984",
            "isbn": "0201134470",
            "doi": None,
        }
        hit = {
            "title": "The TeXbook",
            "authors": ["Knuth"],
            "year": "1984",
            "source": "Open Library",
            "type": "book",
        }
        with mock.patch("benthic.verify._lookup_openlibrary_isbn", return_value=hit):
            res = verify.verify_ref(ref)
        self.assertEqual(res["verdict"], "verified")
        self.assertEqual(res["resolved_by"], "isbn")

    def test_field_diffs(self):
        from benthic import verify
        claimed = {"title": "Attention Is All You Need", "authors": ["Vaswani", "Müller"], "year": "2017", "venue": "NeurIPS"}
        actual = {"title": "Attention is all you need", "authors": ["Vaswani", "Shazeer"], "year": "2018", "venue": "NIPS"}
        diffs = verify.field_diffs(claimed, actual)
        self.assertEqual(diffs["title"]["status"], "ok")
        self.assertEqual(diffs["year"]["status"], "warn")
        self.assertEqual(diffs["authors"]["status"], "warn")

    def test_chimera_detection(self):
        from benthic import verify
        ref = {
            "format": "text",
            "raw": "Vaswani, A. (2021). Quantum Computing for Protein Folding.",
            "title": "Quantum Computing for Protein Folding",
            "authors": ["Vaswani"],
            "year": "2021",
            "doi": None,
        }
        best = {
            "title": "Attention is all you need",
            "authors": ["Vaswani", "Shazeer"],
            "year": "2017",
            "source": "OpenAlex",
            "doi": "10.48550/arXiv.1706.03762",
        }
        with mock.patch("benthic.verify.search", return_value=(best, 45, 0.40)):
            res = verify.verify_ref(ref)
        self.assertEqual(res["verdict"], "not_found")
        self.assertEqual(res.get("pattern"), "chimera")
        self.assertIn("blend real authors with an invented title", res["note"])
        self.assertIn("Sprachmodelle vermischen oft", res["note_parts"][0]["msg"])

    def test_wrong_doi_pattern(self):
        from benthic import verify
        ref = {
            "format": "text",
            "raw": "Smith, J. (2020). Made up paper title. 10.1038/nature14539",
            "title": "Made up paper title",
            "authors": ["Smith"],
            "year": "2020",
            "doi": "10.1038/nature14539",
        }
        actual = {
            "title": "Deep learning",
            "authors": ["LeCun", "Bengio", "Hinton"],
            "year": "2015",
            "venue": "Nature",
            "doi": "10.1038/nature14539",
        }
        with mock.patch("benthic.verify.lookup", return_value=actual), \
                mock.patch("benthic.verify.search", return_value=(None, 0, 0.0)):
            res = verify.verify_ref(ref)
        self.assertEqual(res["verdict"], "mismatch")
        self.assertEqual(res.get("pattern"), "wrong_doi")


class Belegstellen(unittest.TestCase):
    """Belegstelle für eine unbelegte Aussage: die PDFs eines Laufs durchsuchen (scan-job)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()
        from benthic import app as appmod, history
        history.append(JOB)
        self.client = ui_client(appmod.app)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def scan(self, **body):
        return self.client.post("/api/support/scan-job", json=body)

    def test_unknown_run_and_short_claim_are_rejected(self):
        self.assertEqual(self.scan(job_id="9999", claim="Ein hinreichend langer Satz.").status_code, 404)
        self.assertEqual(self.scan(job_id="", claim="Ein hinreichend langer Satz.").status_code, 400)
        self.assertEqual(self.scan(job_id="4242", claim="zu kurz").status_code, 400)

    def test_only_real_verdicts_are_scanned_and_matches_carry_page(self):
        found = {"available": True, "passages": [{"text": "…", "page": 7, "terms": ["citation"], "score": 0.4}]}
        with mock.patch("benthic.biblio.pdf_path", return_value="/tmp/x.pdf"), \
                mock.patch("benthic.support.find", return_value=found):
            data = self.scan(job_id="4242", claim="Citation reliability of language models").get_json()
        self.assertEqual(data["scanned"], 2)  # verified + partial, nicht not_found oder retracted
        self.assertEqual({m["doi"] for m in data["matches"]},
                         {"10.1038/s41586-020-2649-2", "10.1145/3065386"})
        self.assertEqual(data["matches"][0]["page"], 7)
        self.assertEqual(data["matches"][0]["score"], 0.4)

    def test_weak_passage_is_not_offered(self):
        found = {"available": True, "passages": [{"text": "…", "page": 1, "terms": [], "score": 0.01}]}
        with mock.patch("benthic.biblio.pdf_path", return_value="/tmp/x.pdf"), \
                mock.patch("benthic.support.find", return_value=found):
            data = self.scan(job_id="4242", claim="Citation reliability of language models").get_json()
        self.assertEqual(data["scanned"], 2)
        self.assertEqual(data["matches"], [])


if __name__ == "__main__":
    unittest.main()
