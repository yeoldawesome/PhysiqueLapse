#!/usr/bin/env bash
# Starts the built PhysiqueLapse.app, checks that its server answers (health, system info with
# the GPU / encoder it picked, the web page), then quits it. Used by the release workflow.
set -euo pipefail
PKG="$(cd "$(dirname "$0")" && pwd)"
APPDATA="$HOME/Library/Application Support/PhysiqueLapse"
rm -f "$APPDATA/app.port"
"$PKG/dist/PhysiqueLapse.app/Contents/MacOS/PhysiqueLapse" &
PID=$!
trap 'kill $PID 2>/dev/null || true; tail -30 "$APPDATA/logs/physiquelapse.log" 2>/dev/null || true' EXIT
for _ in $(seq 1 90); do [ -f "$APPDATA/app.port" ] && break; sleep 2; done
PORT="$(cat "$APPDATA/app.port")"
for _ in $(seq 1 60); do curl -fs "http://127.0.0.1:$PORT/api/health" && break; sleep 2; done
echo
curl -fs "http://127.0.0.1:$PORT/api/system" | python3 -c "import json,sys; d=json.load(sys.stdin); print('acceleration:', d['acceleration']); print('clip model present:', d['models']['clip']['present']); assert d['models']['clip']['present']"
curl -fs "http://127.0.0.1:$PORT/" | grep -q '<div id="root">'
echo "smoke test OK"
