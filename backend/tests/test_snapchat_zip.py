"""Snapchat zips: upload + add-by-path, selective unpack (photos only, nested zips), filter, cleanup."""

import io
import zipfile
from pathlib import Path

from app.services.clip_backend import set_scorer_for_tests
from app.services.pose_backend import set_backend_for_tests
from tests.test_snapchat import FakeScorer, NoPeople, _wait, make_export


def _zip_dir(src: Path, dest: Path, members: list[str]) -> Path:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for m in members:
            z.write(src / m, m)
    return dest


def build_zips(tmp: Path) -> tuple[Path, Path]:
    exp = make_export(tmp / "exp")
    mem = sorted(p.name for p in (exp / "memories").iterdir())
    first = ["json/memories_history.json"] + [f"memories/{n}" for n in mem if n.startswith(("2024-03-15", "2024-03-16", "2024-03-18"))]
    rest = [f"memories/{n}" for n in mem if n.startswith(("2024-03-17", "2024-03-19"))]
    z1 = _zip_dir(exp, tmp / "mydata~1.zip", first)
    z2 = _zip_dir(exp, tmp / "mydata~1-2.zip", rest)
    # a nested per-memory zip (main + overlay) inside the second zip
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zi:
        zi.write(exp / "memories" / "2024-03-20_fff-main.jpg", "2024-03-20_fff-main.jpg")
        zi.writestr("2024-03-20_fff-overlay.png", b"png")
    with zipfile.ZipFile(z2, "a") as z:
        z.writestr("memories/2024-03-20_fff.zip", inner.getvalue())
    return z1, z2


def test_zip_upload_and_path_import(client, tmp_path):
    z1, z2 = build_zips(tmp_path)
    set_scorer_for_tests(FakeScorer())
    set_backend_for_tests(NoPeople(), key="heavy")
    try:
        client.put("/api/settings", json={"timezone": "UTC", "filter_accept": 0.6})
        sess = client.post("/api/snapchat/zip-session").json()
        sid = sess["id"]
        assert client.post("/api/snapchat/zip-session").json()["id"] == sid  # open session is reused

        # browser upload (raw body) of the first zip
        r = client.put(f"/api/snapchat/zip-session/{sid}/upload", params={"name": z1.name}, content=z1.read_bytes(),
                       headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 200, r.text
        assert r.json()["has_metadata"] and r.json()["photos"] == 2 and r.json()["videos"] == 1
        # second zip by path (folder containing it)
        only2 = tmp_path / "zips2"
        only2.mkdir()
        z2.rename(only2 / z2.name)
        r = client.post(f"/api/snapchat/zip-session/{sid}/add-path", json={"path": str(only2)})
        assert r.status_code == 200, r.text
        assert len(r.json()["zips"]) == 2
        # junk is refused
        bad = client.put(f"/api/snapchat/zip-session/{sid}/upload", params={"name": "x.zip"}, content=b"not a zip")
        assert bad.status_code == 422

        r = client.post(f"/api/snapchat/zip-session/{sid}/start")
        assert r.status_code == 200, r.text
        j = _wait(client, r.json()["job"]["id"])
        assert j["status"] == "done", j
        c = j["result"]["counts"]
        assert c["images"] == 6  # 5 loose + 1 from the nested zip; overlays and the video were never extracted
        assert c["accepted"] == 1 and c["review"] == 3 and c["rejected"] == 2  # 0.65*0.95 = .6175 >= .6

        out = client.get(f"/api/snapchat/zip-session/{sid}").json()
        assert out["status"] == "done" and all(z["extracted"] for z in out["zips"])
        unpack = out["options"]["unpack_counts"]
        assert unpack["photos_extracted"] == 6 and unpack["overlays_skipped"] == 2 and unpack["videos_skipped"] == 1
        uploaded = next(z for z in out["zips"] if z["uploaded"])
        assert not Path(uploaded["path"]).exists()  # our temporary upload copy is gone
        assert (only2 / z2.name).exists()  # the user's own zip is untouched

        photos = client.get("/api/photos").json()["items"]
        assert len(photos) == 1 and photos[0]["taken_at"] == "2024-03-15T18:22:01"

        # cleanup refuses while photos wait for review, then works with force
        assert client.delete(f"/api/snapchat/zip-session/{sid}/staging").status_code == 409
        r = client.delete(f"/api/snapchat/zip-session/{sid}/staging", params={"force": True})
        assert r.json()["deleted_files"] >= 6 and r.json()["session"]["staging_bytes"] == 0
        # the imported photo survives cleanup (it was copied into the library)
        assert client.get(f"/api/photos/{photos[0]['id']}/original").status_code == 200

        # a new zip session is created after the previous one finished
        assert client.post("/api/snapchat/zip-session").json()["id"] != sid
    finally:
        set_scorer_for_tests(None)
        set_backend_for_tests(None, key=None)


def test_failed_zip_import_can_be_resumed(client, tmp_path):
    z1, _ = build_zips(tmp_path)

    class Broken:
        def positive_probability(self, *a, **k):
            raise RuntimeError("model unavailable")

    set_scorer_for_tests(Broken())
    set_backend_for_tests(NoPeople(), key="heavy")
    try:
        sid = client.post("/api/snapchat/zip-session").json()["id"]
        client.post(f"/api/snapchat/zip-session/{sid}/add-path", json={"path": str(z1)})
        j = _wait(client, client.post(f"/api/snapchat/zip-session/{sid}/start").json()["job"]["id"])
        assert j["status"] == "failed"
        assert client.get(f"/api/snapchat/zip-session/{sid}").json()["status"] == "failed"
        set_scorer_for_tests(FakeScorer())
        r = client.post(f"/api/snapchat/zip-session/{sid}/start")  # resume from the page
        assert r.status_code == 200, r.text
        j = _wait(client, r.json()["job"]["id"])
        assert j["status"] == "done" and j["result"]["counts"]["images"] == 2
    finally:
        set_scorer_for_tests(None)
        set_backend_for_tests(None, key=None)
