"""Body metrics: nearest-date matching, CSV import (long and wide), unit conversion."""

from __future__ import annotations

import bisect
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlmodel import Session, select

from ..models import Metric, MetricEntry

LB_PER_KG = 1 / 0.45359237

PRESETS: list[dict[str, Any]] = [
    {"name": "Weight", "unit": "lb", "kind": "weight", "decimals": 1},
    {"name": "Body fat", "unit": "%", "kind": "bodyfat", "decimals": 1},
    {"name": "Waist", "unit": "in", "kind": "length", "decimals": 1},
    {"name": "Arm", "unit": "in", "kind": "length", "decimals": 2},
    {"name": "Chest", "unit": "in", "kind": "length", "decimals": 1},
]


# ----------------------------------------------------------------------------- nearest matching


@dataclass
class Match:
    value: float
    date: date
    offset_days: int  # entry date - target date (negative = earlier)


def nearest(entries: list[tuple[date, float]], target: date, window_days: int) -> Match | None:
    """entries sorted by date. Exact match wins; otherwise the closest entry within the
    window; on a tie the earlier entry wins (a weigh-in before the photo is the more
    natural pairing)."""
    if not entries:
        return None
    dates = [d for d, _ in entries]
    i = bisect.bisect_left(dates, target)
    cands = []
    if i < len(entries):
        cands.append(entries[i])
    if i > 0:
        cands.append(entries[i - 1])
    best = None
    for d, v in cands:
        off = (d - target).days
        if abs(off) > window_days:
            continue
        key = (abs(off), 0 if off <= 0 else 1)
        if best is None or key < best[0]:
            best = (key, Match(v, d, off))
    return best[1] if best else None


def offset_label(off: int | None) -> str | None:
    if off is None:
        return None
    if off == 0:
        return "same day"
    n = abs(off)
    return f"{n} day{'s' if n != 1 else ''} {'earlier' if off < 0 else 'later'}"


def _series(s: Session, metric_id: int) -> list[tuple[date, float]]:
    rows = s.exec(select(MetricEntry.date, MetricEntry.value).where(MetricEntry.metric_id == metric_id).order_by(MetricEntry.date)).all()
    return [(d, v) for d, v in rows]


