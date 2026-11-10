"""Abs group: detection (mocked CLIP), manual override, close-ups, sequence, export."""

import numpy as np
from PIL import Image

from app.services import abs_view as AV
from app.services import landmarks as L
from app.services.clip_backend import set_scorer_for_tests
from app.services.pose_backend import set_backend_for_tests
from tests import pose_fixtures as PF
from tests.conftest import save_image
from tests.test_pose import FakeBackend, _tagged_image, _wait

SHIRTLESS = {10, 60}  # image tags whose torso crop the fake CLIP calls "abs visible"


class TorsoScorer:
    """Reads the tag back from the original image via a per-crop lookup table filled by the test."""

    def __init__(self):
        self.calls = 0

    def positive_probability(self, images, positive, negative):
        self.calls += 1
        # crops are taken from tagged images whose torso region we painted with the tag value
        return [0.95 if int(np.asarray(im.convert("RGB"))[im.height // 2, im.width // 2, 1]) > 200 else 0.05 for im in images]


def _torso_painted(tag: int, shirtless: bool):
    img = _tagged_image(tag, 800, 1000)
    if shirtless:  # bright green torso block = "abs visible" for the fake scorer
        arr = np.asarray(img).copy()
        arr[420:700, 320:480] = (0, 255, 0)
        img = Image.fromarray(arr)
    return img


def test_closeup_transform_puts_torso_in_fixed_spots():
    pts = L.points_dict(PF.front_relaxed())
    tr = AV.closeup_transform(pts)
    sm = (AV._xy(pts["left_shoulder"]) + AV._xy(pts["right_shoulder"])) / 2
    hm = (AV._xy(pts["left_hip"]) + AV._xy(pts["right_hip"])) / 2
    assert np.allclose(tr.apply(sm), [AV.CANVAS / 2, AV.SHOULDER_Y * AV.CANVAS], atol=1e-6)
    assert np.allclose(tr.apply(hm), [AV.CANVAS / 2, AV.HIP_Y * AV.CANVAS], atol=1e-6)


def test_detect_override_sequence_and_export(client, tmp_path):
    fixtures = {10: PF.front_relaxed(), 60: PF.front_double_bicep(), 110: PF.front_relaxed()}
    set_backend_for_tests(FakeBackend(fixtures), key="heavy")
    scorer = TorsoScorer()
    set_scorer_for_tests(scorer)
    try:
        root = tmp_path / "abs"
        for i, tag in enumerate(fixtures):
            save_image(root / f"2024-0{i + 1}-01 {tag}.png", _torso_painted(tag, tag in SHIRTLESS))
        _wait(client, client.post("/api/imports/folder", json={"path": str(root)}).json()["job"]["id"])
        assert _wait(client, client.post("/api/landmarks/extract").json()["id"])["status"] == "done"

        j = _wait(client, client.post("/api/abs/detect", json={}).json()["id"])
        assert j["status"] == "done", j
        assert j["result"]["found"] == 2
        assert client.get("/api/abs/summary").json() == {"visible": 2, "checked": 3, "unchecked": 0}
        # a second run has nothing to do
        assert _wait(client, client.post("/api/abs/detect", json={}).json()["id"])["result"]["checked"] == 0

        photos = {p["original_filename"]: p for p in client.get("/api/photos").json()["items"]}
        covered = photos["2024-03-01 110.png"]
        assert covered["abs_visible"] is False and covered["abs_source"] == "auto"
        assert client.get("/api/photos", params={"abs": "visible"}).json()["total"] == 2

        seq = client.get("/api/abs/sequence").json()
        assert seq["canvas_w"] == AV.CANVAS and [f["days_since_start"] for f in seq["frames"]] == [0, 31]
        r = client.get(seq["frames"][0]["aligned_url"])
        assert r.status_code == 200 and Image.open(__import__("io").BytesIO(r.content)).size == (AV.CANVAS, AV.CANVAS)

        # manual override wins over detection and survives a forced re-check
        p = client.patch(f"/api/photos/{covered['id']}", json={"abs_visible": True}).json()
        assert p["abs_visible"] is True and p["abs_source"] == "manual"
        _wait(client, client.post("/api/abs/detect", json={"force": True}).json()["id"])
        assert client.get(f"/api/photos/{covered['id']}").json()["abs_visible"] is True
        assert len(client.get("/api/abs/sequence").json()["frames"]) == 3
        # reset to automatic
        p = client.patch(f"/api/photos/{covered['id']}", json={"abs_auto": True}).json()
        assert p["abs_visible"] is False and p["abs_source"] == "auto"

        r = client.post("/api/exports", json={"group": "abs", "format": "gif", "fps": 2, "width": 270})
        assert r.status_code == 200, r.text
        j = _wait(client, r.json()["job"]["id"])
        assert j["status"] == "done", j
        assert j["result"]["frames"] == 2 and "Abs" in j["result"]["path"]
        assert client.post("/api/exports", json={"format": "gif"}).status_code == 404  # neither pose nor group
    finally:
        set_backend_for_tests(None, key=None)
        set_scorer_for_tests(None)


def test_classify_also_checks_abs_and_dashboard_lists_it(client, tmp_path):
    """Abs sits with the poses: classifying queues the abs check, and the dashboard reports the count."""
    import time

    fixtures = {10: PF.front_relaxed(), 110: PF.front_relaxed()}
    set_backend_for_tests(FakeBackend(fixtures), key="heavy")
    set_scorer_for_tests(TorsoScorer())
    try:
        root = tmp_path / "abs2"
        for i, tag in enumerate(fixtures):
            save_image(root / f"2024-0{i + 1}-01 {tag}.png", _torso_painted(tag, tag in SHIRTLESS))
        _wait(client, client.post("/api/imports/folder", json={"path": str(root)}).json()["job"]["id"])
        _wait(client, client.post("/api/landmarks/extract").json()["id"])
        assert _wait(client, client.post("/api/poses/classify", json={}).json()["id"])["status"] == "done"
        deadline = time.time() + 20
        while client.get("/api/abs/summary").json()["checked"] < 2 and time.time() < deadline:
            time.sleep(0.1)
        assert client.get("/api/dashboard").json()["abs"] == {"visible": 1, "checked": 2, "unchecked": 0}
    finally:
        set_backend_for_tests(None, key=None)
        set_scorer_for_tests(None)


def test_detect_skips_photos_deleted_mid_job(client, tmp_path):
    """A photo deleted while the check runs is skipped instead of failing the whole job."""
    from sqlmodel import Session

    from app.db import engine
    from app.models import Photo
    from app.services import abs_view, sleeves

    fixtures = {10: PF.front_relaxed(), 60: PF.front_double_bicep()}
    set_backend_for_tests(FakeBackend(fixtures), key="heavy")
    set_scorer_for_tests(TorsoScorer())
    try:
        for i, tag in enumerate(fixtures):
            save_image(tmp_path / "d" / f"2024-0{i + 1}-01 {tag}.png", _torso_painted(tag, True))
        _wait(client, client.post("/api/imports/folder", json={"path": str(tmp_path / "d")}).json()["job"]["id"])
        _wait(client, client.post("/api/landmarks/extract").json()["id"])
        ids = sorted(p["id"] for p in client.get("/api/photos").json()["items"])
        assert len(ids) == 2

        class Ctx:
            """Deletes the first photo at the job's first progress report (after it listed its work)."""

            def __init__(self, victim):
                self.victim = victim

            def session(self):
                return Session(engine())

            def progress(self, *a, **k):
                if self.victim is not None:
                    from app.services import ingest

                    with Session(engine()) as s:
                        ingest.delete_photo(s, s.get(Photo, self.victim))
                        s.commit()
                    self.victim = None

            def check_cancelled(self):
                pass

        r = abs_view.job_detect_abs(Ctx(ids[0]), {"ids": ids})
        assert r["checked"] == 2 and r["found"] == 1  # listed 2, one vanished, the other was scored
        r = sleeves.job_detect_sleeves(Ctx(None), {"ids": ids})
        assert r["checked"] == 1  # the deleted photo is no longer listed
    finally:
        set_backend_for_tests(None, key=None)
        set_scorer_for_tests(None)
