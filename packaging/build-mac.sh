#!/usr/bin/env bash
# Builds the PhysiqueLapse Mac app for the chip of the Mac it runs on (Apple Silicon or Intel).
#
#   1. builds the frontend (npm run build)
#   2. exports CLIP to ONNX -> packaging/clip_onnx (once; runs on the Apple GPU via Core ML).
#      Intel Macs have no recent PyTorch: put an export made elsewhere in packaging/clip_onnx
#      (the release workflow copies the Apple Silicon job's).
#   3. freezes the app with PyInstaller -> packaging/dist/PhysiqueLapse.app
#   4. signs it ad hoc (Apple Silicon refuses to run unsigned code) and packs
#      packaging/out/PhysiqueLapse-<version>-mac.dmg (Apple Silicon) or ...-mac-intel.dmg
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
ARCH="$(uname -m)"
SUFFIX="mac"
[ "$ARCH" = "x86_64" ] && SUFFIX="mac-intel"
echo "==> Building PhysiqueLapse $VERSION for Mac ($ARCH)"

(cd "$ROOT/frontend" && { [ -d node_modules ] || npm ci --no-fund --no-audit; } && npm run build)

if [ ! -f "$PKG/clip_onnx/visual.onnx" ]; then
  if [ "$ARCH" = "x86_64" ]; then
    echo "packaging/clip_onnx is missing: export CLIP on another machine (backend/scripts/export_clip_onnx.py) and copy it here" >&2
    exit 1
  fi
  echo "==> Exporting CLIP to ONNX"
  "$PY" "$ROOT/backend/scripts/export_clip_onnx.py" --out "$PKG/clip_onnx"
fi

# where CLIP runs here (Core ML on the Apple GPU / Neural Engine) and that it agrees with the
# CPU; Core ML refusing parts of the model shows up as "E5RT" errors, which fail the build
echo "==> Checking CLIP on this Mac"
"$PY" "$ROOT/backend/scripts/check_clip.py" "$PKG/clip_onnx" 2>&1 | tee "$PKG/clip-check.log"
if grep -q "E5RT" "$PKG/clip-check.log"; then
  echo "Core ML could not compile the CLIP model (see the E5RT errors above)" >&2
  exit 1
fi

PL_VERSION="$VERSION" "$PY" -m PyInstaller --noconfirm --clean --distpath "$PKG/dist" --workpath "$PKG/build" "$PKG/PhysiqueLapse.spec"
APP="$PKG/dist/PhysiqueLapse.app"
codesign --force --deep --sign - "$APP"
codesign --verify --deep "$APP"
echo "==> App: $APP ($(du -sh "$APP" | cut -f1))"

OUT="$PKG/out"
mkdir -p "$OUT"
DMG="$OUT/PhysiqueLapse-$VERSION-$SUFFIX.dmg"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "PhysiqueLapse" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG"
rm -rf "$STAGE"
echo "==> Done: $DMG"
