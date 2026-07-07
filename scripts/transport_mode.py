# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""transport_mode.py — per-segment transport-mode inference.

For each pair of consecutive check-ins (A → B) infer how the traveller got
from A to B.  Three signal families, combined as a rule cascade with a
self-training statistical layer for the ambiguous middle band:

  1. FR24 flight diary — a diary flight whose calendar day intersects
     [A.ts, B.ts] plus a ≥100 km jump is authoritative: flight.
  2. Venue categories — exact-name anchor sets (in-vehicle categories like
     "Train"/"Plane"/"Bus Line" at either endpoint; station categories at
     the departure endpoint).  Exact names only: substring matching would
     drag in "Police Station", "TV Station", "Stationery Store"…
  3. Speed & distance bands — v = d / Δt is a LOWER BOUND of true travel
     speed (Δt includes dwell time at B), so bands lean on distance too
     and never over-claim.

The self-training layer is a dependency-free Gaussian Naive Bayes on
(log10 speed, log10 distance).  Every build it re-harvests weak labels from
the rule anchors (FR24 legs, category-anchored segments, unambiguous walk
crawls, bicycle-tagged trips) and re-fits — so it keeps teaching itself as
new check-ins arrive.  It only adjudicates segments the rules leave
ambiguous, and its verdict is clamped to modes physically plausible for
the observed speed/distance.

Mode codes (single char, embedded in TRIPS_JSON as checkin["m"]):
    W walk · B bike · C car/road · T train/metro/tram · U bus
    F ferry/boat · P plane · G gap (Δt too large to infer) · "" none

Exposed surface (called from build.py):
    flight_windows(flights)            → sorted [(ts0, ts1), …]
    train_model(checkin_seqs, windows) → model (or None)
    classify_segments(checkins, windows, bike=False, model=None) → [mode, …]
        one mode char per check-in: modes[i] is how the traveller ARRIVED
        at checkins[i] (modes[0] == "").
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from flights import _haversine_km

# ── Category anchors (exact venue-category names, from real data) ─────────────

# In-vehicle categories: being checked in at one at EITHER endpoint of a
# segment means you were on that vehicle for the segment.
IN_VEHICLE = {
    "Plane": "P",
    "Train": "T",
    "Bus Line": "U",
    "Boat or Ferry": "F",
}

# Departure-station categories: a check-in HERE is usually followed by
# boarding, so they anchor the segment that LEAVES them (and, weaker, the
# one arriving — people also check in when getting off).
STATION = {
    "Airport": "P", "International Airport": "P", "Airport Terminal": "P",
    "Airport Gate": "P", "Airport Lounge": "P",
    "Rail Station": "T", "Train Station": "T", "Metro Station": "T",
    "Tram Station": "T", "Light Rail Station": "T", "Platform": "T",
    "Airport Tram Station": "T", "Monorail": "T",
    "Bus Station": "U", "Bus Stop": "U", "Bus Terminal": "U",
    "Ferry Terminal": "F", "Pier": "F", "Harbor or Marina": "F", "Port": "F",
}

# Weak hints — used ONLY as training labels for the NB layer, never as
# rule overrides (a Fuel Station check-in is a good hint you're driving,
# but not proof the previous segment was by car).
CAR_HINT = {"Fuel Station", "Gas Station", "Parking", "Rest Area", "Toll Booth"}
BIKE_HINT = {"Bike Trail", "Bike Rental / Bike Share"}

# Physical plausibility per mode: (max speed km/h, max distance km).
# The NB layer may only pick a mode the segment's (v, d) can support;
# v is a lower bound so only the UPPER limits are trustworthy.
_PLAUSIBLE = {
    "W": (10, 20), "B": (32, 120), "C": (160, 900),
    "T": (280, 1500), "U": (130, 1200), "F": (80, 600),
}

# Band thresholds (km/h, km)
SAME_PLACE_KM = 0.05
FLIGHT_MIN_KM = 100.0
FLIGHT_SPEED = 150.0
FLIGHT_DIST = 500.0
DRIVE_SPEED = 25.0
WALK_SPEED = 7.0
WALK_MAX_KM = 8.0
GAP_HOURS = 18.0

MODE_META = {  # char → (emoji, css colour, label) — mirrored in the templates
    "W": ("🚶", "#7dc87d", "walked"),
    "B": ("🚴", "#5abf9e", "cycled"),
    "C": ("🚗", "#e8b86d", "drove"),
    "T": ("🚆", "#6da8e8", "train"),
    "U": ("🚌", "#c9a0e8", "bus"),
    "F": ("⛴️", "#5bc8d8", "ferry"),
    "P": ("✈️", "#e87a7a", "flew"),
    "G": ("·", "#666666", "gap"),
}


