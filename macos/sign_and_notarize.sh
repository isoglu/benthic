#!/usr/bin/env bash
# Signiert Benthic mit Developer ID (Hardened Runtime), baut das DMG, notarisiert
# App und DMG bei Apple und heftet die Tickets an (stapler). Danach öffnet
# macOS die App auf jedem Mac ohne Warnung.
#
# Aufruf:
#   ./macos/sign_and_notarize.sh [--dry-run]
#
# Signiert nur den Standard-Build dist/Benthic.app. Ein Build mit Plugins oder
# eigener Bundle-Kennung wird abgelehnt: solche Builds bleiben lokal.
#
# Voraussetzungen (einmalig, siehe README > Releasing):
#   BENTHIC_SIGN_IDENTITY  z. B. "Developer ID Application: Vorname Nachname (TEAMID1234)"
#   BENTHIC_NOTARY_PROFILE Name eines Profils aus `xcrun notarytool store-credentials`
#
# --dry-run signiert ad hoc mit Hardened Runtime und Entitlements, baut das DMG
# und prüft, dass die App so startet; es lädt nichts zu Apple hoch.
set -euo pipefail

cd "$(dirname "$0")/.."   # Projektwurzel
APP_NAME="Benthic"
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
  esac
done

APP="dist/$APP_NAME.app"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' macos/Info.plist)"
DMG="dist/$APP_NAME-$VERSION.dmg"
ENT="macos/entitlements.plist"

if [ "$DRY_RUN" = 1 ]; then
  IDENTITY="-"
  TIMESTAMP="--timestamp=none"
  echo "==> Probelauf: Ad-hoc-Signatur mit Hardened Runtime (kein Upload zu Apple)"
else
  IDENTITY="${BENTHIC_SIGN_IDENTITY:?BENTHIC_SIGN_IDENTITY fehlt (siehe README > Releasing)}"
  PROFILE="${BENTHIC_NOTARY_PROFILE:?BENTHIC_NOTARY_PROFILE fehlt (siehe README > Releasing)}"
  TIMESTAMP="--timestamp"
  security find-identity -v -p codesigning | grep -q "$IDENTITY" \
    || { echo "Zertifikat nicht im Schlüsselbund: $IDENTITY" >&2; exit 1; }
fi

[ -d "$APP" ] || { echo "Erst bauen: ./macos/build_app.sh" >&2; exit 1; }

# Nur der unveränderte Standard-Build wird signiert und notarisiert.
if [ -e "$APP/Contents/Resources/backend/plugins" ]; then
  echo "Abgelehnt: $APP enthält Plugins. Builds mit Plugins werden nicht signiert." >&2
  exit 1
fi
WANT_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' macos/Info.plist)"
HAVE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist")"
if [ "$HAVE_ID" != "$WANT_ID" ]; then
  echo "Abgelehnt: $APP hat die Kennung $HAVE_ID statt $WANT_ID." >&2
  exit 1
fi
./scripts/trace_check.sh "$APP" || { echo "Abgelehnt: trace_check.sh hat Treffer im Bundle gefunden." >&2; exit 1; }

is_macho() {
  case "$(head -c 4 "$1" | xxd -p)" in
    cffaedfe|feedfacf|cafebabe|cefaedfe) return 0 ;;
    *) return 1 ;;
  esac
}

echo "==> Signiere eingebetteten Code (innen nach außen)"
count=0
while IFS= read -r -d '' f; do
  is_macho "$f" || continue
  codesign --force --options runtime $TIMESTAMP --entitlements "$ENT" --sign "$IDENTITY" "$f"
  count=$((count + 1))
done < <(find "$APP/Contents/Resources" -type f -print0)
echo "    $count Dateien"

echo "==> Signiere die App"
codesign --force --options runtime $TIMESTAMP --entitlements "$ENT" --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | tail -2

if [ "$DRY_RUN" = 0 ]; then
  echo "==> Notarisiere die App (dauert meist wenige Minuten)"
  ZIP=".swifttmp/$APP_NAME-notarize.zip"
  rm -f "$ZIP"
  ditto -c -k --keepParent "$APP" "$ZIP"
  xcrun notarytool submit "$ZIP" --keychain-profile "$PROFILE" --wait
  xcrun stapler staple "$APP"
  rm -f "$ZIP"
fi

echo "==> Baue DMG"
./macos/make_dmg.sh
codesign --force $TIMESTAMP --sign "$IDENTITY" "$DMG"

if [ "$DRY_RUN" = 0 ]; then
  echo "==> Notarisiere das DMG"
  xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait
  xcrun stapler staple "$DMG"
  echo "==> Gatekeeper-Prüfung"
  spctl --assess --type execute --verbose=2 "$APP"
  spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
fi

echo ""
echo "Fertig: $DMG"
[ "$DRY_RUN" = 1 ] && echo "(Probelauf: ad hoc signiert, nicht notarisiert. Auf anderen Macs warnt Gatekeeper.)"
exit 0
