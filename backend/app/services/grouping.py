"""Group photos by how well they align with each other (instead of named poses).

Distance between two photos = how badly one lines up on the other: the same weighted similarity
fit the "joints" alignment uses (shoulders, elbows, wrists, hips, a little head), with the same
rotation clamp; what's left over, in torso lengths, averaged both ways. The fit never mirrors, so
a left-side and a right-side pose never look alike. Average-linkage clustering at a chosen
strictness turns the distances into groups; every group aligns onto its most central photo.
Groups below a minimum size, and photos without a body, go to "Ungrouped".
"""

from __future__ import annotations

import math
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
from sqlmodel import Session, select

from .. import config
from ..jobs import JobContext, job
from ..models import Alignment, Photo, PoseCategory, PoseExample, PoseLabel, now
from ..settings import load_settings, save_settings
from . import landmarks as L
from .align import JOINT_FIT_WEIGHTS, TORSO_POINTS, VIS, anchors

JOINTS = tuple(JOINT_FIT_WEIGHTS)
WEIGHTS = np.array([JOINT_FIT_WEIGHTS[n] for n in JOINTS], dtype=np.float64)
TORSO_IDX = np.array([JOINTS.index(n) for n in TORSO_POINTS])
FAR = 1.0
FACE_PENALTY = 0.4
FACING_WEIGHT = 0.6
FACE_POINTS = ("nose", "left_eye", "right_eye")
UNGROUPED = "Ungrouped"
DEFAULT_THRESHOLD = 0.15
DEFAULT_MIN_SIZE = 3


@dataclass
class PoseSet:
    ids: list[int]
    pts: np.ndarray
    vis: np.ndarray
    torso: np.ndarray
    face: np.ndarray | None = None
    facing: np.ndarray | None = None

    def __post_init__(self) -> None:
        n = len(self.ids)
        if self.face is None:
            self.face = np.zeros(n, bool)
        if self.facing is None:
            self.facing = np.full(n, np.nan)


def _face_terms(pts: dict, torso: float) -> tuple[bool, float]:
    shown = sum(1 for n in FACE_POINTS if pts[n]["visibility"] >= VIS) >= 2
    if not shown or not torso or not math.isfinite(torso):
        return shown, math.nan
    sx = (pts["left_shoulder"]["x_px"] + pts["right_shoulder"]["x_px"]) / 2
    return shown, (pts["nose"]["x_px"] - sx) / torso


def view_penalty(face_i: bool, facing_i: float, face: np.ndarray, facing: np.ndarray) -> np.ndarray:
    """Extra distance from one photo to many for front/back and facing direction."""
    pen = np.where(face != face_i, FACE_PENALTY, 0.0)
    both = face & face_i & np.isfinite(facing) & math.isfinite(facing_i)
    return pen + np.where(
        both,
        FACING_WEIGHT * np.abs(np.where(both, facing, 0.0) - (facing_i if math.isfinite(facing_i) else 0.0)),
        0.0,
    )


def physique_ids(s: Session) -> list[int]:
    """Photos that show the user's body: a person was found, not excluded, not someone else."""
    stmt = select(Photo.id).where(
        Photo.landmarks_status == "ok",
        Photo.excluded == False,  # noqa: E712
        (Photo.person_status.is_(None)) | (Photo.person_status != "not_me"),
    )
    return list(s.exec(stmt.order_by(Photo.id)).all())


