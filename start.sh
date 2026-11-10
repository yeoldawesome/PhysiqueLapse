#!/usr/bin/env bash
# PhysiqueLapse launcher for macOS / Linux.  ./start.sh [--dev|--setup|--test]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
MODE="${1:-run}"

if [ ! -x "$PY" ]; then
  echo "==> creating .venv"
  python3.12 -m venv "$VENV" 2>/dev/null || python3 -m venv "$VENV"
  "$PY" -m pip install --upgrade pip >/dev/null
fi
if [ ! -f "$VENV/.deps-stamp" ] || [ "$ROOT/backend/pyproject.toml" -nt "$VENV/.deps-stamp" ] || [ "$MODE" = "--setup" ]; then
  echo "==> installing backend dependencies"
  "$PY" -m pip install -e "$ROOT/backend[dev]" --extra-index-url https://download.pytorch.org/whl/cpu
  touch "$VENV/.deps-stamp"
fi
if [ "$MODE" = "--test" ]; then cd "$ROOT/backend" && exec "$PY" -m pytest; fi
if [ ! -f "$ROOT/data/models/pose_landmarker_heavy.task" ] || [ "$MODE" = "--setup" ]; then
  "$PY" "$ROOT/backend/scripts/download_models.py" || echo "WARNING: model download failed"
fi
cd "$ROOT/frontend"
[ -d node_modules ] && [ ! package.json -nt node_modules ] || npm install --no-fund --no-audit
if [ "$MODE" = "--setup" ]; then echo "==> setup complete"; exit 0; fi
if [ "$MODE" = "--dev" ]; then
  (cd "$ROOT/backend" && "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app) &
  API=$!
  trap 'kill $API 2>/dev/null' EXIT
  npm run dev
else
  npm run build
  cd "$ROOT/backend"
  ( sleep 3; (xdg-open http://127.0.0.1:8000 || open http://127.0.0.1:8000) >/dev/null 2>&1 ) &
  exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
fi
