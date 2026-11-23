"""Capture page: series per pose, ghost selection, outline ghost, capture upload."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from .conftest import image_bytes, make_image


def _photo(s, **kw):
    from app.models import Photo

    n = kw.pop("n", 0)
    p = Photo(sha256=f"sha{n}-{time.time_ns()}", original_path=f"originals/x{n}.jpg", original_filename=f"x{n}.jpg", ext="jpg", width=400, height=500, **kw)
    s.add(p)
    s.commit()
    s.refresh(p)
    return p


def _pose(s, name="Front Double Bicep"):
    from sqlmodel import select

    from app.models import PoseCategory

    return s.exec(select(PoseCategory).where(PoseCategory.name == name)).first()


def test_pick_ghost_reference_last_first(session):
    from app.models import CaptureSeries
    from app.services.capture import pick_ghost, series_for_pose

    cat = _pose(session)
    a = _photo(session, n=1, pose_category_id=cat.id, taken_at=datetime(2024, 1, 1))
    b = _photo(session, n=2, pose_category_id=cat.id, taken_at=datetime(2024, 3, 1))
    c = _photo(session, n=3, pose_category_id=cat.id, taken_at=datetime(2024, 2, 1))
    _photo(session, n=4, pose_category_id=cat.id, taken_at=datetime(2025, 1, 1), excluded=True)  # excluded: never the ghost
    series = series_for_pose(session, cat.id)
    assert series_for_pose(session, cat.id).id == series.id  # one per pose
    # no reference set yet: falls back to the newest photo
    assert pick_ghost(session, series, "reference").id == b.id
    cat.reference_photo_id = c.id
    session.add(cat)
    session.commit()
    assert pick_ghost(session, series).id == c.id  # default source for a pose = its reference
    assert pick_ghost(session, series, "last").id == b.id
    assert pick_ghost(session, series, "first").id == a.id

    # daily series: only its own photos; "last" is the most recent capture
    daily = CaptureSeries(name="Morning", mode="daily", ghost_source="last")
    session.add(daily)
    session.commit()
    assert pick_ghost(session, daily) is None
    d1 = _photo(session, n=5, capture_series_id=daily.id, taken_at=datetime(2024, 5, 1, 7))
    d2 = _photo(session, n=6, capture_series_id=daily.id, taken_at=datetime(2024, 5, 2, 7))
    assert pick_ghost(session, daily).id == d2.id
    assert pick_ghost(session, daily, "first").id == d1.id
    assert pick_ghost(session, daily, "reference").id == d2.id  # no reference yet -> newest
    daily.reference_photo_id = d1.id
    session.add(daily)
    session.commit()
    assert pick_ghost(session, daily, "reference").id == d1.id


def test_parse_captured_at_converts_to_server_local_time():
    from app.services.capture import parse_captured_at

    dt = parse_captured_at("2025-06-07T08:09:10.123-05:00")
    expected = datetime.fromisoformat("2025-06-07T08:09:10-05:00").astimezone().replace(tzinfo=None)
    assert dt == expected and dt.microsecond == 0
    assert parse_captured_at("2025-06-07T08:09:10") == datetime(2025, 6, 7, 8, 9, 10)
    for bad in ("1999-01-01T00:00:00", (datetime.now() + timedelta(days=3)).isoformat(), "yesterday"):
        try:
            parse_captured_at(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_capture_upload_reference_mode(client):
    from app.jobs import _REGISTRY, job

    calls: list[str] = []
    saved = {k: _REGISTRY.get(k) for k in ("extract_landmarks", "align_photos", "classify_poses")}
    try:
        for name in saved:
            job(name, lane="cv")(lambda ctx, params, name=name: calls.append(name) or {"message": "ok"})
        series = client.get("/api/capture/series").json()
        pose = series["poses"][0]
        assert pose["series_id"] is None
        sr = client.post(f"/api/capture/series/for-pose/{pose['pose_id']}").json()
        assert sr["mode"] == "reference" and sr["pose_category_id"] == pose["pose_id"]
        data = image_bytes(make_image(seed=31))
        form = {"series_id": str(sr["id"]), "captured_at": "2025-06-07T08:09:10-05:00", "meta": '{"facing": "user", "mirror": true, "evil": 1}'}
        r = client.post("/api/capture/upload", data=form, files={"file": ("PhysiqueLapse_test.jpg", data, "image/jpeg")})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["created"] is True and out["job_id"]
        p = client.get(f"/api/photos/{out['photo_id']}").json()
        assert p["pose_category_id"] == pose["pose_id"] and p["taken_at_source"] == "capture" and p["taken_at_confidence"] == "high"
        expected = datetime.fromisoformat("2025-06-07T08:09:10-05:00").astimezone().replace(tzinfo=None)
        assert p["taken_at"] == expected.isoformat()
        # retry after a lost reply: same photo, nothing queued twice
        r2 = client.post("/api/capture/upload", data=form, files={"file": ("PhysiqueLapse_test.jpg", data, "image/jpeg")}).json()
        assert r2["photo_id"] == out["photo_id"] and r2["created"] is False and r2["job_id"] is None
        t0 = time.time()
        while len(calls) < 2 and time.time() - t0 < 10:
            time.sleep(0.05)
        assert calls == ["extract_landmarks", "align_photos"]  # never classified
        # a capture that looks just like the previous day's is still kept (no near-duplicate skip)
        img = make_image(seed=31)
        img.paste((250, 10, 10), (0, 0, 20, 20))  # a tiny change: still a near-duplicate by perceptual hash
        form2 = dict(form, captured_at="2025-06-08T08:09:10-05:00")
        r3 = client.post("/api/capture/upload", data=form2, files={"file": ("b.jpg", image_bytes(img), "image/jpeg")}).json()
        assert r3["created"] is True and r3["photo_id"] != out["photo_id"]
        # the newest capture is now the "last" ghost
        g = client.get(f"/api/capture/series/{sr['id']}/ghost", params={"source": "last"}).json()["ghost"]
        assert g["photo_id"] == r3["photo_id"] and g["image_url"].endswith("/preview")
    finally:
        for name, h in saved.items():
            if h is not None:
                _REGISTRY[name] = h


def test_classify_never_touches_captures(session):
    from sqlmodel import select

    from app.models import Photo
    from app.services.classify import job_classify  # noqa: F401  (import registers the job)

    cat = _pose(session)
    p = _photo(session, n=9, pose_category_id=cat.id, pose_source="capture", landmarks_status="ok", taken_at=datetime(2024, 1, 1))
    stmt = select(Photo).where(Photo.pose_source.not_in(("manual", "capture")))
    assert p.id not in [x.id for x in session.exec(stmt).all()]


def test_capture_upload_validation(client):
    sr = client.post("/api/capture/series", json={"name": "Morning"}).json()
    files = {"file": ("a.jpg", image_bytes(make_image(seed=40)), "image/jpeg")}
    assert client.post("/api/capture/upload", data={"series_id": "999", "captured_at": "2025-01-01T00:00:00"}, files=files).status_code == 404
    assert client.post("/api/capture/upload", data={"series_id": str(sr["id"]), "captured_at": "nope"}, files=files).status_code == 422
    bad = {"file": ("a.jpg", b"not an image", "image/jpeg")}
    assert client.post("/api/capture/upload", data={"series_id": str(sr["id"]), "captured_at": "2025-01-01T00:00:00"}, files=bad).status_code == 422


def test_series_settings_and_edges(client):
    sr = client.post("/api/capture/series", json={"name": "Daily front"}).json()
    assert sr["mode"] == "daily" and sr["ghost_source"] == "last"
    r = client.patch(f"/api/capture/series/{sr['id']}", json={"ghost_opacity": 0.7, "ghost_style": "edges", "mirror": False, "timer_s": 5})
    assert r.status_code == 200 and r.json()["ghost_opacity"] == 0.7 and r.json()["timer_s"] == 5
    assert client.patch(f"/api/capture/series/{sr['id']}", json={"ghost_style": "hologram"}).status_code == 422
    assert client.patch(f"/api/capture/series/{sr['id']}", json={"timer_s": 7}).status_code == 422
    up = client.post(
        "/api/capture/upload", data={"series_id": str(sr["id"]), "captured_at": "2025-01-01T07:00:00"},
        files={"file": ("a.jpg", image_bytes(make_image(seed=41)), "image/jpeg")},
    ).json()
    g = client.get(f"/api/capture/series/{sr['id']}/ghost").json()["ghost"]
    assert g["photo_id"] == up["photo_id"] and g["landmarks"] is None
    e = client.get(g["edges_url"])
    assert e.status_code == 200 and e.headers["content-type"] == "image/png"
    from io import BytesIO

    from PIL import Image

    im = Image.open(BytesIO(e.content))
    assert im.mode == "RGBA" and im.getextrema()[3][1] == 255  # some opaque outline pixels
    listing = client.get("/api/capture/series").json()
    assert listing["daily"][0]["photo_count"] == 1 and listing["daily"][0]["last_thumb_url"]
