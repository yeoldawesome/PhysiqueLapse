"""Viewer: the ordered, aligned frame sequence of one pose category."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..db import get_session
from ..models import Alignment, Photo, PoseCategory
from ..schemas import iso
from ..settings import load_settings

router = APIRouter(tags=["viewer"])


def sequence_photos(s: Session, cid: int, from_: date | None, to: date | None) -> list[Photo]:
    stmt = select(Photo).where(Photo.pose_category_id == cid, Photo.excluded == False, Photo.taken_at.is_not(None))  # noqa: E712
    if from_:
        stmt = stmt.where(Photo.taken_at >= datetime.combine(from_, datetime.min.time()))
    if to:
        stmt = stmt.where(Photo.taken_at < datetime.combine(to + timedelta(days=1), datetime.min.time()))
    return list(s.exec(stmt.order_by(Photo.taken_at, Photo.id)).all())


@router.get("/poses/{cid}/sequence")
def sequence(
    cid: int,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    s: Session = Depends(get_session),
) -> dict[str, Any]:
    cat = s.get(PoseCategory, cid)
    if cat is None:
        raise HTTPException(404, "pose category not found")
    settings = load_settings(s)
    photos = sequence_photos(s, cid, from_, to)
    torso_canvas = None
    try:
        from ..services.align import category_context

        torso_canvas = category_context(s, cat, settings, auto_pick=False).target.lengths["torso"]
    except LookupError:
        pass
    first = photos[0].taken_at if photos else None
    from ..services.measure import frame_values
    from ..services.stats import stats_for_dates

    meas = frame_values(s, [p.id for p in photos])
    stats = stats_for_dates(s, [p.taken_at.date() for p in photos], settings.stats_window_days, first.date() if first else None)
    frames, unaligned = [], 0
    for p, st in zip(photos, stats, strict=True):
        a = s.get(Alignment, p.id)
        ok = a is not None and a.output_path and a.pose_category_id == cid
        if not ok or a.stale:
            unaligned += 1
        v = int(a.computed_at.timestamp()) if ok and a.computed_at else 0
        frames.append(
            {
                "id": p.id,
                "taken_at": iso(p.taken_at),
                "days_since_start": (p.taken_at.date() - first.date()).days if first else None,
                "aligned_url": f"/api/photos/{p.id}/aligned?v={v}" if ok else None,
                "preview_url": f"/api/photos/{p.id}/preview",
                "flags": (a.flags or {}) if ok else {},
                "method": a.method if ok else None,
                "stale": bool(a.stale) if ok else True,
                "flip_horizontal": p.flip_horizontal,
                "stats": st,
                "measurements": meas.get(p.id, {}),
                # how far this photo's shoulders/elbows/wrists land from the reference's, in % of torso length
                "pose_diff_pct": (
                    round(100 * a.joint_residual_px / torso_canvas, 1)
                    if ok and a.joint_residual_px is not None and torso_canvas
                    else None
                ),
            }
        )
    no_date = s.exec(
        select(Photo.id).where(Photo.pose_category_id == cid, Photo.excluded == False, Photo.taken_at.is_(None))  # noqa: E712
    ).all()
    return {
        "pose_category_id": cid,
        "canvas_w": settings.canvas_w,
        "canvas_h": settings.canvas_h,
        "reference_photo_id": cat.reference_photo_id,
        "frames": frames,
        "unaligned": unaligned,
        "without_date": len(no_date),
    }
