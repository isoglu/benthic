#!/usr/bin/env python3
"""Schreibt THIRD_PARTY_NOTICES.md: alles, was das App-Bundle an Fremdcode mitliefert.

Quellen: die Lizenzdateien der Python-Pakete (aus ihren .dist-info-Ordnern),
die Lizenz der CPython-Laufzeit und die Komponenten, die python-build-standalone
fest in die Laufzeit einbaut (Versionen werden aus der Laufzeit gelesen).

Aufruf:  scripts/make_notices.py [--site-packages DIR] [--runtime DIR]
  ohne --site-packages installiert das Skript requirements.txt wie build_app.sh
  in einen temporären Ordner (universal2-Räder, Python 3.12).
  ohne --runtime nimmt es die von uv verwaltete Laufzeit (wie make_universal_runtime.sh).
"""

from __future__ import annotations

import argparse
import email.parser
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_VERSION = os.environ.get("BENTHIC_PY_VERSION", "3.12.14")


def uv_runtime() -> Path:
    base = subprocess.run(["uv", "python", "dir"], capture_output=True, text=True, check=True).stdout.strip()
    return Path(base) / f"cpython-{PY_VERSION}-macos-aarch64-none"


def install_requirements(runtime: Path, target: Path) -> None:
    subprocess.run([str(runtime / "bin" / "python3.12"), "-m", "pip", "install", "--quiet",
                    "--disable-pip-version-check", "--target", str(target),
                    "--platform", "macosx_11_0_universal2", "--python-version", "3.12",
                    "--implementation", "cp", "--abi", "cp312", "--only-binary=:all:",
                    "-r", str(ROOT / "requirements.txt")], check=True)


def runtime_versions(runtime: Path) -> dict:
    code = ("import json, ssl, sqlite3, pyexpat, _decimal, sys;"
            "print(json.dumps({'python': sys.version.split()[0], 'openssl': ssl.OPENSSL_VERSION.split()[1],"
            "'sqlite': sqlite3.sqlite_version, 'expat': pyexpat.EXPAT_VERSION.split('_')[1],"
            "'mpdecimal': _decimal.__libmpdec_version__}))")
    import json
    out = json.loads(subprocess.run([str(runtime / "bin" / "python3.12"), "-I", "-c", code],
                                    capture_output=True, text=True, check=True).stdout)
    lib = (runtime / "lib" / "libpython3.12.dylib").read_bytes()
    m = re.search(rb"\b(1\.0\.\d+), \d+-\w+-\d{4}", lib)
    out["bzip2"] = m.group(1).decode() if m else ""
    xz = re.findall(rb"\x00(5\.\d+\.\d+)\x00", lib)
    out["xz"] = xz[0].decode() if xz else ""
    build = runtime / "BUILD"
    out["pbs"] = build.read_text().strip() if build.is_file() else ""
    return out


