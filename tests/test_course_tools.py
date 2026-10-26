"""Werkzeuge aus dem Seminar: Suchstrings und Suchprotokoll, Vorwärtssuche, Quellenmatrix, Quellenarten,
Kursvorgaben und die Verbundkataloge für Bücher (offline, Netz gemockt)."""

import csv
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from tests import ui_client


def reply(payload, text=None):
    r = mock.Mock(status_code=200)
    r.json.return_value = payload
    r.text = text if text is not None else json.dumps(payload)
    r.content = r.text.encode("utf-8")
    r.headers = {"Content-Type": "application/json"}
    return r


WORK = {"doi": "https://doi.org/10.1000/seg", "title": "Benefit segmentation and churn in subscriptions",
        "publication_year": 2021, "authorships": [{"author": {"display_name": "Ann Smith"}}],
        "open_access": {"is_oa": False}, "abstract_inverted_index": {"Segmentation": [0], "reduces": [1], "churn.": [2]}}


class Data(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()
        from benthic import config
        config.save_settings({"language": "de"})  # Spaltennamen wie im Code

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class Suchstring(Data):
    def test_detects_search_strings(self):
        from benthic import finder
        for text in ('"market segmentation" AND churn', "segment* OR cluster*", '"benefit segmentation"', "a NOT b"):
            self.assertTrue(finder.is_search_string(text), text)
        for text in ("Language models often invent references.", 'He said "no" to it.', "Smith and Jones (2020) agree"):
            self.assertFalse(finder.is_search_string(text), text)

    def test_search_string_goes_unchanged_without_wildcard(self):
        from benthic import finder
        with mock.patch.object(finder.oa, "_api_get", return_value=reply({"meta": {"count": 42}, "results": [WORK]})) as get:
            r = finder.find('("benefit segmentation" OR segment*) AND churn')
        self.assertEqual(get.call_args.kwargs["params"]["search"], '("benefit segmentation" OR segment) AND churn')
        self.assertEqual(r["total"], 42)
        self.assertEqual([s["doi"] for s in r["suggestions"]], ["10.1000/seg"])
        self.assertEqual(finder.find('"x"')["error"], "short")  # ein Buchstabe ist kein Suchbegriff

    def test_search_log_endpoint(self):
        from benthic import app as appmod, finder, projects
        client = ui_client(appmod.app)
        pid = projects.create("Kaffee-Abos")["id"]
        projects.set_active(pid)
        with mock.patch.object(finder.oa, "_api_get", return_value=reply({"meta": {"count": 42}, "results": [WORK]})):
            j = client.post("/api/find-sources", json={"claim": '"benefit segmentation" AND churn'}).get_json()
        self.assertTrue(j["log_id"])
        self.assertEqual(client.post("/api/search-log", json={"id": j["log_id"], "doi": "10.1000/SEG"}).status_code, 200)
        self.assertEqual(client.post("/api/search-log", json={"id": "nope", "doi": "10.1000/seg"}).status_code, 404)
        rows = list(csv.reader(io.StringIO(client.get(f"/api/search-log?format=csv&project={pid}").get_data(as_text=True))))
        self.assertEqual(rows[0][:5], ["Datum", "Datenbank", "Suchstring", "Filter", "Treffer"])
        self.assertEqual(rows[1][1:3], ["OpenAlex", '"benefit segmentation" AND churn'])
        self.assertEqual(rows[1][4:8], ["42", "1", "1", "10.1000/seg"])
        self.assertEqual(len(list(csv.reader(io.StringIO(
            client.get("/api/search-log?format=csv&project=other").get_data(as_text=True))))), 1)  # nur die Kopfzeile
        self.assertEqual(client.get("/api/search-log").status_code, 200)  # XLSX


class Vorwaertssuche(Data):
    def test_citing_works(self):
        from benthic import app as appmod, related
        citing = {"meta": {"count": 3}, "results": [
            {"doi": "https://doi.org/10.2000/new", "display_name": "Segmentation revisited", "publication_year": 2024,
             "authorships": [{"author": {"display_name": "Bo Lee"}}], "cited_by_count": 9, "open_access": {"is_oa": True}},
            {"doi": None, "display_name": "Without DOI"}]}
        calls = []

        def api(url, params=None, **_):
            calls.append((url, params))
            return reply({"id": "https://openalex.org/W1", "cited_by_count": 120} if "doi:" in url else citing)
        with mock.patch.object(related.oa, "_api_get", side_effect=api):
            r = ui_client(appmod.app).post("/api/cited-by", json={"doi": "10.1000/seg", "since": 2022}).get_json()
        self.assertIn("cites:W1,publication_year:>2021", calls[1][1]["filter"])
        self.assertEqual(calls[1][1]["sort"], "cited_by_count:desc")
        self.assertEqual((r["total"], r["since"], [w["doi"] for w in r["works"]]), (3, 2022, ["10.2000/new"]))
        with mock.patch.object(related.oa, "_api_get", return_value=None):
            self.assertEqual(ui_client(appmod.app).post("/api/cited-by", json={"doi": "10.1000/seg"}).status_code, 502)


class Quellenmatrix(Data):
    def test_matrix_export(self):
        from benthic import app as appmod, projects, related
        pid = projects.create("Kaffee-Abos")["id"]
        projects.add_items(pid, [{"doi": "10.1000/seg", "title": "Benefit segmentation", "year": 2021, "verdict": "verified"},
                                 {"doi": "10.9000/unknown", "title": "Unknown work", "verdict": "partial"}])
        meta = {"10.1000/seg": {"title": "Benefit segmentation and churn", "year": 2021, "type": "article",
                             "authors": ["Smith", "Lee", "Kim"], "venue": "Journal of Marketing"}}
        client = ui_client(appmod.app)
        with mock.patch.object(related, "works_by_doi", return_value=meta):
            rows = list(csv.DictReader(io.StringIO(client.get(f"/api/projects/{pid}/matrix?format=csv").get_data(as_text=True))))
            self.assertEqual(client.get(f"/api/projects/{pid}/matrix").status_code, 200)  # XLSX
        self.assertEqual(rows[0]["Autor:innen und Jahr"], "Smith, Lee u. a. (2021)")
        self.assertEqual((rows[0]["Zeitschrift oder Verlag"], rows[0]["Art"], rows[0]["Urteil"]),
                         ("Journal of Marketing", "Artikel", "Bestätigt"))
        self.assertEqual((rows[1]["Titel"], rows[1]["Urteil"], rows[1]["Kernergebnis"]), ("Unknown work", "Weicht ab", ""))
        self.assertEqual(client.get("/api/projects/nope/matrix").status_code, 404)


class Quellenarten(unittest.TestCase):
    def test_mix_and_course_rules(self):
        from benthic import journals
        items = [{"verdict": "verified", "year": 2020, "type": "journal-article"},
                 {"verdict": "verified", "year": 2019, "type": "book"},
                 {"verdict": "uncertain", "year": 2024, "web": True},
                 {"verdict": "verified", "year": 2023, "type": "posted-content"},
                 {"verdict": "not_found", "year": 2021, "venue": {"type": "journal"}}]
        h = journals.health(items)
        self.assertEqual(h["mix"], {"journal": 2, "book": 1, "preprint": 1, "report": 0, "web": 1, "other": 0, "unknown": 0})
        mix = next(d for d in h["dimensions"] if d["key"] == "mix")
        self.assertEqual(mix["state"], "ok")  # eine Webseite von fünf ist kein Hinweis
        self.assertFalse(any(d["key"] == "course" for d in h["dimensions"]))
        course = journals.health(items, course={"min_sources": 10, "excluded": ["web", "preprint"], "note": "Keine Blogs."})
        dim = next(d for d in course["dimensions"] if d["key"] == "course")
        self.assertEqual(dim["state"], "notes")
        self.assertIn("4", dim["headline"])  # vier zitierfähige (die erfundene zählt nicht), verlangt zehn
        self.assertIn("Keine Blogs.", dim["detail"])
        ok = journals.health(items[:2], course={"min_sources": 2, "excluded": ["web"]})
        self.assertEqual(next(d for d in ok["dimensions"] if d["key"] == "course")["state"], "ok")
        grey = journals.health([{"verdict": "verified", "web": True}, {"verdict": "verified", "type": "report"},
                                {"verdict": "verified", "type": "journal-article"}])
        self.assertEqual(next(d for d in grey["dimensions"] if d["key"] == "mix")["state"], "notes")


class Kursvorgaben(Data):
    def post(self, rules):
        from benthic import app as appmod
        body = json.dumps({"benthic": "house-rules", "version": 1, "name": "Seminar", **rules}).encode()
        return ui_client(appmod.app).post("/api/house-rules", data={"file": (io.BytesIO(body), "rules.json")},
                                          content_type="multipart/form-data")

    def test_course_rules_load_and_validate(self):
        from benthic import config, house
        r = self.post({"course": {"min_sources": "15", "excluded": ["web", "web", "preprint"], "note": " Keine Blogs. "}})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(config.load_settings()["house_course"],
                         {"min_sources": 15, "excluded": ["preprint", "web"], "note": "Keine Blogs."})
        for bad in ({"min_sources": 0}, {"min_sources": "viele"}, {"excluded": ["journal"]}, {"excluded": "web"}, ["web"]):
            self.assertEqual(self.post({"course": bad}).status_code, 400, bad)
        house.remove()
        self.assertEqual(config.load_settings()["house_course"], {})


LOBID = {"totalItems": 1, "member": [{
    "id": "http://lobid.org/resources/991000020419706484#!", "title": "Marketing - Grundlagen marktorientierter Unternehmensführung",
    "otherTitleInformation": ["Konzepte - Instrumente - Praxisbeispiele"],
    "contribution": [{"agent": {"label": "Meffert, Heribert", "type": ["Person"]}},
                     {"agent": {"label": "Springer Fachmedien Wiesbaden", "type": ["CorporateBody"]}}],
    "publication": [{"startDate": "2018", "publishedBy": ["Springer Gabler"]}]}]}
K10 = ("<?xml version='1.0' encoding='UTF-8'?><zs:searchRetrieveResponse xmlns:zs='http://www.loc.gov/zing/srw/'><zs:records>"
       "<zs:record><zs:recordData><record xmlns='http://www.loc.gov/MARC21/slim'><controlfield tag='001'>1882866088</controlfield>"
       "<datafield tag='100'><subfield code='a'>Meffert, Heribert</subfield></datafield>"
       "<datafield tag='245'><subfield code='a'>Marketing</subfield><subfield code='b'>Grundlagen marktorientierter Unternehmensführung"
       "</subfield></datafield><datafield tag='264'><subfield code='b'>Springer Gabler</subfield><subfield code='c'>2024</subfield>"
       "</datafield></record></zs:recordData></zs:record></zs:records></zs:searchRetrieveResponse>")


class Verbundkataloge(unittest.TestCase):
    def setUp(self):
        from benthic import verify
        verify._cache.clear()

    def test_lobid_with_year_first(self):
        from benthic import verify
        calls = []

        def api(url, params=None, **_):
            calls.append(params["q"])
            return reply(LOBID)
        with mock.patch.object(verify.oa, "_api_get", side_effect=api):
            hits = verify._search_lobid("Marketing: Grundlagen marktorientierter Unternehmensführung", ["Meffert"], year="2018")
        self.assertIn("publication.startDate:2018", calls[0])
        self.assertEqual(len(calls), 1)  # der erste Versuch hat getroffen
        self.assertEqual((hits[0]["title"], hits[0]["authors"], hits[0]["year"], hits[0]["source"]),
                         ("Marketing - Grundlagen marktorientierter Unternehmensführung: Konzepte - Instrumente - Praxisbeispiele",
                          ["Meffert"], "2018", "lobid (hbz)"))
        self.assertEqual(hits[0]["url"], "http://lobid.org/resources/991000020419706484")

    def test_k10plus_falls_back_and_reads_utf8(self):
        from benthic import verify
        calls = []

        def api(url, params=None, **_):
            calls.append(params["query"])
            r = reply({}, text="")
            r.content = K10.encode("utf-8") if "jhr" not in params["query"] else b"<?xml version='1.0'?><x/>"
            return r
        with mock.patch.object(verify.oa, "_api_get", side_effect=api):
            hits = verify._search_k10plus("Marketing: Grundlagen marktorientierter Unternehmensführung. Konzepte",
                                          ["Meffert"], year="2018")
        self.assertEqual(calls[0], 'pica.tit="Marketing Grundlagen marktorientierter Unternehmensführung Konzepte" '
                                   'and pica.per="Meffert" and pica.jhr=2018')
        self.assertNotIn("jhr", calls[1])  # ohne Jahr nachgefragt
        self.assertEqual(hits[0]["title"], "Marketing: Grundlagen marktorientierter Unternehmensführung")
        self.assertEqual(hits[0]["source"], "K10plus (GBV, SWB)")


if __name__ == "__main__":
    unittest.main()
