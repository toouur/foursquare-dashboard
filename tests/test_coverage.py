# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""metrics.coverage — radius of gyration is measured from the home of that era."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from conftest import make_row
from metrics.coverage import CELL_DEG, compute_coverage

CHI = (47.02, 28.83)
MSQ = (53.90, 27.56)
SWITCH = int(datetime(2015, 1, 1, tzinfo=timezone.utc).timestamp())
ERAS = [(SWITCH, "Chișinău")]          # Chișinău before 2015, Minsk after


def _ts(y, m, d=10):
    return int(datetime(y, m, d, 12, tzinfo=timezone.utc).timestamp())


def _row(ts, city, lat, lng, **kw):
    kw.setdefault("source_app", "Swarm")
    return make_row(ts, city=city, lat=str(lat), lng=str(lng), venue_id=f"v{ts}", **kw)


def _life():
    rows = []
    for m in range(1, 13):
        rows.append(_row(_ts(2014, m), "Chișinău", *CHI))
        rows.append(_row(_ts(2016, m), "Minsk", *MSQ))
    return rows


def test_each_era_is_measured_from_its_own_home():
    years = {y["y"]: y for y in compute_coverage(_life(), "Minsk", ERAS)["years"]}
    # all check-ins sit on their era's home centroid → rg 0, not the ~770 km between them
    assert years[2014]["rg"] == 0 and years[2016]["rg"] == 0


def test_flat_home_misreads_the_other_era():
    years = {y["y"]: y for y in compute_coverage(_life(), "Minsk", [])["years"]}
    assert years[2014]["rg"] > 700


def test_month_grid_shape_and_buckets():
    rows = _life() + [_row(_ts(2016, 7, 20), "Warsaw", 52.23, 21.01)]
    cov = compute_coverage(rows, "Minsk", ERAS)
    assert len(cov["thresholds"]) == 5
    y16 = next(y for y in cov["years"] if y["y"] == 2016)
    assert len(y16["months"]) == 12
    july = y16["months"][6]
    assert july["n"] == 2 and july["rg"] > 200
    assert july["b"] == max(m["b"] for m in y16["months"])
    assert all(1 <= m["b"] <= 5 for m in y16["months"] if m["n"])


def test_empty_months_are_zero():
    rows = [_row(_ts(2016, 3), "Minsk", *MSQ)]
    y = compute_coverage(rows, "Minsk")["years"][0]
    assert [m["n"] for m in y["months"]].count(0) == 11
    assert y["months"][0] == {"n": 0, "rg": 0, "b": 0}


def test_refurbished_and_missing_coords_are_skipped():
    rows = [_row(_ts(2016, 3), "Minsk", *MSQ),
            _row(_ts(2016, 4), "Rome", 41.9, 12.5, source_app="refurbished"),
            _row(_ts(2016, 5), "Minsk", "", ""),
            _row(_ts(2016, 6), "Minsk", 0, 0)]
    cov = compute_coverage(rows, "Minsk")
    assert sum(m["n"] for m in cov["years"][0]["months"]) == 1
    assert [c["city"] for c in cov["cities"]] == ["Minsk"]


def test_city_cells_and_area():
    lat, lng = MSQ
    rows = [_row(_ts(2016, 1, d), "Minsk", lat + (d % 3) * CELL_DEG, lng) for d in range(1, 10)]
    c = compute_coverage(rows, "Minsk")["cities"][0]
    assert c["n"] == 9 and c["cells"] == 3
    assert c["km2"] == pytest.approx(3 * 0.25 * 0.59, abs=0.05)   # cos(53.9°) ≈ 0.59


def test_empty():
    assert compute_coverage([], "Minsk") == {"years": [], "thresholds": [0.0] * 5, "cities": []}
