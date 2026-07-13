# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Offline tests for the Last.fm per-year scrobble aggregation
(scripts/fetch_lastfm.py). No network — exercises add_scrobble / derive_aggregate
/ bootstrap on synthetic data only."""
import json
from datetime import datetime, timezone

import fetch_lastfm as lf


def _ts(y, mo=6, d=15):
    return int(datetime(y, mo, d, 12, 0, tzinfo=timezone.utc).timestamp())


def test_add_scrobble_counts_year_month_and_artist():
    st = lf.new_state("u")
    lf.add_scrobble(st, "Aphex Twin", _ts(2020, 3))
    lf.add_scrobble(st, "Aphex Twin", _ts(2020, 3))
    lf.add_scrobble(st, "Boards of Canada", _ts(2020, 7))
    y = st["years"]["2020"]
    assert y["scrobbles"] == 3
    assert y["artists"]["Aphex Twin"] == 2
    assert y["months"][2] == 2   # March
    assert y["months"][6] == 1   # July
    assert st["total"] == 3
    assert st["last_ts"] == _ts(2020, 7)


def test_add_scrobble_ignores_blank_artist():
    st = lf.new_state("u")
    lf.add_scrobble(st, "", _ts(2021))
    lf.add_scrobble(st, "   ", _ts(2021))
    assert st["total"] == 0
    assert st["years"] == {}


def test_derive_aggregate_picks_top_artist_and_shape():
    st = lf.new_state("u")
    for _ in range(5):
        lf.add_scrobble(st, "The Cure", _ts(2019))
    for _ in range(3):
        lf.add_scrobble(st, "Slowdive", _ts(2019))
    agg = lf.derive_aggregate(st)
    assert agg["total_scrobbles"] == 8
    y = agg["years"]["2019"]
    assert y["scrobbles"] == 8
    assert y["top_artist"] == {"name": "The Cure", "plays": 5}
    assert len(y["months"]) == 12
    assert "artists" not in y            # heavy counter dropped from the aggregate
    assert "generated_at_utc" in agg


def test_derive_aggregate_tie_break_is_deterministic():
    # Equal play counts -> alphabetically-first artist wins, stable across runs.
    st = lf.new_state("u")
    lf.add_scrobble(st, "Zola Jesus", _ts(2018))
    lf.add_scrobble(st, "Air", _ts(2018))
    assert lf.derive_aggregate(st)["years"]["2018"]["top_artist"]["name"] == "Air"


def test_bootstrap_from_export(tmp_path):
    export = {
        "username": "TESTER",
        "scrobbles": [
            {"artist": "Portishead", "date": _ts(2015) * 1000},
            {"artist": "Portishead", "date": _ts(2015, 8) * 1000},
            {"artist": "Massive Attack", "date": _ts(2016) * 1000},
            {"artist": "", "date": _ts(2016) * 1000},        # skipped
            {"artist": "Tricky"},                            # no date -> skipped
        ],
    }
    p = tmp_path / "export.json"
    p.write_text(json.dumps(export), encoding="utf-8")
    st = lf.bootstrap_from_export(p, "fallback")
    assert st["user"] == "TESTER"
    assert st["total"] == 3
    assert st["years"]["2015"]["scrobbles"] == 2
    assert st["years"]["2016"]["scrobbles"] == 1
    agg = lf.derive_aggregate(st)
    assert agg["years"]["2015"]["top_artist"]["name"] == "Portishead"
