"""Decide whether a Snapchat memory is a physique photo.

score = clip_weight * clip + pose_weight * pose
  clip: zero-shot probability mass on the positive prompts
  pose: 0 without a person; otherwise presence x fraction of core points visible
        x size factor (person >= 25% of image height -> 1) x 0.5 if two people
Pose only runs when clip >= gate, which skips obvious non-physique images fast.
"""

from __future__ import annotations

import numpy as np

from ..settings import AppSettings
from .pose_backend import Point

CORE = ("nose", "left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_hip", "right_hip")
VIS = 0.5


def pose_score(poses: list[list[Point]], image_h: int) -> float:
    if not poses:
        return 0.0
    main = max(poses, key=lambda p: max(q.y_px for q in p) - min(q.y_px for q in p))
    by = {q.name: q for q in main}
    core = [by[n] for n in CORE if n in by]
    presence = float(np.mean([q.presence for q in core])) if core else 0.0
    visible = sum(1 for q in core if q.visibility >= VIS) / len(CORE)
    ys = [q.y_px for q in main if q.visibility >= VIS] or [q.y_px for q in main]
    height_frac = (max(ys) - min(ys)) / max(1, image_h)
    size = min(1.0, height_frac / 0.25)
    s = presence * visible * size
    if len(poses) > 1:
        s *= 0.5
    return round(float(s), 4)


def combine(clip: float, pose: float | None, settings: AppSettings) -> float:
    p = pose if pose is not None else 0.0
    w = settings.filter_clip_weight + settings.filter_pose_weight
    if w <= 0:
        return clip
    return round((settings.filter_clip_weight * clip + settings.filter_pose_weight * p) / w, 4)


def decide(score: float, settings: AppSettings) -> str:
    if score >= settings.filter_accept:
        return "auto_accept"
    if score < settings.filter_reject:
        return "auto_reject"
    return "review"
