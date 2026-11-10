# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""metrics/rhythms.py — soundtrack, weather & you, left behind, jet lag, holidays, reach."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import make_row

from metrics import rhythms as rh

H, DAY = 3600, 86400


def pt(t, city, country, off=3.0, category="Café", vid=None, **kw):
    r = make_row(t, city=city, country=country, venue=kw.pop("venue", "V"), venue_id=vid or f"{city}{t}",
                 lat="1", lng="1", category=category, **kw)
    return (r, datetime.fromtimestamp(t, tz=timezone(timedelta(hours=off))), 1.0, 1.0)


T0 = int(datetime(2024, 5, 1, 9, tzinfo=timezone.utc).timestamp())


def test_soundtrack_places_scrobbles_at_the_last_checkin():
    pts = [pt(T0, "Minsk", "Belarus"), pt(T0 + 2 * DAY, "Tbilisi", "Georgia")]
    scr = [(T0 + 60 * i, "Proem", "Deep", "") for i in range(60)]                 # Minsk
    scr += [(T0 + 2 * DAY + 60 * i, "Mgzavrebi", "Song", "") for i in range(55)]  # Tbilisi
    scr += [(T0 + DAY, "Lost", "Gap", "")]                                        # 24 h after: unplaced
    s = rh.soundtrack(pts, scr)
    assert s["placed"] == 115 and s["total"] == 116
    assert [c["country"] for c in s["countries"]] == ["Belarus", "Georgia"]
    assert s["countries"][1]["artists"][0] == ["Mgzavrebi", 55]


def test_weather_mood_lift_of_a_group_on_rainy_days():
    pts, wx = [], {"Minsk|Belarus": {}}
    for i in range(400):
        t = T0 + i * DAY
        rainy = i % 2 == 0
        d = datetime.fromtimestamp(t, tz=timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
        wx["Minsk|Belarus"][d] = [10.0, 5.0, 5.0 if rainy else 0.0, 0.0, 61]
        pts.append(pt(t, "Minsk", "Belarus", category="Museum" if rainy else "Park"))
    m = rh.weather_mood(pts, wx, lambda c: {"Museum": "Culture", "Park": "Nature"}[c], set())
    assert m["buckets"]["rain"]["more"][0][0] == "Culture" and m["buckets"]["rain"]["more"][0][1] == 2.0


def test_abandoned_and_city_returns():
    pts = [pt(T0 + i * DAY, "Minsk", "Belarus", vid="canteen", venue="Canteen") for i in range(12)]
    pts += [pt(T0 + 1200 * DAY, "Minsk", "Belarus", vid="new")]
    a = rh.abandoned(pts, set())
    assert a[0]["venue_id"] == "canteen" and a[0]["visits"] == 12 and a[0]["years_gone"] > 3
    pts2 = [pt(T0 + i, "Oslo", "Norway", vid=f"o{i}") for i in range(45)]
    pts2 += [pt(T0 + 100 + i, "Oslo", "Norway", vid=f"o{i}") for i in range(9)]
    assert rh.city_returns(pts2, set())["loyal"][0]["pct"] == 20


def test_jetlag_day_starts_at_four():
    pts = []
    for i in range(40):                                   # home days, first check-in 09:00
        pts.append(pt(T0 + i * DAY, "Minsk", "Belarus", 3))
    land = T0 + 41 * DAY
    pts.append(pt(land - 20 * H, "Minsk", "Belarus", 3))
    pts.append(pt(land, "Irkutsk", "Russia", 8))
    pts.append(pt(land + 8 * H, "Irkutsk", "Russia", 8))         # 01:00 local next night → arrival day
    pts.append(pt(land + 26 * H, "Irkutsk", "Russia", 8))        # day 1, 19:00 local
    j = rh.jetlag(pts, "Minsk", None)
    assert j["jumps"] == 1 and j["trips"][0]["shift"] == 5.0
    assert j["baseline"]["first"] == 12.0
    assert j["trips"][0]["first"][0] == [1, 19.0]


def test_holidays_new_year_and_public():
    eve = int(datetime(2023, 12, 31, 20, tzinfo=timezone.utc).timestamp())
    pts = [pt(T0, "Minsk", "Belarus", 3), pt(eve, "Naples", "Italy", 1, vid="nap")]
    may = int(datetime(2024, 5, 1, 10, tzinfo=timezone.utc).timestamp())
    pts.append(pt(may, "Rome", "Italy", 2))
    pts.sort(key=lambda p: int(p[0]["date"]))
    h = rh.holidays(pts, {"IT-2024": [["2024-05-01", "Labour Day"]]}, {"Italy": "it", "Belarus": "by"}, "Minsk", None)
    assert h["new_year"][0]["year"] == 2023 and h["new_year"][0]["city"] == "Naples" and h["new_year"][0]["abroad"]
    assert h["count"] == 1 and h["abroad"] == 1 and h["public"][0]["holiday"] == "Labour Day"


def test_companion_reach_counts_countries_and_regions():
    pts = [pt(T0 + i, c, k, with_name="Joanna K.") for i, (c, k) in
           enumerate([("Warsaw", "Poland"), ("Kraków", "Poland"), ("Vilnius", "Lithuania")])]
    reach = rh.companion_reach(pts, lambda r: {"Warsaw": "PL-14", "Kraków": "PL-12", "Vilnius": "LT-VL"}[r["city"]])
    assert reach == [{"name": "Joanna K.", "checkins": 3, "countries": 2, "regions": 3, "cities": 3,
                      "codes": ["LT-VL", "PL-12", "PL-14"], "country_list": ["Poland", "Lithuania"],
                      "first": reach[0]["first"]}]


def test_a_private_home_is_masked_only_when_asked(monkeypatch):
    eve = int(datetime(2025, 12, 31, 22, tzinfo=timezone.utc).timestamp())
    pts = [pt(eve, "Minsk", "Belarus", category="Home (private)", venue="Lermontova 43", vid="h")]
    assert rh.holidays(pts, None, {}, "Minsk", None)["new_year"][0]["venue"] == "Lermontova 43"
    monkeypatch.setattr(rh, "HIDE_HOME", True)
    e = rh.holidays(pts, None, {}, "Minsk", None)["new_year"][0]
    assert e["venue"] == "Home" and e["venue_id"] == ""
