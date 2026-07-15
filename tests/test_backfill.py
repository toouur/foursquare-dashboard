# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for build_backfill.py — the pre-2012 "refurbished" backfill
converter (backfill.yaml -> backfill.csv in the Foursquare 23-column schema).

Covers:
  - coarse date parsing (day / month-only / year-only / date object)
  - centroid reuse from an existing checkins.csv (offline geocode)
  - gazetteer fallback + explicit-coord override + unresolved handling
  - the ``source_app == "refurbished"`` marker + ``rf`` id scheme
  - exact 23-column schema on emitted rows
"""
import csv
from datetime import date, datetime, timezone

import build_backfill as bb


# ── date parsing ──────────────────────────────────────────────────────────
def _ts(y, mo, d):
    return int(datetime(y, mo, d, 12, 0, 0, tzinfo=timezone.utc).timestamp())


def test_parse_full_day_string():
    assert bb.parse_backfill_date("2009-07-14") == _ts(2009, 7, 14)


def test_parse_month_only_fills_first():
    assert bb.parse_backfill_date("2009-07") == _ts(2009, 7, 1)


def test_parse_year_only_fills_july_first():
    assert bb.parse_backfill_date("2009") == _ts(2009, 7, 1)


def test_parse_date_object():
    # PyYAML parses an unquoted 2009-07-14 to a datetime.date
    assert bb.parse_backfill_date(date(2009, 7, 14)) == _ts(2009, 7, 14)


def test_parse_all_dates_are_noon_utc():
    ts = bb.parse_backfill_date("2010-03-02")
    assert datetime.fromtimestamp(ts, tz=timezone.utc).hour == 12


def test_parse_bad_date_raises():
    import pytest
    with pytest.raises(ValueError):
        bb.parse_backfill_date("not-a-date")


# ── centroid reuse ────────────────────────────────────────────────────────
def _write_checkins(path, rows):
    fields = ["city", "lat", "lng"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_load_centroids_means_per_city(tmp_path):
    csv_path = tmp_path / "checkins.csv"
    _write_checkins(csv_path, [
        {"city": "Prague", "lat": "50.00", "lng": "14.00"},
        {"city": "Prague", "lat": "50.20", "lng": "14.40"},
        {"city": "Brno",   "lat": "49.19", "lng": "16.61"},
    ])
    cen = bb.load_centroids(csv_path)
    assert cen["prague"] == (50.10, 14.20)
    assert cen["brno"] == (49.19, 16.61)


def test_load_centroids_missing_file_is_empty(tmp_path):
    assert bb.load_centroids(tmp_path / "nope.csv") == {}


def test_resolve_prefers_explicit_then_centroid_then_gazetteer():
    centroids = {"prague": (50.10, 14.20)}
    gaz = {"prague": (1.0, 2.0), "atlantis": (0.0, 0.0)}
    # explicit wins
    assert bb.resolve_coords(
        {"city": "Prague", "lat": 9.9, "lng": 8.8}, centroids, gaz) == (9.9, 8.8)
    # centroid beats gazetteer
    assert bb.resolve_coords({"city": "Prague"}, centroids, gaz) == (50.10, 14.20)
    # gazetteer fallback for a city not in checkins
    assert bb.resolve_coords({"city": "Atlantis"}, centroids, gaz) == (0.0, 0.0)
    # nothing resolves -> None
    assert bb.resolve_coords({"city": "Nowhere"}, centroids, gaz) is None


# ── row emission: schema, marker, ids ─────────────────────────────────────
def test_build_rows_schema_marker_and_ids():
    entries = [
        {"date": "2011-05-02", "city": "Vienna",  "country": "Austria"},
        {"date": "2009-07-14", "city": "Prague",  "country": "Czechia",
         "note": "Interrail"},
    ]
    centroids = {"vienna": (48.2, 16.37), "prague": (50.08, 14.44)}
    rows, unresolved = bb.build_rows(entries, centroids, {})
    assert unresolved == []
    assert len(rows) == 2

    # every row carries the exact 23-column schema, no extras/missing
    for r in rows:
        assert list(r.keys()) == bb.SCHEMA
        assert r["source_app"] == "refurbished"
        assert r["city_inferred"] == "1"
        assert r["venue_id"] == ""

    # date-sorted -> Prague (2009) is rf0001, Vienna (2011) is rf0002
    assert rows[0]["checkin_id"] == "rf0001"
    assert rows[0]["city"] == "Prague"
    assert rows[0]["shout"] == "Interrail"
    assert rows[1]["checkin_id"] == "rf0002"
    assert rows[1]["city"] == "Vienna"
    # coords formatted to 6 dp
    assert rows[0]["lat"] == "50.080000"


def test_build_rows_reports_unresolvable():
    entries = [
        {"date": "2009-01-01", "city": "Ghostville"},   # no coords anywhere
        {"date": "2009-01-01"},                          # missing city
    ]
    rows, unresolved = bb.build_rows(entries, {}, {})
    assert rows == []
    assert len(unresolved) == 2


def test_source_provenance_rides_in_source_url():
    entries = [{"date": "2010-01-01", "city": "Prague", "source": "polarsteps"}]
    rows, _ = bb.build_rows(entries, {"prague": (50.08, 14.44)}, {})
    assert rows[0]["source_url"] == "backfill:polarsteps"


def test_write_csv_roundtrips(tmp_path):
    entries = [{"date": "2010-01-01", "city": "Prague", "country": "Czechia"}]
    rows, _ = bb.build_rows(entries, {"prague": (50.08, 14.44)}, {})
    out = tmp_path / "backfill.csv"
    bb.write_csv(rows, out)
    with open(out, encoding="utf-8", newline="") as fh:
        back = list(csv.DictReader(fh))
    assert back[0]["source_app"] == "refurbished"
    assert back[0]["checkin_id"] == "rf0001"
    assert list(back[0].keys()) == bb.SCHEMA
