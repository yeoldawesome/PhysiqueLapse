"""Landmark extraction trigger."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..jobs import job_to_dict, runner

router = APIRouter(prefix="/landmarks", tags=["landmarks"])


@router.post("/extract")
def extract(body: dict | None = None) -> dict[str, Any]:
    body = body or {}
    params = {"ids": body.get("ids"), "force": bool(body.get("force", False))}
    try:
        return job_to_dict(runner().enqueue("extract_landmarks", params))
    except KeyError:
        raise HTTPException(501, "landmark extraction is not available") from None
