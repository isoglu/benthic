#!/usr/bin/env bash
# Screenshots of the Benthic interface for the website and the README, from a demo data folder.
#
#   scripts/shoot_screens.sh <en|de> <data-dir> <home-dir> <out-dir>
#
# Starts the backend from this checkout on port 8343 (en) or 8344 (de) with the given data and
# home folders, takes the shots with macos/tools/snapshot.swift (offscreen WebKit, 2x) and stops
# the backend again. Result: <out-dir>/site/*-dark.png and *-dark.jpg (2000 px wide, for
# site/img/<lang>/), and for English also <out-dir>/docs/*.png (for docs/screenshots/).
# The demo data needs history entries 6, 8 and 13 (de) or 14 (en), one batch and the project
# llm-citation-study-0011; override the IDs with HIST_* and BATCH if yours differ.
# ONLY=ai (or any part of a file name) retakes just those shots.
set -euo pipefail
cd "$(dirname "$0")/.."

L="${1:?en or de}"; DATA="${2:?data dir}"; HOME_DIR="${3:?home dir}"; OUT="${4:?out dir}"
PORT=8343; THESIS_DEFAULT=14
[ "$L" = "de" ] && { PORT=8344; THESIS_DEFAULT=13; }
HIST_THESIS="${HIST_THESIS:-$THESIS_DEFAULT}"
HIST_VERIFY="${HIST_VERIFY:-8}"; HIST_RESULTS="${HIST_RESULTS:-6}"
BATCH="${BATCH:-$(basename "$(ls "$DATA"/batches/*.json | head -1)" .json)}"
mkdir -p "$OUT/site" "$OUT/docs" .swifttmp

SNAP=.swifttmp/snapshot
[ -x "$SNAP" ] || swiftc -O macos/tools/snapshot.swift -o "$SNAP"

# The demo's download folder follows the data folder wherever it lives.
python3 - "$DATA" <<'PY'
import json, sys, pathlib
d = pathlib.Path(sys.argv[1]); p = d / "settings.json"
s = json.loads(p.read_text(encoding="utf-8")); s["download_dir"] = str(d / "papers")
p.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
PY

BENTHIC_DATA_DIR="$DATA" HOME="$HOME_DIR" PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -c "from benthic.app import app; app.run(host='127.0.0.1', port=$PORT, threaded=True)" \
  >"$OUT/server-$L.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null && break; sleep 0.5; done

PRE="(function(){var s=document.createElement('style');s.textContent='*,*::before,*::after{transition:none!important;animation:none!important;caret-color:transparent!important}';document.head.appendChild(s);document.documentElement.dataset.theme='dark';try{localStorage.setItem('benthic-update-dismissed','9.9.9');localStorage.removeItem('benthic-assistant')}catch(e){};})();"
shot() { # out width height js [wait]; ONLY=<part of a file name> takes just the matching shots
  case "$(basename "$1")" in *"${ONLY:-}"*) ;; *) return 0 ;; esac
  "$SNAP" "http://127.0.0.1:$PORT/" "$1" "$2" "$3" "${5:-3.5}" "$PRE setTimeout(function(){ $4 }, 900); 0"
}

HIDE="var u=document.getElementById('upd-status'); if(u){u.closest('.card').style.display='none'};"
MASK="var fd=document.getElementById('files-dir'); if(fd){fd.textContent='~/Documents/Benthic'};"
PROJ="showView('projects'); setTimeout(function(){showProject('llm-citation-study-0011', true)},400)"
WATCH="showView('projects'); setTimeout(function(){scrollMainTo(document.getElementById('watch-panel'), false)},600)"
AI="showView('settings'); showSettingsSection('ai'); $HIDE (function pick(n){var s=document.getElementById('ai-assistant'); if(s && s.querySelector('option[value=chatgpt]')){s.value='chatgpt'; s.onchange && s.onchange(); setTimeout(function(){var L=mcpInfo && mcpInfo.launcher; if(L){document.querySelectorAll('#ai-guide pre').forEach(function(p){p.textContent=p.textContent.split(L).join('/Users/you/Library/Application Support/Benthic/benthic-mcp.sh')})} scrollMainTo(document.getElementById('ai-card'), false)}, 400)} else if(n<60){setTimeout(function(){pick(n+1)}, 150)}})(0)"
VER="viewHistory('$HIST_VERIFY'); setTimeout(function(){scrollMainTo(document.getElementById('verify-results'), false)},1800)"
THESIS="viewHistory('$HIST_THESIS'); setTimeout(function(){showReportTab('document'); scrollMainTo(document.getElementById('verify-results'), false)},1800)"
PASSAGE="viewHistory('$HIST_THESIS'); setTimeout(function(){var tr=document.querySelector('#verify-body tr[data-doi=\"10.48550/arxiv.1512.03385\"]'); tr.querySelector('.support-btn').click(); setTimeout(function(){scrollMainTo(tr, false)}, 1600)}, 1800)"
SEND="showView('settings'); showSettingsSection('apps'); $HIDE setTimeout(function(){scrollMainTo(document.getElementById('send-card'), false)}, 900)"
BATCHJS="showView('batch'); setTimeout(function(){showBatch('$BATCH'); setTimeout(function(){var q=document.getElementById('batch-quota'); if(q){q.hidden=true}; scrollMainTo(document.getElementById('batch-card'), false)}, 900)}, 500)"

S="$OUT/site"
shot "$S/overview-dark.png" 1280 820 "showView('overview')"
shot "$S/verify-dark.png" 1280 820 "viewHistory('$HIST_VERIFY')"
shot "$S/thesis-dark.png" 1280 820 "$THESIS" 4.5
shot "$S/batch-dark.png" 1280 820 "$BATCHJS" 4
shot "$S/project-dark.png" 1280 820 "$PROJ"
shot "$S/download-dark.png" 1280 820 "viewHistory('$HIST_RESULTS')"
shot "$S/ai-dark.png" 1280 820 "$AI" 6
for f in "$S"/*"${ONLY:-}"*-dark.png; do
  sips -s format jpeg -s formatOptions 82 --resampleWidth 2000 "$f" --out "${f%.png}.jpg" >/dev/null
done

if [ "$L" = "en" ]; then
  D="$OUT/docs"
  shot "$D/overview.png" 1120 800 "showView('overview')"
  shot "$D/overview-dark.png" 1120 800 "showView('overview')"
  shot "$D/verify.png" 1120 800 "$VER" 4
  shot "$D/verify-dark.png" 1120 800 "$VER" 4
  shot "$D/thesis.png" 1120 800 "$THESIS" 4.5
  shot "$D/passage.png" 1120 800 "$PASSAGE" 6
  shot "$D/watch.png" 1120 800 "$WATCH"
  shot "$D/projects.png" 1120 800 "$PROJ"
  shot "$D/results.png" 1120 800 "viewHistory('$HIST_RESULTS')"
  shot "$D/results-dark.png" 1120 800 "viewHistory('$HIST_RESULTS')"
  shot "$D/settings.png" 1120 800 "showView('settings'); $HIDE"
  shot "$D/send.png" 1120 800 "$SEND"
  shot "$D/ai-connect.png" 1120 800 "$AI" 6
  shot "$D/history.png" 1120 800 "showView('history')"
  shot "$D/library.png" 1120 800 "showView('library'); setTimeout(function(){ $MASK }, 1200)" 4
  shot "$D/palette.png" 1120 800 "openPalette()"
  shot "$D/setup.png" 1120 800 "openSetup(true)"
  shot "$D/batch.png" 1280 800 "$BATCHJS" 4
fi
echo "Fertig: $OUT"
