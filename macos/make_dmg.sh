#!/usr/bin/env bash
# Verpackt dist/Benthic.app als Installations-DMG (dist/Benthic-<Version>.dmg).
# Fensterlayout nach macos/design/dmg-background.svg (660 x 420 pt):
#   Benthic.app bei (165, 200), Link auf /Applications bei (495, 200), Icons 128 pt.
# Nur Bordmittel von macOS: hdiutil, tiffutil, osascript (Finder), SetFile falls vorhanden.
# Aufruf:  ./macos/make_dmg.sh   (vorher ./macos/build_app.sh)
set -euo pipefail

cd "$(dirname "$0")/.."   # Projektwurzel
APP_NAME="Benthic"
APP="dist/$APP_NAME.app"
DESIGN="macos/design"

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' macos/Info.plist)"
VOLNAME="$APP_NAME $VERSION"
DMG="dist/$APP_NAME-$VERSION.dmg"

WORK=".swifttmp/dmg"
STAGING="$WORK/staging"
RW_DMG="$WORK/rw.dmg"
OUT_DMG="$WORK/out.dmg"

# Fenster und Iconpositionen in pt, passend zur Hintergrundgrafik.
# Iconpositionen zählen ab der Inhaltsfläche, die Fenstergrenzen in Finder
# schließen aber die Titelleiste ein (28 pt bis macOS 15, 32 pt ab macOS 26).
# Mit 28 pt Zuschlag zeigt das Fenster bis macOS 15 die volle Grafik; unter
# macOS 26 fehlen wenige pt am dunklen unteren Rand.
WIN_X=200; WIN_Y=120
WIN_W=660; WIN_H=420
TITLEBAR=28
ICON_SIZE=128
APP_X=165; APP_Y=200
LINK_X=495; LINK_Y=200

# Bei Abbruch das beschreibbare Image nicht eingehängt zurücklassen.
MOUNT=""
cleanup() {
  if [ -n "$MOUNT" ] && [ -d "$MOUNT" ]; then
    hdiutil detach "$MOUNT" -force -quiet || true
  fi
}
trap cleanup EXIT

if [ ! -d "$APP" ]; then
  echo "Fehler: $APP fehlt. Zuerst ./macos/build_app.sh ausführen." >&2
  exit 1
fi
echo "==> Version $VERSION (aus macos/Info.plist)"

# Ein Start der App schreibt __pycache__ ins Bundle und bricht die Signatur;
# so ein Bundle startet auf anderen Macs nicht. Dann vorher neu bauen.
echo "==> Prüfe Signatur von $APP"
if ! SIG_ERR="$(codesign --verify --deep "$APP" 2>&1)"; then
  printf '%s\n' "$SIG_ERR" | head -n 3 | sed 's/^/    /' >&2
  echo "Fehler: Signatur ungültig (App seit dem Build gestartet?). Neu bauen mit ./macos/build_app.sh" >&2
  exit 1
fi

if [ ! -f "$DESIGN/dmg-background.png" ] || [ ! -f "$DESIGN/dmg-background@2x.png" ]; then
  echo "==> DMG-Hintergrund fehlt, rendere Marken-Assets"
  ./macos/make_icon.sh
fi

# Ein Volume gleichen Namens (etwa ein geöffnetes älteres DMG) aushängen, sonst
# hängt macOS das neue unter "$VOLNAME 1" ein und Finder gestaltet das falsche.
if [ -d "/Volumes/$VOLNAME" ]; then
  echo "==> Hänge vorhandenes Volume aus: /Volumes/$VOLNAME"
  hdiutil detach "/Volumes/$VOLNAME" -force -quiet
fi

echo "==> Bereite Inhalt vor"
rm -rf "$WORK"
mkdir -p "$STAGING/.background"
ditto "$APP" "$STAGING/$APP_NAME.app"      # ditto erhält Signatur und Attribute
ln -s /Applications "$STAGING/Applications"

echo "==> Kombiniere Hintergrund zu Retina-TIFF (1x und 2x)"
tiffutil -cathidpicheck "$DESIGN/dmg-background.png" "$DESIGN/dmg-background@2x.png" \
  -out "$STAGING/.background/background.tiff" 2>&1 | sed 's/^/    /'

echo "==> Erzeuge beschreibbares Image"
# Etwas Reserve für .DS_Store und Volume-Icon.
SIZE_MB=$(( $(du -sm "$STAGING" | cut -f1) + 20 ))
hdiutil create -quiet -srcfolder "$STAGING" -volname "$VOLNAME" \
  -fs HFS+ -fsargs "-c c=64,a=16,e=16" -format UDRW -size "${SIZE_MB}m" "$RW_DMG"

