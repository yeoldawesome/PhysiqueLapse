"""Landmarks -> pose feature vector.

All features are invariant to image translation and scale: lengths are divided
by the torso length T = |shoulder_mid - hip_mid|. They are computed on the
landmarks *as displayed* (after the photo's flip), so a flipped photo gets the
features of its mirror image.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

FEATURE_VERSION = 2

FEATURE_NAMES: list[str] = [
    "elbow_angle_l",
    "elbow_angle_r",
    "shoulder_abduction_l",
    "shoulder_abduction_r",
    "wrist_height_l",
    "wrist_height_r",
    "wrist_lateral_l",
    "wrist_lateral_r",
    "elbow_lateral_l",
    "elbow_lateral_r",
    "shoulder_width",
    "hip_width",
    "shoulder_hip_ratio",
    "facing_nose",
    "facing_ears",
    "facing_z",
    "face_visibility",
    "legs_visibility",
]
IDX = {n: i for i, n in enumerate(FEATURE_NAMES)}

# Features that change sign under a horizontal mirror (L/R swap handled separately).
_SIGNED = ("facing_nose", "facing_ears", "facing_z")


def _xy(p: dict) -> np.ndarray:
    return np.array([p["x_px"], p["y_px"]], dtype=np.float64)


def _angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Angle ABC in degrees."""
    v1, v2 = a - b, c - b
    n = np.linalg.norm(v1) * np.linalg.norm(v2)
    if n < 1e-9:
        return 180.0
    return math.degrees(math.acos(float(np.clip(np.dot(v1, v2) / n, -1.0, 1.0))))


def _vec_angle(v1: np.ndarray, v2: np.ndarray) -> float:
    n = np.linalg.norm(v1) * np.linalg.norm(v2)
    if n < 1e-9:
        return 0.0
    return math.degrees(math.acos(float(np.clip(np.dot(v1, v2) / n, -1.0, 1.0))))


def torso_frame(pts: dict[str, dict]) -> tuple[np.ndarray, np.ndarray, float, np.ndarray, np.ndarray]:
    """(shoulder_mid, hip_mid, T, up_unit, right_unit) in image coords (y down)."""
    sm = (_xy(pts["left_shoulder"]) + _xy(pts["right_shoulder"])) / 2
    hm = (_xy(pts["left_hip"]) + _xy(pts["right_hip"])) / 2
    v = sm - hm
    T = float(np.linalg.norm(v))
    if T < 1e-6:
        T = 1e-6
        up = np.array([0.0, -1.0])
    else:
        up = v / T
    perp = np.array([-up[1], up[0]])  # rotate up by +90deg in image coords -> points to image right when upright
    return sm, hm, T, up, perp


def compute(data: dict[str, Any]) -> list[float] | None:
    """Feature vector from a landmark dict (flip already applied), or None if no person."""
    if not data or not data.get("detected") or not data.get("points"):
        return None
    pts = {p["name"]: p for p in data["points"]}
    sm, hm, T, up, perp = torso_frame(pts)
    f = [0.0] * len(FEATURE_NAMES)
    for side, s in (("left", "l"), ("right", "r")):
        sh, el, wr = _xy(pts[f"{side}_shoulder"]), _xy(pts[f"{side}_elbow"]), _xy(pts[f"{side}_wrist"])
        f[IDX[f"elbow_angle_{s}"]] = _angle(sh, el, wr)
        f[IDX[f"shoulder_abduction_{s}"]] = _vec_angle(el - sh, -up)  # 0 = arm hanging along the torso
        f[IDX[f"wrist_height_{s}"]] = float(np.dot(wr - sh, up) / T)  # > 0: wrist above shoulder
        f[IDX[f"wrist_lateral_{s}"]] = float(abs(np.dot(wr - sm, perp)) / T)
        f[IDX[f"elbow_lateral_{s}"]] = float(abs(np.dot(el - sm, perp)) / T)
    sw = float(np.linalg.norm(_xy(pts["left_shoulder"]) - _xy(pts["right_shoulder"])))
    hw = float(np.linalg.norm(_xy(pts["left_hip"]) - _xy(pts["right_hip"])))
    f[IDX["shoulder_width"]] = sw / T
    f[IDX["hip_width"]] = hw / T
    f[IDX["shoulder_hip_ratio"]] = sw / hw if hw > 1e-6 else 3.0
    # facing: positive = the subject's LEFT side is turned toward the camera
    nose = _xy(pts["nose"])
    f[IDX["facing_nose"]] = float(np.clip(np.dot(sm - nose, perp) / T, -1.5, 1.5))
    f[IDX["facing_ears"]] = float(pts["left_ear"]["visibility"] - pts["right_ear"]["visibility"])
    f[IDX["facing_z"]] = float(np.clip(pts["right_shoulder"]["z"] - pts["left_shoulder"]["z"], -1.5, 1.5))
    f[IDX["face_visibility"]] = float(np.mean([pts[n]["visibility"] for n in ("nose", "left_eye", "right_eye")]))
    f[IDX["legs_visibility"]] = float(
        np.mean([pts[n]["visibility"] for n in ("left_knee", "right_knee", "left_ankle", "right_ankle")])
    )
    return [round(x, 5) for x in f]


def as_dict(vec: list[float]) -> dict[str, float]:
    return {n: vec[i] for i, n in enumerate(FEATURE_NAMES)}


def mirror(vec: list[float]) -> list[float]:
    """Features of the horizontally mirrored pose (for tests and symmetric rules)."""
    out = list(vec)
    for base in ("elbow_angle", "shoulder_abduction", "wrist_height", "wrist_lateral", "elbow_lateral"):
        out[IDX[f"{base}_l"]], out[IDX[f"{base}_r"]] = vec[IDX[f"{base}_r"]], vec[IDX[f"{base}_l"]]
    for n in _SIGNED:
        out[IDX[n]] = -vec[IDX[n]]
    return out


def facing_side(vec: list[float]) -> float:
    """> 0: subject's left side toward the camera, < 0: right side. Roughly in [-1, 1]."""
    d = as_dict(vec)
    return float(np.tanh(1.5 * d["facing_nose"] + 1.0 * d["facing_ears"] + 2.0 * d["facing_z"]))
