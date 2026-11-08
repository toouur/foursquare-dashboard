# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""metrics/explore.py and fetch_geo_context.py — the stats page's exploration extras."""
from __future__ import annotations

from datetime import date, datetime, timezone

from conftest import make_row

import fetch_geo_context as fgc
from metrics import explore as ex


def ts(y, m, d, h=12, mi=0):
    return int(datetime(y, m, d, h, mi, tzinfo=timezone.utc).timestamp())


def minsk(t, venue="Cafe", vid="v1", **kw):
    return make_row(t, city="Minsk", country="Belarus", venue=venue, venue_id=vid,
                    lat="53.9", lng="27.56", category=kw.pop("category", "Café"), **kw)


def test_tiles_square_and_cluster():
    assert ex.tile_of(0.0, 0.0) == (8192, 8192)
    sq = {(x, y) for x in range(3) for y in range(3)}
    assert ex.max_square(sq)[0] == 3
    assert ex.max_square(sq | {(5, 5)})[0] == 3
    assert ex.max_cluster(sq) == [(1, 1)]             # only the centre has 4 visited neighbours
    assert ex.max_cluster({(0, 0), (1, 0)}) == []


def test_sun_position_day_and_night_in_minsk():
    noon, dec = ex.sun_position(ts(2024, 6, 21, 9, 50), 53.9, 27.56)   # ~local solar noon
    assert 58 < noon < 60.5 and 23 < dec < 23.5
    night, _ = ex.sun_position(ts(2024, 12, 21, 22, 0), 53.9, 27.56)
    assert night < -40


def test_sun_white_nights_and_polar():
    spb = make_row(ts(2024, 6, 21, 21, 0), city="Saint Petersburg", country="Russia",
                   lat="59.94", lng="30.31", venue="Nevsky", venue_id="s")
    north = make_row(ts(2024, 6, 21, 21, 0), city="Tromsø", country="Norway",
                     lat="69.65", lng="18.96", venue="Pier", venue_id="t")
    winter = make_row(ts(2024, 12, 21, 11, 0), city="Tromsø", country="Norway",
                      lat="69.65", lng="18.96", venue="Pier", venue_id="t")
    out = ex.compute_explore([minsk(ts(2024, 6, 21)), spb, north, winter], "config")["sun"]
    assert [c[0] for c in out["white_nights"]] == ["Saint Petersburg"]   # Minsk is too far south
    assert out["polar_day"][0][0] == "Tromsø" and out["polar_night"][0][0] == "Tromsø"


def test_extremes_capitals_chains_regions():
    rows = [minsk(ts(2020, 1, 1), venue="McDonald's", vid="m1"),
            make_row(ts(2021, 5, 1), city="Oslo", country="Norway", venue="McDonald’s",
                     venue_id="m2", lat="59.91", lng="10.75"),
            make_row(ts(2022, 5, 1), city="Bergen", country="Norway", venue="Museum",
                     venue_id="b", lat="60.39", lng="5.32")]
    out = ex.compute_explore(rows, "config", {"regions": {
        "Minsk|Belarus": {"code": "BY-HM", "name": "Minsk"},
        "Oslo|Norway": {"code": "NO-03", "name": "Oslo"},
        "Bergen|Norway": {"code": "NO-46", "name": "Vestland"}}})
    assert out["extremes"]["north"]["city"] == "Bergen" and out["extremes"]["east"]["city"] == "Minsk"
    assert out["capitals"]["of"] == 2 and out["capitals"]["missed"] == []
    mc = next(c for c in out["chains"] if c["brand"] == "McDonald's")
    assert mc["countries"] == 2 and mc["first"]["city"] == "Minsk" and mc["last"]["city"] == "Oslo"
    assert out["regions"]["total"] == 3 and out["regions"]["countries"][0]["country"] == "Norway"


def test_bonds_skip_homes_and_areas():
    rows = [minsk(ts(2010 + i, 3, 1), venue="Old Bar", vid="bar") for i in range(5)]
    rows += [minsk(ts(2008 + i, 3, 1), venue="Flat", vid="home", category="Home (private)") for i in range(9)]
    rows += [minsk(ts(2008 + i, 3, 1), venue="Centre", vid="area", category="Neighborhood") for i in range(9)]
    b = ex.compute_explore(rows, "config")["bonds"]
    assert [x["venue_id"] for x in b] == ["bar"] and b[0]["years"] == 4.0 and b[0]["visits"] == 5


def test_weather_records_and_home_split():
    rows = [minsk(ts(2024, 1, 10)), minsk(ts(2024, 7, 10)),
            make_row(ts(2024, 7, 12), city="Rome", country="Italy", venue="Forum", venue_id="f",
                     lat="41.89", lng="12.49")]
    wx = {"Minsk|Belarus": {"2024-01-10": [-12.0, -20.5, 0.0, 3.1, 73], "2024-07-10": [25.0, 14.0, 6.0, 0.0, 61]},
          "Rome|Italy": {"2024-07-12": [36.0, 22.0, 0.0, 0.0, 0]}}
    w = ex.compute_explore(rows, "config", {"weather": wx}, home_city="Minsk")["weather"]
    assert w["coldest"][0]["tmin"] == -20.5 and w["hottest"][0]["city"] == "Rome"
    assert [d["city"] for d in w["hottest"]] == ["Rome", "Minsk", "Minsk"]  # a city may repeat on other days
    assert w["snowiest"] == [w["coldest"][0]] and len(w["wettest"]) == 1    # dry/snowless days left out
    assert w["home"]["n"] == 2 and w["away"]["n"] == 1 and w["away"]["hot"] == 100 and w["home"]["rain"] == 50


