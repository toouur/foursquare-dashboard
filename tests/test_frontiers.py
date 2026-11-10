# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""metrics/frontiers.py — borders, time zones, streaks, weather luck, missing regions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import make_row

from metrics import frontiers as fr

H = 3600


def pt(t, city, country, lat, lng, off=2.0, category="Café", venue="V", vid=None):
    r = make_row(t, city=city, country=country, venue=venue, venue_id=vid or f"{city}{t}",
                 lat=str(lat), lng=str(lng), category=category)
    d = datetime.fromtimestamp(t, tz=timezone(timedelta(hours=off)))
    return (r, d, lat, lng)


T0 = int(datetime(2024, 5, 1, 9, tzinfo=timezone.utc).timestamp())


def test_borders_count_land_crossings_not_flights():
    pts = [pt(T0, "Chișinău", "Moldova", 47.0, 28.85, 3),
           pt(T0 + 2 * H, "Leușeni", "Moldova", 46.8, 28.2, 3, category="Border Crossing", vid="post"),
           pt(T0 + 3 * H, "Iași", "Romania", 47.16, 27.6, 3),
           pt(T0 + 9 * H, "Iași", "Romania", 47.16, 27.6, 3, category="Airport"),
           pt(T0 + 13 * H, "Rome", "Italy", 41.8, 12.25, 2, category="Airport"),     # a flight
           pt(T0 + 30 * H, "Vatican City", "Holy See", 41.90, 12.45, 2),
           pt(T0 + 31 * H, "Rome", "Italy", 41.90, 12.48, 2)]
    b = fr.borders(pts)
    assert b["crossings"] == 3 and b["pair_count"] == 2
    assert b["pairs"][0][2] == 2 and set(b["pairs"][0][:2]) == {"Holy See", "Italy"}
    assert b["busiest"]["route"] == ["Italy", "Holy See", "Italy"]   # same 2 countries, more crossings
    assert b["post_count"] == 1 and b["posts"][0]["visits"] == 1
    assert b["first"]["to"] == "Romania"


def test_zones_offsets_jump_and_distance_from_home_clock():
    pts = [pt(T0, "Minsk", "Belarus", 53.9, 27.56, 3),
           pt(T0 + 10 * H, "Irkutsk", "Russia", 52.3, 104.3, 8),
           pt(T0 + 20 * H, "Irkutsk", "Russia", 52.3, 104.3, 8),
           pt(T0 + 30 * H, "Kolkata", "India", 22.6, 88.4, 5.5)]
    z = fr.zones(pts, "Minsk", None)
    assert z["count"] == 3 and z["span"] == 5.0
    assert z["east"]["city"] == "Irkutsk" and z["west"]["city"] == "Minsk"
    assert z["jump"]["hours"] == 5.0 and z["far"]["hours"] == 5.0 and z["far"]["home"] == 3.0


def test_streaks_abroad_runs_new_places_and_country_years():
    day = 86400
    pts = [pt(T0, "Minsk", "Belarus", 53.9, 27.56)]
    pts += [pt(T0 + i * day, "Vilnius", "Lithuania", 54.7, 25.3, vid="v") for i in (1, 2, 5)]  # silent days 3–4
    pts += [pt(T0 + 6 * day, "Minsk", "Belarus", 53.9, 27.56)]
    for y in (2021, 2022, 2023):
        t = int(datetime(y, 7, 1, tzinfo=timezone.utc).timestamp())
        pts.append(pt(t, "Riga", "Latvia", 56.9, 24.1))
    pts.sort(key=lambda p: int(p[0]["date"]))
    s = fr.streaks(pts, "Minsk", None)
    assert s["abroad"][0]["days"] == 5 and s["abroad"][0]["countries"] == ["Lithuania"]
    latvia = next(c for c in s["country_years"] if c["country"] == "Latvia")
    assert (latvia["years"], latvia["from"], latvia["to"]) == (3, 2021, 2023)
    assert s["new_places"]["days"] == 2                    # day 0 Minsk, day 1 Vilnius
    assert all(c["country"] != "Belarus" for c in s["country_years"])


def test_luck_against_the_normal_of_the_month():
    wx = {"Riga|Latvia": {f"2024-07-0{i}": [25.0, 15.0, 2.0 if i < 3 else 0.0, 0.0, 1] for i in range(1, 6)},
          "Minsk|Belarus": {f"2024-01-0{i}": [-10.0, -15.0, 0.0, 0.0, 1] for i in range(1, 6)},
          "Oslo|Norway": {"2024-07-01": [20.0, 10.0, 0.0, 0.0, 1]}}            # too few days
    normals = {"Riga|Latvia": {"07": [22.0, 2.0, 0.5, 310]}, "Minsk|Belarus": {"01": [-5.0, 1.0, 0.4, 310]},
               "Oslo|Norway": {"07": [21.0, 2.0, 0.4, 310]}}
    lk = fr.luck(wx, normals, {"Minsk"})
    assert lk["cities"] == 2
    assert lk["warmer"][0]["city"] == "Riga" and lk["warmer"][0]["dt"] == 3.0
    assert lk["colder"][0]["city"] == "Minsk" and lk["colder"][0]["dt"] == -5.0
    assert lk["warmer"][0]["rain"] == 40 and lk["warmer"][0]["rain_norm"] == 50
    assert lk["summary"]["away"]["days"] == 5 and lk["summary"]["all"]["days"] == 10


def test_nearest_unvisited_and_countries_within_reach():
    regions_out = {"countries": [{"country": "Armenia", "n": 2, "of": 3,
                                  "regions": [["Yerevan", 10, "AM-ER"], ["Shirak", 3, "AM-SH"]]}]}
    iso = {"names": {"AM-ER": "Yerevan", "AM-SH": "Shirak", "AM-LO": "Lori", "GE-TB": "Tbilisi"},
           "centre": {"AM-ER": [40.18, 44.51], "AM-SH": [40.8, 43.85], "AM-LO": [40.9, 44.5],
                      "GE-TB": [41.7, 44.8]},
           "totals": {"AM": 3, "GE": 12}}
    out = fr.nearest(regions_out, iso, {"Armenia": "am", "Georgia": "ge"}, (40.18, 44.51))
    assert [r["code"] for r in out["home"]] == ["AM-LO", "GE-TB"]
    assert out["complete_next"] == [{"country": "Armenia", "n": 2, "of": 3, "missing": ["Lori"], "left": 1}]
