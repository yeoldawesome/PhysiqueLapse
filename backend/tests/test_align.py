"""M3: similarity solve recovers synthetic transforms; clamp; head fallback; manual round trip; API."""

import copy
import math

import numpy as np
import pytest
from PIL import Image

from app.models import PoseCategory
from app.services import align as A
from app.services import landmarks as L
from app.services.pose_backend import set_backend_for_tests
from tests import pose_fixtures as PF
from tests.conftest import save_image
from tests.test_pose import FakeBackend, _tagged_image, _wait

W, H = 1080, 1350


def transformed(d: dict, s: float, theta: float, tx: float, ty: float) -> dict:
    d = copy.deepcopy(d)
    M = s * A.rot(theta)
    for q in d["points"]:
        x, y = M @ np.array([q["x_px"], q["y_px"]]) + np.array([tx, ty])
        q["x_px"], q["y_px"] = float(x), float(y)
    return d


def cat(**kw) -> PoseCategory:
    base = dict(id=1, name="FDB", anchor_mode="auto", scale_basis="torso", rotation_basis="torso", framing_zoom=1.0, framing_dx=0.0, framing_dy=0.0)
    base.update(kw)
    return PoseCategory(**base)


@pytest.mark.parametrize("seed", range(12))
def test_recovers_random_similarity(seed):
    rng = np.random.default_rng(seed)
    ref = PF.front_double_bicep()
    c = cat()
    target = A.make_target(L.points_dict(ref), PF.W, PF.H, c, W, H)
    s, th = float(rng.uniform(0.4, 3.0)), float(rng.uniform(-7.5, 7.5))
    tx, ty = float(rng.uniform(-300, 300)), float(rng.uniform(-300, 300))
    photo = transformed(ref, s, th, tx, ty)
    tr, flags, basis = A.solve(L.points_dict(photo), target, c, clamp_deg=8.0)
    assert flags == {} and basis == "joints"
    r = A.residuals(L.points_dict(photo), tr, target)
    T = target.lengths["torso"]
    assert r["head"] < 0.01
    assert r["shoulder_mid"] < 1e-6 * T + 1e-6 and r["hip_mid"] < 1e-6 * T + 1e-6
    assert r["eye_mid"] < 1e-5 * T + 1e-6
    # the recovered transform undoes the synthetic one (times the reference's fit)
    assert tr.scale == pytest.approx(target.ref_transform.scale / s, rel=1e-9)
    assert tr.rotation_deg == pytest.approx(-th, abs=1e-9)


def test_rotation_is_clamped():
    ref = PF.front_relaxed()
    c = cat(align_method="head")
    target = A.make_target(L.points_dict(ref), PF.W, PF.H, c, W, H)
    photo = transformed(ref, 1.0, 20.0, 0, 0)
    tr, flags, _ = A.solve(L.points_dict(photo), target, c, clamp_deg=8.0)
    assert tr.rotation_deg == pytest.approx(-8.0)
    assert flags["rotation_clamped"] == pytest.approx(-20.0, abs=0.1)
    # head still lands exactly
    assert A.residuals(L.points_dict(photo), tr, target)["head"] < 0.01


def test_rotation_basis_none_never_rotates():
    ref = PF.front_relaxed()
    c = cat(rotation_basis="none")
    target = A.make_target(L.points_dict(ref), PF.W, PF.H, c, W, H)
    tr, flags, _ = A.solve(L.points_dict(transformed(ref, 1.3, 5.0, 10, 10)), target, c, clamp_deg=8.0)
    assert tr.rotation_deg == 0.0 and "rotation_clamped" not in flags


def test_head_size_fallback_when_hips_hidden():
    ref = PF.front_double_bicep()
    c = cat(align_method="head")
    target = A.make_target(L.points_dict(ref), PF.W, PF.H, c, W, H)
    photo = transformed(ref, 1.7, 3.0, 50, -40)
    for q in photo["points"]:
        if q["name"] in ("left_hip", "right_hip"):
            q["visibility"] = 0.1
    tr, flags, basis = A.solve(L.points_dict(photo), target, c, clamp_deg=8.0)
    assert basis == "head" and flags.get("scale_from_head")
    assert tr.scale == pytest.approx(target.ref_transform.scale / 1.7, rel=1e-9)


def test_low_anchor_confidence_flag_for_back_pose_with_face_anchor():
    ref = PF.back_double_bicep()
    c = cat(anchor_mode="face", align_method="head")
    target = A.make_target(L.points_dict(ref), PF.W, PF.H, c, W, H)
    _, flags, _ = A.solve(L.points_dict(ref), target, c, clamp_deg=8.0)
    assert flags.get("low_anchor_confidence")
    c2 = cat(anchor_mode="ears", align_method="head")
    target2 = A.make_target(L.points_dict(ref), PF.W, PF.H, c2, W, H)
    _, flags2, _ = A.solve(L.points_dict(ref), target2, c2, clamp_deg=8.0)
    assert not flags2.get("low_anchor_confidence")


