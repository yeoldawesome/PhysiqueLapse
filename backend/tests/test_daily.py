"""Daily capture series: streak / filmstrip, own alignment, flipbook sequence, export."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from io import BytesIO

from PIL import Image

from . import pose_fixtures as PF
from .conftest import image_bytes, make_image
from .test_align import transformed


def _wait(client, jid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _photo(s, series_id, when, n):
    from app.models import Photo

    p = Photo(sha256=f"d{n}-{time.time_ns()}", original_path=f"originals/d{n}.jpg", original_filename=f"d{n}.jpg", ext="jpg",
              width=400, height=500, capture_series_id=series_id, taken_at=when)
    s.add(p)
    s.commit()
    return p


def test_streak_and_filmstrip(session):
    from app.models import CaptureSeries
    from app.services.capture import pick_ghost, streak

    sr = CaptureSeries(name="Morning", mode="daily", ghost_source="last")
    session.add(sr)
    session.commit()
    today = date(2025, 3, 10)
    at = lambda d, h=7: datetime.combine(today - timedelta(days=d), datetime.min.time()) + timedelta(hours=h)  # noqa: E731
    assert streak(session, sr, today) == {"taken_today": False, "streak": 0, "days": streak(session, sr, today)["days"]}
    p1 = _photo(session, sr.id, at(1), 1)  # yesterday
    _photo(session, sr.id, at(2), 2)
    _photo(session, sr.id, at(4), 4)  # gap on day 3
    st = streak(session, sr, today)
    assert st["taken_today"] is False and st["streak"] == 2  # yesterday + the day before: today isn't over yet
    t1 = _photo(session, sr.id, at(0, 6), 5)
    t2 = _photo(session, sr.id, at(0, 20), 6)  # two shots today count once
    st = streak(session, sr, today)
    assert st["taken_today"] is True and st["streak"] == 3
    assert len(st["days"]) == 7 and st["days"][-1]["date"] == "2025-03-10"
    assert st["days"][-1]["photo_id"] == t2.id  # newest photo of the day
    assert st["days"][-2]["photo_id"] == p1.id and st["days"][-4]["photo_id"] is None
    assert pick_ghost(session, sr).id == t2.id  # "last" = the latest photo
    assert t1.id != t2.id


def test_daily_series_alignment_sequence_and_export(client):
    from sqlmodel import Session

    from app import config
    from app.db import engine
    from app.models import Photo
    from app.services import landmarks as L

    sr = client.post("/api/capture/series", json={"name": "Daily front"}).json()
    base = PF.front_double_bicep()
    fixtures = [base, transformed(base, 0.85, 3.0, 40, 30), transformed(base, 1.1, -2.0, -30, -20)]
    ids = []
    for i, fx in enumerate(fixtures):
        r = client.post(
            "/api/capture/upload",
            data={"series_id": str(sr["id"]), "captured_at": f"2025-01-0{i + 1}T07:00:00"},
            files={"file": (f"d{i}.jpg", image_bytes(make_image(800, 1000, seed=60 + i)), "image/jpeg")},
        ).json()
        ids.append(r["photo_id"])
        L.save_raw(r["photo_id"], fx)  # stands in for the pose model
    with Session(engine()) as s:
        for pid in ids:
            p = s.get(Photo, pid)
            p.landmarks_status, p.landmarks_quality = "ok", 0.99
            s.add(p)
        s.commit()
    # wait for the chains queued by the uploads (landmark extraction fails: no model in tests)
    time.sleep(0.5)
    j = _wait(client, client.post(f"/api/capture/series/{sr['id']}/align", params={"force": True}).json()["id"])
    assert j["status"] == "done", j
    assert j["result"]["aligned"] == 3
    seq = client.get(f"/api/capture/series/{sr['id']}/sequence").json()
    assert [f["id"] for f in seq["frames"]] == ids and seq["unaligned"] == 0
    assert [f["days_since_start"] for f in seq["frames"]] == [0, 1, 2]
    img = client.get(seq["frames"][1]["aligned_url"])
    assert img.status_code == 200 and Image.open(BytesIO(img.content)).size == (seq["canvas_w"], seq["canvas_h"])
    # its own files, not the pose alignment
    assert (config.DERIVED_DIR / "series" / str(sr["id"]) / f"{ids[1]}.jpg").exists()
    assert not (config.ALIGNED_DIR / f"{ids[1]}.jpg").exists()
    listing = client.get("/api/capture/series").json()["daily"][0]
    assert listing["reference_photo_id"] == ids[0] and listing["photo_count"] == 3 and "streak" in listing

    # flipbook export of the series
    ex = client.post("/api/exports", json={"group": "series", "series_id": sr["id"], "format": "gif", "fps": 4, "width": 216}).json()
    assert _wait(client, ex["job"]["id"], timeout=120)["status"] == "done", ex
    # deleting a photo removes its series frame too
    client.delete(f"/api/photos/{ids[2]}")
    assert not (config.DERIVED_DIR / "series" / str(sr["id"]) / f"{ids[2]}.jpg").exists()
    assert len(client.get(f"/api/capture/series/{sr['id']}/sequence").json()["frames"]) == 2


def test_series_export_needs_a_series(client):
    assert client.post("/api/exports", json={"group": "series", "series_id": 999}).status_code == 404
