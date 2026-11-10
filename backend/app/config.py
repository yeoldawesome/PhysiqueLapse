"""Paths and process-wide constants.

Everything the app writes lives under DATA_DIR (default: <project>/data). Set the
DATA_DIR environment variable to relocate it. Paths stored in the database are
relative to DATA_DIR so the folder can be moved or backed up by copying it.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

DATA_DIR = Path(os.environ.get("DATA_DIR") or (PROJECT_ROOT / "data")).resolve()
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

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))

THUMB_LONG_SIDE = 400
PREVIEW_LONG_SIDE = 1280

POSE_MODEL_URLS = {
    "lite": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
    "full": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task",
    "heavy": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task",
}
CLIP_MODEL_NAME = "ViT-B-32"
CLIP_PRETRAINED = "laion2b_s34b_b79k"


def clip_weights_path() -> Path:
    """Flat copy of the CLIP weights (the Hugging Face cache nests deep enough to hit
    Windows' 260-character path limit when the data folder itself is deep)."""
    return MODELS_DIR / "clip_vit_b32_laion2b.safetensors"


def pose_model_path(variant: str = "heavy") -> Path:
    return MODELS_DIR / f"pose_landmarker_{variant}.task"


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
