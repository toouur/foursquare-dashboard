# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Playwright E2E checks for the stats extras (explore / frontiers / rhythms) on
the live site: every card the data can fill is shown and filled, the regions map
draws outlines, tabs jump to their cards below the sticky bar, the page no longer
ships the index map payload, and nothing throws.

Marked `live` + `e2e` like test_e2e_smoke.py (weekly job, never offline).
"""
import json
import re

import pytest

pytestmark = [pytest.mark.live, pytest.mark.e2e]

BASE = "https://4sq.pages.dev"

# card id → the element inside it that must have content when the card shows
CARDS = {
    "places": {"exRegCard": "exRegList", "exBordCard": "exBordPairs", "exSoundCard": "exSoundCtry",
               "exLeftCard": "exRetLoyal"},
    "time": {"exZoneCard": "exZoneList", "exStreakCard": "exStreakKpi", "exLagCard": "exLagTbl",
             "exHolCard": "exHolNye", "exLuckCard": "exLuckKpi", "exMoodCard": "exMoodCols"},
}


def goto(page, path):
    try:
        return page.goto(BASE + path, wait_until="load")
    except Exception as e:  # net::ERR_* — offline, DNS, etc.
        pytest.skip(f"live site unreachable: {e}")


@pytest.mark.parametrize("tab", sorted(CARDS))
def test_explore_cards_render(page, tab):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    goto(page, f"/stats.html#{tab}")
    page.wait_for_timeout(1500)
    shown = 0
    for card, body in CARDS[tab].items():
        if page.eval_on_selector(f"#{card}", "e => e.style.display") == "none":
            continue                     # no data for it on this build: hidden, not broken
        shown += 1
        text = page.inner_text(f"#{body}").strip()
        assert text and "undefined" not in text and "NaN" not in text, f"{card}: {text[:80]!r}"
    assert shown, f"no explore card shown on #{tab}"
    assert not errors, errors[:3]


def test_stats_payload_is_only_what_the_page_reads(page):
    goto(page, "/stats.html")
    keys = page.evaluate("Object.keys(S)")
    assert "unique_places" not in keys and "venues_heatmap" not in keys
    assert "explore" in keys


def test_regions_map_draws_outlines(page):
    goto(page, "/stats.html#places")
    page.eval_on_selector("#exRegMap", "e => e.scrollIntoView()")
    page.wait_for_selector("#exRegMap .leaflet-overlay-pane canvas, #exRegMap .leaflet-overlay-pane path",
                           timeout=20000)
    assert page.eval_on_selector("#exRegMap", "e => e.querySelectorAll('.ex-map-wait').length") == 0


def test_anchor_lands_below_the_sticky_tabs(page):
    goto(page, "/stats.html#heights")
    page.wait_for_timeout(1200)
    title_top = page.eval_on_selector("#exEdgesCard .card-title", "e => e.getBoundingClientRect().top")
    bar_bottom = page.eval_on_selector("#stTabs", "e => e.getBoundingClientRect().bottom")
    assert title_top >= bar_bottom - 2


def test_regions_map_file_is_light():
    import urllib.request
    try:
        with urllib.request.urlopen(BASE + "/regions-map.json", timeout=30) as r:
            body = r.read()
    except Exception as e:
        pytest.skip(f"live site unreachable: {e}")
    g = json.loads(body)
    assert g["type"] == "FeatureCollection" and g["features"]
    assert len(body) < 1_000_000                       # simplified to 2 decimals
    assert not re.search(rb"\d\.\d{3}\d", body[:20000])  # no 4-decimal coordinates
