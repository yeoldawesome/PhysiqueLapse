"""Server mode accounts: sign-in, invite sign-up, isolation between accounts, admin, resets.
The desktop app has no login."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta

import pytest

from .conftest import TEST_EMAIL, TEST_PASSWORD, image_bytes, login, make_account, make_image


def _api_routes(app):
    for path, ops in app.openapi()["paths"].items():
        if path.startswith("/api"):
            for m in ops:
                yield m.upper(), re.sub(r"\{[^}:]+(:path)?\}", "1", path)


def _upload_photo(c, seed=1) -> int:
    sid = c.post("/api/imports/upload-session").json()["id"]
    data = image_bytes(make_image(seed=seed))
    it = c.post(f"/api/imports/upload-session/{sid}/files/init", json={"rel_path": f"p{seed}.jpg", "size": len(data)}).json()
    c.put(f"/api/imports/upload-session/{sid}/files/{it['id']}", params={"offset": 0}, content=data)
    jid = c.post(f"/api/imports/upload-session/{sid}/commit").json()["job"]["id"]
    t0 = time.time()
    while time.time() - t0 < 20:
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert j["status"] == "done", j
    return j["result"]["photo_ids"][0]


def _client(app):
    from fastapi.testclient import TestClient

    return TestClient(app, base_url="https://testserver")


# ----------------------------------------------------------------------------- sign-in


def test_every_api_route_needs_login(server_client):
    from app.auth import PUBLIC_API

    checked = 0
    for method, path in _api_routes(server_client.app):
        if path in PUBLIC_API:
            continue
        r = server_client.request(method, path)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
        checked += 1
    assert checked > 60
    for path in ("/api/photos/1/thumb", "/api/photos/1/original", "/api/docs", "/api/openapi.json"):
        assert server_client.get(path).status_code == 401, path


def test_login_logout_flow(server_client):
    c = server_client
    st = c.get("/api/auth/status").json()
    assert st["auth_required"] and not st["logged_in"] and st["user"] is None and st["setup_needed"] is False
    assert c.get("/api/dashboard").status_code == 401
    r = c.post("/api/auth/login", json={"email": "OWNER@example.com ", "password": TEST_PASSWORD})  # case/space-insensitive email
    assert r.status_code == 200 and r.json()["user"]["is_admin"] is True
    cookie = r.headers["set-cookie"].lower()
    assert "pl_session=" in cookie and "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert c.get("/api/dashboard").status_code == 200
    assert c.get("/api/auth/status").json()["user"]["email"] == TEST_EMAIL
    assert "no-store" in c.get("/api/dashboard").headers["cache-control"]  # never kept by a shared cache
    c.post("/api/auth/logout")
    c.cookies.clear()
    assert c.get("/api/dashboard").status_code == 401


def test_wrong_password_lockout_and_forged_cookie(server_client):
    c = server_client
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": TEST_EMAIL, "password": "nope nope nope"}).status_code == 401
    r = c.post("/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x" * 12}).status_code in (401, 429)
    assert c.get("/api/dashboard", cookies={"pl_session": "made-up"}).status_code == 401


def test_cross_origin_requests_refused(server_client, monkeypatch):
    from app import config

    c = server_client
    login(c)
    assert c.put("/api/settings", json={"units": "kg"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}, headers={"Origin": "null"}).status_code == 403
    assert c.put("/api/settings", json={"units": "kg"}, headers={"Origin": "https://testserver"}).status_code == 200
    monkeypatch.setattr(config, "PUBLIC_URL", "https://app.example.org")
    assert c.put("/api/settings", json={"units": "lb"}, headers={"Origin": "https://app.example.org"}).status_code == 200


def test_security_headers(server_client):
    r = server_client.get("/api/health")
    assert "connect-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


# ----------------------------------------------------------------------------- invites, sign-up, isolation


def test_signup_with_invite_and_accounts_are_isolated(server_client, data_dir):
    from app import accounts

    c = server_client
    login(c)
    owner_photo = _upload_photo(c, seed=1)
    sr = c.post("/api/capture/series", json={"name": "Owner daily"}).json()
    inv = c.post("/api/admin/invites", json={"note": "for Sam", "uses": 1, "days": 7}).json()
    assert inv["link"].endswith(f"/signup?invite={inv['code']}")
    c.post("/api/auth/logout")
    c.cookies.clear()

    bad = c.post("/api/auth/signup", json={"invite": "AAAA-BBBB-CCCC", "email": "sam@example.com", "password": "a long password"})
    assert bad.status_code == 422 and "invite" in bad.json()["detail"]
    r = c.post(
        "/api/auth/signup",
        json={"invite": inv["code"].lower().replace("-", ""), "email": "sam@example.com", "name": "Sam", "password": "sams long password"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["is_admin"] is False
    again = c.post("/api/auth/signup", json={"invite": inv["code"], "email": "eve@example.com", "password": "eves long password"})
    assert again.status_code == 422  # the invite is used up

    # Sam sees an empty library and nothing of the owner's
    assert c.get("/api/photos").json()["total"] == 0
    for path in (f"/api/photos/{owner_photo}", f"/api/photos/{owner_photo}/thumb", f"/api/photos/{owner_photo}/original",
                 f"/api/capture/series/{sr['id']}", f"/api/capture/series/{sr['id']}/sequence"):
        assert c.get(path).status_code == 404, path
    assert c.get("/api/capture/series").json()["daily"] == []
    assert all(j["type"] != "ingest_upload" for j in c.get("/api/jobs").json())
    assert c.get("/api/admin/users").status_code == 403  # not an admin
    _upload_photo(c, seed=2)
    sam = accounts.get_user_by_email("sam@example.com")
    assert (data_dir / "libraries" / str(sam.id) / "physiquelapse.db").exists()

    # and the owner doesn't see Sam's
    c.post("/api/auth/logout")
    c.cookies.clear()
    login(c)
    assert [p["id"] for p in c.get("/api/photos").json()["items"]] == [owner_photo]
    assert c.get("/api/photos").json()["items"][0]["original_filename"] == "p1.jpg"
    users = {u["email"]: u for u in c.get("/api/admin/users").json()}
    assert users["sam@example.com"]["used_mb"] > 0


def test_invite_expiry_and_revoke(server_client):
    from contextlib import closing

    from app import accounts

    code, iid = accounts.create_invite("x", uses=3, days=1)
    accounts.revoke_invite(iid)
    with pytest.raises(accounts.AccountError):
        accounts.sign_up(code, "a@example.com", "a long password")
    code2, iid2 = accounts.create_invite("y", uses=1, days=1)
    with closing(accounts.connect()) as con:
        con.execute("UPDATE invites SET expires_at = ? WHERE id = ?", ((datetime.now() - timedelta(minutes=1)).isoformat(), iid2))
    with pytest.raises(accounts.AccountError):
        accounts.sign_up(code2, "b@example.com", "a long password")
    code3, _ = accounts.create_invite("z", uses=1, days=1)
    with pytest.raises(accounts.AccountError):  # duplicate email: the code is not used up
        accounts.sign_up(code3, TEST_EMAIL, "a long password")
    assert accounts.sign_up(code3, "c@example.com", "a long password").email == "c@example.com"


def test_admin_disable_and_delete_user(server_client, data_dir):
    from app import accounts

    c = server_client
    sam = make_account("sam@example.com", "sams long password")
    with _client(c.app) as other:
        login(other, "sam@example.com", "sams long password")
        _upload_photo(other, seed=3)
        lib = data_dir / "libraries" / str(sam.id)
        assert lib.exists()
        login(c)
        assert c.patch(f"/api/admin/users/{sam.id}", json={"disabled": True}).status_code == 200
        assert other.get("/api/photos").status_code == 401  # signed out at once
        assert other.post("/api/auth/login", json={"email": "sam@example.com", "password": "sams long password"}).status_code == 401
    assert c.delete(f"/api/admin/users/{sam.id}").status_code == 200
    assert not lib.exists() and accounts.get_user(sam.id) is None
    me = accounts.get_user_by_email(TEST_EMAIL)
    assert c.patch(f"/api/admin/users/{me.id}", json={"disabled": True}).status_code == 409


# ----------------------------------------------------------------------------- passwords


def test_password_reset_flow(server_client, monkeypatch):
    from app import config, mailer

    c = server_client
    assert c.post("/api/auth/forgot", json={"email": TEST_EMAIL}).status_code == 503  # no email set up
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.org")
    monkeypatch.setattr(config, "MAIL_FROM", "PhysiqueLapse <no-reply@example.org>")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://app.example.org")
    monkeypatch.setattr(mailer, "send_reset", lambda to, name, link: sent.append((to, link)))
    r1 = c.post("/api/auth/forgot", json={"email": "nobody@example.com"})
    r2 = c.post("/api/auth/forgot", json={"email": TEST_EMAIL})
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()  # no way to tell who has an account
    assert len(sent) == 1 and sent[0][0] == TEST_EMAIL and sent[0][1].startswith("https://app.example.org/reset?token=")
    token = sent[0][1].split("token=")[1]
    with _client(c.app) as other:
        login(other)
        assert c.post("/api/auth/reset", json={"token": token, "password": "short"}).status_code == 422
        r = c.post("/api/auth/reset", json={"token": token, "password": "a brand new password"})
        assert r.status_code == 200 and c.get("/api/dashboard").status_code == 200  # signed in by the reset
        assert other.get("/api/dashboard").status_code == 401  # every other session ended
    assert c.post("/api/auth/reset", json={"token": token, "password": "another new password"}).status_code == 422  # single use
    c.cookies.clear()
    login(c, TEST_EMAIL, "a brand new password")


def test_change_password_and_delete_own_account(server_client, data_dir):
    from app import accounts

    c = server_client
    sam = make_account("sam@example.com", "sams long password")
    login(c, "sam@example.com", "sams long password")
    _upload_photo(c, seed=4)
    assert c.post("/api/auth/password", json={"current": "wrong", "new": "sams newer password"}).status_code == 401
    assert c.post("/api/auth/password", json={"current": "sams long password", "new": "sams newer password"}).status_code == 200
    assert c.get("/api/dashboard").status_code == 200  # still signed in here
    assert c.post("/api/auth/delete-account", json={"password": "sams long password"}).status_code == 401
    assert c.post("/api/auth/delete-account", json={"password": "sams newer password"}).status_code == 200
    assert accounts.get_user(sam.id) is None and not (data_dir / "libraries" / str(sam.id)).exists()
    c.cookies.clear()
    login(c)  # the only admin can't delete themself
    assert c.post("/api/auth/delete-account", json={"password": TEST_PASSWORD}).status_code == 409


def test_storage_quota(server_client):
    from app import accounts

    c = server_client
    sam = make_account("sam@example.com", "sams long password")
    accounts.set_quota(sam.id, 100)  # MB
    login(c, "sam@example.com", "sams long password")
    sid = c.post("/api/imports/upload-session").json()["id"]
    r = c.post(f"/api/imports/upload-session/{sid}/files/init", json={"rel_path": "big.jpg", "size": 101 << 20})
    assert r.status_code == 413 and "storage is full" in r.json()["detail"]
    assert c.get("/api/auth/usage").json()["quota_mb"] == 100


# ----------------------------------------------------------------------------- setup, CLI, desktop


def test_cli_create_admin_invite_and_migrate(data_dir, monkeypatch, capsys):
    """The owner's library from before accounts moves into their account and stays usable."""
    import io

    from sqlmodel import Session, select

    from app import accounts, tenancy
    from app.db import engine
    from app.models import Photo

    with Session(engine()) as s:  # the single library at DATA_DIR
        s.add(Photo(sha256="old", original_path="originals/old.jpg", original_filename="old.jpg", ext="jpg"))
        s.commit()
    (data_dir / "originals" / "old.jpg").write_bytes(b"x")
    monkeypatch.setattr("sys.stdin", io.StringIO("the owners password\n"))
    assert accounts._cli(["create-admin", "--email", "me@example.com", "--stdin"]) == 0
    assert accounts._cli(["invite", "--note", "friend"]) == 0
    assert "Invite code:" in capsys.readouterr().out
    assert accounts._cli(["migrate-library", "--email", "me@example.com"]) == 0
    me = accounts.get_user_by_email("me@example.com")
    lib = tenancy.library_for_user(me.id)
    assert not (data_dir / "physiquelapse.db").exists() and (lib.root / "originals" / "old.jpg").exists()
    assert list(data_dir.glob("physiquelapse.before-move-*.db"))
    with tenancy.use_library(lib), Session(engine()) as s:
        assert [p.sha256 for p in s.exec(select(Photo)).all()] == ["old"]
    assert accounts._cli(["migrate-library", "--email", "me@example.com"]) == 1  # nothing left to move


