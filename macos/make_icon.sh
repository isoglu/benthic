#!/usr/bin/env bash
# Rendert die Marken-Assets aus den SVG-Mastern in macos/design/:
#   - macos/AppIcon.icns  (16 und 32 pt aus AppIcon-small.svg, ab 64 px aus AppIcon.svg)
#   - macos/design/AppIcon-1024.png
#   - macos/design/dmg-background.png und dmg-background@2x.png
# Aufruf:  ./macos/make_icon.sh      (braucht macOS 13+ für SVG in AppKit)
set -euo pipefail

cd "$(dirname "$0")/.."   # Projektwurzel
DESIGN="macos/design"
mkdir -p .swiftcache .swifttmp
RENDER=".swifttmp/render_svg"

echo "==> Kompiliere SVG-Renderer"
TMPDIR="$PWD/.swifttmp" swiftc -O macos/tools/render_svg.swift -o "$RENDER" \
  -module-cache-path "$PWD/.swiftcache"

GRID=0.0977   # macOS-Icon-Raster: 824 pt Körper auf 1024 pt Fläche
ICONSET=".swifttmp/AppIcon.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"

small() { "$RENDER" "$DESIGN/AppIcon-small.svg" "$ICONSET/$1" "$2" "$2" "$GRID"; }
full()  { "$RENDER" "$DESIGN/AppIcon.svg" "$ICONSET/$1" "$2"; }

echo "==> Rendere Iconset"
small icon_16x16.png 16
small icon_16x16@2x.png 32
small icon_32x32.png 32
full  icon_32x32@2x.png 64
full  icon_128x128.png 128
full  icon_128x128@2x.png 256
full  icon_256x256.png 256
full  icon_256x256@2x.png 512
full  icon_512x512.png 512
full  icon_512x512@2x.png 1024

iconutil -c icns "$ICONSET" -o macos/AppIcon.icns
cp "$ICONSET/icon_512x512@2x.png" "$DESIGN/AppIcon-1024.png"

echo "==> Rendere DMG-Hintergrund (1x, 2x)"
"$RENDER" "$DESIGN/dmg-background.svg" "$DESIGN/dmg-background.png" 660 420
"$RENDER" "$DESIGN/dmg-background.svg" "$DESIGN/dmg-background@2x.png" 1320 840

echo "Fertig: macos/AppIcon.icns"
