"""Siegel für Prüfberichte: Prüfsumme, Ed25519-Signatur und QR-Code, nachprüfbar auf benthic.isoglu.com/v.

Was ein Siegel leistet:

1. Der **Datensatz** (``record``) hält fest, was der Bericht behauptet: Arbeit, Zeitpunkt, Zählungen und
   jede Quelle mit Urteil, DOI und zitiertem Titel. Seine SHA-256-Prüfsumme ist die Siegelnummer.
2. Das **Siegel** (``seal``) ist ein kurzer Binärblock: Kopf (Art, Zeit, Version, Zählungen, Name der Arbeit),
   die Prüfsumme, der öffentliche Schlüssel dieser Installation und eine Ed25519-Signatur über alles davor.
   Es steht im QR-Code; wer ihn scannt, sieht, was der Bericht sagen muss, und ob die Signatur stimmt.
   Nach der Signatur dürfen **Blöcke** folgen, je Art (1 Byte), Länge (2 Byte) und Inhalt. Die Signatur deckt
   sie nicht ab, damit ein Dienst ein Siegel später bestätigen kann, ohne es zu brechen; jeder Block muss sich
   darum selbst belegen (eigene Signatur). Art 1 ist für eine solche Bestätigung reserviert (``ATTESTATION``),
   ihr Inhalt wird mit dem Dienst festgelegt. Leser überspringen Arten, die sie nicht kennen.
3. Der **Link** im Bericht trägt zusätzlich den ganzen Datensatz (komprimiert). Die Prüfseite rechnet die
   Prüfsumme nach und fragt jede DOI selbst bei Crossref und DataCite ab, im Browser der prüfenden Person.

Alles steht im Fragment der Adresse (nach ``#``); Browser schicken es nie an den Server. benthic.isoglu.com
liefert nur die statische Prüfseite aus und sieht keinen Bericht.

Der Schlüssel entsteht beim ersten Bericht und bleibt in diesem Mac (``seal.key`` im Datenordner). Er belegt,
dass ein Bericht seit dem Erstellen unverändert ist und von derselben Installation stammt wie andere Berichte
mit demselben Schlüssel. Gegen einen Bericht aus einer umgebauten Kopie der App hilft keine Signatur; dafür
prüft die Seite die Quellen selbst nach, statt dem Bericht zu glauben.

Ed25519 nach RFC 8032, eigener Code ohne Abhängigkeiten; ein Bericht braucht eine Signatur, die Laufzeit ist
unerheblich.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import struct
import zlib
from typing import Dict, List, Optional, Tuple

from . import __version__, config, qr

VERIFY_URL = "https://benthic.isoglu.com/v"
FORMAT = 1
KINDS = {"report": 0, "batch": 1}
ATTESTATION = 1  # Blockart für die Bestätigung durch einen Dienst (siehe oben); heute schreibt niemand sie
# Zählfelder im Siegel, in fester Reihenfolge je Art.
REPORT_COUNTS = ("verified", "corrected", "partial", "uncertain", "mismatch", "not_found", "retracted")
BATCH_COUNTS = ("checked", "flagged_files", "retracted_files", "errors", "entries", "not_found", "mismatch", "retracted")
NAME_BYTES = 48

# --------------------------------------------------------------------------
# Ed25519 (RFC 8032, Abschnitt 5.1)
# --------------------------------------------------------------------------
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _add(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s: int, point: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    out = (0, 1, 1, 0)
    while s:
        if s & 1:
            out = _add(out, point)
        point = _add(point, point)
        s >>= 1
    return out


def _compress(point: Tuple[int, int, int, int]) -> bytes:
    zi = pow(point[2], _P - 2, _P)
    x, y = point[0] * zi % _P, point[1] * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(raw: bytes) -> Optional[Tuple[int, int, int, int]]:
    if len(raw) != 32:
        return None
    y = int.from_bytes(raw, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _expand(secret: bytes) -> Tuple[int, bytes]:
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(secret: bytes) -> bytes:
    return _compress(_mul(_expand(secret)[0], _G))


def sign(secret: bytes, message: bytes) -> bytes:
    a, prefix = _expand(secret)
    pub = _compress(_mul(a, _G))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    big_r = _compress(_mul(r, _G))
    h = int.from_bytes(hashlib.sha512(big_r + pub + message).digest(), "little") % _L
    return big_r + ((r + h * a) % _L).to_bytes(32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(signature) != 64:
        return False
    a, r = _decompress(public), _decompress(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % _L
    left, right = _mul(s, _G), _add(r, _mul(h, a))
    # Gleichheit in projektiven Koordinaten
    return ((left[0] * right[2] - right[0] * left[2]) % _P == 0
            and (left[1] * right[2] - right[1] * left[2]) % _P == 0)


# --------------------------------------------------------------------------
# Schlüssel dieser Installation
# --------------------------------------------------------------------------
def _key_path():
    return config.data_dir() / "seal.key"


def secret_key() -> bytes:
    """32 zufällige Bytes, beim ersten Aufruf angelegt (nur für den Benutzer lesbar)."""
    path = _key_path()
    try:
        raw = path.read_bytes()
        if len(raw) == 32:
            return raw
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = os.urandom(32)
    tmp = path.with_name(f".seal.key.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    try:
        os.link(tmp, path)  # nie einen vorhandenen Schlüssel überschreiben
    except FileExistsError:
        raw = path.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)
    return raw


# --------------------------------------------------------------------------
# Datensatz, Siegel, Adresse
# --------------------------------------------------------------------------
def canonical(record: Dict) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _cut(text: str, limit: int) -> bytes:
    raw = str(text or "").encode("utf-8")[:limit]
    return raw.decode("utf-8", "ignore").encode("utf-8")


def _version_bytes() -> bytes:
    parts = [int(p) if p.isdigit() else 0 for p in (__version__.split(".") + ["0", "0"])[:3]]
    return bytes(min(255, p) for p in parts)


def _title(r: Dict) -> str:
    """Der zitierte Titel (so steht er in der Arbeit), ohne vorangestellte Nummer wie „4.“ oder „[4]“."""
    claimed, found = r.get("claimed") or {}, r.get("found") or {}
    text = str(claimed.get("title") or r.get("input") or r.get("identifier") or found.get("title") or "")
    return re.sub(r"^\s*(?:\[\d+\]|\(\d+\)|\d+[.)])\s+", "", text)[:240]


def report_record(job: Dict, job_id: str) -> Dict:
    """Was ein Prüfbericht behauptet, ohne Darstellung und unabhängig von der Sprache."""
    results = sorted(job.get("results") or [], key=lambda r: r.get("order", 0))
    doc = job.get("document") or None
    counts = {v: sum(1 for r in results if r.get("verdict") == v) for v in REPORT_COUNTS}
    record = {
        "format": FORMAT, "kind": "report", "app": __version__, "report": str(job_id),
        "time": int(job.get("finished_at") or job.get("created_at") or 0),
        "counts": counts,
        "sources": [[int(r.get("order", i)) + 1, r.get("verdict") or "uncertain", r.get("doi") or "",
                     _title(r), str((r.get("claimed") or {}).get("year") or (r.get("found") or {}).get("year") or "")]
                    for i, r in enumerate(results)],
    }
    if doc:
        record["document"] = {
            "name": str(doc.get("name") or ""), "sha256": doc.get("sha256") or "",
            "entries": int(doc.get("entries") or 0), "citations": int(doc.get("citations") or 0),
            "cross_check": bool(doc.get("cross_check")),
            "missing": [str(m.get("label") or "") for m in doc.get("missing_in_list") or []],
            "uncited": [str(u.get("label") or "") for u in doc.get("uncited") or []],
        }
    quotes = job.get("quote_check") or []
    if quotes:
        record["quotes"] = [[str(q.get("status") or ""), str(q.get("label") or "")] for q in quotes]
    return record


def batch_record(b: Dict) -> Dict:
    t = b.get("totals") or {}
    rows = []
    for r in b.get("rows") or []:
        rows.append([str(r.get("name") or ""), str(r.get("state") or "")]
                    + [int(r.get(k) or 0) for k in ("entries", "missing", "not_found", "mismatch", "retracted", "quotes_bad", "style")])
    return {"format": FORMAT, "kind": "batch", "app": __version__, "report": str(b.get("id") or ""),
            "time": int(b.get("finished_at") or b.get("created_at") or 0), "name": str(b.get("name") or ""),
            "totals": {k: int(t.get(k) or 0) for k in sorted(t) if isinstance(t.get(k), (int, float))}, "rows": rows}


def batch_counts(record: Dict) -> List[int]:
    t, rows = record.get("totals") or {}, record.get("rows") or []
    col = lambda i: sum(r[i] for r in rows if len(r) > i)
    return [t.get("checked", 0), t.get("flagged_files", 0), t.get("retracted_files", 0), t.get("errors", 0),
            col(2), col(4), col(5), col(6)]


def make(record: Dict, name: str, counts: List[int]) -> Dict:
    """Siegel zu einem Datensatz: Bytes, Prüfsumme, Schlüssel und Adressen für QR-Code und Link."""
    body = canonical(record)
    digest = hashlib.sha256(body).digest()
    secret = secret_key()
    pub = public_key(secret)
    raw_name = _cut(name, NAME_BYTES)
    head = (struct.pack(">BBI", FORMAT, KINDS[record["kind"]], max(0, min(record.get("time") or 0, 2 ** 32 - 1)))
            + _version_bytes() + bytes([len(counts)]) + b"".join(struct.pack(">H", min(int(c), 65535)) for c in counts)
            + bytes([len(raw_name)]) + raw_name + digest + pub)
    sealed = head + sign(secret, head)
    base = os.environ.get("BENTHIC_VERIFY_URL") or VERIFY_URL
    packed = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = packed.compress(body) + packed.flush()
    return {
        "digest": digest.hex(), "key": hashlib.sha256(pub).hexdigest(),
        "number": " ".join(digest.hex()[i:i + 4] for i in range(0, 16, 4)).upper(),
        "key_id": " ".join(hashlib.sha256(pub).hexdigest()[i:i + 4] for i in range(0, 8, 4)).upper(),
        "qr_url": f"{base}#{b64(sealed)}",
        "full_url": f"{base}#{b64(sealed)}.{b64(packed)}",
        "record": record,
    }


def parse(seal_b64: str) -> Optional[Dict]:
    """Gegenstück der Prüfseite, für Tests: Siegel lesen und Signatur prüfen."""
    try:
        raw = unb64(seal_b64)
        fmt, kind, when = struct.unpack(">BBI", raw[:6])
        version = ".".join(str(b) for b in raw[6:9])
        n = raw[9]
        counts = [struct.unpack(">H", raw[10 + 2 * i:12 + 2 * i])[0] for i in range(n)]
        k = 10 + 2 * n
        name_len = raw[k]
        name = raw[k + 1:k + 1 + name_len].decode("utf-8")
        k += 1 + name_len
        digest, pub, sig = raw[k:k + 32], raw[k + 32:k + 64], raw[k + 64:k + 128]
        if len(sig) != 64:
            return None
        blocks, j = [], k + 128
        while j < len(raw):  # Blöcke nach der Signatur; einer, der über das Ende reicht, macht das Siegel unlesbar
            size = struct.unpack(">H", raw[j + 1:j + 3])[0]
            if j + 3 + size > len(raw):
                return None
            blocks.append({"type": raw[j], "data": raw[j + 3:j + 3 + size]})
            j += 3 + size
        return {"format": fmt, "kind": kind, "time": when, "version": version, "counts": counts, "name": name,
                "digest": digest.hex(), "valid": verify(pub, raw[:k + 64], sig), "blocks": blocks}
    except (ValueError, struct.error, IndexError, UnicodeDecodeError):
        return None


def qr_svg(url: str, size_mm: float = 30, dark: str = "#071a2a", light: str = "#ffffff", quiet: int = 4) -> str:
    """QR-Code als SVG mit Ruhezone; ``size_mm`` ist die Kantenlänge samt Ruhezone."""
    modules = qr.encode(url, "M")
    n = len(modules) + 2 * quiet
    return (f'<svg class="qr" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{size_mm}mm" '
            f'height="{size_mm}mm" shape-rendering="crispEdges" role="img" aria-label="QR">'
            f'<rect width="{n}" height="{n}" fill="{light}"/><path fill="{dark}" d="{qr.svg_path(modules, quiet)}"/></svg>')
