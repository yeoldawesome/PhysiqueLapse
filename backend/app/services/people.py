"""Filter people: find the photos that are not of the library's owner.

1. Scan: for every photo, find the subject's face (OpenCV YuNet; the face around the nose the
   pose model found, else the largest) and its 128-number fingerprint (OpenCV SFace).
2. The user marks some photos "me" (and optionally "not me").
3. Every other photo with a face gets a score: how close its face is to the closest "me" face.
   High -> "me". Anything lower is "unsure" (shown as "doesn't look like you", least like you
   first, for the user to check): on real selfies a low score is mostly the owner with a cap
   brim over the eyes, looking down at the phone or at a steep angle, so a low score alone never
   makes a photo "not me". "not_me" comes only from the user (marking photos "not me") and from
   faces that match someone the user marked. Photos without a usable face are "no_face".

Measured on the user's 1,122 photos (SFace, 1,055 usable faces): with 10 examples of the owner,
142 faces scored below 0.25, and by eye nearly all of them were the owner; with 200 varied
examples that dropped to 35. Hence: many examples, best match, no automatic "not me".

The labels the user sets are never overwritten. Everything runs locally.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np
from sqlmodel import Session, select

from .. import config
from ..jobs import JobContext, job
from ..models import PersonFace, Photo, now
from . import images
from . import landmarks as L

log = logging.getLogger(__name__)

PEOPLE_VERSION = 1
ANALYSE_LONG_SIDE = 1280
MIN_FACE_PX = 24  # smaller faces give unreliable fingerprints
# cosine similarity of SFace fingerprints (OpenCV's same-person threshold is 0.363)
ME_SIM = 0.40
TOP_K = 1  # best match: varied examples (caps, angles) each cover their own look


def face_crop_path(photo_id: int):
    return config.DERIVED_DIR / "person_face" / f"{photo_id}.jpg"


def models_available() -> bool:
    return config.face_model_path("detector").exists() and config.face_model_path("recognizer").exists()


_models: tuple[Any, Any] | None = None
_lock = threading.Lock()


def _get_models():
    global _models
    import cv2

    with _lock:
        if _models is None:
            if not models_available():
                raise RuntimeError("face models missing. Run: .venv\\Scripts\\python backend\\scripts\\download_models.py")
            det = cv2.FaceDetectorYN.create(str(config.face_model_path("detector")), "", (320, 320), 0.6, 0.3, 5000)
            rec = cv2.FaceRecognizerSF.create(str(config.face_model_path("recognizer")), "")
            _models = (det, rec)
        return _models


def analyse(photo: Photo) -> dict[str, Any]:
    """Find the subject's face in a photo and fingerprint it."""
    import cv2

    det, rec = _get_models()
    img, _ = images.open_image(config.abs_path(photo.original_path))
    try:
        small = images.resize_long_side(img, ANALYSE_LONG_SIDE).convert("RGB")
        scale = img.width / small.width
    finally:
        img.close()
    bgr = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2BGR)
    with _lock:  # OpenCV DNN objects are not thread-safe
        det.setInputSize((bgr.shape[1], bgr.shape[0]))
        _, faces = det.detect(bgr)
    n = 0 if faces is None else len(faces)
    out: dict[str, Any] = {"n_faces": n, "feature": [], "det_score": None, "face_px": None}
    if not n:
        return out
    # the subject: the face around the nose the pose model found (original pixel coordinates)
    pick = None
    raw = L.load_raw(photo.id)
    if raw and raw.get("detected"):
        nose = next((q for q in raw["points"] if q["name"] == "nose"), None)
        if nose is not None:
            nx, ny = nose["x_px"] / scale, nose["y_px"] / scale
            for f in faces:
                x, y, w, h = f[:4]
                if x - 0.1 * w <= nx <= x + 1.1 * w and y - 0.1 * h <= ny <= y + 1.1 * h and (pick is None or w * h > pick[2] * pick[3]):
                    pick = f
    if pick is None:
        pick = max(faces, key=lambda f: f[2] * f[3])
    out["det_score"], out["face_px"] = float(pick[14]), float(pick[2])
    if pick[2] < MIN_FACE_PX:
        return out
    with _lock:
        aligned = rec.alignCrop(bgr, pick)
        feat = rec.feature(aligned).flatten().astype(np.float64)
    out["feature"] = [round(float(x), 6) for x in feat / (np.linalg.norm(feat) + 1e-9)]
    p = face_crop_path(photo.id)
    p.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(p), aligned)
    return out


