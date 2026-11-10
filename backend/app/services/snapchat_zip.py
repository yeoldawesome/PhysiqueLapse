"""Snapchat export zips -> extracted photos -> physique filter.

Snapchat delivers "My Data" with memories as one or more zips (mydata~123.zip,
mydata~123-2.zip, ...). We never extract everything: only photos (not overlay
layers), memories_history.json, and photos inside nested per-memory zips.
Videos are skipped. Extraction is resumable (files already extracted with the
same size are skipped). Zips uploaded through the browser are deleted after
extraction; zips added by path are only read.

Staging layout: data/incoming/snapchat/<session_id>/
    zips/                 uploaded zip files (temporary)
    extracted/memories/   photos
    extracted/json/       memories_history.json
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath

from .. import config
from ..jobs import JobContext, job
from ..models import ImportSession
from . import images

log = logging.getLogger(__name__)

META_NAMES = {"memories_history.json"}


def staging_dir(session_id: int) -> Path:
    return config.INCOMING_DIR / "snapchat" / str(session_id)


def extracted_dir(session_id: int) -> Path:
    return staging_dir(session_id) / "extracted"


def _wanted(name: str) -> str | None:
    """'image' | 'meta' | 'zip' | None for a zip entry name."""
    base = PurePosixPath(name).name
    low = base.lower()
    if not base or low.startswith(".") or "__macosx" in name.lower():
        return None
    if low in META_NAMES:
        return "meta"
    if low.endswith(".zip"):
        return "zip"
    if "overlay" in low:
        return None
    if images.is_image(base):
        return "image"
    return None


def _target(out: Path, kind: str, base: str) -> Path:
    return out / ("json" if kind == "meta" else "memories") / base


def _unique(p: Path, size: int) -> Path | None:
    """Destination path; None if an identical-size file is already there (resume)."""
    if not p.exists():
        return p
    if p.stat().st_size == size:
        return None
    i = 1
    while True:
        q = p.with_name(f"{p.stem}_{i}{p.suffix}")
        if not q.exists():
            return q
        if q.stat().st_size == size:
            return None
        i += 1


def _copy_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with zf.open(info) as src, open(tmp, "wb") as f:
        shutil.copyfileobj(src, f, 1 << 20)
    tmp.replace(dest)
    try:
        ts = time.mktime(info.date_time + (0, 0, -1))
        os.utime(dest, (ts, ts))
    except (OverflowError, ValueError):
        pass


def extract_zip(zf: zipfile.ZipFile, out: Path, counts: dict, depth: int = 0, ctx: JobContext | None = None) -> None:
    for info in zf.infolist():
        if info.is_dir():
            continue
        if ctx is not None:
            ctx.check_cancelled()
        kind = _wanted(info.filename)
        if kind is None:
            low = info.filename.lower()
            if images.is_video(low):
                counts["videos_skipped"] = counts.get("videos_skipped", 0) + 1
            elif "overlay" in low:
                counts["overlays_skipped"] = counts.get("overlays_skipped", 0) + 1
            continue
        if kind == "zip":
            if depth >= 2:
                continue
            # nested per-memory zip (main + overlay): read it in memory if small, else via a temp file
            try:
                if info.file_size < 200_000_000:
                    with zf.open(info) as raw:
                        inner = zipfile.ZipFile(io.BytesIO(raw.read()))
                    extract_zip(inner, out, counts, depth + 1, ctx)
                else:
                    tmp = out / ".nested.zip"
                    _copy_entry(zf, info, tmp)
                    with zipfile.ZipFile(tmp) as inner:
                        extract_zip(inner, out, counts, depth + 1, ctx)
                    tmp.unlink(missing_ok=True)
            except zipfile.BadZipFile:
                counts["errors"] = counts.get("errors", 0) + 1
            continue
        base = PurePosixPath(info.filename).name
        dest = _unique(_target(out, kind, base), info.file_size)
        if dest is None:
            counts["already_extracted"] = counts.get("already_extracted", 0) + 1
            continue
        _copy_entry(zf, info, dest)
        key = "photos_extracted" if kind == "image" else "metadata_files"
        counts[key] = counts.get(key, 0) + 1


def zip_summary(path: Path) -> dict:
    """Quick look inside a zip without extracting (for the UI list)."""
    out = {"path": str(path), "name": path.name, "bytes": path.stat().st_size, "photos": 0, "videos": 0, "nested_zips": 0, "has_metadata": False, "error": None}
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                k = _wanted(info.filename)
                if k == "image":
                    out["photos"] += 1
                elif k == "zip":
                    out["nested_zips"] += 1
                elif k == "meta":
                    out["has_metadata"] = True
                elif images.is_video(info.filename):
                    out["videos"] += 1
    except (zipfile.BadZipFile, OSError) as e:
        out["error"] = f"not a readable zip: {e}"
    return out


@job("snapchat_zip_import", lane="cv", title="Snapchat import (zips)")
def job_snapchat_zips(ctx: JobContext, params: dict) -> dict:
    sid = int(params["session_id"])
    try:
        return _run(ctx, sid)
    except BaseException:
        with ctx.session() as s:
            sess = s.get(ImportSession, sid)
            if sess is not None:
                sess.status = "cancelled" if ctx.cancelled else "failed"
                s.add(sess)
                s.commit()
        raise


def _run(ctx: JobContext, sid: int) -> dict:
    with ctx.session() as s:
        sess = s.get(ImportSession, sid)
        if sess is None:
            raise ValueError("import session not found")
        opts = dict(sess.options or {})
        zips = list(opts.get("zips", []))
        sess.status = "running"
        sess.job_id = ctx.job_id
        sess.root_path = str(extracted_dir(sid))
        s.add(sess)
        s.commit()
    out = extracted_dir(sid)
    out.mkdir(parents=True, exist_ok=True)
    counts: dict = {}
    done_zips = set(opts.get("extracted_zips", []))
    for i, z in enumerate(zips, 1):
        ctx.check_cancelled()
        path = Path(z["path"])
        if z["path"] in done_zips:
            continue
        ctx.progress(i - 1, len(zips), f"Unpacking {path.name} ({i}/{len(zips)})", force=True)
        if not path.exists():
            raise FileNotFoundError(f"zip not found: {path}")
        with zipfile.ZipFile(path) as zf:
            extract_zip(zf, out, counts, ctx=ctx)
        done_zips.add(z["path"])
        if z.get("uploaded"):
            path.unlink(missing_ok=True)  # our temporary copy; the user's original is untouched
        with ctx.session() as s:
            sess = s.get(ImportSession, sid)
            o = dict(sess.options or {})
            o["extracted_zips"] = sorted(done_zips)
            merged = dict(o.get("unpack_counts", {}))
            for k, v in counts.items():
                merged[k] = merged.get(k, 0) + v
            o["unpack_counts"] = merged
            sess.options = o
            s.add(sess)
            s.commit()
        counts = {}
    # hand over to the regular Snapchat import (scan, date matching, scoring, review)
    from .snapchat import job_snapchat

    return job_snapchat(ctx, {"session_id": sid})


def delete_staging(session_id: int) -> int:
    d = staging_dir(session_id)
    n = sum(1 for p in d.rglob("*") if p.is_file()) if d.exists() else 0
    shutil.rmtree(d, ignore_errors=True)
    return n
