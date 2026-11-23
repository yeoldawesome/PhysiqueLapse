"""User settings: typed defaults in code, overrides stored one row per key."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .models import Setting

DEFAULT_POSITIVE_PROMPTS = [
    "a shirtless man flexing his muscles in a mirror",
    "a bodybuilding progress photo",
    "a physique pose showing muscles",
    "a man flexing his biceps",
    "a fitness progress selfie showing abs",
    "a shirtless man posing in a gym",
    "a back double biceps bodybuilding pose",
    "a person showing their physique in a bathroom mirror",
]
DEFAULT_NEGATIVE_PROMPTS = [
    "a photo of food",
    "a landscape",
    "a group of friends",
    "a screenshot of a phone app",
    "a selfie of a face",
    "a pet",
    "a document or text",
    "a car",
    "a city street",
    "a video game",
    "a meme",
    "a party",
    "a fully clothed person",
    "a building",
]


class AppSettings(BaseModel):
    # alignment canvas
    canvas_w: int = 1080
    canvas_h: int = 1350
    rotation_clamp_deg: float = 15.0  # selfies tilt 10-20 deg; body fit over 4+ torso points is robust enough
    bg_removal: bool = False
    # stats
    units: Literal["lb", "kg"] = "lb"
    stats_window_days: int = 7
    measure_smooth_days: int = 21  # trend window for photo measurements
    abs_threshold: float = 0.5  # CLIP probability above which a torso crop counts as "abs visible"
    face_threshold: float = 0.85  # face-not-covered probability needed for the Face group
    sleeves_threshold: float = 0.5  # "bare arms" probability below which a photo counts as "sleeves"
    # time
    timezone: str = "local"  # IANA name or "local"
    # dedupe
    phash_threshold: int = 6
    # pose
    pose_model: Literal["lite", "full", "heavy"] = "heavy"
    landmark_input_long_side: int = 1280
    flip_tta: bool = False
    review_confidence: float = 0.6
    knn_k: int = 2  # examples averaged per pose when matching
    knn_min_labels: int = 2  # examples needed before matching replaces the rules
    knn_min_categories: int = 2
    # physique filter (snapchat import)
    filter_accept: float = 0.70
    filter_reject: float = 0.30
    filter_clip_weight: float = 0.65
    filter_pose_weight: float = 0.35
    filter_pose_gate: float = 0.15
    clip_positive_prompts: list[str] = Field(default_factory=lambda: list(DEFAULT_POSITIVE_PROMPTS))
    clip_negative_prompts: list[str] = Field(default_factory=lambda: list(DEFAULT_NEGATIVE_PROMPTS))
    # viewer
    playback_fps: float = 2.0
    playback_crossfade: bool = True
    # import
    skip_near_duplicates: bool = True
    # after a browser upload: landmarks -> classify -> align the new photos (None = on in server mode)
    auto_process_uploads: bool | None = None


RESERVED_KEYS = {"schema_version"}


def load_settings(s: Session) -> AppSettings:
    rows = s.exec(select(Setting)).all()
    data: dict[str, Any] = {}
    for r in rows:
        if r.key in RESERVED_KEYS:
            continue
        if r.key in AppSettings.model_fields:
            data[r.key] = r.value.get("v")
    try:
        return AppSettings(**data)
    except Exception:
        # a corrupt row must not take the app down; fall back to defaults for bad keys
        good = {}
        for k, v in data.items():
            try:
                AppSettings(**{k: v})
                good[k] = v
            except Exception:
                pass
        return AppSettings(**good)


def save_settings(s: Session, patch: dict[str, Any]) -> AppSettings:
    current = load_settings(s).model_dump()
    current.update({k: v for k, v in patch.items() if k in AppSettings.model_fields})
    validated = AppSettings(**current)  # raises on bad values
    for k, v in validated.model_dump().items():
        if k not in patch:
            continue
        row = s.get(Setting, k)
        if row is None:
            s.add(Setting(key=k, value={"v": v}))
        else:
            row.value = {"v": v}
    s.commit()
    return validated
