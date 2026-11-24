"""Paths and process-wide constants.

Everything the app writes lives under DATA_DIR (default: <project>/data). Set the
DATA_DIR environment variable to relocate it.

Photos, derived images and the database belong to the *library* (see tenancy.py), which is
DATA_DIR itself. The library paths below (ORIGINALS_DIR, THUMBS_DIR, DB_PATH, ...) are looked up
on every use. Paths stored in the database are relative to the library folder, so a library can
be moved or backed up by copying it. Models live in MODELS_DIR.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

# The desktop build (PyInstaller): the built website is bundled next to the code, and each
# Windows user's library lives in %LOCALAPPDATA%\PhysiqueLapse, so installing, updating or
# uninstalling the program never touches it.
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    FRONTEND_DIST = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "frontend_dist"
    _default_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "PhysiqueLapse"
else:
    _default_data = PROJECT_ROOT / "data"

DATA_DIR = Path(os.environ.get("DATA_DIR") or _default_data).resolve()
MODELS_DIR = DATA_DIR / "models"
CLIP_CACHE_DIR = MODELS_DIR / "open_clip"

# library paths, relative to the current library's folder (see the module docstring)
_LIBRARY_PATHS = {
    "ORIGINALS_DIR": "originals",
    "DERIVED_DIR": "derived",
    "EXAMPLES_DIR": "examples",  # uploaded pose examples (not library photos)
    "THUMBS_DIR": "derived/thumbs",
    "PREVIEWS_DIR": "derived/previews",
    "LANDMARKS_DIR": "derived/landmarks",
    "ALIGNED_DIR": "derived/aligned",
    "EXPORTS_DIR": "derived/exports",
    "TRASH_DIR": "trash",
    "INCOMING_DIR": "incoming",
    "DB_PATH": "physiquelapse.db",
}


def library_root() -> Path:
    from .tenancy import current

    return current().root


def __getattr__(name: str) -> Path:
    sub = _LIBRARY_PATHS.get(name)
    if sub is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return library_root() / sub

# ---------------------------------------------------------------------------- process
# Local only: the server listens on this computer's loopback address, no login.


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))
# job runner: model-work threads
CV_WORKERS = max(1, _env_int("PL_CV_WORKERS", 1))
# cap for torch / OpenCV threads
THREADS = _env_int("PL_THREADS", 0)  # 0 = automatic

# uploads from the browser window (drag and drop)
MAX_UPLOAD_MB = max(1, _env_int("PL_MAX_UPLOAD_MB", 300))  # per file
UPLOAD_CHUNK_MB = 8


def cpu_threads(reserve: int = 2) -> int:
    """Threads a model library may use."""
    if THREADS > 0:
        return THREADS
    try:
        n = len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except AttributeError:
        n = os.cpu_count() or 4
    return max(1, n - reserve)


THUMB_LONG_SIDE = 400
PREVIEW_LONG_SIDE = 1280

POSE_MODEL_URLS = {
    "lite": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
    "full": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task",
    "heavy": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task",
}
# Face detection (YuNet) + face recognition (SFace) from the OpenCV model zoo (Apache-2.0); run by
# OpenCV itself (cv2.FaceDetectorYN / cv2.FaceRecognizerSF), used by the "Filter people" feature.
FACE_MODEL_URLS = {
    "detector": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "recognizer": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}
CLIP_MODEL_NAME = "ViT-B-32"
CLIP_PRETRAINED = "laion2b_s34b_b79k"


def clip_weights_path() -> Path:
    """Flat copy of the CLIP weights (the Hugging Face cache nests deep enough to hit
    Windows' 260-character path limit when the data folder itself is deep)."""
    return MODELS_DIR / "clip_vit_b32_laion2b.safetensors"


# CLIP as ONNX (scripts/export_clip_onnx.py): runs on any GPU through ONNX Runtime (DirectML on
# Windows, CUDA where onnxruntime-gpu is installed) without PyTorch. The desktop app ships it.
CLIP_ONNX_FILES = ("visual.onnx", "textual.onnx", "bpe_simple_vocab_16e6.txt.gz", "clip_onnx.json")


def clip_onnx_dir() -> Path | None:
    """Folder holding the exported CLIP ONNX files: bundled with the desktop app, else data/models."""
    dirs = [MODELS_DIR / "clip_onnx"]
    if FROZEN:
        dirs.insert(0, Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "clip_onnx")
    for d in dirs:
        if all((d / f).is_file() for f in CLIP_ONNX_FILES):
            return d
    return None


def pose_model_path(variant: str = "heavy") -> Path:
    return MODELS_DIR / f"pose_landmarker_{variant}.task"


def face_model_path(kind: str) -> Path:
    """kind: detector | recognizer"""
    return MODELS_DIR / FACE_MODEL_URLS[kind].rsplit("/", 1)[-1]


def ensure_dirs() -> None:
    """Shared folders, plus the current library's folders when a library is chosen."""
    from .tenancy import current_or_none

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    lib = current_or_none()
    if lib is not None:
        for sub in _LIBRARY_PATHS.values():
            if sub != "physiquelapse.db":
                (lib.root / sub).mkdir(parents=True, exist_ok=True)


def rel(path: Path) -> str:
    """Path relative to the library folder, with forward slashes, for storage in the DB."""
    return Path(path).resolve().relative_to(library_root().resolve()).as_posix()


def abs_path(stored: str) -> Path:
    """Inverse of rel()."""
    return library_root() / stored


# The one library: DATA_DIR itself.
def _set_default_library() -> None:
    from .tenancy import Library, set_default

    set_default(Library(0, DATA_DIR))


_set_default_library()
