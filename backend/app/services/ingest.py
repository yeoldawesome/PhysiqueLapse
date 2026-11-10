"""Bring image files into the library.

For every file: hash -> ledger / duplicate checks -> open + EXIF orientation ->
perceptual hash -> date -> copy original (byte-identical) -> thumbnail + preview
-> photo row. Each file is committed on its own, so an interrupted import can be
run again and continues.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from .. import config
from ..jobs import JobContext, job
from ..models import Alignment, ImportCandidate, ImportSession, Photo, PoseFeatures, PoseLabel, now
from ..settings import AppSettings, load_settings
from . import dates, images

log = logging.getLogger(__name__)

STATUS_KEYS = (
    "files",
    "images",
    "accepted",
    "duplicates",
    "near_duplicates",
    "skipped_ledger",
    "videos_skipped",
    "other_skipped",
    "errors",
)


@dataclass
class IngestResult:
    status: str  # accepted|duplicate|near_duplicate|skipped_ledger|video|other|error
    photo_id: int | None = None
    candidate_id: int | None = None
    duplicate_of: int | None = None
    message: str = ""


def load_phash_index(s: Session) -> images.PhashIndex:
    idx = images.PhashIndex()
    for pid, ph in s.exec(select(Photo.id, Photo.phash)).all():
        idx.add(pid, ph)
    return idx


def _candidate_for(s: Session, sha: str) -> ImportCandidate | None:
    return s.exec(select(ImportCandidate).where(ImportCandidate.sha256 == sha)).first()


def _upsert_candidate(
    s: Session,
    cand: ImportCandidate | None,
    *,
    sha: str,
    path: Path,
    display_name: str,
    session_id: int | None,
    **fields,
) -> ImportCandidate:
    if cand is None:
        cand = ImportCandidate(sha256=sha, file_path=str(path), file_name=display_name, session_id=session_id)
    else:
        cand.file_path = str(path)
        cand.file_name = display_name
        if session_id is not None:
            cand.session_id = session_id
    for k, v in fields.items():
        setattr(cand, k, v)
    cand.decided_at = now()
    s.add(cand)
    return cand


def ingest_file(
    s: Session,
    src: Path,
    *,
    source: str,
    source_ref: str | None,
    session_id: int | None,
    settings: AppSettings,
    index: images.PhashIndex,
    display_name: str | None = None,
    snapchat_date: datetime | None = None,
    snapchat_confidence: str = "high",
    move: bool = False,
    force: bool = False,
) -> IngestResult:
    """Ingest one file. `force` bypasses the ledger and near-duplicate skip ("keep anyway")."""
    display_name = display_name or src.name
    ext = src.suffix.lower()
    if images.is_video(src):
        return IngestResult("video", message="video skipped")
    if not images.is_image(src):
        return IngestResult("other", message=f"unsupported extension {ext or '(none)'}")

    try:
        sha = images.sha256_file(src)
    except OSError as e:
        return IngestResult("error", message=f"cannot read: {e}")

    existing_photo = s.exec(select(Photo).where(Photo.sha256 == sha)).first()
    cand = _candidate_for(s, sha)
    if existing_photo is not None:
        c = _upsert_candidate(
            s, cand, sha=sha, path=src, display_name=display_name, session_id=session_id,
            decision="duplicate", duplicate_of_photo_id=existing_photo.id, photo_id=existing_photo.id,
        )
        s.commit()
        return IngestResult("duplicate", duplicate_of=existing_photo.id, candidate_id=c.id, message="exact duplicate")

    if cand is not None and not force and cand.decision in ("rejected", "deleted", "auto_reject"):
        # ledger says we have seen this file and decided against it
        return IngestResult("skipped_ledger", candidate_id=cand.id, message=f"previously {cand.decision}")

    try:
        img, exif = images.open_image(src)
    except Exception as e:
        c = _upsert_candidate(
            s, cand, sha=sha, path=src, display_name=display_name, session_id=session_id,
            decision="error", error=f"unreadable image: {type(e).__name__}: {e}"[:300],
        )
        s.commit()
        return IngestResult("error", candidate_id=c.id, message=f"unreadable image: {e}")

    try:
        ph = images.phash_hex(img)
    except Exception:
        ph = None

    if ph and not force and settings.skip_near_duplicates:
        near_id, dist = index.nearest(ph)
        if near_id is not None and dist <= settings.phash_threshold:
            c = _upsert_candidate(
                s, cand, sha=sha, path=src, display_name=display_name, session_id=session_id,
                decision="duplicate", duplicate_of_photo_id=near_id, phash=ph, media_type="image",
                error=f"near-duplicate (distance {dist})",
            )
            s.commit()
            return IngestResult("near_duplicate", duplicate_of=near_id, candidate_id=c.id, message=f"near-duplicate (d={dist})")

    try:
        mtime = os.path.getmtime(src)
    except OSError:
        mtime = None
    dr = dates.resolve_date(
        exif=exif, snapchat_date=snapchat_date, snapchat_confidence=snapchat_confidence,
        filename=display_name, mtime=mtime,
    )

    stored_ext = ".jpg" if ext == ".jpeg" else ext
    dest = config.ORIGINALS_DIR / f"{sha[:16]}{stored_ext}"
    photo = Photo(
        sha256=sha,
        phash=ph,
        original_path=f"originals/{dest.name}",
        original_filename=display_name,
        ext=stored_ext.lstrip("."),
        source=source,
        source_ref=source_ref,
        width=img.width,
        height=img.height,
        exif_orientation=int(exif.get("Orientation", 1) or 1),
        taken_at=dr.taken_at,
        taken_at_source=dr.source,
        taken_at_confidence=dr.confidence,
    )
    pid: int | None = None
    try:
        s.add(photo)
        s.flush()  # get the id for derived file names
        pid = photo.id
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            if move:
                shutil.move(str(src), str(dest))
            else:
                shutil.copy2(src, dest)
        images.save_jpeg(images.resize_long_side(img, config.THUMB_LONG_SIDE), config.THUMBS_DIR / f"{pid}.jpg", 85)
        images.save_jpeg(images.resize_long_side(img, config.PREVIEW_LONG_SIDE), config.PREVIEWS_DIR / f"{pid}.jpg", 90)
        c = _upsert_candidate(
            s, cand, sha=sha, path=src, display_name=display_name, session_id=session_id,
            decision="accepted", decided_by=("user" if force else "auto"), photo_id=pid, phash=ph,
            source_date=dr.taken_at, date_source=dr.source, media_type="image", error=None,
        )
        s.commit()
    except Exception as e:
        s.rollback()
        log.exception("ingest failed for %s", src)
        if pid is not None:
            for p in (config.THUMBS_DIR / f"{pid}.jpg", config.PREVIEWS_DIR / f"{pid}.jpg"):
                p.unlink(missing_ok=True)
        c = _upsert_candidate(
            s, _candidate_for(s, sha), sha=sha, path=src, display_name=display_name, session_id=session_id,
            decision="error", error=f"{type(e).__name__}: {e}"[:300],
        )
        s.commit()
        return IngestResult("error", candidate_id=c.id, message=str(e))
    finally:
        img.close()

    index.add(pid, ph)
    return IngestResult("accepted", photo_id=pid, candidate_id=c.id)


# ----------------------------------------------------------------------------- deletion


def delete_photo(s: Session, photo: Photo, *, remember: bool = True) -> None:
    """Move our copy of the original to trash, remove derived files and rows.
    The user's source files are never touched. With `remember`, the hash stays in
    the ledger so a re-import skips it."""
    pid = photo.id
    src = config.abs_path(photo.original_path)
    if src.exists():
        config.TRASH_DIR.mkdir(parents=True, exist_ok=True)
        dest = config.TRASH_DIR / src.name
        if dest.exists():
            dest = config.TRASH_DIR / f"{src.stem}_{pid}{src.suffix}"
        shutil.move(str(src), str(dest))
    for p in (
        config.THUMBS_DIR / f"{pid}.jpg",
        config.PREVIEWS_DIR / f"{pid}.jpg",
        config.ALIGNED_DIR / f"{pid}.jpg",
        config.LANDMARKS_DIR / f"{pid}.json",
    ):
        p.unlink(missing_ok=True)
    from ..models import PhotoMeasurement

    (config.DERIVED_DIR / "masks" / f"{pid}.png").unlink(missing_ok=True)
    for model in (Alignment, PoseFeatures, PhotoMeasurement):
        row = s.get(model, pid)
        if row is not None:
            s.delete(row)
    for lab in s.exec(select(PoseLabel).where(PoseLabel.photo_id == pid)).all():
        s.delete(lab)
    from ..models import PoseCategory

    for cat in s.exec(select(PoseCategory).where(PoseCategory.reference_photo_id == pid)).all():
        cat.reference_photo_id = None
        s.add(cat)
    cand = _candidate_for(s, photo.sha256)
    if remember:
        _upsert_candidate(
            s, cand, sha=photo.sha256, path=Path(cand.file_path) if cand else config.abs_path(photo.original_path),
            display_name=cand.file_name if cand else photo.original_filename,
            session_id=cand.session_id if cand else None, decision="deleted", decided_by="user", photo_id=None,
        )
    elif cand is not None:
        s.delete(cand)
    s.delete(photo)
    s.commit()


# ----------------------------------------------------------------------------- jobs


def _iter_folder(root: Path, recursive: bool):
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for fn in sorted(filenames):
                yield Path(dirpath) / fn
    else:
        for p in sorted(root.iterdir()):
            if p.is_file():
                yield p


def _bump(counts: dict, key: str, n: int = 1) -> None:
    counts[key] = counts.get(key, 0) + n


_RESULT_TO_COUNT = {
    "accepted": "accepted",
    "duplicate": "duplicates",
    "near_duplicate": "near_duplicates",
    "skipped_ledger": "skipped_ledger",
    "video": "videos_skipped",
    "other": "other_skipped",
    "error": "errors",
}


def run_ingest_session(ctx: JobContext, session_id: int, *, files: list[tuple[Path, str]], source: str, move: bool) -> dict:
    """Shared loop for upload and folder imports. `files` = [(path, display_name)]."""
    with ctx.session() as s:
        sess = s.get(ImportSession, session_id)
        if sess is None:
            raise ValueError(f"import session {session_id} not found")
        settings = load_settings(s)
        index = load_phash_index(s)
        counts = dict(sess.counts or {})
        counts["files"] = len(files)
        sess.status = "running"
        sess.job_id = ctx.job_id
        sess.counts = counts
        s.add(sess)
        s.commit()
        total = len(files)
        ctx.progress(0, total, f"Importing {total} files")
        done = 0
        try:
            for path, name in files:
                ctx.check_cancelled()
                if images.is_image(path):
                    _bump(counts, "images")
                r = ingest_file(
                    s, path, source=source, source_ref=sess.root_path or f"session:{session_id}",
                    session_id=session_id, settings=settings, index=index, display_name=name, move=move,
                )
                _bump(counts, _RESULT_TO_COUNT.get(r.status, "errors"))
                done += 1
                sess.counts = dict(counts)
                s.add(sess)
                s.commit()
                ctx.progress(done, total, f"{name[:60]} - {r.status.replace('_', ' ')}")
        except Exception:
            sess.status = "cancelled" if ctx.cancelled else "failed"
            sess.finished_at = now()
            s.add(sess)
            s.commit()
            raise
        sess.status = "done"
        sess.finished_at = now()
        sess.counts = dict(counts)
        s.add(sess)
        s.commit()
        msg = f"Imported {counts.get('accepted', 0)} of {total} files"
        extras = []
        if counts.get("duplicates"):
            extras.append(f"{counts['duplicates']} duplicates")
        if counts.get("near_duplicates"):
            extras.append(f"{counts['near_duplicates']} near-duplicates")
        if counts.get("errors"):
            extras.append(f"{counts['errors']} errors")
        if extras:
            msg += " (" + ", ".join(extras) + ")"
        ctx.progress(total, total, msg, force=True)
        return {"message": msg, "counts": counts, "session_id": session_id}


@job("ingest_upload", lane="io", title="Import uploaded files")
def job_ingest_upload(ctx: JobContext, params: dict) -> dict:
    session_id = int(params["session_id"])
    staging = config.INCOMING_DIR / str(session_id)
    files: list[tuple[Path, str]] = []
    if staging.exists():
        for p in _iter_folder(staging, recursive=True):
            files.append((p, p.relative_to(staging).as_posix()))
    result = run_ingest_session(ctx, session_id, files=files, source="upload", move=True)
    shutil.rmtree(staging, ignore_errors=True)
    return result


@job("ingest_folder", lane="io", title="Import folder")
def job_ingest_folder(ctx: JobContext, params: dict) -> dict:
    session_id = int(params["session_id"])
    with ctx.session() as s:
        sess = s.get(ImportSession, session_id)
        if sess is None or not sess.root_path:
            raise ValueError("import session has no folder")
        root = Path(sess.root_path)
        recursive = bool((sess.options or {}).get("recursive", True))
    if not root.is_dir():
        raise FileNotFoundError(f"folder not found: {root}")
    ctx.message("Scanning folder")
    files = [(p, p.relative_to(root).as_posix()) for p in _iter_folder(root, recursive)]
    return run_ingest_session(ctx, session_id, files=files, source="folder", move=False)


@job("reimport_candidate", lane="io", title="Import file")
def job_reimport_candidate(ctx: JobContext, params: dict) -> dict:
    """'Keep anyway' for a near-duplicate / rejected candidate."""
    cid = int(params["candidate_id"])
    with ctx.session() as s:
        cand = s.get(ImportCandidate, cid)
        if cand is None:
            raise ValueError("candidate not found")
        sess = s.get(ImportSession, cand.session_id) if cand.session_id else None
        settings = load_settings(s)
        index = load_phash_index(s)
        src = Path(cand.file_path)
        if not src.exists():
            raise FileNotFoundError(f"source file no longer exists: {src}")
        source = sess.kind if sess else "folder"
        r = ingest_file(
            s, src, source=source, source_ref=sess.root_path if sess else None, session_id=cand.session_id,
            settings=settings, index=index, display_name=cand.file_name,
            snapchat_date=cand.source_date if (sess and sess.kind == "snapchat") else None, force=True,
        )
        if sess is not None and r.status == "accepted":
            counts = dict(sess.counts or {})
            _bump(counts, "accepted")
            sess.counts = counts
            s.add(sess)
            s.commit()
        return {"message": f"{cand.file_name}: {r.status}", "status": r.status, "photo_id": r.photo_id}