def packages(site: Path) -> list:
    out = []
    for info in sorted(site.glob("*.dist-info"), key=lambda p: p.name.lower()):
        meta = email.parser.Parser().parsestr((info / "METADATA").read_text(encoding="utf-8"))
        lic = meta.get("License-Expression") or ""
        if not lic:
            lic = ", ".join(c.split("::")[-1].strip() for c in meta.get_all("Classifier", []) if c.startswith("License ::"))
        if not lic:
            raw = (meta.get("License") or "").strip()
            lic = raw.splitlines()[0] if raw else "see licence text"
        urls = [u.split(",", 1)[1].strip() for u in meta.get_all("Project-URL", []) if "," in u]
        source = next((u for u in urls if re.search(r"github|gitlab|foss|source|code", u, re.I)), None)
        source = source or meta.get("Home-page") or (urls[0] if urls else "")
        files = sorted(p for p in info.rglob("*") if p.is_file() and re.match(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", p.name, re.I))
        texts = [(p.relative_to(info).as_posix(), p.read_text(encoding="utf-8", errors="replace").strip()) for p in files]
        out.append({"name": meta["Name"], "version": meta["Version"], "license": lic, "source": source, "texts": texts})
    return out


NATIVE = """\
### OpenSSL {openssl}

Licensed under the Apache License, Version 2.0 (full text under *requests*
below). Copyright (c) The OpenSSL Project Authors. All rights reserved.
Source: https://www.openssl.org/source/

### SQLite {sqlite}

Public domain. The SQLite authors disclaim copyright to the source code and
offer this blessing instead: May you do good and not evil. May you find
forgiveness for yourself and forgive others. May you share freely, never
taking more than you give. Source: https://sqlite.org/

### libffi

Copyright (c) Anthony Green, Red Hat, Inc and others.

{mit}

### Expat {expat}

Copyright (c) 1998-2000 Thai Open Source Software Center Ltd and Clark Cooper.
Copyright (c) Expat maintainers.

{mit}

### mpdecimal {mpdecimal}

Copyright (c) Stefan Krah. All rights reserved.

{bsd2}

### bzip2 / libbzip2 {bzip2}

This program, "bzip2", the associated library "libbzip2", and all
documentation, are copyright (C) 1996-2019 Julian R Seward. All rights
reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. The origin of this software must not be misrepresented; you must not
   claim that you wrote the original software. If you use this software in a
   product, an acknowledgment in the product documentation would be
   appreciated but is not required.
3. Altered source versions must be plainly marked as such, and must not be
   misrepresented as being the original software.
4. The name of the author may not be used to endorse or promote products
   derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR "AS IS" AND ANY EXPRESS OR IMPLIED
WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO
EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

### XZ Utils (liblzma) {xz}

Copyright (C) The XZ Utils authors and contributors. Licensed under the BSD
Zero Clause License:

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.

### HACL* (hash functions inside CPython)

Copyright (c) 2016-2022 INRIA, CMU and Microsoft Corporation.

{mit}
"""

MIT = """\
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""

BSD2 = """\
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS "AS IS" AND ANY
EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."""


def render(pkgs: list, v: dict, runtime: Path) -> str:
    cpython_license = (runtime / "lib" / "python3.12" / "LICENSE.txt").read_text(encoding="utf-8").strip()
    lines = [
        "# Third-party notices",
        "",
        "Benthic, developed by Sinan Isoglu, is licensed under the AGPL-3.0 (see",
        "LICENSE). The macOS app bundles the",
        "software below, each under its own licence. This file is generated by",
        "`scripts/make_notices.py` from the packages and runtime that",
        "`macos/build_app.sh` puts into the bundle; the build fails if a bundled",
        "package is missing here.",
        "",
        "## Summary",
        "",
        "| Component | Version | Licence |",
        "|---|---|---|",
        f"| CPython | {v['python']} | PSF License Version 2 |",
        f"| OpenSSL | {v['openssl']} | Apache-2.0 |",
        f"| SQLite | {v['sqlite']} | Public domain |",
        "| libffi | (bundled with the runtime) | MIT |",
        f"| Expat | {v['expat']} | MIT |",
        f"| mpdecimal | {v['mpdecimal']} | BSD-2-Clause |",
        f"| bzip2 | {v['bzip2']} | bzip2 licence (BSD-style) |",
        f"| XZ Utils (liblzma) | {v['xz']} | 0BSD |",
        "| HACL* | (part of CPython) | MIT |",
    ]
    for p in pkgs:
        lines.append(f"| {p['name']} | {p['version']} | {p['license']} |")
    lines += [
        "",
        "## Python runtime",
        "",
        f"The app ships CPython {v['python']} as built by python-build-standalone"
        + (f" (release {v['pbs']})" if v["pbs"] else "") + ",",
        "<https://github.com/astral-sh/python-build-standalone>. Only the",
        "interpreter, the standard library and the libraries statically linked",
        "into it are included; Tcl/Tk, IDLE, pip and headers are removed. zlib,",
        "libedit and ncurses are used from macOS itself and are not bundled.",
        "",
        "### CPython",
        "",
        "```",
        cpython_license,
        "```",
        "",
        "## Libraries linked into the runtime",
        "",
        NATIVE.format(mit=MIT, bsd2=BSD2, **v).strip(),
        "",
        "## Python packages",
        "",
    ]
    for p in pkgs:
        lines += [f"### {p['name']} {p['version']}", "", f"Licence: {p['license']}" + (f". Source: <{p['source']}>" if p["source"] else ""), ""]
        if p["name"].lower() == "certifi":
            lines += ["certifi is covered by the Mozilla Public License 2.0; its source code,",
                      "including the CA bundle, is available at the address above.", ""]
        for rel, text in p["texts"]:
            lines += [f"`{rel}`:", "", "```", text, "```", ""]
        if not p["texts"]:
            lines += ["(No licence file in the package; see the source address.)", ""]
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site-packages", type=Path)
    ap.add_argument("--runtime", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "THIRD_PARTY_NOTICES.md")
    a = ap.parse_args()
    runtime = a.runtime or uv_runtime()
    with tempfile.TemporaryDirectory() as tmp:
        site = a.site_packages
        if site is None:
            site = Path(tmp) / "site-packages"
            install_requirements(runtime, site)
        text = render(packages(site), runtime_versions(runtime), runtime)
    a.out.write_text(text, encoding="utf-8")
    print(f"geschrieben: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
