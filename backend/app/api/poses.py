"""Pose categories: CRUD, reference photo, classification and alignment jobs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from ..db import get_session
from ..jobs import job_to_dict, runner
from ..models import Alignment, Photo, PoseCategory, PoseLabel, now
from ..schemas import CategoryIn, category_out

router = APIRouter(prefix="/poses", tags=["poses"])

ANCHOR_MODES = {"auto", "face", "ears"}
SCALE_BASES = {"torso", "nose_hip", "head"}
ROTATION_BASES = {"torso", "eyes", "shoulders", "none"}


def _get(s: Session, cid: int) -> PoseCategory:
    c = s.get(PoseCategory, cid)
    if c is None:
        raise HTTPException(404, "pose category not found")
    return c


def _counts(s: Session) -> dict[int | None, int]:
    return dict(s.exec(select(Photo.pose_category_id, func.count(Photo.id)).group_by(Photo.pose_category_id)).all())


def _mark_category_stale(s: Session, cid: int) -> None:
    for a in s.exec(select(Alignment).where(Alignment.pose_category_id == cid)).all():
        a.stale = True
        s.add(a)


@router.get("")
def list_categories(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    counts = _counts(s)
    cats = s.exec(select(PoseCategory).order_by(PoseCategory.sort_order, PoseCategory.id)).all()
    labels = dict(s.exec(select(PoseLabel.pose_category_id, func.count(PoseLabel.id)).group_by(PoseLabel.pose_category_id)).all())
    out = []
    for c in cats:
        d = category_out(c, counts.get(c.id, 0))
        d["label_count"] = labels.get(c.id, 0)
        out.append(d)
    return out


@router.post("")
def create_category(body: CategoryIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(422, "name is required")
    if s.exec(select(PoseCategory).where(func.lower(PoseCategory.name) == name.lower())).first():
        raise HTTPException(409, "a category with that name exists")
    max_order = s.exec(select(func.max(PoseCategory.sort_order))).one() or 0
    c = PoseCategory(name=name, sort_order=max_order + 1)
    _apply(c, body)
    s.add(c)
    s.commit()
    s.refresh(c)
    return category_out(c, 0)


def _apply(c: PoseCategory, body: CategoryIn) -> None:
    if body.name is not None and body.name.strip():
        c.name = body.name.strip()
    if body.sort_order is not None:
        c.sort_order = body.sort_order
    if body.anchor_mode is not None:
        if body.anchor_mode not in ANCHOR_MODES:
            raise HTTPException(422, f"anchor_mode must be one of {sorted(ANCHOR_MODES)}")
        c.anchor_mode = body.anchor_mode
    if body.scale_basis is not None:
        if body.scale_basis not in SCALE_BASES:
            raise HTTPException(422, f"scale_basis must be one of {sorted(SCALE_BASES)}")
        c.scale_basis = body.scale_basis
    if body.rotation_basis is not None:
        if body.rotation_basis not in ROTATION_BASES:
            raise HTTPException(422, f"rotation_basis must be one of {sorted(ROTATION_BASES)}")
        c.rotation_basis = body.rotation_basis
    if body.align_method is not None:
        if body.align_method not in ("body", "head"):
            raise HTTPException(422, "align_method must be body or head")
        c.align_method = body.align_method
    if body.normalize_facing is not None:
        c.normalize_facing = body.normalize_facing
    if body.framing_zoom is not None:
        c.framing_zoom = max(0.25, min(4.0, body.framing_zoom))
    if body.framing_dx is not None:
        c.framing_dx = max(-1.0, min(1.0, body.framing_dx))
    if body.framing_dy is not None:
        c.framing_dy = max(-1.0, min(1.0, body.framing_dy))


@router.patch("/{cid}")
def patch_category(cid: int, body: CategoryIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    c = _get(s, cid)
    before = (c.align_method, c.anchor_mode, c.scale_basis, c.rotation_basis, c.normalize_facing, c.framing_zoom, c.framing_dx, c.framing_dy)
    if body.name and body.name.strip().lower() != c.name.lower():
        if s.exec(select(PoseCategory).where(func.lower(PoseCategory.name) == body.name.strip().lower())).first():
            raise HTTPException(409, "a category with that name exists")
    _apply(c, body)
    after = (c.align_method, c.anchor_mode, c.scale_basis, c.rotation_basis, c.normalize_facing, c.framing_zoom, c.framing_dx, c.framing_dy)
    if before != after:
        _mark_category_stale(s, cid)
    s.add(c)
    s.commit()
    s.refresh(c)
    return category_out(c, _counts(s).get(c.id, 0))


@router.delete("/{cid}")
def delete_category(cid: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    c = _get(s, cid)
    if c.rule_key == "other":
        raise HTTPException(422, "the Other category cannot be deleted")
    other = s.exec(select(PoseCategory).where(PoseCategory.rule_key == "other")).first()
    moved = 0
    for p in s.exec(select(Photo).where(Photo.pose_category_id == cid)).all():
        p.pose_category_id = other.id if other else None
        p.pose_source = "manual" if p.pose_source == "manual" else "none"
        p.pose_confidence = None
        p.updated_at = now()
        s.add(p)
        moved += 1
    for lab in s.exec(select(PoseLabel).where(PoseLabel.pose_category_id == cid)).all():
        s.delete(lab)
    for a in s.exec(select(Alignment).where(Alignment.pose_category_id == cid)).all():
        a.stale = True
        s.add(a)
    s.delete(c)
    s.commit()
    return {"deleted": cid, "moved_to_other": moved}


@router.post("/{cid}/reference")
def set_reference(cid: int, body: dict, s: Session = Depends(get_session)) -> dict[str, Any]:
    c = _get(s, cid)
    pid = body.get("photo_id")
    if pid is None:
        c.reference_photo_id = None
    else:
        p = s.get(Photo, int(pid))
        if p is None:
            raise HTTPException(404, "photo not found")
        if p.pose_category_id != cid:
            # assigning a reference also assigns the pose (manual)
            from ..schemas import PhotoPatch
            from .photos import apply_patch

            apply_patch(s, p, PhotoPatch(pose_category_id=cid))
        c.reference_photo_id = p.id
    _mark_category_stale(s, cid)
    s.add(c)
    s.commit()
    s.refresh(c)
    return category_out(c, _counts(s).get(c.id, 0))


def _enqueue(type_: str, params: dict) -> dict[str, Any]:
    try:
        return job_to_dict(runner().enqueue(type_, params))
    except KeyError:
        raise HTTPException(501, f"{type_} is not available in this build") from None


@router.post("/classify")
def classify(body: dict | None = None, s: Session = Depends(get_session)) -> dict[str, Any]:
    body = body or {}
    return _enqueue("classify_poses", {"ids": body.get("ids"), "force": bool(body.get("force", False))})


@router.post("/{cid}/align")
def align(cid: int, body: dict | None = None, s: Session = Depends(get_session)) -> dict[str, Any]:
    _get(s, cid)
    body = body or {}
    return _enqueue("align_photos", {"pose_category_id": cid, "force": bool(body.get("force", False))})


@router.post("/align-all")
def align_all(body: dict | None = None) -> dict[str, Any]:
    body = body or {}
    return _enqueue("align_photos", {"force": bool(body.get("force", False))})
