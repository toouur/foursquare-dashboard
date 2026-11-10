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


def test_region_code_fixes_borders_parents_and_territories():
    iso = {"parents": {"BE-VAN": "BE-VLG"}, "totals": {"BY": 7, "BE": 3}}
    reg = {"Smolensk Region|Belarus": {"code": "RU-SMO"}, "Antwerp|Belgium": {"code": "BE-VAN"},
           "Hong Kong|Hong Kong": {"code": "CN-HK"}, "Ungheni|Romania": {"code": "MD-UN"}}
    fixes = {"Ungheni|Romania": "RO-IS", "Iffezheim|France": None}
    assert ex.region_code("Smolensk Region|Belarus", "by", reg, iso, fixes) is None   # across the border
    assert ex.region_code("Antwerp|Belgium", "be", reg, iso, fixes) == "BE-VLG"       # province → region
    assert ex.region_code("Hong Kong|Hong Kong", "hk", reg, iso, fixes) == "CN-HK"
    assert ex.region_code("Ungheni|Romania", "ro", reg, iso, fixes) == "RO-IS"        # fix wins
    assert ex.region_code("Iffezheim|France", "fr", {"Iffezheim|France": {"code": "FR-GES"}}, iso, fixes) is None


def test_regions_skip_country_checkins_and_count_of_total():
    rows = [minsk(ts(2020, 1, 1)),
            make_row(ts(2020, 2, 1), city="Dolostsy", country="Belarus", venue="Belarus", venue_id="c",
                     lat="53.9", lng="27.56", category="Country")]
    reg = {"Minsk|Belarus": {"code": "BY-HM", "name": ""}, "Dolostsy|Belarus": {"code": "BY-VI"}}
    r = ex.regions(rows, reg, {"totals": {"BY": 2}, "names": {"BY-HM": "Minsk (city)"}}, {}, {"Belarus": "by"})
    assert r["total"] == 1 and r["countries"][0]["of"] == 2 and r["complete"] == []
    assert r["countries"][0]["regions"] == [["Minsk (city)", 1, "BY-HM", 2020]]
    assert r["years"] == [{"year": 2020, "new": 1, "names": ["Minsk (city)"], "total": 1,
                           "top": ["Minsk (city)", "Belarus", 1]}]


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
    assert w["floors"]["hot"] == 30.0
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
    assert fgc._region({"ISO3166-2-lvl8": "SI-003", "municipality": "Bled"}) == ("SI-003", "Bled")


def test_outline_hit_must_be_the_region_itself():
    minsk = {"extratags": {"admin_level": "4", "place": "city"}, "address": {"ISO3166-2-lvl4": "BY-HM"}}
    district = {"extratags": {"admin_level": "6"}, "address": {"ISO3166-2-lvl4": "BY-MI"}}
    assert fgc._is_region(minsk, "BY-HM") and not fgc._is_region(district, "BY-MI")
    assert fgc._is_region({"extratags": {"ISO3166-2": "SI-003"}}, "SI-003")


def test_fill_regions_retries_at_municipal_zoom_and_when_the_point_moved(monkeypatch):
    monkeypatch.setattr(fgc.time, "sleep", lambda s: None)

    def rev(u, p):
        return _Resp({"address": {"ISO3166-2-lvl8": "SI-003"}} if p["zoom"] == 10 else {"address": {}})
    s = _Session(rev)
    cache = {"Minsk|Belarus": {"code": "BY-HM", "name": "", "t": 0, "at": [53.9, 27.56]},
             "Gomel|Belarus": {"code": "RU-SMO", "name": "", "t": 0, "at": [54.7, 31.0]}}
    fgc.fill_regions(cache, {"Bled|Slovenia": (46.37, 14.11), "Minsk|Belarus": (53.9, 27.56),
                             "Gomel|Belarus": (52.43, 31.0)}, 10, 1e9, session=s)
    assert [c[1]["zoom"] for c in s.calls] == [8, 10, 8, 10]       # Bled, then Gomel (moved); Minsk kept
    assert cache["Bled|Slovenia"]["code"] == "SI-003" and cache["Bled|Slovenia"]["at"] == [46.37, 14.11]
    assert cache["Minsk|Belarus"]["code"] == "BY-HM"


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
    s = _Session(lambda u, p: _Resp({"results": [{"elevation": 5.4}] * len(p["locations"].split("|"))}))
    fgc.fill_elevation(cache, {"53.900,27.560", "41.890,12.490"}, 10, session=s)
    assert len(s.calls) == 1 and s.calls[0][1]["locations"] == "41.890,12.490" and cache["41.890,12.490"] == 5

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


