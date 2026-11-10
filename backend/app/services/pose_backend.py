"""Pose estimation backends.

Everything downstream (features, classification, alignment) works on named
keypoints, so a backend only has to return points named with the MediaPipe
33-point vocabulary below. Backends that know fewer points (e.g. COCO-17 from
RTMPose / YOLO-pose) fill the rest with visibility 0.

MediaPipe is pinned to 0.10.21 because later releases upload usage metrics to
Google with no opt-out (see PLAN.md §0/§1).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from .. import config

log = logging.getLogger(__name__)

LANDMARK_NAMES: list[str] = [
    "nose",
    "left_eye_inner",
    "left_eye",
    "left_eye_outer",
    "right_eye_inner",
    "right_eye",
    "right_eye_outer",
    "left_ear",
    "right_ear",
    "mouth_left",
    "mouth_right",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_pinky",
    "right_pinky",
    "left_index",
    "right_index",
    "left_thumb",
    "right_thumb",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
    "left_heel",
    "right_heel",
    "left_foot_index",
    "right_foot_index",
]
NAME_TO_INDEX = {n: i for i, n in enumerate(LANDMARK_NAMES)}


def mirror_name(name: str) -> str:
    if name.startswith("left_"):
        return "right_" + name[5:]
    if name.startswith("right_"):
        return "left_" + name[6:]
    return name


# index permutation that swaps left/right labels
MIRROR_PERM: list[int] = [NAME_TO_INDEX[mirror_name(n)] for n in LANDMARK_NAMES]


@dataclass
class Point:
    name: str
    x_px: float
    y_px: float
    z: float
    visibility: float
    presence: float

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "x_px": round(self.x_px, 2),
            "y_px": round(self.y_px, 2),
            "z": round(self.z, 4),
            "visibility": round(self.visibility, 4),
            "presence": round(self.presence, 4),
        }


class PoseBackend(Protocol):
    name: str

    def detect(self, rgb: np.ndarray, num_poses: int = 1) -> list[list[Point]]:
        """Return up to num_poses poses, each a list of 33 Points in pixel coords of `rgb`."""
        ...


class ModelMissing(RuntimeError):
    pass


class MediaPipeBackend:
    def __init__(self, variant: str = "heavy") -> None:
        self.variant = variant
        self.name = f"mediapipe-pose-{variant}"
        self.path: Path = config.pose_model_path(variant)
        if not self.path.exists():
            raise ModelMissing(
                f"Pose model not found at {self.path}. Run: .venv\\Scripts\\python backend\\scripts\\download_models.py"
            )
        self._landmarkers: dict[int, object] = {}
        self._lock = threading.Lock()
        self._buf = self.path.read_bytes()

    def _get(self, num_poses: int, masks: bool = False):
        lm = self._landmarkers.get((num_poses, masks))
        if lm is None:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision

            opts = vision.PoseLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_buffer=self._buf),
                running_mode=vision.RunningMode.IMAGE,
                num_poses=num_poses,
                min_pose_detection_confidence=0.5,
                min_pose_presence_confidence=0.5,
                min_tracking_confidence=0.5,
                output_segmentation_masks=masks,
            )
            lm = vision.PoseLandmarker.create_from_options(opts)
            self._landmarkers[(num_poses, masks)] = lm
        return lm

    def detect(self, rgb: np.ndarray, num_poses: int = 1) -> list[list[Point]]:
        return self.detect_with_masks(rgb, num_poses, masks=False)[0]

    def detect_with_masks(self, rgb: np.ndarray, num_poses: int = 1, masks: bool = True) -> tuple[list[list[Point]], list[np.ndarray | None]]:
        """Poses plus, per pose, a body-outline probability mask (HxW float, same size as rgb)."""
        import mediapipe as mp

        h, w = rgb.shape[:2]
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb, dtype=np.uint8))
        with self._lock:
            res = self._get(num_poses, masks).detect(img)
            seg = [np.array(m.numpy_view(), dtype=np.float32).squeeze() for m in (res.segmentation_masks or [])] if masks else []
        out: list[list[Point]] = []
        for pose in res.pose_landmarks or []:
            pts = []
            for i, p in enumerate(pose):
                pts.append(
                    Point(
                        name=LANDMARK_NAMES[i],
                        x_px=float(p.x) * w,
                        y_px=float(p.y) * h,
                        z=float(p.z),
                        visibility=float(p.visibility if p.visibility is not None else 0.0),
                        presence=float(p.presence if p.presence is not None else 0.0),
                    )
                )
            out.append(pts)
        if masks:
            return out, [seg[i] if i < len(seg) else None for i in range(len(out))]
        return out, [None] * len(out)

    def close(self) -> None:
        for lm in self._landmarkers.values():
            try:
                lm.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self._landmarkers.clear()


_backend: PoseBackend | None = None
_backend_key: str | None = None
_backend_lock = threading.Lock()


def get_backend(variant: str = "heavy") -> PoseBackend:
    """Lazily created singleton; switching the model variant in Settings recreates it."""
    global _backend, _backend_key
    with _backend_lock:
        if _backend is None or _backend_key != variant:
            if _backend is not None and hasattr(_backend, "close"):
                _backend.close()  # type: ignore[union-attr]
            _backend = MediaPipeBackend(variant)
            _backend_key = variant
        return _backend


def set_backend_for_tests(backend: PoseBackend | None, key: str = "test") -> None:
    global _backend, _backend_key
    _backend, _backend_key = backend, key
