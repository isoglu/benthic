"""Verlagsseiten, Prüfungen auf Menschen und geschlossene Arbeiten: aufgezeichnete Fälle aus Sinans Liste vom
2026-11-08 (offline).

Neun DOIs, die frei lesbar sind und doch einen Klick im Browser brauchten oder mit einem Fehler statt „keine
freie Kopie“ endeten. Die Antworten der Verzeichnisse, der Verlagsseiten und ihrer robots.txt liegen in
tests/corpus/fetch, so läuft der Test ohne Netz. Seiten mit einer Prüfung auf Menschen (Cloudflare, Anubis) sind
gekürzt aufgezeichnet: Titel und Merkmal der Prüfung, sonst nichts.

    python -m tests.test_fetch_landing --record [doi ...]   neu aufzeichnen (braucht Netz, fragt höflich langsam)
"""

import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import requests
from requests.structures import CaseInsensitiveDict

from benthic import oa, routes
from benthic.i18n import tr_parts
from tests.test_corpus import _Reply, _body, _key, _strip

HERE = Path(__file__).resolve().parent / "corpus" / "fetch"

SAGE, WILEY = "10.1177/16094069241296206", "10.1111/1475-679x.12618"
ELSEVIER_OA, ELSEVIER_GREEN = "10.1016/j.indmarman.2023.05.020", "10.1016/j.jacceco.2015.03.005"
PNAS, TANDF = "10.1073/pnas.1120893109", "10.1080/17449480.2021.1912369"
CLOSED, ELGAR, ROUTLEDGE = "10.1016/j.indmarman.2023.05.014", "10.4337/9781800880863.00114", "10.4324/9780203761885-24"
DOIS = [SAGE, WILEY, ELSEVIER_OA, ELSEVIER_GREEN, PNAS, TANDF, CLOSED, ELGAR, ROUTLEDGE]


def _slug(doi: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "_", doi)


def _skeleton(html: str) -> str:
    """Von einer Seite nur, was der Downloader liest: Titel, Meta-Tags zu PDF und Weiterleitung, PDF-Links. Kein
    Abstract, kein fremder Fließtext im Repositorium."""
    keep = [t for t in re.findall(r"<title>.*?</title>|<meta[^>]+>", html, re.S | re.I) if re.search(r"pdf|refresh|<title", t, re.I)]
    links = "".join(f"<a {m.group(0)}></a>" for m in oa._PDF_LINK_RE.finditer(html))
    return f"<html><head>{''.join(keep)}</head><body>{links}</body></html>"


def _page(url, resp) -> dict:
    """Eine Seitenantwort für die Aufzeichnung: Prüfseiten nur mit Titel und Merkmal, nie eine IP-Adresse."""
    content = resp.content or b""
    kind = (resp.headers.get("Content-Type") or "").lower()
    if content.startswith(b"%PDF-"):
        body = "%PDF-recorded"
    else:
        body = content[:65536].decode("utf-8", "replace")
        if oa._bot_check(resp):
            title = re.search(r"<title>(.*?)</title>", body, re.S | re.I)
            marks = " ".join(m for m in oa._BOT_CHECK if m in body.lower())
            body = (f"<html><head><title>{title.group(1).strip() if title else ''}</title></head>"
                    f"<body><!-- bot check, page trimmed for the recording: {marks} --></body></html>")
        elif "html" in kind or body.lstrip()[:1] == "<":
            body = _skeleton(body)
        body = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "0.0.0.0", body)
    keep = {k.lower(): v for k, v in resp.headers.items() if k.lower() in ("content-type", "cf-mitigated")}
    return {"url": url, "final": resp.url, "status": resp.status_code, "headers": keep, "body": body}


class _Page:
    """Was oa.download_pdf von einer requests.Response liest."""

    def __init__(self, rec):
        self.status_code = rec["status"]
        self.text = rec["body"]
        self.content = self.text.encode("utf-8")
        self.url = rec["final"]
        self.headers = CaseInsensitiveDict(rec["headers"])


def _fresh():
    oa._work_cache.clear()
    oa._robots.clear()
    oa._last_hit.clear()