@job("scan_people", lane="cv", title="Find faces (filter people)")
def job_scan_people(ctx: JobContext, params: dict) -> dict:
    force = bool(params.get("force"))
    with ctx.session() as s:
        done = {r.photo_id for r in s.exec(select(PersonFace).where(PersonFace.version == PEOPLE_VERSION)).all()}
        todo = [p.id for p in s.exec(select(Photo).order_by(Photo.id)).all() if force or p.id not in done]
    total = len(todo)
    if total:
        ctx.progress(0, total, "Loading face models")
        _get_models()
    found = 0
    for i, pid in enumerate(todo, 1):
        ctx.check_cancelled()
        with ctx.session() as s:
            p = s.get(Photo, pid)
            if p is None:  # deleted while the job was running
                continue
            try:
                r = analyse(p)
            except Exception as e:  # unreadable file: record "no face" so it is not retried forever
                log.warning("face scan failed for %s: %s", pid, e)
                r = {"n_faces": 0, "feature": [], "det_score": None, "face_px": None}
            row = s.get(PersonFace, pid) or PersonFace(photo_id=pid)
            row.n_faces, row.feature, row.det_score, row.face_px = r["n_faces"], r["feature"], r["det_score"], r["face_px"]
            row.version, row.computed_at = PEOPLE_VERSION, now()
            s.add(row)
            s.commit()
            found += int(bool(r["feature"]))
        if i % 10 == 0 or i == total:
            ctx.progress(i, total, f"Scanned {i}/{total} · {found} faces")
    with ctx.session() as s:
        summary = recompute(s)
    msg = f"Scanned {total} photos ({found} with a usable face)" if total else "All photos already scanned"
    if summary["me_examples"]:
        msg += f": {summary['not_me']} not you, {summary['unsure']} not sure"
    return {"message": msg, "scanned": total, "faces": found, **summary}


# ----------------------------------------------------------------------------- matching


def _features(s: Session) -> tuple[list[int], np.ndarray]:
    rows = [r for r in s.exec(select(PersonFace)).all() if r.feature]
    if not rows:
        return [], np.zeros((0, 128))
    return [r.photo_id for r in rows], np.array([r.feature for r in rows], dtype=np.float64)


def _top_mean(sims: np.ndarray) -> np.ndarray:
    """Mean of the TOP_K best similarities per row (fewer when there are fewer examples)."""
    k = min(TOP_K, sims.shape[1])
    return np.sort(sims, axis=1)[:, -k:].mean(axis=1)


def recompute(s: Session) -> dict[str, int]:
    """Set person_status / person_score on every photo from the faces and the user's labels."""
    ids, F = _features(s)
    idx = {pid: i for i, pid in enumerate(ids)}
    photos = s.exec(select(Photo)).all()
    scanned = {r.photo_id for r in s.exec(select(PersonFace)).all()}
    me = [idx[p.id] for p in photos if p.person_label == "me" and p.id in idx]
    other = [idx[p.id] for p in photos if p.person_label == "not_me" and p.id in idx]
    me_score = other_score = None
    if me:
        S = F @ F[me].T
        for col, row in enumerate(me):  # a photo is not its own example
            S[row, col] = -1.0
        me_score = _top_mean(S)
    if other:
        S = F @ F[other].T
        for col, row in enumerate(other):
            S[row, col] = -1.0
        other_score = _top_mean(S)
    for p in photos:
        i = idx.get(p.id)
        score = round(float(me_score[i]), 4) if (me_score is not None and i is not None) else None
        if p.person_label in ("me", "not_me"):
            status = p.person_label
        elif p.id not in scanned:
            status = None
        elif i is None:
            status = "no_face"
        elif me_score is None:
            status = "unsure"
        elif other_score is not None and other_score[i] >= ME_SIM and other_score[i] > me_score[i]:
            status = "not_me"  # the face of someone you marked "not me"
        elif me_score[i] >= ME_SIM:
            status = "me"
        else:
            status = "unsure"  # doesn't look like you: for you to check, never deleted automatically
        if p.person_status != status or p.person_score != score:
            p.person_status, p.person_score = status, score
            s.add(p)
    s.commit()
    return summary(s)


def summary(s: Session) -> dict[str, int]:
    photos = s.exec(select(Photo.person_status, Photo.person_label)).all()
    c = {"me": 0, "not_me": 0, "unsure": 0, "no_face": 0, "unscanned": 0}
    examples = 0
    for st, lab in photos:
        c[st or "unscanned"] += 1
        examples += int(lab == "me")
    return {**c, "me_examples": examples, "total": len(photos), "models": int(models_available())}


def suggestions(s: Session, limit: int = 60) -> list[int]:
    """Before any "me" is marked: faces most like the library's most common face (likely the
    owner), best first, for the user to confirm."""
    ids, F = _features(s)
    if not ids:
        return []
    S = F @ F.T
    # the face with the most look-alikes is the owner's (the library is mostly the owner)
    medoid = int(np.argmax((S >= ME_SIM).sum(axis=1)))
    order = np.argsort(-S[medoid])
    return [ids[i] for i in order[:limit]]


def set_label(s: Session, ids: list[int], label: str | None) -> dict[str, int]:
    for pid in ids:
        p = s.get(Photo, pid)
        if p is not None:
            p.person_label = label
            p.updated_at = now()
            s.add(p)
    s.commit()
    return recompute(s)
