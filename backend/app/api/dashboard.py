"""Dashboard summary numbers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from ..db import get_session
from ..models import Alignment, ImportCandidate, Job, Metric, MetricEntry, Photo, PoseCategory
from ..schemas import category_out, photo_out
from ..services.people import summary as people_summary
from .abs import summary as abs_summary
from .faces import summary as face_summary

router = APIRouter(tags=["dashboard"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _cover(s: Session, c: PoseCategory) -> dict[str, Any] | None:
    """Card image for a pose: its reference photo (if still in the pose), else its most recent photo."""
    p = s.get(Photo, c.reference_photo_id) if c.reference_photo_id else None
    if p is None or p.pose_category_id != c.id:
        p = s.exec(
            select(Photo)
            .where(Photo.pose_category_id == c.id, Photo.excluded == False)  # noqa: E712
            .order_by(Photo.taken_at.desc().nulls_last(), Photo.id.desc())
            .limit(1)
        ).first()
    return {"photo_id": p.id, "thumb_url": f"/api/photos/{p.id}/thumb", "flip_horizontal": p.flip_horizontal} if p else None


def _closeup_cover(s: Session, column, kind: str) -> str | None:
    """Card image for Abs / Face: the most recent close-up."""
    p = s.exec(
        select(Photo).where(column == True, Photo.excluded == False).order_by(Photo.taken_at.desc().nulls_last(), Photo.id.desc()).limit(1)  # noqa: E712
    ).first()
    return f"/api/photos/{p.id}/{kind}" if p else None


@router.get("/dashboard")
def dashboard(s: Session = Depends(get_session)) -> dict[str, Any]:
    total = s.exec(select(func.count(Photo.id))).one()
    per_pose = dict(
        s.exec(select(Photo.pose_category_id, func.count(Photo.id)).group_by(Photo.pose_category_id)).all()
    )
    cats = s.exec(select(PoseCategory).order_by(PoseCategory.sort_order, PoseCategory.id)).all()
    first, last = s.exec(select(func.min(Photo.taken_at), func.max(Photo.taken_at))).one()
    latest = s.exec(select(Photo).order_by(Photo.created_at.desc(), Photo.id.desc()).limit(8)).all()
    counts = {
        "photos": total,
        "unassigned": per_pose.get(None, 0),
        "no_date": s.exec(select(func.count(Photo.id)).where(Photo.taken_at.is_(None))).one(),
        "low_confidence_date": s.exec(select(func.count(Photo.id)).where(Photo.taken_at_confidence == "low")).one(),
        "needs_review": s.exec(select(func.count(Photo.id)).where(Photo.pose_needs_review == True)).one(),  # noqa: E712
        "landmarks_pending": s.exec(select(func.count(Photo.id)).where(Photo.landmarks_status == "pending")).one(),
        "landmarks_missing": s.exec(select(func.count(Photo.id)).where(Photo.landmarks_status.in_(("none", "failed")))).one(),
        "aligned": s.exec(select(func.count(Alignment.photo_id)).where(Alignment.stale == False)).one(),  # noqa: E712
        "alignment_stale": s.exec(select(func.count(Alignment.photo_id)).where(Alignment.stale == True)).one(),  # noqa: E712
        "review_queue": s.exec(select(func.count(ImportCandidate.id)).where(ImportCandidate.decision == "review")).one(),
        "active_jobs": s.exec(select(func.count(Job.id)).where(Job.status.in_(("queued", "running")))).one(),
        "excluded": s.exec(select(func.count(Photo.id)).where(Photo.excluded == True)).one(),  # noqa: E712
    }
    weight = None
    wm = s.exec(select(Metric).where(Metric.kind == "weight")).first()
    if wm is not None:
        entries = s.exec(select(MetricEntry).where(MetricEntry.metric_id == wm.id).order_by(MetricEntry.date)).all()
        if entries:
            weight = {
                "unit": wm.unit,
                "latest": entries[-1].value,
                "latest_date": entries[-1].date.isoformat(),
                "first": entries[0].value,
                "first_date": entries[0].date.isoformat(),
                "change": round(entries[-1].value - entries[0].value, 2),
                "entries": len(entries),
            }
    return {
        "counts": counts,
        "categories": [{**category_out(c, per_pose.get(c.id, 0)), "cover": _cover(s, c)} for c in cats],
        "abs": {**abs_summary(s), "cover_url": _closeup_cover(s, Photo.abs_visible, "abs")},
        "face": {**face_summary(s), "cover_url": _closeup_cover(s, Photo.face_visible, "face")},
        "people": people_summary(s),
        "date_span": {"first": first.isoformat() if first else None, "last": last.isoformat() if last else None},
        "latest": [photo_out(p) for p in latest],
        "weight": weight,
    }