def stats_at(
    s: Session,
    target: date,
    window_days: int,
    *,
    first_date: date | None = None,
    metric_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    stmt = select(Metric).order_by(Metric.sort_order, Metric.id)
    if metric_ids:
        stmt = stmt.where(Metric.id.in_(metric_ids))
    out = []
    for m in s.exec(stmt).all():
        series = _series(s, m.id)
        hit = nearest(series, target, window_days)
        first = nearest(series, first_date, window_days) if first_date else None
        out.append(
            {
                "metric_id": m.id,
                "name": m.name,
                "unit": m.unit,
                "decimals": m.decimals,
                "value": hit.value if hit else None,
                "date": hit.date.isoformat() if hit else None,
                "offset_days": hit.offset_days if hit else None,
                "offset_label": offset_label(hit.offset_days) if hit else None,
                "first_value": first.value if first else None,
                "change_since_first": round(hit.value - first.value, 4) if hit and first else None,
            }
        )
    return out


def stats_for_dates(s: Session, targets: list[date], window_days: int, first_date: date | None) -> list[list[dict[str, Any]]]:
    """stats_at for many dates with each metric series loaded once (viewer sequences)."""
    metrics = s.exec(select(Metric).order_by(Metric.sort_order, Metric.id)).all()
    series = {m.id: _series(s, m.id) for m in metrics}
    firsts = {m.id: (nearest(series[m.id], first_date, window_days) if first_date else None) for m in metrics}
    out = []
    for t in targets:
        row = []
        for m in metrics:
            hit = nearest(series[m.id], t, window_days)
            if hit is None:
                continue
            f = firsts[m.id]
            row.append(
                {
                    "metric_id": m.id,
                    "name": m.name,
                    "unit": m.unit,
                    "decimals": m.decimals,
                    "value": hit.value,
                    "date": hit.date.isoformat(),
                    "offset_days": hit.offset_days,
                    "offset_label": offset_label(hit.offset_days),
                    "first_value": f.value if f else None,
                    "change_since_first": round(hit.value - f.value, 4) if f else None,
                }
            )
        out.append(row)
    return out


# ----------------------------------------------------------------------------- units


def convert_weight(value: float, from_unit: str, to_unit: str) -> float:
    if from_unit == to_unit:
        return value
    if from_unit == "kg" and to_unit == "lb":
        return value * LB_PER_KG
    if from_unit == "lb" and to_unit == "kg":
        return value / LB_PER_KG
    raise ValueError(f"cannot convert {from_unit} to {to_unit}")


def set_weight_units(s: Session, unit: str) -> int:
    """Convert every weight-kind metric to `unit`. Returns the number of entries converted."""
    n = 0
    for m in s.exec(select(Metric).where(Metric.kind == "weight")).all():
        if m.unit == unit or m.unit not in ("lb", "kg"):
            continue
        for e in s.exec(select(MetricEntry).where(MetricEntry.metric_id == m.id)).all():
            e.value = round(convert_weight(e.value, m.unit, unit), 3)
            s.add(e)
            n += 1
        m.unit = unit
        s.add(m)
    s.commit()
    return n


# ----------------------------------------------------------------------------- CSV

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y")
_NUM = re.compile(r"^\s*([-+]?\d+(?:[.,]\d+)?)\s*([a-zA-Z%]*)\s*$")


def parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_value(raw: str) -> float | None:
    m = _NUM.match(raw or "")
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


@dataclass
class CsvResult:
    format: str = "unknown"
    rows: list[dict[str, Any]] = field(default_factory=list)  # parsed (date, metric, value, note, line)
    errors: list[dict[str, Any]] = field(default_factory=list)  # line, error, raw
    new_metrics: list[str] = field(default_factory=list)
    inserted: int = 0
    updated: int = 0

    def as_dict(self, preview: int = 50) -> dict[str, Any]:
        return {
            "format": self.format,
            "rows": len(self.rows),
            "preview": self.rows[:preview],
            "errors": self.errors[:200],
            "error_count": len(self.errors),
            "new_metrics": self.new_metrics,
            "inserted": self.inserted,
            "updated": self.updated,
        }


def parse_csv(text: str) -> CsvResult:
    res = CsvResult()
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        res.errors.append({"line": 1, "error": "empty file"})
        return res
    low = [h.lower() for h in header]
    if "date" not in low:
        res.errors.append({"line": 1, "error": "header needs a 'date' column"})
        return res
    di = low.index("date")
    if "metric" in low and "value" in low:
        res.format = "long"
        mi, vi = low.index("metric"), low.index("value")
        ni = low.index("note") if "note" in low else None
        for line, row in enumerate(reader, start=2):
            if not any(c.strip() for c in row):
                continue
            try:
                d = parse_date(row[di])
                name = row[mi].strip()
                v = parse_value(row[vi])
            except IndexError:
                res.errors.append({"line": line, "error": "missing columns", "raw": ",".join(row)})
                continue
            if d is None:
                res.errors.append({"line": line, "error": f"bad date {row[di]!r}", "raw": ",".join(row)})
            elif not name:
                res.errors.append({"line": line, "error": "empty metric name", "raw": ",".join(row)})
            elif v is None:
                res.errors.append({"line": line, "error": f"bad value {row[vi]!r}", "raw": ",".join(row)})
            else:
                note = row[ni].strip() if ni is not None and ni < len(row) and row[ni].strip() else None
                res.rows.append({"line": line, "date": d.isoformat(), "metric": name, "value": v, "note": note})
    else:
        res.format = "wide"
        cols = [(i, h) for i, h in enumerate(header) if i != di and h and h.lower() != "note"]
        ni = low.index("note") if "note" in low else None
        for line, row in enumerate(reader, start=2):
            if not any(c.strip() for c in row):
                continue
            d = parse_date(row[di]) if di < len(row) else None
            if d is None:
                res.errors.append({"line": line, "error": f"bad date {row[di] if di < len(row) else ''!r}", "raw": ",".join(row)})
                continue
            note = row[ni].strip() if ni is not None and ni < len(row) and row[ni].strip() else None
            for i, name in cols:
                cell = row[i].strip() if i < len(row) else ""
                if not cell:
                    continue
                v = parse_value(cell)
                if v is None:
                    res.errors.append({"line": line, "error": f"bad value {cell!r} for {name}", "raw": ",".join(row)})
                    continue
                res.rows.append({"line": line, "date": d.isoformat(), "metric": name, "value": v, "note": note})
    return res


def _metric_by_name(s: Session) -> dict[str, Metric]:
    return {m.name.lower(): m for m in s.exec(select(Metric)).all()}


def _strip_unit(name: str) -> tuple[str, str]:
    """'Weight (lb)' -> ('Weight', 'lb')."""
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", name)
    return (m.group(1).strip(), m.group(2).strip()) if m else (name.strip(), "")


def import_csv(s: Session, text: str, *, dry_run: bool) -> CsvResult:
    res = parse_csv(text)
    known = _metric_by_name(s)
    new: dict[str, Metric] = {}
    for r in res.rows:
        base, unit = _strip_unit(r["metric"])
        key = base.lower()
        r["metric"] = base
        if key not in known and key not in new:
            preset = next((p for p in PRESETS if p["name"].lower() == key), None)
            new[key] = Metric(
                name=base,
                unit=unit or (preset["unit"] if preset else ""),
                kind=preset["kind"] if preset else "custom",
                decimals=preset["decimals"] if preset else 1,
                sort_order=len(known) + len(new),
            )
    res.new_metrics = [m.name for m in new.values()]
    existing_pairs: set[tuple[int, date]] = set()
    if not dry_run:
        for m in new.values():
            s.add(m)
        s.commit()
        known.update({k: m for k, m in new.items()})
    for r in res.rows:
        m = known.get(r["metric"].lower())
        d = date.fromisoformat(r["date"])
        if m is None:  # dry run, metric would be created
            res.inserted += 1
            continue
        row = s.exec(select(MetricEntry).where(MetricEntry.metric_id == m.id, MetricEntry.date == d)).first()
        if row is None and (m.id, d) not in existing_pairs:
            res.inserted += 1
            existing_pairs.add((m.id, d))
            if not dry_run:
                s.add(MetricEntry(metric_id=m.id, date=d, value=r["value"], note=r["note"]))
                s.flush()
        else:
            res.updated += 1
            if not dry_run and row is not None:
                row.value = r["value"]
                row.note = r["note"] or row.note
                s.add(row)
    if not dry_run:
        s.commit()
    return res


SAMPLE_CSV_LONG = """date,metric,value,note
2024-01-01,Weight,182.4,morning fasted
2024-01-08,Weight,181.0,
2024-01-01,Body fat,15.2,
2024-01-15,Waist,33.5,at navel
"""

SAMPLE_CSV_WIDE = """date,Weight,Body fat,Waist,note
2024-01-01,182.4,15.2,33.5,morning fasted
2024-01-08,181.0,,,
2024-01-15,180.2,14.8,33.1,
"""
