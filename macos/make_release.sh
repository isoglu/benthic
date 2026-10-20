#!/usr/bin/env bash
# Schreibt das Release-Manifest für die Update-Prüfung der App.
#
# Aufruf:  ./macos/make_release.sh [--notes="A|B|C"] [--base-url=URL] [--key-file=DATEI] [--update-key=DATEI]
#   --base-url  Ordner-Adresse, unter der DMG und Manifest veröffentlicht werden
#               (Standard: https://benthic.isoglu.com/, wie in build_app.sh)
#   --notes     Neuerungen auf Deutsch, durch | getrennt (Update-Hinweis); die englischen kommen
#               immer aus CHANGELOG.md.
#               Ohne diese Option gelten die fett gesetzten Stichworte der neuesten
#               Version aus CHANGELOG.de.md; deren Nummer muss zu Info.plist passen.
#   --key-file  privater Signaturschlüssel als Datei (0600); ohne Angabe aus dem Schlüsselbund
#               (Eintrag "Benthic release signing", macOS fragt um Erlaubnis), siehe macos/tools/sign_update.swift
#   --update-key  öffentlicher Schlüssel eines Test-Builds (build_app.sh --update-key); ohne Angabe muss der
#               Schlüssel zu releaseUpdateKey in macos/BenthicApp.swift passen
# Das Manifest trägt eine Ed25519-Signatur über Version und Prüfsumme des DMG (Feld signature); die App
# installiert ab 1.1.2 nur Updates mit gültiger Signatur, ältere Fassungen übergehen das Feld.
# Ergebnis: dist/release.json. DMG und Manifest
# gehören zusammen in diesen Ordner; build_app.sh hinterlegt in der App die
# passende Manifest-Adresse. Dazu kommt dist/Benthic.mcpb für das GitHub-Release
# (MCP-Registry, Smithery; siehe scripts/make_mcpb.py) und dist/benthic-zotero-<Version>.xpi,
# das Zotero-Plug-in; dist/zotero-updates.json gehört nach benthic.isoglu.com/zotero/updates.json.
set -euo pipefail

cd "$(dirname "$0")/.."
BASE="https://benthic.isoglu.com/"; NOTES=""; KEY_ARGS=(); UPDATE_KEY=""
for arg in "$@"; do
  case "$arg" in
    --base-url=*) BASE="${arg#--base-url=}" ;;
    --notes=*) NOTES="${arg#--notes=}" ;;
    --key-file=*) KEY_ARGS=("$arg") ;;
    --update-key=*) UPDATE_KEY="${arg#--update-key=}" ;;
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

echo "==> Signaturschlüssel"
mkdir -p .swiftcache .swifttmp
SIGNER=".swifttmp/sign_update"
TMPDIR="$PWD/.swifttmp" swiftc -O macos/tools/sign_update.swift -o "$SIGNER" -module-cache-path "$PWD/.swiftcache"
if [ -n "$UPDATE_KEY" ]; then
  EXPECTED="$(tr -d ' \n' < "$UPDATE_KEY")"
else
  EXPECTED="$(sed -n 's/^let releaseUpdateKey = "\(.*\)"$/\1/p' macos/BenthicApp.swift)"
fi
[ -n "$EXPECTED" ] || { echo "Kein öffentlicher Schlüssel in macos/BenthicApp.swift (releaseUpdateKey)" >&2; exit 1; }
ACTUAL="$("$SIGNER" public ${KEY_ARGS[@]+"${KEY_ARGS[@]}"})"
[ "$ACTUAL" = "$EXPECTED" ] || { echo "Der Signaturschlüssel passt nicht zum öffentlichen Schlüssel der App" >&2; exit 1; }

python3 - "$DMG" "$OUT" "$VERSION" "$MIN_OS" "$BASE$DMG_NAME" "$NOTES" "$SIGNER" ${KEY_ARGS[@]+"${KEY_ARGS[@]}"} <<'PY'
import base64, hashlib, json, os, re, subprocess, sys, datetime
dmg, out, version, min_os, url, notes, signer = sys.argv[1:8]
key_args = sys.argv[8:]
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
# Gleich in benthic/updates.py (SIGNED_FORMAT) und BenthicApp.swift (UpdateSignature.message).
message = f"Benthic update\nversion {version}\nsha256 {h.hexdigest()}\n".encode()
signature = subprocess.run([signer, "sign", *key_args], input=message, capture_output=True, check=True).stdout.decode().strip()
if len(base64.b64decode(signature, validate=True)) != 64:
    sys.exit("Signatur hat nicht 64 Bytes")
manifest = {
    "version": version, "url": url, "sha256": h.hexdigest(), "size": os.path.getsize(dmg),
    "published": datetime.date.today().isoformat(), "minimum_os": min_os,
    "notes": [n.strip() for n in notes.split("|") if n.strip()],   # Deutsch, so lesen es auch ältere Fassungen ohne Sprachwahl
    "notes_en": notes_en,
    "signature": signature,   # Ed25519 über message; ältere Fassungen der App übergehen das Feld
}
with open(out, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(json.dumps(manifest, indent=2, ensure_ascii=False))
PY
python3 scripts/make_mcpb.py
python3 scripts/make_zotero_plugin.py
echo "Fertig: $OUT (zusammen mit $DMG nach $BASE hochladen), dist/Benthic.mcpb und dist/benthic-zotero-$VERSION.xpi an Release v$VERSION hängen, dist/zotero-updates.json als zotero/updates.json auf die Website"
