"""Zotero: Import über einen nachgebauten Connector, Lesen einer nachgebauten Datenbank (offline)."""

import json
import os
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from benthic import zotero

RIS = "TY  - JOUR\nTI  - Array programming with NumPy\nDO  - 10.1038/s41586-020-2649-2\nER  - \n"


class FakeZotero(BaseHTTPRequestHandler):
    received = []
    sessions = set()

    def log_message(self, *args):
        pass

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Zotero-Version", "10.0.6")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/connector/ping":
            return self._send(200, b"Zotero is running", "text/html")
        self._send(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if self.path == "/connector/getSelectedCollection":
            return self._send(200, json.dumps({"libraryName": "My Library", "libraryEditable": True,
                                               "id": 7, "name": "Thesis"}).encode())
        if self.path.startswith("/connector/import"):
            session = self.path.split("session=")[-1]
            if session in self.sessions:
                return self._send(409, b'{"error":"SESSION_EXISTS"}')
            self.sessions.add(session)
            assert self.headers.get("X-Zotero-Connector-API-Version") == "3"
            assert self.headers.get("Content-Type").startswith("text/plain")
            text = body.decode("utf-8")
            if not text.startswith("TY  -"):
                return self._send(400)
            FakeZotero.received.append(text)
            return self._send(201, json.dumps([{"title": "Array programming with NumPy"}]).encode())
        self._send(404)


def make_db(folder: Path):
    con = sqlite3.connect(folder / "zotero.sqlite")
    con.executescript("""
        CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
        CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
        CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INT, libraryID INT, key TEXT);
        CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT);
        CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value);
        CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY);
        CREATE TABLE groups (groupID INTEGER PRIMARY KEY, libraryID INT);
        INSERT INTO itemTypes VALUES (1, 'journalArticle'), (2, 'attachment'), (3, 'book');
        INSERT INTO fields VALUES (1, 'title'), (2, 'DOI'), (3, 'extra'), (4, 'date');
        INSERT INTO items VALUES (1, 1, 1, 'AAAA1111'), (2, 1, 1, 'BBBB2222'), (3, 2, 1, 'CCCC3333'),
                                 (4, 3, 2, 'DDDD4444'), (5, 1, 1, 'EEEE5555');
        INSERT INTO itemDataValues VALUES (1, 'Ileal-lymphoid-nodular hyperplasia'), (2, '10.1016/S0140-6736(97)11096-0'),
            (3, 'Array programming with NumPy'), (4, 'https://doi.org/10.1038/s41586-020-2649-2'),
            (5, 'attachment.pdf'), (6, 'A book in a group'), (7, 'Original: x' || char(10) || 'DOI: 10.1234/book.1'),
            (8, 'Deleted paper'), (9, '10.1234/deleted'), (10, '1998-02-28');
        INSERT INTO itemData VALUES (1,1,1), (1,2,2), (1,4,10), (2,1,3), (2,2,4), (3,1,5), (4,1,6), (4,3,7), (5,1,8), (5,2,9);
        INSERT INTO deletedItems VALUES (5);
        INSERT INTO groups VALUES (4242, 2);
    """)
    con.commit()
    con.close()


class Connector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeZotero)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = zotero.PORT
        zotero.PORT = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        zotero.PORT = cls.port

    def test_status_and_send(self):
        st = zotero.status()
        self.assertTrue(st["running"])
        self.assertEqual(st["version"], "10.0.6")
        self.assertEqual(st["target"]["collection"], "Thesis")
        self.assertEqual(zotero.send(RIS)["sent"], 1)
        self.assertEqual(zotero.send(RIS)["sent"], 1)  # neue Sitzung je Import, kein 409
        self.assertEqual(FakeZotero.received[-1], RIS)

    def test_errors(self):
        with self.assertRaises(zotero.ZoteroError) as e:
            zotero.send("not ris")
        self.assertEqual(e.exception.status, 502)
        with self.assertRaises(zotero.ZoteroError):
            zotero.send("  ")

    def test_not_running(self):
        old, zotero.PORT = zotero.PORT, 9  # nichts hört dort
        try:
            self.assertFalse(zotero.status()["running"])
            with self.assertRaises(zotero.ZoteroError) as e:
                zotero.send(RIS)
            self.assertEqual(e.exception.status, 503)
        finally:
            zotero.PORT = old


class Bibliothek(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        make_db(self.folder)

    def tearDown(self):
        self.tmp.cleanup()

    def test_items(self):
        items = {it["key"]: it for it in zotero.library_items(self.folder)}
        self.assertEqual(set(items), {"AAAA1111", "BBBB2222", "DDDD4444"})  # ohne Anhang und Papierkorb
        self.assertEqual(items["AAAA1111"]["doi"], "10.1016/S0140-6736(97)11096-0")
        self.assertEqual(items["AAAA1111"]["year"], "1998")
        self.assertEqual(items["BBBB2222"]["doi"], "10.1038/s41586-020-2649-2")
        self.assertEqual(items["DDDD4444"]["doi"], "10.1234/book.1")  # aus "Extra"
        self.assertEqual(items["DDDD4444"]["link"], "zotero://select/groups/4242/items/DDDD4444")
        self.assertEqual(items["AAAA1111"]["link"], "zotero://select/library/items/AAAA1111")

    def test_scan(self):
        def fake_scan(dois, email=""):
            out = {d.lower(): {"doi": d, "title": None, "notices": [], "ok": True} for d in dois}
            out["10.1016/s0140-6736(97)11096-0"]["notices"] = [
                {"kind": "retraction", "type": "retraction", "notice_doi": "10.1016/s0140-6736(10)60175-4",
                 "date": "2010-02-06", "source": ["retraction-watch"]}]
            return out
        with mock.patch("benthic.watch.scan", side_effect=fake_scan), \
             mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name}):
            r = zotero.scan_library(self.folder)
        self.assertEqual((r["items"], r["with_doi"], r["checked"]), (3, 3, 3))
        self.assertEqual([f["key"] for f in r["flagged"]], ["AAAA1111"])

    def test_data_dir_from_prefs(self):
        home = Path(self.tmp.name) / "home"
        prefs = home / "Library" / "Application Support" / "Zotero" / "Profiles" / "abc.default"
        prefs.mkdir(parents=True)
        (prefs / "prefs.js").write_text(f'user_pref("extensions.zotero.dataDir", "{self.folder}");\n'
                                        'user_pref("extensions.zotero.useDataDir", true);\n')
        with mock.patch("pathlib.Path.home", return_value=home), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BENTHIC_ZOTERO_DIR", None)
            self.assertEqual(zotero.data_dir(), self.folder)

    def test_missing_library(self):
        with self.assertRaises(zotero.ZoteroError):
            zotero.library_items(Path(self.tmp.name) / "nowhere")


if __name__ == "__main__":
    unittest.main()
