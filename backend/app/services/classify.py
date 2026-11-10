"""Pose classification: hand-written rules, replaced by kNN over your own labels
once there are enough of them.

Rules only know the built-in categories (rule_key). User-created categories are
learned exclusively through kNN.
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
    scores["selfie_side_down"] = min(side, gmean(gt(eo, 135, 10), lt(ho, -0.95, 0.1)))
    scores["selfie_side_90"] = min(side, gmean(gt(eo, 55, 8), lt(eo, 125, 8), gt(ho, -0.95, 0.1)))
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


class KNN:
    def __init__(self, X: np.ndarray, y: np.ndarray, k: int) -> None:
        self.mu = X.mean(axis=0)
        self.sd = X.std(axis=0)
        self.sd[self.sd < 1e-6] = 1.0
        self.X = (X - self.mu) / self.sd
        self.y = y
        self.k = max(1, min(k, len(y)))

    def predict(self, vec: list[float]) -> tuple[int, float]:
        q = (np.asarray(vec, dtype=np.float64) - self.mu) / self.sd
        d = np.linalg.norm(self.X - q, axis=1)
        idx = np.argsort(d)[: self.k]
        w = 1.0 / (d[idx] + 1e-3)
        votes: dict[int, float] = {}
        for i, wi in zip(idx, w, strict=True):
            votes[int(self.y[i])] = votes.get(int(self.y[i]), 0.0) + float(wi)
        best = max(votes.items(), key=lambda kv: kv[1])
        return best[0], best[1] / sum(votes.values())


def knn_active(labels: list[tuple[int, list[float]]], settings: AppSettings) -> bool:
    cats = {c for c, _ in labels}
    return len(labels) >= settings.knn_min_labels and len(cats) >= settings.knn_min_categories


def build_knn(s: Session, settings: AppSettings) -> KNN | None:
    rows = s.exec(select(PoseLabel)).all()
    data: list[tuple[int, list[float]]] = []
    for lab in rows:
        feats = s.get(PoseFeatures, lab.photo_id)
        vec = feats.vector if feats is not None and feats.vector else lab.vector
        if vec and len(vec) == len(F.FEATURE_NAMES):
            data.append((lab.pose_category_id, vec))
    if not knn_active(data, settings):
        return None
    X = np.array([v for _, v in data], dtype=np.float64)
    y = np.array([c for c, _ in data])
    return KNN(X, y, settings.knn_k)


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


@job("classify_poses", lane="cv", title="Classify poses")
def job_classify(ctx: JobContext, params: dict) -> dict:
    ids = params.get("ids")
    with ctx.session() as s:
        settings = load_settings(s)
        cats = s.exec(select(PoseCategory)).all()
        cats_by_key = {c.rule_key: c.id for c in cats if c.rule_key}
        stmt = select(Photo).where(Photo.pose_source != "manual", Photo.landmarks_status.in_(("ok", "none")))
        if ids:
            stmt = stmt.where(Photo.id.in_(ids))
        photos = s.exec(stmt.order_by(Photo.id)).all()
        # features first (labels need them for kNN)
        for p in s.exec(select(Photo).where(Photo.landmarks_status == "ok")).all():
            ensure_features(s, p)
        s.commit()
        model = build_knn(s, settings)
        total = len(photos)
        s.commit()
        ctx.progress(0, total, f"Classifying with {'kNN' if model else 'rules'}")
        changed = 0
        review = 0
        for i, p in enumerate(photos, 1):
            ctx.check_cancelled()
            vec = ensure_features(s, p) if p.landmarks_status == "ok" else None
            if model is not None and vec is not None:
                cid, conf = model.predict(vec)
                pred = Prediction(cid, round(conf, 4), "knn")
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
    method = "kNN" if model else "rules"
    return {
        "message": f"Classified {total} photos with {method}: {changed} changed, {review} need review",
        "classified": total,
        "changed": changed,
        "needs_review": review,
        "method": method,
    }
