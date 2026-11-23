"""Shared fixtures: a fresh data dir + database per test, synthetic images, API client."""

from __future__ import annotations

import io
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

os.environ["PL_AUTO_DOWNLOAD_MODELS"] = "0"  # server-mode startup must not fetch models in tests

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from app import db

    d = tmp_path / "data"
    d.mkdir()
    db.reset_engine_for_tests(d)
    db.init_db()
    yield d
    db.reset_engine_for_tests(d)


@pytest.fixture()
def session(data_dir):
    from sqlmodel import Session

    from app.db import engine

    with Session(engine()) as s:
        yield s


TEST_PASSWORD = "correct horse battery"
TEST_EMAIL = "owner@example.com"


def make_account(email: str = TEST_EMAIL, password: str = TEST_PASSWORD, *, admin: bool = False):
    from app import accounts

    return accounts.create_user(email, password, email.split("@")[0], is_admin=admin)


def login(c, email: str = TEST_EMAIL, password: str = TEST_PASSWORD) -> None:
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


@pytest.fixture()
def client(data_dir, tmp_path, monkeypatch):
    """API client. With PL_MODE=server (CI runs the suite in both modes) it signs in to an admin
    account first, so every existing test also runs behind the login, in that account's own
    library (which the test itself then uses too)."""
    from fastapi.testclient import TestClient

    from app import auth, config, jobs, tenancy
    from app.main import create_app

    jobs._runner = None
    auth.reset_limiters()
    app = create_app()
    # https: the session cookie is Secure
    with TestClient(app, base_url="https://testserver") as c:
        if config.SERVER:
            u = make_account(admin=True)
            login(c)
            monkeypatch.setattr(tenancy, "_default", tenancy.library_for_user(u.id))
            # path imports are limited to PL_IMPORT_ROOTS in server mode: allow the test's folder
            monkeypatch.setattr(config, "IMPORT_ROOTS", [tmp_path.resolve()])
        yield c
    jobs._runner = None


@pytest.fixture()
def server_client(data_dir, monkeypatch):
    """A client in server mode with one admin account (TEST_EMAIL / TEST_PASSWORD), NOT signed in,
    and no default library (as on the real server)."""
    from fastapi.testclient import TestClient

    from app import auth, config, jobs, tenancy
    from app.main import create_app

    monkeypatch.setattr(config, "SERVER", True)
    monkeypatch.setattr(config, "MODE", "server")
    jobs._runner = None
    auth.reset_limiters()
    make_account(admin=True)
    monkeypatch.setattr(tenancy, "_default", None)
    app = create_app()
    with TestClient(app, base_url="https://testserver") as c:
        yield c
    jobs._runner = None


# ----------------------------------------------------------------------------- image helpers


def make_image(w: int = 320, h: int = 400, seed: int = 0, mode: str = "RGB") -> Image.Image:
    """A deterministic gradient image with a seed-dependent pattern (distinct phash per seed)."""
    import numpy as np

    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    base = np.zeros((h, w, 3), dtype=np.float64)
    base[..., 0] = (x / w) * 255
    base[..., 1] = (y / h) * 255
    base[..., 2] = ((x + y) / (w + h)) * 255
    # a few random rectangles so hashes differ by seed
    for _ in range(6):
        x0, y0 = int(rng.integers(0, w // 2)), int(rng.integers(0, h // 2))
        x1, y1 = x0 + int(rng.integers(w // 8, w // 2)), y0 + int(rng.integers(h // 8, h // 2))
        base[y0:y1, x0:x1] = rng.integers(0, 255, size=3)
    img = Image.fromarray(base.clip(0, 255).astype("uint8"), "RGB")
    return img.convert(mode) if mode != "RGB" else img


def save_image(path: Path, img: Image.Image, *, exif_dt: datetime | None = None, orientation: int | None = None, fmt: str | None = None) -> Path:
    fmt = fmt or {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "heic": "HEIF"}[path.suffix.lower().lstrip(".")]
    kwargs = {}
    if exif_dt is not None or orientation is not None:
        exif = Image.Exif()
        if orientation is not None:
            exif[0x0112] = orientation
        if exif_dt is not None:
            s = exif_dt.strftime("%Y:%m:%d %H:%M:%S")
            exif[0x0132] = s
            ifd = exif.get_ifd(0x8769)
            ifd[0x9003] = s
            ifd[0x9004] = s
        kwargs["exif"] = exif.tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "HEIF":
        import pillow_heif

        pillow_heif.register_heif_opener()
        img.save(path, format="HEIF", quality=90, **kwargs)
    else:
        img.save(path, format=fmt, **kwargs)
    return path


def image_bytes(img: Image.Image, fmt: str = "JPEG") -> bytes:
    b = io.BytesIO()
    img.save(b, format=fmt)
    return b.getvalue()
