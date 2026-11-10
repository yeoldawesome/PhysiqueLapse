"""Face group: eye/mouth alignment, detection (mocked CLIP), override, sequence, export."""

import io

import numpy as np
from PIL import Image

from app.services import face_view as FV
from app.services import landmarks as L
from app.services.clip_backend import set_scorer_for_tests
from app.services.pose_backend import set_backend_for_tests
from tests import pose_fixtures as PF
from tests.conftest import save_image
from tests.test_pose import FakeBackend, _wait
from tests.test_sleeves import ColorScorer, _painted

COVERED = {60}  # painted red -> the fake scorer says "phone in front of the face"


def test_closeup_transform_puts_eyes_and_mouth_in_fixed_spots():
    pts = L.points_dict(PF.front_relaxed())
    tr = FV.closeup_transform(pts)
    assert np.allclose(tr.apply(FV._eye_mid(pts)), [FV.CANVAS / 2, FV.EYE_Y * FV.CANVAS], atol=1e-6)
    assert np.allclose(tr.apply(FV._mouth_mid(pts)), [FV.CANVAS / 2, FV.MOUTH_Y * FV.CANVAS], atol=1e-6)


def test_detect_override_sequence_and_export(client, tmp_path):
    fixtures = {10: PF.front_relaxed(), 60: PF.front_relaxed(), 110: PF.front_double_bicep()}
    set_backend_for_tests(FakeBackend(fixtures), key="heavy")
    set_scorer_for_tests(ColorScorer())
    try:
        root = tmp_path / "f"
        for i, tag in enumerate(fixtures):
            save_image(root / f"2024-0{i + 1}-01 {tag}.png", _painted(tag, tag in COVERED))
        _wait(client, client.post("/api/imports/folder", json={"path": str(root)}).json()["job"]["id"])
        _wait(client, client.post("/api/landmarks/extract").json()["id"])
        j = _wait(client, client.post("/api/faces/detect", json={}).json()["id"])
        assert j["status"] == "done", j
        assert j["result"]["found"] == 2
        assert client.get("/api/faces/summary").json() == {"visible": 2, "checked": 3, "unchecked": 0}
        assert client.get("/api/dashboard").json()["face"]["visible"] == 2
        assert client.get("/api/photos", params={"face": "visible"}).json()["total"] == 2

        seq = client.get("/api/faces/sequence").json()
        assert seq["group"] == "face" and [f["days_since_start"] for f in seq["frames"]] == [0, 60]
        r = client.get(seq["frames"][0]["aligned_url"])
        assert r.status_code == 200 and Image.open(io.BytesIO(r.content)).size == (FV.CANVAS, FV.CANVAS)

        photos = {p["original_filename"]: p for p in client.get("/api/photos").json()["items"]}
        covered = photos["2024-02-01 60.png"]
        assert covered["face_visible"] is False
        p = client.patch(f"/api/photos/{covered['id']}", json={"face_visible": True}).json()
        assert p["face_visible"] is True and p["face_source"] == "manual"
        _wait(client, client.post("/api/faces/detect", json={"force": True}).json()["id"])
        assert len(client.get("/api/faces/sequence").json()["frames"]) == 3
        p = client.patch(f"/api/photos/{covered['id']}", json={"face_auto": True}).json()
        assert p["face_visible"] is False and p["face_source"] == "auto"

        r = client.post("/api/exports", json={"group": "face", "format": "gif", "fps": 2, "width": 200})
        assert r.status_code == 200, r.text
        j = _wait(client, r.json()["job"]["id"])
        assert j["status"] == "done", j
        assert j["result"]["frames"] == 2 and "Face" in j["result"]["path"]
    finally:
        set_backend_for_tests(None, key=None)
        set_scorer_for_tests(None)
