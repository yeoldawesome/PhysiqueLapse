"""First run: the tutorial, then automatic pose grouping.

Flow for a new library:
1. import photos and find body landmarks;
2. preview unlabeled groups based on how well photos align;
3. apply groups and align each group.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sqlalchemy import func
from sqlmodel import Session, select

from ..jobs import JobContext, job
from ..models import Photo, PoseCategory, PoseExample, PoseFeatures, PoseLabel
from ..settings import load_settings
from . import features as F

MAX_CANDIDATES = 60


def pose_setup_done(s: Session) -> bool:
    settings = load_settings(s)
    return settings.grouping_active or settings.pose_setup_done


def _physique_filter(stmt):
    """Photos that show the user's body: a person was found, not excluded, not marked as someone else."""
    return stmt.where(
        Photo.landmarks_status == "ok",
        Photo.excluded == False,  # noqa: E712
        (Photo.person_status.is_(None)) | (Photo.person_status != "not_me"),
    )


def status(s: Session) -> dict[str, Any]:
    st = load_settings(s)
    photos = s.exec(select(func.count(Photo.id))).one()
    pending = s.exec(select(func.count(Photo.id)).where(Photo.landmarks_status == "pending")).one()
    physique = s.exec(_physique_filter(select(func.count(Photo.id)))).one()
    done = pose_setup_done(s)
    if photos == 0:
        stage = "import"
    elif done:
        stage = "done"
    elif pending:
        stage = "prepare"
    else:
        stage = "group"
    return {
        "stage": stage,
        "show_tutorial": not st.tutorial_seen and photos == 0,
        "tutorial_seen": st.tutorial_seen,
        "pose_setup_done": done,
        "skipped": st.pose_setup_skipped,
        "photos": photos,
        "landmarks_pending": pending,
        "physique_photos": physique,
        "features_ready": physique,
    }


def poses(s: Session) -> list[dict[str, Any]]:
    """The poses to ask about, in order, with their mirror partner and the examples picked so far."""
    from .examples import sibling_map

    cats = s.exec(select(PoseCategory).where((PoseCategory.rule_key.is_(None)) | (PoseCategory.rule_key != "other"))
                  .order_by(PoseCategory.sort_order, PoseCategory.id)).all()
    sib = sibling_map(s)
    labels: dict[int, list[int]] = {}
    for lab in s.exec(select(PoseLabel).order_by(PoseLabel.created_at)).all():
        labels.setdefault(lab.pose_category_id, []).append(lab.photo_id)
    uploads = dict(s.exec(select(PoseExample.pose_category_id, func.count(PoseExample.id)).group_by(PoseExample.pose_category_id)).all())
    return [
        {"id": c.id, "name": c.name, "rule_key": c.rule_key, "mirror_id": sib.get(c.id), "examples": labels.get(c.id, []), "uploaded": uploads.get(c.id, 0)}
        for c in cats
    ]


def candidates(s: Session, cat: PoseCategory, limit: int = MAX_CANDIDATES) -> list[dict[str, Any]]:
    """The user's physique photos most likely to show this pose, best first. Photos already
    picked for another pose are left out."""
    from .classify import rule_scores

    labeled = dict(s.exec(select(PoseLabel.photo_id, PoseLabel.pose_category_id)).all())
    rows = s.exec(
        _physique_filter(select(Photo, PoseFeatures).join(PoseFeatures, PoseFeatures.photo_id == Photo.id))
    ).all()
    n = len(F.FEATURE_NAMES)
    mine = [np.asarray(v.vector, float) for p, v in rows if labeled.get(p.id) == cat.id and len(v.vector) == n]
    out = []
    for p, feats in rows:
        if p.id in labeled and labeled[p.id] != cat.id:
            continue
        vec = feats.vector
        if not vec or len(vec) != n:
            continue
        score = rule_scores(vec).get(cat.rule_key, 0.0) if cat.rule_key else 0.0
        if mine:
            # closeness to the examples picked so far (features are on comparable scales)
            d = min(float(np.linalg.norm(np.asarray(vec, float) - m)) for m in mine)
            score = 0.5 * score + 0.5 / (1.0 + d)
        out.append({"photo_id": p.id, "score": round(score, 4), "picked": labeled.get(p.id) == cat.id,
                    "taken_at": p.taken_at.isoformat() if p.taken_at else None})
    out.sort(key=lambda r: (not r["picked"], -r["score"], r["taken_at"] or ""))
    return out[:limit]


@job("pose_setup_prepare", lane="cv", title="Prepare pose setup")
def job_prepare(ctx: JobContext, params: dict) -> dict:
    """Pose features for every photo with landmarks (what the pose step ranks photos by)."""
    from .classify import ensure_features

    with ctx.session() as s:
        ids = list(s.exec(select(Photo.id).where(Photo.landmarks_status == "ok")).all())
        total = len(ids)
        for i, pid in enumerate(ids, 1):
            ctx.check_cancelled()
            p = s.get(Photo, pid)
            if p is not None:
                ensure_features(s, p)
            if i % 50 == 0:
                s.commit()
                ctx.progress(i, total, f"Reading poses: {i}/{total}")
        s.commit()
        n = s.exec(_physique_filter(select(func.count(Photo.id)))).one()
    return {"message": f"{n} photos show your physique, ready for the pose examples", "physique_photos": n}
