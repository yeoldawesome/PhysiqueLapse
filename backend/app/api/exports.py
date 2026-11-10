"""Time-lapse exports (MP4 / GIF)."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from .. import config
from ..db import get_session
from ..jobs import job_to_dict, runner
from ..models import Export, PoseCategory
from ..services.export_video import delete_export_file, export_out, list_exports

router = APIRouter(prefix="/exports", tags=["exports"])


class CaptionIn(BaseModel):
    date: bool = True
    days: bool = True
    metrics: list[int] = Field(default_factory=list)


class ExportIn(BaseModel):
    pose_id: int
    from_: date | None = Field(None, alias="from")
    to: date | None = None
    fps: float = Field(2.0, gt=0, le=30)
    format: str = Field("mp4", pattern="^(mp4|gif)$")
    width: int | None = Field(None, ge=128, le=4096)
    crossfade: float = Field(0.0, ge=0, le=5)
    max_pose_diff_pct: float | None = Field(None, gt=0, le=200)
    caption: CaptionIn = Field(default_factory=CaptionIn)

    model_config = {"populate_by_name": True}


@router.post("")
def create(body: ExportIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    if s.get(PoseCategory, body.pose_id) is None:
        raise HTTPException(404, "pose category not found")
    params = body.model_dump(by_alias=True, mode="json")
    params["pose_id"] = body.pose_id
    ex = Export(pose_category_id=body.pose_id, format=body.format, params=params, status="queued")
    s.add(ex)
    s.commit()
    s.refresh(ex)
    j = runner().enqueue("export_video", {"export_id": ex.id}, dedupe=False)
    ex.job_id = j.id
    s.add(ex)
    s.commit()
    return {"export": export_out(ex), "job": job_to_dict(j)}


@router.get("")
def list_(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return list_exports(s)


@router.get("/{export_id}/download")
def download(export_id: int, s: Session = Depends(get_session)):
    ex = s.get(Export, export_id)
    if ex is None or not ex.path:
        raise HTTPException(404, "export not ready")
    p = config.abs_path(ex.path)
    if not p.exists():
        raise HTTPException(404, "export file missing")
    return FileResponse(p, media_type="video/mp4" if ex.format == "mp4" else "image/gif", filename=p.name)


@router.delete("/{export_id}")
def delete(export_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    ex = s.get(Export, export_id)
    if ex is None:
        raise HTTPException(404, "export not found")
    if ex.status in ("queued", "running"):
        raise HTTPException(409, "export is still running")
    delete_export_file(ex)
    s.delete(ex)
    s.commit()
    return {"deleted": export_id}
