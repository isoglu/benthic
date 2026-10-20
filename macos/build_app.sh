#!/usr/bin/env bash
# Baut Benthic.app (native macOS-App mit gebündeltem Python-Backend).
#
# Aufruf:  ./macos/build_app.sh [--native] [--update-url=URL] [Optionen für eigene Builds]
#   --native          nur die Architektur dieses Macs (schneller, für Entwicklung);
#                     ohne Option entsteht eine Universal-App (Apple Silicon und Intel)
#   --update-url=URL  Adresse des Release-Manifests (macos/make_release.sh) für die
#                     Update-Prüfung; auch über BENTHIC_UPDATE_URL. Ohne Angabe:
#                     https://benthic.isoglu.com/release.json; --update-url=none schaltet sie ab
#   --update-key=DATEI  öffentlicher Schlüssel (Base64) für Test-Builds des Aktualisierens; ohne Angabe
#                     prüft die App Updates mit releaseUpdateKey aus macos/BenthicApp.swift
#
# Eigene Builds (z. B. mit Plugins, siehe docs/extending.md):
#   --plugin=DIR       Plugin-Ordner ins Bundle übernehmen (mehrfach möglich); eine
#                      requirements.txt darin wird mitinstalliert
#   --bundle-id=ID     andere Bundle-Kennung (Standard aus macos/Info.plist)
#   --name=NAME        App-Name, auch für den Ordner NAME.app (Standard: Benthic)
#   --data-folder=NAME Ordner unter ~/Library/Application Support (Standard: Benthic)
#   --port=N           lokaler Port des Backends (Standard: 8327)
#   --out=DIR          Zielordner (Standard: dist)
set -euo pipefail

cd "$(dirname "$0")/.."   # Projektwurzel
ARCH_MODE="universal"
UPDATE_URL="${BENTHIC_UPDATE_URL:-}"; UPDATE_KEY=""
UPDATE_BASE="https://benthic.isoglu.com/"   # Ablage für DMG und Manifest
APP_NAME="Benthic"; BUNDLE_ID=""; DATA_FOLDER=""; PORT=""; BUILD_DIR="dist"
PLUGINS=()
for arg in "$@"; do
  case "$arg" in
    --native) ARCH_MODE="native" ;;
    --update-url=*) UPDATE_URL="${arg#--update-url=}" ;;
    --update-key=*) UPDATE_KEY="${arg#--update-key=}" ;;
    --plugin=*) PLUGINS+=("${arg#--plugin=}") ;;
    --bundle-id=*) BUNDLE_ID="${arg#--bundle-id=}" ;;
    --name=*) APP_NAME="${arg#--name=}" ;;
    --data-folder=*) DATA_FOLDER="${arg#--data-folder=}" ;;
    --port=*) PORT="${arg#--port=}" ;;
    --out=*) BUILD_DIR="${arg#--out=}" ;;
    *) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
  esac
done

[ -z "$UPDATE_URL" ] && UPDATE_URL="${UPDATE_BASE}release.json"
[ "$UPDATE_URL" = "none" ] && UPDATE_URL=""
APP="$BUILD_DIR/$APP_NAME.app"
SWIFT_FLAGS=()
if [ -n "$UPDATE_KEY" ]; then
  UPDATE_KEY_VALUE="$(tr -d ' \n' < "$UPDATE_KEY")"
  SWIFT_FLAGS=(-DBENTHIC_TEST_UPDATE_KEY)
elif [ -n "$UPDATE_URL" ] && ! grep -Eq '^let releaseUpdateKey = "[A-Za-z0-9+/]{43}="$' macos/BenthicApp.swift; then
  # Ohne Schlüssel könnte die App nie ein Update installieren; lieber jetzt abbrechen.
  echo "Kein öffentlicher Update-Schlüssel in macos/BenthicApp.swift (releaseUpdateKey)" >&2
  exit 1
fi
MIN_OS="13.0"   # Intel braucht 13+: die Command Line Tools liefern Swift-Kompatibilitätsbibliotheken nur noch für arm64

echo "==> $APP_NAME ($ARCH_MODE)"
echo "    Updates: ${UPDATE_URL:-aus}"
[ "${#PLUGINS[@]}" -gt 0 ] && echo "    Plugins: ${PLUGINS[*]}"
echo "==> Leere vorherigen Build"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" .swiftcache .swifttmp

