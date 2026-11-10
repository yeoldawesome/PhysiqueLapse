"""Sleeves: does clothing cover the arms (shoulder to elbow and beyond)?

Used by the Viewer's "Hide sleeves" toggle (and exports / Library) so a time-lapse of an
arm pose only shows photos where the arm itself is visible.

Two CLIP zero-shot checks, combined by taking the lower "bare arms" probability:
- the upper arm of the arm being shown (the arm whose wrist is farther from the face; the
  other one usually holds the phone), cropped tight and turned upright, with everything
  outside a band around the arm painted gray so the shirt on the torso does not count;
  sleeves always cover the upper arm first, so this is the part that decides.
- the whole person, turned upright: tank top / shirtless vs. t-shirt / hoodie / long
  sleeves / jacket. This catches photos taken from far away, where the arm crop is tiny.

The user can override per photo (sleeves_source = "manual"), which detection never
overwrites.
"""

from __future__ import annotations

import logging

import numpy as np
from PIL import Image
from sqlmodel import select

from .. import config
from ..jobs import JobContext, job
from ..models import Photo, now
from ..settings import load_settings
from . import images
from . import landmarks as L
from .align import _xy

log = logging.getLogger(__name__)

SLEEVES_VERSION = 1
ARM_N = 224  # side of the upright upper-arm crop
ARM_BAND = 0.30  # half-width of the kept band, in upper-arm lengths

ARM_POSITIVE = ["bare skin of a man's upper arm", "a bare muscular bicep", "a bare arm", "skin"]
ARM_NEGATIVE = ["a t-shirt sleeve", "a long sleeve shirt sleeve", "a hoodie sleeve", "fabric", "a sweater sleeve"]
PERSON_POSITIVE = ["a man wearing a sleeveless tank top", "a shirtless man", "a man in a sleeveless shirt with bare arms"]
PERSON_NEGATIVE = [
    "a man wearing a t-shirt",
    "a man wearing a hoodie",
    "a man wearing a long sleeve shirt",
    "a man wearing a jacket",
    "a man wearing a sweater",
]
_PERSON_POINTS = ("nose", "left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip")


def shown_arm(pts: dict[str, dict]) -> str:
    """The arm being shown: wrist farther from the nose (the phone hand sits near the face)."""
    n = _xy(pts["nose"])
    dl = float(np.linalg.norm(_xy(pts["left_wrist"]) - n))
    dr = float(np.linalg.norm(_xy(pts["right_wrist"]) - n))
    return "left" if dl > dr else "right"


