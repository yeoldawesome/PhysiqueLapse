"""Sleeves check: detection (mocked CLIP), manual override, filters, Viewer frames, export."""

import numpy as np
from PIL import Image

from app.services import landmarks as L
from app.services import sleeves as SL
from app.services.clip_backend import set_scorer_for_tests
from app.services.pose_backend import set_backend_for_tests
from tests import pose_fixtures as PF
from tests.conftest import save_image
from tests.test_pose import FakeBackend, _tagged_image, _wait

SLEEVED = {60}  # tags whose photo is painted red ("sleeves"); the rest are green ("bare arms")


class ColorScorer:
    """'Bare arms' when the crop is mostly green, 'sleeves' when mostly red."""

    def positive_probability(self, images, positive, negative):
        out = []
        for im in images:
            a = np.asarray(im.convert("RGB")).astype(int)
            out.append(0.95 if (a[..., 1] - a[..., 0]).mean() > 0 else 0.05)
        return out


def _painted(tag: int, sleeved: bool):
    arr = np.asarray(_tagged_image(tag, 800, 1000)).astype(float)
    color = np.array((220, 0, 0) if sleeved else (0, 220, 0), dtype=float)
    tl = arr[:8, :8].copy()
    arr = 0.5 * arr + 0.5 * color  # tinted, but the seeded texture keeps photos distinct for dedupe
    arr[:8, :8] = tl  # keep the tag pixel the fake pose backend reads
    return Image.fromarray(arr.astype(np.uint8))


def test_crops_are_upright_and_banded():
    pts = L.points_dict(PF.front_double_bicep())
    img = Image.new("RGB", (PF.W, PF.H), (0, 200, 0))
    arm = SL.arm_crop(img, pts, SL.shown_arm(pts))
    assert arm is not None and arm.size == (SL.ARM_N, SL.ARM_N)
    a = np.asarray(arm)
    assert tuple(a[SL.ARM_N // 2, 0]) == (128, 128, 128)  # outside the band: gray
    assert a[SL.ARM_N // 2, SL.ARM_N // 2, 1] == 200  # on the arm: image content
    assert SL.person_crop(img, pts) is not None


def test_detect_override_filters_and_export(client, tmp_path):
    fixtures = {10: PF.front_relaxed(), 60: PF.front_relaxed(), 110: PF.front_relaxed()}
    set_backend_for_tests(FakeBackend(fixtures), key="heavy")
    set_scorer_for_tests(ColorScorer())
    try:
        root = tmp_path / "sl"
        for i, tag in enumerate(fixtures):
            save_image(root / f"2024-0{i + 1}-01 {tag}.png", _painted(tag, tag in SLEEVED))
        _wait(client, client.post("/api/imports/folder", json={"path": str(root)}).json()["job"]["id"])
        _wait(client, client.post("/api/landmarks/extract").json()["id"])
        j = _wait(client, client.post("/api/sleeves/detect", json={}).json()["id"])
        assert j["status"] == "done", j
        assert j["result"]["found"] == 1
        assert client.get("/api/sleeves/summary").json() == {"sleeves": 1, "checked": 3, "unchecked": 0}
        assert _wait(client, client.post("/api/sleeves/detect", json={}).json()["id"])["result"]["checked"] == 0

        assert _wait(client, client.post("/api/poses/classify", json={}).json()["id"])["status"] == "done"
        photos = {p["original_filename"]: p for p in client.get("/api/photos").json()["items"]}
        sleeved = photos["2024-02-01 60.png"]
        assert sleeved["sleeves"] is True and sleeved["sleeves_source"] == "auto"
        assert client.get("/api/photos", params={"sleeves": "hide"}).json()["total"] == 2
        assert client.get("/api/photos", params={"sleeves": "only"}).json()["total"] == 1

        # Viewer frames carry the flag
        cid = sleeved["pose_category_id"]
        frames = client.get(f"/api/poses/{cid}/sequence").json()["frames"]
        assert {f["id"]: f["sleeves"] for f in frames}[sleeved["id"]] is True

        # export with hide_sleeves leaves it out
        client.post(f"/api/poses/{cid}/reference", json={"photo_id": photos["2024-01-01 10.png"]["id"]})
        assert _wait(client, client.post(f"/api/poses/{cid}/align").json()["id"])["status"] == "done"
        r = client.post("/api/exports", json={"pose_id": cid, "format": "gif", "fps": 2, "width": 200, "hide_sleeves": True})
        j = _wait(client, r.json()["job"]["id"])
        assert j["status"] == "done", j
        assert j["result"].get("frames") == 2, j["result"]

        # manual override wins over a forced re-check; reset goes back to automatic
        p = client.patch(f"/api/photos/{sleeved['id']}", json={"sleeves": False}).json()
        assert p["sleeves"] is False and p["sleeves_source"] == "manual"
        _wait(client, client.post("/api/sleeves/detect", json={"force": True}).json()["id"])
        assert client.get(f"/api/photos/{sleeved['id']}").json()["sleeves"] is False
        p = client.patch(f"/api/photos/{sleeved['id']}", json={"sleeves_auto": True}).json()
        assert p["sleeves"] is True and p["sleeves_source"] == "auto"
    finally:
        set_backend_for_tests(None, key=None)
        set_scorer_for_tests(None)
