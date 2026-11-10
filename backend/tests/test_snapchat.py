"""M6: synthetic Snapchat export -> scan, metadata matching, scored import, review queue, resume."""

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from app.services import physique_filter as PF
from app.services import snapchat as SC
from app.services.clip_backend import set_scorer_for_tests
from app.services.pose_backend import Point, set_backend_for_tests
from app.settings import AppSettings
from tests.conftest import make_image, save_image

# clip score per file, encoded via the image's top-left blue value
SCORES = {20: 0.95, 60: 0.90, 100: 0.50, 140: 0.10, 180: 0.05, 220: 0.55}


def _tag(img, tag):
    for x in range(32):
        for y in range(32):
            img.putpixel((x, y), (0, 0, tag))
    return img


class FakeScorer:
    def positive_probability(self, images, positive, negative):
        out = []
        for im in images:
            b = int(im.convert("RGB").getpixel((16, 16))[2])  # JPEG shifts values slightly: nearest tag
            tag = min(SCORES, key=lambda t: abs(t - b))
            out.append(SCORES[tag] if abs(tag - b) <= 12 else 0.0)
        return out


class NoPeople:
    name = "none"

    def detect(self, rgb, num_poses=1):
        return []


def make_export(root: Path) -> Path:
    mem = root / "memories"
    entries = [
        {"Date": "2024-03-15 18:22:01 UTC", "Media Type": "Image", "Download Link": "x"},
        {"Date": "2024-03-16 02:10:00 UTC", "Media Type": "Image"},
        {"Date": "2024-03-17 09:00:00 UTC", "Media Type": "Image"},
        {"Date": "2024-03-17 21:00:00 UTC", "Media Type": "Image"},
        {"Date": "2024-03-18 10:00:00 UTC", "Media Type": "Video"},
        {"Date": "2024-03-19 10:00:00 UTC", "Media Type": "Image"},
    ]
    (root / "json").mkdir(parents=True)
    (root / "json" / "memories_history.json").write_text(json.dumps({"Saved Media": entries}))
    files = {
        "2024-03-15_aaa-main.jpg": 20,
        "2024-03-16_bbb-main.jpg": 60,
        "2024-03-17_ccc-main.jpg": 100,  # two images that day -> day-level date only
        "2024-03-17_ddd-main.jpg": 140,
        "2024-03-19_eee-main.jpg": 180,
        "2024-03-20_fff-main.jpg": 220,  # no metadata entry
    }
    for name, tag in files.items():
        save_image(mem / name, _tag(make_image(300, 400, seed=tag), tag))
    save_image(mem / "2024-03-15_aaa-overlay.png", make_image(300, 400, seed=999))
    (mem / "2024-03-18_ggg-main.mp4").write_bytes(b"\x00" * 32)
    return root


def test_pose_score_and_decisions():
    def pose(h, vis=0.9):
        return [Point(n, 100, y, 0, vis, vis) for n, y in zip(PF.CORE, [10, 40, 40, 70, 70, 10 + h, 10 + h], strict=True)]

    assert PF.pose_score([], 1000) == 0
    full = PF.pose_score([pose(400)], 1000)
    assert full == pytest.approx(0.9, abs=0.01)
    assert PF.pose_score([pose(100)], 1000) < full  # small person
    assert PF.pose_score([pose(400), pose(380)], 1000) == pytest.approx(full / 2, abs=0.01)  # group shot
    s = AppSettings()
    assert PF.decide(0.7, s) == "auto_accept" and PF.decide(0.69, s) == "review" and PF.decide(0.29, s) == "auto_reject"
    assert PF.combine(1.0, None, s) == pytest.approx(0.65)


def test_scan_and_metadata_matching(tmp_path):
    root = make_export(tmp_path / "snap")
    res = SC.scan(root)
    assert res.counts() == {"image": 6, "overlay": 1, "video": 1}
    assert res.metadata_file.name == "memories_history.json" and len(res.entries) == 6
    m = SC.match_metadata(res)
    by = {p.name: v for p, v in m.items()}
    assert by["2024-03-15_aaa-main.jpg"] == (datetime(2024, 3, 15, 18, 22, 1), "high")
    assert by["2024-03-17_ccc-main.jpg"][1] == "medium"
    assert by["2024-03-20_fff-main.jpg"] == (datetime(2024, 3, 20, 12, 0), "medium")


