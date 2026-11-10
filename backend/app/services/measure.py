"""Body-size estimates from a photo: body-outline mask + pose landmarks.

Every measurement is a 2-D width across the body outline (clothes included),
divided by the torso length T = |shoulder-mid - hip-mid| in the same photo. The
skeleton does not grow when muscles do, so T works as a per-photo ruler and the
ratios are comparable across photos taken at any distance. Widths are only
comparable within the same pose category (a side shot measures depth, not width).

A measurement is marked invalid instead of guessed when the scan line runs into
another body part (an arm resting against the torso, an arm raised along the
shoulder line) or off the image.

Optional: real units. When the user logs tape measurements (Stats page) for the
matching metric, a per-category factor k (inches = k * ratio) is fitted by least
squares over dates that have both.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
from PIL import Image
from sqlmodel import Session, select

from .. import config
from ..db import engine
from ..jobs import JobContext, job
from ..models import Metric, MetricEntry, Photo, PhotoMeasurement, now
from ..settings import load_settings
from . import images
from . import landmarks as L

log = logging.getLogger(__name__)

MEASURE_VERSION = 4
VIS = 0.5

# name -> (label, description, tape metric names used for calibration)
DEFINITIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "shoulders": ("Shoulder width", "outer delt to outer delt, just below the shoulder joints", ("shoulders", "shoulder")),
    "lats": ("Lat width", "torso width just below the armpits (arms must be away from the body)", ("lats", "chest", "back")),
    "waist": ("Waist width", "torso width just above the hips", ("waist",)),
    "vtaper": ("V-taper", "shoulder width / waist width", ()),
    "arm": ("Upper arm", "upper-arm thickness at the biceps (bigger side)", ("arm", "arms", "bicep", "biceps", "upper arm")),
    "forearm": ("Forearm", "forearm thickness near the elbow (bigger side)", ("forearm", "forearms")),
}
RATIO_NAMES = [n for n in DEFINITIONS if n != "vtaper"]

# Which pose types give the most comparable reading for each measurement (first match with
# enough photos is the default chart). Torso widths need the body square to the camera;
# arm thickness needs the arm away from the body.
_FRONT = ("selfie_front_left", "selfie_front_right")
_FLEX = ("selfie_flex_left", "selfie_flex_right")
PREFERRED_POSES: dict[str, tuple[str, ...]] = {
    "shoulders": (*_FRONT, "front_relaxed", "front_double_bicep", *_FLEX, "front_lat_spread"),
    "lats": ("front_lat_spread", "front_double_bicep", *_FLEX, "back_double_bicep"),
    "waist": (*_FRONT, "front_relaxed", "front_double_bicep", "front_lat_spread", *_FLEX),
    "vtaper": (*_FRONT, "front_relaxed", "front_double_bicep", "front_lat_spread", *_FLEX),
    "arm": (*_FLEX, "front_double_bicep", "back_double_bicep", "selfie_side_90_left", "selfie_side_90_right"),
    "forearm": ("front_relaxed", "front_double_bicep", *_FLEX, *_FRONT, "selfie_side_down_left", "selfie_side_down_right"),
}


# ----------------------------------------------------------------------------- mask io


def mask_path(photo_id: int):
    return config.DERIVED_DIR / "masks" / f"{photo_id}.png"


def save_mask(photo_id: int, mask: np.ndarray) -> None:
    p = mask_path(photo_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    m = np.clip(np.asarray(mask, dtype=np.float32).squeeze(), 0, 1)
    Image.fromarray((m * 255).astype(np.uint8)).save(p, optimize=True)


def load_mask(photo_id: int) -> np.ndarray | None:
    p = mask_path(photo_id)
    if not p.exists():
        return None
    with Image.open(p) as im:
        return np.asarray(im.convert("L")) >= 128


# ----------------------------------------------------------------------------- geometry


def _xy(p: dict) -> np.ndarray:
    return np.array([p["x_px"], p["y_px"]], dtype=np.float64)


@dataclass
class Run:
    a: np.ndarray  # end points in original-image px
    b: np.ndarray
    length: float
    touches_edge: bool


def scan(mask: np.ndarray, sx: float, sy: float, center: np.ndarray, direction: np.ndarray, max_len: float, gap: int = 2) -> Run | None:
    """Contiguous mask run through `center` along +-`direction` (unit vector, original px).
    `sx, sy` map original px -> mask px. Gaps up to `gap` mask px are bridged."""
    h, w = mask.shape
    d = direction / (np.linalg.norm(direction) or 1.0)
    step = 1.0 / max(sx, sy)  # one mask pixel, in original px
    cx, cy = center
    mx, my = int(round(cx * sx)), int(round(cy * sy))
    if not (0 <= mx < w and 0 <= my < h) or not mask[my, mx]:
        return None
    ends, edge = [], False
    for sign in (1.0, -1.0):
        t, miss, last_in = 0.0, 0, 0.0
        while t < max_len:
            t += step
            x, y = cx + sign * d[0] * t, cy + sign * d[1] * t
            ix, iy = int(round(x * sx)), int(round(y * sy))
            if not (0 <= ix < w and 0 <= iy < h):
                edge = True
                break
            if mask[iy, ix]:
                last_in, miss = t, 0
            else:
                miss += 1
                if miss > gap:
                    break
        else:
            edge = True  # ran to max_len: not a closed run
        ends.append(center + sign * d * last_in)
    return Run(ends[0], ends[1], float(np.linalg.norm(ends[0] - ends[1])), edge)


def _segment_cross(p: np.ndarray, q: np.ndarray, origin: np.ndarray, up: np.ndarray, level: float) -> np.ndarray | None:
    """Where segment p-q crosses the line {origin + up*level + perp*s}; None if it doesn't."""
    hp, hq = np.dot(p - origin, up), np.dot(q - origin, up)
    if (hp - level) * (hq - level) > 0 or hp == hq:
        return None
    t = (level - hp) / (hq - hp)
    return p + t * (q - p)


