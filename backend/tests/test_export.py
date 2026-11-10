"""M4: frame schedule, caption burn-in, MP4 + GIF export of a 30-frame synthetic sequence."""

import time
from datetime import datetime, timedelta

import numpy as np
from PIL import Image

from app import config
from app.services import export_video as EX


def test_frame_schedule_without_and_with_crossfade():
    s = EX.frame_schedule(3, fps=2, crossfade=0, out_fps=30)
    assert len(s) == 3 * 15 + 15
    assert all(a == b for a, b, _ in s)
    s = EX.frame_schedule(3, fps=2, crossfade=0.2, out_fps=30)
    blends = [x for x in s if x[0] != x[1]]
    assert len(blends) == 2 * 6  # 0.2 s * 30 fps between each pair
    assert all(0 < t < 1 for _, _, t in blends)
    assert len(s) == 3 * 15 + 15  # crossfades replace hold frames, total length unchanged
    # crossfade longer than the hold is capped
    s = EX.frame_schedule(2, fps=10, crossfade=5, out_fps=30)
    assert sum(1 for a, b, _ in s if a != b) == 2


def test_caption_is_drawn():
    img = Image.new("RGB", (540, 675), (128, 128, 128))
    out = np.asarray(EX.draw_caption(img, ["Mar 15, 2024  ·  Day 74", "Weight 180.2 lb"]))
    bottom = out[-80:]
    assert (bottom.max(axis=2) > 230).any()  # white text present
    assert (np.asarray(img)[:400] == out[:400]).all()  # top untouched


def _seed_sequence(client, n=30):
    """Photos + alignments written directly (no model needed)."""
    from sqlmodel import Session

    from app.db import engine
    from app.models import Alignment, Photo

    cat = next(c for c in client.get("/api/poses").json() if c["rule_key"] == "front_relaxed")
    W, H = 1080, 1350
    with Session(engine()) as s:
        for i in range(n):
            p = Photo(
                sha256=f"{i:064x}", original_path="originals/x.jpg", original_filename=f"f{i}.jpg", ext="jpg",
                width=W, height=H, taken_at=datetime(2024, 1, 1) + timedelta(days=3 * i), taken_at_source="manual",
                taken_at_confidence="high", pose_category_id=cat["id"], pose_source="manual", landmarks_status="ok",
            )
            s.add(p)
            s.flush()
            arr = np.zeros((H, W, 3), np.uint8)
            arr[:, :, 0] = (i * 8) % 256
            arr[200 + i * 20 : 400 + i * 20, 300:700] = 255
            path = config.ALIGNED_DIR / f"{p.id}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(arr).save(path, quality=90)
            s.add(Alignment(photo_id=p.id, pose_category_id=cat["id"], output_path=config.rel(path), canvas_w=W, canvas_h=H))
        s.commit()
    return cat["id"]


def _wait(client, jid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.1)
    raise AssertionError("timeout")


def test_export_mp4_and_gif(client):
    cid = _seed_sequence(client, 30)
    weight = next(m for m in client.get("/api/metrics").json() if m["kind"] == "weight")
    client.post("/api/metrics/entries", json={"metric_id": weight["id"], "date": "2024-01-02", "value": 180.0})

    seq = client.get(f"/api/poses/{cid}/sequence").json()
    assert len(seq["frames"]) == 30 and seq["frames"][1]["stats"][0]["offset_label"] == "2 days earlier"
    assert seq["frames"][-1]["stats"] == []  # outside the 7-day window

    r = client.post("/api/exports", json={"pose_id": cid, "fps": 4, "format": "mp4", "width": 540, "crossfade": 0.1,
                                          "caption": {"date": True, "days": True, "metrics": [weight["id"]]}})
    assert r.status_code == 200, r.text
    j = _wait(client, r.json()["job"]["id"])
    assert j["status"] == "done", j
    ex = next(e for e in client.get("/api/exports").json() if e["id"] == r.json()["export"]["id"])
    assert ex["status"] == "done" and ex["frames"] == 30 and ex["size_bytes"] > 10_000
    path = config.abs_path(ex["path"])
    import imageio_ffmpeg

    nframes, secs = imageio_ffmpeg.count_frames_and_secs(str(path))
    assert abs(secs - (30 + 1) / 4) < 0.2  # 30 photos at 4 fps + final linger
    d = client.get(ex["download_url"])
    assert d.status_code == 200 and d.headers["content-type"] == "video/mp4"

    r = client.post("/api/exports", json={"pose_id": cid, "fps": 5, "format": "gif", "width": 270, "from": "2024-01-01", "to": "2024-02-15"})
    j = _wait(client, r.json()["job"]["id"])
    assert j["status"] == "done", j
    gif = client.get("/api/exports").json()[0]
    assert gif["frames"] == 16  # 2024-01-01 .. 2024-02-15 every 3 days
    with Image.open(config.abs_path(gif["path"])) as im:
        assert im.size == (270, 338 - 338 % 2) and im.n_frames >= 16
    assert client.delete(f"/api/exports/{gif['id']}").status_code == 200
    assert not config.abs_path(gif["path"]).exists()


