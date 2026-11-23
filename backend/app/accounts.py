"""Accounts for the server: users, sessions, invite codes, password resets.

Kept in their own small database (DATA_DIR/accounts.db), apart from every library, and read
with the standard sqlite3 module. Each account owns one library (tenancy.library_for_user).
Passwords are argon2-hashed; session, invite and reset tokens are random and stored only as
SHA-256. The desktop app has no accounts.

On the server:
    docker compose exec physiquelapse python -m app.accounts create-admin --email you@example.com
    docker compose exec physiquelapse python -m app.accounts migrate-library --email you@example.com
    docker compose exec physiquelapse python -m app.accounts invite --note "for Sam"
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import shutil
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

MIN_PASSWORD_LEN = 10
RESET_TTL = timedelta(hours=1)
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  disabled INTEGER NOT NULL DEFAULT 0,
  quota_mb INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_sha256 TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  user_agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS invites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code_sha256 TEXT NOT NULL UNIQUE,
  note TEXT NOT NULL DEFAULT '',
  uses_left INTEGER NOT NULL DEFAULT 1,
  expires_at TEXT,
  revoked INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS password_resets (
  token_sha256 TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  used_at TEXT
);
"""


class AccountError(ValueError):
    """A request about accounts that can't be done (shown to the user as is)."""


@dataclass(frozen=True)
class User:
    id: int
    email: str
    name: str
    is_admin: bool
    disabled: bool
    quota_mb: int | None
    created_at: str

    def public(self) -> dict:
        return {"id": self.id, "email": self.email, "name": self.name, "is_admin": self.is_admin}


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _iso(d: datetime) -> str:
    return d.isoformat()


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def db_path() -> Path:
    return config.DATA_DIR / "accounts.db"


_init_lock = threading.Lock()
_initialized: set[str] = set()


def connect() -> sqlite3.Connection:
    path = db_path()
    key = str(path)
    if key not in _initialized:
        with _init_lock:
            if key not in _initialized:
                path.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(path, timeout=30)) as c:
                    c.execute("PRAGMA journal_mode=WAL")
                    c.executescript(SCHEMA)
                    c.commit()
                _initialized.add(key)
    c = sqlite3.connect(path, timeout=30, isolation_level=None)  # autocommit; BEGIN where needed
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    return c


def _user(row) -> User | None:
    if row is None:
        return None
    return User(row["id"], row["email"], row["name"], bool(row["is_admin"]), bool(row["disabled"]), row["quota_mb"], row["created_at"])


# ----------------------------------------------------------------------------- users


def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if len(e) > 254 or "@" not in e or e.startswith("@") or e.endswith("@") or " " in e or "." not in e.split("@")[-1]:
        raise AccountError("Enter a valid email address.")
    return e


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise AccountError(f"Use at least {MIN_PASSWORD_LEN} characters for the password.")
    if len(password) > 1024:
        raise AccountError("That password is too long.")


def count_users() -> int:
    with closing(connect()) as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(email: str, password: str, name: str = "", *, is_admin: bool = False) -> User:
    from .auth import hash_password

    email = normalize_email(email)
    check_password(password)
    h = hash_password(password)
    with closing(connect()) as c:
        try:
            cur = c.execute(
                "INSERT INTO users (email, name, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
                (email, name.strip()[:80], h, int(is_admin), _iso(_now())),
            )
        except sqlite3.IntegrityError:
            raise AccountError("An account with that email already exists.") from None
        return get_user(cur.lastrowid)  # type: ignore[return-value]


def get_user(uid: int) -> User | None:
    with closing(connect()) as c:
        return _user(c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())


