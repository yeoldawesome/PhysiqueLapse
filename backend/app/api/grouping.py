"""Automatic, unlabeled pose groups based on alignment similarity."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from ..db import get_session
from ..jobs import job_to_dict, runner
from ..services import grouping as G
from ..settings import load_settings

router = APIRouter(prefix="/grouping", tags=["grouping"])


class GroupingIn(BaseModel):
    threshold: float = Field(0.15, ge=0.02, le=0.6)
    min_size: int = Field(20, ge=1, le=50)


class MergeIn(BaseModel):
    into: int


@router.get("")
def status(s: Session = Depends(get_session)) -> dict[str, Any]:
    settings = load_settings(s)
    return {
        "active": settings.grouping_active,
        "threshold": settings.grouping_threshold,
        "min_size": settings.grouping_min_size,
    }


@router.get("/preview")
def preview(
    threshold: float = 0.15,
    min_size: int = 20,
    s: Session = Depends(get_session),
) -> dict[str, Any]:
    if not 0.02 <= threshold <= 0.6 or not 1 <= min_size <= 50:
        raise HTTPException(422, "threshold or min_size is outside the supported range")
    return G.preview(s, threshold, min_size)


@router.post("/apply")
def apply(body: GroupingIn) -> dict[str, Any]:
    j = runner().enqueue_chain([
        ("group_poses", {"threshold": body.threshold, "min_size": body.min_size}),
        ("align_photos", {"force": True}),
    ])
    return {"job": job_to_dict(j)}


@router.post("/merge/{cid}")
def merge(cid: int, body: MergeIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        moved = G.merge(s, cid, body.into)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    j = runner().enqueue_chain([("align_photos", {"force": True})])
    return {"moved": moved, "job": job_to_dict(j)}
