"""Engine, sessions, schema creation, tiny migrations and seed data."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine, select

from . import config
from .models import Metric, PoseCategory, Setting

log = logging.getLogger(__name__)

SCHEMA_VERSION = 7

_engine = None


def engine():
    global _engine
    if _engine is None:
        config.ensure_dirs()
        _engine = create_engine(
            f"sqlite:///{config.DB_PATH.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
            pool_pre_ping=True,
        )

        @event.listens_for(_engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    return _engine


def reset_engine_for_tests(path) -> None:
    """Point the app at a fresh database file (tests only)."""
    global _engine
    config.DATA_DIR = path
    config.DB_PATH = path / "physiquelapse.db"
    config.ORIGINALS_DIR = path / "originals"
    config.DERIVED_DIR = path / "derived"
    config.THUMBS_DIR = config.DERIVED_DIR / "thumbs"
    config.PREVIEWS_DIR = config.DERIVED_DIR / "previews"
    config.LANDMARKS_DIR = config.DERIVED_DIR / "landmarks"
    config.ALIGNED_DIR = config.DERIVED_DIR / "aligned"
    config.EXPORTS_DIR = config.DERIVED_DIR / "exports"
    config.MODELS_DIR = path / "models"
    config.CLIP_CACHE_DIR = config.MODELS_DIR / "open_clip"
    config.TRASH_DIR = path / "trash"
    config.INCOMING_DIR = path / "incoming"
    if _engine is not None:
        _engine.dispose()
    _engine = None


@contextmanager
def session_scope() -> Iterator[Session]:
    s = Session(engine())
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(engine()) as s:
        yield s


# --------------------------------------------------------------------------- migrations

_MIGRATIONS: dict[int, list[str]] = {
    # version -> SQL statements that bring a database from version-1 to version
    2: ["ALTER TABLE import_candidates ADD COLUMN prev_decision VARCHAR"],
    3: ["ALTER TABLE pose_categories ADD COLUMN align_method VARCHAR DEFAULT 'body'"],
    4: [
        "ALTER TABLE alignments ADD COLUMN joint_residual_px FLOAT",
        "UPDATE pose_categories SET align_method = 'joints' WHERE align_method IS NULL OR align_method = 'body'",
        "UPDATE alignments SET stale = 1",
    ],
    5: [
        "ALTER TABLE photos ADD COLUMN abs_visible BOOLEAN",
        "ALTER TABLE photos ADD COLUMN abs_score FLOAT",
        "ALTER TABLE photos ADD COLUMN abs_source VARCHAR DEFAULT 'none'",
        "ALTER TABLE photos ADD COLUMN abs_version INTEGER",
    ],
    6: [
        "ALTER TABLE photos ADD COLUMN sleeves BOOLEAN",
        "ALTER TABLE photos ADD COLUMN sleeves_score FLOAT",
        "ALTER TABLE photos ADD COLUMN sleeves_source VARCHAR DEFAULT 'none'",
        "ALTER TABLE photos ADD COLUMN sleeves_version INTEGER",
    ],
    7: [
        "ALTER TABLE photos ADD COLUMN face_visible BOOLEAN",
        "ALTER TABLE photos ADD COLUMN face_score FLOAT",
        "ALTER TABLE photos ADD COLUMN face_source VARCHAR DEFAULT 'none'",
        "ALTER TABLE photos ADD COLUMN face_version INTEGER",
    ],
}


def _get_version(s: Session) -> int:
    row = s.get(Setting, "schema_version")
    return int(row.value.get("v", 0)) if row else 0


def _set_version(s: Session, v: int) -> None:
    row = s.get(Setting, "schema_version")
    if row is None:
        row = Setting(key="schema_version", value={"v": v})
        s.add(row)
    else:
        row.value = {"v": v}
    s.commit()


def init_db() -> None:
    config.ensure_dirs()
    SQLModel.metadata.create_all(engine())
    with Session(engine()) as s:
        v = _get_version(s)
        if v == 0:
            _set_version(s, SCHEMA_VERSION)
        else:
            for target in range(v + 1, SCHEMA_VERSION + 1):
                for stmt in _MIGRATIONS.get(target, []):
                    try:
                        s.exec(text(stmt))
                    except Exception as e:  # column already present etc.
                        log.warning("migration %s: %s (%s)", target, stmt, e)
                _set_version(s, target)
        _seed(s)
        s.commit()


BUILTIN_CATEGORIES: list[tuple[str, str, str]] = [
    # name, rule_key, anchor_mode
    ("Front Double Bicep", "front_double_bicep", "auto"),
    ("Back Double Bicep", "back_double_bicep", "ears"),
    ("Front Relaxed", "front_relaxed", "auto"),
    ("Front Lat Spread", "front_lat_spread", "auto"),
    ("Side Arm Down Flex - Left", "side_left", "auto"),
    ("Side Arm Down Flex - Right", "side_right", "auto"),
    ("Mirror Selfie - Front (Phone in Left Hand)", "selfie_front_left", "auto"),
    ("Mirror Selfie - Front (Phone in Right Hand)", "selfie_front_right", "auto"),
    ("Mirror Selfie - Flex (Left Arm)", "selfie_flex_left", "auto"),
    ("Mirror Selfie - Flex (Right Arm)", "selfie_flex_right", "auto"),
    ("Mirror Selfie - Side (Arm Down) - Left Side", "selfie_side_down_left", "auto"),
    ("Mirror Selfie - Side (Arm Down) - Right Side", "selfie_side_down_right", "auto"),
    ("Mirror Selfie - Side (Arm 90) - Left Side", "selfie_side_90_left", "auto"),
    ("Mirror Selfie - Side (Arm 90) - Right Side", "selfie_side_90_right", "auto"),
    ("Other", "other", "auto"),
]


def _seed(s: Session) -> None:
    # renamed / split built-ins: the old single side-selfie category becomes "arm down"
    # (old key, new key, old default name). Split categories keep their photos for now; the next
    # "Classify poses" moves automatically-classified photos to the correct side. The reference
    # is cleared because it may show the other side.
    renames = (
        ("selfie_side", "selfie_side_down", "Mirror Selfie - Side"),
        ("selfie_side_down", "selfie_side_down_right", "Mirror Selfie - Side (Arm Down)"),
        ("selfie_side_90", "selfie_side_90_right", "Mirror Selfie - Side (Arm 90)"),
        ("selfie_flex", "selfie_flex_right", "Mirror Selfie - Flex"),
        ("selfie_front", "selfie_front_right", "Mirror Selfie - Front"),
    )
    for old_key, new_key, old_name in renames:
        c = s.exec(select(PoseCategory).where(PoseCategory.rule_key == old_key)).first()
        if c is not None and not s.exec(select(PoseCategory).where(PoseCategory.rule_key == new_key)).first():
            c.rule_key = new_key
            if c.name == old_name:
                c.name = next((n for n, k, _ in BUILTIN_CATEGORIES if k == new_key), c.name)
            if old_key != "selfie_side":
                c.reference_photo_id = None
            s.add(c)
            s.flush()
    s.flush()
    # Built-in categories are offered once: a fresh library gets them all, an existing one gets
    # only built-ins added in a later version. After that the categories are the user's: deleting,
    # renaming or reordering one is never undone here. The keys already offered are remembered.
    offered_row = s.get(Setting, "builtin_categories_offered")
    existing = s.exec(select(PoseCategory)).all()
    if offered_row is not None:
        offered = set(offered_row.value.get("v", []))
    elif existing:  # library from before this was tracked: everything it lacks was removed by the user
        offered = {k for _n, k, _a in BUILTIN_CATEGORIES}
    else:
        offered = set()
    have = {c.rule_key for c in existing if c.rule_key}
    names = {c.name.lower() for c in existing}
    next_order = max((c.sort_order for c in existing), default=-1) + 1
    for i, (name, key, anchor) in enumerate(BUILTIN_CATEGORIES):
        if key in offered or key in have:
            offered.add(key)
            continue
        offered.add(key)
        if name.lower() in names:  # user already made one with this name: adopt it
            c = s.exec(select(PoseCategory).where(PoseCategory.name == name)).first()
            if c is not None and c.rule_key is None:
                c.rule_key = key
                s.add(c)
            continue
        s.add(PoseCategory(name=name, rule_key=key, sort_order=i if not existing else next_order, anchor_mode=anchor))
        next_order += 1
    if offered_row is None:
        offered_row = Setting(key="builtin_categories_offered", value={"v": sorted(offered)})
    else:
        offered_row.value = {"v": sorted(offered)}
    s.add(offered_row)
    # "Other" must exist: photos of a deleted category move there
    if "other" not in have and not s.exec(select(PoseCategory).where(PoseCategory.rule_key == "other")).first():
        s.flush()
        if not s.exec(select(PoseCategory).where(PoseCategory.rule_key == "other")).first():
            s.add(PoseCategory(name="Other", rule_key="other", sort_order=next_order, anchor_mode="auto"))
    if not s.exec(select(Metric.id).limit(1)).first():
        s.add(Metric(name="Weight", unit="lb", is_builtin=True, sort_order=0, decimals=1, kind="weight"))
    s.commit()