echo "==> Hänge Image ein"
MOUNT="$(hdiutil attach -readwrite -noverify -noautoopen "$RW_DMG" \
  | grep -o '/Volumes/.*$' | head -n 1)"
if [ "$MOUNT" != "/Volumes/$VOLNAME" ]; then
  echo "Fehler: Image liegt unter \"$MOUNT\" statt \"/Volumes/$VOLNAME\"." >&2
  exit 1
fi

# Finder meldet ein frisch eingehängtes Volume mit kurzer Verzögerung; vorher
# scheitert `tell disk` mit -1728.
for _ in $(seq 1 20); do
  osascript -e "tell application \"Finder\" to exists disk \"$VOLNAME\"" 2>/dev/null | grep -q true && break
  sleep 0.5
done

echo "==> Gestalte Finder-Fenster (Hintergrund, Icongröße, Positionen)"
# Beim ersten Lauf fragt macOS nach der Freigabe, Finder zu steuern.
osascript - "$VOLNAME" "$APP_NAME.app" "$WIN_X" "$WIN_Y" "$WIN_W" "$((WIN_H + TITLEBAR))" \
  "$ICON_SIZE" "$APP_X" "$APP_Y" "$LINK_X" "$LINK_Y" >/dev/null <<'APPLESCRIPT'
on run argv
  set volName to item 1 of argv
  set appItem to item 2 of argv
  set winX to (item 3 of argv) as integer
  set winY to (item 4 of argv) as integer
  set winW to (item 5 of argv) as integer
  set winH to (item 6 of argv) as integer
  set iconSize to (item 7 of argv) as integer
  set appX to (item 8 of argv) as integer
  set appY to (item 9 of argv) as integer
  set linkX to (item 10 of argv) as integer
  set linkY to (item 11 of argv) as integer
  tell application "Finder"
    tell disk volName
      open
      set current view of container window to icon view
      set toolbar visible of container window to false
      set statusbar visible of container window to false
      set pathbar visible of container window to false
      set bounds of container window to {winX, winY, winX + winW, winY + winH}
      set opts to icon view options of container window
      set arrangement of opts to not arranged
      set icon size of opts to iconSize
      set text size of opts to 12
      set label position of opts to bottom
      set shows item info of opts to false
      set background picture of opts to file ".background:background.tiff"
      set position of item appItem of container window to {appX, appY}
      set position of item "Applications" of container window to {linkX, linkY}
      close
      open
      update without registering applications
      delay 1
      close
    end tell
  end tell
end run
APPLESCRIPT

# Finder schreibt .DS_Store verzögert; ohne die Datei fehlt das Layout im DMG.
for _ in $(seq 1 20); do
  [ -f "$MOUNT/.DS_Store" ] && break
  sleep 0.5
done
if [ ! -f "$MOUNT/.DS_Store" ]; then
  echo "Fehler: Finder hat kein .DS_Store geschrieben (Freigabe für Finder erteilt?)." >&2
  exit 1
fi

# Erst nach Finder setzen: Finder entfernt beim Gestalten .VolumeIcon.icns
# und löscht das Custom-Icon-Flag des Volumes.
echo "==> Setze Volume-Icon"
SETFILE="$(xcrun --find SetFile 2>/dev/null || true)"
if [ -n "$SETFILE" ]; then
  cp macos/AppIcon.icns "$MOUNT/.VolumeIcon.icns"
  "$SETFILE" -a V "$MOUNT/.background"
  "$SETFILE" -a C "$MOUNT"
else
  echo "    (SetFile nicht gefunden, Volume-Icon übersprungen)"
fi

chmod -Rf go-w "$MOUNT" || true
sync

echo "==> Hänge Image aus"
for _ in 1 2 3 4 5; do
  hdiutil detach "$MOUNT" -quiet && break
  sleep 2
done
[ -d "$MOUNT" ] && hdiutil detach "$MOUNT" -force -quiet
MOUNT=""

echo "==> Komprimiere und prüfe Image"
hdiutil convert -quiet "$RW_DMG" -format UDZO -imagekey zlib-level=9 -o "$OUT_DMG"
hdiutil verify -quiet "$OUT_DMG"

# Erst jetzt das alte DMG ersetzen: bei einem Fehler oben bleibt es unverändert.
mv -f "$OUT_DMG" "$DMG"
rm -rf "$WORK"

echo ""
echo "Fertig: $DMG ($(du -h "$DMG" | awk '{ print $1 }'))"
echo "Öffnen mit:  open $DMG"
