# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
Tests for the /v2/venues/search fallback in refresh_backfill_venue.

/v2/venues/{id} is premium and 402s for tokens that lost premium access, so the
script falls back to the free search endpoint. The fallback must key off the
known venue_id — never off the name — or a same-named venue would silently
overwrite a reconstructed row with the wrong coordinates.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import refresh_backfill_venue as rbv  # noqa: E402

VID = "4d502fd1c5ff6ea80d1d9807"
LL = "47.025,28.835"


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code, self.text = payload, status, str(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


def _payload(*venues):
    return {"meta": {"code": 200}, "response": {"venues": list(venues)}}


def _venue(vid, name, lat, lng):
    return {"id": vid, "name": name,
            "location": {"lat": lat, "lng": lng, "address": "Str. Test 1",
                         "city": "Chișinău", "country": "Moldova"},
            "categories": [{"name": "Rock Club", "primary": True}]}


def _patch_get(monkeypatch, resp, seen=None):
    def fake_get(url, params=None, timeout=None):
        if seen is not None:
            seen.append((url, params))
        return resp
    monkeypatch.setattr(rbv.requests, "get", fake_get)


def test_search_picks_the_matching_id_not_the_first_hit(monkeypatch):
    other = _venue("ffffffffffffffffffffffff", "Gin Do & Contrabass", 1.0, 2.0)
    mine = _venue(VID, "Gin Do & Contrabass", 47.0301, 28.8402)
    _patch_get(monkeypatch, _Resp(_payload(other, mine)))

    found = rbv.search_venue("tok", VID, "Gin Do & Contrabass", LL)

    assert found is not None and found["id"] == VID
    assert rbv.venue_to_patch(found)["lat"] == "47.0301"


def test_search_returns_none_when_id_absent(monkeypatch):
    """A closed venue is hidden from search — better nothing than a wrong venue."""
    _patch_get(monkeypatch, _Resp(_payload(_venue("0" * 24, "Gin Do & Contrabass", 1.0, 2.0))))

    assert rbv.search_venue("tok", VID, "Gin Do & Contrabass", LL) is None


def test_search_query_is_scoped_to_the_row_coordinates(monkeypatch):
    seen: list = []
    _patch_get(monkeypatch, _Resp(_payload()), seen)

    rbv.search_venue("tok", VID, "Gin Do & Contrabass", LL)

    (url, params), = seen
    assert url == rbv.SEARCH_API
    assert params["ll"] == LL and params["query"] == "Gin Do & Contrabass"


def test_search_skipped_without_name_or_coords(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not call the API without a name/ll hint")
    monkeypatch.setattr(rbv.requests, "get", boom)

    assert rbv.search_venue("tok", VID, "", LL) is None
    assert rbv.search_venue("tok", VID, "Gin Do & Contrabass", "") is None


def test_search_tolerates_bad_responses(monkeypatch):
    _patch_get(monkeypatch, _Resp(None, status=500))
    assert rbv.search_venue("tok", VID, "X", LL) is None

    _patch_get(monkeypatch, _Resp(None))          # 200 but unparseable
    assert rbv.search_venue("tok", VID, "X", LL) is None

    _patch_get(monkeypatch, _Resp({"meta": {"code": 403}}))
    assert rbv.search_venue("tok", VID, "X", LL) is None


def test_fetch_venue_402_returns_none_so_the_fallback_runs(monkeypatch):
    _patch_get(monkeypatch, _Resp({"meta": {"code": 402}}, status=402))

    assert rbv.fetch_venue("tok", VID) is None


def test_country_is_normalized_to_the_english_canonical():
    """/venues/search answers in the venue's language; /venues/{id} answered in
    English. An un-normalized value becomes a whole extra country on the
    dashboard, sitting next to the real one."""
    venue = _venue(VID, "Stadionul UTM", 47.06, 28.86)
    venue["location"]["country"] = "Republica Moldova"

    assert rbv.venue_to_patch(venue)["country"] == "Moldova"


def test_unknown_country_passes_through_untouched():
    venue = _venue(VID, "Somewhere", 1.0, 2.0)
    venue["location"]["country"] = "Kingdom of Nowhere"

    assert rbv.venue_to_patch(venue)["country"] == "Kingdom of Nowhere"


def test_country_aliases_config_is_readable_and_covers_moldova():
    assert rbv.CTRY_NORM.get("Republica Moldova") == "Moldova"