def measure(points: dict[str, dict], mask: np.ndarray, image_w: int, image_h: int) -> dict[str, Any]:
    """All measurements for one photo. Points are in original px of the un-flipped image."""
    need = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
    if any(points[n]["visibility"] < VIS for n in need):
        return {"torso_px": None, "items": {}, "reason": "shoulders or hips not visible"}
    sx, sy = mask.shape[1] / image_w, mask.shape[0] / image_h
    ls, rs, lh, rh = (_xy(points[n]) for n in need)
    sm, hm = (ls + rs) / 2, (lh + rh) / 2
    T = float(np.linalg.norm(sm - hm))
    if T < 20:
        return {"torso_px": T, "items": {}, "reason": "person too small in the frame"}
    up = (sm - hm) / T
    perp = np.array([-up[1], up[0]])
    items: dict[str, Any] = {}

    def put(name: str, run: Run | None, valid: bool, why: str = "") -> None:
        if run is None:
            items[name] = {"valid": False, "why": why or "not on the body outline"}
            return
        ok = bool(valid and not run.touches_edge)
        items[name] = {
            "valid": ok,
            "ratio": round(run.length / T, 4),
            "px": round(run.length, 1),
            "line": [[round(float(v), 1) for v in run.a], [round(float(v), 1) for v in run.b]],
            **({} if ok else {"why": why or "runs off the photo"}),
        }

    arms = {
        side: [_xy(points[f"{side}_shoulder"]), _xy(points[f"{side}_elbow"]), _xy(points[f"{side}_wrist"])]
        for side in ("left", "right")
    }

    def torso_width(name: str, level: float, why_merge: str) -> None:
        """Torso width at `level` (in T, measured down from the shoulder line)."""
        c = sm - up * level * T
        run = scan(mask, sx, sy, c, perp, 1.6 * T)
        if run is None:
            put(name, None, False)
            return
        valid = True
        for s_, e_, w_ in arms.values():
            for p, q in ((s_, e_), (e_, w_)):
                x = _segment_cross(p, q, sm, up, -level * T)
                if x is None:
                    continue
                arm_off = abs(np.dot(x - c, perp))
                end = run.a if np.dot(run.a - c, perp) * np.dot(x - c, perp) > 0 else run.b
                if abs(np.dot(end - c, perp)) > arm_off - 0.05 * T:
                    valid = False  # the outline reaches the arm: arm and torso merged
        put(name, run, valid, why_merge)

    # shoulders: widest run across the delts, ends must stay close to the joints (else an arm is in line)
    best = None
    for level in (0.04, 0.08, 0.12, 0.16):
        c = sm - up * level * T
        run = scan(mask, sx, sy, c, perp, 1.6 * T)
        if run is not None and (best is None or run.length > best.length):
            best = run
    if best is not None:
        half = np.linalg.norm(ls - rs) / 2
        reach = max(abs(np.dot(best.a - sm, perp)), abs(np.dot(best.b - sm, perp)))
        put("shoulders", best, bool(reach <= half + 0.25 * T), "an arm is raised along the shoulder line")
    else:
        put("shoulders", None, False)

    torso_width("lats", 0.30, "arms are touching the torso at armpit level")
    torso_width("waist", 0.82, "arms or hands are touching the waist")

    sh, wa = items.get("shoulders", {}), items.get("waist", {})
    if sh.get("valid") and wa.get("valid") and wa["px"] > 0:
        items["vtaper"] = {"valid": True, "ratio": round(sh["px"] / wa["px"], 4)}
    else:
        items["vtaper"] = {"valid": False, "why": "needs valid shoulder and waist widths"}

    def elbow_angle(side: str) -> float:
        s_, e_, w_ = arms[side]
        v1, v2 = s_ - e_, w_ - e_
        n = np.linalg.norm(v1) * np.linalg.norm(v2)
        return 180.0 if n < 1e-9 else math.degrees(math.acos(float(np.clip(np.dot(v1, v2) / n, -1, 1))))

    def clear_of_torso(run: Run) -> bool:
        """True when a gap separates the limb from the torso at that height. If they touch,
        the outline merges and the torso run reaches past the limb's inner edge."""
        mid = (run.a + run.b) / 2
        level = float(np.dot(mid - sm, up))
        if level > 0.05 * T:
            return True  # above the shoulders (raised arm): nothing to merge with
        m = sm + up * level
        side = np.sign(np.dot(mid - m, perp)) or 1.0
        inner = min(abs(np.dot(run.a - m, perp)), abs(np.dot(run.b - m, perp)))
        trun = scan(mask, sx, sy, m, perp, 1.6 * T)
        if trun is None:
            return True
        torso_edge = max(np.dot(trun.a - m, perp) * side, np.dot(trun.b - m, perp) * side)
        return bool(torso_edge < inner - 0.01 * T)

    def limb(name: str, a: str, b: str, fractions: tuple[float, ...], max_ratio: float, min_bone: float, min_elbow: float,
             need_gap: bool = False) -> None:
        per_side = {}
        for side in ("left", "right"):
            pa, pb = _xy(points[f"{side}_{a}"]), _xy(points[f"{side}_{b}"])
            if min(points[f"{side}_{a}"]["visibility"], points[f"{side}_{b}"]["visibility"]) < VIS:
                continue
            if elbow_angle(side) < min_elbow:
                continue  # forearm folded onto the biceps, or holding the phone: not measurable
            bone = pb - pa
            blen = np.linalg.norm(bone)
            if blen < min_bone * T:
                continue  # bone pointing at the camera (foreshortened): width is not comparable
            n = np.array([-bone[1], bone[0]]) / blen
            best_run = None
            for f in fractions:
                run = scan(mask, sx, sy, pa + bone * f, n, max_ratio * 1.5 * T)
                if run is not None and (best_run is None or run.length > best_run.length):
                    best_run = run
            if best_run is None:
                continue
            ok = best_run.length <= max_ratio * T and not best_run.touches_edge and (not need_gap or clear_of_torso(best_run))
            per_side[side] = (best_run, ok)
        good = {k: v for k, v in per_side.items() if v[1]}
        if good:
            side, (run, _) = max(good.items(), key=lambda kv: kv[1][0].length)
            put(name, run, True)
            items[name]["side"] = side
        elif per_side:
            run = max(per_side.values(), key=lambda v: v[0].length)[0]
            put(name, run, False, "limb is pressed against the body or another limb")
        else:
            items[name] = {"valid": False, "why": "limb not visible"}

    limb("arm", "shoulder", "elbow", (0.45, 0.55, 0.65), 0.55, 0.3, 0.0)
    # the upper arm attaches at the shoulder, so only the forearm needs a visible gap to the torso
    limb("forearm", "elbow", "wrist", (0.2, 0.3, 0.4), 0.38, 0.35, 70.0, need_gap=True)
    return {"torso_px": round(T, 1), "items": items}


