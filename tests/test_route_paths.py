# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Offline tests for scripts/route_paths.py — polyline codec, simplification,
the persistent RouteCache and attach_routes (with a mocked fetcher, no network).

Coordinates are synthetic: 1° of latitude ≈ 111.19 km, longitude fixed at 0,
same convention as test_transport_mode.py.
"""
import json

import route_paths as rp

KM_PER_DEG = 111.19


def ci(ts, lat=50.0, lng=0.0, m=""):
    return {"ts": ts, "lat": lat, "lng": lng, "m": m}


def trip(*checkins):
    return [{"checkins": list(checkins)}]


# ── polyline5 codec ───────────────────────────────────────────────────────────

def test_polyline_roundtrip():
    pts = [(50.0, 0.0), (50.12345, 0.54321), (49.99999, -1.00001)]
    dec = rp.decode_polyline(rp.encode_polyline(pts))
    assert len(dec) == len(pts)
    for (la, lo), (dla, dlo) in zip(pts, dec):
        assert abs(la - dla) < 1e-5 and abs(lo - dlo) < 1e-5


def test_polyline_known_google_example():
    # Reference vector from Google's encoded-polyline documentation.
    enc = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert rp.decode_polyline(enc) == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert rp.encode_polyline([(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]) == enc


def test_polyline_empty():
    assert rp.encode_polyline([]) == ""
    assert rp.decode_polyline("") == []


# ── Douglas-Peucker simplify ──────────────────────────────────────────────────

def test_simplify_drops_collinear_points():
    pts = [(50.0, 0.0), (50.1, 0.0), (50.2, 0.0), (50.3, 0.0)]
    assert rp.simplify(pts, 1e-4) == [(50.0, 0.0), (50.3, 0.0)]


def test_simplify_keeps_corner():
    pts = [(50.0, 0.0), (50.1, 0.1), (50.2, 0.0)]
    assert rp.simplify(pts, 1e-4) == pts


def test_simplify_short_input_unchanged():
    assert rp.simplify([(50.0, 0.0)], 1e-4) == [(50.0, 0.0)]
    assert rp.simplify([(50.0, 0.0), (51.0, 1.0)], 1e-4) == [(50.0, 0.0), (51.0, 1.0)]


# ── RouteCache ────────────────────────────────────────────────────────────────

def test_cache_save_load_roundtrip(tmp_path):
    p = str(tmp_path / "routes_cache.json")
    c = rp.RouteCache(p)
    assert c.data == {} and not c.dirty
    c.put("foot:50.0000,0.0000;50.0100,0.0000", {"p": "abc", "km": 1.2})
    assert c.dirty
    c.save()
    assert not c.dirty
    c2 = rp.RouteCache(p)
    assert c2.get("foot:50.0000,0.0000;50.0100,0.0000") == {"p": "abc", "km": 1.2}


def test_cache_corrupt_file_starts_fresh(tmp_path):
    p = tmp_path / "routes_cache.json"
    p.write_text("{not json", encoding="utf-8")
    assert rp.RouteCache(str(p)).data == {}


def test_cache_no_path_never_saves(tmp_path):
    c = rp.RouteCache(None)
    c.put("k", {"x": 1})
    c.save()  # must not raise


def test_cache_save_skipped_when_clean(tmp_path):
    p = str(tmp_path / "routes_cache.json")
    rp.RouteCache(p).save()
    import os
    assert not os.path.exists(p)


# ── attach_routes ─────────────────────────────────────────────────────────────

def _fetch_ok(profile, a, b):
    poly = rp.encode_polyline([a, ((a[0] + b[0]) / 2, 0.01), b])
    from flights import _haversine_km
    return "ok", (poly, _haversine_km(a[0], a[1], b[0], b[1]) * 1.2)


def test_attach_routes_basic_ok():
    t0 = 1591012800
    trips = trip(ci(t0), ci(t0 + 3600, lat=50.0 + 5 / KM_PER_DEG, m="C"))
    cache = rp.RouteCache(None)
    out = rp.attach_routes(trips, cache, fetcher=_fetch_ok, sleep_s=0)
    key = f"{t0}-{t0 + 3600}"
    assert key in out
    poly, km = out[key]
    assert rp.decode_polyline(poly)[0] == (50.0, 0.0)
    assert 5.5 < km < 6.5
    assert len(cache.data) == 1 and cache.dirty


def test_attach_routes_cache_hit_skips_fetch():
    t0 = 1591012800
    trips = trip(ci(t0), ci(t0 + 3600, lat=50.0 + 5 / KM_PER_DEG, m="C"))
    cache = rp.RouteCache(None)
    calls = []

    def counting(profile, a, b):
        calls.append(profile)
        return _fetch_ok(profile, a, b)

    rp.attach_routes(trips, cache, fetcher=counting, sleep_s=0)
    out2 = rp.attach_routes(trips, cache, fetcher=counting, sleep_s=0)
    assert len(calls) == 1  # second pass served from cache
    assert len(out2) == 1


def test_attach_routes_noroute_cached_error_not():
    t0 = 1591012800
    trips = trip(ci(t0), ci(t0 + 3600, lat=50.0 + 5 / KM_PER_DEG, m="W"))

    cache = rp.RouteCache(None)
    rp.attach_routes(trips, cache, fetcher=lambda *a: ("noroute", None), sleep_s=0)
    assert list(cache.data.values()) == [{"x": 1}]

    cache2 = rp.RouteCache(None)
    out = rp.attach_routes(trips, cache2, fetcher=lambda *a: ("error", None), sleep_s=0)
    assert cache2.data == {} and out == {}  # transient → retried next build


def test_attach_routes_absurd_detour_cached_as_noroute():
    t0 = 1591012800
    trips = trip(ci(t0), ci(t0 + 3600, lat=50.0 + 5 / KM_PER_DEG, m="C"))

    def detour(profile, a, b):
        return "ok", ("abc", 100.0)  # 100 km routed for a 5 km hop

    cache = rp.RouteCache(None)
    out = rp.attach_routes(trips, cache, fetcher=detour, sleep_s=0)
    assert out == {} and list(cache.data.values()) == [{"x": 1}]


def test_attach_routes_budget_limits_fetches():
    t0 = 1591012800
    cks = [ci(t0 + i * 3600, lat=50.0 + i * 5 / KM_PER_DEG, m=("" if i == 0 else "C"))
           for i in range(5)]  # 4 segments
    calls = []

    def counting(profile, a, b):
        calls.append(1)
        return _fetch_ok(profile, a, b)

    out = rp.attach_routes(trip(*cks), rp.RouteCache(None),
                           max_fetch=2, fetcher=counting, sleep_s=0)
    assert len(calls) == 2 and len(out) == 2


def test_attach_routes_budget_spends_newest_first():
    # Two trips enumerated oldest-first (as build.py passes them), but the fetch
    # budget is smaller than the backlog.  The NEWEST segments must win the
    # budget — otherwise a fresh trip is starved behind years of old ones.
    old_t = 1_400_000_000
    new_t = 1_700_000_000
    old_trip = {"checkins": [ci(old_t, lat=40.0),
                             ci(old_t + 3600, lat=40.0 + 5 / KM_PER_DEG, m="C")]}
    new_trip = {"checkins": [ci(new_t, lat=52.0),
                             ci(new_t + 3600, lat=52.0 + 5 / KM_PER_DEG, m="C")]}
    out = rp.attach_routes([old_trip, new_trip], rp.RouteCache(None),
                           max_fetch=1, fetcher=_fetch_ok, sleep_s=0)
    assert list(out) == [f"{new_t}-{new_t + 3600}"]  # newest fetched, oldest deferred


def test_attach_routes_skips_unroutable_modes_and_distances():
    t0 = 1591012800
    cks = [
        ci(t0),
        ci(t0 + 1 * 3600, lat=50.0 + 5 / KM_PER_DEG, m="P"),        # plane → arc
        ci(t0 + 2 * 3600, lat=50.0 + 10 / KM_PER_DEG, m="G"),       # gap
        ci(t0 + 3 * 3600, lat=50.0 + 15 / KM_PER_DEG, m=""),        # untagged
        ci(t0 + 4 * 3600, lat=50.0 + 15.01 / KM_PER_DEG, m="W"),    # < MIN_ROUTE_KM
        ci(t0 + 5 * 3600, lat=50.0 + 100 / KM_PER_DEG, m="W"),      # > foot ceiling
    ]
    out = rp.attach_routes(trip(*cks), rp.RouteCache(None),
                           fetcher=_fetch_ok, sleep_s=0)
    assert out == {}


def test_attach_routes_carry_forward_pairing():
    # Coordless check-in between two plotted ones: the key must join the two
    # PLOTTED timestamps, matching the segment the template actually draws.
    t0 = 1591012800
    cks = [ci(t0),
           {"ts": t0 + 1800, "lat": "", "lng": "", "m": ""},
           ci(t0 + 3600, lat=50.0 + 5 / KM_PER_DEG, m="C")]
    out = rp.attach_routes(trip(*cks), rp.RouteCache(None),
                           fetcher=_fetch_ok, sleep_s=0)
    assert list(out) == [f"{t0}-{t0 + 3600}"]


def test_attach_routes_profile_selection():
    t0 = 1591012800
    seen = {}

    def spy(profile, a, b):
        seen[profile] = True
        return _fetch_ok(profile, a, b)

    for i, (m, prof) in enumerate([("W", "foot"), ("B", "bike"),
                                   ("C", "car"), ("U", "car"), ("T", "rail")]):
        base = 40.0 + i * 2  # separate cache keys
        cks = [ci(t0, lat=base), ci(t0 + 3600, lat=base + 5 / KM_PER_DEG, m=m)]
        rp.attach_routes(trip(*cks), rp.RouteCache(None), fetcher=spy, sleep_s=0)
        assert seen.pop(prof, False), f"{m} should route with profile {prof}"


def test_routes_json_values_are_json_safe():
    t0 = 1591012800
    trips = trip(ci(t0), ci(t0 + 3600, lat=50.0 + 5 / KM_PER_DEG, m="T"))
    out = rp.attach_routes(trips, rp.RouteCache(None), fetcher=_fetch_ok, sleep_s=0)
    assert json.loads(json.dumps(out)) == out
