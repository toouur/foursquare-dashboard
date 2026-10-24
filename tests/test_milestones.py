# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""metrics.milestones — the records board labels history exactly like the echo bot."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import telegram_checkin_echo as echo
from conftest import make_row
from metrics import milestones as ms
from metrics.milestones import compute_milestones, labels_for

DAY = 86400
YEAR = 365 * DAY


def _ts(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def _row(ts, vid, venue="Cafe", city="Chișinău", country="Moldova", category="Café", **kw):
    kw.setdefault("source_app", "Swarm")
    return make_row(ts, city=city, country=country, category=category, venue=venue, venue_id=vid,
                    lat="47.02", lng="28.83", checkin_id=f"c{ts}", **kw)


def test_constants_mirror_the_bot():
    assert ms.INSTANT_MILESTONES == echo.INSTANT_MILESTONES
    assert ms.DIGEST_MILESTONES == echo.DIGEST_MILESTONES
    assert ms.COMEBACK_INSTANT_DAYS == echo.COMEBACK_INSTANT_DAYS
    assert ms.COMEBACK_DIGEST_DAYS == echo.COMEBACK_DIGEST_DAYS
    assert ms.TOTAL_STEP == echo.TOTAL_STEP
    assert ms.REFURB == echo.REFURB
    assert ms.HOME_CATEGORY == echo.HOME_CATEGORY
    assert ms.PASS_THROUGH == set(echo.PASS_THROUGH)
    assert ms.RANK == echo.RANK


def _history():
    t0 = _ts("2013-03-01T12:00:00")
    rows = []
    # 30 visits to one café: 10th and 25th are milestones
    rows += [_row(t0 + i * DAY, "cafe") for i in range(30)]
    # a trip abroad: new country + new city
    rows.append(_row(t0 + 40 * DAY, "warsaw", city="Warsaw", country="Poland"))
    # a bus line passed 50 times: milestone_big demoted to milestone
    rows += [_row(t0 + 50 * DAY + i * 600, "bus", category="Bus Line") for i in range(50)]
    # back at the café after 6 years: comeback_long
    rows.append(_row(t0 + 6 * YEAR, "cafe"))
    # back in Warsaw 2 years after the last visit: city_back + comeback
    rows.append(_row(t0 + 6 * YEAR + 10 * DAY, "warsaw2", city="Warsaw", country="Poland"))
    # home and reconstructed rows are never told
    rows.append(_row(t0 + 6 * YEAR + 20 * DAY, "home", category="Home (private)"))
    rows.append(_row(t0 - YEAR, "old", city="Odesa", country="Ukraine", source_app="refurbished"))
    return rows


def test_counts_match_the_bot_label_for_label():
    rows = _history()
    h = echo.History(sorted(rows, key=lambda r: int(r["date"])))
    want: Counter[str] = Counter()
    for i in range(len(h.rows)):
        if h.is_real(i):
            want.update(echo.events(echo.facts_for(h, i)))
    assert compute_milestones(rows)["counts"] == dict(want)


def test_labels_sorted_by_rank_with_visit_kind_last():
    got = labels_for(first_in_country=True, first_in_city=True, days_since_city=None,
                     days_since_last=None, visit_no=1, checkin_no=2000, category="Bar")
    assert got == ["new_country", "new_city", "total", "new_venue"]


def test_events_skip_plain_visits_and_carry_gaps():
    out = compute_milestones(_history())
    labels = [e["label"] for e in out["events"]]
    assert "visit" not in labels and "new_venue" not in labels
    newest = out["events"][0]
    assert newest["venue_id"] == "warsaw2"          # home row is last but never told
    assert newest["label"] == "city_back" and newest["gap_days"] >= 365
    # 6 years away from the café is also 6 years away from the city, which ranks first
    long_cb = next(e for e in out["events"] if "comeback_long" in e["labels"])
    assert long_cb["label"] == "city_back"
    assert long_cb["venue_id"] == "cafe" and long_cb["gap_days"] >= 5 * 365


def test_records():
    rec = compute_milestones(_history(), distance_from_home=[["2013-04-10", 1100.4], ["2013-04-11", 3]])["records"]
    assert rec["farthest_day"] == {"date": "2013-04-10", "km": 1100}
    assert rec["busiest_day"]["n"] == 50 and rec["busiest_day"]["cities"] == ["Chișinău"]
    assert rec["top_venue"]["venue_id"] == "bus" and rec["top_venue"]["n"] == 50
    assert rec["longest_comeback"]["venue_id"] == "cafe"
    assert rec["longest_silence"]["days"] > 5 * 365
    # the reconstructed Odesa row never counts towards a day record
    assert "Ukraine" not in rec["most_countries_day"]["countries"]


def test_firsts_by_year():
    f = compute_milestones(_history())["firsts_by_year"]
    # Moldova + Poland; the refurbished Odesa row is history but not a 2013 first
    assert f["2013"]["countries"] == 2 and f["2013"]["cities"] == 2
    assert "2012" not in f
    assert f["2013"]["venues"] == 3      # cafe, warsaw, bus


def test_empty():
    assert compute_milestones([]) == {"events": [], "counts": {}, "records": {}, "firsts_by_year": {}}
