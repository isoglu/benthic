#!/usr/bin/env bash
# Baut eine schlanke Universal-2-Python-Laufzeit (Apple Silicon + Intel).
#
# Quelle sind die beiden python-build-standalone-Laufzeiten von uv in derselben
# Version. Sie haben denselben Aufbau; nur wenige Dateien sind Maschinencode
# (Interpreter, libpython, wenige dylibs). Diese werden mit lipo zusammengeführt
# und neu signiert (Apple Silicon startet keinen unsignierten Code). Alles, was
# Benthic nicht braucht (Tcl/Tk, IDLE, pip, Header, Build-Konfiguration), bleibt weg.
#
# Aufruf:  macos/tools/make_universal_runtime.sh <Zielordner> [native]
#   native = nur die Architektur dieses Macs (schneller, für Entwicklung)
set -euo pipefail

OUT="${1:?Zielordner fehlt}"
MODE="${2:-universal}"
VER="${BENTHIC_PY_VERSION:-3.12.14}"

runtime_dir() {  # $1 = aarch64 | x86_64  (die von uv verwaltete Laufzeit, nicht die venv)
  local spec="cpython-$VER-macos-$1-none"
  local dir
  dir="$(uv python dir)/$spec"
  if [ ! -x "$dir/bin/python3.12" ]; then
    echo "    lade $spec über uv" >&2
    uv python install "$spec" >&2
  fi
  [ -x "$dir/bin/python3.12" ] || { echo "Laufzeit nicht gefunden: $dir" >&2; exit 1; }
  echo "$dir"
}

ARM="$(runtime_dir aarch64)"
echo "    Apple Silicon: $ARM"
if [ "$MODE" != "native" ]; then
  X86="$(runtime_dir x86_64)"
  echo "    Intel:         $X86"
fi

rm -rf "$OUT"
mkdir -p "$OUT"
rsync -a \
  --exclude '__pycache__' \
  --exclude 'include' --exclude 'share' \
  --exclude 'lib/pkgconfig' --exclude 'lib/itcl*' --exclude 'lib/tcl9*' --exclude 'lib/tk9*' \
  --exclude 'lib/thread*' --exclude 'lib/libtcl*' \
  --exclude 'lib/python3.12/site-packages' --exclude 'lib/python3.12/idlelib' \
  --exclude 'lib/python3.12/tkinter' --exclude 'lib/python3.12/lib-dynload/_tkinter*' \
  --exclude 'lib/python3.12/turtledemo' \
  --exclude 'lib/python3.12/ensurepip' --exclude 'lib/python3.12/config-3.12-darwin' \
  --exclude 'bin/idle*' --exclude 'bin/2to3*' --exclude 'bin/pydoc*' --exclude 'bin/pip*' \
  --exclude 'bin/python*-config' \
  "$ARM/" "$OUT/"
mkdir -p "$OUT/lib/python3.12/site-packages"

# uv schreibt seinen Installationsort in die Build-Konfiguration; zurück auf den
# Präfix von python-build-standalone, damit kein lokaler Pfad im Bundle landet.
for dir in "$ARM" ${X86:+"$X86"}; do
  sed -i '' "s|$dir|/install|g" "$OUT/lib/python3.12/_sysconfigdata__darwin_darwin.py"
done

is_macho() {
  case "$(head -c 4 "$1" | xxd -p)" in
    cffaedfe|feedfacf|cafebabe|cefaedfe) return 0 ;;
    *) return 1 ;;
  esac
}

count=0
while IFS= read -r -d '' f; do
  is_macho "$f" || continue
  rel="${f#"$OUT"/}"
  if [ "$MODE" != "native" ]; then
    if [ ! -f "$X86/$rel" ]; then
      echo "Fehlt in der Intel-Laufzeit: $rel" >&2
      exit 1
    fi
    lipo -create "$ARM/$rel" "$X86/$rel" -output "$f"
  fi
  # Auch die Kennung der Bibliothek trägt sonst den lokalen uv-Pfad.
  case "$rel" in lib/libpython*.dylib) install_name_tool -id "@rpath/$(basename "$rel")" "$f" 2>/dev/null ;; esac
  codesign --force --sign - "$f" 2>/dev/null
  count=$((count + 1))
done < <(find "$OUT" -type f -print0)

echo "    $count Maschinencode-Dateien $( [ "$MODE" = native ] && echo signiert || echo "zusammengeführt und signiert")"
echo "    Architekturen: $(lipo -archs "$OUT/bin/python3.12")"
