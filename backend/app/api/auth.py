"""Accounts: status, sign in / out, sign up with an invite, password reset, own account.

The desktop app has no accounts: status says so and nothing here is needed.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import auth, config, mailer

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)


def _accounts():
    from .. import accounts

    return accounts


def _err(e: Exception) -> HTTPException:
    return HTTPException(422, str(e))


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    required = config.auth_required()
    out: dict[str, Any] = {
        "mode": config.MODE,
        "auth_required": required,
        "logged_in": not required,
        "user": None,
        # server mode: importing from server folders only when PL_IMPORT_ROOTS is set
        "path_import": (not config.SERVER) or bool(config.IMPORT_ROOTS),
        "email_reset": mailer.configured(),
    }
    if required:
        A = _accounts()
        u = A.user_for_session(request.cookies.get(auth.COOKIE_NAME))
        out["logged_in"] = u is not None
        out["user"] = u.public() if u else None
        out["setup_needed"] = A.count_users() == 0
    return out


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    if not config.auth_required():
        return {"ok": True}
    A = _accounts()
    keys = (auth.client_key(request), "email:" + body.email.strip().lower())
    auth.check_limit(auth.limiter, *keys)
    u = await run_in_threadpool(A.verify_login, body.email, body.password)
    if u is None:
        auth.limiter.failed(*keys)
        raise HTTPException(401, "Wrong email or password.")
    auth.limiter.succeeded(*keys)
    auth.set_cookie(response, A.create_session(u.id, request.headers.get("user-agent", "")))
    return {"ok": True, "user": u.public()}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    if config.auth_required():
        _accounts().end_session(request.cookies.get(auth.COOKIE_NAME))
    auth.clear_cookie(response)
    return {"ok": True}


class SignupIn(BaseModel):
    invite: str = Field(max_length=64)
    email: str = Field(max_length=254)
    name: str = Field("", max_length=80)
    password: str = Field(max_length=1024)


@router.post("/signup")
async def signup(body: SignupIn, request: Request, response: Response) -> dict[str, Any]:
    if not config.auth_required():
        raise HTTPException(404, "no accounts in the desktop app")
    A = _accounts()
    key = auth.client_key(request)
    auth.check_limit(auth.signup_limiter, key)
    try:
        u = await run_in_threadpool(A.sign_up, body.invite, body.email, body.password, body.name)
    except A.AccountError as e:
        if "invite" in str(e):
            auth.signup_limiter.failed(key)
        raise _err(e) from None
    auth.set_cookie(response, A.create_session(u.id, request.headers.get("user-agent", "")))
    return {"ok": True, "user": u.public()}


class ForgotIn(BaseModel):
    email: str = Field(max_length=254)


@router.post("/forgot")
async def forgot(body: ForgotIn, request: Request) -> dict[str, Any]:
    """Email a reset link. Same answer whether or not the email has an account."""
    if not config.auth_required():
        raise HTTPException(404, "no accounts in the desktop app")
    if not mailer.configured():
        raise HTTPException(503, "Password reset by email isn't set up on this server. Ask the admin to reset it.")
    A = _accounts()
    email = body.email.strip().lower()
    keys = (auth.client_key(request), "email:" + email)
    auth.check_limit(auth.forgot_limiter, *keys)
    auth.forgot_limiter.failed(*keys)  # every request counts: only a few emails per address
    u = A.get_user_by_email(email)
    if u is not None and not u.disabled:
        link = mailer.reset_link(A.create_reset(u.id), str(request.base_url))
        try:
            await run_in_threadpool(mailer.send_reset, u.email, u.name, link)
        except Exception:
            log.exception("reset email for account %s failed", u.id)
    return {"ok": True, "message": "If that email has an account, a reset link is on its way."}


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=1024)


@router.post("/reset")
async def reset(body: ResetIn, request: Request, response: Response) -> dict[str, Any]:
    if not config.auth_required():
        raise HTTPException(404, "no accounts in the desktop app")
    A = _accounts()
    key = auth.client_key(request)
    auth.check_limit(auth.signup_limiter, key)
    try:
        u = await run_in_threadpool(A.use_reset, body.token, body.password)
    except A.AccountError as e:
        auth.signup_limiter.failed(key)
        raise _err(e) from None
    auth.set_cookie(response, A.create_session(u.id, request.headers.get("user-agent", "")))
    return {"ok": True, "user": u.public()}


class PasswordIn(BaseModel):
    current: str = Field(max_length=1024)
    new: str = Field(max_length=1024)


@router.post("/password")
async def change_password(body: PasswordIn, request: Request, response: Response) -> dict[str, Any]:
    u = auth.current_user(request)
    if u is None:
        raise HTTPException(404, "no accounts in the desktop app")
    A = _accounts()
    if await run_in_threadpool(A.verify_login, u.email, body.current) is None:
        raise HTTPException(401, "The current password is wrong.")
    try:
        A.set_password(u.id, body.new)  # logs out every session...
    except A.AccountError as e:
        raise _err(e) from None
    auth.set_cookie(response, A.create_session(u.id, request.headers.get("user-agent", "")))  # ...except this one
    return {"ok": True}


class DeleteIn(BaseModel):
    password: str = Field(max_length=1024)


@router.post("/delete-account")
async def delete_account(body: DeleteIn, request: Request, response: Response) -> dict[str, Any]:
    """Delete your account and every photo, measurement and export in it."""
    u = auth.current_user(request)
    if u is None:
        raise HTTPException(404, "no accounts in the desktop app")
    A = _accounts()
    if await run_in_threadpool(A.verify_login, u.email, body.password) is None:
        raise HTTPException(401, "Wrong password.")
    if u.is_admin and sum(1 for x in A.list_users() if x.is_admin) == 1:
        raise HTTPException(409, "You're the only admin, so this account can't be deleted here.")
    await run_in_threadpool(A.delete_user, u.id)
    auth.clear_cookie(response)
    return {"ok": True}


@router.get("/usage")
def usage(request: Request) -> dict[str, Any]:
    u = auth.current_user(request)
    if u is None:
        return {"used_mb": None, "quota_mb": None}
    A = _accounts()
    return {"used_mb": round(A.library_bytes(u.id) / (1 << 20), 1), "quota_mb": A.quota_bytes(u) >> 20}
