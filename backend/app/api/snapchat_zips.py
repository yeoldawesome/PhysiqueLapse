"""Snapchat export zips: collect zips (browser upload or path on disk), then unpack + filter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlmodel import Session, select

from ..db import get_session
from ..fsutil import replace_retry
from ..jobs import job_to_dict, runner
from ..models import ImportCandidate, ImportSession
from ..schemas import PathIn, session_out
from ..services import snapchat_zip as SZ
from ..settings import save_settings

router = APIRouter(prefix="/snapchat", tags=["snapchat"])

_SAFE = re.compile(r"[^\w.\-~ ()]+")


def _sess(s: Session, sid: int, *, editable: bool = False) -> ImportSession:
    sess = s.get(ImportSession, sid)
    if sess is None or sess.kind != "snapchat":
        raise HTTPException(404, "Snapchat import not found")
    if editable and sess.status in ("queued", "running"):
        raise HTTPException(409, "this import is running")
    return sess


def _out(s: Session, sess: ImportSession) -> dict[str, Any]:
    o = session_out(sess)
    zips = []
    done = set((sess.options or {}).get("extracted_zips", []))
    for z in (sess.options or {}).get("zips", []):
        zips.append({**z, "extracted": z["path"] in done, "present": Path(z["path"]).exists()})
    o["zips"] = zips
    st = SZ.staging_dir(sess.id)
    o["staging_bytes"] = sum(p.stat().st_size for p in st.rglob("*") if p.is_file()) if st.exists() else 0
    decisions = dict(
        s.exec(
            select(ImportCandidate.decision, func.count(ImportCandidate.id))
            .where(ImportCandidate.session_id == sess.id)
            .group_by(ImportCandidate.decision)
        ).all()
    )
    if sess.status in ("queued", "running"):
        decisions.pop("review", None)
    o["decisions"] = decisions
    return o


def _add_zip(sess: ImportSession, path: Path, uploaded: bool) -> dict[str, Any]:
    info = SZ.zip_summary(path)
    if info["error"]:
        if uploaded:
            path.unlink(missing_ok=True)
        raise HTTPException(422, f"{path.name}: {info['error']}")
    opts = dict(sess.options or {})
    zips = [z for z in opts.get("zips", []) if z["path"] != str(path)]
    zips.append({"path": str(path), "name": path.name, "uploaded": uploaded, "bytes": info["bytes"],
                 "photos": info["photos"], "videos": info["videos"], "nested_zips": info["nested_zips"],
                 "has_metadata": info["has_metadata"]})
    opts["zips"] = zips
    sess.options = opts
    return info


@router.post("/zip-session")
def create(s: Session = Depends(get_session)) -> dict[str, Any]:
    # reuse an unfinished zip session so a page reload doesn't orphan uploads
    sess = s.exec(
        select(ImportSession)
        .where(ImportSession.kind == "snapchat", ImportSession.status == "open")
        .order_by(ImportSession.id.desc())
    ).first()
    if sess is None:
        sess = ImportSession(kind="snapchat", status="open", counts={}, options={"zips": [], "source": "zips"})
        s.add(sess)
        s.commit()
        s.refresh(sess)
        sess.root_path = str(SZ.extracted_dir(sess.id))
        s.add(sess)
        s.commit()
    return _out(s, sess)


@router.get("/zip-session/{sid}")
def get(sid: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    return _out(s, _sess(s, sid))


@router.put("/zip-session/{sid}/upload")
async def upload(sid: int, request: Request, name: str = Query(...), s: Session = Depends(get_session)) -> dict[str, Any]:
    """Raw request body = the zip file. Streamed to disk (no temp copy), so multi-GB zips are fine."""
    sess = _sess(s, sid, editable=True)
    base = _SAFE.sub("_", Path(name).name)[:150] or "snapchat.zip"
    if not base.lower().endswith(".zip"):
        raise HTTPException(422, "only .zip files")
    d = SZ.staging_dir(sid) / "zips"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / base
    i = 1
    while dest.exists():
        dest = d / f"{Path(base).stem}_{i}.zip"
        i += 1
    tmp = dest.with_suffix(".zip.part")
    n = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
                n += len(chunk)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    if n == 0:
        tmp.unlink(missing_ok=True)
        raise HTTPException(422, "empty upload")
    replace_retry(tmp, dest)
    info = _add_zip(sess, dest, uploaded=True)
    s.add(sess)
    s.commit()
    return info


@router.post("/zip-session/{sid}/add-path")
def add_path(sid: int, body: PathIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    """A zip file, or a folder: every .zip directly inside it is added."""
    sess = _sess(s, sid, editable=True)
    p = Path(body.path.strip().strip('"')).expanduser()
    if p.is_dir():
        found = sorted(q for q in p.iterdir() if q.is_file() and q.suffix.lower() == ".zip")
        if not found:
            raise HTTPException(404, f"no .zip files in {p}")
    elif p.is_file():
        found = [p]
    else:
        raise HTTPException(404, f"not found: {p}")
    errors = []
    for q in found:
        try:
            _add_zip(sess, q, uploaded=False)
        except HTTPException as e:
            errors.append(e.detail)
    s.add(sess)
    s.commit()
    out = _out(s, sess)
    out["errors"] = errors
    return out


@router.delete("/zip-session/{sid}/zips")
def remove_zip(sid: int, path: str, s: Session = Depends(get_session)) -> dict[str, Any]:
    sess = _sess(s, sid, editable=True)
    opts = dict(sess.options or {})
    keep = [z for z in opts.get("zips", []) if z["path"] != path]
    gone = next((z for z in opts.get("zips", []) if z["path"] == path), None)
    if gone and gone.get("uploaded"):
        Path(gone["path"]).unlink(missing_ok=True)
    opts["zips"] = keep
    sess.options = opts
    s.add(sess)
    s.commit()
    return _out(s, sess)


@router.post("/zip-session/{sid}/start")
def start(sid: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    sess = _sess(s, sid, editable=True)
    if not (sess.options or {}).get("zips"):
        raise HTTPException(422, "add at least one zip first")
    missing = [z["name"] for z in sess.options["zips"] if not Path(z["path"]).exists() and z["path"] not in sess.options.get("extracted_zips", [])]
    if missing:
        raise HTTPException(410, f"zip files are gone: {', '.join(missing)}")
    sess.status = "queued"
    opts = dict(sess.options or {})
    opts["auto_pipeline"] = True
    sess.options = opts
    s.add(sess)
    save_settings(s, {"pose_setup_done": True, "pose_setup_skipped": []})
    s.commit()
    j = runner().enqueue("snapchat_zip_import", {"session_id": sid}, dedupe=True)
    sess.job_id = j.id
    s.add(sess)
    s.commit()
    return {"session": _out(s, sess), "job": job_to_dict(j)}


@router.get("/zip-sessions")
def list_sessions(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = s.exec(select(ImportSession).where(ImportSession.kind == "snapchat").order_by(ImportSession.id.desc()).limit(20)).all()
    return [_out(s, r) for r in rows]


@router.delete("/zip-session/{sid}/staging")
def cleanup(sid: int, force: bool = False, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Delete the extracted photos once review is finished (accepted ones are already copied into the library)."""
    sess = _sess(s, sid, editable=True)
    pending = s.exec(
        select(func.count(ImportCandidate.id)).where(ImportCandidate.session_id == sid, ImportCandidate.decision.in_(("review", "pending")))
    ).one()
    if pending and not force:
        raise HTTPException(409, f"{pending} photos are still waiting for review; deleting now makes them impossible to accept")
    n = SZ.delete_staging(sid)
    return {"deleted_files": n, "session": _out(s, sess)}
