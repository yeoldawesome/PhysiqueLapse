"""Admin: invite codes and accounts (server mode, admins only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import auth, config, mailer

router = APIRouter(prefix="/admin", tags=["admin"])


def _admin(request: Request):
    if not config.auth_required():
        raise HTTPException(404, "no accounts in the desktop app")
    return auth.require_admin(request)


def _A():
    from .. import accounts

    return accounts


class InviteIn(BaseModel):
    note: str = Field("", max_length=120)
    uses: int = Field(1, ge=1, le=100)
    days: int | None = Field(14, ge=1, le=365)


@router.get("/invites")
def list_invites(_=Depends(_admin)) -> list[dict[str, Any]]:
    return _A().list_invites()


@router.post("/invites")
def create_invite(body: InviteIn, request: Request, admin=Depends(_admin)) -> dict[str, Any]:
    code, iid = _A().create_invite(body.note, body.uses, body.days, admin.id)
    base = config.PUBLIC_URL or str(request.base_url).rstrip("/")
    return {"id": iid, "code": code, "link": f"{base}/signup?invite={code}"}


@router.delete("/invites/{invite_id}")
def revoke_invite(invite_id: int, _=Depends(_admin)) -> dict[str, Any]:
    _A().revoke_invite(invite_id)
    return {"ok": True}


@router.get("/users")
def list_users(_=Depends(_admin)) -> list[dict[str, Any]]:
    A = _A()
    return [
        {**u.public(), "disabled": u.disabled, "created_at": u.created_at, "quota_mb": A.quota_bytes(u) >> 20,
         "used_mb": round(A.library_bytes(u.id) / (1 << 20), 1)}
        for u in A.list_users()
    ]


class UserPatch(BaseModel):
    disabled: bool | None = None
    quota_mb: int | None = Field(None, ge=100, le=1_000_000)


def _user_or_404(uid: int):
    u = _A().get_user(uid)
    if u is None:
        raise HTTPException(404, "account not found")
    return u


@router.patch("/users/{uid}")
def patch_user(uid: int, body: UserPatch, admin=Depends(_admin)) -> dict[str, Any]:
    A = _A()
    _user_or_404(uid)
    if body.disabled is not None:
        if uid == admin.id and body.disabled:
            raise HTTPException(409, "You can't disable your own account.")
        A.set_disabled(uid, body.disabled)
    if "quota_mb" in body.model_fields_set:
        A.set_quota(uid, body.quota_mb)
    return {"ok": True}


@router.post("/users/{uid}/reset-link")
async def reset_link(uid: int, request: Request, _=Depends(_admin)) -> dict[str, Any]:
    """A password-reset link for this account: emailed when email is set up, and returned so
    you can pass it on yourself either way."""
    A = _A()
    u = _user_or_404(uid)
    link = mailer.reset_link(A.create_reset(u.id), str(request.base_url))
    sent = False
    if mailer.configured():
        try:
            await run_in_threadpool(mailer.send_reset, u.email, u.name, link)
            sent = True
        except Exception:
            sent = False
    return {"link": link, "emailed": sent}


@router.delete("/users/{uid}")
async def delete_user(uid: int, admin=Depends(_admin)) -> dict[str, Any]:
    if uid == admin.id:
        raise HTTPException(409, "Delete your own account from the Account page.")
    _user_or_404(uid)
    await run_in_threadpool(_A().delete_user, uid)
    return {"ok": True}
