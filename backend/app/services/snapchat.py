"""Snapchat Memories importer.

Expected export layout (Snapchat "My Data" with memories included; extracted):
    <root>/json/memories_history.json      {"Saved Media": [{"Date": "2024-03-15 18:22:01 UTC", "Media Type": "Image", ...}]}
    <root>/memories/2024-03-15_<uuid>-main.jpg
    <root>/memories/2024-03-15_<uuid>-overlay.png   (captions/stickers layer; skipped)
    <root>/memories/2024-03-15_<uuid>-main.mp4      (videos; skipped)
The scanner tolerates other folder depths and file names; anything it cannot place
is reported by /snapchat/scan before an import starts. Dates in the export are UTC
and are converted to local time (Settings -> timezone).

NOTE: written against the documented layout, not yet against the user's real
export (PLAN.md §11 Q3). Adjust `classify_file` / `match_metadata` if it differs.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import func
from sqlmodel import Session, select

from ..jobs import JobContext, job
from ..models import ImportCandidate, ImportSession, now
from ..settings import AppSettings, load_settings
from . import dates, images, ingest
from . import physique_filter as PF

log = logging.getLogger(__name__)

_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_META_NAMES = ("memories_history.json",)


@dataclass
class SnapFile:
    path: Path
    kind: str  # image|video|overlay|other
    day: str | None  # YYYY-MM-DD from the filename (UTC)


@dataclass
class ScanResult:
    root: Path
    memories_dir: Path | None = None
    metadata_file: Path | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)  # {"utc": datetime, "type": "image"|"video"}
    files: list[SnapFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        c: dict[str, int] = defaultdict(int)
        for f in self.files:
            c[f.kind] += 1
        return dict(c)


def classify_file(p: Path) -> SnapFile:
    name = p.name.lower()
    m = _DATE_PREFIX.match(p.name)
    day = m.group(1) if m else None
    if "overlay" in name:
        return SnapFile(p, "overlay", day)
    if images.is_video(p):
        return SnapFile(p, "video", day)
    if images.is_image(p):
        return SnapFile(p, "image", day)
    return SnapFile(p, "other", day)


def _parse_utc(raw: str) -> datetime | None:
    raw = (raw or "").replace(" UTC", "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def load_metadata(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    items = data.get("Saved Media") if isinstance(data, dict) else data
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        utc = _parse_utc(str(it.get("Date", "")))
        if utc is None:
            continue
        t = str(it.get("Media Type", "")).lower()
        out.append({"utc": utc, "type": "video" if t.startswith("video") else "image"})
    return out


def scan(root: Path) -> ScanResult:
    res = ScanResult(root=root)
    if not root.is_dir():
        res.warnings.append("path is not a folder (extract the Snapchat zip first)")
        return res
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        d = Path(dirpath)
        for fn in sorted(filenames):
            p = d / fn
            if fn.lower() in _META_NAMES and res.metadata_file is None:
                res.metadata_file = p
                continue
            if fn.lower().endswith((".json", ".html", ".htm", ".txt", ".css", ".js")):
                continue
            sf = classify_file(p)
            res.files.append(sf)
            if sf.kind in ("image", "video") and res.memories_dir is None and d.name.lower() == "memories":
                res.memories_dir = d
    if res.metadata_file:
        try:
            res.entries = load_metadata(res.metadata_file)
        except Exception as e:
            res.warnings.append(f"could not read {res.metadata_file.name}: {e}")
    else:
        res.warnings.append("memories_history.json not found; dates will come from file names")
    if not any(f.kind == "image" for f in res.files):
        res.warnings.append("no image files found. If the export only has download links, use Snapchat's 'export memories' option.")
    return res


def match_metadata(res: ScanResult) -> dict[Path, tuple[datetime, str]]:
    """file -> (utc datetime, confidence). A file gets the exact timestamp when exactly
    one metadata entry of its type exists on its UTC day; otherwise the day only."""
    by_day: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    for e in res.entries:
        by_day[(e["utc"].strftime("%Y-%m-%d"), e["type"])].append(e["utc"])
    files_per_day: dict[tuple[str, str], int] = defaultdict(int)
    for f in res.files:
        if f.kind in ("image", "video") and f.day:
            files_per_day[(f.day, f.kind)] += 1
    out: dict[Path, tuple[datetime, str]] = {}
    for f in res.files:
        if f.kind != "image" or not f.day:
            continue
        cands = by_day.get((f.day, "image"), [])
        if len(cands) == 1 and files_per_day[(f.day, "image")] == 1:
            out[f.path] = (cands[0], "high")
        else:
            try:
                out[f.path] = (datetime.strptime(f.day, "%Y-%m-%d").replace(hour=12), "medium")
            except ValueError:
                continue
    return out


def scan_summary(s: Session, root: Path) -> dict[str, Any]:
    res = scan(root)
    c = res.counts()
    matched = match_metadata(res)
    known_paths = set(s.exec(select(ImportCandidate.file_path)).all())
    imgs = [f for f in res.files if f.kind == "image"]
    return {
        "path": str(root),
        "memories_dir": str(res.memories_dir) if res.memories_dir else None,
        "metadata_file": str(res.metadata_file) if res.metadata_file else None,
        "metadata_entries": len(res.entries),
        "images": c.get("image", 0),
        "videos": c.get("video", 0),
        "overlays": c.get("overlay", 0),
        "other": c.get("other", 0),
        "matched_metadata": sum(1 for v in matched.values() if v[1] == "high"),
        "sample_files": [str(f.path.relative_to(root)) for f in imgs[:10]],
        "already_known": sum(1 for f in imgs if str(f.path) in known_paths),
        "warnings": res.warnings,
    }


# ----------------------------------------------------------------------------- job


FINAL = ("accepted", "rejected", "auto_reject", "duplicate", "deleted", "error")


def _score_batch(cands: list[ImportCandidate], settings: AppSettings) -> None:
    from .clip_backend import get_scorer
    from .pose_backend import get_backend

    pil = []
    keep = []
    for c in cands:
        try:
            img, _ = images.open_image(c.file_path)
            small = images.resize_long_side(img, 512)
            pil.append(small.copy() if small is img else small)
            img.close()
            keep.append(c)
        except Exception as e:
            c.decision, c.error = "error", f"unreadable: {e}"[:300]
    if not keep:
        return
    clips = get_scorer().positive_probability(pil, settings.clip_positive_prompts, settings.clip_negative_prompts)
    backend = None
    for c, im, clip in zip(keep, pil, clips, strict=True):
        c.clip_score = round(clip, 4)
        pose = None
        if clip >= settings.filter_pose_gate:
            if backend is None:
                backend = get_backend(settings.pose_model)
            rgb = np.asarray(images.resize_long_side(im, 640).convert("RGB"))
            pose = PF.pose_score(backend.detect(rgb, num_poses=2), rgb.shape[0])
        c.pose_score = pose
        c.score = PF.combine(clip, pose, settings)
        c.decision = PF.decide(c.score, settings)
        c.decided_by = "auto"
        c.decided_at = now()


@job("snapchat_import", lane="cv", title="Snapchat import")
def job_snapchat(ctx: JobContext, params: dict) -> dict:
    sid = int(params["session_id"])
    try:
        return _run_import(ctx, sid, defer_completion=bool(params.get("defer_completion")))
    except BaseException:
        with ctx.session() as s:
            sess = s.get(ImportSession, sid)
            if sess is not None and sess.status in ("queued", "running"):
                sess.status = "cancelled" if ctx.cancelled else "failed"
                s.add(sess)
                s.commit()
        raise


def _run_import(ctx: JobContext, sid: int, *, defer_completion: bool = False) -> dict:
    with ctx.session() as s:
        sess = s.get(ImportSession, sid)
        root = Path(sess.root_path)
        settings = load_settings(s)
        sess.status = "running"
        sess.job_id = ctx.job_id
        s.add(sess)
        s.commit()
        ctx.message("Scanning export")
        res = scan(root)
        if not root.is_dir():
            raise FileNotFoundError(f"folder not found: {root}")
        matched = match_metadata(res)
        counts = dict(sess.counts or {})
        c = res.counts()
        counts.update(files=len(res.files), images=c.get("image", 0), videos_skipped=c.get("video", 0), overlays_skipped=c.get("overlay", 0))
        sess.counts = counts
        s.add(sess)
        s.commit()

        # phase 1: register every image as a candidate (hash once; resumable)
        known = {row.file_path: row for row in s.exec(select(ImportCandidate).where(ImportCandidate.session_id == sid)).all()}
        imgs = [f for f in res.files if f.kind == "image"]
        ctx.progress(0, len(imgs), "Hashing files")
        for i, f in enumerate(imgs, 1):
            ctx.check_cancelled()
            if str(f.path) not in known:
                try:
                    sha = images.sha256_file(f.path)
                except OSError as e:
                    log.warning("cannot read %s: %s", f.path, e)
                    continue
                cand = s.exec(select(ImportCandidate).where(ImportCandidate.sha256 == sha)).first()
                utc, conf = matched.get(f.path, (None, "low"))
                local = dates.utc_to_local(utc, settings.timezone) if utc else None
                if cand is None:
                    cand = ImportCandidate(
                        session_id=sid, file_path=str(f.path), file_name=str(f.path.relative_to(root)), sha256=sha,
                        source_date=local, date_source="snapchat_metadata" if conf == "high" else "filename", media_type="image",
                    )
                elif cand.decision not in FINAL:
                    cand.session_id, cand.file_path = sid, str(f.path)
                    cand.source_date = cand.source_date or local
                s.add(cand)
                if i % 50 == 0:
                    s.commit()
            if i % 50 == 0 or i == len(imgs):
                s.commit()
                ctx.progress(i, len(imgs), f"Hashing files {i}/{len(imgs)}")
        s.commit()

        # phase 2: score pending candidates in batches
        pending = s.exec(
            select(ImportCandidate).where(ImportCandidate.session_id == sid, ImportCandidate.decision == "pending").order_by(ImportCandidate.id)
        ).all()
        total = len(pending)
        ctx.progress(0, total, f"Scoring {total} images (loading models)")
        index = ingest.load_phash_index(s)
        accepted = 0
        t_start = datetime.now()
        for i in range(0, total, 32):
            ctx.check_cancelled()
            batch = pending[i : i + 32]
            _score_batch(batch, settings)
            for cand in batch:
                s.add(cand)
            s.commit()
            for cand in batch:
                if cand.decision == "auto_accept":
                    r = ingest.ingest_file(
                        s, Path(cand.file_path), source="snapchat", source_ref=str(root), session_id=sid, settings=settings,
                        index=index, display_name=cand.file_name, snapchat_date=cand.source_date,
                        snapchat_confidence="high" if cand.date_source == "snapchat_metadata" else "medium",
                    )
                    cand = s.get(ImportCandidate, cand.id)
                    if r.status == "accepted":
                        accepted += 1
                        cand.decision, cand.decided_by = "auto_accept", "auto"
                        s.add(cand)
                        s.commit()
            done = min(total, i + 32)
            el = (datetime.now() - t_start).total_seconds()
            eta = el / done * (total - done) if done else 0
            ctx.progress(done, total, f"Scored {done}/{total} · {accepted} imported · ETA {int(eta // 60)}m{int(eta % 60):02d}s")

        dec = dict(
            s.exec(
                select(ImportCandidate.decision, func.count(ImportCandidate.id))
                .where(ImportCandidate.session_id == sid)
                .group_by(ImportCandidate.decision)
            ).all()
        )
        counts.update(
            accepted=dec.get("auto_accept", 0) + dec.get("accepted", 0),
            rejected=dec.get("auto_reject", 0) + dec.get("rejected", 0),
            review=dec.get("review", 0),
            duplicates=dec.get("duplicate", 0),
            errors=dec.get("error", 0),
        )
        sess = s.get(ImportSession, sid)
        sess.counts = counts
        if not defer_completion:
            sess.status = "done"
            sess.finished_at = now()
        s.add(sess)
        s.commit()
    msg = f"Snapchat: {counts['accepted']} imported, {counts['review']} to review, {counts['rejected']} rejected"
    return {"message": msg, "counts": counts, "session_id": sid}
