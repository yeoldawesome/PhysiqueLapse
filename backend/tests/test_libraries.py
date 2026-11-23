"""One library (folder + database) per account: isolation, jobs, fair queue."""

from __future__ import annotations

import threading
import time

import pytest

from .conftest import image_bytes, make_image


def test_two_libraries_are_separate(data_dir, tmp_path):
    from sqlmodel import Session, select

    from app import config, tenancy
    from app.db import engine
    from app.models import Photo

    a = tenancy.Library(1, data_dir / "libraries" / "1")
    b = tenancy.Library(2, data_dir / "libraries" / "2")
    with tenancy.use_library(a):
        assert config.THUMBS_DIR == a.root / "derived" / "thumbs"
        with Session(engine()) as s:
            s.add(Photo(sha256="aaa", original_path="originals/a.jpg", original_filename="a.jpg", ext="jpg"))
            s.commit()
        assert config.abs_path("originals/a.jpg") == a.root / "originals" / "a.jpg"
        assert config.rel(a.root / "derived" / "x.jpg") == "derived/x.jpg"
    with tenancy.use_library(b):
        assert config.DB_PATH == b.root / "physiquelapse.db"
        with Session(engine()) as s:
            assert s.exec(select(Photo)).all() == []  # B sees nothing of A
    with tenancy.use_library(a):
        with Session(engine()) as s:
            assert [p.sha256 for p in s.exec(select(Photo)).all()] == ["aaa"]
    assert (a.root / "physiquelapse.db").exists() and (b.root / "physiquelapse.db").exists()
    assert {lib.id for lib in tenancy.all_libraries()} >= {1, 2}


def test_no_library_chosen_is_an_error(data_dir):
    from app import config, tenancy

    saved = tenancy.default()
    tenancy.set_default(None)
    try:
        with pytest.raises(tenancy.NoLibrary):
            _ = config.THUMBS_DIR
        assert config.MODELS_DIR  # shared paths still work
    finally:
        tenancy.set_default(saved)


def test_fair_queue_round_robin():
    from pathlib import Path

    from app.jobs import FairQueue
    from app.tenancy import Library

    a, b = Library(1, Path("a")), Library(2, Path("b"))
    q = FairQueue()
    for i in range(1, 4):
        q.put(a, i)  # A queues a big batch first
    q.put(b, 100)
    q.put(b, 101)
    order = [q.get() for _ in range(5)]
    assert [(lib.id, j) for lib, j in order] == [(1, 1), (2, 100), (1, 2), (2, 101), (1, 3)]
    q.put_stop()
    assert q.get() is None


def test_jobs_run_in_the_library_that_queued_them(data_dir, tmp_path):
    from sqlmodel import Session

    from app import config, jobs, tenancy
    from app.db import engine
    from app.models import Job

    seen: list[tuple[int, str]] = []
    done = threading.Event()

    @jobs.job("test_where_am_i", lane="io")
    def _where(ctx, params):
        seen.append((tenancy.current().id, str(config.DB_PATH)))
        if len(seen) == 2:
            done.set()
        return {"message": "ok"}

    a = tenancy.Library(1, data_dir / "libraries" / "1")
    b = tenancy.Library(2, data_dir / "libraries" / "2")
    jobs._runner = None
    r = jobs.runner()
    r.start()
    try:
        with tenancy.use_library(a):
            ja = r.enqueue("test_where_am_i", {"x": 1})
        with tenancy.use_library(b):
            jb = r.enqueue("test_where_am_i", {"x": 1})  # same params, other library: not deduped
        assert done.wait(10)
        assert sorted(seen) == [(1, str(a.root / "physiquelapse.db")), (2, str(b.root / "physiquelapse.db"))]
        for lib, j in ((a, ja), (b, jb)):
            with tenancy.use_library(lib):
                t0 = time.time()
                while time.time() - t0 < 5:
                    with Session(engine()) as s:
                        if s.get(Job, j.id).status == "done":
                            break
                    time.sleep(0.05)
                with Session(engine()) as s:
                    assert s.get(Job, j.id).status == "done"  # each job row lives in its own library
    finally:
        r.stop()
        jobs._runner = None
        jobs._REGISTRY.pop("test_where_am_i", None)


def test_api_works_on_the_default_library(client, data_dir):
    sess = client.post("/api/imports/upload-session").json()
    r = client.post(f"/api/imports/upload-session/{sess['id']}/files", files=[("files", ("a.jpg", image_bytes(make_image(seed=5)), "image/jpeg"))])
    assert r.status_code == 200
    assert (data_dir / "incoming" / str(sess["id"]) / "a.jpg").exists()