# ----------------------------------------------------------------------------- job


def compute_for_photo(s: Session, photo: Photo, *, redetect: bool = True) -> PhotoMeasurement | None:
    raw = L.load_raw(photo.id)
    if not raw or not raw.get("detected"):
        return None
    mask = load_mask(photo.id)
    if mask is None and redetect:
        mask = _detect_mask(photo, raw)
    pm = s.get(PhotoMeasurement, photo.id) or PhotoMeasurement(photo_id=photo.id)
    if mask is None:
        pm.data, pm.torso_px = {"reason": "no body outline available"}, None
    else:
        res = measure(L.points_dict(raw), mask, raw["image_w"], raw["image_h"])
        pm.data = res.get("items", {})
        if res.get("reason"):
            pm.data = {"reason": res["reason"]}
        pm.torso_px = res.get("torso_px")
    pm.version = MEASURE_VERSION
    pm.computed_at = now()
    s.add(pm)
    return pm


def _detect_mask(photo: Photo, raw: dict) -> np.ndarray | None:
    """Re-run the pose model only to get the outline (for photos extracted before masks existed)."""
    from .pose_backend import get_backend

    with Session(engine()) as s2:
        variant = load_settings(s2).pose_model
    be = get_backend(variant)
    fn = getattr(be, "detect_with_masks", None)
    if fn is None:
        return None
    img, _ = images.open_image(config.abs_path(photo.original_path))
    try:
        small = images.resize_long_side(img, 1280)
        poses, masks = fn(np.asarray(small), num_poses=1)
    finally:
        img.close()
    if not poses or masks is None or masks[0] is None:
        return None
    save_mask(photo.id, masks[0])
    return load_mask(photo.id)


