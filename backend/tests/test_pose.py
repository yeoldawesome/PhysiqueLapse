"""M2: features, rules, flip invariance, kNN, and the extract/classify jobs with a fake backend."""

import time

import numpy as np
import pytest

from app.services import classify as C
from app.services import features as F
from app.services.landmarks import apply_flip
from app.services.pose_backend import LANDMARK_NAMES, Point, set_backend_for_tests
from app.settings import AppSettings
from tests import pose_fixtures as PF
from tests.conftest import make_image, save_image

CATS = {
    "front_double_bicep": 1, "back_double_bicep": 2, "front_relaxed": 3, "side_left": 4, "side_right": 5,
    "selfie_front": 7, "selfie_flex": 8, "selfie_side_down": 9, "selfie_side_90": 10, "front_lat_spread": 11, "other": 6,
}


@pytest.mark.parametrize(
    "fixture,expected",
    [
        (PF.front_double_bicep, "front_double_bicep"),
        (PF.back_double_bicep, "back_double_bicep"),
        (PF.front_relaxed, "front_relaxed"),
        (PF.side_left, "side_left"),
        (lambda: PF.mirrored(PF.side_left()), "side_right"),
        (lambda: PF.mirrored(PF.front_double_bicep()), "front_double_bicep"),
        (PF.selfie_front, "selfie_front"),
        (PF.front_lat_spread, "front_lat_spread"),
        (PF.selfie_side_down, "selfie_side_down"),
        (PF.selfie_side_90, "selfie_side_90"),
        (lambda: PF.mirrored(PF.selfie_side_90()), "selfie_side_90"),
        (PF.selfie_flex, "selfie_flex"),
        (lambda: PF.mirrored(PF.selfie_flex()), "selfie_flex"),
    ],
)
def test_rules_classify_fixtures(fixture, expected):
    vec = F.compute(fixture())
    pred = C.predict_rules(vec, CATS)
    scores = C.rule_scores(vec)
    assert pred.category_id == CATS[expected], scores
    assert pred.confidence >= 0.6, (pred, scores)


def test_no_person_goes_to_other():
    assert F.compute(PF.no_person()) is None
    pred = C.predict_rules(None, CATS)
    assert pred.category_id == CATS["other"] and pred.confidence == 0.0


def test_ambiguous_pose_goes_to_other():
    d = PF.front_relaxed()
    for q in d["points"]:  # one arm up, one down, face hidden: no rule fits
        if q["name"] == "left_wrist":
            q["y_px"] = 250
        if q["name"] == "left_elbow":
            q["x_px"], q["y_px"] = 620, 380
        if "eye" in q["name"] or q["name"] == "nose":
            q["visibility"] = 0.1
    pred = C.predict_rules(F.compute(d), CATS)
    assert pred.category_id == CATS["other"]


def test_feature_scale_and_translation_invariance():
    d = PF.front_double_bicep()
    v1 = np.array(F.compute(d))
    d2 = PF.front_double_bicep()
    for q in d2["points"]:
        q["x_px"] = q["x_px"] * 2.5 + 37
        q["y_px"] = q["y_px"] * 2.5 - 11
    v2 = np.array(F.compute(d2))
    assert np.allclose(v1, v2, atol=1e-3)


@pytest.mark.parametrize("fixture", [PF.front_double_bicep, PF.front_relaxed, PF.side_left, PF.back_double_bicep])
def test_flip_gives_mirrored_features(fixture):
    d = fixture()
    v = F.compute(d)
    vf = F.compute(apply_flip(d, True))
    assert np.allclose(vf, F.mirror(v), atol=2e-4)
    # flip twice = identity
    assert apply_flip(apply_flip(d, True), True)["points"] == d["points"]


def test_flip_swaps_side_facing():
    v = F.compute(PF.side_left())
    assert F.facing_side(v) > 0.5
    assert F.facing_side(F.compute(PF.mirrored(PF.side_left()))) < -0.5


def test_knn_vote_and_activation():
    s = AppSettings(knn_min_labels=4, knn_min_categories=2, knn_k=3)
    fdb, rel = F.compute(PF.front_double_bicep()), F.compute(PF.front_relaxed())
    rng = np.random.default_rng(0)
    labels = [(1, list(np.array(fdb) + rng.normal(0, 0.01, len(fdb)))) for _ in range(3)]
    assert not C.knn_active(labels, s)  # one category only
    labels += [(3, list(np.array(rel) + rng.normal(0, 0.01, len(rel)))) for _ in range(3)]
    assert C.knn_active(labels, s)
    model = C.KNN(np.array([v for _, v in labels]), np.array([c for c, _ in labels]), s.knn_k)
    cid, conf = model.predict(fdb)
    assert cid == 1 and conf > 0.9
    cid, conf = model.predict(rel)
    assert cid == 3 and conf > 0.9


