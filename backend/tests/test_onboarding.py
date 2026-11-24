"""First run: automatic, unlabeled grouping after landmark detection."""

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


def test_automatic_grouping_flow(client, tmp_path):
    pose = PF.front_double_bicep()
    fixtures = {10: pose, 20: pose, 30: pose, 160: PF.no_person()}
    set_backend_for_tests(FakeBackend(fixtures), key="heavy")
    try:
        root = tmp_path / "poses"
        for tag in fixtures:
            save_image(root / f"{tag}.png", _tagged_image(tag))
        j = _wait(client, client.post("/api/imports/folder", json={"path": str(root)}).json()["job"]["id"])
        assert j["result"]["counts"]["accepted"] == 4
        st = _until(client, lambda s: s["stage"] == "group")
        assert st["physique_photos"] == 3 and st["landmarks_pending"] == 0
        photos = {p["original_filename"]: p for p in client.get("/api/photos").json()["items"]}
        assert all(p["pose_category_id"] is None for p in photos.values())

        preview = client.get("/api/grouping/preview", params={"threshold": 0.15, "min_size": 2}).json()
        assert preview["photos"] == 3 and [g["name"] for g in preview["groups"]] == ["Group 1"]
        r = client.post("/api/grouping/apply", json={"threshold": 0.15, "min_size": 2}).json()
        _wait(client, r["job"]["id"])
        photos = {p["original_filename"]: p for p in client.get("/api/photos").json()["items"]}
        assert all(photos[f"{tag}.png"]["pose_source"] == "group" for tag in (10, 20, 30))
        categories = client.get("/api/poses").json()
        assert [c["name"] for c in categories if c["photo_count"] > 0] == ["Group 1", "Ungrouped"]
        assert client.get("/api/grouping").json()["active"]
    finally:
        set_backend_for_tests(None, key=None)


def test_upload_processing_chain_uses_groups_after_setup(client, data_dir):
    from app.db import session_scope
    from app.services.ingest import processing_chain
    from app.services.onboarding import pose_setup_done
    from app.settings import save_settings

    with session_scope() as s:
        assert not pose_setup_done(s)
        save_settings(s, {"pose_setup_done": True})
        assert pose_setup_done(s)
    assert [c["type"] for c in processing_chain([1], True)] == ["extract_landmarks", "classify_poses", "align_photos"]
    assert [c["type"] for c in processing_chain([1], False)] == ["extract_landmarks", "group_poses", "align_photos"]
