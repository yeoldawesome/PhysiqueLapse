"""The model files the app needs, and downloading them (scripts/download_models.py from a
source checkout, or the background job the desktop app runs on first launch).

These downloads are the only network requests the app ever makes. Each is a plain HTTPS GET
of a public file; nothing about the user or their photos is sent.
"""

from __future__ import annotations

import logging
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import config
from ..fsutil import replace_retry
from ..jobs import JobContext, job

log = logging.getLogger(__name__)

# the CLIP weights as published by LAION (the same file open_clip fetches for this model)
CLIP_URL = "https://huggingface.co/laion/CLIP-ViT-B-32-laion2B-s34B-b79K/resolve/main/open_clip_model.safetensors"


@dataclass
class ModelFile:
    key: str
    label: str
    url: str
    path: Path
    approx_mb: int


def required(pose_variant: str = "heavy") -> list[ModelFile]:
    files = [
        ModelFile(f"pose_{pose_variant}", f"pose model ({pose_variant})", config.POSE_MODEL_URLS[pose_variant], config.pose_model_path(pose_variant), 31),
        ModelFile("face_detector", "face detector", config.FACE_MODEL_URLS["detector"], config.face_model_path("detector"), 1),
        ModelFile("face_recognizer", "face recognizer", config.FACE_MODEL_URLS["recognizer"], config.face_model_path("recognizer"), 37),
        ModelFile("clip", "image model (CLIP)", CLIP_URL, config.clip_weights_path(), 577),
    ]
    if pose_variant != "lite":
        # small pose model the Capture page runs in the browser (served by this app, never fetched
        # by the browser from Google)
        files.append(ModelFile("pose_lite", "live pose model (browser)", config.POSE_MODEL_URLS["lite"], config.pose_model_path("lite"), 6))
    return files


def present(m: ModelFile) -> bool:
    if m.key == "clip":
        from .clip_backend import local_weights  # also accepts a Hugging Face cache copy

        return local_weights() is not None
    return m.path.is_file() and m.path.stat().st_size > 0


def missing(pose_variant: str = "heavy") -> list[ModelFile]:
    return [m for m in required(pose_variant) if not present(m)]


def download(url: str, dest: Path, on_progress: Callable[[int, int], None] | None = None) -> None:
    """Download to dest via a .part file (an interrupted download never looks complete)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "physiquelapse"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            got += len(b)
            if on_progress:
                on_progress(got, total)
    if tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise OSError(f"empty download from {url}")
    replace_retry(tmp, dest)


@job("download_models", lane="io", title="Download models (first run)")
def job_download_models(ctx: JobContext, params: dict) -> dict:
    todo = missing(params.get("pose_variant") or "heavy")
    if not todo:
        return {"message": "All models present", "downloaded": 0}
    total_mb = sum(m.approx_mb for m in todo)
    done_mb = 0.0
    for m in todo:
        ctx.check_cancelled()
        base = done_mb

        def progress(got: int, total: int, m=m, base=base) -> None:
            mb = got / 1e6
            ctx.progress(int(base + mb), max(total_mb, int(base + (total / 1e6 if total else mb))), f"Downloading {m.label}: {mb:.0f} / {total / 1e6 if total else m.approx_mb:.0f} MB")

        log.info("downloading %s from %s", m.label, m.url)
        download(m.url, m.path, progress)
        done_mb = base + m.path.stat().st_size / 1e6
    return {"message": f"Downloaded {len(todo)} model files ({done_mb:.0f} MB)", "downloaded": len(todo)}
