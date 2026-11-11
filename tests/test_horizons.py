# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""metrics/horizons.py — heritage, food passport, gone, gravity, replay, then & now,
city twins, next-trip forecast."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from conftest import make_row

from metrics import horizons as hz

DAY = 86400
T0 = int(datetime(2015, 5, 1, 9, tzinfo=timezone.utc).timestamp())
UTC3 = timezone(timedelta(hours=3))
CONFIG = Path(__file__).resolve().parents[1] / "config"


def pt(t, city="Minsk", country="Belarus", lat=53.9, lng=27.56, category="Café", vid=None, **kw):
    r = make_row(t, city=city, country=country, venue=kw.pop("venue", "V"), venue_id=vid or f"{city}{t}",
                 lat=str(lat), lng=str(lng), category=category, **kw)
    return (r, datetime.fromtimestamp(t, tz=UTC3), lat, lng)


def stamp(r):
    return datetime.fromtimestamp(int(r["date"]), tz=UTC3)


def test_unesco_matches_inside_the_component_radius_only():
    sites = {"1": ["old town of X", ["Belarus"], [[53.9, 27.56, 600]]],
             "2": ["Far Away", ["Belarus"], [[55.0, 30.0, 600]]]}
    pts = [pt(T0, lat=53.902, lng=27.561, vid="a"),     # ~230 m from the centre
           pt(T0 + DAY, lat=53.902, lng=27.561, vid="a"),
           pt(T0 + 2 * DAY, lat=53.95, lng=27.56, vid="b")]   # 5.5 km off
    u = hz.unesco(pts, sites)
    assert u["n"] == 1 and u["total"] == 2
    s = u["sites"][0]
    assert s["name"] == "Old town of X" and s["n"] == 2 and s["venue_id"] == "a"
    assert u["countries"] == [["Belarus", 1]]


def test_unesco_skips_area_categories():
    sites = {"1": ["Site", ["Belarus"], [[53.9, 27.56, 600]]]}
    assert hz.unesco([pt(T0, category="City")], sites, {"City"}) == {}


def test_cuisines_counts_where_and_at_the_source():
    cmap = {"Italian Restaurant": "Italy", "Pizzeria": "Italy", "Sushi Restaurant": "Japan"}
    flags = {"Italy": "it", "Belarus": "by", "Japan": "jp"}
    pts = [pt(T0, category="Pizzeria", vid="p"), pt(T0 + DAY, category="Pizzeria", vid="p"),
           pt(T0 + 2 * DAY, "Rome", "Italy", category="Italian Restaurant", vid="r"),
           pt(T0 + 3 * DAY, category="Sushi Restaurant", vid="s"),
           pt(T0 + 4 * DAY, category="Park")]
    c = hz.cuisines(pts, cmap, flags)
    it = c["cuisines"][0]
    assert it["cuisine"] == "Italy" and it["n"] == 3 and it["places"] == 2 and it["at_source"] == 1
    assert it["where"] == [["Belarus", 2], ["Italy", 1]] and it["visits"] == 2
    assert c["never_at_source"] == ["Japan"] and c["widest"] == "Italy"


def test_graveyard_counts_closed_places_and_share_by_discovery_year():
    rows = [make_row(T0 + i * DAY, city="Minsk", country="Belarus", venue="Bar", venue_id="gone",
                     category="Bar") for i in range(3)]
    rows += [make_row(T0 + 400 * DAY, city="Minsk", country="Belarus", venue="Cafe", venue_id="open",
                      category="Café")]
    rows += [make_row(T0, venue="Home", venue_id="h", category="Home (private)")]
    g = hz.graveyard(rows, {"gone", "h"}, {"Home (private)"}, stamp)
    assert g["n"] == 1 and g["of"] == 2 and g["pct"] == 50.0 and g["visits"] == 3
    assert g["loved"][0]["venue_id"] == "gone" and g["loved"][0]["n"] == 3
    assert g["by_year"] == [[2015, 1, 1], [2016, 0, 1]]
    assert hz.graveyard(rows, set(), set(), stamp) == {}


def test_gravity_mean_point_and_shift():
    pts = [pt(T0, lat=50.0, lng=20.0), pt(T0 + 1, lat=52.0, lng=20.0),
           pt(T0 + 400 * DAY, "Kyiv", "Ukraine", lat=50.45, lng=30.52),
           pt(T0 + 401 * DAY, "Kyiv", "Ukraine", lat=50.45, lng=30.52),
           pt(T0 + 402 * DAY, "Kyiv", "Ukraine", lat=50.45, lng=30.52)]
    g = hz.gravity(pts)
    y15, y16 = g["years"]
    assert abs(y15["lat"] - 51.0) < 0.05 and abs(y15["lng"] - 20.0) < 0.01
    assert y16["near"][0] == "Kyiv" and y16["near"][2] == 0
    assert g["jump"]["year"] == 2016 and 700 < g["jump"]["km"] < 800