def replay(doi, route_list=None):
    """(Ergebnis, nicht aufgezeichnete Anfragen, abgerufene Seiten) für eine DOI, ganz ohne Netz."""
    from benthic.fetcher import BenthicDownloader
    data = json.loads((HERE / f"{_slug(doi)}.json").read_text(encoding="utf-8"))
    api_calls = {_key(c["url"], c["params"]): c for c in data["api"]}
    pages = {p["url"]: p for p in data["pages"]}
    missing, asked = [], []

    def api(url, params=None, headers=None, timeout=20.0):
        call = api_calls.get(_key(url, params))
        if call is None:
            missing.append(_key(url, params))
            return None
        return None if call["status"] is None else _Reply(call)

    def get(url, headers=None, **_kw):
        asked.append(url)
        if url not in pages:
            missing.append(url)
            raise requests.exceptions.ConnectionError("nicht aufgezeichnet")
        return _Page(pages[url])

    _fresh()
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": tmp}), \
            mock.patch.object(oa, "_api_get", side_effect=api), mock.patch.object(oa.requests, "get", side_effect=get), \
            mock.patch.object(oa.time, "sleep"):
        res = BenthicDownloader(Path(tmp) / "papers", skip_existing=False, route_list=route_list or []).download_one(doi)
        saved = sorted(p.name for p in (Path(tmp) / "papers").iterdir())
    return res, missing, asked, saved


class Recorded(unittest.TestCase):
    """Die neun DOIs, wie Benthic sie heute sieht."""

    BLOCKED = {  # DOI: (PDF-Link für den Klick, Rechner, die nur Menschen hereinlassen oder Downloads verbieten)
        SAGE: ("https://journals.sagepub.com/doi/pdf/10.1177/16094069241296206", {"journals.sagepub.com"}),
        WILEY: ("https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/1475-679X.12618", {"onlinelibrary.wiley.com"}),
        ELSEVIER_OA: ("https://www.sciencedirect.com/science/article/pii/S0019850123000974/pdfft?isDTMRedir=true&download=true",
                      {"www.sciencedirect.com"}),
        PNAS: ("https://europepmc.org/articles/PMC3358869?pdf=render", {"europepmc.org"}),
        TANDF: ("https://www.tandfonline.com/doi/pdf/10.1080/17449480.2021.1912369?needAccess=true", {"www.tandfonline.com"}),
    }
    CLOSED = {CLOSED: "(Journal article, Industrial Marketing Management)",
              ELGAR: "(Reference entry, Edward Elgar Publishing eBooks)",
              ROUTLEDGE: "(Book chapter, Routledge eBooks)"}

    def test_every_case_is_recorded(self):
        self.assertEqual(sorted(p.name for p in HERE.glob("*.json")), sorted(f"{_slug(d)}.json" for d in DOIS))

    def test_open_access_behind_a_check_gets_the_pdf_link_and_the_reason(self):
        for doi, (pdf_url, hosts) in self.BLOCKED.items():
            with self.subTest(doi):
                r, missing, asked, saved = replay(doi)
                self.assertEqual(missing, [])
                self.assertEqual((r["status"], saved), ("blocked", []))
                self.assertEqual(r["pdf_url"], pdf_url)  # die Verlagsfassung vor dem Manuskript im Repositorium
                self.assertTrue(hosts <= set(r["blocked_by"]), r["blocked_by"])
                en = tr_parts(r["error_parts"], "en")
                self.assertTrue(en.startswith("Open access known, but the PDF cannot be fetched automatically"), en)
                self.assertTrue("Check for a human visitor at" in en or "No automated downloads allowed at" in en, en)

    def test_the_publisher_page_is_asked_when_a_source_lists_it_as_free(self):
        _, _, asked, _ = replay(WILEY)  # OpenAlex nennt nur den PDF-Link; die Verlagsseite selbst kommt danach
        self.assertIn(f"https://doi.org/{WILEY}", asked)
        _, _, asked, _ = replay(PNAS)  # Europe PMC nennt die PNAS-Seite „frei“
        self.assertIn(f"https://doi.org/{PNAS}", asked)

    def test_a_paywalled_publisher_version_is_never_asked(self):
        r, missing, asked, _ = replay(ELSEVIER_GREEN)  # frei ist nur das Manuskript bei SSRN
        self.assertEqual((missing, r["status"]), ([], "blocked"))
        self.assertFalse([u for u in asked if "jacceco" in u or "sciencedirect" in u], asked)
        self.assertIn("papers.ssrn.com", r["blocked_by"])

    def test_robots_forbidding_the_pdf_is_respected(self):
        r, _, asked, _ = replay(ELSEVIER_OA)
        self.assertNotIn(self.BLOCKED[ELSEVIER_OA][0], asked)  # ScienceDirect verbietet */pdfft* in robots.txt
        self.assertIn("No automated downloads allowed at www.sciencedirect.com (robots.txt).", tr_parts(r["error_parts"], "en"))

    def test_closed_access_is_absent_with_reason_and_publisher_link(self):
        for doi, what in self.CLOSED.items():
            with self.subTest(doi):
                r, missing, _, _ = replay(doi)
                self.assertEqual(missing, [])
                self.assertEqual(r["status"], "absent")
                self.assertEqual(r["landing_url"], f"https://doi.org/{doi}")
                self.assertEqual(tr_parts(r["error_parts"], "en"),
                                 f"No open-access copy: closed access according to OpenAlex {what}.")

    def test_extra_routes_that_find_nothing_are_not_an_error(self):
        # Sinans Fall vom 2026-11-08: Zusatzwege an, keiner hatte das Kapitel, und Benthic meldete "Fehler".
        class Empty(routes.Route):
            id, label = "empty", "Weg"

            def fetch(self, identifier, emit):
                raise routes.RouteMiss("no copy", retryable=True)

        r, missing, _, _ = replay(ELGAR, route_list=[Empty()])
        self.assertEqual((missing, r["status"], r["retryable"]), ([], "absent", True))
        self.assertEqual(tr_parts(r["error_parts"], "en"),
                         f"No open-access copy: closed access according to OpenAlex {self.CLOSED[ELGAR]}. "
                         "Extra routes without success: Weg: no copy.")
        from benthic import mcp_server
        line = mcp_server._fmt_download({"results": [r], "status": "done"}).splitlines()[-1]
        self.assertIn(f"NO OPEN-ACCESS COPY FOUND. Link: https://doi.org/{ELGAR} (No open-access copy: closed", line)


