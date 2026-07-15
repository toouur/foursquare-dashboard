# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for import_polarsteps.py — the Polarsteps export → backfill.yaml
review-file adapter.

Covers:
  - ISO2 → country-name resolution (map hit, then export ``detail`` fallback)
  - city label preference (``display_name`` over the raw ``location.name``)
  - the ``--before`` year window (2012+ steps excluded as Foursquare-era dupes)
  - same-day/one-city dedup keeping the earliest step
  - ``--append`` dedup by ``ps_id`` on a re-run
"""
import datetime as dt
import json

import yaml

import import_polarsteps as ip


def _ts(y, mo, d, h=12):
    return int(dt.datetime(y, mo, d, h, 0, 0, tzinfo=dt.timezone.utc).timestamp())


ISO2 = {"MD": "Moldova", "BY": "Belarus", "UA": "Ukraine"}


# ── country resolution ─────────────────────────────────────────────────────
def test_country_from_iso2_map():
    loc = {"country_code": "by", "detail": "SomethingElse"}
    assert ip.step_country(loc, ISO2) == "Belarus"


def test_country_falls_back_to_detail_when_code_unknown():
    loc = {"country_code": "ZZ", "detail": "Neverland"}
    assert ip.step_country(loc, ISO2) == "Neverland"


# ── city label ─────────────────────────────────────────────────────────────
def test_city_prefers_display_name():
    step = {"display_name": "Lahaza"}
    loc = {"name": "Гайненскі сельскі Савет"}
    assert ip.step_city(step, loc) == "Lahaza"


def test_city_falls_back_to_location_name():
    step = {"display_name": ""}
    loc = {"name": "Minsk"}
    assert ip.step_city(step, loc) == "Minsk"


# ── export fixture ─────────────────────────────────────────────────────────
def _write_export(root, steps, flags=None):
    """Write a minimal Polarsteps export tree with one trip carrying ``steps``."""
    tdir = root / "trip" / "demo_1"
    tdir.mkdir(parents=True)
    (tdir / "trip.json").write_text(
        json.dumps({"name": "Demo trip", "all_steps": steps}), encoding="utf-8")
    flags_path = root / "flags.json"
    flags_path.write_text(json.dumps(flags or {"Moldova": "MD", "Belarus": "BY"}),
                          encoding="utf-8")
    return flags_path


def test_window_excludes_2012_and_later(tmp_path):
    steps = [
        {"id": 1, "start_time": _ts(2011, 6, 5), "display_name": "Minsk",
         "location": {"lat": 53.9, "lon": 27.5, "country_code": "BY"}},
        {"id": 2, "start_time": _ts(2012, 1, 1), "display_name": "Minsk",
         "location": {"lat": 53.9, "lon": 27.5, "country_code": "BY"}},
    ]
    _write_export(tmp_path, steps)
    entries, total, skipped = ip.build_entries(tmp_path, ISO2, before_year=2012)
    assert total == 2
    assert [e["date"] for e in entries] == ["2011-06-05"]


def test_all_years_when_no_window(tmp_path):
    steps = [
        {"id": 1, "start_time": _ts(2011, 6, 5), "display_name": "Minsk",
         "location": {"country_code": "BY"}},
        {"id": 2, "start_time": _ts(2020, 1, 1), "display_name": "Minsk",
         "location": {"country_code": "BY"}},
    ]
    _write_export(tmp_path, steps)
    entries, _total, _skipped = ip.build_entries(tmp_path, ISO2, before_year=None)
    assert len(entries) == 2


def test_same_day_city_dedup_keeps_earliest(tmp_path):
    steps = [
        {"id": 10, "start_time": _ts(2011, 6, 5, h=18), "display_name": "Minsk",
         "location": {"lat": 1.0, "lon": 2.0, "country_code": "BY"},
         "description": "evening"},
        {"id": 11, "start_time": _ts(2011, 6, 5, h=9), "display_name": "Minsk",
         "location": {"lat": 3.0, "lon": 4.0, "country_code": "BY"},
         "description": "morning"},
    ]
    _write_export(tmp_path, steps)
    entries, _total, _skipped = ip.build_entries(tmp_path, ISO2, before_year=2012)
    assert len(entries) == 1
    e = entries[0]
    assert e["note"] == "morning"          # earliest step wins
    assert e["lat"] == 3.0 and e["lng"] == 4.0
    assert e["ps_id"] == 11


def test_step_without_timestamp_skipped(tmp_path):
    steps = [
        {"id": 1, "display_name": "Minsk", "location": {"country_code": "BY"}},
        {"id": 2, "start_time": _ts(2011, 6, 5), "display_name": "Minsk",
         "location": {"country_code": "BY"}},
    ]
    _write_export(tmp_path, steps)
    entries, total, skipped = ip.build_entries(tmp_path, ISO2, before_year=2012)
    assert total == 2 and skipped == 1 and len(entries) == 1


def test_note_defaults_to_trip_name_when_no_description(tmp_path):
    steps = [{"id": 1, "start_time": _ts(2011, 6, 5), "display_name": "Minsk",
              "location": {"country_code": "BY"}}]
    _write_export(tmp_path, steps)
    entries, _total, _skipped = ip.build_entries(tmp_path, ISO2, before_year=2012)
    assert entries[0]["note"] == "Demo trip"
    assert entries[0]["trip"] == "Demo trip"


# ── write / append ─────────────────────────────────────────────────────────
def test_write_then_append_dedups_by_ps_id(tmp_path):
    out = tmp_path / "review.yaml"
    e1 = {"date": "2011-06-05", "city": "Minsk", "country": "Belarus",
          "source": "polarsteps", "ps_id": 1}
    e2 = {"date": "2011-06-06", "city": "Mir", "country": "Belarus",
          "source": "polarsteps", "ps_id": 2}
    n = ip.write_yaml([e1], out, append=False)
    assert n == 1
    # re-run adds only the new step, not the already-present one
    n2 = ip.write_yaml([e1, e2], out, append=True)
    assert n2 == 1
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert [d["ps_id"] for d in data] == [1, 2]


def test_iso2_map_inverts_flags(tmp_path):
    flags = tmp_path / "f.json"
    flags.write_text(json.dumps({"Moldova": "MD", "Belarus": "BY"}), encoding="utf-8")
    m = ip.load_iso2_map(flags)
    assert m["MD"] == "Moldova" and m["BY"] == "Belarus"
