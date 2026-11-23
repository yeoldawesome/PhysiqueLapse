"""Capture page: series, which photo is the ghost, the edge-outline ghost, and taking in a
photo shot in the browser (dated by the phone, assigned to its pose or series, then
landmarks + alignment without classification)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sqlmodel import Session, select

from .. import config
from ..models import CaptureSeries, Photo, PoseCategory, now
from ..settings import load_settings
from . import ingest
from . import landmarks as L

log = logging.getLogger(__name__)

GHOST_SOURCES = ("reference", "last", "first")
GHOST_STYLES = ("photo", "edges", "skeleton")


# ----------------------------------------------------------------------------- series


def series_for_pose(s: Session, pose_id: int) -> CaptureSeries:
    """The reference-mode series of a pose (made the first time the pose is opened for capture)."""
    cat = s.get(PoseCategory, pose_id)
    if cat is None:
        raise LookupError("pose not found")
    row = s.exec(
        select(CaptureSeries).where(CaptureSeries.mode == "reference", CaptureSeries.pose_category_id == pose_id)
    ).first()
    if row is None:
        row = CaptureSeries(name=cat.name, mode="reference", pose_category_id=pose_id, ghost_source="reference")
        s.add(row)
        s.commit()
        s.refresh(row)
    return row


def series_photos(series: CaptureSeries):
    """Select statement for the photos that belong to a series (kept, dated)."""
    stmt = select(Photo).where(Photo.excluded == False)  # noqa: E712
    if series.mode == "reference" and series.pose_category_id is not None:
        return stmt.where(Photo.pose_category_id == series.pose_category_id)
    return stmt.where(Photo.capture_series_id == series.id)


def reference_photo(s: Session, series: CaptureSeries) -> Photo | None:
    if series.pose_category_id is not None:
        cat = s.get(PoseCategory, series.pose_category_id)
        if cat is not None and cat.reference_photo_id:
            p = s.get(Photo, cat.reference_photo_id)
            if p is not None:
                return p
    if series.reference_photo_id:
        p = s.get(Photo, series.reference_photo_id)
        if p is not None:
            return p
    return None


def _edge(s: Session, series: CaptureSeries, newest: bool) -> Photo | None:
    order = (Photo.taken_at.desc(), Photo.id.desc()) if newest else (Photo.taken_at.asc(), Photo.id.asc())
    return s.exec(series_photos(series).where(Photo.taken_at.is_not(None)).order_by(*order).limit(1)).first()


def pick_ghost(s: Session, series: CaptureSeries, source: str | None = None) -> Photo | None:
    """The photo to line up against. `reference` = the pose's (or series') reference photo,
    `last` = the newest photo of the series, `first` = the oldest. Falls back to whatever exists
    (reference -> last -> first) so a new series still shows something once it has a photo."""
    source = source if source in GHOST_SOURCES else series.ghost_source
    order = {"reference": ("reference", "last", "first"), "last": ("last", "reference", "first"), "first": ("first", "reference", "last")}[source]
    for src in order:
        p = reference_photo(s, series) if src == "reference" else _edge(s, series, newest=(src == "last"))
        if p is not None:
            return p
    return None


def ghost_landmarks(photo: Photo) -> list[dict[str, Any]] | None:
    """Landmarks of the ghost as the camera saw it (no display flip), x/y as 0..1 of the image."""
    raw = L.load_raw(photo.id)
    if not raw or not raw.get("detected") or not raw.get("points"):
        return None
    w, h = float(raw["image_w"]), float(raw["image_h"])
    return [
        {"name": p["name"], "x": round(p["x_px"] / w, 5), "y": round(p["y_px"] / h, 5), "v": round(p.get("visibility", 0.0), 3)}
        for p in raw["points"]
    ]


def ghost_info(s: Session, series: CaptureSeries, source: str | None = None) -> dict[str, Any] | None:
    p = pick_ghost(s, series, source)
    if p is None:
        return None
    v = int(p.updated_at.timestamp()) if p.updated_at else 0
    return {
        "photo_id": p.id,
        "taken_at": p.taken_at.isoformat() if p.taken_at else None,
        "width": p.width,
        "height": p.height,
        "image_url": f"/api/photos/{p.id}/preview",
        "edges_url": f"/api/capture/ghost/{p.id}/edges?v={v}",
        "landmarks": ghost_landmarks(p),
    }


# ----------------------------------------------------------------------------- edge ghost


def edges_path(photo_id: int) -> Path:
    return config.DERIVED_DIR / "ghost_edges" / f"{photo_id}.png"


def edges_png(photo: Photo) -> Path:
    """White outline of the photo on a transparent background (Canny on the preview), cached."""
    import cv2
    from PIL import Image

    out = edges_path(photo.id)
    if out.exists():
        return out
    src = config.PREVIEWS_DIR / f"{photo.id}.jpg"
    rgb = np.asarray(Image.open(src).convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    med = float(np.median(gray))
    edges = cv2.Canny(gray, int(max(0, 0.66 * med)), int(min(255, 1.33 * med)))
    edges = cv2.dilate(edges, np.ones((2, 2), np.uint8))
    rgba = np.zeros((*edges.shape, 4), np.uint8)
    rgba[..., :3] = 255
    rgba[..., 3] = edges
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png.tmp")
    Image.fromarray(rgba, "RGBA").save(tmp, "PNG", optimize=True)
    tmp.replace(out)
    return out


# ----------------------------------------------------------------------------- taking in a capture


def parse_captured_at(value: str) -> datetime:
    """Phone time (ISO 8601, ideally with its UTC offset) -> this server's naive local time,
    which is how the whole library stores dates."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    dt = dt.replace(microsecond=0)
    if dt > now() + timedelta(days=1) or dt.year < 2000:
        raise ValueError("capture time looks wrong")
    return dt