def load_points(s: Session, ids: list[int]) -> PoseSet:
    keep, points, visible, torsos, faces, facings = [], [], [], [], [], []
    for pid in ids:
        photo = s.get(Photo, pid)
        data = L.load_for_photo(photo) if photo is not None else None
        if not data or not data.get("detected"):
            continue
        pts = L.points_dict(data)
        if any(n not in pts for n in JOINTS):
            continue
        points.append([[pts[n]["x_px"], pts[n]["y_px"]] for n in JOINTS])
        visible.append([pts[n]["visibility"] >= VIS for n in JOINTS])
        try:
            torso = anchors(pts).torso or math.nan
        except Exception:
            torso = math.nan
        torsos.append(torso)
        face, facing = _face_terms(pts, torso)
        faces.append(face)
        facings.append(facing)
        keep.append(pid)
    if not keep:
        return PoseSet([], np.zeros((0, len(JOINTS), 2)), np.zeros((0, len(JOINTS)), bool), np.zeros(0))
    return PoseSet(
        keep,
        np.asarray(points, np.float64),
        np.asarray(visible, bool),
        np.asarray(torsos, np.float64),
        np.asarray(faces, bool),
        np.asarray(facings, np.float64),
    )


def fit_errors(
    src: np.ndarray,
    src_vis: np.ndarray,
    dst: np.ndarray,
    dst_vis: np.ndarray,
    dst_torso: np.ndarray,
    clamp_deg: float,
) -> np.ndarray:
    """Vectorised weighted fit error in each destination's torso lengths."""
    matching = src_vis[None, :] & dst_vis
    weights = WEIGHTS[None, :] * matching
    total_weight = weights.sum(1)
    ok = (
        (matching.sum(1) >= 3)
        & (matching[:, TORSO_IDX].sum(1) >= 2)
        & (total_weight > 0)
        & np.isfinite(dst_torso)
        & (dst_torso > 1e-6)
    )
    safe_weight = np.where(total_weight > 0, total_weight, 1.0)
    src_all = np.broadcast_to(src, dst.shape)
    src_mean = (weights[..., None] * src_all).sum(1) / safe_weight[:, None]
    dst_mean = (weights[..., None] * dst).sum(1) / safe_weight[:, None]
    src_centered, dst_centered = src_all - src_mean[:, None, :], dst - dst_mean[:, None, :]
    a = (weights * (src_centered[..., 0] * dst_centered[..., 0] + src_centered[..., 1] * dst_centered[..., 1])).sum(1)
    b = (weights * (src_centered[..., 0] * dst_centered[..., 1] - src_centered[..., 1] * dst_centered[..., 0])).sum(1)
    variance = (weights * (src_centered**2).sum(2)).sum(1)
    theta = np.clip(np.arctan2(b, a), -math.radians(clamp_deg), math.radians(clamp_deg))
    cosine, sine = np.cos(theta), np.sin(theta)
    rotated_x = cosine[:, None] * src_centered[..., 0] - sine[:, None] * src_centered[..., 1]
    rotated_y = sine[:, None] * src_centered[..., 0] + cosine[:, None] * src_centered[..., 1]
    scale = (weights * (rotated_x * dst_centered[..., 0] + rotated_y * dst_centered[..., 1])).sum(1) / np.where(
        variance > 1e-9, variance, 1.0
    )
    dx, dy = scale[:, None] * rotated_x - dst_centered[..., 0], scale[:, None] * rotated_y - dst_centered[..., 1]
    error = np.sqrt((weights * (dx**2 + dy**2)).sum(1) / safe_weight) / np.where(ok, dst_torso, 1.0)
    return np.where(ok, np.minimum(error, FAR), FAR)


def distance_matrix(ps: PoseSet, clamp_deg: float) -> np.ndarray:
    """Symmetric distances: the mean of fitting each photo onto the other."""
    n = len(ps.ids)
    errors = np.empty((n, n), np.float64)
    penalties = np.empty((n, n), np.float64)
    for i in range(n):
        errors[i] = fit_errors(ps.pts[i], ps.vis[i], ps.pts, ps.vis, ps.torso, clamp_deg)
        penalties[i] = view_penalty(bool(ps.face[i]), float(ps.facing[i]), ps.face, ps.facing)
    distances = (errors + errors.T + penalties + penalties.T) / 2
    np.fill_diagonal(distances, 0.0)
    return np.minimum(distances, FAR)


_cache_lock = threading.Lock()
_cache: dict[str, Any] = {}


def _cache_key(s: Session, ids: list[int], clamp_deg: float) -> tuple:
    rows = s.exec(select(Photo.id, Photo.flip_horizontal, Photo.updated_at).where(Photo.id.in_(ids))).all() if ids else []
    return (str(config.DB_PATH), clamp_deg, tuple((r[0], r[1], str(r[2])) for r in rows))


def cached_matrix(s: Session) -> tuple[PoseSet, np.ndarray]:
    clamp = load_settings(s).rotation_clamp_deg
    ids = physique_ids(s)
    key = _cache_key(s, ids, clamp)
    with _cache_lock:
        if _cache.get("key") == key:
            return _cache["ps"], _cache["D"]
    ps = load_points(s, ids)
    distances = distance_matrix(ps, clamp)
    with _cache_lock:
        _cache.update(key=key, ps=ps, D=distances)
    return ps, distances


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


@dataclass
class Group:
    members: list[int]
    medoid: int
    spread: float
    name: str = ""


def cluster(ps: PoseSet, distances: np.ndarray, threshold: float, min_size: int) -> tuple[list[Group], list[int]]:
    """Average-linkage groups at threshold; small groups become ungrouped."""
    n = len(ps.ids)
    if n == 0:
        return [], []
    if n == 1:
        labels = np.array([1])
    else:
        from scipy.cluster.hierarchy import fcluster, linkage
        from scipy.spatial.distance import squareform

        Z = linkage(squareform(distances, checks=False), method="average")
        labels = fcluster(Z, t=threshold, criterion="distance")
    groups, ungrouped = [], []
    for label in np.unique(labels):
        idx = np.flatnonzero(labels == label)
        members = [ps.ids[i] for i in idx]
        if len(idx) < min_size:
            ungrouped.extend(members)
            continue
        sub = distances[np.ix_(idx, idx)]
        medoid_idx = int(idx[int(np.argmin(sub.sum(1)))])
        groups.append(Group(members, ps.ids[medoid_idx], float(distances[medoid_idx, idx].mean())))
    groups.sort(key=lambda g: (-len(g.members), g.medoid))
    return groups, ungrouped


def suggest_names(groups: list[Group]) -> None:
    """Keep automatic groups unlabeled, even if photos had named labels before regrouping."""
    for i, group in enumerate(groups, 1):
        group.name = f"Group {i}"


