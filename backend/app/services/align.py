"""Alignment: map every photo of a pose category onto the category reference.

Similarity transform (scale s, rotation theta, translation t), in pixel
coordinates of the EXIF-corrected, flip-applied image -> canvas pixels.

Reference R is placed fit-to-canvas (x framing zoom, + framing offsets). Its head
anchor, torso length and torso angle on the canvas become targets. For a photo P:
    s     = torso_T / torso_P          (head-size fallback when hips are not visible)
    theta = clamp(angle_T - angle_P)   (rotation basis selectable per category)
    t     = head_T - s Rot(theta) head_P   -> head lands exactly on the target
Residuals are measured on shoulder-mid, hip-mid and eye-mid.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlmodel import Session, select

from .. import config
from ..jobs import JobContext, job
from ..models import Alignment, Photo, PoseCategory, now
from ..settings import AppSettings, load_settings
from . import features as F
from . import images
from . import landmarks as L

log = logging.getLogger(__name__)

VIS = L.VIS
BG_RGB = (24, 24, 27)
HEAD_POINTS = {
    "auto": ("nose", "left_eye", "right_eye", "left_ear", "right_ear"),
    "face": ("nose", "left_eye", "right_eye"),
    "ears": ("left_ear", "right_ear"),
}


# ----------------------------------------------------------------------------- geometry


def _xy(p: dict) -> np.ndarray:
    return np.array([p["x_px"], p["y_px"]], dtype=np.float64)


def _mid(pts: dict, a: str, b: str) -> np.ndarray:
    return (_xy(pts[a]) + _xy(pts[b])) / 2


def _ang(v: np.ndarray) -> float:
    return math.degrees(math.atan2(v[1], v[0]))


def wrap180(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def rot(theta_deg: float) -> np.ndarray:
    t = math.radians(theta_deg)
    c, s = math.cos(t), math.sin(t)
    return np.array([[c, -s], [s, c]])


@dataclass
class Anchors:
    head: np.ndarray
    head_visible: int
    shoulder_mid: np.ndarray
    hip_mid: np.ndarray
    eye_mid: np.ndarray
    hips_visible: bool
    torso: float
    nose_hip: float
    head_size: float
    angle_torso: float
    angle_eyes: float
    angle_shoulders: float

    def length(self, basis: str) -> float:
        return {"torso": self.torso, "nose_hip": self.nose_hip, "head": self.head_size}[basis]

    def angle(self, basis: str) -> float | None:
        return {"torso": self.angle_torso, "eyes": self.angle_eyes, "shoulders": self.angle_shoulders, "none": None}[basis]


def anchors(points: dict[str, dict], anchor_mode: str = "auto") -> Anchors:
    names = HEAD_POINTS.get(anchor_mode, HEAD_POINTS["auto"])
    head = np.mean([_xy(points[n]) for n in names], axis=0)
    head_visible = sum(1 for n in names if points[n]["visibility"] >= VIS)
    sm = _mid(points, "left_shoulder", "right_shoulder")
    hm = _mid(points, "left_hip", "right_hip")
    em = _mid(points, "left_eye", "right_eye")
    hips_visible = max(points["left_hip"]["visibility"], points["right_hip"]["visibility"]) >= VIS
    eyes = np.linalg.norm(_xy(points["left_eye"]) - _xy(points["right_eye"]))
    ears = np.linalg.norm(_xy(points["left_ear"]) - _xy(points["right_ear"]))
    # the axis points "up" the body (hip -> shoulder); eyes/shoulders lines run subject-right -> subject-left
    return Anchors(
        head=head,
        head_visible=head_visible,
        shoulder_mid=sm,
        hip_mid=hm,
        eye_mid=em,
        hips_visible=hips_visible,
        torso=float(np.linalg.norm(sm - hm)),
        nose_hip=float(np.linalg.norm(_xy(points["nose"]) - hm)),
        head_size=float((eyes + ears) / 2),
        angle_torso=_ang(sm - hm),
        angle_eyes=_ang(_xy(points["left_eye"]) - _xy(points["right_eye"])),
        angle_shoulders=_ang(_xy(points["left_shoulder"]) - _xy(points["right_shoulder"])),
    )


@dataclass
class Transform:
    scale: float
    rotation_deg: float
    tx: float
    ty: float

    @property
    def M(self) -> np.ndarray:
        return self.scale * rot(self.rotation_deg)

    @property
    def t(self) -> np.ndarray:
        return np.array([self.tx, self.ty])

    def apply(self, p: np.ndarray) -> np.ndarray:
        return self.M @ p + self.t

    def matrix(self) -> np.ndarray:
        return np.hstack([self.M, self.t.reshape(2, 1)])


@dataclass
class Target:
    canvas_w: int
    canvas_h: int
    ref_transform: Transform
    head: np.ndarray
    shoulder_mid: np.ndarray
    hip_mid: np.ndarray
    eye_mid: np.ndarray
    lengths: dict[str, float]
    angles: dict[str, float | None]
    facing: float = 0.0
    points: dict[str, tuple[np.ndarray, float]] = field(default_factory=dict)  # reference body points on the canvas


def fit_transform(w: int, h: int, W: int, H: int, zoom: float = 1.0, dx: float = 0.0, dy: float = 0.0) -> Transform:
    s = min(W / w, H / h) * zoom
    tx = (W - s * w) / 2 + dx * W
    ty = (H - s * h) / 2 + dy * H
    return Transform(s, 0.0, tx, ty)


def make_target(ref_points: dict[str, dict], ref_w: int, ref_h: int, cat: PoseCategory, W: int, H: int) -> Target:
    tr = fit_transform(ref_w, ref_h, W, H, cat.framing_zoom, cat.framing_dx, cat.framing_dy)
    a = anchors(ref_points, cat.anchor_mode)
    return Target(
        canvas_w=W,
        canvas_h=H,
        ref_transform=tr,
        head=tr.apply(a.head),
        shoulder_mid=tr.apply(a.shoulder_mid),
        hip_mid=tr.apply(a.hip_mid),
        eye_mid=tr.apply(a.eye_mid),
        lengths={b: tr.scale * a.length(b) for b in ("torso", "nose_hip", "head")},
        angles={b: a.angle(b) for b in ("torso", "eyes", "shoulders", "none")},
        points={n: (tr.apply(_xy(ref_points[n])), float(ref_points[n]["visibility"])) for n in FIT_WEIGHTS if n in ref_points},
    )


# Body fit: stable points only (arms move between flexes). Torso points weigh more
# because the torso is what a physique time-lapse is about.
FIT_WEIGHTS = {
    "nose": 1.0,
    "left_eye": 0.5,
    "right_eye": 0.5,
    "left_ear": 0.5,
    "right_ear": 0.5,
    "left_shoulder": 2.0,
    "right_shoulder": 2.0,
    "left_hip": 1.5,
    "right_hip": 1.5,
}
TORSO_POINTS = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")


def fit_similarity(src: np.ndarray, dst: np.ndarray, w: np.ndarray, clamp_deg: float | None) -> tuple[Transform, float]:
    """Weighted least-squares similarity (2-D Umeyama, no reflection). Returns
    (transform, unclamped rotation in degrees). When the rotation is clamped the
    scale and translation are re-solved for the clamped angle."""
    W = w.sum()
    mp, mq = (w[:, None] * src).sum(0) / W, (w[:, None] * dst).sum(0) / W
    P, Q = src - mp, dst - mq
    a = float((w * (P[:, 0] * Q[:, 0] + P[:, 1] * Q[:, 1])).sum())
    b = float((w * (P[:, 0] * Q[:, 1] - P[:, 1] * Q[:, 0])).sum())
    var = float((w * (P**2).sum(1)).sum())
    theta_free = math.degrees(math.atan2(b, a))
    theta = theta_free
    if clamp_deg is not None and abs(theta) > clamp_deg:
        theta = math.copysign(clamp_deg, theta)
    R = rot(theta)
    s = float((w * ((P @ R.T) * Q).sum(1)).sum()) / var if var > 1e-9 else 1.0
    t = mq - s * (R @ mp)
    return Transform(s, theta, float(t[0]), float(t[1])), theta_free


def solve_body(points: dict[str, dict], target: Target, clamp_deg: float, use_rotation: bool) -> tuple[Transform, dict[str, Any]] | None:
    names = [
        n for n in FIT_WEIGHTS
        if n in points and n in target.points and points[n]["visibility"] >= VIS and target.points[n][1] >= VIS
    ]
    if len(names) < 3 or sum(1 for n in names if n in TORSO_POINTS) < 2:
        return None  # not enough shared, visible points: caller falls back to the head anchor
    src = np.array([_xy(points[n]) for n in names])
    dst = np.array([target.points[n][0] for n in names])
    w = np.array([FIT_WEIGHTS[n] for n in names])
    tr, free = fit_similarity(src, dst, w, clamp_deg if use_rotation else 0.0)
    flags: dict[str, Any] = {}
    if use_rotation and abs(free) > clamp_deg:
        flags["rotation_clamped"] = round(free, 1)
    if not any(n in names for n in ("left_hip", "right_hip")):
        flags["no_hips_in_fit"] = True
    return tr, flags


def solve(points: dict[str, dict], target: Target, cat: PoseCategory, clamp_deg: float) -> tuple[Transform, dict[str, Any], str]:
    """Auto solve. Returns (transform, flags, scale_basis_used)."""
    if (cat.align_method or "body") == "body":
        res = solve_body(points, target, clamp_deg, use_rotation=cat.rotation_basis != "none")
        if res is not None:
            tr, flags = res
            if sum(1 for n in HEAD_POINTS["auto"] if points[n]["visibility"] >= VIS) < 2:
                flags["low_anchor_confidence"] = True
            return tr, flags, "body"
    a = anchors(points, cat.anchor_mode)
    flags: dict[str, Any] = {}
    basis = cat.scale_basis
    if basis in ("torso", "nose_hip") and not a.hips_visible:
        basis = "head"
        flags["scale_from_head"] = True
    length = a.length(basis)
    if length < 1e-3 or target.lengths[basis] < 1e-3:
        raise ValueError(f"degenerate {basis} length")
    s = target.lengths[basis] / length
    theta = 0.0
    tgt_angle = target.angles.get(cat.rotation_basis)
    p_angle = a.angle(cat.rotation_basis)
    if tgt_angle is not None and p_angle is not None:
        theta = wrap180(tgt_angle - p_angle)
        if abs(theta) > clamp_deg:
            flags["rotation_clamped"] = round(theta, 1)
            theta = math.copysign(clamp_deg, theta)
    if a.head_visible < 2:
        flags["low_anchor_confidence"] = True
    M = s * rot(theta)
    t = target.head - M @ a.head
    return Transform(float(s), float(theta), float(t[0]), float(t[1])), flags, basis


def solve_two_point(src_a: np.ndarray, src_b: np.ndarray, dst_a: np.ndarray, dst_b: np.ndarray) -> Transform:
    """Exact similarity mapping src_a->dst_a and src_b->dst_b (manual anchor editing)."""
    vs, vd = src_b - src_a, dst_b - dst_a
    ls, ld = np.linalg.norm(vs), np.linalg.norm(vd)
    if ls < 1e-6:
        raise ValueError("anchor points coincide")
    s = ld / ls
    theta = wrap180(_ang(vd) - _ang(vs))
    M = s * rot(theta)
    t = dst_a - M @ src_a
    return Transform(float(s), float(theta), float(t[0]), float(t[1]))


def residuals(points: dict[str, dict], tr: Transform, target: Target, anchor_mode: str = "auto") -> dict[str, float]:
    a = anchors(points, anchor_mode)
    out = {
        "head": float(np.linalg.norm(tr.apply(a.head) - target.head)),
        "shoulder_mid": float(np.linalg.norm(tr.apply(a.shoulder_mid) - target.shoulder_mid)),
        "hip_mid": float(np.linalg.norm(tr.apply(a.hip_mid) - target.hip_mid)),
        "eye_mid": float(np.linalg.norm(tr.apply(a.eye_mid) - target.eye_mid)),
    }
    out["mean"] = float(np.mean([out["shoulder_mid"], out["hip_mid"], out["eye_mid"]]))
    return out


# ----------------------------------------------------------------------------- rendering


def render(photo: Photo, tr: Transform, W: int, H: int, *, bg_removal: bool = False) -> np.ndarray:
    import cv2

    img, _ = images.open_image(config.abs_path(photo.original_path))
    try:
        rgb = np.asarray(img)
    finally:
        img.close()
    if photo.flip_horizontal:
        rgb = np.ascontiguousarray(rgb[:, ::-1])
    if bg_removal:
        rgb = _remove_bg(rgb)
    M = tr.matrix().copy()
    s = tr.scale
    if s < 0.95:
        # pre-shrink with area interpolation to avoid aliasing, then warp the smaller image
        f = max(s, 1e-3) * 1.05
        small = cv2.resize(rgb, (max(1, round(rgb.shape[1] * f)), max(1, round(rgb.shape[0] * f))), interpolation=cv2.INTER_AREA)
        fx, fy = small.shape[1] / rgb.shape[1], small.shape[0] / rgb.shape[0]
        M[:, 0] /= fx
        M[:, 1] /= fy
        rgb = small
    return cv2.warpAffine(rgb, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=BG_RGB)


def _remove_bg(rgb: np.ndarray) -> np.ndarray:
    try:
        from rembg import remove  # optional extra
    except ImportError:
        return rgb
    rgba = np.asarray(remove(rgb))
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    bg = np.empty_like(rgb, dtype=np.float32)
    bg[:] = BG_RGB
    return (rgba[..., :3].astype(np.float32) * alpha + bg * (1 - alpha)).astype(np.uint8)


def write_output(photo_id: int, canvas: np.ndarray) -> str:
    from PIL import Image

    path = config.ALIGNED_DIR / f"{photo_id}.jpg"
    images.save_jpeg(Image.fromarray(canvas), path, quality=92)
    return config.rel(path)


# ----------------------------------------------------------------------------- category orchestration


@dataclass
class CategoryContext:
    cat: PoseCategory
    reference: Photo
    target: Target
    settings: AppSettings
    ref_facing: float = 0.0
    errors: list[str] = field(default_factory=list)


def _fit_cost(src: dict[str, dict], ref: dict[str, dict], clamp_deg: float) -> float | None:
    """Leftover error (in reference torso lengths) after fitting `src` onto `ref` with the
    body fit and the rotation clamp. None when the two share too few visible points."""
    names = [n for n in FIT_WEIGHTS if n in src and n in ref and src[n]["visibility"] >= VIS and ref[n]["visibility"] >= VIS]
    if len(names) < 3 or sum(1 for n in names if n in TORSO_POINTS) < 2:
        return None
    P = np.array([_xy(src[n]) for n in names])
    Q = np.array([_xy(ref[n]) for n in names])
    w = np.array([FIT_WEIGHTS[n] for n in names])
    tr, _ = fit_similarity(P, Q, w, clamp_deg)
    resid = np.linalg.norm((P @ tr.M.T + tr.t) - Q, axis=1)
    T = anchors(ref).torso or 1.0
    return float(np.sqrt((w * resid**2).sum() / w.sum()) / T)


def pick_default_reference(s: Session, cat: PoseCategory, clamp_deg: float = 8.0) -> Photo | None:
    """The photo every other photo of the category aligns to best: for each candidate,
    fit all others onto it (body fit, rotation clamp) and sum the leftover error.
    The earliest photo is often an outlier (other framing, lean or distance), which
    would drag every other frame off. Ties go to the earliest photo."""
    rows = s.exec(
        select(Photo)
        .where(Photo.pose_category_id == cat.id, Photo.landmarks_status == "ok", Photo.excluded == False)  # noqa: E712
        .order_by(Photo.taken_at.is_(None), Photo.taken_at.asc(), Photo.id.asc())
    ).all()
    good = [p for p in rows if (p.landmarks_quality or 0) >= 0.6] or list(rows)
    if len(good) <= 2:
        return good[0] if good else None
    if len(good) > 120:  # keep it O(n^2)-cheap on huge categories
        step = len(good) / 120
        good = [good[int(i * step)] for i in range(120)]
    pts: list[tuple[Photo, dict]] = []
    for p in good:
        data = L.load_for_photo(p)
        if data and data.get("detected"):
            pts.append((p, L.points_dict(data)))
    if not pts:
        return good[0]
    best, best_cost = pts[0][0], math.inf
    for p, ref in pts:
        costs = [_fit_cost(q, ref, clamp_deg) for o, q in pts if o.id != p.id]
        costs = [c if c is not None else 1.0 for c in costs]  # can't fit -> heavy penalty
        cost = float(np.sum(np.minimum(costs, 1.0)))  # one wild photo can't dominate
        if cost < best_cost - 1e-9:
            best, best_cost = p, cost
    return best


def category_context(s: Session, cat: PoseCategory, settings: AppSettings, *, auto_pick: bool = True) -> CategoryContext:
    ref = s.get(Photo, cat.reference_photo_id) if cat.reference_photo_id else None
    if ref is None and auto_pick:
        ref = pick_default_reference(s, cat, settings.rotation_clamp_deg)
        if ref is not None:
            cat.reference_photo_id = ref.id
            s.add(cat)
            s.commit()
    if ref is None:
        raise LookupError(f"{cat.name}: no reference photo with landmarks")
    data = L.load_for_photo(ref)
    if not data or not data.get("detected"):
        raise LookupError(f"{cat.name}: the reference photo has no landmarks; run landmark extraction or pick another reference")
    pts = L.points_dict(data)
    tgt = make_target(pts, ref.width, ref.height, cat, settings.canvas_w, settings.canvas_h)
    vec = F.compute(data)
    return CategoryContext(cat=cat, reference=ref, target=tgt, settings=settings, ref_facing=F.facing_side(vec) if vec else 0.0)


def maybe_normalize_facing(s: Session, photo: Photo, cc: CategoryContext) -> bool:
    """Auto-flip a photo whose facing is opposite to the reference. Manual flips are respected."""
    if not cc.cat.normalize_facing or photo.flip_source == "manual" or photo.id == cc.reference.id:
        return False
    if abs(cc.ref_facing) < 0.3:
        return False
    data = L.load_for_photo(photo)
    vec = F.compute(data) if data else None
    if vec is None:
        return False
    f = F.facing_side(vec)
    if abs(f) >= 0.3 and math.copysign(1, f) != math.copysign(1, cc.ref_facing):
        photo.flip_horizontal = not photo.flip_horizontal
        photo.flip_source = "auto"
        s.add(photo)
        return True
    return False


def align_photo(s: Session, photo: Photo, cc: CategoryContext, *, keep_manual: bool = True) -> Alignment:
    W, H = cc.settings.canvas_w, cc.settings.canvas_h
    a = s.get(Alignment, photo.id) or Alignment(photo_id=photo.id)
    flags: dict[str, Any] = {}
    basis_used = "fit"
    if keep_manual and a.method == "manual" and a.canvas_w == W and a.canvas_h == H:
        tr = Transform(a.scale, a.rotation_deg, a.tx, a.ty)
        flags = dict(a.flags or {})
        if a.reference_photo_id != cc.reference.id:
            flags["manual_reference_changed"] = True
        basis_used = a.scale_basis_used
        method = "manual"
    else:
        method = "auto"
        maybe_normalize_facing(s, photo, cc)
        if photo.id == cc.reference.id:
            tr = cc.target.ref_transform
            basis_used = cc.cat.scale_basis
        else:
            data = L.load_for_photo(photo)
            if data and data.get("detected"):
                tr, flags, basis_used = solve(L.points_dict(data), cc.target, cc.cat, cc.settings.rotation_clamp_deg)
            else:
                tr = fit_transform(photo.width, photo.height, W, H)
                flags["no_landmarks"] = True
    resid = None
    data = L.load_for_photo(photo)
    if data and data.get("detected"):
        resid = residuals(L.points_dict(data), tr, cc.target, cc.cat.anchor_mode)["mean"]
    canvas = render(photo, tr, W, H, bg_removal=cc.settings.bg_removal)
    a.output_path = write_output(photo.id, canvas)
    a.reference_photo_id = cc.reference.id
    a.pose_category_id = cc.cat.id
    a.canvas_w, a.canvas_h = W, H
    a.scale, a.rotation_deg, a.tx, a.ty = tr.scale, tr.rotation_deg, tr.tx, tr.ty
    a.method = method
    a.scale_basis_used = basis_used
    a.flags = flags
    a.residual_px = round(resid, 2) if resid is not None else None
    a.stale = False
    a.computed_at = now()
    s.add(a)
    return a


def needs_work(a: Alignment | None, photo: Photo, cc: CategoryContext) -> bool:
    if a is None or a.stale or not a.output_path:
        return True
    if a.reference_photo_id != cc.reference.id or a.pose_category_id != cc.cat.id:
        return True
    if (a.canvas_w, a.canvas_h) != (cc.settings.canvas_w, cc.settings.canvas_h):
        return True
    return not config.abs_path(a.output_path).exists()


@job("align_photos", lane="cv", title="Align photos")
def job_align(ctx: JobContext, params: dict) -> dict:
    cid = params.get("pose_category_id")
    ids = params.get("ids")
    force = bool(params.get("force"))
    done_total, skipped_cats, errors = 0, [], []
    with ctx.session() as s:
        settings = load_settings(s)
        if ids:
            cat_ids = sorted({p.pose_category_id for p in s.exec(select(Photo).where(Photo.id.in_(ids))).all() if p.pose_category_id})
        elif cid:
            cat_ids = [int(cid)]
        else:
            cat_ids = [c.id for c in s.exec(select(PoseCategory).order_by(PoseCategory.sort_order)).all()]
        plans: list[tuple[int, list[int]]] = []
        for c_id in cat_ids:
            cat = s.get(PoseCategory, c_id)
            if cat is None:
                continue
            try:
                cc = category_context(s, cat, settings)
            except LookupError as e:
                stmt_n = select(Photo.id).where(Photo.pose_category_id == c_id)
                if s.exec(stmt_n).first() is not None:
                    skipped_cats.append(str(e))
                continue
            stmt = select(Photo).where(Photo.pose_category_id == c_id)
            if ids:
                stmt = stmt.where(Photo.id.in_(ids))
            todo = [
                p.id for p in s.exec(stmt.order_by(Photo.taken_at)).all()
                if force or ids or needs_work(s.get(Alignment, p.id), p, cc)
            ]
            if todo:
                plans.append((c_id, todo))
    total = sum(len(t) for _, t in plans)
    ctx.progress(0, total, f"Aligning {total} photos")
    i = 0
    for c_id, todo in plans:
        with ctx.session() as s:
            cat = s.get(PoseCategory, c_id)
            cc = category_context(s, cat, settings)
            for pid in todo:
                ctx.check_cancelled()
                photo = s.get(Photo, pid)
                if photo is None:
                    continue
                try:
                    align_photo(s, photo, cc, keep_manual=not force)
                    s.commit()
                    done_total += 1
                except Exception as e:
                    s.rollback()
                    log.warning("align failed for %s: %s", pid, e)
                    errors.append(f"photo {pid}: {e}")
                i += 1
                ctx.progress(i, total, f"{cat.name}: {i}/{total}")
    msg = f"Aligned {done_total} photos"
    if skipped_cats:
        msg += f"; skipped: {'; '.join(skipped_cats)}"
    if errors:
        msg += f"; {len(errors)} failed"
    return {"message": msg, "aligned": done_total, "skipped_categories": skipped_cats, "errors": errors[:20]}


# ----------------------------------------------------------------------------- evaluation


def evaluate_category(s: Session, cat: PoseCategory, settings: AppSettings) -> dict[str, Any]:
    try:
        cc = category_context(s, cat, settings, auto_pick=False)
    except LookupError as e:
        return {"category": cat.name, "error": str(e), "rows": [], "summary": {}}
    H = settings.canvas_h
    rows = []
    flag_counts: dict[str, int] = {}
    for p in s.exec(select(Photo).where(Photo.pose_category_id == cat.id).order_by(Photo.taken_at)).all():
        a = s.get(Alignment, p.id)
        if a is None:
            continue
        for k, v in (a.flags or {}).items():
            if v:
                flag_counts[k] = flag_counts.get(k, 0) + 1
        data = L.load_for_photo(p)
        if not data or not data.get("detected") or p.id == cc.reference.id:
            continue
        r = residuals(L.points_dict(data), Transform(a.scale, a.rotation_deg, a.tx, a.ty), cc.target, cat.anchor_mode)
        rows.append({"photo_id": p.id, "taken_at": p.taken_at.isoformat() if p.taken_at else None, "method": a.method, "stale": a.stale, **{k: round(v, 2) for k, v in r.items()}})

    def stats(key: str) -> dict[str, float]:
        v = np.array([r[key] for r in rows]) if rows else np.array([0.0])
        return {
            "mean_px": round(float(v.mean()), 2),
            "median_px": round(float(np.median(v)), 2),
            "p95_px": round(float(np.percentile(v, 95)), 2),
            "mean_pct_h": round(float(v.mean()) / H * 100, 3),
            "p95_pct_h": round(float(np.percentile(v, 95)) / H * 100, 3),
        }

    return {
        "category": cat.name,
        "reference_photo_id": cc.reference.id,
        "canvas": [settings.canvas_w, H],
        "n": len(rows),
        "summary": {k: stats(k) for k in ("mean", "shoulder_mid", "hip_mid", "eye_mid", "head")},
        "flags": flag_counts,
        "rows": rows,
    }
