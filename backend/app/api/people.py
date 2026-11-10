"""Filter people: mark photos of yourself, find the ones that are someone else."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..db import get_session
from ..jobs import job_to_dict, runner
from ..models import Photo
from ..services import people as P

router = APIRouter(prefix="/people", tags=["people"])


class LabelIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=5000)
    label: str | None = Field(None, pattern="^(me|not_me)$")  # None = back to automatic


def _item(p: Photo) -> dict[str, Any]:
    return {
        "id": p.id,
        "taken_at": p.taken_at.isoformat(timespec="seconds") if p.taken_at else None,
        "thumb_url": f"/api/photos/{p.id}/thumb",
        "face_url": f"/api/people/face/{p.id}" if P.face_crop_path(p.id).exists() else None,
        "flip_horizontal": p.flip_horizontal,
        "status": p.person_status,
        "label": p.person_label,
        "score": p.person_score,
        "original_filename": p.original_filename,
    }


@router.post("/scan")
def scan(body: dict | None = None) -> dict[str, Any]:
    if not P.models_available():
        raise HTTPException(409, "face models missing: run backend\\scripts\\download_models.py")
    return job_to_dict(runner().enqueue("scan_people", {"force": bool((body or {}).get("force"))}))


@router.get("/summary")
def summary(s: Session = Depends(get_session)) -> dict[str, Any]:
    return P.summary(s)


@router.get("/suggestions")
def suggestions(limit: int = 60, s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Faces most like the library's most common face: likely you, for confirming."""
    out = []
    for pid in P.suggestions(s, limit):
        p = s.get(Photo, pid)
        if p is not None and p.person_label is None:
            out.append(_item(p))
    return out


@router.get("/list")
def list_(status: str, s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Photos with this status; not-me / unsure / you sorted least-like-you first."""
    if status not in ("me", "not_me", "unsure", "no_face", "examples"):
        raise HTTPException(422, "unknown status")
    if status == "examples":
        stmt = select(Photo).where(Photo.person_label == "me")
    else:
        stmt = select(Photo).where(Photo.person_status == status)
    photos = list(s.exec(stmt).all())
    photos.sort(key=lambda p: (p.person_score if p.person_score is not None else 9, p.id))
    return [_item(p) for p in photos]


@router.post("/label")
def label(body: LabelIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    return P.set_label(s, body.ids, body.label)


@router.get("/face/{photo_id}")
def face(photo_id: int):
    path = P.face_crop_path(photo_id)
    if not path.exists():
        raise HTTPException(404, "no face crop")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
