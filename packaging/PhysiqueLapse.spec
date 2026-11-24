# PyInstaller recipe for PhysiqueLapse: Windows -> PhysiqueLapse.exe (one folder, no console
# window); Mac -> PhysiqueLapse.app. Build with packaging\build.ps1 (Windows) or
# packaging/build-mac.sh (Mac), which build the frontend and export CLIP first.
# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH is set by PyInstaller)
BACKEND = ROOT / "backend"
DIST = ROOT / "frontend" / "dist"
if not (DIST / "index.html").exists():
    raise SystemExit("frontend/dist is missing: run `npm run build` in frontend/ (build.ps1 does this)")

# CLIP as ONNX (build.ps1 exports it first): runs on the GPU via DirectML, so PyTorch stays out
CLIP_ONNX = ROOT / "packaging" / "clip_onnx"
if not (CLIP_ONNX / "visual.onnx").exists():
    raise SystemExit("packaging/clip_onnx is missing: run backend/scripts/export_clip_onnx.py --out packaging/clip_onnx (build.ps1 does this)")

datas = [(str(DIST), "frontend_dist"), (str(CLIP_ONNX), "clip_onnx")]
binaries = []
hiddenimports = []
# packages with data files / native libraries that must come along whole
MAC = sys.platform == "darwin"
# (pythonnet / clr_loader: pywebview's Windows backend)
for pkg in ("mediapipe", "webview", "imageio_ffmpeg", "pillow_heif", *(() if MAC else ("clr_loader", "pythonnet"))):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
# ONNX Runtime's DLLs (DirectML.dll, onnxruntime_providers_*.dll); not its torch/onnx-based tooling
binaries += collect_dynamic_libs("onnxruntime")
# the app imports its services and API routers by name at runtime
hiddenimports += collect_submodules("app") + collect_submodules("uvicorn") + ([] if MAC else ["clr"])

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # (matplotlib must stay: mediapipe imports it when it loads)
    # (torch / open_clip: CLIP runs from the bundled ONNX files instead; ~1 GB smaller)
    excludes=["tkinter", "pytest", "IPython", "notebook", "jupyter", "pandas", "tensorboard",
              "torch", "torchvision", "open_clip", "onnx"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PhysiqueLapse",
    console=False,
    icon=str(ROOT / "packaging" / ("icon.icns" if MAC else "icon.ico")),
    version=None if MAC else str(ROOT / "packaging" / "version_info.txt"),
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PhysiqueLapse")  # noqa: F821
if MAC:
    VERSION = os.environ.get("PL_VERSION", "0.0.0")
    app = BUNDLE(  # noqa: F821
        coll,
        name="PhysiqueLapse.app",
        icon=str(ROOT / "packaging" / "icon.icns"),
        bundle_identifier="app.physiquelapse.desktop",
        version=VERSION,
        info_plist={
            "CFBundleName": "PhysiqueLapse",
            "CFBundleDisplayName": "PhysiqueLapse",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.healthcare-fitness",
            # the app's own server on 127.0.0.1 (WebKit's App Transport Security)
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )
