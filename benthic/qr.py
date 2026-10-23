"""QR-Code nach ISO/IEC 18004, ohne fremden Code: Bytemodus, Fehlerkorrektur L/M/Q/H, Versionen 1 bis 40.

Für das Siegel im Prüfbericht (seal.py). ``encode`` liefert die Modulmatrix, ``svg_path`` einen einzigen
SVG-Pfad daraus (je Zeile zusammenhängende dunkle Läufe als Rechteck), damit der Code im Druck scharf bleibt.
"""

from __future__ import annotations

from typing import List

LEVELS = {"L": 1, "M": 0, "Q": 3, "H": 2}  # Formatbits der Stufen
_ORDER = "LMQH"

# Fehlerkorrektur-Codewörter je Block und Anzahl der Blöcke, Index = Version (Tabelle 9 der Norm).
_ECC_PER_BLOCK = {
    "L": (0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "M": (0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    "Q": (0, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "H": (0, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
}
_BLOCKS = {
    "L": (0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    "M": (0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    "Q": (0, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    "H": (0, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
}

# GF(256) mit dem Polynom x^8 + x^4 + x^3 + x^2 + 1.
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _generator(degree: int) -> List[int]:
    """Koeffizienten von (x - a^0)(x - a^1)...(x - a^(degree-1)) ohne den führenden."""
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= c
            nxt[j + 1] ^= _mul(c, _EXP[i])
        poly = nxt
    return poly[1:]


def _ecc(data: List[int], degree: int) -> List[int]:
    gen = _generator(degree)
    rest = [0] * degree
    for byte in data:
        factor = byte ^ rest.pop(0)
        rest.append(0)
        for k, g in enumerate(gen):
            rest[k] ^= _mul(g, factor)
    return rest


def _raw_modules(version: int) -> int:
    """Module für Daten und Fehlerkorrektur (ohne Funktionsmuster, Format- und Versionsfelder)."""
    n = (16 * version + 128) * version + 64
    if version >= 2:
        align = version // 7 + 2
        n -= (25 * align - 10) * align - 55
        if version >= 7:
            n -= 36
    return n


def _data_codewords(version: int, level: str) -> int:
    return _raw_modules(version) // 8 - _ECC_PER_BLOCK[level][version] * _BLOCKS[level][version]


def _align_positions(version: int) -> List[int]:
    if version == 1:
        return []
    count = version // 7 + 2
    size = version * 4 + 17
    step = 26 if version == 32 else (version * 4 + count * 2 + 1) // (count * 2 - 2) * 2
    return [6] + sorted(size - 7 - i * step for i in range(count - 1))


def capacity(version: int, level: str = "M") -> int:
    """Höchstzahl der Bytes im Bytemodus."""
    head = 4 + (8 if version < 10 else 16)
    return (_data_codewords(version, level) * 8 - head) // 8


class _Matrix:
    def __init__(self, version: int):
        self.version = version
        self.size = version * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def put(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.fixed[y][x] = True

    def finder(self, cx: int, cy: int) -> None:
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < self.size and 0 <= y < self.size:
                    d = max(abs(dx), abs(dy))
                    self.put(x, y, d not in (2, 4))

    def alignment(self, cx: int, cy: int) -> None:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                self.put(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

    def functions(self) -> None:
        s = self.size
        for i in range(s):  # Taktlinien
            self.put(6, i, i % 2 == 0)
            self.put(i, 6, i % 2 == 0)
        self.finder(3, 3)
        self.finder(s - 4, 3)
        self.finder(3, s - 4)
        pos = _align_positions(self.version)
        last = len(pos) - 1
        for i, a in enumerate(pos):
            for j, b in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                self.alignment(a, b)
        self.format_bits("M", 0)  # nur reservieren, später überschrieben
        if self.version >= 7:
            rem = self.version
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.version << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = s - 11 + i % 3, i // 3
                self.put(a, b, bit)
                self.put(b, a, bit)

    def format_bits(self, level: str, mask: int) -> None:
        data = LEVELS[level] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 == 1
        s = self.size
        for i in range(6):
            self.put(8, i, bit(i))
        self.put(8, 7, bit(6))
        self.put(8, 8, bit(7))
        self.put(7, 8, bit(8))
        for i in range(9, 15):
            self.put(14 - i, 8, bit(i))
        for i in range(8):
            self.put(s - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.put(8, s - 15 + i, bit(i))
        self.put(8, s - 8, True)

    def place(self, codewords: List[int]) -> None:
        s, i, total = self.size, 0, len(codewords) * 8
        right = s - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = ((right + 1) & 2) == 0
            for vert in range(s):
                y = s - 1 - vert if upward else vert
                for j in range(2):
                    x = right - j
                    if not self.fixed[y][x] and i < total:
                        self.dark[y][x] = (codewords[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        test = (lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
                lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
                lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
                lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0)[mask]
        for y in range(self.size):
            row, fixed = self.dark[y], self.fixed[y]
            for x in range(self.size):
                if not fixed[x] and test(x, y):
                    row[x] = not row[x]

    def penalty(self) -> int:
        s, score = self.size, 0
        lines = [row[:] for row in self.dark] + [[self.dark[y][x] for y in range(s)] for x in range(s)]
        finder_a = [True, False, True, True, True, False, True, False, False, False, False]
        finder_b = finder_a[::-1]
        for line in lines:
            run, prev = 0, None
            for v in line:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run, prev = 1, v
            if run >= 5:
                score += run - 2
            padded = [False] * 4 + line + [False] * 4
            for k in range(len(padded) - 10):
                window = padded[k:k + 11]
                if window == finder_a or window == finder_b:
                    score += 40
        for y in range(s - 1):
            for x in range(s - 1):
                v = self.dark[y][x]
                if v == self.dark[y][x + 1] == self.dark[y + 1][x] == self.dark[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(row) for row in self.dark)
        score += abs(dark * 20 - s * s * 10) // (s * s) * 10
        return score


def _codewords(data: bytes, version: int, level: str) -> List[int]:
    bits: List[int] = []

    def add(value: int, length: int) -> None:
        bits.extend((value >> (length - 1 - i)) & 1 for i in range(length))

    add(0b0100, 4)
    add(len(data), 8 if version < 10 else 16)
    for b in data:
        add(b, 8)
    room = _data_codewords(version, level) * 8
    add(0, min(4, room - len(bits)))
    add(0, -len(bits) % 8)
    out = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    while len(out) < room // 8:
        out.append(pad[(len(out) - len(bits) // 8) % 2])

    # In Blöcke teilen, Fehlerkorrektur anhängen, verschränken.
    blocks_n, ecc_n = _BLOCKS[level][version], _ECC_PER_BLOCK[level][version]
    raw = _raw_modules(version) // 8
    short_n = blocks_n - raw % blocks_n
    short_len = raw // blocks_n - ecc_n  # Datenbytes eines kurzen Blocks
    blocks, k = [], 0
    for i in range(blocks_n):
        n = short_len + (0 if i < short_n else 1)
        chunk = out[k:k + n]
        k += n
        blocks.append((chunk, _ecc(chunk, ecc_n)))
    result = []
    for i in range(short_len + 1):
        for chunk, _ in blocks:
            if i < len(chunk):
                result.append(chunk[i])
    for i in range(ecc_n):
        for _, ecc in blocks:
            result.append(ecc[i])
    return result


def encode(data, level: str = "M", min_version: int = 1) -> List[List[bool]]:
    """Kleinste passende Version, beste Maske. Liefert Zeilen von Modulen (True = dunkel)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    level = level.upper()
    if level not in LEVELS:
        raise ValueError("level")
    version = next((v for v in range(max(1, min_version), 41) if capacity(v, level) >= len(data)), None)
    if version is None:
        raise ValueError("too long")
    words = _codewords(bytes(data), version, level)
    best, best_score = None, None
    for mask in range(8):
        m = _Matrix(version)
        m.functions()
        m.place(words)
        m.apply_mask(mask)
        m.format_bits(level, mask)
        score = m.penalty()
        if best_score is None or score < best_score:
            best, best_score = m, score
    return best.dark


def svg_path(modules: List[List[bool]], quiet: int = 0) -> str:
    """Ein SVG-Pfad (d) aller dunklen Module, je Zeile zu Läufen zusammengefasst; Einheit = ein Modul."""
    parts = []
    for y, row in enumerate(modules):
        x = 0
        while x < len(row):
            if row[x]:
                start = x
                while x < len(row) and row[x]:
                    x += 1
                parts.append(f"M{start + quiet} {y + quiet}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    return "".join(parts)
