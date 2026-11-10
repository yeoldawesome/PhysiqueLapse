"""Body-size estimates: synthetic outlines with known widths, rejection rules, trend, calibration, API."""

from datetime import date, datetime, timedelta

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sqlmodel import Session

from app.services import landmarks as L
from app.services import measure as M
from tests import pose_fixtures as PF

W, H = PF.W, PF.H  # 800 x 1000, torso length T = 300 in the fixtures


def body_mask(pose: dict, *, torso_w=200, delt_w=260, waist_w=200, arm_w=50, forearm_w=40, scale=1.0) -> np.ndarray:
    """Draw a simple body outline around fixture landmarks (mask at `scale` x image size)."""
    pts = {k: (v["x_px"] * scale, v["y_px"] * scale) for k, v in L.points_dict(pose).items()}
    im = Image.new("L", (int(W * scale), int(H * scale)), 0)
    d = ImageDraw.Draw(im)
    cx = 400 * scale
    d.rectangle([cx - delt_w / 2 * scale, 385 * scale, cx + delt_w / 2 * scale, 470 * scale], fill=255)  # delts
    d.rectangle([cx - torso_w / 2 * scale, 400 * scale, cx + torso_w / 2 * scale, 640 * scale], fill=255)  # chest/lats
    d.rectangle([cx - waist_w / 2 * scale, 640 * scale, cx + waist_w / 2 * scale, 720 * scale], fill=255)  # waist
    d.ellipse([cx - 45 * scale, 220 * scale, cx + 45 * scale, 330 * scale], fill=255)  # head
    d.rectangle([cx - 15 * scale, 320 * scale, cx + 15 * scale, 400 * scale], fill=255)  # neck
    for side in ("left", "right"):
        d.line([pts[f"{side}_shoulder"], pts[f"{side}_elbow"]], fill=255, width=int(arm_w * scale))
        d.line([pts[f"{side}_elbow"], pts[f"{side}_wrist"]], fill=255, width=int(forearm_w * scale))
        d.line([pts[f"{side}_hip"], pts[f"{side}_knee"]], fill=255, width=int(70 * scale))
    return np.asarray(im) >= 128


def test_double_bicep_widths():
    pose = PF.front_double_bicep()
    r = M.measure(L.points_dict(pose), body_mask(pose), W, H)
    it = r["items"]
    assert r["torso_px"] == pytest.approx(300, abs=0.5)
    assert it["lats"]["valid"] and it["lats"]["ratio"] == pytest.approx(200 / 300, abs=0.02)
    assert it["waist"]["valid"] and it["waist"]["ratio"] == pytest.approx(200 / 300, abs=0.02)
    assert it["arm"]["valid"] and it["arm"]["ratio"] == pytest.approx(50 / 300, abs=0.02)


def test_arm_along_shoulder_line_refuses_shoulder_width():
    pose = PF.front_double_bicep()
    for q in pose["points"]:
        if q["name"] == "left_elbow":
            q["x_px"], q["y_px"] = 660, 430  # upper arm straight out at delt height
        if q["name"] == "left_wrist":
            q["x_px"], q["y_px"] = 670, 300
    it = M.measure(L.points_dict(pose), body_mask(pose), W, H)["items"]
    assert not it["shoulders"]["valid"] and "raised" in it["shoulders"]["why"]
    assert not it["vtaper"]["valid"]


def test_relaxed_front_shoulders_and_merged_arms():
    pose = PF.front_relaxed()
    mask = body_mask(pose, delt_w=290, waist_w=180)
    it = M.measure(L.points_dict(pose), mask, W, H)["items"]
    assert it["shoulders"]["valid"] and it["shoulders"]["ratio"] == pytest.approx(290 / 300, abs=0.02)
    assert it["waist"]["valid"] and it["waist"]["ratio"] == pytest.approx(180 / 300, abs=0.02)
    assert it["vtaper"]["valid"] and it["vtaper"]["ratio"] == pytest.approx(290 / 180, abs=0.05)
    # arms hang against the torso at armpit level: lat width is refused
    wide_arms = body_mask(pose, torso_w=300)
    it2 = M.measure(L.points_dict(pose), wide_arms, W, H)["items"]
    assert not it2["lats"]["valid"] and "touching" in it2["lats"]["why"]


def test_scale_invariance_and_mask_resolution():
    pose = PF.front_double_bicep()
    a = M.measure(L.points_dict(pose), body_mask(pose), W, H)["items"]
    b = M.measure(L.points_dict(pose), body_mask(pose, scale=0.6), W, H)["items"]  # coarser mask
    for k in ("lats", "waist", "arm"):
        assert a[k]["ratio"] == pytest.approx(b[k]["ratio"], abs=0.02)


