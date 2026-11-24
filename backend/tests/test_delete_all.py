"""Settings → Delete all photos."""

from __future__ import annotations

import time

from .conftest import image_bytes, make_image


def _wait(client, jid, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _import(client, seeds):
    sess = client.post("/api/imports/upload-session").json()
    files = [("files", (f"p{s}.jpg", image_bytes(make_image(seed=s)), "image/jpeg")) for s in seeds]
    client.post(f"/api/imports/upload-session/{sess['id']}/files", files=files)
    return _wait(client, client.post(f"/api/imports/upload-session/{sess['id']}/commit").json()["job"]["id"])


def test_delete_all_photos_needs_confirmation_and_can_reimport(client, data_dir):
    from app import config

    assert _import(client, [1, 2, 3])["result"]["counts"]["accepted"] == 3
    assert client.get("/api/photos").json()["total"] == 3
    assert client.post("/api/photos/delete-all", json={"confirm": "yes"}).status_code == 422
    j = _wait(client, client.post("/api/photos/delete-all", json={"confirm": "DELETE"}).json()["id"])
    assert j["status"] == "done" and j["result"]["deleted"] == 3
    assert client.get("/api/photos").json()["total"] == 0
    assert len(list(config.TRASH_DIR.iterdir())) == 3  # the library's copies, recoverable
    assert not any(config.THUMBS_DIR.iterdir())
    # forgotten, so the same files import again
    assert _import(client, [1, 2, 3])["result"]["counts"]["accepted"] == 3
