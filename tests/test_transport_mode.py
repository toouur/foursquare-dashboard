# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Offline tests for scripts/transport_mode.py — the per-segment transport-mode
classifier (rule cascade + self-training Naive Bayes layer).

Coordinates are synthetic: 1° of latitude ≈ 111.19 km, so distances are
controlled by latitude deltas alone (longitude fixed at 0).
"""
import transport_mode as tm

KM_PER_DEG = 111.19


def ci(ts, lat=50.0, lng=0.0, category="", venue=""):
    """Minimal trip check-in dict as metrics.py emits them."""
    return {"ts": ts, "lat": lat, "lng": lng, "category": category, "venue": venue}


def pair(d_km, dt_h, cat_a="", cat_b=""):
    """Two check-ins d_km apart, dt_h hours apart, starting 2020-06-01 12:00 UTC."""
    t0 = 1591012800
    return [ci(t0, lat=50.0, category=cat_a),
            ci(t0 + int(dt_h * 3600), lat=50.0 + d_km / KM_PER_DEG, category=cat_b)]


def classify(checkins, windows=(), bike=False, model=None):
    return tm.classify_segments(checkins, list(windows), bike=bike, model=model)


# ── Basics: element 0, same place, missing coords ─────────────────────────────

def test_first_element_always_empty():
    assert classify(pair(5, 1))[0] == ""


def test_same_place_no_mode():
    assert classify(pair(0.01, 2)) == ["", ""]


def test_missing_coords_no_mode():
    t0 = 1590926400
    rows = [ci(t0), {"ts": t0 + 3600, "lat": "", "lng": "", "category": ""}]
    assert classify(rows) == ["", ""]


def test_empty_and_single_input():
    assert classify([]) == []
    assert classify([ci(1590926400)]) == [""]


def test_carry_forward_across_coordless_checkin():
    # A coordless check-in between two plotted ones must not blank the segment:
    # the second plotted point pairs against the FIRST one (as the map draws it).
    t0 = 1591012800
    rows = [ci(t0, lat=50.0),
            {"ts": t0 + 1800, "lat": "", "lng": "", "category": ""},
            ci(t0 + 3600, lat=50.0 + 60 / KM_PER_DEG)]
    assert classify(rows) == ["", "", "C"]  # 60 km in 1 h, paired across the gap


def test_zero_zero_coords_treated_as_missing():
    # (0,0) is what unparsed rows carry; the template drops them (c.lat&&c.lng),
    # so the classifier must carry forward across them too.
    t0 = 1591012800
    rows = [ci(t0, lat=50.0),
            ci(t0 + 1800, lat=0.0, lng=0.0),
            ci(t0 + 3600, lat=50.0 + 1.5 / KM_PER_DEG)]
    assert classify(rows) == ["", "", "W"]  # 1.5 km in 1 h → walk


# ── Speed/distance bands ──────────────────────────────────────────────────────

def test_band_walk():
    # 1.5 km in 30 min → 3 km/h
    assert classify(pair(1.5, 0.5))[1] == "W"


def test_band_drive():
    # 60 km in 1 h → 60 km/h
    assert classify(pair(60, 1))[1] == "C"


def test_band_flight_by_speed():
    # 800 km in 2 h → 400 km/h
    assert classify(pair(800, 2))[1] == "P"


def test_band_flight_by_distance():
    # 600 km in 10 h → only 60 km/h, but ≥500 km range → plane
    assert classify(pair(600, 10))[1] == "P"


def test_band_bike_trip_midspeed():
    # 15 km/h on a bicycle-tagged trip → bike; same segment untagged → car
    assert classify(pair(15, 1), bike=True)[1] == "B"
    assert classify(pair(15, 1), bike=False)[1] == "C"


def test_dwell_lower_bound_long_slow_is_ride():
    # 40 km in 12 h → 3.3 km/h apparent, but 40 km is not a stroll
    assert classify(pair(40, 12))[1] == "C"


def test_gap_over_18h():
    assert classify(pair(50, 30))[1] == "G"


def test_same_minute_double_checkin_does_not_crash():
    t0 = 1590926400
    rows = [ci(t0, lat=50.0), ci(t0, lat=50.02)]  # dt=0, ~2.2 km
    modes = classify(rows)
    assert modes[1] != ""  # clamped dt → classified, not skipped


# ── FR24 flight windows ───────────────────────────────────────────────────────

def _windows_for(day="2020-06-01"):
    return tm.flight_windows([{"date": day}])


def test_flight_window_match():
    # 300 km hop inside a diary-flight day → plane even at low apparent speed
    modes = classify(pair(300, 8), windows=_windows_for("2020-06-01"))
    assert modes[1] == "P"


def test_flight_window_wrong_day_no_match():
    # same hop but the diary flight was weeks earlier → falls through to bands
    modes = classify(pair(300, 8), windows=_windows_for("2020-05-01"))
    assert modes[1] == "C"  # 37.5 km/h band


def test_flight_window_short_hop_not_flight():
    # diary day matches but only 20 km moved → not a flight
    modes = classify(pair(20, 1), windows=_windows_for("2020-06-01"))
    assert modes[1] == "C"


def test_flight_windows_sorted_and_slack():
    ws = tm.flight_windows([{"date": "2020-06-02"}, {"date": "2020-06-01"}])
    assert ws == sorted(ws)
    w0, w1 = ws[0]
    assert w1 - w0 == 86400 + 2 * 3 * 3600  # day + slack both sides


def test_flight_windows_bad_rows_skipped():
    assert tm.flight_windows([{"date": "not-a-date"}, {}, {"date": "2020-06-01"}]) \
        == tm.flight_windows([{"date": "2020-06-01"}])


# ── Category anchors ──────────────────────────────────────────────────────────

def test_station_anchor_train():
    # 100 km in 1 h from a Train Station → train
    assert classify(pair(100, 1, cat_a="Train Station"))[1] == "T"


def test_in_vehicle_beats_station():
    # Bus Line (in-vehicle) at B beats Train Station at A
    assert classify(pair(50, 1, cat_a="Train Station", cat_b="Bus Line"))[1] == "U"


def test_anchor_arrival_endpoint_counts():
    assert classify(pair(30, 0.7, cat_b="Metro Station"))[1] == "T"


def test_airport_anchor_needs_real_distance():
    # Airport at A but only 5 km moved → not a flight, band decides (walk-ish)
    modes = classify(pair(5, 2, cat_a="Airport"))
    assert modes[1] != "P"


def test_metro_anchor_rejected_for_900km_hop():
    # Metro Station followed by a 900 km hop → _sane rejects T (dmax 1500 ok
    # but vmax 280 exceeded at 900 km/h)
    assert classify(pair(900, 1, cat_a="Metro Station"))[1] == "P"


def test_no_substring_false_positives():
    # Police Station / TV Station must NOT anchor as train
    for cat in ("Police Station", "TV Station", "Stationery Store"):
        assert classify(pair(10, 0.5, cat_a=cat))[1] == "C"


def test_car_hint_is_not_a_rule_anchor():
    # Fuel Station is training-only: a 2 km 20-min hop past one stays a walk
    assert classify(pair(2, 1, cat_a="Fuel Station"))[1] == "W"


# ── Naive Bayes layer ─────────────────────────────────────────────────────────

def _synthetic_trainset():
    """Two well-separated clusters: walks (3 km/h, 1.5 km) and drives (60, 60)."""
    seqs = []
    for _ in range(10):
        seqs.append(pair(1.5, 0.5))          # v=3  → 'W' harvest label
        seqs.append(pair(60, 1))             # v=60 → 'C' harvest label (band core)
    return seqs


def test_train_model_roundtrip():
    model = tm.train_model(_synthetic_trainset(), [])
    assert model is not None
    assert set(model) >= {"W", "C"}
    for m, params in model.items():
        n, mu1, var1, mu2, var2, lp = params
        assert n >= tm._MIN_CLASS_SAMPLES and var1 >= tm._MIN_VAR and lp < 0


def test_train_model_too_thin_returns_none():
    # 3 walks only → single thin class → None (band fallback)
    assert tm.train_model([pair(1.5, 0.5)] * 3, []) is None


def test_nb_only_adjudicates_ambiguous_band():
    # NB never overrides a rule anchor or the walk band
    model = tm.train_model(_synthetic_trainset(), [])
    assert classify(pair(1.5, 0.5), model=model)[1] == "W"          # below band
    assert classify(pair(100, 1, cat_a="Train Station"), model=model)[1] == "T"


def test_nb_never_overrides_bike_band():
    # Bike-trip band-B is trip-tag evidence; NB (trained without B) must not
    # steal it even though 15 km/h sits inside its adjudication speed range.
    model = tm.train_model(_synthetic_trainset(), [])
    assert classify(pair(15, 1), bike=True, model=model)[1] == "B"


def test_nb_clamped_to_plausible():
    # Even a model trained only on W/C can't call a 200-km 20-km/h segment 'W'
    model = tm.train_model(_synthetic_trainset(), [])
    got = classify(pair(200, 10), model=model)[1]
    assert got in tm._plausible(20, 200)
    assert got != "W"


def test_plausible_prunes_by_upper_limits_only():
    allowed = tm._plausible(20, 5)  # 20 km/h over 5 km
    assert "W" not in allowed       # vmax 10 exceeded
    assert {"B", "C", "T", "U", "F"} <= allowed


# ── Harvest labels ────────────────────────────────────────────────────────────

def _harvest_labels(rows, windows=(), bike=False):
    out = []
    tm._harvest(rows, list(windows), bike, out)
    return [m for _, _, m in out]


def test_harvest_flight_label():
    assert _harvest_labels(pair(300, 8), windows=_windows_for("2020-06-01")) == ["P"]


def test_harvest_car_hint_label():
    assert _harvest_labels(pair(20, 0.5, cat_b="Fuel Station")) == ["C"]


def test_harvest_bike_trip_label():
    assert _harvest_labels(pair(15, 1), bike=True) == ["B"]


def test_harvest_skips_gaps_and_same_place():
    assert _harvest_labels(pair(50, 30)) == []     # gap
    assert _harvest_labels(pair(0.01, 1)) == []    # same place
