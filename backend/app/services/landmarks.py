"""Landmark extraction job, on-disk cache, and the flip transform.

Cache file: data/derived/landmarks/{photo_id}.json, for the EXIF-corrected,
un-flipped image in original pixel coordinates:
    {model, image_w, image_h, detected, num_people, points: [{name, x_px, y_px, z, visibility, presence}] x 33}
The per-photo flip is applied on read (apply_flip), so toggling it never
re-runs the model.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
from sqlmodel import select

from .. import config
from ..jobs import JobContext, job
from ..models import Alignment, Photo, PoseFeatures, now
from ..settings import load_settings
from . import images
from .pose_backend import LANDMARK_NAMES, MIRROR_PERM, Point, get_backend, mirror_name

log = logging.getLogger(__name__)

VIS = 0.5  # a landmark counts as visible at or above this
QUALITY_POINTS = ("nose", "left_shoulder", "right_shoulder", "left_hip", "right_hip")


# ----------------------------------------------------------------------------- flip


def apply_flip(data: dict[str, Any], flip: bool) -> dict[str, Any]:
    """Mirror x and swap left/right labels. Returns a new dict; input untouched."""
    if not flip or not data.get("points"):
        return dict(data)
    w = float(data["image_w"])
    pts = data["points"]
    by_name = {p["name"]: p for p in pts}
    out = []
    for p in pts:
        src = by_name.get(mirror_name(p["name"]), p)
        q = dict(src)
        q["name"] = p["name"]
        q["x_px"] = round(w - src["x_px"], 2)
        out.append(q)
    d = dict(data)
    d["points"] = out
    return d


def points_dict(data: dict[str, Any]) -> dict[str, dict]:
    return {p["name"]: p for p in data.get("points", [])}


def quality(data: dict[str, Any]) -> float:
    pts = points_dict(data)
    if not pts:
        return 0.0
    return float(np.mean([pts[n]["visibility"] for n in QUALITY_POINTS if n in pts]))


# ----------------------------------------------------------------------------- cache io


def cache_path(photo_id: int):
    return config.LANDMARKS_DIR / f"{photo_id}.json"


def load_raw(photo_id: int) -> dict[str, Any] | None:
    p = cache_path(photo_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def load_for_photo(photo: Photo) -> dict[str, Any] | None:
    """Landmarks as displayed (flip applied), or None."""
    raw = load_raw(photo.id)
    if raw is None:
        return None
    return apply_flip(raw, photo.flip_horizontal)


def save_raw(photo_id: int, data: dict[str, Any]) -> None:
    p = cache_path(photo_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    tmp.replace(p)


# ----------------------------------------------------------------------------- detection


def _mirror_pose(pts: list[Point], w: float) -> list[Point]:
    """Pose detected on a mirrored image -> pose in original image coordinates."""
    out = []
    for i in range(len(LANDMARK_NAMES)):
        src = pts[MIRROR_PERM[i]]
        out.append(Point(LANDMARK_NAMES[i], w - src.x_px, src.y_px, src.z, src.visibility, src.presence))
    return out


def _largest(poses: list[list[Point]]) -> list[Point]:
    def area(p: list[Point]) -> float:
        xs = [q.x_px for q in p]
        ys = [q.y_px for q in p]
        return (max(xs) - min(xs)) * (max(ys) - min(ys))

    return max(poses, key=area)


def detect_image(rgb: np.ndarray, *, variant: str, tta: bool, num_poses: int = 1) -> tuple[list[Point] | None, int, np.ndarray | None]:
    """Return (pose in rgb pixel coords or None, number of people seen, body-outline mask or None)."""
    be = get_backend(variant)
    with_masks = getattr(be, "detect_with_masks", None)
    if with_masks is not None:
        poses, masks = with_masks(rgb, num_poses=num_poses)
    else:
        poses, masks = be.detect(rgb, num_poses=num_poses), []
    if not poses:
        return None, 0, None
    main = _largest(poses)
    i = next(k for k, p in enumerate(poses) if p is main)
    mask = masks[i] if i < len(masks) else None
    if tta:
        w = rgb.shape[1]
        mposes = be.detect(np.ascontiguousarray(rgb[:, ::-1]), num_poses=1)
        if mposes:
            m = _mirror_pose(mposes[0], w)
            main = [
                Point(
                    a.name,
                    (a.x_px + b.x_px) / 2,
                    (a.y_px + b.y_px) / 2,
                    (a.z + b.z) / 2,
                    (a.visibility + b.visibility) / 2,
                    (a.presence + b.presence) / 2,
                )
                for a, b in zip(main, m, strict=True)
            ]
    return main, len(poses), mask


def extract_for_photo(photo: Photo, *, variant: str, long_side: int, tta: bool) -> dict[str, Any]:
    src = config.abs_path(photo.original_path)
    img, _ = images.open_image(src)
    try:
        W, H = img.size
        small = images.resize_long_side(img, long_side)
        sx, sy = W / small.width, H / small.height
        rgb = np.asarray(small)
        pose, n, mask = detect_image(rgb, variant=variant, tta=tta)
    finally:
        img.close()
    if mask is not None:
        from .measure import save_mask

        save_mask(photo.id, mask)
    data: dict[str, Any] = {"model": f"mediapipe-pose-{variant}", "image_w": W, "image_h": H, "detected": False, "num_people": n, "points": []}
    if pose is not None:
        data["detected"] = True
        for p in pose:
            p.x_px *= sx
            p.y_px *= sy
        data["points"] = [p.as_dict() for p in pose]
    return data


@job("extract_landmarks", lane="cv", title="Extract landmarks")
def job_extract_landmarks(ctx: JobContext, params: dict) -> dict:
    ids = params.get("ids")
    force = bool(params.get("force"))
    with ctx.session() as s:
        settings = load_settings(s)
        stmt = select(Photo.id)
        if ids:
            stmt = stmt.where(Photo.id.in_(ids))
            if not force:
                stmt = stmt.where(Photo.landmarks_status != "ok")
        elif force:
            pass
        else:
            stmt = stmt.where(Photo.landmarks_status.in_(("pending", "failed")))
        todo = list(s.exec(stmt.order_by(Photo.id)).all())
    total = len(todo)
    if total == 0:
        return {"message": "All photos already have landmarks", "processed": 0}
    ctx.progress(0, total, "Loading pose model")
    get_backend(settings.pose_model)  # raises ModelMissing early with a clear message
    counts = {"ok": 0, "none": 0, "failed": 0}
    for i, pid in enumerate(todo, 1):
        ctx.check_cancelled()
        with ctx.session() as s:
            photo = s.get(Photo, pid)
            if photo is None:
                continue
            try:
                data = extract_for_photo(
                    photo, variant=settings.pose_model, long_side=settings.landmark_input_long_side, tta=settings.flip_tta
                )
                save_raw(pid, data)
                photo.landmarks_status = "ok" if data["detected"] else "none"
                photo.landmarks_quality = round(quality(data), 4) if data["detected"] else 0.0
                photo.landmarks_path = config.rel(cache_path(pid))
            except Exception as e:
                log.warning("landmarks failed for photo %s: %s", pid, e)
                photo.landmarks_status = "failed"
                photo.landmarks_quality = None
            counts[photo.landmarks_status] += 1
            photo.updated_at = now()
            feats = s.get(PoseFeatures, pid)
            if feats is not None:
                s.delete(feats)
            if photo.landmarks_status == "ok":
                try:
                    from .measure import compute_for_photo

                    compute_for_photo(s, photo, redetect=False)
                except Exception as e:  # measurements are a bonus; never fail landmarking for them
                    log.warning("measure failed for photo %s: %s", pid, e)
            a = s.get(Alignment, pid)
            if a is not None and a.method == "auto":
                a.stale = True
                s.add(a)
            s.add(photo)
            s.commit()
        ctx.progress(i, total, f"{i}/{total} · {counts['ok']} detected, {counts['none']} no person, {counts['failed']} failed")
    msg = f"Landmarks: {counts['ok']} detected, {counts['none']} without a person"
    if counts["failed"]:
        msg += f", {counts['failed']} failed"
    return {"message": msg, "counts": counts, "next": "classify_poses"}
