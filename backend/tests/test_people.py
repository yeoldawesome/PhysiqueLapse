"""Filter people: matching faces against the photos marked "me", labels, deleting."""

import numpy as np

from tests.conftest import make_image, save_image


def _import(client, tmp_path, n):
    from tests.test_pose import _wait

    root = tmp_path / "ppl"
    for i in range(n):
        save_image(root / f"{i}.png", make_image(300, 400, seed=500 + i))
    j = _wait(client, client.post("/api/imports/folder", json={"path": str(root)}).json()["job"]["id"])
    assert j["status"] == "done", j
    return sorted(p["id"] for p in client.get("/api/photos", params={"page_size": 100}).json()["items"])


def _unit(v):
    v = np.asarray(v, dtype=float)
    return list(v / np.linalg.norm(v))


def test_people_matching_labels_and_delete(client, tmp_path):
    from sqlmodel import Session

    from app.db import engine
    from app.models import PersonFace

    ids = _import(client, tmp_path, 9)
    rng = np.random.default_rng(1)
    me, other = rng.normal(size=128), rng.normal(size=128)
    feats = {}
    for pid in ids[:6]:  # 6 photos of "me" (small variations)
        feats[pid] = _unit(me + rng.normal(scale=0.3, size=128))
    feats[ids[6]] = _unit(other + rng.normal(scale=0.3, size=128))  # someone else
    feats[ids[7]] = _unit(other + rng.normal(scale=0.3, size=128))  # same someone else
    with Session(engine()) as s:  # ids[8]: scanned, no usable face
        for pid in ids:
            s.add(PersonFace(photo_id=pid, n_faces=1 if pid in feats else 0, feature=feats.get(pid, [])))
        s.commit()

    # nothing marked yet: everything with a face is "not sure", the hidden one is "no face"
    from app.services import people as P

    with Session(engine()) as s:
        sm = P.recompute(s)
    assert sm["unsure"] == 8 and sm["no_face"] == 1 and sm["me_examples"] == 0
    sugg = [x["id"] for x in client.get("/api/people/suggestions").json()]
    assert set(sugg[:6]) == set(ids[:6])  # the most common face comes first

    sm = client.post("/api/people/label", json={"ids": ids[:3], "label": "me"}).json()
    # a face unlike yours is only "doesn't look like you" (unsure), never "not you" by itself
    assert sm["me_examples"] == 3 and sm["me"] == 6 and sm["unsure"] == 2 and sm["not_me"] == 0 and sm["no_face"] == 1
    unsure = client.get("/api/people/list", params={"status": "unsure"}).json()
    assert {x["id"] for x in unsure} == {ids[6], ids[7]} and all(x["score"] < P.ME_SIM for x in unsure)
    # mark one stranger "not me": their other photo is found automatically
    sm = client.post("/api/people/label", json={"ids": [ids[6]], "label": "not_me"}).json()
    assert sm["not_me"] == 2 and sm["unsure"] == 0
    not_me = client.get("/api/people/list", params={"status": "not_me"}).json()
    assert {x["id"] for x in not_me} == {ids[6], ids[7]}
    # the library filter sees the same thing
    assert client.get("/api/photos", params={"person": "not_me"}).json()["total"] == 2
    # a manual label wins over the face match, and can be undone
    client.post("/api/people/label", json={"ids": [ids[7]], "label": "me"})
    assert client.get(f"/api/photos/{ids[7]}").json()["person_status"] == "me"
    client.post("/api/people/label", json={"ids": [ids[7]], "label": None})
    assert client.get(f"/api/photos/{ids[7]}").json()["person_status"] == "not_me"

    # delete all not-you: rows and face rows go, the rest stay
    r = client.post("/api/photos/bulk", json={"ids": [x["id"] for x in not_me], "delete": True}).json()
    assert r["updated"] == 2
    sm = client.get("/api/people/summary").json()
    assert sm["not_me"] == 0 and sm["total"] == 7
    with Session(engine()) as s:
        assert s.get(PersonFace, ids[6]) is None
    assert client.get("/api/dashboard").json()["people"]["me"] == 6


def test_scan_needs_the_face_models(client):
    # the test data folder has no models: the scan is refused with a clear message
    r = client.post("/api/people/scan", json={})
    assert r.status_code == 409 and "download_models" in r.json()["detail"]
    assert client.get("/api/people/summary").json()["models"] == 0