def arm_crop(img: Image.Image, pts: dict[str, dict], side: str) -> Image.Image | None:
    """Upper arm turned upright (shoulder at the top), outside a band around it painted gray."""
    import cv2

    sh, el = pts[f"{side}_shoulder"], pts[f"{side}_elbow"]
    if sh["visibility"] < 0.5 or el["visibility"] < 0.5:
        return None
    a, b = _xy(sh), _xy(el)
    ua = float(np.linalg.norm(b - a))
    if ua < 15:
        return None
    N = ARM_N
    dst_a, dst_b = np.array([N / 2, 0.08 * N]), np.array([N / 2, 0.92 * N])
    ds, dd = b - a, dst_b - dst_a
    sc = float(np.linalg.norm(dd) / np.linalg.norm(ds))
    ang = float(np.arctan2(dd[1], dd[0]) - np.arctan2(ds[1], ds[0]))
    c, s_ = sc * np.cos(ang), sc * np.sin(ang)
    M = np.array([[c, -s_, 0.0], [s_, c, 0.0]], dtype=np.float32)
    M[:, 2] = dst_a - M[:, :2] @ a
    arr = cv2.warpAffine(np.asarray(img.convert("RGB")), M, (N, N), flags=cv2.INTER_AREA, borderValue=(128, 128, 128)).copy()
    half = int(ARM_BAND * 0.84 * N)
    arr[:, : N // 2 - half] = 128
    arr[:, N // 2 + half :] = 128
    return Image.fromarray(arr)


def person_crop(img: Image.Image, pts: dict[str, dict]) -> Image.Image | None:
    """The whole person, rotated upright about the hips when the photo is tilted."""
    P = np.array([_xy(pts[n]) for n in _PERSON_POINTS if pts[n]["visibility"] >= 0.3])
    if len(P) < 4:
        return None
    sm = (_xy(pts["left_shoulder"]) + _xy(pts["right_shoulder"])) / 2
    hm = (_xy(pts["left_hip"]) + _xy(pts["right_hip"])) / 2
    v = sm - hm
    ang = float(np.degrees(np.arctan2(v[0], -v[1])))  # 0 when upright
    src = img
    if abs(ang) > 10:
        src = img.convert("RGB").rotate(ang, resample=Image.Resampling.BICUBIC, center=(float(hm[0]), float(hm[1])), fillcolor=(128, 128, 128))
        t = np.radians(ang)
        R = np.array([[np.cos(t), np.sin(t)], [-np.sin(t), np.cos(t)]])
        P = (P - hm) @ R.T + hm
    x0, y0 = P.min(0)
    x1, y1 = P.max(0)
    pad = 0.15 * max(x1 - x0, y1 - y0)
    box = (int(max(0, x0 - pad)), int(max(0, y0 - pad)), int(min(src.width, x1 + pad)), int(min(src.height, y1 + pad)))
    if box[2] - box[0] < 10 or box[3] - box[1] < 10:
        return None
    return src.crop(box).copy()


def crops_for(img: Image.Image, pts: dict[str, dict]) -> tuple[Image.Image | None, Image.Image | None]:
    side = shown_arm(pts)
    other = "right" if side == "left" else "left"
    arm = arm_crop(img, pts, side)
    if arm is None:
        arm = arm_crop(img, pts, other)
    person = person_crop(img, pts)
    return arm, (images.resize_long_side(person, 448) if person is not None else None)


@job("detect_sleeves", lane="cv", title="Check for sleeves")
def job_detect_sleeves(ctx: JobContext, params: dict) -> dict:
    force = bool(params.get("force"))
    ids = params.get("ids")
    with ctx.session() as s:
        stmt = select(Photo).where(Photo.landmarks_status == "ok")
        if ids:
            stmt = stmt.where(Photo.id.in_(ids))
        elif not force:
            stmt = stmt.where((Photo.sleeves_score.is_(None)) | (Photo.sleeves_version != SLEEVES_VERSION))
        todo = [p.id for p in s.exec(stmt.order_by(Photo.id)).all()]
    total = len(todo)
    if not total:
        return {"message": "All photos already checked for sleeves", "checked": 0}
    from .clip_backend import get_scorer

    ctx.progress(0, total, "Loading image model")
    scorer = get_scorer()
    found = 0
    for i in range(0, total, 16):
        ctx.check_cancelled()
        batch = todo[i : i + 16]
        with ctx.session() as s:
            threshold = load_settings(s).sleeves_threshold
            arms, persons, photos = [], [], []
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
                    arm, person = crops_for(img, pts)
                finally:
                    img.close()
                photos.append(p)
                arms.append(arm)
                persons.append(person)
            arm_imgs = [a for a in arms if a is not None]
            person_imgs = [x for x in persons if x is not None]
            arm_sc = iter(scorer.positive_probability(arm_imgs, ARM_POSITIVE, ARM_NEGATIVE) if arm_imgs else [])
            person_sc = iter(scorer.positive_probability(person_imgs, PERSON_POSITIVE, PERSON_NEGATIVE) if person_imgs else [])
            for p, a, x in zip(photos, arms, persons, strict=True):
                scores = []
                if a is not None:
                    scores.append(float(next(arm_sc)))
                if x is not None:
                    scores.append(float(next(person_sc)))
                bare = min(scores) if scores else 1.0  # nothing to look at: do not hide the photo
                p.sleeves_score = round(bare, 4)
                p.sleeves_version = SLEEVES_VERSION
                if p.sleeves_source != "manual":
                    p.sleeves = bare < threshold
                    p.sleeves_source = "auto"
                found += int(bool(p.sleeves))
                p.updated_at = now()
                s.add(p)
            s.commit()
        ctx.progress(min(total, i + 16), total, f"Checked {min(total, i + 16)}/{total} · {found} with sleeves")
    return {"message": f"Checked {total} photos: {found} have sleeves covering the arms", "checked": total, "found": found}
