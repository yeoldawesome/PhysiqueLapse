"""M5: nearest-date matching, CSV import (long + wide + bad rows), unit conversion, API."""

from datetime import date

import pytest

from app.services import stats as ST

D = date.fromisoformat
SERIES = [(D("2024-01-01"), 180.0), (D("2024-01-05"), 179.0), (D("2024-01-09"), 178.0)]


@pytest.mark.parametrize(
    "target,window,expected",
    [
        ("2024-01-05", 7, (179.0, 0)),  # exact
        ("2024-01-06", 7, (179.0, -1)),  # earlier entry closer
        ("2024-01-08", 7, (178.0, 1)),  # later entry closer
        ("2024-01-07", 7, (179.0, -2)),  # tie (2 earlier vs 2 later) -> earlier wins
        ("2023-12-30", 7, (180.0, 2)),  # before the first entry
        ("2024-01-20", 7, None),  # outside window
        ("2024-01-20", 11, (178.0, -11)),  # window inclusive
    ],
)
def test_nearest(target, window, expected):
    m = ST.nearest(SERIES, D(target), window)
    if expected is None:
        assert m is None
    else:
        assert (m.value, m.offset_days) == expected


def test_nearest_empty_and_labels():
    assert ST.nearest([], D("2024-01-01"), 7) is None
    assert ST.offset_label(0) == "same day"
    assert ST.offset_label(-2) == "2 days earlier"
    assert ST.offset_label(1) == "1 day later"


def test_csv_long_with_bad_rows():
    text = """date,metric,value,note
2024-01-01,Weight,182.4,fasted
2024-01-02,Weight,abc,
not-a-date,Weight,180,
2024-01-03,,180,
01/04/2024,Body fat,15.2 %,
2024-01-05,Waist (in),33.5,
"""
    r = ST.parse_csv(text)
    assert r.format == "long"
    assert [row["line"] for row in r.rows] == [2, 6, 7]
    assert r.rows[1] == {"line": 6, "date": "2024-01-04", "metric": "Body fat", "value": 15.2, "note": None}
    assert [e["line"] for e in r.errors] == [3, 4, 5]


def test_csv_wide_semicolon_and_bom():
    text = "﻿date;Weight;Body fat;note\n2024-01-01;182,4;15.1;a\n2024-01-02;181.9;;\nbad;1;2;\n"
    r = ST.parse_csv(text)
    assert r.format == "wide"
    assert [(x["date"], x["metric"], x["value"]) for x in r.rows] == [
        ("2024-01-01", "Weight", 182.4),
        ("2024-01-01", "Body fat", 15.1),
        ("2024-01-02", "Weight", 181.9),
    ]
    assert r.rows[0]["note"] == "a"
    assert len(r.errors) == 1 and r.errors[0]["line"] == 4


def test_csv_needs_date_column():
    r = ST.parse_csv("day,weight\n2024-01-01,1\n")
    assert r.errors and "date" in r.errors[0]["error"]


def test_weight_conversion_roundtrip():
    kg = ST.convert_weight(180.0, "lb", "kg")
    assert kg == pytest.approx(81.6466, abs=1e-3)
    assert ST.convert_weight(kg, "kg", "lb") == pytest.approx(180.0)
    with pytest.raises(ValueError):
        ST.convert_weight(1, "lb", "stone")


def test_import_dry_run_then_commit_and_upsert(session):
    text = "date,Weight,Waist (in)\n2024-01-01,182.4,33.5\n2024-01-08,181.0,\n"
    dry = ST.import_csv(session, text, dry_run=True)
    assert dry.inserted == 3 and dry.new_metrics == ["Waist"]
    from sqlmodel import select

    from app.models import Metric, MetricEntry

    assert session.exec(select(MetricEntry)).all() == []
    res = ST.import_csv(session, text, dry_run=False)
    assert res.inserted == 3 and res.updated == 0
    waist = session.exec(select(Metric).where(Metric.name == "Waist")).one()
    assert waist.unit == "in" and waist.kind == "length"
    again = ST.import_csv(session, "date,metric,value\n2024-01-01,weight,183\n", dry_run=False)
    assert again.updated == 1 and again.inserted == 0
    w = session.exec(select(Metric).where(Metric.kind == "weight")).one()
    e = session.exec(select(MetricEntry).where(MetricEntry.metric_id == w.id, MetricEntry.date == D("2024-01-01"))).one()
    assert e.value == 183


def test_stats_api_and_units_switch(client):
    metrics = client.get("/api/metrics").json()
    weight = next(m for m in metrics if m["kind"] == "weight")
    for d, v in (("2024-01-01", 180.0), ("2024-01-10", 175.0)):
        assert client.post("/api/metrics/entries", json={"metric_id": weight["id"], "date": d, "value": v}).status_code == 200
    # upsert
    client.post("/api/metrics/entries", json={"metric_id": weight["id"], "date": "2024-01-10", "value": 176.0})
    assert len(client.get("/api/metrics/entries", params={"metric_id": weight["id"]}).json()) == 2
    st = client.get("/api/stats/at", params={"date": "2024-01-12", "first": "2024-01-01"}).json()
    w = next(x for x in st if x["metric_id"] == weight["id"])
    assert w["value"] == 176.0 and w["offset_label"] == "2 days earlier" and w["change_since_first"] == -4.0
    st = client.get("/api/stats/at", params={"date": "2024-02-12", "window_days": 7}).json()
    assert next(x for x in st if x["metric_id"] == weight["id"])["value"] is None
    # switching units converts stored weight
    client.put("/api/settings", json={"units": "kg"})
    m = next(m for m in client.get("/api/metrics").json() if m["kind"] == "weight")
    assert m["unit"] == "kg" and m["latest"]["value"] == pytest.approx(79.832, abs=1e-3)
    assert client.put("/api/settings", json={"units": "stone"}).status_code == 422
    # presets, custom metric, csv endpoints
    presets = client.get("/api/metrics/presets").json()
    assert {p["name"] for p in presets} >= {"Body fat", "Waist"} and all(p["unit"] in ("%", "cm") for p in presets)
    r = client.post("/api/metrics", json={"name": "Calf", "unit": "cm"})
    assert r.status_code == 200
    assert client.delete(f"/api/metrics/{weight['id']}").status_code == 422
    assert "date,metric,value" in client.get("/api/metrics/sample-csv").text
    files = {"file": ("m.csv", b"date,Calf\n2024-01-01,40\n2024-01-02,x\n", "text/csv")}
    prev = client.post("/api/metrics/import-csv", params={"dry_run": True}, files=files).json()
    assert prev["dry_run"] and prev["inserted"] == 1 and prev["error_count"] == 1
    files = {"file": ("m.csv", b"date,Calf\n2024-01-01,40\n", "text/csv")}
    done = client.post("/api/metrics/import-csv", params={"dry_run": False}, files=files).json()
    assert done["inserted"] == 1
