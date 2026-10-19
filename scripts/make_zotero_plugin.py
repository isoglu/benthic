#!/usr/bin/env python3
"""Baut das Zotero-Plug-in (zotero/) als dist/benthic-zotero-<version>.xpi und das Update-Manifest dazu.

Die Version kommt aus benthic/__init__.py und wird in zotero/manifest.json eingetragen. dist/zotero-updates.json
gehört nach benthic.isoglu.com/zotero/updates.json (die Adresse steht als update_url im Manifest); das .xpi hängt am
GitHub-Release. Aufruf aus dem Repository:  python3 scripts/make_zotero_plugin.py [--check]
"""

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "zotero"
DIST = ROOT / "dist"
FILES = ("manifest.json", "bootstrap.js", "icon.png")


def version() -> str:
    for line in (ROOT / "benthic" / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("no __version__")


def manifest(v: str) -> dict:
    data = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))
    data["version"] = v
    return data


def build(check: bool = False) -> Path:
    v = version()
    data = manifest(v)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if check:
        if (SRC / "manifest.json").read_text(encoding="utf-8") != text:
            raise SystemExit("zotero/manifest.json is not at version " + v + ": run scripts/make_zotero_plugin.py")
        return SRC / "manifest.json"
    (SRC / "manifest.json").write_text(text, encoding="utf-8")
    DIST.mkdir(exist_ok=True)
    xpi = DIST / f"benthic-zotero-{v}.xpi"
    with zipfile.ZipFile(xpi, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            z.write(SRC / name, name)
    addon = data["applications"]["zotero"]
    updates = {"addons": {addon["id"]: {"updates": [{
        "version": v,
        "update_link": f"https://github.com/isoglu/benthic/releases/download/v{v}/{xpi.name}",
        "applications": {"zotero": {"strict_min_version": addon["strict_min_version"]}},
    }]}}}
    (DIST / "zotero-updates.json").write_text(json.dumps(updates, indent=2) + "\n", encoding="utf-8")
    return xpi


if __name__ == "__main__":
    out = build("--check" in sys.argv)
    print(out)
