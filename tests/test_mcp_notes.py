"""Prüfhinweise mit Werten lesen sich in der MCP-Antwort auf Englisch, auch bei deutscher Oberfläche (offline)."""

import os
import tempfile
import unittest
from unittest import mock

from benthic import i18n, mcp_server, questionable, quota, verify

TITLE = "Coral bleaching and ocean warming in the Red Sea"
OTHER = "Deep sea sediment cores from the Mariana Trench"
CLAIMED = {"doi": "10.1234/other", "title": TITLE, "year": 2019, "authors": ["Ana Lopez"],
           "raw": f"Lopez, A. (2019). {TITLE}. doi:10.1234/other"}


def work(title, doi, year=2019, retracted=False):
    return {"doi": doi, "title": title, "year": year, "authors": ["Ana Lopez"], "retracted": retracted,
            "source": "OpenAlex"}


class VerifyNotesOverMcp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": tmp.name}),
                  mock.patch("benthic.i18n.current", return_value="de"),
                  mock.patch.object(quota, "problems", return_value=[])):
            p.start()
            self.addCleanup(p.stop)

    def check(self, ref, lookup=None, search=(None, 0, 0.0), exists=None):
        """verify_ref ohne Netz: Metadaten, Suche und DOI-Dienst liefern, was der Test vorgibt."""
        with mock.patch.object(verify, "lookup", return_value=lookup), \
                mock.patch.object(verify, "search", return_value=search), \
                mock.patch.object(verify, "doi_exists", return_value=exists):
            return verify.verify_ref(dict(ref))

    def mcp_note(self, res):
        text = mcp_server._fmt_verify({"status": "done", "results": [dict(res, order=0)]})
        return text.split("\n   Note: ", 1)[1].split("\n", 1)[0]

    def test_doi_of_another_work(self):
        res = self.check(CLAIMED, lookup=work(OTHER, "10.1234/other"))
        self.assertEqual(res["verdict"], "mismatch")
        self.assertEqual(res["note"], f"Die DOI existiert, gehört aber zu „{OTHER}“.")  # Oberfläche bleibt deutsch
        self.assertEqual(self.mcp_note(res), f"The DOI exists but belongs to “{OTHER}”.")

    def test_corrected_doi(self):
        res = self.check(CLAIMED, lookup=work(OTHER, "10.1234/other"),
                         search=(work(TITLE, "10.1234/coral"), 95, 1.0))
        self.assertEqual(res["verdict"], "corrected")
        self.assertEqual(self.mcp_note(res),
                         f"The given DOI belongs to “{OTHER}”. The cited work has the DOI 10.1234/coral.")

    def test_retracted_with_differences(self):
        res = self.check(CLAIMED, lookup=work(TITLE, "10.1234/other", year=2012, retracted=True))
        self.assertEqual(res["verdict"], "retracted")
        self.assertEqual(res["note"], "Zurückgezogen. Werk gefunden, aber Jahr weicht von der Angabe ab.")
        self.assertEqual(self.mcp_note(res), "Retracted. Work found, but the year differs from the reference.")

    def test_listed_differences(self):
        claimed = dict(CLAIMED, title="Coral bleaching and warming oceans of the Red Sea region", authors=["Ben Okafor"])
        res = self.check(claimed, lookup=work(TITLE, "10.1234/other", year=2012))
        self.assertEqual(res["verdict"], "partial")
        self.assertIn("Titel, Jahr und Autor:innen", res["note"])
        self.assertEqual(self.mcp_note(res), "Work found, but the title, year and authors differ from the reference.")

    def test_sources_that_did_not_answer(self):
        ref = {"doi": "10.1234/missing", "raw": "doi:10.1234/missing"}
        down = [{"service": "crossref", "name": "Crossref"}, {"service": "openalex", "name": "OpenAlex"}]
        with mock.patch.object(quota, "problems", return_value=down):
            res = self.check(ref, exists=False)
        self.assertEqual(res["verdict"], "uncertain")
        self.assertIn("Crossref und OpenAlex", res["note"])
        self.assertEqual(self.mcp_note(res), "Not found, but Crossref and OpenAlex did not answer. Please check again later.")

        down = [{"service": "openalex", "name": "OpenAlex"}, {"service": "semanticscholar", "name": "Semantic Scholar"}]
        with mock.patch.object(quota, "problems", return_value=down):
            res = self.check(ref, exists=False)
        self.assertEqual(res["verdict"], "not_found")
        self.assertEqual(self.mcp_note(res), "The DOI does not exist and no matching work was found. Probably invented. "
                                             "OpenAlex and Semantic Scholar was not available; checked with the other sources.")

    def test_closest_match_without_year(self):
        best = dict(work(TITLE + " in 2019", "10.1234/coral"), year=None)
        res = self.check({"title": TITLE, "raw": TITLE}, search=(best, 70, 0.8))
        self.assertEqual(res["verdict"], "uncertain")
        self.assertTrue(res["note"].endswith("(o. J.). Bitte prüfen."), res["note"])
        self.assertEqual(self.mcp_note(res), f"Closest match: “{TITLE} in 2019” (n.d.). Please check.")

    def test_hijacked_site_is_appended(self):
        from benthic import app
        res = self.check(CLAIMED, lookup=work(OTHER, "10.1234/other"))
        hit = [{"site": "coral-journal.example", "original": "Coral Reefs", "title": "Coral Reefs"}]
        with mock.patch.object(questionable, "hijacked_in", return_value=hit):
            res = app._flag_hijacked(res, CLAIMED)
        self.assertTrue(res["note"].startswith("Die DOI existiert"), res["note"])
        self.assertIn("ist eine gekaperte Kopie von „Coral Reefs“", res["note"])
        self.assertEqual(self.mcp_note(res),
                         f"The DOI exists but belongs to “{OTHER}”. The website given, coral-journal.example, is a "
                         "hijacked copy of “Coral Reefs” (Retraction Watch Hijacked Journal Checker).")

    def test_english_interface_unchanged(self):
        with mock.patch("benthic.i18n.current", return_value="en"):
            res = self.check(CLAIMED, lookup=work(OTHER, "10.1234/other"))
        self.assertEqual(res["note"], f"The DOI exists but belongs to “{OTHER}”.")
        self.assertEqual(self.mcp_note(res), res["note"])

    def test_results_without_parts(self):
        """Läufe aus dem Verlauf von vor dieser Änderung: fester Satz übersetzt, sonst wie gespeichert."""
        self.assertEqual(self.mcp_note({"verdict": "retracted", "note": "Zurückgezogen."}), "Retracted.")
        self.assertEqual(self.mcp_note({"verdict": "mismatch", "note": "Die DOI existiert, gehört aber zu „X“."}),
                         "Die DOI existiert, gehört aber zu „X“.")

    def test_language_values(self):
        part = i18n.note_part("Ähnlichster Treffer: „{title}“ ({year}). Bitte prüfen.", title="T",
                              year=i18n.each_lang(lambda lang: i18n.tr("o. J.", lang)))
        self.assertEqual(i18n.tr_parts([part]), "Ähnlichster Treffer: „T“ (o. J.). Bitte prüfen.")
        self.assertEqual(i18n.tr_parts([part], "en"), "Closest match: “T” (n.d.). Please check.")


if __name__ == "__main__":
    unittest.main()
