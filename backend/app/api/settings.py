"""User settings and read-only system information."""

from __future__ import annotations

import platform
import shutil
import sys
from importlib import metadata
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import func
from sqlmodel import Session, select

from .. import config
from ..db import get_session
from ..models import Photo
from ..settings import (
    DEFAULT_NEGATIVE_PROMPTS,
    DEFAULT_POSITIVE_PROMPTS,
    AppSettings,
    load_settings,
    save_settings,
)

router = APIRouter(tags=["settings"])


@router.get("/settings")
def get_settings(s: Session = Depends(get_session)) -> dict[str, Any]:
    return load_settings(s).model_dump()


@router.put("/settings")
def put_settings(patch: dict[str, Any], s: Session = Depends(get_session)) -> dict[str, Any]:
    before = load_settings(s).units
    try:
        saved = save_settings(s, patch)
    except ValidationError as e:
        raise HTTPException(422, e.errors()[0].get("msg", "invalid value")) from None
    if saved.units != before:
        from ..services.stats import set_weight_units

        set_weight_units(s, saved.units)
    return saved.model_dump()


@router.get("/settings/defaults")
def get_defaults() -> dict[str, Any]:
    d = AppSettings().model_dump()
    d["clip_positive_prompts"] = list(DEFAULT_POSITIVE_PROMPTS)
    d["clip_negative_prompts"] = list(DEFAULT_NEGATIVE_PROMPTS)
    return d


def _version(pkg: str) -> str | None:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def _dir_size(path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


@router.get("/system")
def system_info(s: Session = Depends(get_session)) -> dict[str, Any]:
    settings = load_settings(s)
    usage = shutil.disk_usage(config.DATA_DIR)
    models = {}
    for variant in ("lite", "full", "heavy"):
        p = config.pose_model_path(variant)
        models[f"pose_{variant}"] = {"path": str(p), "present": p.exists(), "bytes": p.stat().st_size if p.exists() else 0}
    from ..services import clip_backend
    from ..services.export_video import video_encoder

    flat = config.clip_weights_path()
    onnx_dir = config.clip_onnx_dir() if clip_backend.onnx_usable() else None
    clip_files = list(onnx_dir.iterdir()) if onnx_dir else [flat] if flat.exists() else []
    models["clip"] = {
        "path": str(onnx_dir or flat),
        "present": bool(clip_files),
        "bytes": sum(f.stat().st_size for f in clip_files),
        "name": f"{config.CLIP_MODEL_NAME} / {config.CLIP_PRETRAINED}",
    }
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe() + " (bundled)"
        except Exception:
            ffmpeg = None
    return {
        "data_dir": str(config.library_root()),
        "db_path": str(config.DB_PATH),
        "disk_free_gb": round(usage.free / 1e9, 1),
        "data_size_mb": round(_dir_size(config.library_root()) / 1e6, 1),
        "photos": s.exec(select(func.count(Photo.id))).one(),
        "models": models,
        "active_pose_model": settings.pose_model,
        "ffmpeg": ffmpeg,
        "acceleration": {"clip": clip_backend.accelerator(), "video": video_encoder(), "pose": "CPU (MediaPipe)"},
        "versions": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "fastapi": _version("fastapi"),
            "mediapipe": _version("mediapipe"),
            "torch": _version("torch"),
            "open_clip_torch": _version("open_clip_torch"),
            "onnxruntime": _version("onnxruntime") or _version("onnxruntime-directml") or _version("onnxruntime-gpu"),
            "opencv": _version("opencv-contrib-python"),
            "numpy": _version("numpy"),
            "pillow": _version("pillow"),
            "rembg": _version("rembg"),
        },
        "bind": f"{config.HOST}:{config.PORT}",
    }


@router.get("/system/models")
def models_status(s: Session = Depends(get_session)) -> dict[str, Any]:
    """Which model files are missing (the desktop app downloads them on first launch)."""
    from ..services import model_files as MF

    miss = MF.missing(load_settings(s).pose_model)
    return {"missing": [{"key": m.key, "label": m.label, "approx_mb": m.approx_mb} for m in miss], "missing_mb": sum(m.approx_mb for m in miss)}


@router.post("/system/models/download")
def models_download(s: Session = Depends(get_session)) -> dict[str, Any]:
    from ..jobs import job_to_dict, runner

    return job_to_dict(runner().enqueue("download_models", {"pose_variant": load_settings(s).pose_model}))
