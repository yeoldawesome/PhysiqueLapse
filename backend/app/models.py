"""SQLModel tables. Metadata only; pixels live on disk under DATA_DIR."""

from __future__ import annotations

import datetime as dt
from datetime import datetime

from pydantic import NaiveDatetime
from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def now() -> datetime:
    """Naive local time. The whole app works in the machine's local time."""
    return datetime.now().replace(microsecond=0)


class PoseCategory(SQLModel, table=True):
    __tablename__ = "pose_categories"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    sort_order: int = 0
    reference_photo_id: int | None = Field(default=None, foreign_key="photos.id")
    anchor_mode: str = "auto"  # auto|face|ears
    scale_basis: str = "torso"  # torso|nose_hip|head
    rotation_basis: str = "torso"  # torso|eyes|shoulders|none
    align_method: str | None = "body"  # body (weighted fit over head+shoulders+hips) | head (pin the head exactly)
    normalize_facing: bool = False
    framing_zoom: float = 1.0
    framing_dx: float = 0.0
    framing_dy: float = 0.0
    rule_key: str | None = None  # builtin rule set name, None for user categories
    created_at: NaiveDatetime = Field(default_factory=now)


class Photo(SQLModel, table=True):
    __tablename__ = "photos"

    id: int | None = Field(default=None, primary_key=True)
    sha256: str = Field(unique=True, index=True)
    phash: str | None = Field(default=None, index=True)
    original_path: str  # relative to DATA_DIR
    original_filename: str
    ext: str
    source: str = "upload"  # upload|folder|snapchat
    source_ref: str | None = None
    width: int = 0
    height: int = 0
    exif_orientation: int = 1
    taken_at: NaiveDatetime | None = Field(default=None, index=True)
    taken_at_source: str = "none"  # exif|snapchat_metadata|filename|file_mtime|manual|none
    taken_at_confidence: str = "low"  # high|medium|low
    flip_horizontal: bool = False
    flip_source: str = "none"  # manual|auto|none
    pose_category_id: int | None = Field(default=None, foreign_key="pose_categories.id", index=True)
    pose_confidence: float | None = None
    pose_source: str = "none"  # rule|knn|manual|none
    pose_needs_review: bool = Field(default=False, index=True)
    landmarks_status: str = Field(default="pending", index=True)  # pending|ok|none|failed
    landmarks_quality: float | None = None
    landmarks_path: str | None = None
    excluded: bool = False
    notes: str | None = None
    created_at: NaiveDatetime = Field(default_factory=now)
    updated_at: NaiveDatetime = Field(default_factory=now)


class PoseFeatures(SQLModel, table=True):
    __tablename__ = "pose_features"

    photo_id: int = Field(primary_key=True, foreign_key="photos.id")
    vector: list = Field(default_factory=list, sa_column=Column(JSON))
    version: int = 1
    computed_at: NaiveDatetime = Field(default_factory=now)


class PoseLabel(SQLModel, table=True):
    __tablename__ = "pose_labels"

    id: int | None = Field(default=None, primary_key=True)
    photo_id: int = Field(unique=True, foreign_key="photos.id")
    pose_category_id: int = Field(foreign_key="pose_categories.id")
    vector: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: NaiveDatetime = Field(default_factory=now)


class Alignment(SQLModel, table=True):
    __tablename__ = "alignments"

    photo_id: int = Field(primary_key=True, foreign_key="photos.id")
    reference_photo_id: int | None = None
    pose_category_id: int | None = None
    canvas_w: int = 1080
    canvas_h: int = 1350
    scale: float = 1.0
    rotation_deg: float = 0.0
    tx: float = 0.0
    ty: float = 0.0
    method: str = "auto"  # auto|manual
    scale_basis_used: str = "torso"  # torso|nose_hip|head|fit
    flags: dict = Field(default_factory=dict, sa_column=Column(JSON))
    residual_px: float | None = None
    output_path: str | None = None
    stale: bool = False
    computed_at: NaiveDatetime = Field(default_factory=now)


class Metric(SQLModel, table=True):
    __tablename__ = "metrics"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    unit: str = ""
    is_builtin: bool = False
    sort_order: int = 0
    decimals: int = 1
    kind: str = "custom"  # weight|bodyfat|length|custom (weight converts with the units setting)


class MetricEntry(SQLModel, table=True):
    __tablename__ = "metric_entries"
    __table_args__ = (UniqueConstraint("metric_id", "date", name="uq_metric_date"),)

    id: int | None = Field(default=None, primary_key=True)
    metric_id: int = Field(foreign_key="metrics.id", index=True)
    date: dt.date = Field(index=True)
    value: float
    note: str | None = None


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    type: str = Field(index=True)
    lane: str = "io"
    status: str = Field(default="queued", index=True)  # queued|running|done|failed|cancelled
    done: int = 0
    total: int = 0
    message: str = ""
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: str | None = None
    created_at: NaiveDatetime = Field(default_factory=now)
    started_at: NaiveDatetime | None = None
    finished_at: NaiveDatetime | None = None


class ImportSession(SQLModel, table=True):
    __tablename__ = "import_sessions"

    id: int | None = Field(default=None, primary_key=True)
    kind: str  # snapchat|folder|upload
    root_path: str | None = None
    status: str = "open"  # open|queued|running|done|failed|cancelled
    counts: dict = Field(default_factory=dict, sa_column=Column(JSON))
    options: dict = Field(default_factory=dict, sa_column=Column(JSON))  # recursive, thresholds, ...
    job_id: int | None = None
    created_at: NaiveDatetime = Field(default_factory=now)
    finished_at: NaiveDatetime | None = None


class ImportCandidate(SQLModel, table=True):
    """One row per file seen by an import. Doubles as the ingest ledger: rejected,
    duplicate and deleted hashes stay here so a re-import does not resurrect them."""

    __tablename__ = "import_candidates"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int | None = Field(default=None, foreign_key="import_sessions.id", index=True)
    file_path: str
    file_name: str
    sha256: str = Field(unique=True, index=True)
    phash: str | None = None
    source_date: NaiveDatetime | None = None
    date_source: str = "none"
    media_type: str = "image"  # image|video|overlay|other
    clip_score: float | None = None
    pose_score: float | None = None
    score: float | None = None
    # pending|auto_accept|auto_reject|review|accepted|rejected|duplicate|error|deleted
    decision: str = Field(default="pending", index=True)
    decided_by: str = "auto"  # auto|user
    prev_decision: str | None = None  # for review undo
    photo_id: int | None = Field(default=None, foreign_key="photos.id")
    duplicate_of_photo_id: int | None = None
    error: str | None = None
    created_at: NaiveDatetime = Field(default_factory=now)
    decided_at: NaiveDatetime | None = None


class Export(SQLModel, table=True):
    __tablename__ = "exports"

    id: int | None = Field(default=None, primary_key=True)
    pose_category_id: int | None = None
    format: str = "mp4"
    path: str | None = None
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    frames: int = 0
    size_bytes: int = 0
    status: str = "queued"
    job_id: int | None = None
    created_at: NaiveDatetime = Field(default_factory=now)


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: dict = Field(default_factory=dict, sa_column=Column(JSON))  # {"v": <json value>}
