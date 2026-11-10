"""Abs group: which photos show visible abs, plus a torso close-up per photo aligned for an
abs time-lapse across all pose types.

Detection: CLIP zero-shot on a crop of the torso (shoulders to just below the hips)
- bare stomach with visible abs vs. a covered torso. The user can override per photo
(abs_source = "manual"), which detection never overwrites.

Close-up: a rigid move (scale + rotation + shift, never a warp) that puts the shoulder
midpoint and hip midpoint at fixed spots on a square canvas, so the abdomen sits in the
same place and at the same scale (relative to torso length) in every frame.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from PIL import Image
from sqlmodel import Session, select

from .. import config
from ..jobs import JobContext, job
from ..models import Photo, now
from ..settings import load_settings
from . import images
from . import landmarks as L
from .align import BG_RGB, _xy, solve_two_point

log = logging.getLogger(__name__)

ABS_VERSION = 1
CANVAS = 1080  # square close-up
SHOULDER_Y, HIP_Y = 0.10, 0.86  # where shoulder-mid and hip-mid land (fraction of the canvas)

POSITIVE = [
    "a shirtless man's bare stomach showing his abs",
    "a six pack of abdominal muscles",
    "a bare male torso with visible abs",
    "a shirtless male torso",
]
NEGATIVE = [
    "a man wearing a tank top",
    "a man wearing a t-shirt",
    "a torso covered by a shirt",
    "clothing",
    "a hoodie or jacket",
]


def abs_path(photo_id: int):
    return config.DERIVED_DIR / "abs" / f"{photo_id}.jpg"


def torso_crop(img: Image.Image, pts: dict[str, dict]) -> Image.Image | None:
    """Axis-aligned crop around the torso (for scoring)."""
    need = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
    if any(pts[n]["visibility"] < 0.5 for n in need):
        return None
    sm = (_xy(pts["left_shoulder"]) + _xy(pts["right_shoulder"])) / 2
    hm = (_xy(pts["left_hip"]) + _xy(pts["right_hip"])) / 2
    T = float(np.linalg.norm(sm - hm))
    if T < 20:
        return None
    c = (sm + hm) / 2
    half = 0.7 * T
    box = (int(max(0, c[0] - half)), int(max(0, sm[1] - 0.1 * T)), int(min(img.width, c[0] + half)), int(min(img.height, hm[1] + 0.2 * T)))
    if box[2] - box[0] < 10 or box[3] - box[1] < 10:
        return None
    return img.crop(box)


def closeup_transform(pts: dict[str, dict]):
    sm = (_xy(pts["left_shoulder"]) + _xy(pts["right_shoulder"])) / 2
    hm = (_xy(pts["left_hip"]) + _xy(pts["right_hip"])) / 2
    return solve_two_point(sm, hm, np.array([CANVAS / 2, SHOULDER_Y * CANVAS]), np.array([CANVAS / 2, HIP_Y * CANVAS]))


def render_closeup(photo: Photo, pts: dict[str, dict]) -> str:
    import cv2

    img, _ = images.open_image(config.abs_path(photo.original_path))
    try:
        rgb = np.asarray(img)
    finally:
        img.close()
    if photo.flip_horizontal:
        rgb = np.ascontiguousarray(rgb[:, ::-1])
    tr = closeup_transform(pts)
    M = tr.matrix().copy()
    if tr.scale < 0.95:  # pre-shrink to avoid aliasing (same as the main alignment)
        f = max(tr.scale, 1e-3) * 1.05
        small = cv2.resize(rgb, (max(1, round(rgb.shape[1] * f)), max(1, round(rgb.shape[0] * f))), interpolation=cv2.INTER_AREA)
        M[:, 0] /= small.shape[1] / rgb.shape[1]
        M[:, 1] /= small.shape[0] / rgb.shape[0]
        rgb = small
    out = cv2.warpAffine(rgb, M, (CANVAS, CANVAS), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=BG_RGB)
    p = abs_path(photo.id)
    images.save_jpeg(Image.fromarray(out), p, quality=90)
    return config.rel(p)


@job("detect_abs", lane="cv", title="Find visible abs")
def job_detect_abs(ctx: JobContext, params: dict) -> dict:
    force = bool(params.get("force"))
    ids = params.get("ids")
    with ctx.session() as s:
        settings = load_settings(s)
        stmt = select(Photo).where(Photo.landmarks_status == "ok")
        if ids:
            stmt = stmt.where(Photo.id.in_(ids))
        elif not force:
            stmt = stmt.where((Photo.abs_score.is_(None)) | (Photo.abs_version != ABS_VERSION))
        todo = [p.id for p in s.exec(stmt.order_by(Photo.id)).all()]
    total = len(todo)
    if not total:
        return {"message": "All photos already checked for abs", "checked": 0}
    from .clip_backend import get_scorer

    ctx.progress(0, total, "Loading image model")
    scorer = get_scorer()
    found = 0
    for i in range(0, total, 16):
        ctx.check_cancelled()
        batch = todo[i : i + 16]
        with ctx.session() as s:
            crops, photos, pts_list = [], [], []
            for pid in batch:
                p = s.get(Photo, pid)
                if p is None:  # deleted while the job was running
                    continue
                data = L.load_for_photo(p)
                if not data or not data.get("detected"):
                    continue
                pts = L.points_dict(data)
                img, _ = images.open_image(config.abs_path(p.original_path))
                try:
                    if p.flip_horizontal:
                        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                    crop = torso_crop(img, pts)
                    crop = crop.copy() if crop is not None else None
                finally:
                    img.close()
                if crop is None:
                    p.abs_score, p.abs_version = 0.0, ABS_VERSION
                    if p.abs_source != "manual":
                        p.abs_visible = False
                    s.add(p)
                    continue
                crops.append(images.resize_long_side(crop, 448))
                photos.append(p)
                pts_list.append(pts)
            scores = scorer.positive_probability(crops, POSITIVE, NEGATIVE) if crops else []
            for p, pts, sc in zip(photos, pts_list, scores, strict=True):
                p.abs_score = round(float(sc), 4)
                p.abs_version = ABS_VERSION
                if p.abs_source != "manual":
                    p.abs_visible = sc >= settings.abs_threshold
                    p.abs_source = "auto"
                if p.abs_visible:
                    found += 1
                    try:
                        render_closeup(p, pts)
                    except Exception as e:
                        log.warning("abs close-up failed for %s: %s", p.id, e)
                p.updated_at = now()
                s.add(p)
            s.commit()
        ctx.progress(min(total, i + 16), total, f"Checked {min(total, i + 16)}/{total} · {found} with visible abs")
    return {"message": f"Checked {total} photos: {found} show visible abs", "checked": total, "found": found}


def ensure_closeup(s: Session, p: Photo) -> bool:
    """Render the close-up on demand (manual 'abs visible' or a flip change)."""
    data = L.load_for_photo(p)
    if not data or not data.get("detected"):
        return False
    pts = L.points_dict(data)
    if any(pts[n]["visibility"] < 0.5 for n in ("left_shoulder", "right_shoulder", "left_hip", "right_hip")):
        return False
    render_closeup(p, pts)
    return True


def sequence(s: Session, date_from=None, date_to=None) -> dict[str, Any]:
    from datetime import datetime, timedelta

    stmt = select(Photo).where(Photo.abs_visible == True, Photo.excluded == False, Photo.taken_at.is_not(None))  # noqa: E712
    if date_from:
        stmt = stmt.where(Photo.taken_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        stmt = stmt.where(Photo.taken_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    photos = list(s.exec(stmt.order_by(Photo.taken_at, Photo.id)).all())
    first = photos[0].taken_at if photos else None
    settings = load_settings(s)
    from .stats import stats_for_dates

    st = stats_for_dates(s, [p.taken_at.date() for p in photos], settings.stats_window_days, first.date() if first else None)
    frames, missing = [], 0
    for p, stt in zip(photos, st, strict=True):
        path = abs_path(p.id)
        if not path.exists() and ensure_closeup(s, p) is False:
            missing += 1
        v = int(path.stat().st_mtime) if path.exists() else 0
        frames.append(
            {
                "id": p.id,
                "taken_at": p.taken_at.isoformat(timespec="seconds"),
                "days_since_start": (p.taken_at.date() - first.date()).days,
                "aligned_url": f"/api/photos/{p.id}/abs?v={v}" if path.exists() else None,
                "preview_url": f"/api/photos/{p.id}/preview",
                "flags": {},
                "method": "abs",
                "stale": False,
                "flip_horizontal": p.flip_horizontal,
                "stats": stt,
                "measurements": {},
                "pose_diff_pct": None,
                "abs_score": p.abs_score,
                "sleeves": p.sleeves,
            }
        )
    no_date = s.exec(select(Photo.id).where(Photo.abs_visible == True, Photo.taken_at.is_(None))).all()  # noqa: E712
    return {
        "pose_category_id": -1,
        "group": "abs",
        "canvas_w": CANVAS,
        "canvas_h": CANVAS,
        "reference_photo_id": None,
        "frames": frames,
        "unaligned": missing,
        "without_date": len(no_date),
    }
