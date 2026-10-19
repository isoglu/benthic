#!/usr/bin/env bash
# Schreibt das Release-Manifest für die Update-Prüfung der App.
#
# Aufruf:  ./macos/make_release.sh [--notes="A|B|C"] [--base-url=URL]
#   --base-url  Ordner-Adresse, unter der DMG und Manifest veröffentlicht werden
#               (Standard: https://benthic.isoglu.com/, wie in build_app.sh)
#   --notes     Neuerungen auf Deutsch, durch | getrennt (Update-Hinweis); die englischen kommen
#               immer aus CHANGELOG.md.
#               Ohne diese Option gelten die fett gesetzten Stichworte der neuesten
#               Version aus CHANGELOG.de.md; deren Nummer muss zu Info.plist passen.
# Ergebnis: dist/release.json. DMG und Manifest
# gehören zusammen in diesen Ordner; build_app.sh hinterlegt in der App die
# passende Manifest-Adresse. Dazu kommt dist/Benthic.mcpb für das GitHub-Release
# (MCP-Registry, Smithery; siehe scripts/make_mcpb.py).
set -euo pipefail

cd "$(dirname "$0")/.."
BASE="https://benthic.isoglu.com/"; NOTES=""
for arg in "$@"; do
  case "$arg" in
    --base-url=*) BASE="${arg#--base-url=}" ;;
    --notes=*) NOTES="${arg#--notes=}" ;;
    *) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
  esac
done
[ -n "$BASE" ] || { echo "--base-url fehlt" >&2; exit 2; }
case "$BASE" in */) ;; *) BASE="$BASE/" ;; esac

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' macos/Info.plist)"
MIN_OS="$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' macos/Info.plist)"
DMG_NAME="Benthic-$VERSION.dmg"
DMG="dist/$DMG_NAME"
[ -f "$DMG" ] || { echo "DMG fehlt: $DMG" >&2; exit 1; }
OUT="dist/release.json"

python3 - "$DMG" "$OUT" "$VERSION" "$MIN_OS" "$BASE$DMG_NAME" "$NOTES" <<'PY'
import hashlib, json, os, re, sys, datetime
dmg, out, version, min_os, url, notes = sys.argv[1:7]
def lead_ins(name):
    """Fett gesetzte Stichworte der neuesten Version; bricht ab, wenn die Version nicht passt."""
    text = open(name, encoding="utf-8").read()
    latest = re.search(r"^## (\S+)", text, re.M)
    if not latest or latest.group(1) != version:
        sys.exit(f"{name} beginnt mit {latest.group(1) if latest else 'nichts'}, Info.plist sagt {version}")
    section = text[latest.end():].split("\n## ")[0]
    return [m.rstrip(".") for m in re.findall(r"^- \*\*(.+?)\*\*", section, re.M)]

notes_en = lead_ins("CHANGELOG.md")
if not notes:
    notes = "|".join(lead_ins("CHANGELOG.de.md"))
h = hashlib.sha256()
with open(dmg, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
manifest = {
    "version": version, "url": url, "sha256": h.hexdigest(), "size": os.path.getsize(dmg),
    "published": datetime.date.today().isoformat(), "minimum_os": min_os,
    "notes": [n.strip() for n in notes.split("|") if n.strip()],   # Deutsch, so lesen es auch ältere Fassungen ohne Sprachwahl
    "notes_en": notes_en,
}
with open(out, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(json.dumps(manifest, indent=2, ensure_ascii=False))
PY
python3 scripts/make_mcpb.py
echo "Fertig: $OUT (zusammen mit $DMG nach $BASE hochladen), dist/Benthic.mcpb an Release v$VERSION hängen"
