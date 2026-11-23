"""End-to-end API tests for the M1 skeleton: upload, folder import, library, edits, delete."""

import time
from datetime import datetime

from tests.conftest import image_bytes, make_image, save_image


def wait_job(client, job_id, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_health_and_settings(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    s = client.get("/api/settings").json()
    assert s["canvas_w"] == 1080 and s["units"] == "lb"
    r = client.put("/api/settings", json={"units": "kg", "phash_threshold": 4})
    assert r.status_code == 200 and r.json()["units"] == "kg"
    assert client.get("/api/settings").json()["phash_threshold"] == 4
    assert client.put("/api/settings", json={"phash_threshold": "nope"}).status_code == 422
    cats = client.get("/api/poses").json()
    assert [c["name"] for c in cats][:2] == ["Front Double Bicep", "Back Double Bicep"]
    assert client.get("/api/system").json()["photos"] == 0


def test_upload_flow_with_folder_paths_and_dates(client):
    sess = client.post("/api/imports/upload-session").json()
    files = [
        ("files", ("IMG_20240315_093000.jpg", image_bytes(make_image(seed=1)), "image/jpeg")),
        ("files", ("photo.png", image_bytes(make_image(seed=2), "PNG"), "image/png")),
        ("files", ("photo.webp", image_bytes(make_image(seed=3), "WEBP"), "image/webp")),
        ("files", ("dup.jpg", image_bytes(make_image(seed=1)), "image/jpeg")),  # exact duplicate of the first
        ("files", ("clip.mp4", b"\x00" * 10, "video/mp4")),
    ]
    data = {
        "paths": ["2024/IMG_20240315_093000.jpg", "2024/photo.png", "misc/photo.webp", "zz/dup.jpg", "clip.mp4"],
        "last_modified": [0, int(datetime(2023, 1, 2, 3, 4, 5).timestamp() * 1000), 0, 0, 0],
    }
    r = client.post(f"/api/imports/upload-session/{sess['id']}/files", files=files, data=data)
    assert r.status_code == 200, r.text
    from app import config

    # the server refuses non-images up front; the desktop app stages them and ingest skips them
    assert r.json()["staged"] == (4 if config.SERVER else 5)
    r = client.post(f"/api/imports/upload-session/{sess['id']}/commit")
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["job"]["id"])
    assert job["status"] == "done", job
    counts = job["result"]["counts"]
    assert counts["accepted"] == 3 and counts["duplicates"] == 1 and counts.get("videos_skipped", 0) == (0 if config.SERVER else 1)

    lib = client.get("/api/photos").json()
    assert lib["total"] == 3
    by_name = {p["original_filename"]: p for p in lib["items"]}
    assert by_name["2024/IMG_20240315_093000.jpg"]["taken_at"] == "2024-03-15T09:30:00"
    assert by_name["2024/IMG_20240315_093000.jpg"]["taken_at_source"] == "filename"
    png = by_name["2024/photo.png"]
    assert png["taken_at_source"] == "file_mtime" and png["taken_at"] == "2023-01-02T03:04:05"
    assert png["taken_at_confidence"] == "low"
    # files served
    pid = png["id"]
    assert client.get(f"/api/photos/{pid}/thumb").headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/photos/{pid}/preview").status_code == 200
    assert client.get(f"/api/photos/{pid}/original").headers["content-type"] == "image/png"
    assert client.get(f"/api/photos/{pid}/aligned").status_code == 404
    # import history
    hist = client.get("/api/imports").json()
    assert hist[0]["status"] == "done" and hist[0]["counts"]["accepted"] == 3
    cands = client.get(f"/api/imports/{sess['id']}/candidates", params={"decision": "duplicate"}).json()
    assert cands["total"] == 1 and cands["items"][0]["file_name"] == "zz/dup.jpg"


def test_folder_import_and_bulk_edits(client, tmp_path):
    root = tmp_path / "shots"
    save_image(root / "a" / "2024-01-01.jpg", make_image(seed=11))
    save_image(root / "a" / "2024-01-05.jpg", make_image(seed=12))
    save_image(root / "b" / "IMG_7.jpg", make_image(seed=13), exif_dt=datetime(2024, 2, 1, 8, 0, 0))
    (root / "b" / "readme.txt").write_text("x")
    r = client.post("/api/imports/folder", json={"path": str(root), "recursive": True})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["job"]["id"])
    assert job["status"] == "done", job
    assert job["result"]["counts"] == {
        "files": 4, "images": 3, "accepted": 3, "other_skipped": 1,
    }
    # originals in the source folder are untouched
    assert (root / "a" / "2024-01-01.jpg").exists()

    photos = client.get("/api/photos", params={"sort": "taken_at_asc"}).json()["items"]
    assert [p["taken_at"][:10] for p in photos] == ["2024-01-01", "2024-01-05", "2024-02-01"]
    ids = [p["id"] for p in photos]

    # filters
    assert client.get("/api/photos", params={"date_source": "exif"}).json()["total"] == 1
    assert client.get("/api/photos", params={"from": "2024-01-02", "to": "2024-01-31"}).json()["total"] == 1
    assert client.get("/api/photos", params={"q": "IMG_7"}).json()["total"] == 1
    assert client.get("/api/photos", params={"pose_id": "none"}).json()["total"] == 3

    # single edit: date (manual), notes, exclude
    r = client.patch(f"/api/photos/{ids[0]}", json={"taken_at": "2023-12-31T10:00:00", "notes": "first", "excluded": True})
    assert r.status_code == 200
    p = r.json()
    assert p["taken_at"] == "2023-12-31T10:00:00" and p["taken_at_source"] == "manual" and p["excluded"]
    assert client.get("/api/photos", params={"excluded": "hide"}).json()["total"] == 2

    # bulk shift +2 days
    r = client.post("/api/photos/bulk", json={"ids": ids[1:], "date_shift_days": 2})
    assert r.json()["updated"] == 2
    p2 = client.get(f"/api/photos/{ids[1]}").json()
    assert p2["taken_at"][:10] == "2024-01-07" and p2["taken_at_source"] == "manual"

    # bulk re-derive from filename restores the original date
    client.post("/api/photos/bulk", json={"ids": [ids[1]], "rederive_date_from": "filename"})
    p2 = client.get(f"/api/photos/{ids[1]}").json()
    assert p2["taken_at"][:10] == "2024-01-05" and p2["taken_at_source"] == "filename"

    # re-derive from exif on the EXIF photo
    client.post("/api/photos/bulk", json={"ids": [ids[2]], "rederive_date_from": "exif"})
    assert client.get(f"/api/photos/{ids[2]}").json()["taken_at"] == "2024-02-01T08:00:00"

    # flip toggle
    r = client.patch(f"/api/photos/{ids[0]}", json={"flip_horizontal": True})
    assert r.json()["flip_horizontal"] and r.json()["flip_source"] == "manual"
    client.post("/api/photos/bulk", json={"ids": ids, "toggle_flip": True})
    flips = [client.get(f"/api/photos/{i}").json()["flip_horizontal"] for i in ids]
    assert flips == [False, True, True]

    # pose assignment creates a label and is manual
    cats = client.get("/api/poses").json()
    fdb = next(c for c in cats if c["name"] == "Front Double Bicep")
    r = client.post("/api/photos/bulk", json={"ids": ids[:2], "patch": {"pose_category_id": fdb["id"]}})
    assert r.json()["updated"] == 2
    p0 = client.get(f"/api/photos/{ids[0]}").json()
    assert p0["pose_source"] == "manual" and p0["has_label"]
    cats = client.get("/api/poses").json()
    assert next(c for c in cats if c["id"] == fdb["id"])["photo_count"] == 2
    assert client.get("/api/photos", params={"pose_id": str(fdb["id"])}).json()["total"] == 2

    # reference
    r = client.post(f"/api/poses/{fdb['id']}/reference", json={"photo_id": ids[2]})
    assert r.json()["reference_photo_id"] == ids[2]
    assert client.get(f"/api/photos/{ids[2]}").json()["pose_category_id"] == fdb["id"]

    # delete -> trash, re-import skipped via ledger
    r = client.delete(f"/api/photos/{ids[0]}")
    assert r.status_code == 200
    assert client.get(f"/api/photos/{ids[0]}").status_code == 404
    assert client.get("/api/photos").json()["total"] == 2
    r = client.post("/api/imports/folder", json={"path": str(root / "a"), "recursive": False})
    job = wait_job(client, r.json()["job"]["id"])
    assert job["result"]["counts"]["skipped_ledger"] == 1 and job["result"]["counts"]["duplicates"] == 1

    dash = client.get("/api/dashboard").json()
    assert dash["counts"]["photos"] == 2
    assert client.get("/api/system").json()["photos"] == 2