@job("measure_photos", lane="cv", title="Measure photos")
def job_measure(ctx: JobContext, params: dict) -> dict:
    force = bool(params.get("force"))
    ids = params.get("ids")
    with ctx.session() as s:
        stmt = select(Photo).where(Photo.landmarks_status == "ok")
        if ids:
            stmt = stmt.where(Photo.id.in_(ids))
        photos = s.exec(stmt.order_by(Photo.id)).all()
        todo = []
        for p in photos:
            pm = s.get(PhotoMeasurement, p.id)
            if force or ids or pm is None or pm.version != MEASURE_VERSION:
                todo.append(p.id)
    total = len(todo)
    if not total:
        return {"message": "All photos are already measured", "measured": 0}
    ctx.progress(0, total, "Measuring")
    valid = 0
    for i, pid in enumerate(todo, 1):
        ctx.check_cancelled()
        with ctx.session() as s:
            p = s.get(Photo, pid)
            try:
                pm = compute_for_photo(s, p)
                s.commit()
                if pm is not None and any(v.get("valid") for v in (pm.data or {}).values() if isinstance(v, dict)):
                    valid += 1
            except Exception as e:
                s.rollback()
                log.warning("measure failed for %s: %s", pid, e)
        ctx.progress(i, total, f"Measured {i}/{total}")
    return {"message": f"Measured {total} photos ({valid} with at least one usable measurement)", "measured": total}