echo "==> Kompiliere Swift-App"
# Modul-Cache und TMPDIR im Workspace (umgeht Sandbox-/CLT-Cache-Probleme).
# -file-compilation-dir: im Binary steht macos/BenthicApp.swift statt des Build-Pfads.
build_slice() {  # $1 = arm64 | x86_64, $2 = Ausgabe
  TMPDIR="$PWD/.swifttmp" swiftc -O macos/BenthicApp.swift -o "$2" ${SWIFT_FLAGS[@]+"${SWIFT_FLAGS[@]}"} \
    -target "$1-apple-macos$MIN_OS" -framework Cocoa -framework WebKit \
    -Xfrontend -disable-autolink-framework -Xfrontend Translation -Xlinker -weak_framework -Xlinker Translation \
    -Xfrontend -disable-autolink-framework -Xfrontend FoundationModels -Xlinker -weak_framework -Xlinker FoundationModels \
    -module-cache-path "$PWD/.swiftcache" -file-compilation-dir .
}
if [ "$ARCH_MODE" = "universal" ]; then
  build_slice arm64 .swifttmp/Benthic-arm64
  build_slice x86_64 .swifttmp/Benthic-x86_64
  lipo -create .swifttmp/Benthic-arm64 .swifttmp/Benthic-x86_64 -output "$APP/Contents/MacOS/Benthic"
else
  build_slice "$(uname -m)" "$APP/Contents/MacOS/Benthic"
fi
echo "    Architekturen: $(lipo -archs "$APP/Contents/MacOS/Benthic")"

echo "==> Kopiere Info.plist + App-Icon"
PLIST="$APP/Contents/Info.plist"
cp macos/Info.plist "$PLIST"
if [ "$APP_NAME" != "Benthic" ]; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleName $APP_NAME" -c "Set :CFBundleDisplayName $APP_NAME" "$PLIST"
fi
[ -n "$BUNDLE_ID" ] && /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $BUNDLE_ID" \
  -c "Set :CFBundleURLTypes:0:CFBundleURLName $BUNDLE_ID" "$PLIST"
if [ "$APP_NAME" != "Benthic" ]; then
  # Eigene Builds bekommen ein eigenes URL-Schema und einen eigenen Eintrag im Dienste-Menü,
  # sonst landet der Text in der falschen App.
  SCHEME="$(printf '%s' "$APP_NAME" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-*//; s/-*$//')"
  /usr/libexec/PlistBuddy -c "Set :CFBundleURLTypes:0:CFBundleURLSchemes:0 $SCHEME" \
    -c "Set :NSServices:0:NSPortName $APP_NAME" \
    -c "Set :NSServices:0:NSMenuItem:default Check References with $APP_NAME" "$PLIST"