def test_export_without_frames_fails_cleanly(client):
    cat = next(c for c in client.get("/api/poses").json() if c["rule_key"] == "side_left")
    r = client.post("/api/exports", json={"pose_id": cat["id"], "format": "mp4"})
    j = _wait(client, r.json()["job"]["id"])
    assert j["status"] == "failed" and "no aligned frames" in j["error"]


def test_schedule_holds_order_and_fast_speeds():
    # 2 extra seconds on the first photo, 3 on the last
    s = EX.frame_schedule(3, fps=2, crossfade=0, out_fps=30, hold_start=2, hold_end=3)
    assert len(s) == 60 + 2 * 15 + 90
    assert s[0] == (0, 0, 0.0) and s[59] == (0, 0, 0.0) and s[-1] == (2, 2, 0.0)
    # faster than the frame rate: length stays exact, some photos get no frame
    s = EX.frame_schedule(120, fps=60, crossfade=0, out_fps=30)
    assert abs(len(s) - (119 / 60 * 30 + 1)) <= 1
    assert EX.play_order(4, boomerang=True) == [0, 1, 2, 3, 2, 1, 0]
    assert EX.play_order(3) == [0, 1, 2]


def test_pick_photos_and_shapes():
    from app.models import Photo

    base = datetime(2024, 1, 1, 9)
    ps = [Photo(id=i, taken_at=base + timedelta(hours=12 * i)) for i in range(30)]  # 2 per day, 15 days
    assert len(EX.pick_photos(ps, "day")) == 15
    assert len(EX.pick_photos(ps, "week")) == 3  # ISO weeks of Jan 1–15, 2024
    assert len(EX.pick_photos(ps, "month")) == 1
    assert [p.id for p in EX.pick_photos(ps, "all", step=10)] == [0, 10, 20]
    assert [p.id for p in EX.pick_photos(ps, "all", step=10, reverse=True)] == [20, 10, 0]

    assert EX.output_size(1080, 1080, 1350, "original") == (1080, 1350)
    assert EX.output_size(1080, 1080, 1350, "9:16") == (1080, 1920)
    assert EX.output_size(720, 1080, 1350, "1:1") == (720, 720)
    img = Image.new("RGB", (400, 500), (200, 50, 50))
    filled = EX.compose(img, 400, 400, "fill")
    assert filled.size == (400, 400) and filled.getpixel((0, 0)) == (200, 50, 50)
    fitted = EX.compose(img, 400, 400, "fit", "#00ff00")
    assert fitted.size == (400, 400) and fitted.getpixel((0, 200)) == (0, 255, 0)  # side bar

    top = np.asarray(EX.draw_caption(Image.new("RGB", (540, 675), (128, 128, 128)), ["Title"], position="top"))
    assert (top[:60].max(axis=2) > 230).any() and (top[-200:] == 128).all()
    bar = np.asarray(EX.draw_progress(Image.new("RGB", (100, 100), (0, 0, 0)), 0.5))
    assert bar[-1, 10].tolist() == [255, 255, 255] and bar[-1, 90].tolist() == [40, 40, 40]


def test_export_with_all_options(client):
    cid = _seed_sequence(client, 30)
    r = client.post(
        "/api/exports",
        json={
            "pose_id": cid, "format": "mp4", "fps": 45, "width": 360, "quality": 5,
            "hold_start": 1, "hold_end": 2, "every": "week", "step": 1, "reverse": True, "boomerang": True,
            "aspect": "9:16", "fit": "fit", "background": "#202020", "progress_bar": True,
            "caption": {"date": True, "days": True, "title": "Cut 2024", "position": "top", "size": 1.5},
        },
    )
    assert r.status_code == 200, r.text
    j = _wait(client, r.json()["job"]["id"])
    assert j["status"] == "done", j
    res = j["result"]
    assert res["frames"] < 30 and res["thinned"] == 30 - res["frames"]  # one per week
    n = res["frames"]
    expect = 1 + (2 * n - 2) / 45 + 2  # start hold + boomerang photos at 45/s + end hold
    assert abs(res["seconds"] - expect) < 0.15, (res, expect)
    ex = next(e for e in client.get("/api/exports").json() if e["id"] == r.json()["export"]["id"])
    import imageio_ffmpeg

    reader = imageio_ffmpeg.read_frames(str(config.abs_path(ex["path"])))
    meta = next(reader)
    assert tuple(meta["size"]) == (360, 640) and meta["fps"] == 45
    reader.close()
    # out-of-range values are rejected
    assert client.post("/api/exports", json={"pose_id": cid, "fps": 61}).status_code == 422
    assert client.post("/api/exports", json={"pose_id": cid, "aspect": "2:1"}).status_code == 422
