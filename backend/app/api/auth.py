"""Login / logout / status (server mode). In desktop mode status says no login is needed."""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import Session

from .. import auth, config
from ..db import get_session

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    password: str


@router.get("/status")
def status(request: Request, s: Session = Depends(get_session)) -> dict[str, Any]:
    required = config.auth_required()
    return {
        "mode": config.MODE,
        "auth_required": required,
        "password_set": auth.password_is_set(s) if required else True,
        "logged_in": (not required) or auth.check_session(s, request.cookies.get(auth.COOKIE_NAME)),
    }


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, s: Session = Depends(get_session)) -> dict[str, Any]:
    if not config.auth_required():
        return {"ok": True}
    key = auth.client_key(request)
    wait = auth.limiter.retry_after(key)
    if wait > 0:
        raise HTTPException(429, f"Too many attempts. Try again in {math.ceil(wait)} s.", headers={"Retry-After": str(math.ceil(wait))})
    if not auth.password_is_set(s):
        raise HTTPException(
            409, "No password set yet. On the server run: docker compose exec physiquelapse python -m app.auth set-password"
        )
    if len(body.password) > 1024 or not auth.verify_password(s, body.password):
        auth.limiter.failed(key)
        raise HTTPException(401, "Wrong password.")
    auth.limiter.succeeded(key)
    token = auth.create_session(s, request.headers.get("user-agent", ""))
    auth.set_cookie(response, token)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, s: Session = Depends(get_session)) -> dict[str, Any]:
    auth.end_session(s, request.cookies.get(auth.COOKIE_NAME))
    auth.clear_cookie(response)
    return {"ok": True}