class LandingPage(unittest.TestCase):
    """Der PDF-Link, den eine frei lesbare Seite selbst anbietet (ohne Netz)."""

    def setUp(self):
        _fresh()
        self.sleep = mock.patch.object(oa.time, "sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()

    @staticmethod
    def page(url, body, status=200, kind="text/html", **headers):
        return _Page({"url": url, "final": url, "status": status, "body": body,
                      "headers": {"content-type": kind, **headers}})

    def fetch(self, url, replies):
        why, asked = {}, []

        def get(u, headers=None, **_kw):
            asked.append((u, headers or {}))
            return replies[u]
        with mock.patch.object(oa.requests, "get", side_effect=get):
            return oa.download_pdf(url, why=why), why, asked

    def test_publisher_patterns(self):
        cases = {
            "https://journals.sagepub.com/doi/10.1177/1609406924": "https://journals.sagepub.com/doi/pdf/10.1177/1609406924?download=true",
            "https://onlinelibrary.wiley.com/doi/full/10.1111/1475-679X.12618":
                "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/1475-679X.12618?download=true",
            "https://www.tandfonline.com/doi/abs/10.1080/1744948": "https://www.tandfonline.com/doi/pdf/10.1080/1744948?download=true",
            "https://pnas.org/doi/full/10.1073/pnas.1120893109": "https://pnas.org/doi/pdf/10.1073/pnas.1120893109?download=true",
            "https://linkinghub.elsevier.com/retrieve/pii/S0019850123000974":
                "https://www.sciencedirect.com/science/article/pii/S0019850123000974/pdfft?isDTMRedir=true&download=true",
            "https://journals.sagepub.com/doi/pdf/10.1177/1609406924": None,  # schon der PDF-Link
            "https://evil.example/doi/10.1177/x": None,
            "https://sagepub.com.evil.example/doi/10.1177/x": None,
        }
        for page, want in cases.items():
            self.assertEqual(oa.publisher_pdf_url(page), want, page)

    def test_digital_commons_page_gives_its_pdf_like_a_click(self):
        page = "https://scholarworks.example.edu/faculty/5792/"
        pdf = "https://scholarworks.example.edu/cgi/viewcontent.cgi?article=6791&context=faculty"
        html = ('<html><head><meta name="bepress_citation_pdf_url" '
                'content="https://scholarworks.example.edu/cgi/viewcontent.cgi?article=6791&amp;context=faculty"></head></html>')
        robots = "https://scholarworks.example.edu/robots.txt"
        content, why, asked = self.fetch(page, {page: self.page(page, html), robots: self.page(robots, "", status=404),
                                                pdf: self.page(pdf, "%PDF-1.7 x", kind="application/pdf")})
        self.assertEqual(content, b"%PDF-1.7 x")
        self.assertEqual(asked[2][0], pdf)
        self.assertEqual(asked[2][1]["Referer"], page)  # wie ein Klick auf der Seite

    def test_wiley_page_without_meta_uses_the_download_button(self):
        page = "https://onlinelibrary.wiley.com/doi/10.1111/x.1"
        pdf = "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/x.1?download=true"
        robots = "https://onlinelibrary.wiley.com/robots.txt"
        content, _, _ = self.fetch(page, {page: self.page(page, "<html>Open access</html>"),
                                          robots: self.page(robots, "User-agent: *\nDisallow: /doi/am-pdf/\n"),
                                          pdf: self.page(pdf, "%PDF-1.4 w", kind="application/pdf")})
        self.assertEqual(content, b"%PDF-1.4 w")

    def test_robots_disallow_stops_before_the_pdf(self):
        page = "https://linkinghub.elsevier.com/retrieve/pii/S1"
        pdf = "https://www.sciencedirect.com/science/article/pii/S1/pdfft?isDTMRedir=true&download=true"
        robots = "https://www.sciencedirect.com/robots.txt"
        content, why, asked = self.fetch(page, {page: self.page(page, "<html>redirect</html>"),
                                                robots: self.page(robots, "User-agent: *\nDisallow: */pdfft*\n")})
        self.assertIsNone(content)
        self.assertEqual((why["robots"], why["pdf_url"]), ("www.sciencedirect.com", pdf))
        self.assertNotIn(pdf, [u for u, _ in asked])

    def test_robots_wildcards(self):
        from urllib.robotparser import RobotFileParser
        rules = RobotFileParser()
        rules.parse("User-agent: *\nDisallow: */pdfft*\nDisallow: /doi/epdf/\nDisallow: /x$\n".splitlines())
        for url, allowed in {"https://s.example/science/article/pii/S1/pdfft?download=true": False,
                             "https://s.example/science/article/pii/S1": True, "https://s.example/doi/epdf/10.1/x": False,
                             "https://s.example/doi/pdf/10.1/x?download=true": True, "https://s.example/x": False,
                             "https://s.example/xy": True}.items():
            self.assertEqual(oa._robots_allow(rules, url), allowed, url)

    def test_a_bot_check_is_never_worked_around(self):
        page = "https://journals.sagepub.com/doi/10.1177/x"
        check = "<html><head><title>Just a moment...</title></head><script src='/cdn-cgi/challenge-platform/x'></script></html>"
        content, why, asked = self.fetch(page, {page: self.page(page, check, status=403, **{"cf-mitigated": "challenge"})})
        self.assertIsNone(content)
        self.assertEqual(len(asked), 1)  # kein zweiter Versuch, kein anderer Weg um die Prüfung
        self.assertEqual(why, {"bot_check": "journals.sagepub.com",
                               "pdf_url": "https://journals.sagepub.com/doi/pdf/10.1177/x?download=true"})
        anubis = "https://repo.example/record/1"
        content, why, _ = self.fetch(anubis, {anubis: self.page(anubis, '<script id="anubis_challenge">{}</script>')})
        self.assertEqual((content, why), (None, {"bot_check": "repo.example"}))

    def test_one_request_per_host_and_second(self):
        clock = [100.0]
        with mock.patch.object(oa.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(oa.time, "sleep", side_effect=lambda s: clock.__setitem__(0, clock[0] + s)) as sleep:
            oa._polite("https://a.example/1")
            oa._polite("https://a.example/2")
            oa._polite("https://b.example/1")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1.0])


