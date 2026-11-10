"""Face group: a face close-up per photo, aligned so the eyes and mouth land in the same
spots, for a face time-lapse across every pose type. Like the Abs group it is an attribute,
not a pose category: a photo keeps its pose group and can also be in Face.

Close-up: one rigid move (scale + rotation + shift, never a warp) that puts the midpoint of
the eyes and the midpoint of the mouth at fixed spots on a square canvas. Using eyes->mouth
(a vertical pair) instead of the eye distance keeps the scale steady when the head is turned.

Visible check: in mirror selfies the phone is often in front of the face. CLIP zero-shot on
two crops of the aligned close-up, the face centre (face vs. phone / flash) and the mouth and
chin (mouth vs. phone / fingers); the lower probability wins. The user can override per photo
(face_source = "manual"), which detection never overwrites.
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

FACE_VERSION = 1
CANVAS = 720
EYE_Y, MOUTH_Y = 0.42, 0.62  # where eye-mid and mouth-mid land (fraction of the canvas)
FACE_POINTS = ("left_eye", "right_eye", "nose", "mouth_left", "mouth_right")
MIN_EYE_MOUTH_PX = 12  # smaller faces would be mostly upscaling blur

# crops of the aligned canvas, as fractions (left, top, right, bottom)
CENTER_BOX = (0.293, 0.313, 0.707, 0.727)
MOUTH_BOX = (0.324, 0.488, 0.676, 0.781)
CENTER_POSITIVE = ["a man's face", "a person's eyes, nose and mouth", "a close-up of a face"]
CENTER_NEGATIVE = ["a black smartphone", "a phone", "a hand holding a phone", "a bright camera flash", "a blurry dark photo"]
MOUTH_POSITIVE = ["a man's mouth and chin", "lips and a mustache", "the lower half of a face"]
MOUTH_NEGATIVE = ["a black smartphone", "a phone case", "a hand holding a phone", "fingers", "a camera flash"]


def face_path(photo_id: int):
    return config.DERIVED_DIR / "face" / f"{photo_id}.jpg"


def face_points_ok(pts: dict[str, dict]) -> bool:
    if any(pts[n]["visibility"] < 0.5 for n in FACE_POINTS):
        return False
    return float(np.linalg.norm(_mouth_mid(pts) - _eye_mid(pts))) >= MIN_EYE_MOUTH_PX


def _eye_mid(pts):
    return (_xy(pts["left_eye"]) + _xy(pts["right_eye"])) / 2


def _mouth_mid(pts):
    return (_xy(pts["mouth_left"]) + _xy(pts["mouth_right"])) / 2


def closeup_transform(pts: dict[str, dict]):
    return solve_two_point(
        _eye_mid(pts), _mouth_mid(pts), np.array([CANVAS / 2, EYE_Y * CANVAS]), np.array([CANVAS / 2, MOUTH_Y * CANVAS])
    )


def render_closeup(photo: Photo, pts: dict[str, dict]) -> Image.Image:
    """Render, save and return the aligned face close-up (flip applied like everywhere else)."""
    import cv2

    img, _ = images.open_image(config.abs_path(photo.original_path))
    try:
        rgb = np.asarray(img.convert("RGB"))
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
    out = Image.fromarray(
        cv2.warpAffine(rgb, M, (CANVAS, CANVAS), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=BG_RGB)
    )
    images.save_jpeg(out, face_path(photo.id), quality=90)
    return out


def _box(im: Image.Image, frac) -> Image.Image:
    w, h = im.size
    return im.crop((round(frac[0] * w), round(frac[1] * h), round(frac[2] * w), round(frac[3] * h)))


def ensure_closeup(s: Session, p: Photo) -> bool:
    """Render the close-up on demand (manual 'face visible' or a flip change)."""
    data = L.load_for_photo(p)
    if not data or not data.get("detected"):
        return False
    pts = L.points_dict(data)
    if not face_points_ok(pts):
        return False
    render_closeup(p, pts)
    return True


@job("detect_faces", lane="cv", title="Find faces")
def job_detect_faces(ctx: JobContext, params: dict) -> dict:
    force = bool(params.get("force"))
    ids = params.get("ids")
    with ctx.session() as s:
        stmt = select(Photo).where(Photo.landmarks_status == "ok")
        if ids:
            stmt = stmt.where(Photo.id.in_(ids))
        elif not force:
            stmt = stmt.where((Photo.face_score.is_(None)) | (Photo.face_version != FACE_VERSION))
        todo = [p.id for p in s.exec(stmt.order_by(Photo.id)).all()]
    total = len(todo)
    if not total:
        return {"message": "All photos already checked for faces", "checked": 0}
    from .clip_backend import get_scorer

    ctx.progress(0, total, "Loading image model")
    scorer = get_scorer()
    found = 0
    for i in range(0, total, 16):
        ctx.check_cancelled()
        with ctx.session() as s:
            threshold = load_settings(s).face_threshold
            photos, centers, mouths = [], [], []
            for pid in todo[i : i + 16]:
                p = s.get(Photo, pid)
                if p is None:  # deleted while the job was running
                    continue
                data = L.load_for_photo(p)
                pts = L.points_dict(data) if data and data.get("detected") else None
                if pts is None or not face_points_ok(pts):
                    p.face_score, p.face_version = 0.0, FACE_VERSION
                    if p.face_source != "manual":
                        p.face_visible, p.face_source = False, "auto"
                    s.add(p)
                    continue
                try:
                    im = render_closeup(p, pts)
                except Exception as e:
                    log.warning("face close-up failed for %s: %s", p.id, e)
                    continue
                photos.append(p)
                centers.append(_box(im, CENTER_BOX))
                mouths.append(_box(im, MOUTH_BOX))
            if photos:
                c_sc = scorer.positive_probability(centers, CENTER_POSITIVE, CENTER_NEGATIVE)
                m_sc = scorer.positive_probability(mouths, MOUTH_POSITIVE, MOUTH_NEGATIVE)
                for p, a, b in zip(photos, c_sc, m_sc, strict=True):
                    sc = min(float(a), float(b))
                    p.face_score, p.face_version = round(sc, 4), FACE_VERSION
                    if p.face_source != "manual":
                        p.face_visible, p.face_source = sc >= threshold, "auto"
                    found += int(bool(p.face_visible))
                    p.updated_at = now()
                    s.add(p)
            s.commit()
        done = min(total, i + 16)
        ctx.progress(done, total, f"Checked {done}/{total} · {found} faces visible")
    return {"message": f"Checked {total} photos: {found} show the face", "checked": total, "found": found}


def sequence(s: Session, date_from=None, date_to=None) -> dict[str, Any]:
    from datetime import datetime, timedelta

    stmt = select(Photo).where(Photo.face_visible == True, Photo.excluded == False, Photo.taken_at.is_not(None))  # noqa: E712
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
        path = face_path(p.id)
        if not path.exists() and ensure_closeup(s, p) is False:
            missing += 1
        v = int(path.stat().st_mtime) if path.exists() else 0
        frames.append(
            {
                "id": p.id,
                "taken_at": p.taken_at.isoformat(timespec="seconds"),
                "days_since_start": (p.taken_at.date() - first.date()).days,
                "aligned_url": f"/api/photos/{p.id}/face?v={v}" if path.exists() else None,
                "preview_url": f"/api/photos/{p.id}/preview",
                "flags": {},
                "method": "face",
                "stale": False,
                "flip_horizontal": p.flip_horizontal,
                "stats": stt,
                "measurements": {},
                "pose_diff_pct": None,
                "face_score": p.face_score,
                "sleeves": p.sleeves,
            }
        )
    no_date = s.exec(select(Photo.id).where(Photo.face_visible == True, Photo.taken_at.is_(None))).all()  # noqa: E712
    return {
        "pose_category_id": -2,
        "group": "face",
        "canvas_w": CANVAS,
        "canvas_h": CANVAS,
        "reference_photo_id": None,
        "frames": frames,
        "unaligned": missing,
        "without_date": len(no_date),
    }