def _wait(client, jid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_import_review_undo_and_resume(client, tmp_path):
    root = make_export(tmp_path / "snap")
    set_scorer_for_tests(FakeScorer())
    set_backend_for_tests(NoPeople(), key="heavy")
    try:
        client.put("/api/settings", json={"timezone": "UTC"})
        scan = client.post("/api/snapchat/scan", json={"path": str(root)}).json()
        assert (scan["images"], scan["videos"], scan["overlays"], scan["matched_metadata"]) == (6, 1, 1, 3)

        r = client.post("/api/snapchat/import", json={"path": str(root)})
        j = _wait(client, r.json()["job"]["id"])
        assert j["status"] == "done", j
        # pose weight 0.35 with no person: score = 0.65 * clip
        # 0.95 -> .6175 review; 0.90 -> .585 review; 0.50 -> .325 review; 0.10/0.05 -> reject; 0.55 -> .3575 review
        c = j["result"]["counts"]
        assert (c["accepted"], c["review"], c["rejected"]) == (0, 4, 2)
        assert client.get("/api/photos").json()["total"] == 0  # nothing copied yet

        # lower the accept threshold and re-run: resume keeps decided items, new import is not duplicated
        q = client.get("/api/review").json()
        assert q["total"] == 4 and [round(x["score"], 3) for x in q["items"]] == [0.618, 0.585, 0.357, 0.325]
        top = q["items"][0]
        assert top["file_name"].endswith("aaa-main.jpg")

        # accept -> copied with the Snapchat timestamp (UTC tz)
        r = client.post(f"/api/review/{top['id']}", json={"decision": "accept"})
        assert r.status_code == 200, r.text
        photos = client.get("/api/photos").json()["items"]
        assert len(photos) == 1 and photos[0]["source"] == "snapchat"
        assert photos[0]["taken_at"] == "2024-03-15T18:22:01" and photos[0]["taken_at_source"] == "snapchat_metadata"

        # reject another, then undo twice (reject, then accept)
        second = q["items"][1]
        client.post(f"/api/review/{second['id']}", json={"decision": "reject"})
        assert client.get("/api/review").json()["total"] == 2
        u = client.post("/api/review/undo").json()
        assert u["id"] == second["id"] and u["decision"] == "review"
        u = client.post("/api/review/undo").json()
        assert u["id"] == top["id"] and u["decision"] == "review"
        assert client.get("/api/photos").json()["total"] == 0

        # accept all above 0.34
        r = client.post("/api/review/bulk", json={"min_score": 0.34, "decision": "accept"}).json()
        assert r["updated"] == 3, r
        assert client.get("/api/photos").json()["total"] == 3
        hist = client.get("/api/review/histogram").json()
        assert hist["total"] == 6 and sum(hist["counts"]) == 6

        # resume / re-run: nothing new is imported, no duplicates
        j = _wait(client, client.post("/api/snapchat/import", json={"path": str(root)}).json()["job"]["id"])
        assert j["status"] == "done"
        assert client.get("/api/photos").json()["total"] == 3
        assert len(client.get("/api/imports").json()) == 1  # same session reused

        # rejected files are never copied, and a plain folder import of the export skips them via the ledger
        j = _wait(client, client.post("/api/imports/folder", json={"path": str(root / "memories")}).json()["job"]["id"])
        cnt = j["result"]["counts"]
        assert cnt.get("skipped_ledger", 0) == 2 and cnt.get("duplicates", 0) == 3
    finally:
        set_scorer_for_tests(None)
        set_backend_for_tests(None, key=None)


@pytest.mark.models
def test_clip_smoke_ordering():
    """Real CLIP weights (pytest -m models): a red image prefers 'red' over 'blue' prompts."""
    from PIL import Image

    from app.services.clip_backend import OpenClipScorer, local_weights

    if local_weights() is None:
        pytest.skip("CLIP weights not downloaded")
    sc = OpenClipScorer()
    red, blue = Image.new("RGB", (224, 224), (220, 20, 20)), Image.new("RGB", (224, 224), (20, 20, 220))
    p = sc.positive_probability([red, blue], ["a plain red square"], ["a plain blue square"])
    assert 0 <= p[1] < 0.5 < p[0] <= 1
