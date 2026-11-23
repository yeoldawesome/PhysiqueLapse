"""Paths and process-wide constants.

Everything the app writes lives under DATA_DIR (default: <project>/data). Set the
DATA_DIR environment variable to relocate it. Paths stored in the database are
relative to DATA_DIR so the folder can be moved or backed up by copying it.
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
ORIGINALS_DIR = DATA_DIR / "originals"
DERIVED_DIR = DATA_DIR / "derived"
EXAMPLES_DIR = DATA_DIR / "examples"  # uploaded pose examples (not library photos)
THUMBS_DIR = DERIVED_DIR / "thumbs"
PREVIEWS_DIR = DERIVED_DIR / "previews"
LANDMARKS_DIR = DERIVED_DIR / "landmarks"
ALIGNED_DIR = DERIVED_DIR / "aligned"
EXPORTS_DIR = DERIVED_DIR / "exports"
MODELS_DIR = DATA_DIR / "models"
CLIP_CACHE_DIR = MODELS_DIR / "open_clip"
TRASH_DIR = DATA_DIR / "trash"
INCOMING_DIR = DATA_DIR / "incoming"
DB_PATH = DATA_DIR / "physiquelapse.db"

# ---------------------------------------------------------------------------- mode
# desktop (default): the local app as it has always been - 127.0.0.1, no login.
# server: the same app on a home server, used from phones and laptops in a browser; login
# required on every /api route. Everything server-only reads these values, never a second
# copy of the code.


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


MODE = (os.environ.get("PL_MODE") or "desktop").strip().lower()
if MODE not in ("desktop", "server"):
    MODE = "desktop"
SERVER = MODE == "server"

HOST = os.environ.get("PL_HOST") or os.environ.get("HOST") or ("0.0.0.0" if SERVER else "127.0.0.1")
PORT = int(os.environ.get("PORT", "8420" if SERVER else "8000"))

COOKIE_SECURE = _env_bool("PL_COOKIE_SECURE", True)  # tailscale serve gives us HTTPS
SESSION_DAYS = _env_int("PL_SESSION_DAYS", 30)
# extra origins allowed to send state-changing requests (besides the request's own host)
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in (os.environ.get("PL_ALLOWED_ORIGINS") or "").split(",") if o.strip()]
TRUSTED_PROXIES = os.environ.get("PL_TRUSTED_PROXIES") or "*"
# job runner: model-work threads and their priority (server mode shares the CPU with other apps)
CV_WORKERS = max(1, _env_int("PL_CV_WORKERS", 1))
JOB_NICE = _env_int("PL_JOB_NICE", 10 if SERVER else 0)
# cap for torch / OpenCV threads; os.cpu_count() sees every host CPU inside a container
THREADS = _env_int("PL_THREADS", 0)  # 0 = automatic (as before)


def cpu_threads(reserve: int = 2) -> int:
    """Threads a model library may use."""
    if THREADS > 0:
        return THREADS
    try:
        n = len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except AttributeError:
        n = os.cpu_count() or 4
    return max(1, n - reserve)


def auth_required() -> bool:
    """Read at request time (tests switch modes)."""
    return SERVER

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


def pose_model_path(variant: str = "heavy") -> Path:
    return MODELS_DIR / f"pose_landmarker_{variant}.task"


def face_model_path(kind: str) -> Path:
    """kind: detector | recognizer"""
    return MODELS_DIR / FACE_MODEL_URLS[kind].rsplit("/", 1)[-1]


def ensure_dirs() -> None:
    for d in (
        DATA_DIR,
        ORIGINALS_DIR,
        THUMBS_DIR,
        PREVIEWS_DIR,
        LANDMARKS_DIR,
        ALIGNED_DIR,
        EXPORTS_DIR,
        MODELS_DIR,
        TRASH_DIR,
        INCOMING_DIR,
        EXAMPLES_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def rel(path: Path) -> str:
    """Path relative to DATA_DIR, with forward slashes, for storage in the DB."""
    return Path(path).resolve().relative_to(DATA_DIR).as_posix()


def abs_path(stored: str) -> Path:
    """Inverse of rel()."""
    return DATA_DIR / stored