def ingest_capture(
    s: Session, src: Path, *, filename: str, series: CaptureSeries, captured_at: datetime,
    ghost_photo_id: int | None, meta: dict[str, Any],
) -> tuple[Photo, bool]:
    """Add a captured photo. Returns (photo, created). Sending the same bytes again (a retried
    upload) returns the existing photo instead of a second copy."""
    settings = load_settings(s)
    r = ingest.ingest_file(
        s, src, source="capture", source_ref=f"series:{series.id}", session_id=None, settings=settings,
        index=ingest.load_phash_index(s), display_name=filename, move=True,
        force=True,  # day-to-day captures against the same ghost look alike: never "near-duplicate"
    )
    if r.status == "duplicate" and r.duplicate_of:
        p = s.get(Photo, r.duplicate_of)
        if p is not None:
            return p, False
    if r.status != "accepted" or r.photo_id is None:
        raise ValueError(r.message or r.status)
    p = s.get(Photo, r.photo_id)
    assert p is not None
    p.taken_at, p.taken_at_source, p.taken_at_confidence = captured_at, "capture", "high"
    p.capture_series_id = series.id
    p.ghost_photo_id = ghost_photo_id
    p.capture = {k: v for k, v in meta.items() if k in ("facing", "mirror", "style", "opacity", "match", "device", "width", "height")}
    if series.pose_category_id is not None:
        p.pose_category_id = series.pose_category_id
        p.pose_source, p.pose_confidence, p.pose_needs_review = "capture", 1.0, False
    p.updated_at = now()
    s.add(p)
    s.commit()
    s.refresh(p)
    return p, True


def processing_after_capture(series: CaptureSeries, photo_id: int) -> list[tuple[str, dict]]:
    """Landmarks, then alignment (no classification: the pose is known)."""
    steps: list[tuple[str, dict]] = [("extract_landmarks", {"ids": [photo_id]})]
    if series.pose_category_id is not None:
        steps.append(("align_photos", {"ids": [photo_id]}))
    return steps