# ----------------------------------------------------------------------------- series, smoothing, calibration


def rolling_median(points: list[tuple[date, float]], days: int) -> list[tuple[date, float]]:
    """Centered rolling median over +-days/2 around each point (points sorted by date)."""
    out = []
    half = timedelta(days=days / 2)
    for d, _ in points:
        win = [v for e, v in points if d - half <= e <= d + half]
        out.append((d, float(np.median(win))))
    return out


def tape_metric(s: Session, name: str) -> Metric | None:
    names = DEFINITIONS[name][2]
    if not names:
        return None
    for m in s.exec(select(Metric)).all():
        if m.name.strip().lower() in names:
            return m
    return None


def fit_calibration(pairs: list[tuple[float, float]]) -> float | None:
    """Least-squares k for tape = k * ratio (through the origin)."""
    if not pairs:
        return None
    r = np.array([p[0] for p in pairs])
    t = np.array([p[1] for p in pairs])
    den = float((r * r).sum())
    return float((r * t).sum() / den) if den > 0 else None


def series(s: Session, name: str, pose_id: int | None, smooth_days: int, window_days: int) -> dict[str, Any]:
    if name not in DEFINITIONS:
        raise KeyError(name)
    stmt = select(Photo, PhotoMeasurement).join(PhotoMeasurement, PhotoMeasurement.photo_id == Photo.id).where(
        Photo.taken_at.is_not(None), Photo.excluded == False  # noqa: E712
    )
    if pose_id:
        stmt = stmt.where(Photo.pose_category_id == pose_id)
    raw = []
    for p, pm in s.exec(stmt.order_by(Photo.taken_at)).all():
        it = (pm.data or {}).get(name)
        if isinstance(it, dict) and it.get("valid"):
            raw.append((p.taken_at.date(), it["ratio"], p.id))
    pts = [(d, v) for d, v, _ in raw]
    smooth = rolling_median(pts, smooth_days) if pts else []
    out: dict[str, Any] = {
        "name": name,
        "label": DEFINITIONS[name][0],
        "description": DEFINITIONS[name][1],
        "pose_id": pose_id,
        "points": [{"photo_id": pid, "date": d.isoformat(), "ratio": v} for d, v, pid in raw],
        "smooth": [{"date": d.isoformat(), "ratio": round(v, 4)} for d, v in smooth],
        "calibration": None,
        "tape_metric_names": list(DEFINITIONS[name][2]),
    }
    m = tape_metric(s, name)
    if m is not None and smooth:
        tape = [(e.date, e.value) for e in s.exec(select(MetricEntry).where(MetricEntry.metric_id == m.id).order_by(MetricEntry.date)).all()]
        # one pair per tape entry (the photo-trend value closest to it), so many photos near one tape date don't dominate
        pairs = _dedupe_pairs(smooth, tape, window_days)
        k = fit_calibration(pairs)
        if k is not None:
            resid = [abs(k * r_ - t) / t for r_, t in pairs if t]
            out["calibration"] = {
                "metric_id": m.id,
                "metric": m.name,
                "unit": m.unit,
                "k": round(k, 4),
                "pairs": len(pairs),
                "mean_abs_error_pct": round(100 * float(np.mean(resid)), 1) if resid else None,
            }
            for row in out["points"]:
                row["value"] = round(k * row["ratio"], 2)
            for row in out["smooth"]:
                row["value"] = round(k * row["ratio"], 2)
    return out


def _dedupe_pairs(smooth: list[tuple[date, float]], tape: list[tuple[date, float]], window_days: int) -> list[tuple[float, float]]:
    out = []
    for td, tv in tape:
        best = None
        for d, v in smooth:
            off = abs((d - td).days)
            if off <= window_days and (best is None or off < best[0]):
                best = (off, v)
        if best is not None:
            out.append((best[1], tv))
    return out


