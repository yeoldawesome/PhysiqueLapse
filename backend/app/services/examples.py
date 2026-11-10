"""Pose examples: the images the classifier learns each pose from.

Two sources, both treated the same:
- library photos whose pose you set or accepted by hand (PoseLabel), and
- images you uploaded for a pose on the Settings page (PoseExample), kept in
  data/examples/ outside the library, so they can be anyone's photos.

Left/right pairs are mirrors of each other, so every example also counts, mirrored,
for its sibling pose (an example for "Side (Arm 90) - Left" teaches "... - Right" too).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from PIL import Image
from sqlmodel import Session, select

from .. import config
from ..models import PoseCategory, PoseExample, PoseFeatures, PoseLabel
from . import features as F
from . import images
from . import landmarks as L

log = logging.getLogger(__name__)

THUMB_LONG_SIDE = 400
IMAGE_LONG_SIDE = 1600


@dataclass
class Example:
    category_id: int
    vector: list[float]
    source: str  # photo | upload | mirror
    ref_id: int  # photo id or example id


def example_path(example_id: int):
    return config.EXAMPLES_DIR / f"{example_id}.jpg"


def thumb_path(example_id: int):
    return config.EXAMPLES_DIR / f"{example_id}.thumb.jpg"


def features_for_image(img: Image.Image, *, variant: str, long_side: int, tta: bool) -> list[float] | None:
    """Landmarks -> feature vector for an image that is not in the library (no caching)."""
    small = images.resize_long_side(img, long_side)
    pose, _, _ = L.detect_image(np.asarray(small.convert("RGB")), variant=variant, tta=tta)
    if pose is None:
        return None
    data = {"detected": True, "image_w": small.width, "image_h": small.height, "points": [p.as_dict() for p in pose]}
    return F.compute(data)


def add_upload(s: Session, category: PoseCategory, filename: str, raw: bytes, *, variant: str, long_side: int, tta: bool) -> PoseExample:
    """Store an uploaded example image and its pose features. Raises ValueError when no person is found."""
    import io

    img, _ = images.open_image(io.BytesIO(raw))
    try:
        rgb = img.convert("RGB")
    finally:
        img.close()
    vec = features_for_image(rgb, variant=variant, long_side=long_side, tta=tta)
    if vec is None:
        raise ValueError(f"no person found in {filename}")
    ex = PoseExample(pose_category_id=category.id, original_filename=filename, vector=vec, feature_version=F.FEATURE_VERSION)
    s.add(ex)
    s.commit()
    s.refresh(ex)
    images.save_jpeg(images.resize_long_side(rgb, IMAGE_LONG_SIDE), example_path(ex.id), quality=90)
    images.save_jpeg(images.resize_long_side(rgb, THUMB_LONG_SIDE), thumb_path(ex.id), quality=85)
    return ex


def delete_example(s: Session, ex: PoseExample) -> None:
    for p in (example_path(ex.id), thumb_path(ex.id)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    s.delete(ex)
    s.commit()


def sibling_map(s: Session) -> dict[int, int]:
    """category id -> id of its mirror-image pose (left <-> right)."""
    from .classify import SIDE_PAIRS

    by_key = {c.rule_key: c.id for c in s.exec(select(PoseCategory)).all() if c.rule_key}
    out: dict[int, int] = {}
    for a, b in SIDE_PAIRS:
        if a in by_key and b in by_key:
            out[by_key[a]], out[by_key[b]] = by_key[b], by_key[a]
    return out


def training_set(s: Session, *, exclude_photo: int | None = None, exclude_example: int | None = None, mirror: bool = True) -> list[Example]:
    """Every usable example (library labels + uploads), plus each one mirrored for its sibling pose."""
    n = len(F.FEATURE_NAMES)
    out: list[Example] = []
    for lab in s.exec(select(PoseLabel)).all():
        if lab.photo_id == exclude_photo:
            continue
        feats = s.get(PoseFeatures, lab.photo_id)
        vec = feats.vector if feats is not None and feats.vector else lab.vector
        if vec and len(vec) == n:
            out.append(Example(lab.pose_category_id, list(vec), "photo", lab.photo_id))
    for ex in s.exec(select(PoseExample)).all():
        if ex.id == exclude_example:
            continue
        if ex.vector and len(ex.vector) == n:
            out.append(Example(ex.pose_category_id, list(ex.vector), "upload", ex.id))
    if mirror:
        sib = sibling_map(s)
        out += [Example(sib.get(e.category_id, e.category_id), F.mirror(e.vector), "mirror", e.ref_id) for e in out]
    return out

