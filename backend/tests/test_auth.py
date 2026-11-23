"""Server mode login: every /api route needs a session; desktop mode needs none."""

from __future__ import annotations

import re

import pytest

from .conftest import TEST_PASSWORD, image_bytes, make_image


def _api_routes(app):
    for path, ops in app.openapi()["paths"].items():
        if path.startswith("/api"):
            for m in ops:
                yield m.upper(), re.sub(r"\{[^}:]+(:path)?\}", "1", path)


def test_every_api_route_needs_login(server_client):
    from app.auth import PUBLIC_API

    checked = 0
    for method, path in _api_routes(server_client.app):
        if path in PUBLIC_API:
            continue
        r = server_client.request(method, path)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
        checked += 1
    assert checked > 50  # sanity: the whole API was walked, media routes included


def test_media_routes_need_login(server_client):
    for path in ("/api/photos/1/thumb", "/api/photos/1/preview", "/api/photos/1/original", "/api/photos/1/aligned", "/api/docs", "/api/openapi.json"):
        assert server_client.get(path).status_code == 401, path


def test_login_logout_flow(server_client):
    c = server_client
    assert c.get("/api/auth/status").json() == {"mode": "server", "auth_required": True, "password_set": True, "logged_in": False}
    assert c.get("/api/dashboard").status_code == 401
    r = c.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "pl_session=" in cookie and "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert c.get("/api/dashboard").status_code == 200
    assert c.get("/api/auth/status").json()["logged_in"] is True
    assert c.post("/api/auth/logout").status_code == 200
    c.cookies.clear()
    assert c.get("/api/dashboard").status_code == 401


def test_logged_out_token_is_dead(server_client):
    c = server_client
    c.post("/api/auth/login", json={"password": TEST_PASSWORD})
    token = c.cookies.get("pl_session")
    c.post("/api/auth/logout")
    c.cookies.clear()
    assert c.get("/api/dashboard", cookies={"pl_session": token}).status_code == 401


def test_wrong_password_and_rate_limit(server_client):
    c = server_client
    for _ in range(5):
        assert c.post("/api/auth/login", json={"password": "nope nope nope"}).status_code == 401
    r = c.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert r.status_code == 429  # locked out, even with the right password
    assert int(r.headers["retry-after"]) > 0


def test_forged_cookie_refused(server_client):
    assert server_client.get("/api/dashboard", cookies={"pl_session": "made-up"}).status_code == 401


def test_cross_origin_post_refused(server_client):
    c = server_client
    c.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert c.put("/api/settings", json={"units": "kg"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/auth/login", json={"password": TEST_PASSWORD}, headers={"Origin": "null"}).status_code == 403
    assert c.put("/api/settings", json={"units": "kg"}, headers={"Origin": "https://testserver"}).status_code == 200
    # behind tailscale serve the public host arrives as X-Forwarded-Host
    r = c.put(
        "/api/settings", json={"units": "lb"},
        headers={"Origin": "https://box.tail1.ts.net:8443", "X-Forwarded-Host": "box.tail1.ts.net:8443"},
    )
    assert r.status_code == 200


def test_settings_never_return_the_hash(server_client):
    c = server_client
    c.post("/api/auth/login", json={"password": TEST_PASSWORD})
    body = c.get("/api/settings").text + c.get("/api/system").text
    assert "argon2" not in body and "password" not in body


def test_new_password_logs_everyone_out(server_client):
    from sqlmodel import Session

    from app import auth
    from app.db import engine

    c = server_client
    c.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert c.get("/api/dashboard").status_code == 200
    with Session(engine()) as s:
        auth.set_password(s, "another long password")
    assert c.get("/api/dashboard").status_code == 401


def test_no_password_set_gives_setup_hint(server_client):
    from sqlmodel import Session, delete

    from app.db import engine
    from app.models import AuthState

    with Session(engine()) as s:
        s.exec(delete(AuthState))
        s.commit()
    r = server_client.post("/api/auth/login", json={"password": "anything at all"})
    assert r.status_code == 409 and "set-password" in r.json()["detail"]


def test_short_password_rejected(data_dir):
    from sqlmodel import Session

    from app import auth
    from app.db import engine

    with Session(engine()) as s, pytest.raises(ValueError):
        auth.set_password(s, "short")


def test_security_headers_in_server_mode(server_client):
    r = server_client.get("/api/health")
    assert r.status_code == 200
    assert "connect-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_upload_after_login(server_client):
    c = server_client
    c.post("/api/auth/login", json={"password": TEST_PASSWORD})
    sess = c.post("/api/imports/upload-session").json()
    r = c.post(
        f"/api/imports/upload-session/{sess['id']}/files",
        files=[("files", ("a.jpg", image_bytes(make_image(seed=3)), "image/jpeg"))],
    )
    assert r.status_code == 200 and r.json()["staged"] == 1


def test_desktop_mode_needs_no_login(data_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config, jobs
    from app.main import create_app

    monkeypatch.setattr(config, "SERVER", False)
    monkeypatch.setattr(config, "MODE", "desktop")
    jobs._runner = None
    with TestClient(create_app()) as c:
        assert c.get("/api/dashboard").status_code == 200
        assert c.get("/api/auth/status").json()["auth_required"] is False
        assert "content-security-policy" not in c.get("/api/health").headers
    jobs._runner = None


def test_cli_set_password(data_dir, monkeypatch, capsys):
    import io

    from sqlmodel import Session

    from app import auth
    from app.db import engine

    monkeypatch.setattr("sys.stdin", io.StringIO("a very good password\n"))
    assert auth._cli(["set-password", "--stdin"]) == 0
    with Session(engine()) as s:
        assert auth.verify_password(s, "a very good password")
        assert not auth.verify_password(s, "wrong password here")
    assert auth._cli(["status"]) == 0
    assert "password_set=True" in capsys.readouterr().out


def test_migration_from_v8_adds_auth_tables_and_backs_up(tmp_path):
    """An existing v8 library upgrades in place and keeps a copy of the old database."""
    import sqlite3

    from app import db

    d = tmp_path / "lib"
    d.mkdir()
    db.reset_engine_for_tests(d)
    db.init_db()
    # pretend it's a v8 database from before server mode
    con = sqlite3.connect(d / "physiquelapse.db")
    con.execute("DROP TABLE auth_state")
    con.execute("DROP TABLE auth_sessions")
    con.execute("""UPDATE settings SET value='{"v": 8}' WHERE key='schema_version'""")
    con.commit()
    con.close()
    db.reset_engine_for_tests(d)
    db.init_db()
    con = sqlite3.connect(d / "physiquelapse.db")
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    version = con.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()[0]
    con.close()
    assert {"auth_state", "auth_sessions"} <= tables
    assert '"v": 9' in version or '"v":9' in version
    assert (d / "physiquelapse.before-v9.db").exists()
    db.reset_engine_for_tests(d)
