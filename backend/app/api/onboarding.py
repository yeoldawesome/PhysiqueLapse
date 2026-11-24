"""First-run tutorial and the "teach your poses" step (see services/onboarding.py)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..jobs import job_to_dict, runner
from ..models import Job, PoseCategory
from ..services import onboarding as O
from ..settings import save_settings

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


class OnboardingPatch(BaseModel):
    tutorial_seen: bool | None = None
    skipped: list[int] | None = None


class FinishIn(BaseModel):
    skipped: list[int] = []


@router.get("")
def get_status(s: Session = Depends(get_session)) -> dict[str, Any]:
    return O.status(s)


@router.patch("")
def patch_status(body: OnboardingPatch, s: Session = Depends(get_session)) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if body.tutorial_seen is not None:
        patch["tutorial_seen"] = body.tutorial_seen
    if body.skipped is not None:
        patch["pose_setup_skipped"] = body.skipped
    if patch:
        save_settings(s, patch)
    return O.status(s)


def _active(s: Session, types: tuple[str, ...]) -> Job | None:
    return s.exec(select(Job).where(Job.type.in_(types), Job.status.in_(("queued", "running")))).first()


@router.post("/prepare")
def prepare(s: Session = Depends(get_session)) -> dict[str, Any]:
    """Find the joints in every photo, then read their poses. Idempotent while it runs."""
    running = _active(s, ("extract_landmarks", "pose_setup_prepare"))
    if running is not None:
        return {"job": job_to_dict(running)}
    j = runner().enqueue_chain([("extract_landmarks", {}), ("pose_setup_prepare", {})])
    return {"job": job_to_dict(j)}


@router.get("/poses")
def list_poses(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return O.poses(s)


@router.get("/poses/{cid}/candidates")
def pose_candidates(cid: int, limit: int = O.MAX_CANDIDATES, s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    cat = s.get(PoseCategory, cid)
    if cat is None:
        raise HTTPException(404, "pose category not found")
    return O.candidates(s, cat, max(1, min(200, limit)))


@router.post("/finish")
def finish(body: FinishIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Pose examples done: sort every photo into its pose, then line them up."""
    save_settings(s, {"pose_setup_done": True, "pose_setup_skipped": body.skipped})
    j = runner().enqueue_chain([("classify_poses", {"force": True}), ("align_photos", {})])
    return {"job": job_to_dict(j), "status": O.status(s)}
