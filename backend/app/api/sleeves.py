"""Sleeves check: which photos have clothing covering the arms (Viewer "Hide sleeves")."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from ..db import get_session
from ..jobs import job_to_dict, runner
from ..models import Photo

router = APIRouter(prefix="/sleeves", tags=["sleeves"])


@router.post("/detect")
def detect(body: dict | None = None) -> dict[str, Any]:
    body = body or {}
    return job_to_dict(runner().enqueue("detect_sleeves", {"force": bool(body.get("force")), "ids": body.get("ids")}))


@router.get("/summary")
def summary(s: Session = Depends(get_session)) -> dict[str, Any]:
    sleeves = s.exec(select(func.count(Photo.id)).where(Photo.sleeves == True)).one()  # noqa: E712
    checked = s.exec(select(func.count(Photo.id)).where(Photo.sleeves_score.is_not(None))).one()
    pending = s.exec(select(func.count(Photo.id)).where(Photo.landmarks_status == "ok", Photo.sleeves_score.is_(None))).one()
    return {"sleeves": sleeves, "checked": checked, "unchecked": pending}
