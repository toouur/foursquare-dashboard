# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""route_paths.py — GPS-like route geometry for trip-map segments.

For each mode-tagged segment between two plotted check-ins, fetch the real
street/rail path from free OSM routing services and hand it to the trips
template so the map draws roads and railways instead of straight chords:

    W → FOSSGIS OSRM routed-foot      B → routed-bike
    C/U → routed-car                  T → BRouter profile=rail
    P keeps its great-circle arc      F/G/"" stay straight lines

Everything is cached in a JSON file committed to the private data repo
(like flights.csv), keyed by profile + endpoint coords rounded to 4 dp.
Each build fetches at most `max_fetch` missing routes (politely, one at a
time, with a small sleep), so the cache warms over hourly CI runs and the
map degrades gracefully to straight lines meanwhile.  Logical no-routes
(off-network endpoints, absurd detours) are cached as failures so they are
never re-asked; transient network errors are NOT cached.

Output consumed by templates/trips.html.tmpl via routes.json:
    { "<ts_a>-<ts_b>": [<polyline5>, <routed_km>], ... }
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.request

from flights import _haversine_km
from transport_mode import _coords

OSRM_BASE = "https://routing.openstreetmap.de"
BROUTER_BASE = "https://brouter.de/brouter"
_UA = "foursquare-dashboard/1.0 (personal trip map; github.com/toouur)"

MODE_PROFILE = {"W": "foot", "B": "bike", "C": "car", "U": "car", "T": "rail"}

# Per-profile ceiling (km, straight-line): beyond this a routed path adds
# nothing at the zoom level the map is viewed at, and the services time out.
MAX_ROUTE_KM = {"foot": 40.0, "bike": 200.0, "car": 800.0, "rail": 1200.0}
MIN_ROUTE_KM = 0.05
FETCH_SLEEP_S = 0.2
_RAIL_SIMPLIFY_DEG = 2e-4  # ≈ 20 m Douglas-Peucker tolerance


# ── polyline5 codec (Google encoded polyline, precision 1e-5) ─────────────────

def _enc_int(d: int) -> str:
    d = d << 1
    if d < 0:
        d = ~d
    s = ""
    while d >= 0x20:
        s += chr((0x20 | (d & 0x1F)) + 63)
        d >>= 5
    return s + chr(d + 63)


def encode_polyline(pts: list[tuple[float, float]]) -> str:
    """[(lat, lng), …] → polyline5 string."""
    out, plat, plng = [], 0, 0
    for lat, lng in pts:
        ilat, ilng = round(lat * 1e5), round(lng * 1e5)
        out.append(_enc_int(ilat - plat))
        out.append(_enc_int(ilng - plng))
        plat, plng = ilat, ilng
    return "".join(out)


def decode_polyline(s: str) -> list[tuple[float, float]]:
    """polyline5 string → [(lat, lng), …]."""
    pts: list[tuple[float, float]] = []
    i, lat, lng = 0, 0, 0
    while i < len(s):
        for is_lng in (0, 1):
            shift, result = 0, 0
            while True:
                byte = ord(s[i]) - 63
                i += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lng:
                lng += delta
            else:
                lat += delta
        pts.append((lat / 1e5, lng / 1e5))
    return pts


# ── Douglas-Peucker simplification (for BRouter's dense GeoJSON tracks) ───────