def test_history_one_point_per_day_and_cell():
    pts = [pt(T0, lat=53.9001, lng=27.5601), pt(T0 + 60, lat=53.9002, lng=27.5602),
           pt(T0 + DAY, lat=53.9, lng=27.56)]
    h = hz.history(pts)
    assert h["d0"] == "2015-05-01" and h["scale"] == 100
    assert h["p"] == [[0, 5390, 2756], [1, 5390, 2756]]


def test_then_now_pairs_first_and_last_photo_years_apart():
    rows = [make_row(T0, venue="Square", venue_id="v", checkin_id="c1", category="Plaza"),
            make_row(T0 + 400 * DAY, venue="Square", venue_id="v", checkin_id="c2", category="Plaza"),
            make_row(T0 + 6 * 365 * DAY, venue="Square", venue_id="v", checkin_id="c3", category="Plaza"),
            make_row(T0, venue="Cafe", venue_id="w", checkin_id="c4", category="Café"),
            make_row(T0 + 365 * DAY, venue="Cafe", venue_id="w", checkin_id="c5", category="Café")]
    photos = {"c1": ["a.jpg", "a2.jpg"], "c2": ["b.jpg"], "c3": ["c.jpg"], "c4": ["d.jpg"], "c5": ["e.jpg"]}
    tn = hz.then_now(rows, photos, stamp)
    assert len(tn) == 1
    assert tn[0]["venue_id"] == "v" and tn[0]["a"]["src"] == "a.jpg" and tn[0]["b"]["src"] == "c.jpg"
    assert tn[0]["photos"] == 3 and tn[0]["years"] == 6.0


def _city(city, country, cats, t0):
    out, i = [], 0
    for cat, k in cats.items():
        for j in range(k):
            out.append(pt(t0 + i, city, country, category=cat, vid=f"{city}{cat}{j % 30}"))
            i += 1
    return out


def test_city_twins_finds_the_most_alike_city():
    a = _city("A", "X", {"Canal": 30, "Pier": 20, "Park": 20}, T0)
    b = _city("B", "Y", {"Canal": 25, "Pier": 25, "Park": 20}, T0 + DAY)
    c = _city("C", "Z", {"Mosque": 30, "Bazaar": 20, "Park": 20}, T0 + 2 * DAY)
    t = hz.city_twins(a + b + c, home="C")
    assert t["n"] == 3
    assert {t["pairs"][0]["a"], t["pairs"][0]["b"]} == {"A", "B"}
    assert "Park" not in t["pairs"][0]["shared"][:1]      # idf: the common category weighs least
    assert t["cities"]["A|X"][0] == "B" and t["home"][0] == "C"


def test_forecast_gap_month_and_nearest_new_country():
    trips = [{"start_date": f"20{y}-0{m}-10", "end_date": f"20{y}-0{m}-14", "countries": ["Poland"]}
             for y in (20, 21, 22, 23) for m in (3, 7)]
    geo = {"PL": ["Poland", 52.0, 19.0], "LT": ["Lithuania", 55.0, 24.0], "JP": ["Japan", 36.0, 138.0]}
    f = hz.forecast(trips, geo, {"Poland": "pl", "Belarus": "by"}, {"Poland", "Belarus"},
                    (53.9, 27.56), today=date(2024, 2, 1))
    assert f["trips"] == 8 and f["last_end"] == "2023-07-14" and f["since"] == 202
    assert f["month"] == 3 and f["month_countries"] == [["Poland", 4]]
    assert f["overdue"] > 0
    assert [c["country"] for c in f["nearest_new"]] == ["Lithuania", "Japan"]


def test_configs_are_well_formed():
    u = json.loads((CONFIG / "unesco.json").read_text(encoding="utf-8"))["sites"]
    assert len(u) > 1000
    for name, ctry, comps in u.values():
        assert name and isinstance(ctry, list) and comps
        assert all(-90 <= la <= 90 and -180 <= lo <= 180 and 600 <= r <= 15000 for la, lo, r in comps)
    flags = json.loads((CONFIG / "country_flags.json").read_text(encoding="utf-8"))
    cuis = {k: v for k, v in json.loads((CONFIG / "cuisines.json").read_text(encoding="utf-8")).items()
            if not k.startswith("_")}
    missing = {v for v in cuis.values() if v not in flags} - {"Ethiopia", "Yemen", "Syria"}
    assert not missing, missing
    cg = json.loads((CONFIG / "countries_geo.json").read_text(encoding="utf-8"))
    assert len([k for k in cg if not k.startswith("_")]) > 190