def get_user_by_email(email: str) -> User | None:
    with closing(connect()) as c:
        return _user(c.execute("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)).fetchone())


def list_users() -> list[User]:
    with closing(connect()) as c:
        return [_user(r) for r in c.execute("SELECT * FROM users ORDER BY id")]  # type: ignore[misc]


def verify_login(email: str, password: str) -> User | None:
    """The account for these credentials, or None (also for disabled accounts). Takes the same
    time whether or not the email exists."""
    from .auth import _hasher, dummy_hash

    with closing(connect()) as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)).fetchone()
    ph = _hasher()
    if row is None:
        try:
            ph.verify(dummy_hash(), password)
        except Exception:
            pass
        return None
    from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

    try:
        ph.verify(row["password_hash"], password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return None
    if ph.check_needs_rehash(row["password_hash"]):
        with closing(connect()) as c:
            c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (ph.hash(password), row["id"]))
    u = _user(row)
    return None if u is None or u.disabled else u


def set_password(uid: int, password: str) -> None:
    """New password; every session of the account is logged out."""
    from .auth import hash_password

    check_password(password)
    with closing(connect()) as c:
        c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), uid))
        c.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))


def set_disabled(uid: int, disabled: bool) -> None:
    with closing(connect()) as c:
        c.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), uid))
        if disabled:
            c.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))


def set_quota(uid: int, quota_mb: int | None) -> None:
    with closing(connect()) as c:
        c.execute("UPDATE users SET quota_mb = ? WHERE id = ?", (quota_mb, uid))


def delete_user(uid: int) -> None:
    """Delete the account and its whole library (photos, database, derived files)."""
    from . import tenancy
    from .db import dispose_engine_for

    lib = tenancy.library_for_user(uid)
    dispose_engine_for(lib.root / "physiquelapse.db")
    with closing(connect()) as c:
        c.execute("DELETE FROM users WHERE id = ?", (uid,))
    if lib.root.exists():
        shutil.rmtree(lib.root, ignore_errors=True)
    _usage_cache.pop(uid, None)


# ----------------------------------------------------------------------------- sessions


def create_session(uid: int, user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    t = _now()
    with closing(connect()) as c:
        c.execute(
            "INSERT INTO sessions (token_sha256, user_id, created_at, last_seen_at, expires_at, user_agent) VALUES (?, ?, ?, ?, ?, ?)",
            (_sha(token), uid, _iso(t), _iso(t), _iso(t + timedelta(days=config.SESSION_DAYS)), user_agent[:200]),
        )
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(t),))
    return token


_TOUCH_EVERY = timedelta(minutes=5)


def user_for_session(token: str | None) -> User | None:
    """The logged-in account for a session cookie (sliding expiry), or None."""
    if not token:
        return None
    with closing(connect()) as c:
        row = c.execute(
            "SELECT s.token_sha256, s.last_seen_at, s.expires_at, u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_sha256 = ?",
            (_sha(token),),
        ).fetchone()
        if row is None:
            return None
        t = _now()
        if datetime.fromisoformat(row["expires_at"]) < t or row["disabled"]:
            c.execute("DELETE FROM sessions WHERE token_sha256 = ?", (row["token_sha256"],))
            return None
        if t - datetime.fromisoformat(row["last_seen_at"]) > _TOUCH_EVERY:
            c.execute(
                "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_sha256 = ?",
                (_iso(t), _iso(t + timedelta(days=config.SESSION_DAYS)), row["token_sha256"]),
            )
        return _user(row)


def end_session(token: str | None) -> None:
    if token:
        with closing(connect()) as c:
            c.execute("DELETE FROM sessions WHERE token_sha256 = ?", (_sha(token),))


def revoke_sessions(uid: int | None = None) -> int:
    with closing(connect()) as c:
        if uid is None:
            return c.execute("DELETE FROM sessions").rowcount
        return c.execute("DELETE FROM sessions WHERE user_id = ?", (uid,)).rowcount


# ----------------------------------------------------------------------------- invites

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I: easy to read out


