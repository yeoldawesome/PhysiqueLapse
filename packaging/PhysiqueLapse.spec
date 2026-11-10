# PyInstaller recipe for PhysiqueLapse.exe (one folder, no console window).
# Build with packaging\build.ps1, which builds the frontend first.
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH is set by PyInstaller)
BACKEND = ROOT / "backend"
DIST = ROOT / "frontend" / "dist"
if not (DIST / "index.html").exists():
    raise SystemExit("frontend/dist is missing: run `npm run build` in frontend/ (build.ps1 does this)")

datas = [(str(DIST), "frontend_dist")]
binaries = []
hiddenimports = []
# packages with data files / native libraries that must come along whole
for pkg in ("mediapipe", "open_clip", "torchvision", "webview", "clr_loader", "pythonnet", "imageio_ffmpeg", "pillow_heif"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
# the app imports its services and API routers by name at runtime
hiddenimports += collect_submodules("app") + collect_submodules("uvicorn") + ["clr"]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # (matplotlib must stay: mediapipe imports it when it loads)
    excludes=["tkinter", "pytest", "IPython", "notebook", "jupyter", "pandas", "tensorboard"],
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
    icon=str(ROOT / "packaging" / "icon.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="PhysiqueLapse")  # noqa: F821