# ── FR24 diary → time windows ─────────────────────────────────────────────────

def flight_windows(flights: list[dict], slack_h: float = 3.0) -> list[tuple[int, int]]:
    """Each diary flight becomes a UTC window (day start − slack, day end + slack).

    FR24 dep/arr times are airport-local with no offsets, so the calendar
    day (± slack for red-eyes/timezones) is the honest resolution.
    """
    out = []
    slack = int(slack_h * 3600)
    for f in flights or []:
        try:
            d0 = datetime.strptime(f["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        ts0 = int(d0.timestamp())
        out.append((ts0 - slack, ts0 + 86400 + slack))
    out.sort()
    return out


def _in_flight_window(ts_a: int, ts_b: int, windows: list[tuple[int, int]]) -> bool:
    for w0, w1 in windows:
        if w0 > ts_b:
            break
        if ts_a <= w1 and ts_b >= w0:
            return True
    return False


# ── Gaussian Naive Bayes (pure python, 2 features) ────────────────────────────

_MIN_CLASS_SAMPLES = 8
_MIN_VAR = 1e-3


def _features(v_kmh: float, d_km: float) -> tuple[float, float]:
    return (math.log10(max(v_kmh, 0.05)), math.log10(max(d_km, 0.01)))


def _fit_nb(samples: list[tuple[float, float, str]]):
    """samples: [(f1, f2, mode)] → model dict or None if too thin."""
    by_cls: dict[str, list[tuple[float, float]]] = {}
    for f1, f2, m in samples:
        by_cls.setdefault(m, []).append((f1, f2))
    model = {}
    total = 0
    for m, pts in by_cls.items():
        if len(pts) < _MIN_CLASS_SAMPLES:
            continue
        n = len(pts)
        mu1 = sum(p[0] for p in pts) / n
        mu2 = sum(p[1] for p in pts) / n
        var1 = max(sum((p[0] - mu1) ** 2 for p in pts) / n, _MIN_VAR)
        var2 = max(sum((p[1] - mu2) ** 2 for p in pts) / n, _MIN_VAR)
        model[m] = [n, mu1, var1, mu2, var2]
        total += n
    if len(model) < 2:
        return None
    for m in model:
        model[m].append(math.log(model[m][0] / total))  # log prior
    return model


def _nb_predict(model, f1: float, f2: float, allowed: set[str]) -> str | None:
    best, best_lp = None, -math.inf
    for m, (_, mu1, var1, mu2, var2, lp) in model.items():
        if m not in allowed:
            continue
        lp_m = lp
        lp_m -= 0.5 * (math.log(2 * math.pi * var1) + (f1 - mu1) ** 2 / var1)
        lp_m -= 0.5 * (math.log(2 * math.pi * var2) + (f2 - mu2) ** 2 / var2)
        if lp_m > best_lp:
            best, best_lp = m, lp_m
    return best


# ── Segment geometry ──────────────────────────────────────────────────────────

def _segment(a: dict, b: dict):
    """→ (d_km, dt_h, v_kmh) or None when coords/order make it meaningless."""
    try:
        la1, lo1 = float(a["lat"]), float(a["lng"])
        la2, lo2 = float(b["lat"]), float(b["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    d = _haversine_km(la1, lo1, la2, lo2)
    dt = (int(b["ts"]) - int(a["ts"])) / 3600.0
    if dt <= 0:
        dt = 1 / 60.0  # same-minute double check-in
    return d, dt, d / dt


def _plausible(v: float, d: float) -> set[str]:
    # v is a lower bound of true speed → only prune modes whose MAX the
    # observed v already exceeds, or whose range d exceeds.
    return {m for m, (vmax, dmax) in _PLAUSIBLE.items() if v <= vmax and d <= dmax}


# ── Rule cascade ──────────────────────────────────────────────────────────────

def _anchor_mode(a: dict, b: dict, d: float, v: float) -> str | None:
    """Category-anchored mode for segment a→b, or None."""
    cat_a = (a.get("category") or "").strip()
    cat_b = (b.get("category") or "").strip()
    # In-vehicle beats stations; departure beats arrival.
    for cat in (cat_a, cat_b):
        m = IN_VEHICLE.get(cat)
        if m and _sane(m, d, v):
            return m
    for cat in (cat_a, cat_b):
        m = STATION.get(cat)
        if m and _sane(m, d, v):
            return m
    return None


def _sane(mode: str, d: float, v: float) -> bool:
    """Sanity-check an anchor against geometry (a metro check-in followed by
    a 900 km hop is not a metro ride)."""
    if mode == "P":
        return d >= FLIGHT_MIN_KM
    vmax, dmax = _PLAUSIBLE[mode]
    return v <= vmax and d <= dmax


def _band_mode(v: float, d: float, bike: bool) -> str:
    if v >= FLIGHT_SPEED or d >= FLIGHT_DIST:
        return "P"
    if v >= DRIVE_SPEED:
        return "C"
    if v >= WALK_SPEED:
        return "B" if bike else "C"
    if d < WALK_MAX_KM:
        return "W"
    return "C"  # slow apparent speed but a real distance → dwell hid a ride


def classify_segments(checkins: list[dict], windows: list[tuple[int, int]],
                      bike: bool = False, model=None) -> list[str]:
    """One arrival-mode char per check-in (element 0 is always "").

    checkins must be time-ascending dicts with ts/lat/lng/category.
    """
    modes = [""] * len(checkins)
    for i in range(1, len(checkins)):
        a, b = checkins[i - 1], checkins[i]
        seg = _segment(a, b)
        if seg is None:
            continue
        d, dt, v = seg
        if d < SAME_PLACE_KM:
            continue
        if d >= FLIGHT_MIN_KM and _in_flight_window(int(a["ts"]), int(b["ts"]), windows):
            modes[i] = "P"
            continue
        anchor = _anchor_mode(a, b, d, v)
        if anchor:
            modes[i] = anchor
            continue
        if dt > GAP_HOURS:
            modes[i] = "G"
            continue
        band = _band_mode(v, d, bike)
        # Ambiguous middle band → let the self-trained layer adjudicate. Only the
        # generic "C" bucket: bike-trip "B" is already trip-tag evidence, and the
        # thin B class (~200 samples) would lose every segment to the far larger
        # T/C priors.
        if model and band == "C" and WALK_SPEED <= v < FLIGHT_SPEED:
            allowed = _plausible(v, d)
            if not bike:
                allowed.discard("B")
            pick = _nb_predict(model, *_features(v, d), allowed)
            if pick:
                band = pick
        modes[i] = band
    return modes


# ── Self-training: harvest weak labels from rule anchors, fit NB ──────────────

def _harvest(checkins: list[dict], windows: list[tuple[int, int]],
             bike: bool, out: list[tuple[float, float, str]]) -> None:
    for i in range(1, len(checkins)):
        a, b = checkins[i - 1], checkins[i]
        seg = _segment(a, b)
        if seg is None:
            continue
        d, dt, v = seg
        if d < SAME_PLACE_KM or dt > GAP_HOURS:
            continue
        label = None
        if d >= FLIGHT_MIN_KM and _in_flight_window(int(a["ts"]), int(b["ts"]), windows):
            label = "P"
        else:
            label = _anchor_mode(a, b, d, v)
        if not label:
            cat_a = (a.get("category") or "").strip()
            cat_b = (b.get("category") or "").strip()
            if (cat_a in CAR_HINT or cat_b in CAR_HINT) and _sane("C", d, v):
                label = "C"
            elif (cat_a in BIKE_HINT or cat_b in BIKE_HINT) and _sane("B", d, v):
                label = "B"
            elif bike and WALK_SPEED <= v < 25 and _sane("B", d, v):
                label = "B"
            elif v < 4 and d < 3:
                label = "W"  # unambiguous stroll
            elif 30 <= v < 130 and d < 600:
                label = "C"  # confident band core, densifies the C cloud
        if label:
            out.append((*_features(v, d), label))


def train_model(checkin_seqs: list[list[dict]], windows: list[tuple[int, int]],
                bike_flags: list[bool] | None = None):
    """Fit the NB layer on weak labels harvested from every sequence.

    checkin_seqs: list of time-ascending check-in lists (e.g. one per trip).
    Returns a model for classify_segments(), or None if labels are too thin
    (classifier then falls back to pure bands — never worse than rules).
    """
    samples: list[tuple[float, float, str]] = []
    for k, seq in enumerate(checkin_seqs):
        bike = bool(bike_flags[k]) if bike_flags else False
        _harvest(seq, windows, bike, samples)
    return _fit_nb(samples)
