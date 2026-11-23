"""Library: list, inspect, edit, bulk-edit and delete photos; serve their files."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import String, cast, func, or_
from sqlmodel import Session, select

from .. import config
from ..db import get_session
from ..jobs import job_to_dict, runner
from ..models import Alignment, Photo, PoseCategory, PoseFeatures, PoseLabel, now
from ..schemas import BulkPatch, PhotoPatch, alignment_out, photo_out
from ..services import dates, images, ingest
from ..settings import load_settings

router = APIRouter(prefix="/photos", tags=["photos"])

IMMUTABLE = {"Cache-Control": "private, max-age=31536000, immutable"}
MEDIA_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "heic": "image/heic",
    "heif": "image/heif",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "avif": "image/avif",
}

SORTS = {
    "taken_at_desc": lambda: (Photo.taken_at.is_(None), Photo.taken_at.desc(), Photo.id.desc()),
    "taken_at_asc": lambda: (Photo.taken_at.is_(None), Photo.taken_at.asc(), Photo.id.asc()),
    "created_desc": lambda: (Photo.created_at.desc(), Photo.id.desc()),
    "created_asc": lambda: (Photo.created_at.asc(), Photo.id.asc()),
    "filename": lambda: (Photo.original_filename.asc(), Photo.id.asc()),
}


def _get_photo(s: Session, photo_id: int) -> Photo:
    p = s.get(Photo, photo_id)
    if p is None:
        raise HTTPException(404, "photo not found")
    return p


def _alignments_for(s: Session, ids: list[int]) -> dict[int, Alignment]:
    if not ids:
        return {}
    rows = s.exec(select(Alignment).where(Alignment.photo_id.in_(ids))).all()
    return {a.photo_id: a for a in rows}


@router.get("")
def list_photos(
    s: Session = Depends(get_session),
    pose_id: str | None = None,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    source: str | None = None,
    date_source: str | None = None,
    needs_review: bool | None = None,
    flagged: bool | None = None,
    no_date: bool | None = None,
    excluded: str = "all",
    landmarks: str | None = None,
    abs: str | None = None,
    sleeves: str | None = None,
    face: str | None = None,
    person: str | None = None,
    q: str | None = None,
    sort: str = "taken_at_desc",
    page: int = 1,
    page_size: int = Query(120, le=1000),
) -> dict[str, Any]:
    stmt = select(Photo)
    count_stmt = select(func.count(Photo.id))
    conds = []
    if pose_id:
        if pose_id == "none":
            conds.append(Photo.pose_category_id.is_(None))
        else:
            try:
                conds.append(Photo.pose_category_id == int(pose_id))
            except ValueError:
                raise HTTPException(422, "pose_id must be an id or 'none'") from None
    if from_:
        conds.append(Photo.taken_at >= datetime.combine(from_, datetime.min.time()))
    if to:
        conds.append(Photo.taken_at < datetime.combine(to + timedelta(days=1), datetime.min.time()))
    if source:
        conds.append(Photo.source == source)
    if date_source:
        if date_source == "low_confidence":
            conds.append(Photo.taken_at_confidence == "low")
        else:
            conds.append(Photo.taken_at_source == date_source)
    if needs_review is not None:
        conds.append(Photo.pose_needs_review == needs_review)
    if no_date:
        conds.append(Photo.taken_at.is_(None))
    if excluded == "only":
        conds.append(Photo.excluded == True)  # noqa: E712
    elif excluded == "hide":
        conds.append(Photo.excluded == False)  # noqa: E712
    if landmarks:
        conds.append(Photo.landmarks_status == landmarks)
    if abs == "visible":
        conds.append(Photo.abs_visible == True)  # noqa: E712
    elif abs == "hidden":
        conds.append(Photo.abs_visible == False)  # noqa: E712
    if person == "unscanned":
        conds.append(Photo.person_status.is_(None))
    elif person in ("me", "not_me", "unsure", "no_face"):
        conds.append(Photo.person_status == person)
    if face == "visible":
        conds.append(Photo.face_visible == True)  # noqa: E712
    if sleeves == "hide":  # photos whose arms are not covered (unchecked ones stay)
        conds.append(or_(Photo.sleeves.is_(None), Photo.sleeves == False))  # noqa: E712
    elif sleeves == "only":
        conds.append(Photo.sleeves == True)  # noqa: E712
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(Photo.original_filename.ilike(like), Photo.notes.ilike(like)))
    if flagged is not None:
        flagged_ids = select(Alignment.photo_id).where(
            or_(Alignment.stale == True, cast(Alignment.flags, String).notin_(("{}", "null", "")))  # noqa: E712
        )
        flag_cond = or_(Photo.landmarks_status.in_(("none", "failed")), Photo.id.in_(flagged_ids))
        conds.append(flag_cond if flagged else ~flag_cond)
    for c in conds:
        stmt = stmt.where(c)
        count_stmt = count_stmt.where(c)
    total = s.exec(count_stmt).one()
    order = SORTS.get(sort, SORTS["taken_at_desc"])()
    stmt = stmt.order_by(*order).offset((page - 1) * page_size).limit(page_size)
    rows = s.exec(stmt).all()
    al = _alignments_for(s, [p.id for p in rows])
    return {
        "items": [photo_out(p, al.get(p.id)) for p in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }


@router.get("/{photo_id}")
def get_photo(photo_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    p = _get_photo(s, photo_id)
    a = s.get(Alignment, photo_id)
    out = photo_out(p, a)
    cat = s.get(PoseCategory, p.pose_category_id) if p.pose_category_id else None
    out["pose_category_name"] = cat.name if cat else None
    out["is_reference"] = bool(cat and cat.reference_photo_id == p.id)
    out["has_label"] = s.exec(select(PoseLabel.id).where(PoseLabel.photo_id == p.id)).first() is not None
    out["original_path"] = str(config.abs_path(p.original_path))
    return out


# ----------------------------------------------------------------------------- edits


def _mark_alignment_stale(s: Session, photo_id: int) -> None:
    a = s.get(Alignment, photo_id)
    if a is not None:
        a.stale = True
        s.add(a)


def _upsert_label(s: Session, photo: Photo, category_id: int) -> None:
    feats = s.get(PoseFeatures, photo.id)
    lab = s.exec(select(PoseLabel).where(PoseLabel.photo_id == photo.id)).first()
    if lab is None:
        lab = PoseLabel(photo_id=photo.id, pose_category_id=category_id, vector=feats.vector if feats else [])
    else:
        lab.pose_category_id = category_id
        lab.vector = feats.vector if feats else lab.vector
        lab.created_at = now()
    s.add(lab)


def apply_patch(s: Session, p: Photo, patch: PhotoPatch) -> None:
    changed = False
    if patch.clear_date:
        p.taken_at, p.taken_at_source, p.taken_at_confidence = None, "none", "low"
        changed = True
    elif patch.taken_at is not None:
        p.taken_at = patch.taken_at.replace(tzinfo=None, microsecond=0)
        p.taken_at_source, p.taken_at_confidence = "manual", "high"
        changed = True
    if patch.flip_horizontal is not None and patch.flip_horizontal != p.flip_horizontal:
        p.flip_horizontal = patch.flip_horizontal
        p.flip_source = "manual"
        feats = s.get(PoseFeatures, p.id)
        if feats is not None:
            s.delete(feats)  # recomputed from the cached landmarks by the next classify job
        if p.pose_source != "manual":
            p.pose_confidence = None  # the pose may have changed side: the next classify re-checks it
        _mark_alignment_stale(s, p.id)
        changed = True
    if patch.clear_pose:
        p.pose_category_id, p.pose_confidence, p.pose_source, p.pose_needs_review = None, None, "none", False
        lab = s.exec(select(PoseLabel).where(PoseLabel.photo_id == p.id)).first()
        if lab is not None:
            s.delete(lab)
        _mark_alignment_stale(s, p.id)
        changed = True
    elif patch.pose_category_id is not None:
        if s.get(PoseCategory, patch.pose_category_id) is None:
            raise HTTPException(404, "pose category not found")
        if p.pose_category_id != patch.pose_category_id:
            cat = s.get(PoseCategory, p.pose_category_id) if p.pose_category_id else None
            if cat is not None and cat.reference_photo_id == p.id:
                cat.reference_photo_id = None
                s.add(cat)
            _mark_alignment_stale(s, p.id)
        p.pose_category_id = patch.pose_category_id
        p.pose_confidence, p.pose_source, p.pose_needs_review = 1.0, "manual", False
        _upsert_label(s, p, patch.pose_category_id)
        changed = True
    if patch.excluded is not None:
        p.excluded = patch.excluded
        changed = True
    if patch.abs_auto:
        p.abs_source = "auto" if p.abs_score is not None else "none"
        if p.abs_score is not None:
            p.abs_visible = p.abs_score >= load_settings(s).abs_threshold
        changed = True
    elif patch.abs_visible is not None:
        p.abs_visible, p.abs_source = patch.abs_visible, "manual"
        changed = True
    if patch.sleeves_auto:
        p.sleeves_source = "auto" if p.sleeves_score is not None else "none"
        p.sleeves = (p.sleeves_score < load_settings(s).sleeves_threshold) if p.sleeves_score is not None else None
        changed = True
    elif patch.sleeves is not None:
        p.sleeves, p.sleeves_source = patch.sleeves, "manual"
        changed = True
    if patch.face_auto:
        p.face_source = "auto" if p.face_score is not None else "none"
        p.face_visible = (p.face_score >= load_settings(s).face_threshold) if p.face_score is not None else None
        changed = True
    elif patch.face_visible is not None:
        p.face_visible, p.face_source = patch.face_visible, "manual"
        changed = True
    if changed and p.face_visible and (patch.face_visible or patch.face_auto or patch.flip_horizontal is not None):
        from ..services.face_view import ensure_closeup as ensure_face

        try:
            ensure_face(s, p)
        except Exception:  # the close-up is a convenience; the flag itself is saved regardless
            pass
    if changed and p.abs_visible and (patch.abs_visible or patch.abs_auto or patch.flip_horizontal is not None):
        from ..services.abs_view import ensure_closeup

        try:
            ensure_closeup(s, p)
        except Exception:  # the close-up is a convenience; the flag itself is saved regardless
            pass
    if patch.notes is not None:
        p.notes = patch.notes or None
        changed = True
    if changed:
        p.updated_at = now()
        s.add(p)


@router.patch("/{photo_id}")
def patch_photo(photo_id: int, patch: PhotoPatch, s: Session = Depends(get_session)) -> dict[str, Any]:
    p = _get_photo(s, photo_id)
    apply_patch(s, p, patch)
    s.commit()
    s.refresh(p)
    return photo_out(p, s.get(Alignment, photo_id))


def _rederive(p: Photo, how: str) -> dates.DateResult:
    src = config.abs_path(p.original_path)
    exif: dict = {}
    if how in ("exif", "auto") and src.exists():
        try:
            from PIL import Image

            with Image.open(src) as im:
                exif = images.read_exif(im)
        except Exception:
            exif = {}
    mtime = os.path.getmtime(src) if src.exists() else None
    if how == "exif":
        dt = dates.date_from_exif(exif)
        return dates.DateResult(dt, "exif", "high") if dt else dates.DateResult.none()
    if how == "filename":
        dt = dates.date_from_filename(p.original_filename)
        return dates.DateResult(dt, "filename", "medium") if dt else dates.DateResult.none()
    if how == "mtime":
        dt = dates.date_from_mtime(mtime)
        return dates.DateResult(dt, "file_mtime", "low") if dt else dates.DateResult.none()
    return dates.resolve_date(exif=exif, filename=p.original_filename, mtime=mtime)


@router.post("/bulk")
def bulk(body: BulkPatch, s: Session = Depends(get_session)) -> dict[str, Any]:
    photos = s.exec(select(Photo).where(Photo.id.in_(body.ids))).all()
    touched, skipped = 0, 0
    for p in photos:
        if body.delete:
            ingest.delete_photo(s, p)
            touched += 1
            continue
        if body.patch is not None:
            apply_patch(s, p, body.patch)
        if body.accept_pose:
            if p.pose_category_id is None:
                skipped += 1
            else:
                apply_patch(s, p, PhotoPatch(pose_category_id=p.pose_category_id))
        if body.toggle_flip:
            apply_patch(s, p, PhotoPatch(flip_horizontal=not p.flip_horizontal))
        if body.date_shift_days:
            if p.taken_at is None:
                skipped += 1
            else:
                p.taken_at = p.taken_at + timedelta(days=body.date_shift_days)
                p.taken_at_source, p.taken_at_confidence = "manual", "high"
        if body.rederive_date_from:
            dr = _rederive(p, body.rederive_date_from)
            if dr.taken_at is None:
                skipped += 1
            else:
                p.taken_at, p.taken_at_source, p.taken_at_confidence = dr.taken_at, dr.source, dr.confidence
        p.updated_at = now()
        s.add(p)
        touched += 1
    s.commit()
    return {"updated": touched, "skipped": skipped}


@router.delete("/{photo_id}")
def delete_photo(photo_id: int, forget: bool = False, s: Session = Depends(get_session)) -> dict[str, Any]:
    p = _get_photo(s, photo_id)
    ingest.delete_photo(s, p, remember=not forget)
    return {"deleted": photo_id}


@router.post("/{photo_id}/reprocess")
def reprocess(photo_id: int, body: dict, s: Session = Depends(get_session)) -> dict[str, Any]:
    _get_photo(s, photo_id)
    what = body.get("what", "landmarks")
    type_ = {"landmarks": "extract_landmarks", "align": "align_photos"}.get(what)
    if type_ is None:
        raise HTTPException(422, "what must be landmarks or align")
    try:
        j = runner().enqueue(type_, {"ids": [photo_id], "force": True}, dedupe=False)
    except KeyError:
        raise HTTPException(501, f"{what} is not available yet") from None
    return job_to_dict(j)


# ----------------------------------------------------------------------------- files


def _file(path, media_type: str | None = None, headers: dict | None = None, filename: str | None = None):
    if not path.exists():
        raise HTTPException(404, "file missing on disk")
    return FileResponse(path, media_type=media_type, headers=headers or {}, filename=filename)


@router.get("/{photo_id}/thumb")
def thumb(photo_id: int, s: Session = Depends(get_session)):
    _get_photo(s, photo_id)
    return _file(config.THUMBS_DIR / f"{photo_id}.jpg", "image/jpeg", IMMUTABLE)


@router.get("/{photo_id}/preview")
def preview(photo_id: int, s: Session = Depends(get_session)):
    _get_photo(s, photo_id)
    return _file(config.PREVIEWS_DIR / f"{photo_id}.jpg", "image/jpeg", IMMUTABLE)


@router.get("/{photo_id}/original")
def original(photo_id: int, download: bool = False, as_jpeg: bool = False, s: Session = Depends(get_session)):
    """The original file, byte-identical. `as_jpeg` (or a HEIC/HEIF original, which most browsers
    can't show) gives a full-resolution JPEG copy instead, upright, with the EXIF date kept."""
    p = _get_photo(s, photo_id)
    if as_jpeg or p.ext.lower() in ("heic", "heif"):
        from ..services.images import fullres_jpeg

        path = fullres_jpeg(p)
        name = Path(p.original_filename).with_suffix(".jpg").name if download else None
        return _file(path, "image/jpeg", IMMUTABLE, filename=name)
    return _file(
        config.abs_path(p.original_path),
        MEDIA_TYPES.get(p.ext, "application/octet-stream"),
        IMMUTABLE,
        filename=p.original_filename if download else None,
    )


@router.get("/{photo_id}/aligned")
def aligned(photo_id: int, s: Session = Depends(get_session)):
    a = s.get(Alignment, photo_id)
    if a is None or not a.output_path:
        raise HTTPException(404, "not aligned yet")
    return _file(config.abs_path(a.output_path), "image/jpeg", {"Cache-Control": "private, max-age=31536000"})


@router.get("/{photo_id}/face")
def face_closeup(photo_id: int, s: Session = Depends(get_session)):
    from ..services.face_view import face_path

    _get_photo(s, photo_id)
    return _file(face_path(photo_id), "image/jpeg", {"Cache-Control": "private, max-age=31536000"})


@router.get("/{photo_id}/abs")
def abs_closeup(photo_id: int, s: Session = Depends(get_session)):
    from ..services.abs_view import abs_path

    _get_photo(s, photo_id)
    return _file(abs_path(photo_id), "image/jpeg", {"Cache-Control": "private, max-age=31536000"})


@router.get("/{photo_id}/landmarks")
def landmarks(photo_id: int, s: Session = Depends(get_session)):
    p = _get_photo(s, photo_id)
    path = config.LANDMARKS_DIR / f"{photo_id}.json"
    if not path.exists():
        raise HTTPException(404, "no landmarks")
    data = json.loads(path.read_text())
    try:
        from ..services import landmarks as lm

        data = lm.apply_flip(data, p.flip_horizontal)
    except ImportError:
        pass
    data["flip_horizontal"] = p.flip_horizontal
    return JSONResponse(data)


@router.get("/{photo_id}/alignment")
def get_alignment(photo_id: int, s: Session = Depends(get_session)):
    _get_photo(s, photo_id)
    return alignment_out(s.get(Alignment, photo_id))
