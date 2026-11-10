"""Body-size estimates from photos: run, per-photo values + overlay, series with trend and calibration."""

from __future__ import annotations

import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlmodel import Session

from ..db import get_session
from ..jobs import job_to_dict, runner
from ..models import Photo, PhotoMeasurement
from ..services import measure as M
from ..settings import load_settings

router = APIRouter(tags=["measurements"])


@router.get("/measurements/summary")
def summary(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return M.summary(s)


@router.get("/measurements/series")
def series(name: str, pose_id: int | None = None, smooth_days: int | None = None, s: Session = Depends(get_session)) -> dict[str, Any]:
    st = load_settings(s)
    try:
        return M.series(s, name, pose_id, smooth_days or st.measure_smooth_days, st.stats_window_days)
    except KeyError:
        raise HTTPException(404, f"unknown measurement {name}") from None


@router.post("/measurements/run")
def run(body: dict | None = None) -> dict[str, Any]:
    body = body or {}
    return job_to_dict(runner().enqueue("measure_photos", {"force": bool(body.get("force")), "ids": body.get("ids")}))


@router.get("/photos/{photo_id}/measurements")
def photo_measurements(photo_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    if s.get(Photo, photo_id) is None:
        raise HTTPException(404, "photo not found")
    pm = s.get(PhotoMeasurement, photo_id)
    if pm is None:
        return {"photo_id": photo_id, "measured": False, "items": {}}
    items = {k: v for k, v in (pm.data or {}).items() if isinstance(v, dict)}
    return {
        "photo_id": photo_id,
        "measured": True,
        "torso_px": pm.torso_px,
        "reason": (pm.data or {}).get("reason"),
        "items": {k: {**v, "label": M.DEFINITIONS.get(k, (k,))[0]} for k, v in items.items()},
    }


@router.get("/photos/{photo_id}/measure-overlay")
def photo_overlay(photo_id: int, s: Session = Depends(get_session)) -> Response:
    p = s.get(Photo, photo_id)
    if p is None:
        raise HTTPException(404, "photo not found")
    img = M.overlay(p, s.get(PhotoMeasurement, photo_id))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})
