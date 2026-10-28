# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for audit_report.py — which venues and categories it surfaces."""
import audit_report as ar

CFG = {"category_groups": {"Food": ["Café", "Coffee Shop"], "Shops": ["Automotive Repair Shop"],
                           "Places": ["Plaza"]},
       "explorer_groups": {"Cafés & Restaurants": ["Café"], "Coffee Shops": ["Coffee Shop"],
                           "Personal Services": ["Automotive Repair Shop"], "Plazas": ["Plaza"]}}


def _r(vid, venue, category, city="Minsk"):
    return {"venue_id": vid, "venue": venue, "category": category, "city": city}


def test_suspects_skip_reviewed_pinned_and_named_after_places():
    rows = [_r("a", "Белоруснефть", "Automotive Repair Shop")] * 2 \
        + [_r("b", "Coffee Lab", "Café")] * 2 \
        + [_r("c", "Coffee Square", "Plaza")] * 2 \
        + [_r("d", "Coffee Point", "Café")] * 2 \
        + [_r("e", "Coffee Pin", "Café")] * 2 \
        + [_r("f", "Coffee Once", "Café")]
    got = ar.category_suspects(rows, CFG, pins={"e": 1}, ok={"d"})
    assert [(s["venue_id"], s["looks_like"]) for s in got] == [("a", "Fuel Stations"), ("b", "Coffee Shops")]


def test_unknown_categories_and_quiet_render():
    rows = [_r("a", "x", "Café"), _r("b", "y", "Public Bathroom"), _r("c", "z", "Public Bathroom")]
    assert ar.unknown_categories(rows, CFG) == [("Public Bathroom", 2)]
    assert "Nothing new" in ar.render([], [], [])
    assert "Public Bathroom × 2" in ar.render([], [("Public Bathroom", 2)], [])
