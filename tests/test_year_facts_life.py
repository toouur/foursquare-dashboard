# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""metrics.year_facts + metrics.life and their pages (compare.html, life.html)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import gen_compare
import gen_life
from conftest import make_row
from metrics.life import compute_life
from metrics.year_facts import compute_year_facts

SWITCH = int(datetime(2016, 7, 1, tzinfo=timezone.utc).timestamp())
ERAS = [(SWITCH, "Chișinău")]            # Chișinău until mid-2016, Minsk after


def _ts(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def _row(when, vid, city="Minsk", country="Belarus", category="Café", **kw):
    kw.setdefault("source_app", "Swarm")
    kw.setdefault("checkin_id", f"c{_ts(when)}{vid}")
    return make_row(_ts(when), city=city, country=country, category=category, venue=f"V {vid}",
                    venue_id=vid, lat="53.9", lng="27.56", **kw)


def _rows():
    return [
        _row("2015-03-01T10:00:00", "a", city="Chișinău", country="Moldova", with_name="Vika"),
        _row("2015-03-01T18:00:00", "b", city="Chișinău", country="Moldova", shout="hi"),
        _row("2016-02-01T10:00:00", "a", city="Chișinău", country="Moldova"),
        _row("2016-08-01T10:00:00", "c"),
        _row("2016-08-02T10:00:00", "c", category="Home (private)"),
        _row("2016-09-01T10:00:00", "d", city="Warsaw", country="Poland", with_name="Vika"),
        _row("2008-05-01T12:00:00", "", city="Odesa", country="Ukraine", source_app="refurbished"),
    ]


def test_year_facts_new_vs_seen():
    yf = compute_year_facts(_rows(), "Minsk", ERAS)
    y15, y16 = yf["years"]["2015"], yf["years"]["2016"]
    assert y15["checkins"] == 2 and y15["days"] == 1 and y15["per_day"] == 2
    assert y15["new_venues"] == 2 and y15["new_countries"] == ["Moldova"]
    assert y16["venues"] == 3 and y16["new_venues"] == 2          # a was seen in 2015
    assert y16["new_countries"] == ["Belarus", "Poland"]
    assert y15["shouts"] == 1 and y15["companions"] == 1
    assert ["V c", 1] in y16["top_venues"]                         # the Home row is not a top venue
    assert yf["years"]["2008"]["reconstructed"] == 1
    assert sum(y["new_venues"] for y in yf["years"].values()) == 4


def test_eras_are_computed_from_their_own_rows():
    yf = compute_year_facts(_rows(), "Minsk", ERAS)
    e1, e2 = yf["eras"]
    assert e1["city"] == "Chișinău" and e1["to"] == "2016-07-01" and e1["from"] is None
    assert e2["city"] == "Minsk" and e2["to"] is None
    assert e1["facts"]["checkins"] == 4 and e2["facts"]["checkins"] == 3
    assert e1["label"] == "Chișinău 2008–2016"
    # era-aware home share: 3 of 4 Chișinău-era rows are in Chișinău, 2 of 3 Minsk-era rows in Minsk
    assert e1["facts"]["home_pct"] == 75.0 and e2["facts"]["home_pct"] == 66.7


def test_travel_facts():
    trips = [{"start_date": "2016-09-01", "duration": 3}, {"start_date": "2015-12-31", "duration": 2}]
    yf = compute_year_facts(_rows(), "Minsk", ERAS, trips=trips,
                            distance_from_home=[["2016-09-01", 470], ["2015-03-01", 2]],
                            coverage={"years": [{"y": 2016, "rg": 120}]})
    assert yf["years"]["2016"]["trips"] == 1 and yf["years"]["2016"]["trip_days"] == 3
    assert yf["years"]["2016"]["farthest_km"] == 470 and yf["years"]["2016"]["rg_km"] == 120
    assert yf["eras"][1]["facts"]["trips"] == 1


def test_life_bands():
    trips = [{"start_date": "2016-09-01", "end_date": "2016-09-03", "name": "Warsaw", "id": 7}]
    music = {"1970": {"top_artist": {"name": "Bogus"}}, "2016": {"scrobbles": 99, "top_artist": {"name": "Кино", "plays": 9}}}
    L = compute_life(_rows(), "Minsk", ERAS, trips=trips, music_by_year=music)
    assert L["start"] == "2008-05-01" and L["days"] == (datetime(2016, 9, 1) - datetime(2008, 5, 1)).days + 1
    # country runs cover every day from the first check-in, gaps inherited and flagged
    assert sum(r[1] for r in L["country_runs"]) == L["days"]
    assert L["countries"][L["country_runs"][0][2]] == "Ukraine" and L["country_runs"][1][3] == 1
    assert [e[2] for e in L["eras"]] == ["Chișinău", "Minsk"]
    assert sum(e[1] for e in L["eras"]) == L["days"]
    assert L["trips"] == [[L["days"] - 1, 3, "Warsaw", 7]]
    assert sum(L["weeks"]) == 7
    assert L["people"][0][0] == "Vika" and sum(L["people"][0][1]) == 2
    assert L["music"] == [["2016", "Кино", 9, 99]]
    assert compute_life([], "Minsk") == {}


def test_pages_render(tmp_path):
    rows = _rows()
    assert gen_compare.build_page(out_path=str(tmp_path), rows=rows, home_city="Minsk", home_history=ERAS) == 5
    assert gen_life.build_page(out_path=str(tmp_path), rows=rows, home_city="Minsk", home_history=ERAS) > 0
    for page, blob in (("compare.html", "year_facts.json"), ("life.html", "life_data.json")):
        html = (tmp_path / page).read_text(encoding="utf-8")
        assert "{{" not in html            # a nested @media legitimately ends in "}}"
        assert f"fetch('{blob}?v=" in html
        json.loads((tmp_path / blob).read_text(encoding="utf-8"))
