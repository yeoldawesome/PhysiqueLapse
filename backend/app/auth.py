"""Server mode login: one user, one password (argon2), cookie sessions.

Desktop mode never uses any of this: `config.auth_required()` is False and the middleware
lets every request through.

Set the password on the server:

    docker compose exec physiquelapse python -m app.auth set-password
    docker compose exec physiquelapse python -m app.auth revoke-sessions   # log out everywhere
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlmodel import Session, delete, select
from starlette.middleware.base import BaseHTTPMiddleware

from . import config
from .db import engine
from .models import AuthSession, AuthState, now

log = logging.getLogger(__name__)

COOKIE_NAME = "pl_session"
MIN_PASSWORD_LEN = 10
# /api paths reachable without a session
PUBLIC_API = {"/api/health", "/api/auth/login", "/api/auth/status", "/api/auth/logout"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# ----------------------------------------------------------------------------- passwords


def _hasher():
    from argon2 import PasswordHasher  # server extra; never imported by the desktop app

    return PasswordHasher()


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    return _hasher().hash(password)


def password_is_set(s: Session) -> bool:
    return s.get(AuthState, 1) is not None


def set_password(s: Session, password: str) -> None:
    h = hash_password(password)
    row = s.get(AuthState, 1)
    if row is None:
        row = AuthState(id=1, password_hash=h)
    else:
        row.password_hash = h
        row.updated_at = now()
    s.add(row)
    # a new password logs every browser out
    s.exec(delete(AuthSession))
    s.commit()


def verify_password(s: Session, password: str) -> bool:
    row = s.get(AuthState, 1)
    if row is None:
        # spend the same time as a real check so timing doesn't reveal "no password set"
        try:
            _hasher().verify(_DUMMY_HASH(), password)
        except Exception:
            pass
        return False
    from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

    ph = _hasher()
    try:
        ph.verify(row.password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    if ph.check_needs_rehash(row.password_hash):
        row.password_hash = ph.hash(password)
        s.add(row)
        s.commit()
    return True


_dummy: str | None = None


def _DUMMY_HASH() -> str:  # noqa: N802
    global _dummy
    if _dummy is None:
        _dummy = _hasher().hash(secrets.token_urlsafe(16))
    return _dummy


# ----------------------------------------------------------------------------- sessions


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(s: Session, user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    s.add(AuthSession(token_sha256=_sha(token), expires_at=now() + timedelta(days=config.SESSION_DAYS), user_agent=user_agent[:200]))
    # tidy up expired rows while we're here
    s.exec(delete(AuthSession).where(AuthSession.expires_at < now()))
    s.commit()
    return token


_TOUCH_EVERY = timedelta(minutes=5)


def check_session(s: Session, token: str | None) -> bool:
    """Valid, unexpired session? Sliding expiry: use extends it."""
    if not token:
        return False
    row = s.get(AuthSession, _sha(token))
    if row is None:
        return False
    t = now()
    if row.expires_at < t:
        s.delete(row)
        s.commit()
        return False
    if t - row.last_seen_at > _TOUCH_EVERY:
        row.last_seen_at = t
        row.expires_at = t + timedelta(days=config.SESSION_DAYS)
        s.add(row)
        s.commit()
    return True


def end_session(s: Session, token: str | None) -> None:
    if token:
        row = s.get(AuthSession, _sha(token))
        if row is not None:
            s.delete(row)
            s.commit()


def revoke_all(s: Session) -> int:
    n = len(s.exec(select(AuthSession.token_sha256)).all())
    s.exec(delete(AuthSession))
    s.commit()
    return n


def set_cookie(response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=config.SESSION_DAYS * 86400, httponly=True,
        secure=config.COOKIE_SECURE, samesite="strict", path="/",
    )


def clear_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=config.COOKIE_SECURE, samesite="strict")


# ----------------------------------------------------------------------------- rate limit


class LoginLimiter:
    """Failed logins per client: 5 free attempts, then a lockout that doubles each time
    (30 s, 60 s, ... up to 1 h). A global cap slows down attempts spread over many addresses."""

    FREE = 5
    BASE_LOCK = 30.0
    MAX_LOCK = 3600.0
    GLOBAL_PER_MIN = 30

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fails: dict[str, tuple[int, float]] = {}  # key -> (failures, locked_until)
        self._recent: list[float] = []  # global failure times

    def retry_after(self, key: str) -> float:
        """Seconds until this client may try again (0 = now)."""
        t = time.monotonic()
        with self._lock:
            self._recent = [x for x in self._recent if t - x < 60]
            if len(self._recent) >= self.GLOBAL_PER_MIN:
                return max(1.0, 60 - (t - self._recent[0]))
            _, until = self._fails.get(key, (0, 0.0))
            return max(0.0, until - t)

    def failed(self, key: str) -> None:
        t = time.monotonic()
        with self._lock:
            self._recent.append(t)
            n, _ = self._fails.get(key, (0, 0.0))
            n += 1
            until = 0.0
            if n >= self.FREE:
                until = t + min(self.MAX_LOCK, self.BASE_LOCK * 2 ** (n - self.FREE))
            self._fails[key] = (n, until)

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()
            self._recent.clear()


limiter = LoginLimiter()


def client_key(request: Request) -> str:
    # uvicorn --proxy-headers already replaced client.host with the forwarded address
    return request.client.host if request.client else "unknown"


# ----------------------------------------------------------------------------- middleware


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        # no Origin: not a cross-site browser request (SameSite=Strict covers the cookie);
        # "null" from a sandboxed/opaque context is refused below for unsafe methods
        return origin is None
    o = origin.rstrip("/")
    if o in config.ALLOWED_ORIGINS:
        return True
    host = urlsplit(o).netloc.lower()
    hosts = {h.strip().lower() for h in (request.headers.get("host"), request.headers.get("x-forwarded-host")) if h}
    return host in hosts


class AuthMiddleware(BaseHTTPMiddleware):
    """Server mode: every /api route needs a session cookie, and state-changing requests must
    come from our own origin. Pages and built assets stay public (the login page is one)."""

    async def dispatch(self, request: Request, call_next):
        if not config.auth_required():
            return await call_next(request)
        path = request.url.path
        if not (path == "/api" or path.startswith("/api/")):
            return await call_next(request)
        if request.method not in _SAFE_METHODS and not _origin_ok(request):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        if path in PUBLIC_API:
            return await call_next(request)
        with Session(engine()) as s:
            ok = check_session(s, request.cookies.get(COOKIE_NAME))
        if not ok:
            return JSONResponse({"detail": "login required"}, status_code=401)
        return await call_next(request)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=()",
    # Everything is served from this origin; the browser may not contact anything else.
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
        "worker-src 'self' blob:; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if config.SERVER:
            for k, v in SECURITY_HEADERS.items():
                response.headers.setdefault(k, v)
            if config.COOKIE_SECURE:
                response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response


# ----------------------------------------------------------------------------- CLI


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    import getpass
    import sys

    from .db import init_db

    p = argparse.ArgumentParser(prog="python -m app.auth", description="PhysiqueLapse server login")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("set-password", help="set or change the password (logs every browser out)")
    sp.add_argument("--stdin", action="store_true", help="read the password from standard input")
    sub.add_parser("revoke-sessions", help="log out every browser")
    sub.add_parser("status", help="is a password set, how many sessions")
    a = p.parse_args(argv)

    init_db()
    with Session(engine()) as s:
        if a.cmd == "set-password":
            if a.stdin:
                pw = sys.stdin.readline().rstrip("\r\n")
            else:
                pw = getpass.getpass("New password: ")
                if getpass.getpass("Again: ") != pw:
                    print("Passwords don't match.", file=sys.stderr)
                    return 1
            try:
                set_password(s, pw)
            except ValueError as e:
                print(str(e), file=sys.stderr)
                return 1
            print("Password set. Every browser has been logged out.")
        elif a.cmd == "revoke-sessions":
            print(f"Logged out {revoke_all(s)} session(s).")
        else:
            n = len(s.exec(select(AuthSession.token_sha256)).all())
            print(f"mode={config.MODE} password_set={password_is_set(s)} sessions={n} data={config.DATA_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
