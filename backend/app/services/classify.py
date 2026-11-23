"""Pose classification.

A photo goes to the pose whose examples its joints and angles are closest to (PoseMatcher
over the examples in services/examples.py: library photos you set by hand plus uploads).
Until there are examples, hand-written rules for the built-in categories are used instead.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from sqlmodel import Session, select

from ..jobs import JobContext, job
from ..models import Alignment, Photo, PoseCategory, PoseFeatures, PoseLabel, now
from ..settings import AppSettings, load_settings
from . import features as F
from . import landmarks as L
from .examples import Example

log = logging.getLogger(__name__)


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def lt(x: float, t: float, w: float) -> float:
    """Smooth 'x < t' with softness w."""
    return _sig((t - x) / w)


def gt(x: float, t: float, w: float) -> float:
    return _sig((x - t) / w)


def gmean(*xs: float) -> float:
    xs = tuple(max(1e-6, x) for x in xs)
    return float(math.exp(sum(math.log(x) for x in xs) / len(xs)))


def rule_scores(vec: list[float]) -> dict[str, float]:
    d = F.as_dict(vec)
    face = d["face_visibility"]
    sw = d["shoulder_width"]
    side = F.facing_side(vec)  # >0 left side toward camera
    arms_up = gmean(
        lt(d["elbow_angle_l"], 110, 12),
        lt(d["elbow_angle_r"], 110, 12),
        gt(d["wrist_height_l"], -0.05, 0.1),
        gt(d["wrist_height_r"], -0.05, 0.1),
    )
    wide = gt(sw, 0.45, 0.07)
    # both hands away from the body: separates a real double biceps from a one-arm flex holding a phone
    hands_out = gmean(gt(d["wrist_lateral_l"], 0.45, 0.06), gt(d["wrist_lateral_r"], 0.45, 0.06))
    scores = {
        "front_double_bicep": gmean(arms_up, hands_out, gt(face, 0.55, 0.1), wide),
        "back_double_bicep": gmean(arms_up, hands_out, lt(face, 0.45, 0.1), wide),
        # hands on the waist, elbows flared far out to the sides
        "front_lat_spread": gmean(
            gt(d["elbow_angle_l"], 50, 8),
            lt(d["elbow_angle_l"], 115, 8),
            gt(d["elbow_angle_r"], 50, 8),
            lt(d["elbow_angle_r"], 115, 8),
            gt(d["wrist_height_l"], -1.15, 0.1),
            lt(d["wrist_height_l"], -0.35, 0.1),
            gt(d["wrist_height_r"], -1.15, 0.1),
            lt(d["wrist_height_r"], -0.35, 0.1),
            gt(d["elbow_lateral_l"] - d["wrist_lateral_l"], 0.2, 0.06),
            gt(d["elbow_lateral_r"] - d["wrist_lateral_r"], 0.2, 0.06),
            gt(face, 0.55, 0.1),
            wide,
        ),
        "front_relaxed": gmean(
            gt(d["elbow_angle_l"], 140, 10),
            gt(d["elbow_angle_r"], 140, 10),
            lt(d["wrist_height_l"], -0.7, 0.12),
            lt(d["wrist_height_r"], -0.7, 0.12),
            gt(face, 0.55, 0.1),
            wide,
        ),
    }
    narrow = lt(sw, 0.35, 0.06)

    def side_score(near: str, sign: float) -> float:
        h = d[f"wrist_height_{near}"]
        return gmean(
            narrow,
            gt(d[f"elbow_angle_{near}"], 150, 10),
            gt(h, -1.45, 0.12),
            lt(h, -0.45, 0.12),
            _sig(sign * side * 6),
        )

    scores["side_left"] = side_score("l", +1.0)
    scores["side_right"] = side_score("r", -1.0)

    # Mirror selfies: one hand holds the phone (elbow sharply bent, wrist near the
    # chest/face, close to the body). The other arm decides the variant.
    def phone(sd: str) -> float:
        h = d[f"wrist_height_{sd}"]
        return gmean(lt(d[f"elbow_angle_{sd}"], 55, 8), gt(h, -0.6, 0.1), lt(h, 0.1, 0.08), lt(d[f"wrist_lateral_{sd}"], 0.55, 0.06))

    pl, pr = phone("l"), phone("r")
    ph, o = (pl, "r") if pl >= pr else (pr, "l")
    eo, ho = d[f"elbow_angle_{o}"], d[f"wrist_height_{o}"]
    front_on = gt(sw, 0.6, 0.03)
    turned = gt(abs(d["facing_z"]) + abs(d["facing_nose"]), 0.4, 0.08)
    # the phone hand is required, not just one vote among several: min() gates on it
    scores["selfie_flex"] = min(ph, gmean(lt(eo, 100, 10), gt(ho, 0.1, 0.08), front_on, gt(face, 0.55, 0.1)))
    scores["selfie_front"] = min(ph, gmean(gt(eo, 125, 10), lt(ho, -0.75, 0.1), front_on))
    side = min(ph, gmean(lt(sw, 0.6, 0.03), turned))
    side_down = min(side, gmean(gt(eo, 135, 10), lt(ho, -0.95, 0.1)))
    side_90 = min(side, gmean(gt(eo, 55, 8), lt(eo, 125, 8), gt(ho, -0.95, 0.1)))
    flex, front = scores.pop("selfie_flex"), scores.pop("selfie_front")

    # Split by body side. Mirror selfies show a mirror image, and the pose model labels
    # left/right as if the photo were not mirrored, so the subject's REAL side is the
    # opposite of the model's label. (Flipping a photo in the app swaps its group.)
    real_left_phone = 1.0 if ph == pr else 0.0  # phone in the model's "right" hand = real left hand
    scores["selfie_front_left"] = front * real_left_phone
    scores["selfie_front_right"] = front * (1 - real_left_phone)
    real_left_flex = 1.0 if o == "r" else 0.0  # flexing the model's "right" arm = real left arm
    scores["selfie_flex_left"] = flex * real_left_flex
    scores["selfie_flex_right"] = flex * (1 - real_left_flex)
    f = F.facing_side(vec)  # > 0: the model's "left" side faces the camera = real right side
    real_left_side = _sig(-f * 8)
    scores["selfie_side_down_left"] = side_down * real_left_side
    scores["selfie_side_down_right"] = side_down * (1 - real_left_side)
    scores["selfie_side_90_left"] = side_90 * real_left_side
    scores["selfie_side_90_right"] = side_90 * (1 - real_left_side)
    # stage poses are done without a phone in hand
    for k in ("front_double_bicep", "back_double_bicep", "front_lat_spread", "front_relaxed", "side_left", "side_right"):
        scores[k] = min(scores[k], 1.0 - ph)
    return scores


@dataclass
class Prediction:
    category_id: int | None
    confidence: float
    source: str  # rule|knn|none


def predict_rules(vec: list[float] | None, cats_by_key: dict[str, int]) -> Prediction:
    other = cats_by_key.get("other")
    if vec is None:
        return Prediction(other, 0.0, "rule")
    sc = {k: v for k, v in rule_scores(vec).items() if k in cats_by_key}
    if not sc:
        return Prediction(other, 0.0, "rule")
    ranked = sorted(sc.items(), key=lambda kv: kv[1], reverse=True)
    top_key, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top < 0.5:
        return Prediction(other, round(1.0 - top, 4), "rule")
    conf = top * (0.5 + 0.5 * min(1.0, (top - second) / 0.3))
    return Prediction(cats_by_key[top_key], round(conf, 4), "rule")


# How far apart two values must be to count as "a different pose", per feature. Angles in
# degrees, lengths in torso lengths. Distances between poses are measured in these units,
# so joint angles and arm positions dominate and body proportions barely matter.
_SCALE = {
    "elbow_angle": 30.0,
    "shoulder_abduction": 30.0,
    "wrist_height": 0.3,
    "wrist_lateral": 0.3,
    "elbow_lateral": 0.3,
    "shoulder_width": 0.15,  # narrow shoulders = turned to the side
    "hip_width": 0.5,
    "shoulder_hip_ratio": 0.6,
    "facing_nose": 0.4,
    "facing_ears": 0.6,
    "facing_z": 0.4,
    "face_visibility": 0.3,
    "legs_visibility": 0.6,
}
WEIGHTS = np.array([1.0 / _SCALE[n[:-2] if n.endswith(("_l", "_r")) else n] for n in F.FEATURE_NAMES])
FAR = 4.0  # a photo further than this from every pose's examples goes to Other


class PoseMatcher:
    """Nearest-example matcher: a photo goes to the pose whose examples its joints and angles
    are closest to. Each pose's distance is the mean of its k closest examples (k small, so
    one odd example cannot hide a good match and one good example is enough)."""

    def __init__(self, examples: list[Example], k: int = 2, other: int | None = None) -> None:
        self.X = np.array([e.vector for e in examples], dtype=np.float64) * WEIGHTS
        self.y = np.array([e.category_id for e in examples])
        self.k = max(1, k)
        self.other = other
        self.categories = sorted(set(self.y.tolist()))

    def distances(self, vec: list[float]) -> dict[int, float]:
        q = np.asarray(vec, dtype=np.float64) * WEIGHTS
        d = np.linalg.norm(self.X - q, axis=1)
        return {c: float(np.sort(d[self.y == c])[: self.k].mean()) for c in self.categories}

    def predict(self, vec: list[float]) -> tuple[int, float]:
        """(category id, confidence). Confidence is d2 / (d1 + d2): 0.5 when the two nearest
        poses are equally close, 1.0 when the match is unambiguous."""
        ranked = sorted(self.distances(vec).items(), key=lambda kv: kv[1])
        best, d1 = ranked[0]
        if self.other is not None and best != self.other and d1 > FAR:
            return self.other, round(min(1.0, d1 / FAR - 1.0) * 0.5 + 0.5, 4)
        d2 = ranked[1][1] if len(ranked) > 1 else 2.0 * d1 + 1e-6
        conf = d2 / (d1 + d2) if d1 + d2 > 0 else 1.0
        return int(best), round(float(conf), 4)


def matcher_active(examples: list[Example], settings: AppSettings) -> bool:
    cats = {e.category_id for e in examples}
    return len(examples) >= settings.knn_min_labels and len(cats) >= settings.knn_min_categories


def build_matcher(s: Session, settings: AppSettings, **exclude) -> PoseMatcher | None:
    from .examples import training_set

    data = training_set(s, **exclude)
    if not matcher_active(data, settings):
        return None
    other = s.exec(select(PoseCategory.id).where(PoseCategory.rule_key == "other")).first()
    return PoseMatcher(data, settings.knn_k, other)


def example_report(s: Session, settings: AppSettings) -> list[dict]:
    """Every example (hand-set library photo or upload) with the pose the *other* examples say
    it looks like (leave-one-out). An example that looks like another pose is either in the
    wrong pose or a pose two groups share, and pulls look-alike photos the wrong way either way."""
    from .examples import sibling_map, training_set

    cats = s.exec(select(PoseCategory)).all()
    cats_by_key = {c.rule_key: c.id for c in cats if c.rule_key}
    key_of = {c.id: c.rule_key for c in cats}
    other = cats_by_key.get("other")
    base = training_set(s, mirror=False)  # read once; the leave-one-out below works in memory
    sib = sibling_map(s)
    out = []
    for e in base:
        rest = [x for x in base if not (x.source == e.source and x.ref_id == e.ref_id)]
        rest += [Example(sib.get(x.category_id, x.category_id), F.mirror(x.vector), "mirror", x.ref_id) for x in rest]
        looks_like, agree = None, None
        if matcher_active(rest, settings):
            pred, _ = PoseMatcher(rest, settings.knn_k, other).predict(e.vector)
            pred = refine_arm(key_of.get(pred), e.vector, cats_by_key) or pred
            agree = pred == e.category_id
            if not agree:
                looks_like = pred
        arm = refine_arm(key_of.get(e.category_id), e.vector, cats_by_key)
        if arm is not None and arm != e.category_id:  # e.g. an "Arm 90" example with a straight arm
            looks_like, agree = arm, False
        out.append({"kind": e.source, "id": e.ref_id, "pose_category_id": e.category_id, "looks_like": looks_like, "agrees": agree})
    # hand-set photos without a usable pose (no person found) teach nothing but are still listed
    listed = {r["id"] for r in out if r["kind"] == "photo"}
    for lab in s.exec(select(PoseLabel)).all():
        if lab.photo_id not in listed:
            out.append({"kind": "photo", "id": lab.photo_id, "pose_category_id": lab.pose_category_id, "looks_like": None, "agrees": None, "no_pose": True})
    return out


def ensure_features(s: Session, photo: Photo) -> list[float] | None:
    feats = s.get(PoseFeatures, photo.id)
    if feats is not None and feats.version == F.FEATURE_VERSION and feats.vector:
        return feats.vector
    if photo.landmarks_status != "ok":
        return None
    data = L.load_for_photo(photo)
    vec = F.compute(data) if data else None
    if vec is None:
        return None
    if feats is None:
        feats = PoseFeatures(photo_id=photo.id, vector=vec, version=F.FEATURE_VERSION)
    else:
        feats.vector, feats.version, feats.computed_at = vec, F.FEATURE_VERSION, now()
    s.add(feats)
    lab = s.exec(select(PoseLabel).where(PoseLabel.photo_id == photo.id)).first()
    if lab is not None:
        lab.vector = vec
        s.add(lab)
    return vec


# Categories that exist as a left/right pair: a manual assignment fixes the pose, but the
# side still follows the photo (so a manual "side 90" photo lands in the side it shows).
SIDE_PAIRS = [
    ("selfie_front_left", "selfie_front_right"),
    ("selfie_flex_left", "selfie_flex_right"),
    ("selfie_side_down_left", "selfie_side_down_right"),
    ("selfie_side_90_left", "selfie_side_90_right"),
    ("side_left", "side_right"),
]


def side_sibling(rule_key: str | None, vec: list[float] | None, cats_by_key: dict[str, int]) -> int | None:
    """For a photo in one half of a left/right pair, the category id of the half its pose shows."""
    if not rule_key or vec is None:
        return None
    for a, b in SIDE_PAIRS:
        if rule_key in (a, b):
            sc = rule_scores(vec)
            if max(sc.get(a, 0), sc.get(b, 0)) < 0.05:
                return None  # rules can't tell the side: leave it
            best = a if sc.get(a, 0) >= sc.get(b, 0) else b
            return cats_by_key.get(best)
    return None


ARM_FAMILIES = [("selfie_side_down_left", "selfie_side_90_left"), ("selfie_side_down_right", "selfie_side_90_right")]
ARM_BENT_DEG = 132  # shown arm's elbow: below = bent to ~90° (Arm 90), above = hanging by the side (Arm Down)


def shown_elbow(vec: list[float]) -> float:
    """Elbow angle of the arm being shown in a mirror selfie (the arm not holding the phone)."""
    d = F.as_dict(vec)

    def phone(sd: str) -> float:
        h = d[f"wrist_height_{sd}"]
        return gmean(lt(d[f"elbow_angle_{sd}"], 55, 8), gt(h, -0.6, 0.1), lt(h, 0.1, 0.08), lt(d[f"wrist_lateral_{sd}"], 0.55, 0.06))

    shown = "r" if phone("l") >= phone("r") else "l"
    return d[f"elbow_angle_{shown}"]


def refine_arm(rule_key: str | None, vec: list[float] | None, cats_by_key: dict[str, int]) -> int | None:
    """Side mirror selfies: the matcher finds 'side selfie' well but mixes up arm down and arm
    bent, which is one clear number (the shown elbow), so that decides between the two."""
    if not rule_key or vec is None:
        return None
    for down, bent in ARM_FAMILIES:
        if rule_key in (down, bent):
            return cats_by_key.get(bent if shown_elbow(vec) < ARM_BENT_DEG else down)
    return None


def _queue_abs_check() -> None:
    """Keep the abs, sleeves and face checks up to date with the poses: after classifying, check
    any photos not yet checked (a no-op when all are checked)."""
    try:
        from ..jobs import runner
        from .clip_backend import available

        if available():
            runner().enqueue("detect_abs", {})
            runner().enqueue("detect_sleeves", {})
            runner().enqueue("detect_faces", {})
        from .people import models_available

        if models_available():  # new photos get their face fingerprint for Filter people
            runner().enqueue("scan_people", {})
    except Exception as e:  # never fail classification over this
        log.warning("could not queue the abs check: %s", e)


@job("classify_poses", lane="cv", title="Classify poses")
def job_classify(ctx: JobContext, params: dict) -> dict:
    """By default only photos that still need a decision: never classified (new imports, a
    deleted category, a flip) or borderline (needs review). Confident automatic poses stay as
    they are. Borderline ones get a fresh look each run, with the kNN rebuilt from every pose
    you have set or accepted since, which is what moves them. `force` reclassifies everything
    not set by hand."""
    ids = params.get("ids")
    force = bool(params.get("force"))
    with ctx.session() as s:
        settings = load_settings(s)
        cats = s.exec(select(PoseCategory)).all()
        cats_by_key = {c.rule_key: c.id for c in cats if c.rule_key}
        # set by hand or captured for a chosen pose: never reclassified
        stmt = select(Photo).where(Photo.pose_source.not_in(("manual", "capture")), Photo.landmarks_status.in_(("ok", "none")))
        if ids:
            stmt = stmt.where(Photo.id.in_(ids))
        elif not force:
            stmt = stmt.where(
                (Photo.pose_source == "none")
                | (Photo.pose_category_id.is_(None))
                | (Photo.pose_confidence.is_(None))
                | (Photo.pose_needs_review == True)  # noqa: E712
            )
        new = sum(1 for p in s.exec(stmt).all() if p.pose_source == "none" or p.pose_confidence is None)
        photos = s.exec(stmt.order_by(Photo.id)).all()
        # features first (labels need them for kNN)
        for p in s.exec(select(Photo).where(Photo.landmarks_status == "ok")).all():
            ensure_features(s, p)
        s.commit()
        # manual photos: keep the pose, move to the correct side of a left/right pair
        moved = 0
        key_of = {c.id: c.rule_key for c in cats}
        for p in s.exec(select(Photo).where(Photo.pose_source == "manual", Photo.landmarks_status == "ok")).all():
            target = side_sibling(key_of.get(p.pose_category_id), ensure_features(s, p), cats_by_key)
            if target and target != p.pose_category_id:
                p.pose_category_id = target
                p.updated_at = now()
                s.add(p)
                lab = s.exec(select(PoseLabel).where(PoseLabel.photo_id == p.id)).first()
                if lab is not None:
                    lab.pose_category_id = target
                    s.add(lab)
                a = s.get(Alignment, p.id)
                if a is not None:
                    a.stale = True
                    s.add(a)
                moved += 1
        s.commit()
        model = build_matcher(s, settings)
        total = len(photos)
        s.commit()
        ctx.progress(0, total, f"Classifying with {'your examples' if model else 'built-in rules'}")
        changed = 0
        review = 0
        for i, p in enumerate(photos, 1):
            ctx.check_cancelled()
            vec = ensure_features(s, p) if p.landmarks_status == "ok" else None
            if model is not None and vec is not None:
                cid, conf = model.predict(vec)
                cid = refine_arm(key_of.get(cid), vec, cats_by_key) or cid
                pred = Prediction(cid, conf, "knn")  # "knn" = matched to your examples
            else:
                pred = predict_rules(vec, cats_by_key)
            if pred.category_id != p.pose_category_id:
                changed += 1
                cat_old = s.get(PoseCategory, p.pose_category_id) if p.pose_category_id else None
                if cat_old is not None and cat_old.reference_photo_id == p.id:
                    cat_old.reference_photo_id = None
                    s.add(cat_old)
                a = s.get(Alignment, p.id)
                if a is not None:
                    a.stale = True
                    s.add(a)
            p.pose_category_id = pred.category_id
            p.pose_confidence = pred.confidence
            p.pose_source = pred.source
            p.pose_needs_review = vec is None or pred.confidence < settings.review_confidence
            review += int(p.pose_needs_review)
            p.updated_at = now()
            s.add(p)
            if i % 25 == 0 or i == total:
                s.commit()  # never report progress while holding the write lock
                ctx.progress(i, total)
        s.commit()
    method = "your examples" if model else "built-in rules"
    _queue_abs_check()
    if not total:
        msg = "Nothing new to classify (use Settings → Reclassify all to redo every photo)"
    elif ids or force:
        msg = f"Classified {total} photos with {method}: {changed} changed, {review} need review"
    else:
        msg = f"Classified {new} new and re-checked {total - new} borderline photos with {method}: {changed} changed, {review} need review"
    return {
        "message": msg
        + (f"; {moved} hand-assigned photos moved to their correct side" if moved else ""),
        "classified": total,
        "changed": changed,
        "needs_review": review,
        "method": method,
    }
