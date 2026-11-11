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
