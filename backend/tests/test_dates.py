from datetime import datetime

import pytest

from app.services import dates


@pytest.mark.parametrize(
    "name,expected",
    [
        ("2024-03-15.jpg", datetime(2024, 3, 15)),
        ("2024-03-15 09.30.00.jpg", datetime(2024, 3, 15, 9, 30, 0)),
        ("2024-03-15_09-30-00.heic", datetime(2024, 3, 15, 9, 30, 0)),
        ("2024-03-15T093000.png", datetime(2024, 3, 15, 9, 30, 0)),
        ("20240315_093000.jpg", datetime(2024, 3, 15, 9, 30, 0)),
        ("20240315.jpg", datetime(2024, 3, 15)),
        ("IMG_20240315.jpg", datetime(2024, 3, 15)),
        ("IMG_20240315_093000.jpg", datetime(2024, 3, 15, 9, 30, 0)),
        ("PXL_20240315_093000123.jpg", datetime(2024, 3, 15, 9, 30, 0)),
        ("Screenshot_20240315-093000.png", datetime(2024, 3, 15, 9, 30, 0)),
        ("IMG-20240315-WA0001.jpg", datetime(2024, 3, 15)),
        ("2023-11-02_3f4a9c1e-8b21-4e7d-9f0a-1c2d3e4f5a6b-main.jpg", datetime(2023, 11, 2)),
        ("photos/2022/2022-07-04 18.05.59.jpg", datetime(2022, 7, 4, 18, 5, 59)),
        ("1710495000000.jpg", datetime.fromtimestamp(1710495000)),
        ("1710495000.jpg", datetime.fromtimestamp(1710495000)),
    ],
)
def test_filename_patterns(name, expected):
    assert dates.date_from_filename(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "IMG_1234.jpg",
        "holiday.png",
        "19990315_120000.jpg",  # year out of range
        "20241399.jpg",  # invalid month/day
        "12345678.jpg",
        "a20240315b.jpg",  # digits glued to letters is fine, but...
    ],
)
def test_filename_no_false_positives(name):
    result = dates.date_from_filename(name)
    if name == "a20240315b.jpg":
        assert result == datetime(2024, 3, 15)  # letters around a date are allowed
    else:
        assert result is None


def test_invalid_time_in_filename_falls_back_to_midnight():
    assert dates.date_from_filename("20240315_256099.jpg") == datetime(2024, 3, 15)


def test_exif_parsing():
    assert dates.parse_exif_datetime("2024:03:15 09:30:00") == datetime(2024, 3, 15, 9, 30)
    assert dates.parse_exif_datetime("2024-03-15 09:30:00") == datetime(2024, 3, 15, 9, 30)
    assert dates.parse_exif_datetime("0000:00:00 00:00:00") is None
    assert dates.parse_exif_datetime("    :  :     :  :  ") is None
    assert dates.parse_exif_datetime("") is None
    assert dates.parse_exif_datetime(None) is None
    assert dates.parse_exif_datetime("1990:01:01 00:00:00") is None  # out of range


def test_exif_priority_original_over_digitized():
    exif = {"DateTimeOriginal": "2024:03:15 09:30:00", "DateTimeDigitized": "2024:03:16 10:00:00"}
    assert dates.date_from_exif(exif) == datetime(2024, 3, 15, 9, 30)
    exif = {"DateTimeDigitized": "2024:03:16 10:00:00"}
    assert dates.date_from_exif(exif) == datetime(2024, 3, 16, 10, 0)
    exif = {"DateTime": "2024:03:16 10:00:00"}  # plain DateTime is modification time; ignored
    assert dates.date_from_exif(exif) is None


def test_resolution_priority():
    exif = {"DateTimeOriginal": "2024:03:15 09:30:00"}
    snap = datetime(2024, 1, 1, 12)
    mtime = datetime(2024, 6, 1).timestamp()
    r = dates.resolve_date(exif=exif, snapchat_date=snap, filename="20240201_000000.jpg", mtime=mtime)
    assert (r.taken_at, r.source, r.confidence) == (datetime(2024, 3, 15, 9, 30), "exif", "high")
    r = dates.resolve_date(exif={}, snapchat_date=snap, filename="20240201_000000.jpg", mtime=mtime)
    assert (r.taken_at, r.source, r.confidence) == (snap, "snapchat_metadata", "high")
    r = dates.resolve_date(exif={}, snapchat_date=None, filename="20240201_000000.jpg", mtime=mtime)
    assert (r.taken_at, r.source, r.confidence) == (datetime(2024, 2, 1), "filename", "medium")
    r = dates.resolve_date(exif={}, filename="IMG_0001.jpg", mtime=mtime)
    assert (r.taken_at, r.source, r.confidence) == (datetime(2024, 6, 1), "file_mtime", "low")
    r = dates.resolve_date(exif={}, filename="IMG_0001.jpg", mtime=None)
    assert (r.taken_at, r.source, r.confidence) == (None, "none", "low")


def test_utc_to_local_with_named_zone():
    dt = datetime(2024, 7, 1, 12, 0)  # noon UTC
    assert dates.utc_to_local(dt, "America/Chicago") == datetime(2024, 7, 1, 7, 0)
    assert dates.utc_to_local(dt, "UTC") == datetime(2024, 7, 1, 12, 0)
    # unknown zone falls back to the system zone without raising
    assert dates.utc_to_local(dt, "Not/AZone") is not None
