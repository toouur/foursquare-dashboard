# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Tests for the month-narrative composer in scripts/gen_year_pages.py.

The composer is deliberately deterministic (phrase pools rotate on yr+mo),
so these tests pin behavior, not exact prose: helpers, determinism, the
death of "(N×)" tallies, sparse/home/roam paths, country ordinals, and the
list/count agreement in the roam lead ("A, B, C and more — four cities").
"""
import re
from collections import Counter
from datetime import datetime, timezone

from conftest import make_row
from gen_year_pages import (
    _cap_html,
    _compose_month_narrative,
    _dayord,
    _nword,
    _oxford,
)

TALLY = re.compile(r"\(\d+×\)")


# ── Input builder ────────────────────────────────────────────────────────

def _month_inputs(specs, yr=2024, mo=3):
    """specs: list of (city, category, venue, day) → composer positionals."""
    rows = []
    for i, (city, cat, venue, day) in enumerate(specs):
        ts = int(datetime(yr, mo, day, 12, 0, tzinfo=timezone.utc).timestamp())
        rows.append(make_row(ts, city=city, category=cat, venue=venue,
                             checkin_id=f"c{i}"))
    mon_venue = Counter(v for _, _, v, _ in specs if v)
    mon_city = Counter(c for c, _, _, _ in specs if c)
    mon_cat = Counter(cat for _, cat, _, _ in specs if cat)
    return rows, mon_venue, mon_city, mon_cat


def _compose(specs, yr=2024, mo=3, **kw):
    rows, mv, mc, mcat = _month_inputs(specs, yr, mo)
    kw.setdefault("days_active", len({d for _, _, _, d in specs}))
    return _compose_month_narrative(yr, mo, rows, mv, mc, mcat, [], **kw)


def _home_month(n=20, city="Minsk", venue="Corner Café", day0=1):
    """n check-ins in one city — the 'home' lead path."""
    return [(city, "Coffee Shop", venue, day0 + i % 25) for i in range(n)]


# ── Helpers ──────────────────────────────────────────────────────────────

def test_nword_small_counts_spelled_out():
    assert _nword(0) == "zero"
    assert _nword(3) == "three"
    assert _nword(12) == "twelve"


def test_nword_large_counts_stay_digits_with_separator():
    assert _nword(13) == "13"
    assert _nword(1500) == "1,500"


def test_dayord():
    assert _dayord(1) == "1st"
    assert _dayord(2) == "2nd"
    assert _dayord(3) == "3rd"
    assert _dayord(4) == "4th"
    # Teens are always 'th', even 11/12/13 and 111–113.
    assert _dayord(11) == "11th"
    assert _dayord(12) == "12th"
    assert _dayord(13) == "13th"
    assert _dayord(21) == "21st"
    assert _dayord(22) == "22nd"
    assert _dayord(111) == "111th"


def test_oxford():
    assert _oxford([]) == ""
    assert _oxford(["A"]) == "A"
    assert _oxford(["A", "B"]) == "A and B"
    assert _oxford(["A", "B", "C"]) == "A, B and C"


def test_cap_html_skips_leading_tags():
    assert _cap_html("hello world") == "Hello world"
    assert _cap_html("<strong>hello</strong> x") == "<strong>Hello</strong> x"


# ── Composer invariants ──────────────────────────────────────────────────

def test_deterministic_across_calls():
    specs = _home_month()
    assert _compose(specs) == _compose(specs)


def test_no_tally_pattern_anywhere():
    # The old text rendered "Venue (7×)" fragments; the composer must not.
    for mo in (1, 5, 9):
        out = _compose(_home_month(), mo=mo)
        assert not TALLY.search(out)


def test_sparse_month_reads_short():
    out = _compose([("Minsk", "Coffee Shop", "Spot", 4)] * 3)
    assert "three" in out.lower()
    assert "Minsk" in out


def test_home_month_names_the_city():
    out = _compose(_home_month())
    assert "<strong>Minsk</strong>" in out
    assert out.endswith(".")


def test_roam_fallback_list_matches_count():
    # yr+mo ≡ 2 (mod 3) hits the "The map hardly sat still" fallback.
    # Four equal cities → three named + "and more", never "and X and more".
    specs = []
    for i, city in enumerate(["Aaa", "Bbb", "Ccc", "Ddd"]):
        specs += [(city, "Restaurant", f"V{city}", 1 + i * 7)] * 8
    out = _compose(specs, yr=2024, mo=3)
    assert ("<strong>Aaa</strong>, <strong>Bbb</strong>, "
            "<strong>Ccc</strong> and more") in out
    assert "four cities in a single month" in out
    assert not re.search(r"and <strong>[^<]+</strong> and more", out)


def test_single_new_country_carries_ordinal():
    out = _compose(_home_month(), new_countries_m=["Japan"],
                   country_ordinals={"Japan": 47})
    assert "<strong>Japan</strong>" in out
    assert "country № 47" in out


def test_two_new_countries_say_both():
    out = _compose(_home_month(), new_countries_m=["Qatar", "Oman"])
    assert "both joined the map" in out
    assert "two new flags" in out


def test_many_new_countries_capitalized_lead():
    out = _compose(_home_month(),
                   new_countries_m=["Qatar", "Oman", "Fiji", "Chad"])
    assert "Four new flags in a single month" in out


def test_trip_ending_names_the_trip():
    out = _compose(_home_month(), trips_ending=[
        {"name": "Big Trip", "start_date": "2023-11-20",
         "end_date": "2024-01-05"}])
    assert "<strong>Big Trip</strong>" in out


def test_trip_starting_names_the_trip():
    out = _compose(_home_month(), trips_starting=[
        {"name": "South Loop", "start_date": "2024-03-05",
         "end_date": "2024-03-19"}])
    assert "<strong>South Loop</strong>" in out
