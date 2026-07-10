# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/year_covers.py — the stable per-year cover selector.

The whole point of this module is that a year's cover photo does NOT drift
build-to-build. These tests pin that guarantee plus the override precedence.
"""
from datetime import datetime, timedelta, timezone

from conftest import make_row
from year_covers import (
    load_cover_overrides,
    load_month_cover_overrides,
    load_month_notes,
    select_year_covers,
)

# A timestamp `day` days into `year` (day=1 → Jan 1) so ordering is explicit.
def _ts(year, day=1):
    base = datetime(year, 1, 1, 12, 0, tzinfo=timezone.utc)
    return int((base + timedelta(days=day - 1)).timestamp())


def _rows_and_photos(specs):
    """specs: list of (cid, year, day, {row_extra}, [filenames])."""
    rows, photos = [], {}
    for cid, year, day, extra, fnames in specs:
        rows.append(make_row(_ts(year, day), checkin_id=cid, **extra))
        photos[cid] = fnames
    return rows, photos


def test_deterministic_repeat():
    rows, photos = _rows_and_photos([
        ("a", 2020, 3, {}, ["a1.jpg"]),
        ("b", 2020, 9, {"shout": "hi"}, ["b1.jpg"]),
        ("c", 2020, 1, {"with_name": "Sam"}, ["c1.jpg"]),
    ])
    r1 = select_year_covers(rows, photos, config_dir="__nonexistent__")
    r2 = select_year_covers(rows, photos, config_dir="__nonexistent__")
    assert r1 == r2
    assert r1[2020]["fname"] == "b1.jpg"  # shout beats companion beats plain


def test_shout_beats_companions_beats_photo_count():
    rows, photos = _rows_and_photos([
        ("plain", 2021, 5, {}, ["p1.jpg", "p2.jpg", "p3.jpg"]),  # most photos
        ("comp", 2021, 5, {"with_name": "A, B"}, ["c1.jpg"]),     # 2 companions
        ("shout", 2021, 5, {"shout": "x"}, ["s1.jpg"]),           # a shout
    ])
    covers = select_year_covers(rows, photos, "__nonexistent__")
    assert covers[2021]["fname"] == "s1.jpg"


def test_appending_lower_signature_photo_does_not_change_cover():
    base = [("hero", 2022, 2, {"shout": "memorable"}, ["hero.jpg"])]
    rows, photos = _rows_and_photos(base)
    before = select_year_covers(rows, photos, "__nonexistent__")[2022]["fname"]
    # A newer, plain photo arrives later in the same year.
    rows2, photos2 = _rows_and_photos(base + [
        ("later", 2022, 300, {}, ["later.jpg"]),
    ])
    after = select_year_covers(rows2, photos2, "__nonexistent__")[2022]["fname"]
    assert before == after == "hero.jpg"


def test_past_year_unaffected_by_new_year_photos():
    rows, photos = _rows_and_photos([("x", 2019, 4, {}, ["x.jpg"])])
    first = select_year_covers(rows, photos, "__nonexistent__")[2019]["fname"]
    rows2, photos2 = _rows_and_photos([
        ("x", 2019, 4, {}, ["x.jpg"]),
        ("y", 2023, 4, {"shout": "new"}, ["y.jpg"]),
    ])
    covers = select_year_covers(rows2, photos2, "__nonexistent__")
    assert covers[2019]["fname"] == first == "x.jpg"
    assert covers[2023]["fname"] == "y.jpg"


def test_tie_breaks_by_earliest_ts_then_id():
    # Identical signatures — earliest ts wins, then checkin_id.
    rows, photos = _rows_and_photos([
        ("zzz", 2024, 10, {}, ["z.jpg"]),
        ("aaa", 2024, 2, {}, ["a.jpg"]),   # earliest day
        ("mmm", 2024, 20, {}, ["m.jpg"]),
    ])
    covers = select_year_covers(rows, photos, "__nonexistent__")
    assert covers[2024]["fname"] == "a.jpg"


def test_pix_url_builds_absolute_src():
    rows, photos = _rows_and_photos([("a", 2020, 1, {}, ["pic.jpg"])])
    covers = select_year_covers(rows, photos, "__nonexistent__",
                                pix_url="https://cdn.example/pix/")
    assert covers[2020]["src"] == "https://cdn.example/pix/pic.jpg"


def test_override_by_filename(tmp_path):
    (tmp_path / "year_covers.json").write_text('{"2020": "chosen.jpg"}', encoding="utf-8")
    rows, photos = _rows_and_photos([
        ("a", 2020, 1, {"shout": "would-normally-win"}, ["auto.jpg"]),
        ("b", 2020, 2, {}, ["chosen.jpg"]),
    ])
    covers = select_year_covers(rows, photos, tmp_path)
    assert covers[2020]["fname"] == "chosen.jpg"
    assert covers[2020]["override"] is True


def test_override_by_checkin_id(tmp_path):
    (tmp_path / "year_covers.json").write_text('{"2020": "b"}', encoding="utf-8")
    rows, photos = _rows_and_photos([
        ("a", 2020, 1, {"shout": "auto"}, ["auto.jpg"]),
        ("b", 2020, 2, {}, ["target1.jpg", "target2.jpg"]),
    ])
    covers = select_year_covers(rows, photos, tmp_path)
    assert covers[2020]["fname"] == "target1.jpg"  # first photo of that check-in
    assert covers[2020]["checkin_id"] == "b"
    assert covers[2020]["override"] is True


def test_missing_override_file_is_noop():
    rows, photos = _rows_and_photos([("a", 2020, 1, {}, ["a.jpg"])])
    covers = select_year_covers(rows, photos, "__nonexistent__")
    assert covers[2020]["override"] is False


# ── Month keys ("YYYY-MM" pins + "YYYY-MM-note" texts) ──────────────────

MIXED = (
    '{"_comment": "doc", "2024": "year.jpg", "2024-07": "july.jpg",'
    ' "2024-2": "feb-cid", "2024-07-note": "Hand-written July.",'
    ' "2024-13": "bad-month.jpg", "2024-05": "  ", "junk": "x"}'
)


def test_month_keys_parsed_and_separated(tmp_path):
    (tmp_path / "year_covers.json").write_text(MIXED, encoding="utf-8")
    # Year loader sees ONLY the plain-int key; month/note/_comment/junk skipped.
    assert load_cover_overrides(tmp_path) == {2024: "year.jpg"}
    # Month loader: zero-padded and bare month both parse; month 13 and
    # empty values are dropped; note keys don't leak in.
    assert load_month_cover_overrides(tmp_path) == {
        (2024, 7): "july.jpg",
        (2024, 2): "feb-cid",
    }
    assert load_month_notes(tmp_path) == {(2024, 7): "Hand-written July."}


def test_month_loaders_missing_or_malformed_file(tmp_path):
    assert load_month_cover_overrides("__nonexistent__") == {}
    assert load_month_notes("__nonexistent__") == {}
    (tmp_path / "year_covers.json").write_text("{not json", encoding="utf-8")
    assert load_month_cover_overrides(tmp_path) == {}
    assert load_cover_overrides(tmp_path) == {}
    (tmp_path / "year_covers.json").write_text('["list"]', encoding="utf-8")
    assert load_month_notes(tmp_path) == {}