def _new_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def _norm_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def create_invite(note: str = "", uses: int = 1, days: int | None = 14, created_by: int | None = None) -> tuple[str, int]:
    """A new invite code (shown once) and its id."""
    if not 1 <= uses <= 100:
        raise AccountError("Uses must be between 1 and 100.")
    code = _new_code()
    exp = _iso(_now() + timedelta(days=days)) if days else None
    with closing(connect()) as c:
        cur = c.execute(
            "INSERT INTO invites (code_sha256, note, uses_left, expires_at, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (_sha(_norm_code(code)), note.strip()[:120], uses, exp, created_by, _iso(_now())),
        )
        return code, cur.lastrowid  # type: ignore[return-value]


def list_invites() -> list[dict]:
    t = _now()
    with closing(connect()) as c:
        rows = c.execute("SELECT id, note, uses_left, expires_at, revoked, created_at FROM invites ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        expired = bool(r["expires_at"]) and datetime.fromisoformat(r["expires_at"]) < t
        out.append({**dict(r), "revoked": bool(r["revoked"]), "usable": not r["revoked"] and not expired and r["uses_left"] > 0})
    return out


def revoke_invite(invite_id: int) -> None:
    with closing(connect()) as c:
        c.execute("UPDATE invites SET revoked = 1 WHERE id = ?", (invite_id,))


def sign_up(code: str, email: str, password: str, name: str = "") -> User:
    """Create an account with an invite code (the code is used up atomically)."""
    email = normalize_email(email)
    check_password(password)
    if get_user_by_email(email) is not None:
        raise AccountError("An account with that email already exists.")
    with closing(connect()) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            n = c.execute(
                "UPDATE invites SET uses_left = uses_left - 1 WHERE code_sha256 = ? AND uses_left > 0 AND revoked = 0 "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (_sha(_norm_code(code)), _iso(_now())),
            ).rowcount
            if n != 1:
                c.execute("ROLLBACK")
                raise AccountError("That invite code isn't valid (used up, expired or mistyped).")
            c.execute("COMMIT")
        except AccountError:
            raise
        except Exception:
            c.execute("ROLLBACK")
            raise
    try:
        return create_user(email, password, name)
    except AccountError:
        # give the code back if the account couldn't be made
        with closing(connect()) as c:
            c.execute("UPDATE invites SET uses_left = uses_left + 1 WHERE code_sha256 = ?", (_sha(_norm_code(code)),))
        raise


# ----------------------------------------------------------------------------- password resets


def create_reset(uid: int) -> str:
    token = secrets.token_urlsafe(32)
    with closing(connect()) as c:
        c.execute("DELETE FROM password_resets WHERE user_id = ? AND used_at IS NULL", (uid,))  # newest link only
        c.execute("INSERT INTO password_resets (token_sha256, user_id, expires_at) VALUES (?, ?, ?)", (_sha(token), uid, _iso(_now() + RESET_TTL)))
    return token


def use_reset(token: str, password: str) -> User:
    check_password(password)
    with closing(connect()) as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT user_id, expires_at, used_at FROM password_resets WHERE token_sha256 = ?", (_sha(token or ""),)).fetchone()
        if row is None or row["used_at"] or datetime.fromisoformat(row["expires_at"]) < _now():
            c.execute("ROLLBACK")
            raise AccountError("This reset link has expired or was already used. Ask for a new one.")
        c.execute("UPDATE password_resets SET used_at = ? WHERE token_sha256 = ?", (_iso(_now()), _sha(token)))
        c.execute("COMMIT")
    set_password(row["user_id"], password)
    u = get_user(row["user_id"])
    if u is None:
        raise AccountError("This account no longer exists.")
    return u


# ----------------------------------------------------------------------------- storage quota

_usage_cache: dict[int, tuple[float, int]] = {}


def library_bytes(uid: int, max_age: float = 60.0) -> int:
    """Disk used by an account's library (cached for a minute)."""
    from . import tenancy

    hit = _usage_cache.get(uid)
    if hit and time.monotonic() - hit[0] < max_age:
        return hit[1]
    total = 0
    root = tenancy.library_for_user(uid).root
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    _usage_cache[uid] = (time.monotonic(), total)
    return total


def quota_bytes(user: User) -> int:
    mb = user.quota_mb if user.quota_mb is not None else config.QUOTA_MB
    return mb << 20


def note_added(uid: int, nbytes: int) -> None:
    hit = _usage_cache.get(uid)
    if hit:
        _usage_cache[uid] = (hit[0], hit[1] + nbytes)


# ----------------------------------------------------------------------------- moving the owner's library


LIBRARY_ITEMS = ("originals", "derived", "examples", "trash", "incoming", "physiquelapse.db", "physiquelapse.db-wal", "physiquelapse.db-shm")


def migrate_single_library(uid: int) -> Path:
    """Move the server's original single library (DATA_DIR/originals, .../physiquelapse.db, ...)
    into an account's library folder. The database is copied first as a backup."""
    from . import tenancy
    from .db import dispose_engines

    src = config.DATA_DIR
    if not (src / "physiquelapse.db").exists():
        raise AccountError(f"No single library found in {src}.")
    dest = tenancy.library_for_user(uid).root
    if (dest / "physiquelapse.db").exists():
        raise AccountError(f"Account {uid} already has a library at {dest}.")
    dispose_engines()
    backup = src / f"physiquelapse.before-move-{_now():%Y%m%d-%H%M%S}.db"
    with closing(sqlite3.connect(src / "physiquelapse.db")) as a, closing(sqlite3.connect(backup)) as b:
        a.backup(b)
    dest.mkdir(parents=True, exist_ok=True)
    for name in LIBRARY_ITEMS:
        p = src / name
        if p.exists():
            shutil.move(str(p), str(dest / name))
    log.info("moved the single library from %s to %s (database backup: %s)", src, dest, backup)
    return dest


# ----------------------------------------------------------------------------- CLI


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    import getpass
    import sys

    p = argparse.ArgumentParser(prog="python -m app.accounts", description="PhysiqueLapse accounts (server)")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin", help="create an admin account")
    a.add_argument("--email", required=True)
    a.add_argument("--name", default="")
    a.add_argument("--stdin", action="store_true", help="read the password from standard input")
    pw = sub.add_parser("set-password", help="set an account's password (logs it out everywhere)")
    pw.add_argument("--email", required=True)
    pw.add_argument("--stdin", action="store_true")
    inv = sub.add_parser("invite", help="make an invite code")
    inv.add_argument("--note", default="")
    inv.add_argument("--uses", type=int, default=1)
    inv.add_argument("--days", type=int, default=14)
    mig = sub.add_parser("migrate-library", help="move the single library (from before accounts) to an account")
    mig.add_argument("--email", required=True)
    sub.add_parser("list", help="list accounts")
    args = p.parse_args(argv)

    def read_pw() -> str:
        if getattr(args, "stdin", False):
            return sys.stdin.readline().rstrip("\r\n")
        a1 = getpass.getpass("Password: ")
        if getpass.getpass("Again: ") != a1:
            raise AccountError("Passwords don't match.")
        return a1

    try:
        if args.cmd == "create-admin":
            u = create_user(args.email, read_pw(), args.name, is_admin=True)
            print(f"Admin account {u.email} created (id {u.id}).")
        elif args.cmd == "set-password":
            u = get_user_by_email(args.email)
            if u is None:
                raise AccountError("No account with that email.")
            set_password(u.id, read_pw())
            print("Password set; the account was logged out everywhere.")
        elif args.cmd == "invite":
            code, _ = create_invite(args.note, args.uses, args.days or None)
            base = config.PUBLIC_URL or "https://<your address>"
            print(f"Invite code: {code}\nLink: {base}/signup?invite={code}")
        elif args.cmd == "migrate-library":
            u = get_user_by_email(args.email)
            if u is None:
                raise AccountError("No account with that email.")
            print(f"Library moved to {migrate_single_library(u.id)}")
        else:
            for u in list_users():
                gb = library_bytes(u.id) / 1e9
                print(f"{u.id:>4}  {u.email:<32} {'admin ' if u.is_admin else ''}{'disabled ' if u.disabled else ''}{gb:.2f} GB")
    except AccountError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