def test_weather_lists_keep_every_day_past_the_floor_but_at_least_ten():
    rows = [minsk(ts(2024, 7, 1) + i * 86400) for i in range(30)]
    wx = {"Minsk|Belarus": {f"2024-07-{i + 1:02d}": [20.0 + i, 10.0, 0.0, 0.0, 0] for i in range(30)}}
    w = ex.compute_explore(rows, "config", {"weather": wx}, home_city="Minsk")["weather"]
    assert [d["tmax"] for d in w["hottest"]] == [49.0 - i for i in range(20)]   # 30°+ only: 20 days
    assert len(w["coldest"]) == 10                                            # none past -10°: top 10


def test_hunter_badges_first_reach_and_streaks():
    rows = [minsk(ts(2024, 1, d)) for d in (5, 6, 7)] + [minsk(ts(2024, 7, d)) for d in (1, 2)]
    wx = {"Minsk|Belarus": {"2024-01-05": [-5.0, -12.0, 0, 0, 0], "2024-01-06": [-8.0, -21.0, 0, 0, 0],
                            "2024-01-07": [-1.0, -9.0, 0, 0, 0], "2024-07-01": [31.0, 18.0, 0, 0, 0],
                            "2024-07-02": [36.0, 20.0, 0, 0, 0]}}
    h = ex.compute_explore(rows, "config", {"weather": wx}, home_city="Minsk")["weather"]["hunter"]
    assert h["heat"]["best"]["t"] == 36.0 and h["cold"]["best"]["t"] == -21.0 and h["heat"]["goal"] == 50
    b = {x["t"]: x for x in h["cold"]["badges"]}
    assert b[-10]["first"]["date"] == "2024-01-05" and b[-20]["first"]["date"] == "2024-01-06"
    assert b[-10]["days"] == 2 and "first" not in b[-30]
    assert [x["days"] for x in h["heat"]["badges"]][:3] == [2, 1, 0]
    assert h["cold"]["streak"] == {"days": 3, "from": "2024-01-05", "to": "2024-01-07"}
    assert h["heat"]["streak"]["days"] == 2


def test_summit_pins_are_flagged_not_dropped():
    rows = [make_row(ts(2021, 9, 6), city="Doğubayazıt", country="Turkey", venue="Ağrı Dağı",
                     venue_id="ararat", lat="39.706", lng="44.295", category="Mountain"),
            minsk(ts(2024, 1, 1))]
    e = ex.compute_explore(rows, "config", {"elevation": {"39.706,44.295": 4954, "53.900,27.560": 220}},
                           summit_pins={"ararat"})
    top = e["elevation"]["highest"]
    assert top[0]["venue_id"] == "ararat" and top[0]["summit"] is True and "summit" not in top[1]