@pytest.mark.parametrize("method", ["body", "head"])
def test_both_methods_recover_rigid_motion(method):
    ref = PF.side_left()
    c = cat(align_method=method)
    target = A.make_target(L.points_dict(ref), PF.W, PF.H, c, W, H)
    tr, flags, basis = A.solve(L.points_dict(transformed(ref, 1.4, -5.0, 30, 70)), target, c, clamp_deg=8.0)
    assert basis == ("body" if method == "body" else "torso")
    assert tr.rotation_deg == pytest.approx(5.0, abs=1e-6)
    assert tr.scale == pytest.approx(target.ref_transform.scale / 1.4)


def test_body_fit_spreads_error_instead_of_pinning_the_head():
    """Head shifted relative to the body: 'head' pins the head and the torso drifts;
    'body' keeps the torso close and accepts a small head offset."""
    ref = PF.front_relaxed()
    photo = copy.deepcopy(ref)
    head = {"nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_eye_inner", "right_eye_inner",
            "left_eye_outer", "right_eye_outer", "mouth_left", "mouth_right"}
    for q in photo["points"]:
        if q["name"] in head:
            q["x_px"] += 40
    out = {}
    for method in ("body", "head"):
        c = cat(align_method=method)
        target = A.make_target(L.points_dict(ref), PF.W, PF.H, c, W, H)
        tr, _, _ = A.solve(L.points_dict(photo), target, c, clamp_deg=8.0)
        out[method] = A.residuals(L.points_dict(photo), tr, target)
    assert out["head"]["head"] < 0.01
    body_torso = out["body"]["shoulder_mid"] + out["body"]["hip_mid"]
    head_torso = out["head"]["shoulder_mid"] + out["head"]["hip_mid"]
    assert body_torso < 0.5 * head_torso


def test_body_fit_clamps_rotation_and_falls_back_without_torso():
    ref = PF.front_relaxed()
    c = cat(align_method="body")
    target = A.make_target(L.points_dict(ref), PF.W, PF.H, c, W, H)
    tr, flags, _ = A.solve(L.points_dict(transformed(ref, 1.0, 20.0, 0, 0)), target, c, clamp_deg=8.0)
    assert tr.rotation_deg == pytest.approx(-8.0)
    assert flags["rotation_clamped"] == pytest.approx(-20.0, abs=0.1)
    hidden = transformed(ref, 1.2, 2.0, 0, 0)
    for q in hidden["points"]:
        if q["name"] in ("left_shoulder", "right_shoulder", "left_hip", "right_hip"):
            q["visibility"] = 0.1
    _, _, basis = A.solve(L.points_dict(hidden), target, c, clamp_deg=8.0)
    assert basis == "head"  # head-anchor fallback with head-size scale


def test_two_point_solve_is_exact():
    rng = np.random.default_rng(3)
    for _ in range(10):
        a, b, c_, d = (rng.uniform(0, 1000, 2) for _ in range(4))
        tr = A.solve_two_point(a, b, c_, d)
        assert np.allclose(tr.apply(a), c_, atol=1e-9) and np.allclose(tr.apply(b), d, atol=1e-9)


def test_fit_transform_centres_and_frames():
    tr = A.fit_transform(800, 1000, W, H)
    assert tr.scale == pytest.approx(min(W / 800, H / 1000))
    assert tr.apply(np.array([400.0, 500.0])) == pytest.approx([W / 2, H / 2])
    tr2 = A.fit_transform(800, 1000, W, H, zoom=1.5, dx=0.1)
    assert tr2.apply(np.array([400.0, 500.0])) == pytest.approx([W / 2 + 0.1 * W, H / 2])


def test_wrap180():
    assert A.wrap180(190) == pytest.approx(-170)
    assert A.wrap180(-190) == pytest.approx(170)
    assert math.isclose(A.wrap180(0), 0)


# ----------------------------------------------------------------------------- API round trip


