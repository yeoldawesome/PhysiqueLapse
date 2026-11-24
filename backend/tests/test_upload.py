"""Resumable chunked uploads, HEIC, auto-processing after upload."""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from .conftest import image_bytes, make_image, save_image


def wait_job(client, job_id, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _session(c) -> int:
    return c.post("/api/imports/upload-session").json()["id"]


def _init(c, sid, name, data, lm=1_700_000_000_000):
    return c.post(f"/api/imports/upload-session/{sid}/files/init", json={"rel_path": name, "size": len(data), "last_modified": lm})


def _put(c, sid, fid, offset, chunk):
    return c.put(f"/api/imports/upload-session/{sid}/files/{fid}", params={"offset": offset}, content=chunk,
                 headers={"Content-Type": "application/octet-stream"})


def test_chunked_upload_resume_and_import(client):
    data = image_bytes(make_image(seed=11))
    sid = _session(client)
    r = _init(client, sid, "camera roll/IMG_0001.jpg", data)
    assert r.status_code == 200
    item = r.json()
    assert item["received"] == 0 and item["chunk_size"] > 0
    half = len(data) // 2
    assert _put(client, sid, item["id"], 0, data[:half]).json()["received"] == half
    # connection dropped; the page asks again for the same file and resumes
    again = _init(client, sid, "camera roll/IMG_0001.jpg", data).json()
    assert again["id"] == item["id"] and again["received"] == half
    # a chunk sent twice (ack lost) is refused with the right offset
    r = _put(client, sid, item["id"], 0, data[:half])
    assert r.status_code == 409 and str(half) in r.json()["detail"]
    done = _put(client, sid, item["id"], half, data[half:]).json()
    assert done["status"] == "complete" and done["received"] == len(data)
    st = client.get(f"/api/imports/upload-session/{sid}").json()
    assert st["session"]["counts"]["staged"] == 1
    r = client.post(f"/api/imports/upload-session/{sid}/commit").json()
    j = wait_job(client, r["job"]["id"])
    assert j["status"] == "done", j
    assert len(j["result"]["photo_ids"]) == 1
    photos = client.get("/api/photos").json()
    assert photos["total"] == 1
    assert photos["items"][0]["original_filename"] == "camera roll/IMG_0001.jpg"


def test_upload_limits(client, monkeypatch):
    from app import config

    sid = _session(client)
    assert _init(client, sid, "notes.txt", b"hello").status_code == 415
    assert _init(client, sid, "clip.mov", b"x" * 10).status_code == 415
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    assert _init(client, sid, "huge.jpg", b"x" * (2 << 20)).status_code == 413
    # more bytes than announced
    item = _init(client, sid, "small.jpg", b"x" * 100).json()
    assert _put(client, sid, item["id"], 0, b"x" * 5000).status_code == 413
    assert client.get(f"/api/imports/upload-session/{sid}/files/{item['id']}").json()["received"] == 0


def test_upload_path_traversal_stays_in_staging(client, data_dir):
    from app import config

    data = image_bytes(make_image(seed=12))
    sid = _session(client)
    item = _init(client, sid, "../../../../evil/x.jpg", data).json()
    assert ".." not in item["rel_path"]
    _put(client, sid, item["id"], 0, data)
    staged = [p for p in (config.INCOMING_DIR / str(sid)).rglob("*.jpg")]
    assert len(staged) == 1 and staged[0].resolve().is_relative_to((config.INCOMING_DIR / str(sid)).resolve())
    assert not (data_dir.parent / "evil").exists()


def test_cannot_upload_to_committed_session(client):
    data = image_bytes(make_image(seed=13))
    sid = _session(client)
    item = _init(client, sid, "a.jpg", data).json()
    _put(client, sid, item["id"], 0, data)
    j = client.post(f"/api/imports/upload-session/{sid}/commit").json()["job"]
    wait_job(client, j["id"])
    assert _init(client, sid, "b.jpg", data).status_code == 409


def test_heic_upload_keeps_exif_date_and_serves_jpeg(client, tmp_path):
    pytest.importorskip("pillow_heif")
    when = datetime(2025, 6, 7, 8, 9, 10)
    p = save_image(tmp_path / "IMG_0002.heic", make_image(seed=14), exif_dt=when)
    data = p.read_bytes()
    sid = _session(client)
    item = _init(client, sid, "IMG_0002.HEIC", data).json()
    _put(client, sid, item["id"], 0, data)
    wait_job(client, client.post(f"/api/imports/upload-session/{sid}/commit").json()["job"]["id"])
    ph = client.get("/api/photos").json()["items"][0]
    assert ph["taken_at"].startswith("2025-06-07T08:09:10") and ph["taken_at_source"] == "exif"
    r = client.get(f"/api/photos/{ph['id']}/original")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    from io import BytesIO

    from PIL import Image

    im = Image.open(BytesIO(r.content))
    assert im.format == "JPEG"
    assert im.getexif().get_ifd(0x8769).get(0x9003) == "2025:06:07 08:09:10"


def test_auto_process_chains_landmarks_group_align(client):
    """With no groups yet, auto-processing detects landmarks, groups the library, then aligns it."""
    from app.jobs import _REGISTRY, job

    seen: list[tuple[str, dict]] = []
    saved = {k: _REGISTRY.get(k) for k in ("extract_landmarks", "group_poses", "align_photos")}
    try:
        for name in saved:
            job(name, lane="cv")(lambda ctx, params, name=name: seen.append((name, dict(params))) or {"message": "ok"})
        # an older photo that must not be reprocessed
        old = _session(client)
        d0 = image_bytes(make_image(seed=20))
        # (a library past the first-run pose step; before it, uploads only get their joints found)
        client.put("/api/settings", json={"auto_process_uploads": False, "pose_setup_done": True})
        it = _init(client, old, "old.jpg", d0).json()
        _put(client, old, it["id"], 0, d0)
        wait_job(client, client.post(f"/api/imports/upload-session/{old}/commit").json()["job"]["id"])
        assert seen == []

        client.put("/api/settings", json={"auto_process_uploads": True})
        sid = _session(client)
        d1 = image_bytes(make_image(seed=21))
        it = _init(client, sid, "new.jpg", d1).json()
        _put(client, sid, it["id"], 0, d1)
        j = wait_job(client, client.post(f"/api/imports/upload-session/{sid}/commit").json()["job"]["id"])
        new_ids = j["result"]["photo_ids"]
        t0 = time.time()
        while len(seen) < 3 and time.time() - t0 < 10:
            time.sleep(0.05)
        assert seen == [
            (
                "extract_landmarks",
                {
                    "ids": new_ids,
                    "chain": [
                        {"type": "group_poses", "params": {}},
                        {"type": "align_photos", "params": {"force": True}},
                    ],
                },
            ),
            ("group_poses", {"chain": [{"type": "align_photos", "params": {"force": True}}]}),
            ("align_photos", {"force": True}),
        ]
    finally:
        for name, h in saved.items():
            if h is not None:
                _REGISTRY[name] = h


def test_auto_process_off_by_default(data_dir):
    from app.services.ingest import auto_process_enabled
    from app.settings import AppSettings

    assert auto_process_enabled(AppSettings()) is False
    assert auto_process_enabled(AppSettings(auto_process_uploads=True)) is True
