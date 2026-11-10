"""Face group: detection job and the face time-lapse sequence."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlmodel import Session, select

from ..db import get_session
from ..jobs import job_to_dict, runner
from ..models import Photo
from ..services import face_view

router = APIRouter(prefix="/faces", tags=["faces"])


@router.post("/detect")
def detect(body: dict | None = None) -> dict[str, Any]:
    body = body or {}
    return job_to_dict(runner().enqueue("detect_faces", {"force": bool(body.get("force")), "ids": body.get("ids")}))


@router.get("/sequence")
def sequence(from_: date | None = Query(None, alias="from"), to: date | None = None, s: Session = Depends(get_session)) -> dict[str, Any]:
    return face_view.sequence(s, from_, to)


@router.get("/summary")
def summary(s: Session = Depends(get_session)) -> dict[str, Any]:
    visible = s.exec(select(func.count(Photo.id)).where(Photo.face_visible == True)).one()  # noqa: E712
    checked = s.exec(select(func.count(Photo.id)).where(Photo.face_score.is_not(None))).one()
    pending = s.exec(select(func.count(Photo.id)).where(Photo.landmarks_status == "ok", Photo.face_score.is_(None))).one()
    return {"visible": visible, "checked": checked, "unchecked": pending}