def test_align_job_editor_manual_and_eval(client, tmp_path):
    # three photos of the same pose at different scales/positions/rotations
    base = PF.front_double_bicep()
    fixtures = {
        10: base,
        60: transformed(base, 0.8, 4.0, 60, 40),
        110: transformed(base, 1.1, -3.0, -50, -20),
    }
    set_backend_for_tests(FakeBackend(fixtures), key="heavy")
    try:
        root = tmp_path / "fdb"
        for i, tag in enumerate(fixtures):
            save_image(root / f"2024-0{i + 1}-01 {tag}.png", _tagged_image(tag, 800, 1000))
        _wait(client, client.post("/api/imports/folder", json={"path": str(root)}).json()["job"]["id"])
        assert _wait(client, client.post("/api/landmarks/extract").json()["id"])["status"] == "done"
        cats = {c["rule_key"]: c["id"] for c in client.get("/api/poses").json()}
        fdb = cats["front_double_bicep"]
        ids = [p["id"] for p in client.get("/api/photos", params={"sort": "taken_at_asc"}).json()["items"]]
        client.post("/api/photos/bulk", json={"ids": ids, "patch": {"pose_category_id": fdb}})

        # align without explicit reference -> earliest photo becomes the reference
        j = _wait(client, client.post(f"/api/poses/{fdb}/align", json={}).json()["id"])
        assert j["status"] == "done", j
        assert j["result"]["aligned"] == 3
        cat_row = next(c for c in client.get("/api/poses").json() if c["id"] == fdb)
        assert cat_row["reference_photo_id"] == ids[0]

        r = client.get(f"/api/photos/{ids[1]}/aligned")
        assert r.status_code == 200
        from io import BytesIO

        assert Image.open(BytesIO(r.content)).size == (W, H)
        al = client.get(f"/api/photos/{ids[1]}").json()["alignment"]
        assert al["method"] == "auto" and al["residual_px"] < 1.0

        ev = client.get(f"/api/poses/{fdb}/eval").json()
        assert ev["n"] == 2 and ev["summary"]["mean"]["p95_px"] < 1.0

        seq = client.get(f"/api/poses/{fdb}/sequence").json()
        assert [f["id"] for f in seq["frames"]] == ids and seq["unaligned"] == 0
        assert [f["days_since_start"] for f in seq["frames"]] == [0, 31, 60]

        # re-running is a no-op
        j = _wait(client, client.post(f"/api/poses/{fdb}/align", json={}).json()["id"])
        assert j["result"]["aligned"] == 0

        # editor context + manual override + reset
        ctx = client.get(f"/api/alignments/{ids[2]}/context").json()
        assert ctx["reference_photo_id"] == ids[0] and ctx["source_anchors"] is not None
        manual = {"scale": 1.0, "rotation_deg": 2.0, "tx": 10.0, "ty": 20.0}
        r = client.put(f"/api/alignments/{ids[2]}", json=manual)
        assert r.status_code == 200, r.text
        assert r.json()["method"] == "manual" and r.json()["rotation_deg"] == 2.0
        # manual survives a normal align run
        _wait(client, client.post(f"/api/poses/{fdb}/align", json={"force": False}).json()["id"])
        assert client.get(f"/api/alignments/{ids[2]}/context").json()["current"]["method"] == "manual"
        # anchors-based manual solve lands the head exactly on the target
        r = client.put(f"/api/alignments/{ids[2]}", json={"head": ctx["source_anchors"]["head"], "hip": ctx["source_anchors"]["hip"]})
        assert r.json()["residual_px"] < 1.0
        r = client.delete(f"/api/alignments/{ids[2]}")
        assert r.json()["method"] == "auto"

        # changing the reference marks the category stale and re-aligns
        client.post(f"/api/poses/{fdb}/reference", json={"photo_id": ids[1]})
        assert client.get(f"/api/photos/{ids[0]}").json()["alignment"]["stale"] is True
        j = _wait(client, client.post(f"/api/poses/{fdb}/align", json={}).json()["id"])
        assert j["result"]["aligned"] == 3
    finally:
        set_backend_for_tests(None, key=None)


def test_joints_fit_brings_arms_closer_than_body_fit():
    """Same person, arms held a little higher than in the reference: fitting the arm joints
    lands elbows/wrists closer (and is still a single rigid move, so sizes are unchanged)."""
    ref = PF.front_double_bicep()
    photo = transformed(ref, 1.1, 2.0, 15, -10)
    for q in photo["points"]:
        if "elbow" in q["name"] or "wrist" in q["name"]:
            q["y_px"] -= 25
    out = {}
    for method in ("body", "joints"):
        c = cat(align_method=method)
        target = A.make_target(L.points_dict(ref), PF.W, PF.H, c, W, H)
        tr, _, basis = A.solve(L.points_dict(photo), target, c, clamp_deg=15.0)
        assert basis == method
        out[method] = A.joint_match(L.points_dict(photo), tr, target)
        assert tr.scale == pytest.approx(target.ref_transform.scale / 1.1, rel=0.05)
    assert out["joints"] < 0.8 * out["body"]
