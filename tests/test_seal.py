"""Siegel im Prüfbericht: Ed25519, Siegelformat, QR-Code (offline)."""

import base64
import hashlib
import json
import os
import struct
import tempfile
import unittest
import zlib
from unittest import mock

from benthic import qr, seal

JOB = {
    "id": "7", "mode": "verify", "finished_at": 1792300000,
    "document": {"name": "thesis.docx", "sha256": "ab" * 32, "entries": 2, "citations": 3, "cross_check": True,
                 "missing_in_list": [{"label": "Brown, 2021"}], "uncited": []},
    "results": [
        {"order": 0, "verdict": "verified", "doi": "10.1038/s41586-020-2649-2",
         "claimed": {"title": "Array programming with NumPy", "year": "2020"}, "found": {"year": "2020"}},
        {"order": 1, "verdict": "retracted", "doi": "10.1016/S0140-6736(97)11096-0", "input": "2. Wakefield (1998). Ileal."},
    ],
}


class Ed25519(unittest.TestCase):
    def test_rfc8032_vectors(self):
        cases = [
            ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
             "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
             "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
            ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
             "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
             "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
        ]
        for secret, public, msg, sig in cases:
            sk, m = bytes.fromhex(secret), bytes.fromhex(msg)
            self.assertEqual(seal.public_key(sk).hex(), public)
            self.assertEqual(seal.sign(sk, m).hex(), sig)
            self.assertTrue(seal.verify(bytes.fromhex(public), m, bytes.fromhex(sig)))
            self.assertFalse(seal.verify(bytes.fromhex(public), m + b"x", bytes.fromhex(sig)))


class Siegel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BENTHIC_DATA_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def make(self, job=JOB):
        record = seal.report_record(job, job["id"])
        return record, seal.make(record, job["document"]["name"], [record["counts"][v] for v in seal.REPORT_COUNTS])

    def test_key_is_created_once_and_private(self):
        first = seal.secret_key()
        self.assertEqual(seal.secret_key(), first)
        mode = os.stat(os.path.join(self.tmp.name, "seal.key")).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_record_is_language_free_and_cleaned(self):
        record, _ = self.make()
        self.assertEqual(record["counts"]["retracted"], 1)
        self.assertEqual(record["sources"][1][3], "Wakefield (1998). Ileal.")  # Nummer „2.“ entfernt
        self.assertEqual(record["sources"][0][4], "2020")
        self.assertEqual(record["document"]["sha256"], "ab" * 32)

    def test_seal_roundtrip_and_tamper(self):
        record, s = self.make()
        fragment = s["qr_url"].split("#", 1)[1]
        parsed = seal.parse(fragment)
        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["name"], "thesis.docx")
        self.assertEqual(parsed["counts"], [1, 0, 0, 0, 0, 0, 1])
        self.assertEqual(parsed["digest"], hashlib.sha256(seal.canonical(record)).hexdigest())
        raw = bytearray(seal.unb64(fragment))
        raw[11] ^= 1  # eine Zählung ändern
        self.assertFalse(seal.parse(seal.b64(bytes(raw)))["valid"])
        self.assertIsNone(seal.parse("kaputt"))

    def test_full_link_carries_the_record(self):
        record, s = self.make()
        sealed, packed = s["full_url"].split("#", 1)[1].split(".")
        body = zlib.decompress(base64.urlsafe_b64decode(packed + "=" * (-len(packed) % 4)), -15)
        self.assertEqual(json.loads(body), record)
        self.assertEqual(hashlib.sha256(body).hexdigest(), s["digest"])
        self.assertTrue(s["qr_url"].startswith("https://benthic.isoglu.com/v#"))

    def test_blocks_after_the_signature_keep_the_seal_valid(self):
        _, s = self.make()
        raw = seal.unb64(s["qr_url"].split("#", 1)[1])
        self.assertEqual(seal.parse(seal.b64(raw))["blocks"], [])
        block = lambda kind, data: bytes([kind]) + struct.pack(">H", len(data)) + data
        later = raw + block(seal.ATTESTATION, b"\x01" * 120) + block(200, b"unknown")
        parsed = seal.parse(seal.b64(later))
        self.assertTrue(parsed["valid"])  # ein später angehängter Block bricht die Signatur nicht
        self.assertEqual([(b["type"], len(b["data"])) for b in parsed["blocks"]], [(seal.ATTESTATION, 120), (200, 7)])
        self.assertIsNone(seal.parse(seal.b64(later[:-1])))  # abgeschnittener Block
        self.assertIsNone(seal.parse(seal.b64(raw + b"\x01\x00")))  # Kopf ohne Länge

    def test_same_report_same_seal(self):
        self.assertEqual(self.make()[1]["qr_url"], self.make()[1]["qr_url"])

    def test_long_names_are_cut_on_a_character(self):
        job = dict(JOB, document=dict(JOB["document"], name="Ä" * 80))
        record = seal.report_record(job, "7")
        s = seal.make(record, job["document"]["name"], [0] * 7)
        self.assertEqual(seal.parse(s["qr_url"].split("#")[1])["name"], "Ä" * 24)


class QR(unittest.TestCase):
    def test_versions_and_structure(self):
        for text, version in (("HELLO", 1), ("x" * 100, 6), ("x" * 300, 13)):
            m = qr.encode(text, "M")
            self.assertEqual(len(m), 4 * version + 17)
            n = len(m)
            for x0, y0 in ((0, 0), (n - 7, 0), (0, n - 7)):  # drei Suchmuster
                self.assertTrue(all(m[y0][x0 + i] and m[y0 + 6][x0 + i] for i in range(7)))
                self.assertTrue(m[y0 + 3][x0 + 3])
                self.assertFalse(m[y0 + 1][x0 + 1])
            self.assertTrue(m[n - 8][8])  # das dunkle Modul

    def test_capacity_and_limits(self):
        spec_m = [14, 26, 42, 62, 84, 106, 122, 152, 180, 213, 251, 287, 331]  # Bytemodus, Stufe M (ISO 18004)
        self.assertEqual([qr.capacity(v, "M") for v in range(1, 14)], spec_m)
        self.assertEqual(qr.capacity(40, "L"), 2953)
        with self.assertRaises(ValueError):
            qr.encode("x" * 3000, "L")

    def test_svg(self):
        svg = seal.qr_svg("https://benthic.isoglu.com/v#abc")
        self.assertIn('viewBox="0 0 37 37"', svg)  # Version 3 (29 Module) mit Ruhezone
        self.assertIn("<path", svg)


if __name__ == "__main__":
    unittest.main()