def test_peaks_per_continent_badges_and_summit_pins():
    def row(t, city, country, lat, lng, cat="Café", vid=None):
        return make_row(t, city=city, country=country, venue=city, venue_id=vid or city,
                        lat=lat, lng=lng, category=cat)
    rows = [row(ts(2019, 1, 1), "Minsk", "Belarus", "53.9", "27.56"),
            row(ts(2021, 6, 1), "Shymbulak", "Kazakhstan", "43.128", "77.081"),
            row(ts(2022, 1, 1), "Baku", "Azerbaijan", "40.409", "49.867"),
            row(ts(2024, 12, 3), "Puente del Inca", "Argentina", "-32.825", "-69.909"),
            row(ts(2021, 9, 6), "Doğubayazıt", "Turkey", "39.706", "44.295", cat="Mountain", vid="ararat")]
    elev = {"53.900,27.560": 220, "43.128,77.081": 2260, "40.409,49.867": -28,
            "-32.825,-69.909": 2801, "39.706,44.295": 4954}
    p = ex.compute_explore(rows, "config", {"elevation": elev}, summit_pins={"ararat"})["peaks"]
    assert p["best"]["city"] == "Puente del Inca" and p["goal"] == 8849
    assert p["summit_pins"][0]["venue_id"] == "ararat"           # 4,954 m pin never counts as reached
    b = {x["m"]: x for x in p["badges"]}
    assert b[2000]["first"]["city"] == "Shymbulak" and "first" not in b[3000]
    asia = next(c for c in p["continents"] if c["continent"] == "Asia")
    assert asia["high"]["city"] == "Shymbulak" and asia["low"]["m"] == -28 and asia["peak"] == ["Everest", 8849]
    assert "low" not in next(c for c in p["continents"] if c["continent"] == "Europe")


def test_coastal_dem_noise_is_not_below_sea_level():
    rows = [make_row(ts(2024, 2, 12), city="Dubai", country="United Arab Emirates", venue="Palm Mall",
                     venue_id="palm", lat="25.11", lng="55.14")]
    p = ex.compute_explore(rows, "config", {"elevation": {"25.110,55.140": -9}}, summit_pins=set())["peaks"]
    assert "low" not in p["continents"][0]


def test_mountain_category_alone_is_not_a_summit_pin():
    rows = [make_row(ts(2025, 12, 16), city="Tehran", country="Iran", venue="7th Tochal Station",
                     venue_id="tochal", lat="35.89", lng="51.4", category="Mountain")]
    e = ex.compute_explore(rows, "config", {"elevation": {"35.890,51.400": 3736}}, summit_pins=set())
    assert "summit" not in e["elevation"]["highest"][0] and e["peaks"]["best"]["venue_id"] == "tochal"


def test_heights_lists_keep_everything_past_the_floors():
    rows = [make_row(ts(2024, 1, 1 + i), city=f"C{i}", country="Chile", venue=f"V{i}", venue_id=f"v{i}",
                     lat=f"-33.{100 + i}", lng="-70.5") for i in range(14)]
    elev = {f"-33.{100 + i:03d},-70.500": h for i, h in enumerate(
        [2500, 2400, 2300, 2200, 2100, 2050, 2010, 2000, 1999, 1500, 900, 300, -12, -28])}
    h = ex.compute_explore(rows, "config", {"elevation": elev}, summit_pins=set())["elevation"]
    assert [x["m"] for x in h["highest"]][:8] == [2500, 2400, 2300, 2200, 2100, 2050, 2010, 2000]
    assert len(h["highest"]) == 10                  # 8 from 2,000 m up, topped up to ten
    assert [x["m"] for x in h["lowest"]] == [-28, -12]


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


def test_stats_page_ships_only_the_keys_it_reads():
    import gen_stats
    tmpl = "const S={{STATS}};var a=S.explore; var b=S['by_hour']; nomad=S.nomad_kpis||{};"
    data = {"explore": 1, "by_hour": 2, "nomad_kpis": 3, "unique_places": [0] * 9, "venues_heatmap": [1]}
    assert gen_stats.used_keys(tmpl, data) == {"explore": 1, "by_hour": 2, "nomad_kpis": 3}


def test_region_map_geometry_is_simplified():
    g = {"type": "Polygon", "coordinates": [[[27.4012, 53.8701], [27.4049, 53.8698], [27.5, 53.9],
                                             [27.6, 53.8], [27.4012, 53.8701]],
                                            [[27.45, 53.85], [27.451, 53.851], [27.452, 53.85], [27.45, 53.85]]]}
    s = ex.simplify_geometry(g)
    assert s["coordinates"] == [[[27.4, 53.87], [27.5, 53.9], [27.6, 53.8], [27.4, 53.87]]]   # tiny hole gone
    assert ex.simplify_geometry({"type": "Point", "coordinates": [1, 2]}) is None
