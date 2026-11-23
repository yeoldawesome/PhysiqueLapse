"""Per-account storage allowance (server mode). The desktop app has no limit."""

from __future__ import annotations

from fastapi import HTTPException, Request

from . import auth


def check(request: Request, extra_bytes: int = 0) -> None:
    """Refuse an upload that would take the account past its allowance."""
    u = auth.current_user(request)
    if u is None:
        return
    from . import accounts

    used, cap = accounts.library_bytes(u.id), accounts.quota_bytes(u)
    if used + extra_bytes > cap:
        raise HTTPException(
            413,
            f"Your storage is full ({used >> 20} of {cap >> 20} MB used). Delete some photos or ask the admin for more space.",
        )


def added(request: Request, nbytes: int) -> None:
    u = auth.current_user(request)
    if u is not None:
        from . import accounts

        accounts.note_added(u.id, nbytes)
