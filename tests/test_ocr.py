"""Scanseiten: Texterkennung mit Unlimited-OCR, wenn installiert, sonst wie bisher mit Hinweis (offline, Modell gemockt)."""

import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benthic import biblio, config, document, identity, mcp_server, ocr, support
from tests import ui_client

PROSE = "The firms in this market sell cars of different quality to buyers who cannot tell them apart. " * 6
RAW = ('<PAGE><BLOCK>title<LEVEL1>[1, 2, 3, 4]<FORK>The Market for "Lemons"\n<BLOCK>image[0, 0, 9, 9]<FORK>\n'
       '<BLOCK>table[0, 0, 9, 9]<FORK><table><tr><td>x</td><td>y</td></tr></table>'
       '<BLOCK>cross_text[1, 1, 2, 2]<FORK>quality &amp; uncertainty')
OCR_TEXT = 'The Market for "Lemons"\n\nx | y\n\nquality & uncertainty'


class Page:
    def __init__(self, text, images=0):
        self._text, self.images = text, [object()] * images

    def extract_text(self):
        return self._text


class Reader:
    """Seite 1 mit Textebene, Seite 2 ein Scan (ein Bild, nur ein Stempel als Text)."""
    def __init__(self, *_args, **_kw):
        self.pages = [Page(PROSE), Page("Downloaded from jstor.org", images=1)]


class Proc:
    def __init__(self, pages=(), code=0):
        self.stdout = iter(json.dumps({"page": n, "raw": RAW}) + "\n" for n in pages)
        self.code = code

    def wait(self):
        return self.code


def finish():
    """Auf den Hintergrundlauf warten."""
    t = ocr._thread
    if t:
        t.join(5)


class Base(unittest.TestCase):
    installed = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "unlimited-ocr"
        self.home.mkdir()
        if self.installed:
            (self.home / ".venv-mlx" / "bin").mkdir(parents=True)
            (self.home / ".venv-mlx" / "bin" / "python").write_text("")
            (self.home / "models" / "Unlimited-OCR-2-mlx-bf16").mkdir(parents=True)
            (self.home / "models" / "Unlimited-OCR-2-mlx-bf16" / "config.json").write_text("{}")
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": str(root / "data"),
                                                "UNLIMITED_OCR_HOME": str(self.home)})
        self.env.start()
        self.pdf = root / "akerlof.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 scan")
        self.digest = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        ocr._queue.clear()
        ocr._failed.clear()
        support._cache.clear()

    def tearDown(self):
        finish()
        self.env.stop()
        self.tmp.cleanup()