def record(dois) -> int:
    """Fragt die echten Dienste und Seiten (höflich, eine Anfrage je Rechner und Sekunde) und speichert jede Antwort."""
    from benthic import __version__
    from benthic.fetcher import BenthicDownloader
    real_api, real_get = oa._api_get, requests.get
    HERE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": tmp}):
        for doi in dois or DOIS:
            api_calls, pages, inside = [], [], [False]

            def api(url, params=None, headers=None, timeout=20.0):
                inside[0] = True
                try:
                    resp = real_api(url, params=params, headers=headers, timeout=max(timeout, 60.0))
                finally:
                    inside[0] = False
                api_calls.append({"url": url, "params": params or {}, "status": None if resp is None else resp.status_code,
                                  "type": "" if resp is None else resp.headers.get("Content-Type", ""),
                                  "body": "" if resp is None else _strip(_body(resp))})
                return resp

            def get(url, *a, **kw):
                resp = real_get(url, *a, **kw)
                if not inside[0]:
                    pages.append(_page(url, resp))
                return resp
            _fresh()
            with mock.patch.object(oa, "_api_get", side_effect=api), mock.patch.object(oa.requests, "get", side_effect=get):
                res = BenthicDownloader(Path(tmp) / "papers", skip_existing=False).download_one(doi)
            (HERE / f"{_slug(doi)}.json").write_text(json.dumps(
                {"doi": doi, "recorded": time.strftime("%Y-%m-%d"), "benthic": __version__, "api": api_calls,
                 "pages": pages}, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
            print(f"{doi:36} {res['status']:8} {res.get('pdf_url') or res.get('landing_url') or ''}  {res.get('error') or ''}")
    return 0


if __name__ == "__main__":
    if "--record" in sys.argv:
        sys.exit(record([a for a in sys.argv[1:] if a != "--record"]))
    unittest.main()
