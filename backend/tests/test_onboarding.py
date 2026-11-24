"""First run: tutorial flag, classification held back until the user has shown their poses,
candidates ranked per pose, skipping, finish -> classify + align."""

from __future__ import annotations

import time

from app.services.pose_backend import set_backend_for_tests
from tests import pose_fixtures as PF
from tests.conftest import save_image
from tests.test_pose import FakeBackend, _tagged_image, _wait


def _until(client, pred, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get("/api/onboarding").json()
        if pred(st):
            return st
        time.sleep(0.05)
    raise AssertionError(f"timeout: {st}")


def test_tutorial_flag(client):
    st = client.get("/api/onboarding").json()
    assert st["stage"] == "import" and st["show_tutorial"] and not st["pose_setup_done"]
    st = client.patch("/api/onboarding", json={"tutorial_seen": True}).json()
    assert not st["show_tutorial"] and st["tutorial_seen"]


def test_pose_setup_flow(client, tmp_path):
    fixtures = {10: PF.front_double_bicep(), 20: PF.mirrored(PF.front_double_bicep()), 60: PF.front_relaxed(), 110: PF.side_left(), 160: PF.no_person()}
    set_backend_for_tests(FakeBackend(fixtures), key="heavy")
    try:
        root = tmp_path / "poses"
        for tag in fixtures:
            save_image(root / f"{tag}.png", _tagged_image(tag))
        j = _wait(client, client.post("/api/imports/folder", json={"path": str(root)}).json()["job"]["id"])
        assert j["result"]["counts"]["accepted"] == 5
        # the import only prepares: joints + pose features, nothing is sorted yet
        st = _until(client, lambda s: s["stage"] == "poses")
        assert st["physique_photos"] == 4 and st["landmarks_pending"] == 0  # the photo without a person is left out
        photos = {p["original_filename"]: p for p in client.get("/api/photos").json()["items"]}
        assert all(p["pose_category_id"] is None for p in photos.values())

        poses = client.get("/api/onboarding/poses").json()
        by_key = {p["rule_key"]: p for p in poses}
        assert "other" not in by_key
        assert by_key["side_left"]["mirror_id"] == by_key["side_right"]["id"]

        fdb = by_key["front_double_bicep"]["id"]
        cands = client.get(f"/api/onboarding/poses/{fdb}/candidates").json()
        assert len(cands) == 4
        # the two double-bicep photos rank first
        assert {c["photo_id"] for c in cands[:2]} == {photos["10.png"]["id"], photos["20.png"]["id"]}

        # pick one as an example (same call as setting a pose by hand)
        client.patch(f"/api/photos/{photos['10.png']['id']}", json={"pose_category_id": fdb})
        assert client.get("/api/onboarding").json()["stage"] == "poses"  # picking doesn't end the step
        cands = client.get(f"/api/onboarding/poses/{fdb}/candidates").json()
        assert cands[0]["photo_id"] == photos["10.png"]["id"] and cands[0]["picked"]
        relaxed = by_key["front_relaxed"]["id"]
        client.patch(f"/api/photos/{photos['60.png']['id']}", json={"pose_category_id": relaxed})
        # a photo picked for one pose is not offered for another
        assert photos["10.png"]["id"] not in {c["photo_id"] for c in client.get(f"/api/onboarding/poses/{relaxed}/candidates").json()}

        # skip every other pose, finish: classify + align run
        skipped = [p["id"] for p in poses if p["id"] not in (fdb, relaxed)]
        r = client.post("/api/onboarding/finish", json={"skipped": skipped}).json()
        assert r["status"]["pose_setup_done"] and r["status"]["skipped"] == skipped and r["status"]["stage"] == "done"
        _wait(client, r["job"]["id"])
        photos = {p["original_filename"]: p for p in client.get("/api/photos").json()["items"]}
        assert photos["20.png"]["pose_category_id"] == fdb  # matched to the user's example
        assert photos["10.png"]["pose_source"] == "manual"
    finally:
        set_backend_for_tests(None, key=None)


def test_upload_holds_classification_until_setup(client, data_dir):
    from app.db import session_scope
    from app.services.ingest import processing_chain
    from app.services.onboarding import pose_setup_done
    from app.settings import save_settings

    with session_scope() as s:
        assert not pose_setup_done(s)
        save_settings(s, {"pose_setup_done": True})
        assert pose_setup_done(s)
    assert [c["type"] for c in processing_chain([1])] == ["extract_landmarks", "classify_poses", "align_photos"]