class OhneLaufzeit(Base):
    def test_off_by_default_text_as_before(self):
        with mock.patch("benthic.ocr.subprocess.Popen") as popen:
            pages = ocr.pages(Reader(), self.pdf)
        self.assertEqual(pages[0], {"page": 1, "text": PROSE})
        self.assertEqual(pages[1], {"page": 2, "text": "Downloaded from jstor.org", "scan": "off"})
        popen.assert_not_called()
        self.assertIn("off", ocr.note([2], "off", "en"))

    def test_switched_on_but_not_installed(self):
        config.save_settings({"ocr_scans": True})
        with mock.patch("benthic.ocr.subprocess.Popen") as popen:
            pages = ocr.pages(Reader(), self.pdf)
        self.assertEqual(pages[1]["scan"], "missing")
        popen.assert_not_called()
        self.assertEqual(ocr.note([2, 3, 4, 7], "missing", "en"),
                         f"Scan without a text layer: page 2–4, 7. Unlimited-OCR is not installed on this Mac ({self.home}).")

    def test_a_page_with_text_and_a_figure_is_no_scan(self):
        self.assertFalse(ocr.is_scan(Page(PROSE, images=2), PROSE))
        self.assertFalse(ocr.is_scan(Page("", images=0), ""))
        self.assertTrue(ocr.is_scan(Page("", images=1), ""))

    def test_get_fulltext_says_which_page_is_a_scan(self):
        with mock.patch("benthic.biblio.pdf_path", return_value=self.pdf), mock.patch("pypdf.PdfReader", Reader):
            r = biblio.fulltext("10.2307/1879431")
        self.assertIn("[Page 1]\n", r["text"])
        self.assertIn("[Page 2, scan without a text layer]\nDownloaded from jstor.org", r["text"])
        self.assertEqual((r["scan_pages"], r["ocr"]), ([2], "off"))
        with mock.patch("benthic.mcp_server._http", return_value=(200, r)), mock.patch("benthic.mcp_server._report"):
            out = mcp_server.t_get_fulltext({"doi": "10.2307/1879431"})
        self.assertIn("Scan without a text layer: page 2. Text recognition for scans is off (Settings).", out)

    def test_scanned_thesis_says_so(self):
        from benthic import app as appmod

        class Scan(Reader):
            def __init__(self, *_a, **_k):
                self.pages = [Page("", images=1), Page("", images=1)]
        with mock.patch("pypdf.PdfReader", Scan):
            r = ui_client(appmod.app).post("/api/document", data={"file": (io.BytesIO(b"%PDF"), "thesis.pdf")},
                                           content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Scan without a text layer: page 1–2.", r.get_json()["error"])
        self.assertEqual(r.get_json()["document"]["scan_pages"], [1, 2])


class MitLaufzeit(Base):
    installed = True

    def setUp(self):
        super().setUp()
        config.save_settings({"ocr_scans": True})

    def test_runs_in_the_background_then_reads_the_store(self):
        with mock.patch("benthic.ocr.subprocess.Popen", return_value=Proc([2])) as popen:
            first = ocr.pages(Reader(), self.pdf)
            finish()
            second = ocr.pages(Reader(), self.pdf)
            third = ocr.pages(Reader(), self.pdf)
        self.assertEqual(first[1]["scan"], "running")
        self.assertEqual(second[1], {"page": 2, "text": OCR_TEXT, "ocr": True})
        self.assertEqual(third, second)
        self.assertEqual(popen.call_count, 1)
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[0], str(self.home / ".venv-mlx" / "bin" / "python"))
        self.assertEqual(cmd[1:3], ["-I", "-c"])
        self.assertEqual(cmd[4:], [str(self.home), str(self.pdf), "2"])
        self.assertIn("gpu.lock", cmd[3])
        self.assertIn("Multi page parsing.", cmd[3])
        stored = json.loads((Path(os.environ["BENTHIC_DATA_DIR"]) / "ocr" / f"{self.digest}.json").read_text())
        self.assertEqual(stored, {"model": "baidu/Unlimited-OCR-2", "pages": {"2": OCR_TEXT}})

    def test_waits_while_another_run_holds_the_model(self):
        with mock.patch("benthic.ocr.subprocess.Popen", side_effect=[Proc(code=ocr.BUSY), Proc([2])]) as popen, \
             mock.patch("benthic.ocr.time.sleep") as sleep:
            ocr.pages(Reader(), self.pdf)
            finish()
        self.assertEqual(popen.call_count, 2)
        sleep.assert_called_once()
        self.assertEqual(ocr.pages(Reader(), self.pdf)[1]["text"], OCR_TEXT)

    def test_a_failed_run_is_reported_and_not_repeated(self):
        with mock.patch("benthic.ocr.subprocess.Popen", return_value=Proc(code=1)) as popen:
            ocr.pages(Reader(), self.pdf)
            finish()
            again = ocr.pages(Reader(), self.pdf)
        self.assertEqual(again[1]["scan"], "failed")
        self.assertEqual(popen.call_count, 1)

    def test_an_upload_is_copied_for_the_run_and_removed(self):
        seen = []

        def popen(cmd, **_kw):
            seen.append(Path(cmd[5]).is_file())
            return Proc([2])
        data = b"%PDF-1.4 uploaded thesis"
        with mock.patch("benthic.ocr.subprocess.Popen", side_effect=popen):
            scans = []
            with mock.patch("pypdf.PdfReader", Reader):
                document.extract_text("thesis.pdf", data, scans)
            finish()
        self.assertEqual([d["page"] for d in scans], [2])
        self.assertEqual(seen, [True])
        self.assertEqual(list((Path(os.environ["BENTHIC_DATA_DIR"]) / "ocr").glob("*.pdf")), [])

    def test_find_support_picks_up_the_text_once_recognised(self):
        with mock.patch("benthic.ocr.subprocess.Popen", return_value=Proc([2])), mock.patch("pypdf.PdfReader", Reader):
            before = support._pages(self.pdf)
            finish()
            after = support._pages(self.pdf)
        self.assertEqual(before[1]["scan"], "running")
        self.assertIn("uncertainty", after[1]["text"])
        self.assertTrue(after[1]["ocr"])

    def test_identity_never_starts_a_run_but_uses_recognised_pages(self):
        class Scan(Reader):
            def __init__(self, *_a, **_k):
                self.pages = [Page("", images=1)]
        with mock.patch("benthic.ocr.subprocess.Popen") as popen, mock.patch("pypdf.PdfReader", Scan):
            self.assertEqual(identity.check(self.pdf, "10.2307/1879431")["copy"], "no_text")
            popen.assert_not_called()
            ocr._save(self.digest, 1, PROSE)
            self.assertNotEqual(identity.check(self.pdf, "10.2307/1879431")["copy"], "no_text")

    def test_switched_off_ignores_the_store(self):
        ocr._save(self.digest, 2, OCR_TEXT)
        config.save_settings({"ocr_scans": False})
        self.assertEqual(ocr.pages(Reader(), self.pdf)[1]["scan"], "off")


class Ausgabe(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(ocr.clean(RAW), OCR_TEXT)
        self.assertEqual(ocr.clean("plain <|ref|>text"), "plain text")


if __name__ == "__main__":
    unittest.main()
