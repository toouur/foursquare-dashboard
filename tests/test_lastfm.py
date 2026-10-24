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


# ── raw layer (scrobbles_<YYYY>.tsv) + the API walk ───────────────────────────

def _page(tracks, page, pages, total):
    return {"recenttracks": {"@attr": {"page": str(page), "totalPages": str(pages), "total": str(total)},
                             "track": tracks}}


def _t(ts, artist, track, album="", now=False):
    t = {"artist": {"#text": artist}, "name": track, "album": {"#text": album}}
    if now:
        t["@attr"] = {"nowplaying": "true"}
    else:
        t["date"] = {"uts": str(ts), "#text": "ignored"}
    return t


def test_fetch_since_reads_every_field_and_pins_the_window(monkeypatch):
    calls = []
    pages = {
        1: _page([_t(0, "Now", "Playing", now=True), _t(300, "Кино", "Звезда по имени Солнце", "Звезда")], 1, 2, 3),
        2: _page([_t(200, "Air", "La Femme d'Argent", "Moon Safari"), _t(100, "Old", "Too old")], 2, 2, 3),
    }

    def fake(params, retries=4):
        calls.append(params)
        return pages[params["page"]]

    monkeypatch.setattr(lf, "_api_get", fake)
    out = lf.fetch_since("k", "u", 100, to_ts=1000)
    assert out == [
        {"ts": 300, "artist": "Кино", "track": "Звезда по имени Солнце", "album": "Звезда"},
        {"ts": 200, "artist": "Air", "track": "La Femme d'Argent", "album": "Moon Safari"},
    ]
    assert all(c["to"] == 1000 and c["from"] == 101 and c["limit"] == 200 for c in calls)
    assert [c["page"] for c in calls] == [1, 2]


def test_fetch_since_single_track_dict_and_mismatch_warning(monkeypatch, caplog):
    monkeypatch.setattr(lf, "_api_get", lambda p, retries=4: _page(_t(5, "A", "B"), 1, 1, 2))
    out = lf.fetch_since("k", "u", 0, to_ts=10)
    assert out == [{"ts": 5, "artist": "A", "track": "B", "album": ""}]
    assert "FETCH_TOTAL_MISMATCH" in caplog.text


def test_raw_roundtrip_is_exact(tmp_path):
    raw: dict = {}
    tricky = {"ts": _ts(2019), "artist": "Sigur  Rós ", "track": "Track\twith tab", "album": "Line sep\x85x"}
    assert lf.add_raw(raw, tricky)
    assert not lf.add_raw(raw, dict(tricky))                 # same (ts, artist, track) → dup
    assert lf.add_raw(raw, {"ts": _ts(2019) + 1, "artist": "Air", "track": "", "album": None})
    assert not lf.add_raw(raw, {"ts": 1, "artist": "", "track": "x"})
    lf.write_raw(tmp_path, raw)
    assert (tmp_path / "scrobbles_2019.tsv").read_bytes().count(b"\r") == 0
    back = lf.load_raw(tmp_path)
    rows = sorted(back["2019"].values(), key=lambda r: r["ts"])
    assert rows[0] == {"ts": _ts(2019), "artist": "Sigur  Rós ", "track": "Track with tab",
                       "album": "Line sep\x85x"}
    assert rows[1] == {"ts": _ts(2019) + 1, "artist": "Air", "track": "", "album": ""}
    assert lf.raw_last_ts(back) == _ts(2019) + 1


def _run_raw(monkeypatch, tmp_path, scrobbles, state):
    seen = {}

    def fake_fetch(key, user, from_ts, to_ts=None):
        seen["from"] = from_ts
        return [dict(s) for s in scrobbles if s["ts"] > from_ts]

    monkeypatch.setattr(lf, "fetch_since", fake_fetch)
    sp, rd, op = tmp_path / "s.json", tmp_path / "raw", tmp_path / "y.json"
    sp.write_text(json.dumps(state), encoding="utf-8")
    rc = lf.main(["--state", str(sp), "--scrobbles-dir", str(rd), "--out", str(op), "--api-key", "k"])
    return rc, seen["from"], json.loads(sp.read_text(encoding="utf-8")), lf.load_raw(rd)


def test_seed_walk_fills_raw_without_double_counting_years(monkeypatch, tmp_path, capsys):
    old, new = _ts(2015), _ts(2020)
    st = lf.new_state("u")
    lf.add_scrobble(st, "Air", old)                          # the year state already holds 2015
    sc = [{"ts": old, "artist": "Air", "track": "Sexy Boy", "album": "Moon Safari"},
          {"ts": new, "artist": "Muse", "track": "Uprising", "album": "The Resistance"}]
    rc, from_ts, st2, raw = _run_raw(monkeypatch, tmp_path, sc, st)
    assert rc == 0 and from_ts == 0                          # empty raw layer → whole history
    assert st2["total"] == 2 and st2["years"]["2015"]["scrobbles"] == 1
    assert {k: len(v) for k, v in raw.items()} == {"2015": 1, "2020": 1}
    out = capsys.readouterr().out
    assert "CHANGED=true" in out and "RAW_YEAR_DIFF=0" in out

    # next week: both watermarks equal → only the new scrobble is fetched and folded
    sc.append({"ts": new + 60, "artist": "Muse", "track": "Resistance", "album": "The Resistance"})
    rc, from_ts, st3, raw = _run_raw(monkeypatch, tmp_path, sc, st2)
    assert from_ts == new and st3["total"] == 3 and len(raw["2020"]) == 2


def test_raw_year_diff_reports_a_lossy_layer():
    st = lf.new_state("u")
    for _ in range(3):
        lf.add_scrobble(st, "Air", _ts(2018))
    raw: dict = {}
    lf.add_raw(raw, {"ts": _ts(2018), "artist": "Air", "track": "x"})
    assert lf.raw_year_diff(raw, st) == {"2018": (1, 3)}


def test_bootstrap_also_seeds_raw(tmp_path):
    p = tmp_path / "e.json"
    p.write_text(json.dumps({"username": "U", "scrobbles": [
        {"artist": "Air", "track": "Sexy Boy", "album": "Moon Safari", "date": _ts(2015) * 1000}]}), encoding="utf-8")
    raw: dict = {}
    st = lf.bootstrap_from_export(p, "u", raw)
    assert st["total"] == 1
    assert list(raw["2015"].values()) == [{"ts": _ts(2015), "artist": "Air", "track": "Sexy Boy", "album": "Moon Safari"}]
