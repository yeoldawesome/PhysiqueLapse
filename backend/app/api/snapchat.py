"""Snapchat scan/import and the review queue."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from ..db import get_session
from ..jobs import job_to_dict, runner
from ..models import ImportCandidate, ImportSession, Photo, now
from ..schemas import PathIn, candidate_out, session_out
from ..services import ingest, snapchat
from ..settings import load_settings

router = APIRouter(tags=["snapchat"])


@router.post("/snapchat/scan")
def scan(body: PathIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    root = Path(body.path.strip().strip('"')).expanduser()
    if not root.is_dir():
        raise HTTPException(404, f"folder not found: {root}")
    return snapchat.scan_summary(s, root)


@router.post("/snapchat/import")
def start_import(body: PathIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    root = Path(body.path.strip().strip('"')).expanduser()
    if not root.is_dir():
        raise HTTPException(404, f"folder not found: {root}")
    settings = load_settings(s)
    sess = s.exec(
        select(ImportSession).where(ImportSession.kind == "snapchat", ImportSession.root_path == str(root)).order_by(ImportSession.id.desc())
    ).first()
    if sess is not None and sess.status in ("queued", "running"):
        raise HTTPException(409, "this export is already being imported")
    if sess is None:
        sess = ImportSession(kind="snapchat", root_path=str(root), counts={}, options={})
    # resuming reuses the session: candidates already scored keep their decisions
    sess.status = "queued"
    sess.options = {"accept": settings.filter_accept, "reject": settings.filter_reject}
    s.add(sess)
    s.commit()
    s.refresh(sess)
    j = runner().enqueue("snapchat_import", {"session_id": sess.id}, dedupe=True)
    sess.job_id = j.id
    s.add(sess)
    s.commit()
    return {"session": session_out(sess), "job": job_to_dict(j)}


# ----------------------------------------------------------------------------- review queue


class DecisionIn(BaseModel):
    decision: str  # accept|reject


class BulkDecisionIn(BaseModel):
    ids: list[int] | None = None
    decision: str
    session_id: int | None = None
    min_score: float | None = None  # "accept all above X" (applies to the review queue)
    max_score: float | None = None
    all_review: bool = False


def _apply(s: Session, cand: ImportCandidate, decision: str) -> dict[str, Any]:
    if decision not in ("accept", "reject"):
        raise HTTPException(422, "decision must be accept or reject")
    sess = s.get(ImportSession, cand.session_id) if cand.session_id else None
    if sess is not None and sess.status in ("queued", "running"):
        raise HTTPException(409, "this import is still processing")
    prev = cand.decision
    if decision == "accept":
        if cand.photo_id and s.get(Photo, cand.photo_id):
            return candidate_out(cand)
        settings = load_settings(s)
        src = Path(cand.file_path)
        if not src.exists():
            raise HTTPException(410, f"source file is gone: {src}")
        r = ingest.ingest_file(
            s, src, source="snapchat", source_ref=sess.root_path if sess else None, session_id=cand.session_id,
            settings=settings, index=ingest.load_phash_index(s), display_name=cand.file_name,
            snapchat_date=cand.source_date, snapchat_confidence="high" if cand.date_source == "snapchat_metadata" else "medium",
            force=True,
        )
        cand = s.get(ImportCandidate, cand.id)
        if r.status not in ("accepted", "duplicate"):
            raise HTTPException(500, f"import failed: {r.message}")
        cand.decision = "accepted"
    else:
        if cand.photo_id and cand.decision in ("accepted", "auto_accept"):
            p = s.get(Photo, cand.photo_id)
            if p is not None:
                ingest.delete_photo(s, p, remember=True)
                cand = s.get(ImportCandidate, cand.id)
        cand.decision = "rejected"
        cand.photo_id = None
    cand.prev_decision = prev
    cand.decided_by = "user"
    cand.decided_at = now()
    s.add(cand)
    s.commit()
    s.refresh(cand)
    return candidate_out(cand)


def _review_ready():
    active_sessions = select(ImportSession.id).where(ImportSession.status.in_(("queued", "running")))
    return ImportCandidate.session_id.is_(None) | ~ImportCandidate.session_id.in_(active_sessions)


@router.get("/review")
def review_list(
    session_id: int | None = None,
    decision: str = "review",
    min_score: float | None = None,
    max_score: float | None = None,
    page: int = 1,
    page_size: int = 100,
    s: Session = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(ImportCandidate).where(ImportCandidate.decision.in_(decision.split(",")), _review_ready())
    cnt = select(func.count(ImportCandidate.id)).where(ImportCandidate.decision.in_(decision.split(",")), _review_ready())
    for cond in (
        ImportCandidate.session_id == session_id if session_id else None,
        ImportCandidate.score >= min_score if min_score is not None else None,
        ImportCandidate.score < max_score if max_score is not None else None,
    ):
        if cond is not None:
            stmt, cnt = stmt.where(cond), cnt.where(cond)
    total = s.exec(cnt).one()
    rows = s.exec(stmt.order_by(ImportCandidate.score.desc(), ImportCandidate.id).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [candidate_out(c) for c in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/review/histogram")
def histogram(session_id: int | None = None, bins: int = 20, s: Session = Depends(get_session)) -> dict[str, Any]:
    stmt = select(ImportCandidate.score).where(ImportCandidate.score.is_not(None), _review_ready())
    if session_id:
        stmt = stmt.where(ImportCandidate.session_id == session_id)
    scores = [x for x in s.exec(stmt).all()]
    counts = [0] * bins
    for sc in scores:
        counts[min(bins - 1, max(0, int(sc * bins)))] += 1
    settings = load_settings(s)
    return {"bins": bins, "counts": counts, "total": len(scores), "accept": settings.filter_accept, "reject": settings.filter_reject}


@router.post("/review/bulk")
def review_bulk(body: BulkDecisionIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    if body.ids:
        cands = s.exec(select(ImportCandidate).where(ImportCandidate.id.in_(body.ids), _review_ready())).all()
    elif body.min_score is not None:
        stmt = select(ImportCandidate).where(ImportCandidate.decision == "review", ImportCandidate.score >= body.min_score, _review_ready())
        if body.session_id:
            stmt = stmt.where(ImportCandidate.session_id == body.session_id)
        if body.max_score is not None:
            stmt = stmt.where(ImportCandidate.score < body.max_score)
        cands = s.exec(stmt).all()
    elif body.all_review:
        stmt = select(ImportCandidate).where(ImportCandidate.decision == "review", _review_ready())
        if body.session_id:
            stmt = stmt.where(ImportCandidate.session_id == body.session_id)
        if body.max_score is not None:
            stmt = stmt.where(ImportCandidate.score < body.max_score)
        cands = s.exec(stmt).all()
    else:
        raise HTTPException(422, "send ids, min_score, or all_review")
    done, errors = 0, []
    for c in cands:
        try:
            _apply(s, c, body.decision)
            done += 1
        except HTTPException as e:
            errors.append(f"{c.file_name}: {e.detail}")
    return {"updated": done, "errors": errors[:20]}


@router.post("/review/undo")
def review_undo(s: Session = Depends(get_session)) -> dict[str, Any]:
    c = s.exec(
        select(ImportCandidate)
        .where(ImportCandidate.decided_by == "user", ImportCandidate.prev_decision.is_not(None))
        .order_by(ImportCandidate.decided_at.desc(), ImportCandidate.id.desc())
    ).first()
    if c is None:
        raise HTTPException(404, "nothing to undo")
    if c.decision == "accepted" and c.photo_id:
        p = s.get(Photo, c.photo_id)
        if p is not None:
            ingest.delete_photo(s, p, remember=True)
            c = s.get(ImportCandidate, c.id)
    target = c.prev_decision or "review"
    if target in ("accepted", "auto_accept"):
        out = _apply(s, c, "accept")  # bring the photo back
        c = s.get(ImportCandidate, c.id)
    else:
        c.photo_id = None
    c.decision = target
    c.prev_decision = None
    c.decided_by = "auto"
    s.add(c)
    s.commit()
    s.refresh(c)
    out = candidate_out(c)
    return out


@router.post("/review/{candidate_id}")
def review_one(candidate_id: int, body: DecisionIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    c = s.get(ImportCandidate, candidate_id)
    if c is None:
        raise HTTPException(404, "candidate not found")
    return _apply(s, c, body.decision)