# ----------------------------------------------------------------------------- jobs (fake backend)


class FakeBackend:
    """Returns a fixture pose chosen by the image's dominant red value."""

    name = "fake"

    def __init__(self, by_seed):
        self.by_seed = by_seed

    def detect(self, rgb, num_poses=1):
        key = int(rgb[0, 0, 2])  # blue channel of the top-left pixel encodes the fixture
        d = self.by_seed.get(key)
        if d is None or not d["detected"]:
            return []
        h, w = rgb.shape[:2]
        sx, sy = w / PF.W, h / PF.H
        pts = [Point(q["name"], q["x_px"] * sx, q["y_px"] * sy, q["z"], q["visibility"], q["presence"]) for q in d["points"]]
        assert [p.name for p in pts] == LANDMARK_NAMES
        return [pts]


def _tagged_image(tag: int, w: int = 400, h: int = 500):
    img = make_image(w, h, seed=tag)
    for x in range(8):
        for y in range(8):
            img.putpixel((x, y), (0, 0, tag))
    return img


def _wait(client, jid, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_extract_and_classify_jobs(client, tmp_path):
    fixtures = {10: PF.front_double_bicep(), 60: PF.front_relaxed(), 110: PF.side_left(), 160: PF.no_person()}
    set_backend_for_tests(FakeBackend(fixtures), key="heavy")
    try:
        root = tmp_path / "poses"
        for tag in fixtures:
            save_image(root / f"{tag}.png", _tagged_image(tag))
        j = _wait(client, client.post("/api/imports/folder", json={"path": str(root)}).json()["job"]["id"])
        assert j["result"]["counts"]["accepted"] == 4

        j = _wait(client, client.post("/api/landmarks/extract").json()["id"])
        assert j["status"] == "done", j
        assert j["result"]["counts"] == {"ok": 3, "none": 1, "failed": 0}
        # a second run has nothing to do
        j2 = _wait(client, client.post("/api/landmarks/extract").json()["id"])
        assert j2["result"]["processed"] == 0

        photos = {p["original_filename"]: p for p in client.get("/api/photos").json()["items"]}
        lm = client.get(f"/api/photos/{photos['10.png']['id']}/landmarks").json()
        assert lm["detected"] and len(lm["points"]) == 33
        # landmarks are in original pixel coordinates (400x500 image)
        nose = lm["points"][0]
        assert abs(nose["x_px"] - 200) < 2 and abs(nose["y_px"] - 140) < 2

        j = _wait(client, client.post("/api/poses/classify", json={}).json()["id"])
        assert j["status"] == "done", j
        cats = {c["rule_key"]: c["id"] for c in client.get("/api/poses").json()}
        photos = {p["original_filename"]: p for p in client.get("/api/photos").json()["items"]}
        assert photos["10.png"]["pose_category_id"] == cats["front_double_bicep"]
        assert photos["60.png"]["pose_category_id"] == cats["front_relaxed"]
        assert photos["110.png"]["pose_category_id"] == cats["side_left"]
        assert photos["160.png"]["pose_category_id"] == cats["other"]
        assert photos["160.png"]["pose_needs_review"]
        assert photos["10.png"]["pose_source"] == "rule"

        # flipping the side photo makes it a right-side pose on reclassify
        pid = photos["110.png"]["id"]
        client.patch(f"/api/photos/{pid}", json={"flip_horizontal": True})
        lm = client.get(f"/api/photos/{pid}/landmarks").json()
        assert lm["flip_horizontal"] is True
        _wait(client, client.post("/api/poses/classify", json={}).json()["id"])
        assert client.get(f"/api/photos/{pid}").json()["pose_category_id"] == cats["side_right"]

        # manual assignments are never overwritten
        client.patch(f"/api/photos/{photos['60.png']['id']}", json={"pose_category_id": cats["back_double_bicep"]})
        _wait(client, client.post("/api/poses/classify", json={}).json()["id"])
        p = client.get(f"/api/photos/{photos['60.png']['id']}").json()
        assert p["pose_category_id"] == cats["back_double_bicep"] and p["pose_source"] == "manual"
    finally:
        set_backend_for_tests(None, key=None)


@pytest.mark.models
def test_mediapipe_smoke_on_generated_image():
    """Runs only when the real model is present (pytest -m models)."""
    from app import config
    from app.services.pose_backend import MediaPipeBackend

    if not config.pose_model_path("heavy").exists():
        pytest.skip("pose model not downloaded")
    be = MediaPipeBackend("heavy")
    rgb = np.asarray(make_image(640, 800, seed=1))
    poses = be.detect(rgb)
    assert isinstance(poses, list)  # a gradient has no person; the call itself must work