def summary(s: Session) -> list[dict[str, Any]]:
    """Per measurement: valid counts per pose category, and the best category to chart by default."""
    from ..models import PoseCategory

    cat_rows = s.exec(select(PoseCategory)).all()
    cats = {c.id: c.name for c in cat_rows}
    keys = {c.id: c.rule_key for c in cat_rows}
    counts: dict[str, dict[int, int]] = {n: {} for n in DEFINITIONS}
    for p, pm in s.exec(select(Photo, PhotoMeasurement).join(PhotoMeasurement, PhotoMeasurement.photo_id == Photo.id)).all():
        for n in DEFINITIONS:
            it = (pm.data or {}).get(n)
            if isinstance(it, dict) and it.get("valid") and p.pose_category_id:
                counts[n][p.pose_category_id] = counts[n].get(p.pose_category_id, 0) + 1
    out = []
    for n, (label, desc, tape) in DEFINITIONS.items():
        per = sorted(({"pose_id": k, "pose": cats.get(k, "?"), "count": v} for k, v in counts[n].items()), key=lambda x: -x["count"])
        pref = PREFERRED_POSES.get(n, ())

        def rank(row: dict, pref: tuple[str, ...] = pref) -> tuple:
            key = keys.get(row["pose_id"])
            usable = row["count"] >= 5
            return (0 if usable and key in pref else 1, pref.index(key) if key in pref else 99, -row["count"])

        per.sort(key=rank)
        out.append({"name": n, "label": label, "description": desc, "tape_metric_names": list(tape), "by_pose": per,
                    "default_pose_id": per[0]["pose_id"] if per else None})
    return out


def frame_values(s: Session, photo_ids: list[int]) -> dict[int, dict[str, float]]:
    if not photo_ids:
        return {}
    out = {}
    for pm in s.exec(select(PhotoMeasurement).where(PhotoMeasurement.photo_id.in_(photo_ids))).all():
        out[pm.photo_id] = {n: v["ratio"] for n, v in (pm.data or {}).items() if isinstance(v, dict) and v.get("valid")}
    return out


def overlay(photo: Photo, pm: PhotoMeasurement | None, long_side: int = 1280) -> Image.Image:
    """Preview with the body outline tinted and each measurement line drawn (green valid, red rejected)."""
    from PIL import ImageDraw

    img, _ = images.open_image(config.abs_path(photo.original_path))
    small = images.resize_long_side(img, long_side).convert("RGB")
    img.close()
    f = small.width / max(1, photo.width)
    arr = np.asarray(small).copy()
    mask = load_mask(photo.id)
    if mask is not None:
        mk = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize(small.size)) > 127
        arr[mk] = (arr[mk] * 0.65 + np.array([40, 110, 255]) * 0.35).astype(np.uint8)
    if photo.flip_horizontal:
        arr = arr[:, ::-1].copy()
    out = Image.fromarray(arr)
    d = ImageDraw.Draw(out)

    def X(x: float) -> float:
        return small.width - x * f if photo.flip_horizontal else x * f

    w = max(2, round(small.width / 300))
    for name, it in ((pm.data or {}) if pm else {}).items():
        if not isinstance(it, dict) or "line" not in it:
            continue
        (x1, y1), (x2, y2) = it["line"]
        col = (52, 211, 153) if it.get("valid") else (248, 113, 113)
        d.line([(X(x1), y1 * f), (X(x2), y2 * f)], fill=col, width=w)
        for x, y in ((x1, y1), (x2, y2)):
            d.ellipse([X(x) - 2 * w, y * f - 2 * w, X(x) + 2 * w, y * f + 2 * w], fill=col)
        tx = max(X(x1), X(x2)) + 3 * w
        d.text((min(tx, small.width - 60), (y1 + y2) / 2 * f - 6 * w), DEFINITIONS.get(name, (name,))[0], fill=col)
    return out