def preview(s: Session, threshold: float, min_size: int, samples: int = 6) -> dict[str, Any]:
    ps, distances = cached_matrix(s)
    groups, ungrouped = cluster(ps, distances, threshold, min_size)
    suggest_names(groups)
    no_body = s.exec(select(Photo.id).where(Photo.landmarks_status != "ok")).all()
    out = []
    for group in groups:
        rest = [pid for pid in group.members if pid != group.medoid]
        pick = [group.medoid] + rest[:: max(1, len(rest) // max(1, samples - 1))][: samples - 1]
        out.append({
            "name": group.name,
            "size": len(group.members),
            "medoid_photo_id": group.medoid,
            "spread": round(group.spread, 3),
            "thumb_url": f"/api/photos/{group.medoid}/thumb",
            "sample_urls": [f"/api/photos/{pid}/thumb" for pid in pick],
        })
    return {
        "threshold": threshold,
        "min_size": min_size,
        "photos": len(ps.ids),
        "groups": out,
        "ungrouped": len(ungrouped),
        "without_body": len(no_body),
    }


def _backup_db() -> str:
    dest = config.DB_PATH.with_name(f"physiquelapse.before-grouping-{datetime.now():%Y%m%d-%H%M%S}.db")
    try:
        with closing(sqlite3.connect(config.DB_PATH)) as source, closing(sqlite3.connect(dest)) as backup:
            source.backup(backup)
        return dest.name
    except Exception as e:
        raise RuntimeError(f"could not back up the database before grouping: {e}") from e


def ungrouped_category(s: Session) -> PoseCategory:
    """The catch-all category (rule_key 'other'), shown as 'Ungrouped'."""
    category = s.exec(select(PoseCategory).where(PoseCategory.rule_key == "other")).first()
    if category is None:
        category = PoseCategory(name=UNGROUPED, rule_key="other", sort_order=10_000)
    elif category.name == "Other":
        category.name = UNGROUPED
    category.sort_order = 10_000
    category.reference_photo_id = None
    s.add(category)
    s.flush()
    return category


def apply(s: Session, threshold: float, min_size: int) -> dict[str, Any]:
    """Replace pose categories with discovered groups; back up the database first."""
    backup = _backup_db()
    ps, distances = cached_matrix(s)
    groups, ungrouped = cluster(ps, distances, threshold, min_size)
    suggest_names(groups)
    old = {category.id: category for category in s.exec(select(PoseCategory)).all()}
    manual = {
        photo.id: old[photo.pose_category_id].name
        for photo in s.exec(select(Photo).where(Photo.pose_source == "manual")).all()
        if photo.pose_category_id in old
    }
    other = ungrouped_category(s)
    taken = {other.name}
    new_ids: dict[str, int] = {}
    for category in old.values():
        if category.id != other.id:
            category.name = f"__old_{category.id}__"
            s.add(category)
    s.flush()
    for order, group in enumerate(groups):
        name = group.name if group.name not in taken else f"{group.name} ({order + 1})"
        taken.add(name)
        category = PoseCategory(name=name, sort_order=order, reference_photo_id=group.medoid, align_method="joints", anchor_mode="auto")
        s.add(category)
        s.flush()
        new_ids[name] = category.id
        group.name = name

    group_of: dict[int, tuple[int, float]] = {}
    index = {pid: i for i, pid in enumerate(ps.ids)}
    for group in groups:
        medoid_idx = index[group.medoid]
        for pid in group.members:
            distance = float(distances[index[pid], medoid_idx])
            group_of[pid] = (new_ids[group.name], max(0.0, 1.0 - distance / (2 * threshold)))
    for photo in s.exec(select(Photo)).all():
        if photo.id in manual and manual[photo.id] in new_ids:
            photo.pose_category_id, photo.pose_confidence = new_ids[manual[photo.id]], 1.0
        elif photo.id in group_of:
            photo.pose_category_id, photo.pose_confidence = group_of[photo.id]
            photo.pose_source = "group"
        else:
            photo.pose_category_id, photo.pose_confidence = other.id, None
            photo.pose_source = "group"
        photo.pose_needs_review = False
        photo.updated_at = now()
        s.add(photo)
    s.flush()
    for category in old.values():
        if category.id == other.id:
            continue
        for model in (PoseLabel, PoseExample):
            for row in s.exec(select(model).where(model.pose_category_id == category.id)).all():
                s.delete(row)
        s.delete(category)
    for alignment in s.exec(select(Alignment)).all():
        alignment.stale = True
        alignment.method = "auto"
        s.add(alignment)
    s.commit()
    save_settings(s, {
        "grouping_active": True,
        "grouping_threshold": threshold,
        "grouping_min_size": min_size,
        "pose_setup_done": True,
    })
    clear_cache()
    return {
        "groups": len(groups),
        "ungrouped": len(ungrouped),
        "backup": backup,
        "message": f"{len(groups)} groups, {len(ungrouped)} photos ungrouped",
    }


@job("group_poses", lane="cv", title="Group photos by pose")
def job_group(ctx: JobContext, params: dict) -> dict:
    with ctx.session() as s:
        if s.exec(select(Photo.id)).first() is None:
            return {"groups": 0, "ungrouped": 0, "message": "No photos to group"}
        settings = load_settings(s)
        threshold = float(params.get("threshold", settings.grouping_threshold or DEFAULT_THRESHOLD))
        min_size = int(params.get("min_size", settings.grouping_min_size or DEFAULT_MIN_SIZE))
    ctx.message("Comparing every photo with every other")
    with ctx.session() as s:
        result = apply(s, threshold, min_size)
    from .classify import _queue_abs_check

    _queue_abs_check()
    return {**result, "message": result["message"]}


def assign_new(s: Session, ids: list[int] | None) -> dict[str, int]:
    """Assign unplaced photos to the existing group they align with best."""
    settings = load_settings(s)
    other = ungrouped_category(s)
    groups = [category for category in s.exec(select(PoseCategory).where(PoseCategory.id != other.id)).all() if category.reference_photo_id]
    stmt = select(Photo).where(Photo.pose_source.not_in(("manual", "capture")))
    if ids:
        stmt = stmt.where(Photo.id.in_(ids))
    else:
        stmt = stmt.where((Photo.pose_source.in_(("none", "rule", "knn"))) | (Photo.pose_category_id.is_(None)))
    photos = s.exec(stmt).all()
    if not groups and photos:
        apply(s, settings.grouping_threshold, settings.grouping_min_size)
        return {
            "grouped": sum(photo.pose_category_id != other.id for photo in photos),
            "ungrouped": sum(photo.pose_category_id == other.id for photo in photos),
        }
    refs = load_points(s, [category.reference_photo_id for category in groups])
    ref_category = {pid: next(category.id for category in groups if category.reference_photo_id == pid) for pid in refs.ids}
    counts = {"grouped": 0, "ungrouped": 0}
    for photo in photos:
        target, confidence = other.id, None
        if refs.ids and photo.landmarks_status == "ok" and photo.person_status != "not_me" and not photo.excluded:
            pose = load_points(s, [photo.id])
            if pose.ids:
                distances = fit_errors(pose.pts[0], pose.vis[0], refs.pts, refs.vis, refs.torso, settings.rotation_clamp_deg)
                reverse = np.array([
                    fit_errors(refs.pts[j], refs.vis[j], pose.pts, pose.vis, pose.torso, settings.rotation_clamp_deg)[0]
                    for j in range(len(refs.ids))
                ])
                distances = (distances + reverse) / 2 + view_penalty(
                    bool(pose.face[0]), float(pose.facing[0]), refs.face, refs.facing
                )
                j = int(np.argmin(distances))
                if distances[j] <= settings.grouping_threshold:
                    target = ref_category[refs.ids[j]]
                    confidence = max(0.0, 1.0 - float(distances[j]) / (2 * settings.grouping_threshold))
        photo.pose_category_id, photo.pose_confidence = target, confidence
        photo.pose_source, photo.pose_needs_review = "group", False
        photo.updated_at = now()
        s.add(photo)
        counts["grouped" if target != other.id else "ungrouped"] += 1
    s.commit()
    return counts


def merge(s: Session, cid: int, into: int) -> int:
    """Move every photo from one group into another and delete the source group."""
    if cid == into:
        raise ValueError("can't merge a group into itself")
    source, target = s.get(PoseCategory, cid), s.get(PoseCategory, into)
    if source is None or target is None:
        raise LookupError("group not found")
    if source.rule_key == "other":
        raise ValueError("Ungrouped can't be merged away")
    count = 0
    for photo in s.exec(select(Photo).where(Photo.pose_category_id == cid)).all():
        photo.pose_category_id = into
        photo.updated_at = now()
        s.add(photo)
        alignment = s.get(Alignment, photo.id)
        if alignment is not None:
            alignment.stale = True
            s.add(alignment)
        count += 1
    for model in (PoseLabel, PoseExample):
        for row in s.exec(select(model).where(model.pose_category_id == cid)).all():
            s.delete(row)
    s.delete(source)
    s.commit()
    return count
