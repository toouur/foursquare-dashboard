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


def test_raw_roundtrip_is_exact_and_keeps_repeats(tmp_path):
    raw: dict = {}
    tricky = {"ts": _ts(2019), "artist": "Sigur  Rós ", "track": "Track\twith tab", "album": "Line sep\x85x"}
    assert lf.add_raw(raw, tricky)
    assert lf.add_raw(raw, dict(tricky))                     # Last.fm does store a same-second repeat
    assert lf.add_raw(raw, {"ts": _ts(2019) + 1, "artist": "Air", "track": "", "album": None})
    assert not lf.add_raw(raw, {"ts": 1, "artist": "", "track": "x"})
    lf.write_raw(tmp_path, raw)
    assert (tmp_path / "scrobbles_2019.tsv").read_bytes().count(b"\r") == 0
    back = lf.load_raw(tmp_path)
    rows = sorted(back["2019"], key=lambda r: (r["ts"], r["artist"]))
    assert len(rows) == 3
    assert rows[0] == rows[1] == {"ts": _ts(2019), "artist": "Sigur  Rós ", "track": "Track with tab",
                                  "album": "Line sep\x85x"}
    assert rows[2] == {"ts": _ts(2019) + 1, "artist": "Air", "track": "", "album": ""}
    assert lf.raw_last_ts(back) == _ts(2019) + 1


def test_replace_window_is_a_multiset_replace():
    a = {"ts": _ts(2026, 10, 1), "artist": "A", "track": "x", "album": ""}
    b = {"ts": _ts(2026, 10, 2), "artist": "B", "track": "y", "album": ""}
    old = {"ts": _ts(2025, 1, 1), "artist": "Old", "track": "z", "album": ""}
    raw: dict = {}
    for sc in (old, a):
        lf.add_raw(raw, sc)
    years, added, removed = lf.replace_window(raw, _ts(2026, 9, 1), _ts(2026, 10, 20), [a, a, b])
    assert years == {"2026"} and (added, removed) == (2, 0)          # the repeat of a + b
    assert sorted(r["artist"] for r in raw["2026"]) == ["A", "A", "B"]
    assert [r["artist"] for r in raw["2025"]] == ["Old"]             # outside the window: untouched
    assert lf.replace_window(raw, _ts(2026, 9, 1), _ts(2026, 10, 20), [a, a, b]) == (set(), 0, 0)
    years, added, removed = lf.replace_window(raw, _ts(2026, 9, 1), _ts(2026, 10, 20), [a])
    assert (added, removed) == (0, 2)                                # deleted on Last.fm → deleted here


def _run_raw(monkeypatch, tmp_path, scrobbles, state, raw=None, extra=()):
    seen = {}

    def fake_fetch(key, user, from_ts, to_ts=None):
        seen["from"] = from_ts
        return [dict(s) for s in scrobbles if s["ts"] > from_ts]

    monkeypatch.setattr(lf, "fetch_since", fake_fetch)
    sp, rd, op = tmp_path / "s.json", tmp_path / "raw", tmp_path / "y.json"
    sp.write_text(json.dumps(state), encoding="utf-8")
    if raw is not None:
        lf.write_raw(rd, raw)
    rc = lf.main(["--state", str(sp), "--scrobbles-dir", str(rd), "--out", str(op), "--api-key", "k", *extra])
    return rc, seen.get("from"), json.loads(sp.read_text(encoding="utf-8")), lf.load_raw(rd)


def test_seed_walk_fills_raw_and_rebuilds_the_year_state(monkeypatch, tmp_path, capsys):
    old, new = _ts(2015), _ts(2020)
    st = lf.new_state("u")
    lf.add_scrobble(st, "Air", old)
    lf.add_scrobble(st, "Ghost", old)                # the old state counted a play Last.fm no longer has
    sc = [{"ts": old, "artist": "Air", "track": "Sexy Boy", "album": "Moon Safari"},
          {"ts": new, "artist": "Muse", "track": "Uprising", "album": "The Resistance"},
          {"ts": new, "artist": "Muse", "track": "Uprising", "album": "The Resistance"}]
    rc, from_ts, st2, raw = _run_raw(monkeypatch, tmp_path, sc, st)
    assert rc == 0 and from_ts == 0                  # empty raw layer → whole history
    assert {k: len(v) for k, v in raw.items()} == {"2015": 1, "2020": 2}
    assert st2["total"] == 3 and "Ghost" not in st2["years"]["2015"]["artists"]
    out = capsys.readouterr().out
    assert "CHANGED=true" in out and "RAW_YEAR_DIFF=0" in out


def test_weekly_run_catches_a_late_scrobble(monkeypatch, tmp_path, capsys):
    t0 = _ts(2026, 10, 10)
    on_disk = [{"ts": t0, "artist": "A", "track": "1", "album": ""},
               {"ts": t0 + 3600, "artist": "B", "track": "2", "album": ""}]
    raw: dict = {}
    for sc in on_disk:
        lf.add_raw(raw, sc)
    late = {"ts": t0 + 60, "artist": "Tycho", "track": "Awake", "album": "Awake"}   # synced days later
    newer = {"ts": t0 + 7200, "artist": "C", "track": "3", "album": ""}
    rc, from_ts, st2, raw2 = _run_raw(monkeypatch, tmp_path, [*on_disk, late, newer],
                                      lf.state_from_raw(raw, "u"), raw)
    assert rc == 0 and from_ts == t0 + 3600 - lf.OVERLAP_DAYS * 86400
    assert sorted(r["artist"] for r in raw2["2026"]) == ["A", "B", "C", "Tycho"]
    assert st2["total"] == 4
    assert "LATE_SCROBBLES=1" in capsys.readouterr().out


def test_a_shrunken_window_writes_nothing(monkeypatch, tmp_path, capsys):
    t0 = _ts(2026, 10, 10)
    raw: dict = {}
    for k in range(20):
        lf.add_raw(raw, {"ts": t0 + k * 60, "artist": "A", "track": str(k), "album": ""})
    rc, _, _, raw2 = _run_raw(monkeypatch, tmp_path, [], lf.state_from_raw(raw, "u"), raw)
    assert rc == 1 and len(raw2["2026"]) == 20       # an API hiccup must not wipe two weeks
    assert "CHANGED=false" in capsys.readouterr().out


def test_reseed_ignores_the_raw_layer_on_disk(monkeypatch, tmp_path):
    raw: dict = {}
    lf.add_raw(raw, {"ts": _ts(2026), "artist": "A", "track": "1", "album": ""})
    rc, from_ts, _, _ = _run_raw(monkeypatch, tmp_path, [{"ts": _ts(2026), "artist": "A", "track": "1", "album": ""}],
                                 lf.state_from_raw(raw, "u"), raw, extra=["--reseed-raw"])
    assert rc == 0 and from_ts == 0


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
    assert raw["2015"] == [{"ts": _ts(2015), "artist": "Air", "track": "Sexy Boy", "album": "Moon Safari"}]
