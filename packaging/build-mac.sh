#!/usr/bin/env bash
# Builds the PhysiqueLapse Mac app (Apple Silicon).
#
#   1. builds the frontend (npm run build)
#   2. exports CLIP to ONNX -> packaging/clip_onnx (once; runs on the Apple GPU via Core ML)
#   3. freezes the app with PyInstaller -> packaging/dist/PhysiqueLapse.app
#   4. signs it ad hoc (Apple Silicon refuses to run unsigned code) and packs
#      packaging/out/PhysiqueLapse-<version>-mac.dmg (drag to Applications)
#
# Needs a venv with the backend and the desktop extra:
#   python3.12 -m venv .venv && .venv/bin/pip install -e "backend[desktop]"
#
#   ./packaging/build-mac.sh 0.2.0
set -euo pipefail
PKG="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$PKG")"
PY="${PY:-$ROOT/.venv/bin/python}"
VERSION="${1:-$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/backend/pyproject.toml")}"
VERSION="${VERSION#v}"
echo "==> Building PhysiqueLapse $VERSION for Mac"

(cd "$ROOT/frontend" && { [ -d node_modules ] || npm ci --no-fund --no-audit; } && npm run build)

if [ ! -f "$PKG/clip_onnx/visual.onnx" ]; then
  echo "==> Exporting CLIP to ONNX"
  "$PY" "$ROOT/backend/scripts/export_clip_onnx.py" --out "$PKG/clip_onnx"
fi

PL_VERSION="$VERSION" "$PY" -m PyInstaller --noconfirm --clean --distpath "$PKG/dist" --workpath "$PKG/build" "$PKG/PhysiqueLapse.spec"
APP="$PKG/dist/PhysiqueLapse.app"
codesign --force --deep --sign - "$APP"
codesign --verify --deep "$APP"
echo "==> App: $APP ($(du -sh "$APP" | cut -f1))"

OUT="$PKG/out"
mkdir -p "$OUT"
DMG="$OUT/PhysiqueLapse-$VERSION-mac.dmg"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "PhysiqueLapse" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG"
rm -rf "$STAGE"
echo "==> Done: $DMG"
