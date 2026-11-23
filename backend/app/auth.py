"""Server mode sign-in: accounts (see accounts.py), cookie sessions, rate limits, headers.

Desktop mode never uses any of this: `config.auth_required()` is False and the middleware
lets every request through (to the one desktop library).

In server mode every /api route except the few in PUBLIC_API needs a session; the request then
runs inside that account's own library (tenancy), so it can only ever see that account's data.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from . import config, tenancy

log = logging.getLogger(__name__)

COOKIE_NAME = "pl_session"
# /api paths reachable without a session (they never touch a library)
PUBLIC_API = {
    "/api/health",
    "/api/auth/login",
    "/api/auth/status",
    "/api/auth/logout",
    "/api/auth/signup",
    "/api/auth/forgot",
    "/api/auth/reset",
}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# ----------------------------------------------------------------------------- passwords


def _hasher():
    from argon2 import PasswordHasher  # server extra; never imported by the desktop app

    return PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher().hash(password)


_dummy: str | None = None


def dummy_hash() -> str:
    """A real hash to verify against when an email doesn't exist (same timing either way)."""
    global _dummy
    if _dummy is None:
        _dummy = _hasher().hash(secrets.token_urlsafe(16))
    return _dummy


# ----------------------------------------------------------------------------- cookies


def set_cookie(response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=config.SESSION_DAYS * 86400, httponly=True,
        secure=config.COOKIE_SECURE, samesite="lax", path="/",
    )


def clear_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=config.COOKIE_SECURE, samesite="lax")


# ----------------------------------------------------------------------------- rate limits


class Limiter:
    """Failures per key: `free` attempts, then a lockout that doubles each time (base s, up to
    an hour). A global cap slows attempts spread over many addresses."""

    def __init__(self, free: int = 5, base: float = 30.0, max_lock: float = 3600.0, global_per_min: int = 30) -> None:
        self.free, self.base, self.max_lock, self.global_per_min = free, base, max_lock, global_per_min
        self._lock = threading.Lock()
        self._fails: dict[str, tuple[int, float]] = {}
        self._recent: list[float] = []

    def retry_after(self, *keys: str) -> float:
        t = time.monotonic()
        with self._lock:
            self._recent = [x for x in self._recent if t - x < 60]
            if len(self._recent) >= self.global_per_min:
                return max(1.0, 60 - (t - self._recent[0]))
            return max([0.0] + [self._fails.get(k, (0, 0.0))[1] - t for k in keys])

    def failed(self, *keys: str) -> None:
        t = time.monotonic()
        with self._lock:
            self._recent.append(t)
            for k in keys:
                n, _ = self._fails.get(k, (0, 0.0))
                n += 1
                until = t + min(self.max_lock, self.base * 2 ** (n - self.free)) if n >= self.free else 0.0
                self._fails[k] = (n, until)

    def succeeded(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._fails.pop(k, None)

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()
            self._recent.clear()


limiter = Limiter()  # wrong passwords, per address and per email
signup_limiter = Limiter(free=5, base=60.0, global_per_min=20)  # bad invite codes
forgot_limiter = Limiter(free=3, base=120.0, global_per_min=10)  # reset emails


def reset_limiters() -> None:
    for lim in (limiter, signup_limiter, forgot_limiter):
        lim.reset()


def client_key(request: Request) -> str:
    """The visitor's address. Through Cloudflare's tunnel it's CF-Connecting-IP (set by
    Cloudflare, not by the visitor); otherwise the proxy-resolved client address."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return "ip:" + cf.strip()
    return "ip:" + (request.client.host if request.client else "unknown")


def check_limit(lim: Limiter, *keys: str) -> None:
    wait = lim.retry_after(*keys)
    if wait > 0:
        s = int(wait) + 1
        raise HTTPException(429, f"Too many attempts. Try again in {s} s.", headers={"Retry-After": str(s)})


# ----------------------------------------------------------------------------- middleware


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        # no Origin: not a cross-site browser request; "null" (sandboxed page) is refused
        return origin is None
    o = origin.rstrip("/")
    if o in config.ALLOWED_ORIGINS or (config.PUBLIC_URL and o == config.PUBLIC_URL):
        return True
    host = urlsplit(o).netloc.lower()
    hosts = {h.strip().lower() for h in (request.headers.get("host"), request.headers.get("x-forwarded-host")) if h}
    return host in hosts


def current_user(request: Request):
    """The signed-in account (server mode), or None (desktop app)."""
    return getattr(request.state, "user", None)


def require_admin(request: Request):
    u = current_user(request)
    if u is None or not u.is_admin:
        raise HTTPException(403, "admins only")
    return u


class AuthMiddleware(BaseHTTPMiddleware):
    """Server mode: every /api route needs a session, state-changing requests must come from our
    own origin, and the request runs in the signed-in account's library. Pages and built
    assets stay public (the sign-in page is one)."""

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
        from . import accounts

        user = await run_in_threadpool(accounts.user_for_session, request.cookies.get(COOKIE_NAME))
        if user is None:
            return JSONResponse({"detail": "login required"}, status_code=401)
        request.state.user = user
        token = tenancy.set_for_request(tenancy.library_for_user(user.id))
        try:
            response = await call_next(request)
        finally:
            tenancy.reset(token)
        # personal data: never stored by Cloudflare or any other shared cache
        cc = response.headers.get("cache-control", "")
        if "private" not in cc and "no-store" not in cc:
            response.headers["Cache-Control"] = "private, no-store"
        return response


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


if __name__ == "__main__":
    print("Accounts moved: use  python -m app.accounts --help")