def test_desktop_mode_needs_no_login(data_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config, jobs
    from app.main import create_app

    monkeypatch.setattr(config, "SERVER", False)
    monkeypatch.setattr(config, "MODE", "desktop")
    jobs._runner = None
    with TestClient(create_app()) as c:
        assert c.get("/api/dashboard").status_code == 200
        st = c.get("/api/auth/status").json()
        assert st["auth_required"] is False and st["logged_in"] is True
        assert "content-security-policy" not in c.get("/api/health").headers
        assert c.post("/api/auth/signup", json={"invite": "x", "email": "a@b.co", "password": "x" * 12}).status_code == 404
    jobs._runner = None


def test_migration_from_v8_upgrades_and_backs_up(tmp_path):
    """An existing v8 library upgrades in place and keeps a copy of the old database."""
    import sqlite3

    from app import db

    d = tmp_path / "lib"
    d.mkdir()
    db.reset_engine_for_tests(d)
    db.init_db()
    con = sqlite3.connect(d / "physiquelapse.db")
    con.execute("""UPDATE settings SET value='{"v": 8}' WHERE key='schema_version'""")
    con.commit()
    con.close()
    db.reset_engine_for_tests(d)
    db.init_db()
    con = sqlite3.connect(d / "physiquelapse.db")
    version = con.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()[0]
    con.close()
    assert json.loads(version)["v"] == db.SCHEMA_VERSION
    assert (d / f"physiquelapse.before-v{db.SCHEMA_VERSION}.db").exists()
    db.reset_engine_for_tests(d)