def test_bigger_arm_reads_bigger():
    pose = PF.front_double_bicep()
    small = M.measure(L.points_dict(pose), body_mask(pose, arm_w=44), W, H)["items"]["arm"]["ratio"]
    big = M.measure(L.points_dict(pose), body_mask(pose, arm_w=56), W, H)["items"]["arm"]["ratio"]
    assert big / small == pytest.approx(56 / 44, rel=0.06)


def test_hidden_hips_means_no_ruler():
    pose = PF.front_relaxed()
    for q in pose["points"]:
        if "hip" in q["name"]:
            q["visibility"] = 0.1
    r = M.measure(L.points_dict(pose), body_mask(pose), W, H)
    assert r["items"] == {} and "hips" in r["reason"]


def test_rolling_median_and_calibration():
    d0 = date(2024, 1, 1)
    pts = [(d0 + timedelta(days=i), v) for i, v in enumerate([1.0, 1.0, 5.0, 1.0, 1.0])]
    sm = M.rolling_median(pts, 4)
    assert [v for _, v in sm] == [1.0, 1.0, 1.0, 1.0, 1.0]  # the outlier is ignored
    assert M.fit_calibration([(0.2, 14.0), (0.25, 17.5)]) == pytest.approx(70.0)
    assert M.fit_calibration([]) is None


def _photo(s, i, when, cat_id, items):
    from app.models import Photo, PhotoMeasurement

    p = Photo(sha256=f"{i:064x}", original_path="originals/x.jpg", original_filename=f"p{i}.jpg", ext="jpg", width=800, height=1000,
              taken_at=when, pose_category_id=cat_id, landmarks_status="ok")
    s.add(p)
    s.flush()
    s.add(PhotoMeasurement(photo_id=p.id, version=M.MEASURE_VERSION, torso_px=300, data=items))
    return p


def test_series_api_trend_and_tape_calibration(client):
    from app.db import engine

    cats = {c["rule_key"]: c["id"] for c in client.get("/api/poses").json()}
    flex = cats["selfie_flex_right"]
    with Session(engine()) as s:
        for i in range(10):
            ratio = 0.20 + 0.002 * i
            _photo(s, i, datetime(2024, 1, 1) + timedelta(days=7 * i), flex, {"arm": {"valid": True, "ratio": ratio, "px": 60}})
        _photo(s, 99, datetime(2024, 1, 5), cats["selfie_side_90_left"], {"arm": {"valid": True, "ratio": 0.5}})  # other pose
        s.commit()
    summ = {m["name"]: m for m in client.get("/api/measurements/summary").json()}
    assert summ["arm"]["default_pose_id"] == flex  # preferred pose with enough photos wins
    sr = client.get("/api/measurements/series", params={"name": "arm", "pose_id": flex}).json()
    assert len(sr["points"]) == 10 and sr["calibration"] is None and "arm" in sr["tape_metric_names"]
    # log two tape measurements of the arm -> inches estimate appears
    r = client.post("/api/metrics", json={"name": "Arm", "unit": "in"})
    mid = r.json()["id"]
    client.post("/api/metrics/entries", json={"metric_id": mid, "date": "2024-01-01", "value": 14.0})
    client.post("/api/metrics/entries", json={"metric_id": mid, "date": "2024-03-04", "value": 15.26})
    sr = client.get("/api/measurements/series", params={"name": "arm", "pose_id": flex}).json()
    cal = sr["calibration"]
    assert cal and cal["metric"] == "Arm" and cal["pairs"] == 2 and cal["unit"] == "in"
    assert sr["points"][0]["value"] == pytest.approx(14.0, abs=0.4)
    assert client.get("/api/measurements/series", params={"name": "nope"}).status_code == 404


def test_overlay_and_photo_endpoint(client, tmp_path):
    from tests.conftest import make_image, save_image

    root = tmp_path / "one"
    save_image(root / "a.jpg", make_image(400, 500))
    import time

    jid = client.post("/api/imports/folder", json={"path": str(root)}).json()["job"]["id"]
    for _ in range(100):
        if client.get(f"/api/jobs/{jid}").json()["status"] == "done":
            break
        time.sleep(0.05)
    pid = client.get("/api/photos").json()["items"][0]["id"]
    assert client.get(f"/api/photos/{pid}/measurements").json() == {"photo_id": pid, "measured": False, "items": {}}
    r = client.get(f"/api/photos/{pid}/measure-overlay")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
