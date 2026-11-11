# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""metrics.stats.day_anchors — which check-ins make a day in a city / country."""
from __future__ import annotations

import json
from pathlib import Path

from conftest import make_row

from metrics.stats import day_anchors

CONFIG = Path(__file__).resolve().parents[1] / "config"


def r(cat, **kw):
    return make_row(1_700_000_000, city="X", country="Y", category=cat, venue_id=kw.pop("vid", "v"), **kw)


def test_transit_does_not_make_a_day():
    for cat in ("International Airport", "Airport Terminal", "Airport Gate", "Baggage Claim",
                "Border Crossing", "Plane", "Train", "Bus Line", "Boat or Ferry", "Moving Target",
                "Country", "City", "Duty-free Store", "Bridge", "Fuel Station", "Toll Booth", "Road"):
        assert not day_anchors(r(cat)), cat


def test_real_places_and_stations_make_a_day():
    for cat in ("Café", "Hostel", "Park", "Rail Station", "Bus Station", "Metro Station", "Neighborhood"):
        assert day_anchors(r(cat)), cat


def test_reconstructed_rows_keep_their_area_days_but_not_borders():
    assert day_anchors(r("City", source_app="refurbished"))
    assert not day_anchors(r("Border Crossing", source_app="refurbished"))


def test_skip_list_by_venue_id():
    assert not day_anchors(r("Police Station", vid="b"), {"b"})
    skip = json.loads((CONFIG / "day_skip_venues.json").read_text(encoding="utf-8"))
    assert all(len(k) == 24 for k in skip if not k.startswith("_"))


# ── transform.resolve_straddles: a venue between two countries takes your side ──

def _seq(*items):
    return [make_row(1_700_000_000 + 600 * i, city=c, country=k, venue_id=v, category="Bridge")
            for i, (v, k, c) in enumerate(items)]


STR = {"br": {"_name": "bridge", "Moldova": "Ungheni", "Romania": "Ungheni RO"}}


def test_straddle_takes_the_country_of_the_previous_checkin():
    from transform import resolve_straddles
    ro_to_md = _seq(("a", "Romania", "Iași"), ("br", "Romania", "Ungheni RO"), ("b", "Moldova", "Ungheni"))
    md_to_ro = _seq(("b", "Moldova", "Ungheni"), ("br", "Romania", "Ungheni RO"), ("a", "Romania", "Iași"))
    assert resolve_straddles(ro_to_md, STR) == 0 and ro_to_md[1]["country"] == "Romania"
    assert resolve_straddles(md_to_ro, STR) == 1
    assert (md_to_ro[1]["country"], md_to_ro[1]["city"]) == ("Moldova", "Ungheni")


def test_straddle_skips_other_straddles_and_falls_back_to_the_next_side():
    from transform import resolve_straddles
    s = {**STR, "mid": {"Moldova": "Ungheni", "Romania": "Ungheni RO"}}
    rows = _seq(("b", "Moldova", "Ungheni"), ("mid", "Romania", "x"), ("br", "Romania", "x"))
    resolve_straddles(rows, s)
    assert rows[2]["country"] == "Moldova"                       # previous non-straddle is Moldova
    alone = _seq(("br", "Moldova", "Ungheni"), ("a", "Romania", "Iași"))
    resolve_straddles(alone, STR)
    assert alone[0]["country"] == "Moldova"                      # crossing towards Romania: other side
    far = _seq(("br", "Romania", "Ungheni RO"))
    assert resolve_straddles(far, STR) == 0 and far[0]["country"] == "Romania"


def test_straddle_config_is_well_formed():
    cfg = json.loads((CONFIG / "straddle_venues.json").read_text(encoding="utf-8"))
    for k, v in cfg.items():
        if k.startswith("_"):
            continue
        assert len(k) == 24 and len([c for c in v if not c.startswith("_")]) == 2, k
