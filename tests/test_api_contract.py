# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Live API contract tests for the Cloudflare Pages Functions:

  /api/search  (functions/api/search.js)
  /api/feed    (functions/api/feed.js)

These hit the production site (https://4sq.pages.dev) — they verify the
response *contract* the front-end depends on, not the data itself.
Marked `live`; skip offline runs with:  pytest -m "not live"
Network failures skip (not fail) so flaky Wi-Fi never breaks a local run.
"""
import json
import re
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.live

BASE = "https://4sq.pages.dev"
TIMEOUT = 30

FEED_CACHE_CONTROL = "public, max-age=60, s-maxage=3600, stale-while-revalidate=600"

# 12-element feed tuple:
# [ts, date, time, venue, city, country, category, venue_id, lat, lng, id, companions]
FEED_TUPLE_LEN = 12
DATE_RE = re.compile(r"^\d{2} [A-Z][a-z]{2} \d{4}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")

_cache = {}


def _request(path, method="GET"):
    """GET/OPTIONS against the live site; skip the test on network trouble."""
    key = (method, path)
    if key in _cache:
        return _cache[key]
    req = urllib.request.Request(
        BASE + path, method=method,
        headers={"User-Agent": "foursquare-dashboard-contract-tests"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            out = (resp.status, dict(resp.headers), body)
    except urllib.error.HTTPError as e:  # 4xx/5xx are part of the contract
        out = (e.code, dict(e.headers), e.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        pytest.skip(f"live API unreachable: {e}")
    _cache[key] = out
    return out


def get_json(path):
    status, headers, body = _request(path)
    return status, headers, json.loads(body.decode("utf-8"))


def assert_feed_item_shape(item):
    assert isinstance(item, list) and len(item) == FEED_TUPLE_LEN
    ts, date, time_, venue, city, country, cat, vid, lat, lng, cid, comps = item
    assert isinstance(ts, int) and ts > 0
    assert DATE_RE.match(date), date
    assert TIME_RE.match(time_), time_
    for s in (venue, city, country, cat, vid, cid):
        assert isinstance(s, str)
    assert lat is None or isinstance(lat, (int, float))
    assert lng is None or isinstance(lng, (int, float))
    assert isinstance(comps, list)
    assert all(isinstance(c, str) and c != "-" for c in comps)


# ---------------------------------------------------------------------------
# /api/search
# ---------------------------------------------------------------------------

SEARCH_KEYS = {"venue", "city", "trip", "tip", "companion"}


class TestSearchContract:
    def test_response_has_five_result_arrays(self):
        status, _, data = get_json("/api/search?q=minsk")
        assert status == 200
        assert SEARCH_KEYS <= set(data)
        for k in SEARCH_KEYS:
            assert isinstance(data[k], list)

    def test_short_query_returns_empty_arrays(self):
        # q shorter than 2 chars → 200 with five empty arrays (no DB hit)
        status, _, data = get_json("/api/search?q=a")
        assert status == 200
        assert all(data[k] == [] for k in SEARCH_KEYS)

    def test_no_store_cache_header(self):
        _, headers, _ = get_json("/api/search?q=minsk")
        assert headers.get("Cache-Control") == "no-store"

    def test_venue_item_shape(self):
        _, _, data = get_json("/api/search?q=minsk")
        assert data["venue"], "expected venue hits for 'minsk'"
        v = data["venue"][0]
        assert v["t"] == "venue"
        assert set(v) >= {"n", "c", "co", "cat", "cnt"}
        assert isinstance(v["cnt"], int) and v["cnt"] >= 1

    def test_city_item_shape(self):
        _, _, data = get_json("/api/search?q=minsk")
        assert data["city"], "expected city hits for 'minsk'"
        c = data["city"][0]
        assert c["t"] == "city"
        assert set(c) >= {"n", "co", "cnt"}
        assert "minsk" in c["n"].lower()

    def test_tip_item_shape_and_truncation(self):
        _, _, data = get_json("/api/search?q=coffee")
        for tip in data["tip"]:
            assert tip["t"] == "tip"
            assert set(tip) >= {"n", "tx", "c", "co"}
            assert len(tip["tx"]) <= 121  # 120 chars + ellipsis

    def test_companion_items_capped_and_no_dash_sentinel(self):
        _, _, data = get_json("/api/search?q=da")
        assert len(data["companion"]) <= 20
        for comp in data["companion"]:
            assert comp["t"] == "companion"
            assert comp["n"] != "-"
            assert isinstance(comp["cnt"], int) and comp["cnt"] >= 1

    def test_all_words_must_match(self):
        # Client-side matches() post-filter: every word must appear
        _, _, data = get_json("/api/search?q=minsk%20zzqxjw")
        assert all(data[k] == [] for k in SEARCH_KEYS)

    def test_options_preflight(self):
        status, _, body = _request("/api/search", method="OPTIONS")
        assert status == 204
        assert body == b""


# ---------------------------------------------------------------------------
# /api/feed
# ---------------------------------------------------------------------------


class TestFeedContract:
    def test_default_page_shape(self):
        status, _, data = get_json("/api/feed?_v=companions")
        assert status == 200
        assert set(data) >= {"items", "has_more", "next_cursor", "next_cursor_id"}
        assert len(data["items"]) == 20  # default limit
        assert data["has_more"] is True  # 65K+ check-ins in the DB
        for item in data["items"]:
            assert_feed_item_shape(item)

    def test_cache_header_matches_documented_value(self):
        _, headers, _ = get_json("/api/feed?_v=companions")
        assert headers.get("Cache-Control") == FEED_CACHE_CONTROL

    def test_items_sorted_newest_first(self):
        _, _, data = get_json("/api/feed?_v=companions")
        ts = [it[0] for it in data["items"]]
        assert ts == sorted(ts, reverse=True)

    def test_next_cursor_is_last_item_ts(self):
        _, _, data = get_json("/api/feed?_v=companions")
        assert data["next_cursor"] == data["items"][-1][0]
        assert data["next_cursor_id"] == data["items"][-1][10]

    def test_limit_respected_and_clamped(self):
        _, _, small = get_json("/api/feed?limit=5&_v=companions")
        assert len(small["items"]) == 5
        _, _, big = get_json("/api/feed?limit=999&_v=companions")
        assert len(big["items"]) == 50  # server-side max

    def test_cursor_pages_strictly_older(self):
        _, _, page1 = get_json("/api/feed?limit=5&_v=companions")
        cur = page1["next_cursor"]
        _, _, page2 = get_json(f"/api/feed?limit=5&cursor={cur}&_v=companions")
        assert page2["items"], "cursor page must not be empty"
        assert all(it[0] < cur for it in page2["items"])

    def test_after_returns_strictly_newer(self):
        # Anchor at the 5th-newest item, ask for newer → must get the newest 4
        _, _, page = get_json("/api/feed?limit=5&_v=companions")
        anchor = page["items"][-1][0]
        _, _, newer = get_json(f"/api/feed?after={anchor}&limit=50&_v=companions")
        assert "has_more" in newer
        assert all(it[0] > anchor for it in newer["items"])
        # newest-first ordering
        ts = [it[0] for it in newer["items"]]
        assert ts == sorted(ts, reverse=True)

    def test_oldest_fast_path(self):
        _, _, data = get_json("/api/feed?oldest=1&limit=5&_v=companions")
        assert set(data) >= {"items", "total"}
        assert len(data["items"]) == 5
        ts = [it[0] for it in data["items"]]
        assert ts == sorted(ts, reverse=True)  # newest-first, caller reverses
        # These really are the oldest: all before the newest page's oldest item
        _, _, newest = get_json("/api/feed?limit=5&_v=companions")
        assert max(ts) < min(it[0] for it in newest["items"])

    def test_month_view_within_padded_bounds(self):
        _, _, data = get_json("/api/feed?month=2023-06&_v=companions")
        assert set(data) >= {"items", "total"}
        assert data["total"] == len(data["items"])
        pad = 86400  # ±1 day UTC padding, client filters to local month
        lo, hi = 1685577600 - pad, 1688169600 + pad  # 2023-06-01..07-01 UTC
        assert all(lo <= it[0] < hi for it in data["items"])

    def test_resolve_returns_cursor(self):
        _, _, data = get_json("/api/feed?resolve=1600000000&_v=companions")
        assert "cursor" in data
        assert data["cursor"] is None or (
            isinstance(data["cursor"], int) and data["cursor"] <= 1600000000)

    def test_resolve_invalid_ts_is_400(self):
        status, _, data = get_json("/api/feed?resolve=notanumber&_v=companions")
        assert status == 400
        assert "error" in data

    def test_text_search_mode(self):
        _, _, data = get_json("/api/feed?q=minsk&_v=companions")
        assert set(data) >= {"items", "total"}
        assert data["total"] == len(data["items"]) <= 500
        for item in data["items"][:5]:
            assert_feed_item_shape(item)

    def test_companions_populated_somewhere_in_feed(self):
        # Regression guard for the _v=companions schema change: at least one
        # of the 50 newest items should carry a non-empty companions array.
        _, _, data = get_json("/api/feed?limit=50&_v=companions")
        assert any(it[11] for it in data["items"]), \
            "no companions in 50 newest items — tuple shape regression?"
