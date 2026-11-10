"""Per-photo alignment: editor context, manual override, reset; per-category evaluation."""

from __future__ import annotations

from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..db import get_session
from ..models import Alignment, Photo, PoseCategory, now
from ..schemas import alignment_out
from ..services import align as A
from ..services import landmarks as L
from ..settings import load_settings

router = APIRouter(tags=["alignment"])


class ManualIn(BaseModel):
    scale: float | None = None
    rotation_deg: float | None = None
    tx: float | None = None
    ty: float | None = None
    # alternative: corrected anchor points in source pixels (flip applied)
    head: list[float] | None = None
    hip: list[float] | None = None


def _ctx(s: Session, photo_id: int) -> tuple[Photo, PoseCategory, A.CategoryContext]:
    p = s.get(Photo, photo_id)
    if p is None:
        raise HTTPException(404, "photo not found")
    if p.pose_category_id is None:
        raise HTTPException(422, "assign a pose category first")
    cat = s.get(PoseCategory, p.pose_category_id)
    try:
        cc = A.category_context(s, cat, load_settings(s))
    except LookupError as e:
        raise HTTPException(409, str(e)) from None
    return p, cat, cc


def _pt(v: np.ndarray) -> list[float]:
    return [round(float(v[0]), 2), round(float(v[1]), 2)]


@router.get("/alignments/{photo_id}/context")
def context(photo_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    p, cat, cc = _ctx(s, photo_id)
    a = s.get(Alignment, photo_id)
    data = L.load_for_photo(p)
    src = None
    if data and data.get("detected"):
        an = A.anchors(L.points_dict(data), cat.anchor_mode)
        src = {"head": _pt(an.head), "hip": _pt(an.hip_mid), "shoulder": _pt(an.shoulder_mid)}
    auto = None
    if data and data.get("detected") and p.id != cc.reference.id:
        tr, flags, basis = A.solve(L.points_dict(data), cc.target, cat, cc.settings.rotation_clamp_deg)
        auto = {"scale": tr.scale, "rotation_deg": tr.rotation_deg, "tx": tr.tx, "ty": tr.ty, "flags": flags}
    elif p.id == cc.reference.id:
        t = cc.target.ref_transform
        auto = {"scale": t.scale, "rotation_deg": 0.0, "tx": t.tx, "ty": t.ty, "flags": {}}
    else:
        t = A.fit_transform(p.width, p.height, cc.settings.canvas_w, cc.settings.canvas_h)
        auto = {"scale": t.scale, "rotation_deg": 0.0, "tx": t.tx, "ty": t.ty, "flags": {"no_landmarks": True}}
    return {
        "photo_id": p.id,
        "width": p.width,
        "height": p.height,
        "flip_horizontal": p.flip_horizontal,
        "category": {"id": cat.id, "name": cat.name},
        "reference_photo_id": cc.reference.id,
        "is_reference": cc.reference.id == p.id,
        "canvas_w": cc.settings.canvas_w,
        "canvas_h": cc.settings.canvas_h,
        "target": {"head": _pt(cc.target.head), "hip": _pt(cc.target.hip_mid), "shoulder": _pt(cc.target.shoulder_mid)},
        "source_anchors": src,
        "auto": auto,
        "current": alignment_out(a),
        "reference_aligned_url": f"/api/photos/{cc.reference.id}/aligned",
    }


@router.put("/alignments/{photo_id}")
def put_manual(photo_id: int, body: ManualIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    p, cat, cc = _ctx(s, photo_id)
    if body.head is not None and body.hip is not None:
        try:
            tr = A.solve_two_point(np.array(body.head), np.array(body.hip), cc.target.head, cc.target.hip_mid)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
    elif None not in (body.scale, body.rotation_deg, body.tx, body.ty):
        if body.scale <= 0:
            raise HTTPException(422, "scale must be positive")
        tr = A.Transform(body.scale, body.rotation_deg, body.tx, body.ty)
    else:
        raise HTTPException(422, "send scale, rotation_deg, tx, ty — or head and hip anchors")
    a = s.get(Alignment, photo_id) or Alignment(photo_id=photo_id)
    W, H = cc.settings.canvas_w, cc.settings.canvas_h
    a.scale, a.rotation_deg, a.tx, a.ty = tr.scale, tr.rotation_deg, tr.tx, tr.ty
    a.method = "manual"
    a.canvas_w, a.canvas_h = W, H
    a.reference_photo_id = cc.reference.id
    a.pose_category_id = cat.id
    a.flags = {}
    a.scale_basis_used = "manual"
    data = L.load_for_photo(p)
    a.residual_px = (
        round(A.residuals(L.points_dict(data), tr, cc.target, cat.anchor_mode)["mean"], 2) if data and data.get("detected") else None
    )
    a.output_path = A.write_output(p.id, A.render(p, tr, W, H, bg_removal=cc.settings.bg_removal))
    a.stale = False
    a.computed_at = now()
    s.add(a)
    s.commit()
    s.refresh(a)
    return alignment_out(a)


@router.delete("/alignments/{photo_id}")
def reset(photo_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    p, _cat, cc = _ctx(s, photo_id)
    a = s.get(Alignment, photo_id)
    if a is not None:
        a.method = "auto"
        s.add(a)
    a = A.align_photo(s, p, cc, keep_manual=False)
    s.commit()
    s.refresh(a)
    return alignment_out(a)


@router.get("/poses/{cid}/eval")
def evaluate(cid: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    cat = s.get(PoseCategory, cid)
    if cat is None:
        raise HTTPException(404, "pose category not found")
    return A.evaluate_category(s, cat, load_settings(s))
