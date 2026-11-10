"""Request/response shapes shared by routers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from . import config
from .models import Alignment, ImportCandidate, ImportSession, Photo, PoseCategory


def iso(d: datetime | None) -> str | None:
    return d.isoformat(timespec="seconds") if d else None


def photo_out(p: Photo, a: Alignment | None = None) -> dict[str, Any]:
    aligned_url = None
    if a is not None and a.output_path and not a.stale:
        aligned_url = f"/api/photos/{p.id}/aligned?v={int(a.computed_at.timestamp()) if a.computed_at else 0}"
    return {
        "id": p.id,
        "sha256": p.sha256,
        "original_filename": p.original_filename,
        "ext": p.ext,
        "source": p.source,
        "source_ref": p.source_ref,
        "width": p.width,
        "height": p.height,
        "taken_at": iso(p.taken_at),
        "taken_at_source": p.taken_at_source,
        "taken_at_confidence": p.taken_at_confidence,
        "flip_horizontal": p.flip_horizontal,
        "flip_source": p.flip_source,
        "pose_category_id": p.pose_category_id,
        "pose_confidence": p.pose_confidence,
        "pose_source": p.pose_source,
        "pose_needs_review": p.pose_needs_review,
        "landmarks_status": p.landmarks_status,
        "landmarks_quality": p.landmarks_quality,
        "excluded": p.excluded,
        "abs_visible": p.abs_visible,
        "abs_score": p.abs_score,
        "abs_source": p.abs_source,
        "sleeves": p.sleeves,
        "sleeves_score": p.sleeves_score,
        "sleeves_source": p.sleeves_source,
        "face_visible": p.face_visible,
        "face_score": p.face_score,
        "face_source": p.face_source,
        "notes": p.notes,
        "created_at": iso(p.created_at),
        "updated_at": iso(p.updated_at),
        "thumb_url": f"/api/photos/{p.id}/thumb",
        "preview_url": f"/api/photos/{p.id}/preview",
        "original_url": f"/api/photos/{p.id}/original",
        "aligned_url": aligned_url,
        "alignment": alignment_out(a) if a is not None else None,
    }


def alignment_out(a: Alignment | None) -> dict[str, Any] | None:
    if a is None:
        return None
    return {
        "photo_id": a.photo_id,
        "reference_photo_id": a.reference_photo_id,
        "pose_category_id": a.pose_category_id,
        "canvas_w": a.canvas_w,
        "canvas_h": a.canvas_h,
        "scale": a.scale,
        "rotation_deg": a.rotation_deg,
        "tx": a.tx,
        "ty": a.ty,
        "method": a.method,
        "scale_basis_used": a.scale_basis_used,
        "flags": a.flags or {},
        "residual_px": a.residual_px,
        "joint_residual_px": a.joint_residual_px,
        "stale": a.stale,
        "has_output": bool(a.output_path) and config.abs_path(a.output_path).exists(),
        "computed_at": iso(a.computed_at),
    }


def category_out(c: PoseCategory, count: int = 0) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "sort_order": c.sort_order,
        "reference_photo_id": c.reference_photo_id,
        "anchor_mode": c.anchor_mode,
        "scale_basis": c.scale_basis,
        "rotation_basis": c.rotation_basis,
        "align_method": c.align_method or "joints",
        "normalize_facing": c.normalize_facing,
        "framing_zoom": c.framing_zoom,
        "framing_dx": c.framing_dx,
        "framing_dy": c.framing_dy,
        "rule_key": c.rule_key,
        "is_builtin": c.rule_key is not None,
        "photo_count": count,
    }


def session_out(s: ImportSession) -> dict[str, Any]:
    return {
        "id": s.id,
        "kind": s.kind,
        "root_path": s.root_path,
        "status": s.status,
        "counts": s.counts or {},
        "options": s.options or {},
        "job_id": s.job_id,
        "created_at": iso(s.created_at),
        "finished_at": iso(s.finished_at),
    }


def candidate_out(c: ImportCandidate) -> dict[str, Any]:
    return {
        "id": c.id,
        "session_id": c.session_id,
        "file_path": c.file_path,
        "file_name": c.file_name,
        "sha256": c.sha256,
        "source_date": iso(c.source_date),
        "date_source": c.date_source,
        "media_type": c.media_type,
        "clip_score": c.clip_score,
        "pose_score": c.pose_score,
        "score": c.score,
        "decision": c.decision,
        "decided_by": c.decided_by,
        "photo_id": c.photo_id,
        "duplicate_of_photo_id": c.duplicate_of_photo_id,
        "error": c.error,
        "created_at": iso(c.created_at),
        "decided_at": iso(c.decided_at),
        "thumb_url": f"/api/imports/candidates/{c.id}/thumb" if c.media_type == "image" else None,
    }


class PhotoPatch(BaseModel):
    taken_at: datetime | None = None
    clear_date: bool = False
    flip_horizontal: bool | None = None
    pose_category_id: int | None = None
    clear_pose: bool = False
    excluded: bool | None = None
    abs_visible: bool | None = None  # manual override
    abs_auto: bool = False  # drop the manual override, go back to detection
    sleeves: bool | None = None  # manual override: True = clothing covers the arms
    sleeves_auto: bool = False
    face_visible: bool | None = None  # manual override for the Face group
    face_auto: bool = False
    notes: str | None = None


class BulkPatch(BaseModel):
    ids: list[int]
    patch: PhotoPatch | None = None
    date_shift_days: float | None = None
    rederive_date_from: str | None = None  # exif|filename|mtime|auto
    toggle_flip: bool = False
    accept_pose: bool = False  # confirm each photo's current pose (same as picking it by hand)
    delete: bool = False


class CategoryIn(BaseModel):
    name: str | None = None
    sort_order: int | None = None
    anchor_mode: str | None = None
    scale_basis: str | None = None
    rotation_basis: str | None = None
    align_method: str | None = None
    normalize_facing: bool | None = None
    framing_zoom: float | None = None
    framing_dx: float | None = None
    framing_dy: float | None = None


class FolderImportIn(BaseModel):
    path: str
    recursive: bool = True


class PathIn(BaseModel):
    path: str