fi
[ -n "$DATA_FOLDER" ] && /usr/libexec/PlistBuddy -c "Add :BenthicDataFolder string $DATA_FOLDER" "$PLIST"
[ -n "$PORT" ] && /usr/libexec/PlistBuddy -c "Add :BenthicPort integer $PORT" "$PLIST"
[ -n "$UPDATE_KEY" ] && /usr/libexec/PlistBuddy -c "Add :BenthicTestUpdateKey string $UPDATE_KEY_VALUE" "$PLIST"
cp macos/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cp -R macos/Resources/*.lproj "$APP/Contents/Resources/"   # Sprachen: en, de (InfoPlist.strings)

echo "==> Bündle Backend (Code + Python-Runtime + Abhängigkeiten)"
BACKEND="$APP/Contents/Resources/backend"
mkdir -p "$BACKEND"
rsync -a --exclude '__pycache__' --exclude '.DS_Store' benthic "$BACKEND/"
cp run.py requirements.txt "$BACKEND/"
cp LICENSE "$BACKEND/LICENSE.txt"
cp THIRD_PARTY_NOTICES.md "$BACKEND/THIRD_PARTY_NOTICES.txt"
[ -n "$UPDATE_URL" ] && echo "$UPDATE_URL" > "$BACKEND/UPDATE_URL"
REQS=(-r requirements.txt)
for p in ${PLUGINS[@]+"${PLUGINS[@]}"}; do
  [ -d "$p" ] || { echo "Plugin-Ordner fehlt: $p" >&2; exit 1; }
  mkdir -p "$BACKEND/plugins"
  rsync -a --exclude '__pycache__' --exclude '.DS_Store' "${p%/}" "$BACKEND/plugins/"
  if [ -f "$p/requirements.txt" ]; then REQS+=(-r "$p/requirements.txt"); fi
done

echo "==> Python-Runtime ($ARCH_MODE)"
macos/tools/make_universal_runtime.sh "$BACKEND/runtime" "$ARCH_MODE"

echo "==> Installiere Abhängigkeiten in site-packages"
HOST_PY="$(uv python dir)/cpython-${BENTHIC_PY_VERSION:-3.12.14}-macos-aarch64-none/bin/python3.12"
[ -x "$HOST_PY" ] || HOST_PY="$("$PWD/.venv/bin/python" -c 'import sys; print(sys._base_executable)')"
if [ "$ARCH_MODE" = "universal" ]; then
  # universal2-Räder: kompilierte Module (MarkupSafe, charset_normalizer) für beide Architekturen.
  "$HOST_PY" -m pip install --quiet --disable-pip-version-check --target "$BACKEND/site-packages" \
    --platform macosx_11_0_universal2 --python-version 3.12 --implementation cp --abi cp312 \
    --only-binary=:all: "${REQS[@]}"
else
  "$HOST_PY" -m pip install --quiet --disable-pip-version-check --target "$BACKEND/site-packages" "${REQS[@]}"
fi
rm -rf "$BACKEND/site-packages/bin"

echo "==> Prüfe Drittanbieter-Hinweise"
# Jedes mitgelieferte Paket muss in THIRD_PARTY_NOTICES.md stehen.
missing=""
for d in "$BACKEND"/site-packages/*.dist-info; do
  name="$(sed -n 's/^Name: //p' "$d/METADATA" | head -n 1 | tr -d '\r')"
  grep -qiF "$name" THIRD_PARTY_NOTICES.md || missing="$missing $name"
done
if [ -n "$missing" ]; then
  if [ "${#PLUGINS[@]}" -gt 0 ]; then
    echo "    Hinweis, nicht in THIRD_PARTY_NOTICES.md (aus Plugins):$missing"
  else
    echo "Fehlt in THIRD_PARTY_NOTICES.md:$missing (siehe scripts/make_notices.py)" >&2
    exit 1
  fi
fi

echo "==> Fester MCP-Startbefehl (Contents/Resources/bin/benthic-mcp)"
# Ein Pfad pro Installation, den KI-Assistenten direkt starten können: die Plugins und Befehle auf
# benthic.isoglu.com und im Repository rufen ihn auf. Datenordner und Port kommen aus der Info.plist,
# damit eigene Builds (build_app.sh --data-folder/--port) ihre eigene Bibliothek benutzen.
mkdir -p "$APP/Contents/Resources/bin"
cat > "$APP/Contents/Resources/bin/benthic-mcp" <<'MCP'
#!/bin/sh
# Benthic MCP server from this app bundle (stdio). Started by AI assistants.
APP="$(cd "$(dirname "$0")/../../.." && pwd)"
B="$APP/Contents/Resources/backend"
PLIST="$APP/Contents/Info.plist"
FOLDER="$(/usr/libexec/PlistBuddy -c 'Print :BenthicDataFolder' "$PLIST" 2>/dev/null || echo Benthic)"
PORT="$(/usr/libexec/PlistBuddy -c 'Print :BenthicPort' "$PLIST" 2>/dev/null || true)"
export BENTHIC_DATA_DIR="${BENTHIC_DATA_DIR:-$HOME/Library/Application Support/$FOLDER}"
[ -n "$PORT" ] && export BENTHIC_PORT="${BENTHIC_PORT:-$PORT}"
export PYTHONPATH="$B:$B/site-packages" PYTHONDONTWRITEBYTECODE=1
exec "$B/runtime/bin/python3.12" -m benthic.mcp_server
MCP
chmod 755 "$APP/Contents/Resources/bin/benthic-mcp"

echo "==> Kompiliere Bytecode vor (unchecked-hash)"
# Frische mtimes würden zeitgestempelte .pyc veralten lassen; Python schriebe
# sie dann beim Start ins Bundle und bräche die Signatur. unchecked-hash-.pyc
# prüft Python nie gegen die Quelle, -f erneuert alle. -s speichert relative
# Quellpfade (benthic/app.py) statt des Build-Ordners; Tracebacks finden die
# Quellen trotzdem, weil der Backend-Ordner im sys.path liegt.
"$BACKEND/runtime/bin/python3.12" -m compileall -q -f -j 0 -s "$BACKEND" \
  --invalidation-mode unchecked-hash "$BACKEND" >/dev/null

echo "==> Ad-hoc-Signatur (für die Weitergabe: macos/sign_and_notarize.sh)"
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "    (Signatur übersprungen)"

echo ""
echo "Fertig: $APP ($(du -sh "$APP" | cut -f1))"
echo "Starten mit:  open \"$APP\""
