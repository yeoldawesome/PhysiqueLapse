"""Capture page API: series (per pose / daily), ghost info and outline, and uploading a capture."""

from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import config
from ..db import get_session
from ..jobs import runner
from ..models import CaptureSeries, Photo, PoseCategory
from ..services import capture as C
from ..services import images

router = APIRouter(prefix="/capture", tags=["capture"])


def series_out(s: Session, row: CaptureSeries) -> dict[str, Any]:
    from sqlalchemy import func

    count = s.exec(select(func.count()).select_from(C.series_photos(row).subquery())).one()
    last = C._edge(s, row, newest=True)
    return {
        **row.model_dump(mode="json"),
        **({"streak": C.streak(s, row)} if row.mode == "daily" else {}),
        "photo_count": count,
        "last_taken_at": last.taken_at.isoformat() if last and last.taken_at else None,
        "last_thumb_url": f"/api/photos/{last.id}/thumb" if last else None,
    }


def _series(s: Session, sid: int) -> CaptureSeries:
    row = s.get(CaptureSeries, sid)
    if row is None:
        raise HTTPException(404, "series not found")
    return row


@router.get("/series")
def list_series(s: Session = Depends(get_session)) -> dict[str, Any]:
    """Every pose (reference mode; its series is made on first use) and every daily series."""
    poses = s.exec(select(PoseCategory).order_by(PoseCategory.sort_order, PoseCategory.id)).all()
    by_pose = {r.pose_category_id: r for r in s.exec(select(CaptureSeries).where(CaptureSeries.mode == "reference")).all()}
    pose_rows = []
    for c in poses:
        ref = s.get(Photo, c.reference_photo_id) if c.reference_photo_id else None
        r = by_pose.get(c.id)
        pose_rows.append({
            "pose_id": c.id, "name": c.name, "series_id": r.id if r else None,
            "reference_thumb_url": f"/api/photos/{ref.id}/thumb" if ref else None,
        })
    daily = s.exec(
        select(CaptureSeries).where(CaptureSeries.mode == "daily", CaptureSeries.archived == False)  # noqa: E712
        .order_by(CaptureSeries.sort_order, CaptureSeries.id)
    ).all()
    return {"poses": pose_rows, "daily": [series_out(s, d) for d in daily]}


