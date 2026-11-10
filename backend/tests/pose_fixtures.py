"""Hand-built 33-point landmark sets for each starter pose, in an 800x1000 frame.

Convention: MediaPipe 'left' = the subject's left. A subject facing the camera
has their left shoulder on the image RIGHT (larger x).
"""

from __future__ import annotations

import copy

from app.services.pose_backend import LANDMARK_NAMES

W, H = 800, 1000


def _base(face_vis: float = 0.99) -> dict[str, list[float]]:
    # name -> [x, y, z, visibility]
    return {
        "nose": [400, 280, -0.3, face_vis],
        "left_eye_inner": [412, 265, -0.3, face_vis],
        "left_eye": [420, 265, -0.3, face_vis],
        "left_eye_outer": [428, 265, -0.3, face_vis],
        "right_eye_inner": [388, 265, -0.3, face_vis],
        "right_eye": [380, 265, -0.3, face_vis],
        "right_eye_outer": [372, 265, -0.3, face_vis],
        "left_ear": [445, 275, -0.1, 0.9],
        "right_ear": [355, 275, -0.1, 0.9],
        "mouth_left": [415, 305, -0.3, face_vis],
        "mouth_right": [385, 305, -0.3, face_vis],
        "left_shoulder": [500, 400, 0.0, 0.99],
        "right_shoulder": [300, 400, 0.0, 0.99],
        "left_elbow": [540, 560, 0.0, 0.95],
        "right_elbow": [260, 560, 0.0, 0.95],
        "left_wrist": [545, 720, 0.0, 0.9],
        "right_wrist": [255, 720, 0.0, 0.9],
        "left_hip": [470, 700, 0.0, 0.95],
        "right_hip": [330, 700, 0.0, 0.95],
        "left_knee": [465, 850, 0.0, 0.8],
        "right_knee": [335, 850, 0.0, 0.8],
        "left_ankle": [460, 980, 0.0, 0.6],
        "right_ankle": [340, 980, 0.0, 0.6],
    }


def _finish(pts: dict[str, list[float]]) -> dict:
    # hands and feet follow wrists / ankles
    for side in ("left", "right"):
        wx, wy, wz, wv = pts[f"{side}_wrist"]
        for n, dx in (("pinky", 6), ("index", 0), ("thumb", -6)):
            pts.setdefault(f"{side}_{n}", [wx + dx, wy + 15 * (1 if wy > pts[f"{side}_elbow"][1] else -1), wz, wv])
        ax, ay, az, av = pts[f"{side}_ankle"]
        pts.setdefault(f"{side}_heel", [ax, ay + 10, az, av])
        pts.setdefault(f"{side}_foot_index", [ax + 15, ay + 15, az, av])
    points = []
    for n in LANDMARK_NAMES:
        x, y, z, v = pts[n]
        points.append({"name": n, "x_px": float(x), "y_px": float(y), "z": float(z), "visibility": float(v), "presence": float(v)})
    return {"model": "fixture", "image_w": W, "image_h": H, "detected": True, "num_people": 1, "points": points}


def front_relaxed() -> dict:
    return _finish(_base())


def front_double_bicep() -> dict:
    p = _base()
    p["left_elbow"] = [620, 380, 0, 0.95]
    p["right_elbow"] = [180, 380, 0, 0.95]
    p["left_wrist"] = [610, 250, 0, 0.9]
    p["right_wrist"] = [190, 250, 0, 0.9]
    return _finish(p)


def back_double_bicep() -> dict:
    d = front_double_bicep()
    for q in d["points"]:
        if q["name"] in ("nose", "mouth_left", "mouth_right") or "eye" in q["name"]:
            q["visibility"] = q["presence"] = 0.08
    return d


def side_left() -> dict:
    """Subject's LEFT side toward the camera; they face image-left."""
    p = _base()
    p["nose"] = [330, 280, -0.3, 0.95]
    for n in ("left_eye_inner", "left_eye", "left_eye_outer"):
        p[n] = [345, 265, -0.3, 0.9]
    for n in ("right_eye_inner", "right_eye", "right_eye_outer"):
        p[n] = [340, 265, 0.0, 0.3]
    p["left_ear"] = [390, 275, -0.1, 0.9]
    p["right_ear"] = [400, 275, 0.2, 0.2]
    p["left_shoulder"] = [410, 400, -0.2, 0.99]
    p["right_shoulder"] = [390, 400, 0.2, 0.6]
    p["left_elbow"] = [420, 550, -0.2, 0.95]
    p["left_wrist"] = [425, 700, -0.2, 0.9]
    p["right_elbow"] = [370, 540, 0.3, 0.3]
    p["right_wrist"] = [360, 640, 0.3, 0.3]
    p["left_hip"] = [405, 700, -0.1, 0.95]
    p["right_hip"] = [395, 700, 0.1, 0.6]
    return _finish(p)


def mirrored(d: dict) -> dict:
    from app.services.landmarks import apply_flip

    return apply_flip(copy.deepcopy(d), True)


def no_person() -> dict:
    return {"model": "fixture", "image_w": W, "image_h": H, "detected": False, "num_people": 0, "points": []}


def _phone_right(p: dict) -> dict:
    """Right hand holds a phone against the chest (mirror selfie)."""
    p["right_elbow"] = [280, 600, 0, 0.95]
    p["right_wrist"] = [360, 480, 0, 0.9]
    return p


def selfie_front() -> dict:
    return _finish(_phone_right(_base()))


def selfie_flex() -> dict:
    p = _base()
    p["left_elbow"] = [620, 380, 0, 0.95]
    p["left_wrist"] = [610, 250, 0, 0.9]
    return _finish(_phone_right(p))


def front_lat_spread() -> dict:
    p = _base()
    p["left_elbow"] = [610, 540, 0, 0.95]
    p["right_elbow"] = [190, 540, 0, 0.95]
    p["left_wrist"] = [450, 620, 0, 0.9]
    p["right_wrist"] = [350, 620, 0, 0.9]
    return _finish(p)


def _selfie_side(other_elbow, other_wrist) -> dict:
    """Turned body (left side to camera), right hand holds the phone, left arm varies."""
    d = side_left()
    pts = {q["name"]: q for q in d["points"]}
    moves = (("right_elbow", (380, 560)), ("right_wrist", (360, 470)), ("left_elbow", other_elbow), ("left_wrist", other_wrist))
    for n, (x, y) in moves:
        pts[n]["x_px"], pts[n]["y_px"] = float(x), float(y)
        pts[n]["visibility"] = pts[n]["presence"] = 0.9
    return d


def selfie_side_down() -> dict:
    return _selfie_side((420, 550), (425, 700))


def selfie_side_90() -> dict:
    return _selfie_side((420, 550), (330, 560))
