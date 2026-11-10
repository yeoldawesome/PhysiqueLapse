"""Background job status and cancellation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..jobs import job_to_dict, runner
from ..models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(active: bool = False, limit: int = 30, s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    stmt = select(Job)
    if active:
        stmt = stmt.where(Job.status.in_(["queued", "running"]))
    rows = s.exec(stmt.order_by(Job.id.desc()).limit(limit)).all()
    return [job_to_dict(j) for j in rows]


@router.get("/{job_id}")
def get_job(job_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    j = s.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    return job_to_dict(j)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: int) -> dict[str, Any]:
    j = runner().cancel(job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    return job_to_dict(j)


@router.post("/{job_id}/retry")
def retry_job(job_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    j = s.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    if j.status in ("queued", "running"):
        raise HTTPException(409, "job is still active")
    new = runner().enqueue(j.type, dict(j.params or {}), dedupe=True)
    return job_to_dict(new)
