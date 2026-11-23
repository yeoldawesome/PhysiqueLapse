"""Uploads (browser), folder imports (server-side path) and import history."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import string
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import config
from ..db import get_session
from ..jobs import job_to_dict, runner
from ..models import ImportCandidate, ImportSession, UploadItem, now
from ..schemas import FolderImportIn, PathIn, candidate_out, session_out
from ..services import images

router = APIRouter(prefix="/imports", tags=["imports"])

_SAFE_PART = re.compile(r"[^\w.\- ()\[\]&+,'@]+", re.UNICODE)


def _safe_relpath(raw: str | None, fallback: str) -> Path:
    raw = (raw or fallback or "file").replace("\\", "/")
    parts = [p for p in raw.split("/") if p not in ("", ".", "..")]
    parts = [_SAFE_PART.sub("_", p)[:120] or "_" for p in parts]
    return Path(*parts) if parts else Path(_SAFE_PART.sub("_", fallback) or "file")


def require_import_path(path: Path) -> None:
    """Server mode: reading server folders is limited to PL_IMPORT_ROOTS."""
    if not config.path_import_allowed(path):
        if not config.IMPORT_ROOTS:
            raise HTTPException(403, "Importing from server folders is off. Upload from the browser instead.")
        raise HTTPException(403, "That folder is outside the allowed import folders (PL_IMPORT_ROOTS).")


# ----------------------------------------------------------------------------- uploads


@router.post("/upload-session")
def create_upload_session(s: Session = Depends(get_session)) -> dict[str, Any]:
    sess = ImportSession(kind="upload", status="open", counts={}, options={})
    s.add(sess)
    s.commit()
    s.refresh(sess)
    (config.INCOMING_DIR / str(sess.id)).mkdir(parents=True, exist_ok=True)
    return session_out(sess)


@router.post("/upload-session/{session_id}/files")
async def upload_files(
    session_id: int,
    files: list[UploadFile] = File(...),
    paths: list[str] | None = Form(None),
    last_modified: list[int] | None = Form(None),
    s: Session = Depends(get_session),
) -> dict[str, Any]:
    sess = s.get(ImportSession, session_id)
    if sess is None or sess.kind != "upload":
        raise HTTPException(404, "upload session not found")
    if sess.status != "open":
        raise HTTPException(409, "upload session already committed")
    staging = config.INCOMING_DIR / str(session_id)
    staging.mkdir(parents=True, exist_ok=True)
    staged = 0
    for i, up in enumerate(files):
        relp = _safe_relpath(paths[i] if paths and i < len(paths) else None, up.filename or f"file{i}")
        if config.SERVER and not images.is_image(relp):
            continue  # server: images only (videos and other files would be skipped by ingest anyway)
        dest = staging / relp
        dest.parent.mkdir(parents=True, exist_ok=True)
        n = 1
        while dest.exists():
            dest = dest.with_name(f"{relp.stem}_{n}{relp.suffix}")
            n += 1
        cap, written = config.MAX_UPLOAD_MB << 20, 0
        with open(dest, "wb") as f:
            while True:
                chunk = await up.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if config.SERVER and written > cap:
                    break
                f.write(chunk)
        if config.SERVER and written > cap:
            dest.unlink(missing_ok=True)
            raise HTTPException(413, f"{relp.name} is larger than {config.MAX_UPLOAD_MB} MB")
        if last_modified and i < len(last_modified) and last_modified[i]:
            try:
                ts = last_modified[i] / 1000.0
                os.utime(dest, (ts, ts))
            except OSError:
                pass
        staged += 1
    counts = dict(sess.counts or {})
    counts["staged"] = counts.get("staged", 0) + staged
    sess.counts = counts
    s.add(sess)
    s.commit()
    return {"staged": staged, "total_staged": counts["staged"]}


# ----------------------------------------------------------------------------- resumable uploads
#
# Phones on gym Wi-Fi drop connections, so big batches go up in chunks:
#   POST .../files/init {rel_path, size, last_modified}  -> {id, received}  (same file again = resume)
#   PUT  .../files/{id}?offset=N  <raw bytes>             offset must equal `received`
#   GET  .../files/{id}                                   -> {received, status}
# A file is moved into the staging tree when its last byte arrives; commit as before.


class UploadInitIn(BaseModel):
    rel_path: str
    size: int
    last_modified: int = 0


def _open_session(s: Session, session_id: int) -> ImportSession:
    sess = s.get(ImportSession, session_id)
    if sess is None or sess.kind != "upload":
        raise HTTPException(404, "upload session not found")
    if sess.status != "open":
        raise HTTPException(409, "upload session already committed")
    return sess


def _item_out(it: UploadItem) -> dict[str, Any]:
    return {"id": it.id, "rel_path": it.rel_path, "size": it.size, "received": it.received, "status": it.status,
            "chunk_size": config.UPLOAD_CHUNK_MB << 20}


def _parts_dir(session_id: int) -> Path:
    return config.INCOMING_DIR / str(session_id) / ".parts"


@router.get("/upload-session/{session_id}")
def upload_session_status(session_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    sess = s.get(ImportSession, session_id)
    if sess is None or sess.kind != "upload":
        raise HTTPException(404, "upload session not found")
    items = s.exec(select(UploadItem).where(UploadItem.session_id == session_id)).all()
    return {"session": session_out(sess), "files": [_item_out(i) for i in items]}


@router.post("/upload-session/{session_id}/files/init")
def upload_init(session_id: int, body: UploadInitIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    _open_session(s, session_id)
    relp = _safe_relpath(body.rel_path, "file")
    if not images.is_image(relp):
        raise HTTPException(415, f"not an image file: {relp.name}")
    if body.size <= 0:
        raise HTTPException(422, "empty file")
    if body.size > config.MAX_UPLOAD_MB << 20:
        raise HTTPException(413, f"{relp.name} is larger than {config.MAX_UPLOAD_MB} MB")
    rel = relp.as_posix()
    existing = s.exec(
        select(UploadItem).where(
            UploadItem.session_id == session_id, UploadItem.rel_path == rel,
            UploadItem.size == body.size, UploadItem.last_modified_ms == body.last_modified,
        )
    ).first()
    if existing is not None:  # resume (or already complete)
        part = _parts_dir(session_id) / existing.id
        if existing.status == "open" and (not part.exists() or part.stat().st_size != existing.received):
            existing.received = part.stat().st_size if part.exists() else 0  # trust the disk
            s.add(existing)
            s.commit()
        return _item_out(existing)
    it = UploadItem(id=secrets.token_hex(16), session_id=session_id, rel_path=rel, size=body.size, last_modified_ms=body.last_modified)
    s.add(it)
    s.commit()
    return _item_out(it)


@router.get("/upload-session/{session_id}/files/{item_id}")
def upload_item_status(session_id: int, item_id: str, s: Session = Depends(get_session)) -> dict[str, Any]:
    it = s.get(UploadItem, item_id)
    if it is None or it.session_id != session_id:
        raise HTTPException(404, "upload not found")
    return _item_out(it)


@router.put("/upload-session/{session_id}/files/{item_id}")
async def upload_chunk(session_id: int, item_id: str, offset: int, request: Request, s: Session = Depends(get_session)) -> dict[str, Any]:
    sess = _open_session(s, session_id)
    it = s.get(UploadItem, item_id)
    if it is None or it.session_id != session_id:
        raise HTTPException(404, "upload not found")
    if it.status == "complete":
        return _item_out(it)
    if offset != it.received:
        raise HTTPException(409, f"offset {offset} != received {it.received}")
    limit = min(it.size - it.received, (config.UPLOAD_CHUNK_MB << 20) * 2)
    parts = _parts_dir(session_id)
    parts.mkdir(parents=True, exist_ok=True)
    part = parts / it.id
    got = 0
    with open(part, "ab") as f:
        f.truncate(it.received)  # drop bytes of an earlier chunk that never got acknowledged
        async for chunk in request.stream():
            got += len(chunk)
            if got > limit:
                f.truncate(it.received)
                raise HTTPException(413, "chunk too large")
            f.write(chunk)
    it.received += got
    it.updated_at = now()
    if it.received == it.size:
        dest = config.INCOMING_DIR / str(session_id) / it.rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        n = 1
        while dest.exists():
            dest = dest.with_name(f"{Path(it.rel_path).stem}_{n}{Path(it.rel_path).suffix}")
            n += 1
        os.replace(part, dest)
        if it.last_modified_ms:
            try:
                ts = it.last_modified_ms / 1000.0
                os.utime(dest, (ts, ts))
            except OSError:
                pass
        it.status = "complete"
        counts = dict(sess.counts or {})
        counts["staged"] = counts.get("staged", 0) + 1
        sess.counts = counts
        s.add(sess)
    s.add(it)
    s.commit()
    return _item_out(it)


@router.post("/upload-session/{session_id}/commit")
def commit_upload(session_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    sess = s.get(ImportSession, session_id)
    if sess is None or sess.kind != "upload":
        raise HTTPException(404, "upload session not found")
    if sess.status not in ("open", "failed", "cancelled"):
        raise HTTPException(409, f"session is {sess.status}")
    sess.status = "queued"
    s.add(sess)
    s.commit()
    j = runner().enqueue("ingest_upload", {"session_id": session_id}, dedupe=True)
    sess.job_id = j.id
    s.add(sess)
    s.commit()
    return {"session": session_out(sess), "job": job_to_dict(j)}


@router.delete("/upload-session/{session_id}")
def abandon_upload(session_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    sess = s.get(ImportSession, session_id)
    if sess is None or sess.kind != "upload":
        raise HTTPException(404, "upload session not found")
    if sess.status != "open":
        raise HTTPException(409, "session already committed")
    shutil.rmtree(config.INCOMING_DIR / str(session_id), ignore_errors=True)
    for it in s.exec(select(UploadItem).where(UploadItem.session_id == session_id)).all():
        s.delete(it)
    s.delete(sess)
    s.commit()
    return {"deleted": session_id}


# ----------------------------------------------------------------------------- folders


@router.post("/folder")
def import_folder(body: FolderImportIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    root = Path(body.path).expanduser()
    require_import_path(root)
    if not root.is_dir():
        raise HTTPException(404, f"folder not found: {root}")
    try:
        root.resolve().relative_to(config.DATA_DIR.resolve())
        raise HTTPException(422, "cannot import from the app's own data folder")
    except ValueError:
        pass
    sess = ImportSession(kind="folder", root_path=str(root), status="queued", counts={}, options={"recursive": body.recursive})
    s.add(sess)
    s.commit()
    s.refresh(sess)
    j = runner().enqueue("ingest_folder", {"session_id": sess.id}, dedupe=True)
    sess.job_id = j.id
    s.add(sess)
    s.commit()
    return {"session": session_out(sess), "job": job_to_dict(j)}


@router.post("/browse")
def browse(body: PathIn) -> dict[str, Any]:
    """Minimal server-side folder browser for 'add folder by path'."""
    raw = (body.path or "").strip()
    roots: list[str] = []
    if os.name == "nt":
        roots = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    if config.SERVER:
        if not config.IMPORT_ROOTS:
            raise HTTPException(403, "Importing from server folders is off. Set PL_IMPORT_ROOTS to allow some folders.")
        roots = [str(r) for r in config.IMPORT_ROOTS]
    if not raw:
        path = config.IMPORT_ROOTS[0] if config.SERVER else Path.home()
    else:
        path = Path(raw).expanduser()
    require_import_path(path)
    if not path.is_dir():
        raise HTTPException(404, f"not a folder: {path}")
    dirs, n_images, n_other = [], 0, 0
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith(".") or e.name.startswith("$"):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        dirs.append(e.name)
                    elif e.is_file():
                        if images.is_image(e.name):
                            n_images += 1
                        else:
                            n_other += 1
                except OSError:
                    continue
    except PermissionError:
        raise HTTPException(403, f"no permission to read {path}") from None
    dirs.sort(key=str.lower)
    parent = str(path.parent) if path.parent != path and config.path_import_allowed(path.parent) else None
    return {"path": str(path), "parent": parent, "dirs": dirs, "images": n_images, "other_files": n_other, "roots": roots}


# ----------------------------------------------------------------------------- history


@router.get("")
def list_sessions(limit: int = 50, s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = s.exec(select(ImportSession).order_by(ImportSession.id.desc()).limit(limit)).all()
    return [session_out(r) for r in rows]


@router.get("/{session_id}")
def get_import(session_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    sess = s.get(ImportSession, session_id)
    if sess is None:
        raise HTTPException(404, "import session not found")
    out = session_out(sess)
    from sqlalchemy import func

    rows = s.exec(
        select(ImportCandidate.decision, func.count(ImportCandidate.id))
        .where(ImportCandidate.session_id == session_id)
        .group_by(ImportCandidate.decision)
    ).all()
    out["decisions"] = {d: n for d, n in rows}
    return out


@router.get("/{session_id}/candidates")
def list_candidates(
    session_id: int,
    decision: str | None = None,
    page: int = 1,
    page_size: int = 200,
    s: Session = Depends(get_session),
) -> dict[str, Any]:
    from sqlalchemy import func

    stmt = select(ImportCandidate).where(ImportCandidate.session_id == session_id)
    cnt = select(func.count(ImportCandidate.id)).where(ImportCandidate.session_id == session_id)
    if decision:
        decs = decision.split(",")
        stmt = stmt.where(ImportCandidate.decision.in_(decs))
        cnt = cnt.where(ImportCandidate.decision.in_(decs))
    total = s.exec(cnt).one()
    rows = s.exec(stmt.order_by(ImportCandidate.id.asc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [candidate_out(c) for c in rows], "total": total, "page": page, "page_size": page_size}


@router.post("/candidates/{candidate_id}/keep")
def keep_candidate(candidate_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    c = s.get(ImportCandidate, candidate_id)
    if c is None:
        raise HTTPException(404, "candidate not found")
    if c.photo_id and c.decision == "accepted":
        raise HTTPException(409, "already in the library")
    j = runner().enqueue("reimport_candidate", {"candidate_id": candidate_id}, dedupe=True)
    return job_to_dict(j)


@router.get("/candidates/{candidate_id}/thumb")
def candidate_thumb(candidate_id: int, s: Session = Depends(get_session)):
    c = s.get(ImportCandidate, candidate_id)
    if c is None:
        raise HTTPException(404, "candidate not found")
    if c.photo_id:
        p = config.THUMBS_DIR / f"{c.photo_id}.jpg"
        if p.exists():
            return FileResponse(p, media_type="image/jpeg")
    cache = config.DERIVED_DIR / "candidate_thumbs" / f"{candidate_id}.jpg"
    if not cache.exists():
        src = Path(c.file_path)
        if not src.exists():
            raise HTTPException(404, "source file missing")
        try:
            img, _ = images.open_image(src)
            images.save_jpeg(images.resize_long_side(img, config.THUMB_LONG_SIDE), cache, 82)
        except Exception as e:
            raise HTTPException(415, f"cannot render: {e}") from None
    return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/{session_id}")
def delete_session(session_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Remove an import from the history. Photos stay; ledger rows stay (unlinked)."""
    sess = s.get(ImportSession, session_id)
    if sess is None:
        raise HTTPException(404, "import session not found")
    if sess.status in ("queued", "running"):
        raise HTTPException(409, "import is still running")
    for c in s.exec(select(ImportCandidate).where(ImportCandidate.session_id == session_id)).all():
        c.session_id = None
        s.add(c)
    s.delete(sess)
    s.commit()
    sess.finished_at = sess.finished_at or now()
    return {"deleted": session_id}
