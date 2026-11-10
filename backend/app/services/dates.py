"""Resolve when a photo was taken.

Priority: EXIF DateTimeOriginal -> EXIF DateTimeDigitized -> Snapchat metadata ->
filename patterns -> file modification time -> nothing. Each result carries its
source and a confidence so the UI can badge low-confidence dates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

MIN_YEAR, MAX_YEAR = 2000, 2035


@dataclass(frozen=True)
class DateResult:
    taken_at: datetime | None
    source: str  # exif|snapchat_metadata|filename|file_mtime|manual|none
    confidence: str  # high|medium|low

    @staticmethod
    def none() -> DateResult:
        return DateResult(None, "none", "low")


def _valid(y: int, mo: int, d: int, hh: int = 0, mi: int = 0, ss: int = 0) -> datetime | None:
    if not (MIN_YEAR <= y <= MAX_YEAR):
        return None
    try:
        return datetime(y, mo, d, hh, mi, ss)
    except ValueError:
        return None


# ----------------------------------------------------------------------------- EXIF

_EXIF_DT = re.compile(r"^\s*(\d{4})[:\-/.](\d{2})[:\-/.](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?")


def parse_exif_datetime(value: str | None) -> datetime | None:
    """EXIF stores 'YYYY:MM:DD HH:MM:SS'. Some cameras write zeros or garbage."""
    if not value or not isinstance(value, str):
        return None
    m = _EXIF_DT.match(value)
    if not m:
        return None
    y, mo, d, hh, mi, ss = (int(g) if g is not None else 0 for g in m.groups())
    if mo == 0 or d == 0:
        return None
    if hh > 23 or mi > 59 or ss > 60:
        return None
    return _valid(y, mo, d, hh, mi, min(ss, 59))


def date_from_exif(exif: dict | None) -> datetime | None:
    if not exif:
        return None
    for key in ("DateTimeOriginal", "DateTimeDigitized"):
        dt = parse_exif_datetime(exif.get(key))
        if dt:
            return dt
    return None


# ----------------------------------------------------------------------------- filenames

# Order matters: more specific first. Every pattern yields y, mo, d and optional hh, mi, ss.
_FILENAME_PATTERNS: list[re.Pattern[str]] = [
    # 2024-03-15 09.30.00 / 2024-03-15_09-30-00 / 2024-03-15T093000 / 2024-03-15 (Snapchat prefix)
    re.compile(
        r"(?<!\d)(?P<y>20[0-3]\d)[-_.](?P<mo>\d{2})[-_.](?P<d>\d{2})"
        r"(?:[ _T-](?P<hh>\d{2})[.:\-_]?(?P<mi>\d{2})[.:\-_]?(?P<ss>\d{2})?)?(?!\d)"
    ),
    # 20240315_093000 / 20240315-093000 / 20240315 / IMG_20240315 / PXL_20240315_093000123 / Screenshot_20240315-093000
    re.compile(
        r"(?<!\d)(?P<y>20[0-3]\d)(?P<mo>\d{2})(?P<d>\d{2})"
        r"(?:[_\-T ](?P<hh>\d{2})(?P<mi>\d{2})(?P<ss>\d{2})\d{0,3})?(?!\d)"
    ),
]
# unix epoch in milliseconds or seconds (some apps name files this way)
_EPOCH = re.compile(r"(?<!\d)(?P<ms>1[0-9]{12})(?!\d)|(?<!\d)(?P<s>1[0-9]{9})(?!\d)")


def date_from_filename(name: str) -> datetime | None:
    stem = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    for pat in _FILENAME_PATTERNS:
        for m in pat.finditer(stem):
            g = m.groupdict()
            y, mo, d = int(g["y"]), int(g["mo"]), int(g["d"])
            hh = int(g["hh"]) if g.get("hh") else 0
            mi = int(g["mi"]) if g.get("mi") else 0
            ss = int(g["ss"]) if g.get("ss") else 0
            if g.get("hh") and (hh > 23 or mi > 59 or ss > 59):
                hh = mi = ss = 0
            dt = _valid(y, mo, d, hh, mi, ss)
            if dt:
                return dt
    m = _EPOCH.search(stem)
    if m:
        raw = m.group("ms") or m.group("s")
        secs = int(raw) / (1000 if m.group("ms") else 1)
        try:
            dt = datetime.fromtimestamp(secs)
        except (OverflowError, OSError, ValueError):
            return None
        if MIN_YEAR <= dt.year <= MAX_YEAR:
            return dt.replace(microsecond=0)
    return None


# ----------------------------------------------------------------------------- misc


def date_from_mtime(mtime: float | None) -> datetime | None:
    if mtime is None:
        return None
    try:
        dt = datetime.fromtimestamp(mtime).replace(microsecond=0)
    except (OverflowError, OSError, ValueError):
        return None
    return dt if MIN_YEAR <= dt.year <= MAX_YEAR else None


def utc_to_local(dt_utc: datetime, tz_name: str = "local") -> datetime:
    """Convert a UTC datetime (naive or aware) to a naive local datetime."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    if tz_name and tz_name != "local":
        try:
            return dt_utc.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None, microsecond=0)
        except Exception:
            pass
    return dt_utc.astimezone().replace(tzinfo=None, microsecond=0)


def resolve_date(
    *,
    exif: dict | None = None,
    snapchat_date: datetime | None = None,
    snapchat_confidence: str = "high",
    filename: str | None = None,
    mtime: float | None = None,
) -> DateResult:
    dt = date_from_exif(exif)
    if dt:
        return DateResult(dt, "exif", "high")
    if snapchat_date:
        return DateResult(snapchat_date.replace(microsecond=0), "snapchat_metadata", snapchat_confidence)
    if filename:
        dt = date_from_filename(filename)
        if dt:
            return DateResult(dt, "filename", "medium")
    dt = date_from_mtime(mtime)
    if dt:
        return DateResult(dt, "file_mtime", "low")
    return DateResult.none()