def _pt_seg_d(p, a, b, cosl: float) -> float:
    px, py = p[1] * cosl, p[0]
    ax, ay = a[1] * cosl, a[0]
    bx, by = b[1] * cosl, b[0]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def simplify(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Iterative Douglas-Peucker in a locally-scaled lat/lng plane."""
    if len(pts) <= 2:
        return list(pts)
    cosl = max(math.cos(math.radians(pts[len(pts) // 2][0])), 0.05)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        dmax, imax = 0.0, i0
        for i in range(i0 + 1, i1):
            d = _pt_seg_d(pts[i], pts[i0], pts[i1], cosl)
            if d > dmax:
                dmax, imax = d, i
        if dmax > tol:
            keep[imax] = True
            stack.append((i0, imax))
            stack.append((imax, i1))
    return [p for p, k in zip(pts, keep) if k]


# ── Route fetchers ────────────────────────────────────────────────────────────
# Both return (status, payload): ("ok", (polyline5, km)) — routed;
# ("noroute", None) — service answered but found no path (cacheable);
# ("error", None) — network/transient trouble (never cached).

def _http_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_osrm(profile: str, a: tuple[float, float], b: tuple[float, float]):
    url = (f"{OSRM_BASE}/routed-{profile}/route/v1/driving/"
           f"{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}"
           "?overview=simplified&geometries=polyline")
    try:
        js = _http_json(url, 15)
    except json.JSONDecodeError:
        return "noroute", None
    except Exception:
        return "error", None
    if js.get("code") != "Ok" or not js.get("routes"):
        return "noroute", None
    r = js["routes"][0]
    return "ok", (r["geometry"], float(r["distance"]) / 1000.0)


def _fetch_rail(a: tuple[float, float], b: tuple[float, float]):
    url = (f"{BROUTER_BASE}?lonlats={a[1]:.6f},{a[0]:.6f}|{b[1]:.6f},{b[0]:.6f}"
           "&profile=rail&alternativeidx=0&format=geojson")
    try:
        js = _http_json(url, 30)
    except json.JSONDecodeError:
        return "noroute", None  # BRouter reports no-route as plain text
    except Exception:
        return "error", None
    feats = (js or {}).get("features") or []
    if not feats:
        return "noroute", None
    geom = feats[0].get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return "noroute", None
    km = float((feats[0].get("properties") or {}).get("track-length", 0)) / 1000.0
    pts = simplify([(c[1], c[0]) for c in coords], _RAIL_SIMPLIFY_DEG)
    return "ok", (encode_polyline(pts), km)


def _fetch(profile: str, a: tuple[float, float], b: tuple[float, float]):
    if profile == "rail":
        return _fetch_rail(a, b)
    return _fetch_osrm(profile, a, b)


# ── Persistent cache ──────────────────────────────────────────────────────────

def _key(profile: str, a: tuple[float, float], b: tuple[float, float]) -> str:
    return f"{profile}:{a[0]:.4f},{a[1]:.4f};{b[0]:.4f},{b[1]:.4f}"


class RouteCache:
    """JSON-file cache: key → {"p": polyline5, "km": float} or {"x": 1} (no-route)."""

    def __init__(self, path: str | None = None):
        self.path = path
        self.data: dict[str, dict] = {}
        self.dirty = False
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    self.data = loaded
            except (OSError, ValueError):
                pass  # corrupt/unreadable cache → start fresh, rebuild over time

    def get(self, key: str) -> dict | None:
        return self.data.get(key)

    def put(self, key: str, val: dict) -> None:
        self.data[key] = val
        self.dirty = True

    def save(self) -> None:
        if not (self.path and self.dirty):
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, separators=(",", ":"))
        os.replace(tmp, self.path)
        self.dirty = False


# ── Main entry (called from build.py after mode tagging) ──────────────────────

def attach_routes(trips: list[dict], cache: RouteCache, max_fetch: int = 250,
                  fetcher=None, sleep_s: float = FETCH_SLEEP_S) -> dict[str, list]:
    """→ {"<ts_a>-<ts_b>": [polyline5, routed_km]} for every routable segment.

    Walks each trip's coord-bearing check-in pairs with the same
    carry-forward pairing as transport_mode.classify_segments, so the keys
    line up 1:1 with the segments the map draws.  Serves from `cache`,
    fetching at most `max_fetch` missing routes per call.
    """
    fetch = fetcher or _fetch
    out: dict[str, list] = {}
    budget = max_fetch
    for t in trips:
        prev: tuple[dict, tuple[float, float]] | None = None
        for c in t.get("checkins", []):
            ll = _coords(c)
            if ll is None:
                continue
            a, prev = prev, (c, ll)
            if a is None:
                continue
            profile = MODE_PROFILE.get(c.get("m") or "")
            if not profile:
                continue
            straight = _haversine_km(a[1][0], a[1][1], ll[0], ll[1])
            if straight < MIN_ROUTE_KM or straight > MAX_ROUTE_KM[profile]:
                continue
            key = _key(profile, a[1], ll)
            hit = cache.get(key)
            if hit is None:
                if budget <= 0:
                    continue
                budget -= 1
                status, val = fetch(profile, a[1], ll)
                if status == "error":
                    continue  # transient — retry on a future build
                if status == "ok":
                    poly, km = val
                    # A routed path wildly longer than the crow flies means the
                    # router snapped an endpoint to the wrong network — worse
                    # than a straight line.  Cache as no-route.
                    if km > max(straight * 4, straight + 3):
                        hit = {"x": 1}
                    else:
                        hit = {"p": poly, "km": round(km, 2)}
                else:
                    hit = {"x": 1}
                cache.put(key, hit)
                if sleep_s:
                    time.sleep(sleep_s)
            if "p" in hit:
                out[f"{int(a[0]['ts'])}-{int(c['ts'])}"] = [hit["p"], hit["km"]]
    return out