@router.post("/series/for-pose/{pose_id}")
def open_pose_series(pose_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        return series_out(s, C.series_for_pose(s, pose_id))
    except LookupError:
        raise HTTPException(404, "pose not found") from None


class SeriesIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    pose_category_id: int | None = None


@router.post("/series")
def create_daily(body: SeriesIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    if body.pose_category_id is not None and s.get(PoseCategory, body.pose_category_id) is None:
        raise HTTPException(404, "pose not found")
    row = CaptureSeries(name=body.name.strip(), mode="daily", pose_category_id=body.pose_category_id, ghost_source="last")
    s.add(row)
    s.commit()
    s.refresh(row)
    return series_out(s, row)


class SeriesPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    ghost_source: str | None = None
    ghost_style: str | None = None
    ghost_opacity: float | None = Field(default=None, ge=0.0, le=1.0)
    mirror: bool | None = None
    camera_facing: str | None = None
    timer_s: int | None = None
    auto_capture: bool | None = None
    archived: bool | None = None
    reference_photo_id: int | None = None


@router.get("/series/{sid}")
def get_series(sid: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    return series_out(s, _series(s, sid))


@router.patch("/series/{sid}")
def patch_series(sid: int, body: SeriesPatch, s: Session = Depends(get_session)) -> dict[str, Any]:
    row = _series(s, sid)
    patch = body.model_dump(exclude_unset=True)
    if "ghost_source" in patch and patch["ghost_source"] not in C.GHOST_SOURCES:
        raise HTTPException(422, "ghost_source must be reference, last or first")
    if "ghost_style" in patch and patch["ghost_style"] not in C.GHOST_STYLES:
        raise HTTPException(422, "ghost_style must be photo, edges or skeleton")
    if "camera_facing" in patch and patch["camera_facing"] not in ("user", "environment"):
        raise HTTPException(422, "camera_facing must be user or environment")
    if "timer_s" in patch and patch["timer_s"] not in (0, 3, 5, 10):
        raise HTTPException(422, "timer must be 0, 3, 5 or 10 s")
    if patch.get("reference_photo_id") is not None and s.get(Photo, patch["reference_photo_id"]) is None:
        raise HTTPException(404, "photo not found")
    for k, v in patch.items():
        setattr(row, k, v)
    s.add(row)
    s.commit()
    s.refresh(row)
    return series_out(s, row)


@router.get("/series/{sid}/ghost")
def get_ghost(sid: int, source: str | None = None, s: Session = Depends(get_session)) -> dict[str, Any]:
    info = C.ghost_info(s, _series(s, sid), source)
    return {"ghost": info}


@router.get("/series/{sid}/sequence")
def series_sequence(sid: int, from_: date | None = Query(None, alias="from"), to: date | None = None, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Frames of a daily series for the Viewer (flipbook) and export."""
    return C.series_sequence(s, _series(s, sid), from_, to)


@router.get("/series/{sid}/aligned/{photo_id}")
def series_aligned(sid: int, photo_id: int, s: Session = Depends(get_session)):
    from ..models import SeriesAlignment

    a = s.get(SeriesAlignment, (sid, photo_id))
    if a is None or not a.output_path or not config.abs_path(a.output_path).exists():
        raise HTTPException(404, "not aligned yet")
    return FileResponse(config.abs_path(a.output_path), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000"})


@router.post("/series/{sid}/align")
def align_series(sid: int, force: bool = False, s: Session = Depends(get_session)) -> dict[str, Any]:
    from ..jobs import job_to_dict

    _series(s, sid)
    return job_to_dict(runner().enqueue("align_series", {"series_id": sid, "force": force}))


@router.get("/pose-model")
def live_pose_model():
    """The lite pose model for live matching on the Capture page (behind the login like the rest)."""
    path = config.pose_model_path("lite")
    if not path.exists():
        raise HTTPException(404, "live pose model not downloaded yet (Settings: download models)")
    return FileResponse(path, media_type="application/octet-stream", headers={"Cache-Control": "private, max-age=604800"})


@router.get("/ghost/{photo_id}/edges")
def ghost_edges(photo_id: int, s: Session = Depends(get_session)):
    p = s.get(Photo, photo_id)
    if p is None:
        raise HTTPException(404, "photo not found")
    try:
        path = C.edges_png(p)
    except Exception as e:
        raise HTTPException(500, f"could not make the outline: {e}") from None
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.post("/upload")
async def upload_capture(
    file: UploadFile = File(...),
    series_id: int = Form(...),
    captured_at: str = Form(...),
    ghost_photo_id: int | None = Form(None),
    meta: str = Form("{}"),
    s: Session = Depends(get_session),
) -> dict[str, Any]:
    """One photo from the Capture page. Safe to retry: the same file again returns the same photo."""
    series = _series(s, series_id)
    try:
        when = C.parse_captured_at(captured_at)
    except ValueError as e:
        raise HTTPException(422, f"captured_at: {e}") from None
    try:
        meta_d = json.loads(meta) if meta else {}
        if not isinstance(meta_d, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(422, "meta must be a JSON object") from None
    name = Path(file.filename or "capture.jpg").name
    if not images.is_image(name):
        name = "capture.jpg"
    cap = config.MAX_UPLOAD_MB << 20
    config.INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=config.INCOMING_DIR, suffix=Path(name).suffix, delete=False) as tmp:
        written = 0
        while chunk := await file.read(1 << 20):
            written += len(chunk)
            if written > cap:
                break
            tmp.write(chunk)
    tmp_path = Path(tmp.name)
    try:
        if written > cap:
            raise HTTPException(413, f"photo larger than {config.MAX_UPLOAD_MB} MB")
        if written == 0:
            raise HTTPException(422, "empty file")
        try:
            photo, created = C.ingest_capture(
                s, tmp_path, filename=name, series=series, captured_at=when, ghost_photo_id=ghost_photo_id, meta=meta_d
            )
        except ValueError as e:
            raise HTTPException(422, f"not a usable photo: {e}") from None
    finally:
        tmp_path.unlink(missing_ok=True)
    job = None
    if created:
        job = runner().enqueue_chain(C.processing_after_capture(series, photo.id)).id
    return {
        "photo_id": photo.id, "created": created, "job_id": job,
        "taken_at": photo.taken_at.isoformat() if photo.taken_at else None,
        "thumb_url": f"/api/photos/{photo.id}/thumb",
    }