def test_no_geo_context_skips_those_views():
    out = ex.compute_explore([minsk(ts(2024, 1, 1))], "config")
    assert "weather" not in out and "elevation" not in out and "regions" not in out and "tiles" in out


def test_fetch_ranges_and_region_parse():
    d = [date(2024, 1, 1), date(2024, 1, 5), date(2024, 3, 1)]
    assert fgc.ranges(d) == [(date(2024, 1, 1), date(2024, 1, 5)), (date(2024, 3, 1), date(2024, 3, 1))]
    assert fgc._region({"ISO3166-2-lvl4": "BY-MI", "state": "Minsk Region"}) == ("BY-MI", "Minsk Region")
    assert fgc._region({"ISO3166-2-lvl6": "X-1", "county": "Y"}) == ("X-1", "Y")
    assert fgc._region({}) == (None, "")


class _Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


class _Session:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        return self.fn(url, params)


def test_fill_elevation_and_weather_only_ask_for_missing(monkeypatch):
    monkeypatch.setattr(fgc.time, "sleep", lambda s: None)
    cache = {"53.900,27.560": 220}
    s = _Session(lambda u, p: _Resp({"elevation": [5.4] * len(p["latitude"].split(","))}))
    fgc.fill_elevation(cache, {"53.900,27.560", "41.890,12.490"}, 10, session=s)
    assert len(s.calls) == 1 and s.calls[0][1]["latitude"] == "41.890" and cache["41.890,12.490"] == 5

    def arch(u, p):
        return _Resp({"daily": {"time": ["2024-07-10", "2024-07-11"], "temperature_2m_max": [25.04, 26.0],
                                "temperature_2m_min": [14.0, 15.0], "precipitation_sum": [0.0, 1.0],
                                "snowfall_sum": [0.0, 0.0], "weather_code": [1, 61]}})
    wx: dict = {}
    s2 = _Session(arch)
    fgc.fill_weather(wx, {"Minsk|Belarus": (53.9, 27.56)}, {"Minsk|Belarus": {date(2024, 7, 10)}}, 5,
                     date(2024, 9, 1), session=s2)
    assert wx == {"Minsk|Belarus": {"2024-07-10": [25.0, 14.0, 0.0, 0.0, 1]}}   # only the asked day
    fgc.fill_weather(wx, {"Minsk|Belarus": (53.9, 27.56)}, {"Minsk|Belarus": {date(2024, 7, 10)}}, 5,
                     date(2024, 9, 1), session=s2)
    assert len(s2.calls) == 1                                                  # nothing left to fetch


def test_network_error_ends_that_service_not_the_script(monkeypatch):
    import requests
    monkeypatch.setattr(fgc.time, "sleep", lambda s: None)

    class Boom:
        calls = 0

        def get(self, url, **kw):
            Boom.calls += 1
            raise requests.ReadTimeout("slow")
    cache: dict = {}
    assert fgc.fill_elevation(cache, {"1.000,2.000"}, 5, session=Boom()) == 1 and cache == {}
    assert fgc.fill_regions(cache, {"A|B": (1.0, 2.0), "C|D": (3.0, 4.0)}, 5, 0.0, session=Boom()) == 1


def test_country_checkin_is_not_an_extreme():
    rows = [minsk(ts(2024, 1, 1)),
            make_row(ts(2024, 2, 1), city="", country="Turkey", venue="Turkey", venue_id="tr",
                     lat="38.96", lng="35.24", category="Country")]
    e = ex.compute_explore(rows, "config")["extremes"]
    assert {v["venue_id"] for v in e.values()} == {"v1"}


def test_weather_top_lists_one_row_per_date_and_harsher_tie():
    def row(t, city, country, lat, lng):
        return make_row(t, city=city, country=country, venue="X", venue_id=city, lat=lat, lng=lng)
    rows = [row(ts(2025, 8, 31, 9), "Oberkerz", "Romania", "45.6", "24.6"),
            row(ts(2025, 8, 31, 12), "Transfăgărășan", "Romania", "45.6", "24.6"),
            row(ts(2025, 9, 1, 12), "Cârțișoara", "Romania", "45.7", "24.6"),
            row(ts(2024, 7, 1, 12), "Minsk", "Belarus", "53.9", "27.56")]
    wx = {"Oberkerz|Romania": {"2025-08-31": [12.0, 7.5, 56.0, 0.0, 65]},
          "Transfăgărășan|Romania": {"2025-08-31": [10.3, 5.9, 56.0, 0.0, 65]},
          "Cârțișoara|Romania": {"2025-09-01": [18.9, 13.7, 51.6, 0.0, 65]},
          "Minsk|Belarus": {"2024-07-01": [22.0, 12.0, 20.0, 0.0, 61]}}
    trip = {"name": "Romania", "start_ts": 1, "start_date": "2025-08-31",
            "checkins": [{"city": c, "country": "Romania", "date": d} for c, d in
                         (("Oberkerz", "2025-08-31"), ("Transfăgărășan", "2025-08-31"), ("Cârțișoara", "2025-09-01"))]}
    w = ex.compute_explore(rows, "config", {"weather": wx}, trips=[trip], home_city="Minsk")["weather"]
    # same day, same region → one row; the next day of the same trip keeps its own
    assert [(d["city"], d["precip"]) for d in w["wettest"]] == [
        ("Transfăgărășan", 56.0), ("Cârțișoara", 51.6), ("Minsk", 20.0)]