def test_category_crud(client):
    r = client.post("/api/poses", json={"name": "Most Muscular"})
    assert r.status_code == 200
    cid = r.json()["id"]
    assert client.post("/api/poses", json={"name": "most muscular"}).status_code == 409
    r = client.patch(f"/api/poses/{cid}", json={"rotation_basis": "eyes", "framing_zoom": 1.2})
    assert r.json()["rotation_basis"] == "eyes" and r.json()["framing_zoom"] == 1.2
    assert client.patch(f"/api/poses/{cid}", json={"rotation_basis": "bogus"}).status_code == 422
    other = next(c for c in client.get("/api/poses").json() if c["rule_key"] == "other")
    assert client.delete(f"/api/poses/{other['id']}").status_code == 422
    assert client.delete(f"/api/poses/{cid}").json()["deleted"] == cid


def test_folder_browse(client, tmp_path):
    root = tmp_path / "browse"
    (root / "sub").mkdir(parents=True)
    save_image(root / "x.jpg", make_image())
    r = client.post("/api/imports/browse", json={"path": str(root)})
    assert r.status_code == 200
    body = r.json()
    assert body["dirs"] == ["sub"] and body["images"] == 1
    assert client.post("/api/imports/browse", json={"path": str(tmp_path / "nope")}).status_code == 404


def test_jobs_cancel_and_restart_recovery(client, tmp_path):
    root = tmp_path / "many"
    for i in range(6):
        save_image(root / f"{i}.jpg", make_image(seed=100 + i))
    r = client.post("/api/imports/folder", json={"path": str(root)})
    jid = r.json()["job"]["id"]
    j = wait_job(client, jid)
    assert j["status"] == "done" and j["done"] == 6 and j["total"] == 6
    assert client.post(f"/api/jobs/{jid}/cancel").status_code == 200  # cancelling a finished job is a no-op
    assert client.get("/api/jobs", params={"active": True}).json() == []
    assert len(client.get("/api/jobs").json()) >= 1
