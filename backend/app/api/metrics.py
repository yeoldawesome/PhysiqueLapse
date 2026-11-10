"""Body metrics: CRUD, entries, CSV import, stats at a date."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from ..db import get_session
from ..models import Metric, MetricEntry
from ..services import stats as ST
from ..settings import load_settings

router = APIRouter(tags=["metrics"])


class MetricIn(BaseModel):
    name: str | None = None
    unit: str | None = None
    decimals: int | None = None
    sort_order: int | None = None
    kind: str | None = None


class EntryIn(BaseModel):
    metric_id: int
    date: date
    value: float
    note: str | None = None


def _metric_out(s: Session, m: Metric) -> dict[str, Any]:
    n = s.exec(select(func.count(MetricEntry.id)).where(MetricEntry.metric_id == m.id)).one()
    last = s.exec(select(MetricEntry).where(MetricEntry.metric_id == m.id).order_by(MetricEntry.date.desc())).first()
    return {
        "id": m.id,
        "name": m.name,
        "unit": m.unit,
        "is_builtin": m.is_builtin,
        "sort_order": m.sort_order,
        "decimals": m.decimals,
        "kind": m.kind,
        "entry_count": n,
        "latest": {"date": last.date.isoformat(), "value": last.value} if last else None,
    }


def _entry_out(e: MetricEntry) -> dict[str, Any]:
    return {"id": e.id, "metric_id": e.metric_id, "date": e.date.isoformat(), "value": e.value, "note": e.note}


@router.get("/metrics")
def list_metrics(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [_metric_out(s, m) for m in s.exec(select(Metric).order_by(Metric.sort_order, Metric.id)).all()]


@router.get("/metrics/presets")
def presets(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    have = {m.name.lower() for m in s.exec(select(Metric)).all()}
    units = load_settings(s).units
    out = []
    for p in ST.PRESETS:
        if p["name"].lower() in have:
            continue
        q = dict(p)
        if q["kind"] == "length" and units == "kg":
            q["unit"] = "cm"
        out.append(q)
    return out


@router.post("/metrics")
def create_metric(body: MetricIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(422, "name is required")
    if s.exec(select(Metric).where(func.lower(Metric.name) == name.lower())).first():
        raise HTTPException(409, "a metric with that name exists")
    kind = body.kind if body.kind in ("weight", "bodyfat", "length", "custom") else "custom"
    max_order = s.exec(select(func.max(Metric.sort_order))).one() or 0
    m = Metric(name=name, unit=(body.unit or "").strip(), decimals=body.decimals if body.decimals is not None else 1, kind=kind, sort_order=max_order + 1)
    s.add(m)
    s.commit()
    s.refresh(m)
    return _metric_out(s, m)


@router.patch("/metrics/{mid}")
def patch_metric(mid: int, body: MetricIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    m = s.get(Metric, mid)
    if m is None:
        raise HTTPException(404, "metric not found")
    if body.name and body.name.strip():
        m.name = body.name.strip()
    if body.unit is not None and m.kind != "weight":
        m.unit = body.unit.strip()
    if body.decimals is not None:
        m.decimals = max(0, min(4, body.decimals))
    if body.sort_order is not None:
        m.sort_order = body.sort_order
    s.add(m)
    s.commit()
    s.refresh(m)
    return _metric_out(s, m)


@router.delete("/metrics/{mid}")
def delete_metric(mid: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    m = s.get(Metric, mid)
    if m is None:
        raise HTTPException(404, "metric not found")
    if m.is_builtin:
        raise HTTPException(422, "the built-in weight metric cannot be deleted")
    for e in s.exec(select(MetricEntry).where(MetricEntry.metric_id == mid)).all():
        s.delete(e)
    s.delete(m)
    s.commit()
    return {"deleted": mid}


@router.get("/metrics/entries")
def list_entries(
    metric_id: int | None = None,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    s: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    stmt = select(MetricEntry)
    if metric_id:
        stmt = stmt.where(MetricEntry.metric_id == metric_id)
    if from_:
        stmt = stmt.where(MetricEntry.date >= from_)
    if to:
        stmt = stmt.where(MetricEntry.date <= to)
    return [_entry_out(e) for e in s.exec(stmt.order_by(MetricEntry.date.desc(), MetricEntry.id)).all()]


@router.post("/metrics/entries")
def upsert_entry(body: EntryIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    if s.get(Metric, body.metric_id) is None:
        raise HTTPException(404, "metric not found")
    e = s.exec(select(MetricEntry).where(MetricEntry.metric_id == body.metric_id, MetricEntry.date == body.date)).first()
    if e is None:
        e = MetricEntry(metric_id=body.metric_id, date=body.date, value=body.value, note=body.note)
    else:
        e.value = body.value
        e.note = body.note
    s.add(e)
    s.commit()
    s.refresh(e)
    return _entry_out(e)


@router.delete("/metrics/entries/{eid}")
def delete_entry(eid: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    e = s.get(MetricEntry, eid)
    if e is None:
        raise HTTPException(404, "entry not found")
    s.delete(e)
    s.commit()
    return {"deleted": eid}


@router.post("/metrics/import-csv")
async def import_csv(file: UploadFile = File(...), dry_run: bool = True, s: Session = Depends(get_session)) -> dict[str, Any]:
    raw = await file.read()
    if len(raw) > 10_000_000:
        raise HTTPException(413, "CSV larger than 10 MB")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    res = ST.import_csv(s, text, dry_run=dry_run)
    out = res.as_dict()
    out["dry_run"] = dry_run
    return out


@router.get("/metrics/sample-csv")
def sample_csv(format: str = "long") -> PlainTextResponse:
    body = ST.SAMPLE_CSV_WIDE if format == "wide" else ST.SAMPLE_CSV_LONG
    return PlainTextResponse(body, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="metrics_sample_{format}.csv"'})


@router.get("/stats/at")
def stats_at(
    date_: date = Query(..., alias="date"),
    window_days: int | None = None,
    first: date | None = None,
    s: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    w = window_days if window_days is not None else load_settings(s).stats_window_days
    return ST.stats_at(s, date_, w, first_date=first)
