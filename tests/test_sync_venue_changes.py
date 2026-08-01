# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
Tests for sync_venue_changes.patch_backfill — reconstructed rows must follow the
same venue rename/move as the real check-ins, because they reuse the same
Foursquare venue_ids.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sync_venue_changes import (  # noqa: E402
    detect_changes,
    detect_merges,
    load_csv_by_venue,
    load_name_variants,
    patch_backfill,
    patch_comments,
    patch_ratings,
)

VID = "4f8d9b86e4b013a984556fb0"
OLD_NAME = "Aleea bd. Mircea cel Bătrân"
NEW_NAME = "Aleea de pe Bulevardul Mircea cel Bătrân"

COLUMNS = ["date", "venue", "venue_id", "venue_url", "city", "country",
           "lat", "lng", "category", "source_app", "checkin_id"]


def _write_backfill(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in COLUMNS})


def _read_backfill(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _rename_change(field: str = "venue", old: str = OLD_NAME, new: str = NEW_NAME) -> list[dict]:
    return [{"venue_id": VID, "venue_name": new, "fields": {field: (old, new)}}]


def test_patch_backfill_renames_matching_rows(tmp_path):
    bf = tmp_path / "backfill.csv"
    _write_backfill(bf, [
        {"venue": OLD_NAME, "venue_id": VID, "checkin_id": "rf0095", "city": "Chișinău"},
        {"venue": OLD_NAME, "venue_id": VID, "checkin_id": "rf0165", "city": "Chișinău"},
        {"venue": "Casa Germană „Hoffnung”", "venue_id": "51aa4460498ecfbbd9bf0d76",
         "checkin_id": "rf0146", "city": "Chișinău"},
    ])

    records = patch_backfill(bf, _rename_change())

    assert len(records) == 2
    assert {r["checkin_id"] for r in records} == {"rf0095", "rf0165"}
    assert records[0]["fields"]["venue"] == (OLD_NAME, NEW_NAME)

    rows = _read_backfill(bf)
    assert [r["venue"] for r in rows if r["venue_id"] == VID] == [NEW_NAME, NEW_NAME]
    # untouched venue keeps its name
    assert rows[2]["venue"] == "Casa Germană „Hoffnung”"


def test_patch_backfill_skips_blank_venue_id(tmp_path):
    """Placeholder rows (no Foursquare venue) must never be touched."""
    bf = tmp_path / "backfill.csv"
    _write_backfill(bf, [
        {"venue": "Альтаир", "venue_id": "", "checkin_id": "rf0324", "city": "Chișinău"},
        {"venue": OLD_NAME, "venue_id": VID, "checkin_id": "rf0095", "city": "Chișinău"},
    ])

    records = patch_backfill(bf, _rename_change())

    assert [r["checkin_id"] for r in records] == ["rf0095"]
    rows = _read_backfill(bf)
    assert rows[0]["venue"] == "Альтаир"


def test_patch_backfill_dry_run_reports_without_writing(tmp_path):
    bf = tmp_path / "backfill.csv"
    _write_backfill(bf, [{"venue": OLD_NAME, "venue_id": VID, "checkin_id": "rf0095"}])
    before = bf.read_bytes()

    records = patch_backfill(bf, _rename_change(), dry_run=True)

    assert len(records) == 1
    assert bf.read_bytes() == before


def test_patch_backfill_noop_when_already_current(tmp_path):
    bf = tmp_path / "backfill.csv"
    _write_backfill(bf, [{"venue": NEW_NAME, "venue_id": VID, "checkin_id": "rf0095"}])

    assert patch_backfill(bf, _rename_change()) == []


def test_patch_backfill_applies_all_tracked_fields(tmp_path):
    bf = tmp_path / "backfill.csv"
    _write_backfill(bf, [{"venue": OLD_NAME, "venue_id": VID, "checkin_id": "rf0323",
                          "lat": "47.025", "lng": "28.835", "category": "Art Gallery"}])
    changes = [{
        "venue_id": VID,
        "venue_name": NEW_NAME,
        "fields": {
            "venue":    (OLD_NAME, NEW_NAME),
            "lat":      ("47.025", "47.0501"),
            "category": ("Art Gallery", "Museum"),
        },
    }]

    records = patch_backfill(bf, changes)

    assert len(records) == 1
    row = _read_backfill(bf)[0]
    assert row["venue"] == NEW_NAME
    assert row["lat"] == "47.0501"
    assert row["category"] == "Museum"
    assert row["lng"] == "28.835"  # not in the diff → untouched


def _write_ratings(path: Path) -> None:
    path.write_text(json.dumps({
        "venueLikes": [
            {"id": "other", "name": "Somewhere", "url": "u", "createdAt": 1},
            {"id": VID, "name": OLD_NAME,
             "url": "https://app.foursquare.com/v/aleea-bd/4f8d9b86e4b013a984556fb0",
             "createdAt": 1358874854},
        ],
        "venueOkays": [],
        "venueDislikes": [{"id": VID, "name": OLD_NAME, "url": "u", "createdAt": 2}],
    }, ensure_ascii=False), encoding="utf-8")


def _write_comments(path: Path) -> None:
    path.write_text(json.dumps({
        "_meta": {"fetched_at": 1},
        "comments": {
            "64c006e9cf915a3d4b036d1e": {
                "count": 1, "venue": OLD_NAME, "venue_id": VID, "ts": 1690306281,
                "items": [{"text": "Всё так же хороша", "at": 1690306452}],
            },
            "aaaa1111bbbb2222cccc3333": {
                "count": 1, "venue": "Other place", "venue_id": "other", "ts": 2,
            },
        },
    }, ensure_ascii=False), encoding="utf-8")


def test_patch_ratings_renames_all_buckets(tmp_path):
    p = tmp_path / "venueRatings.json"
    _write_ratings(p)

    records = patch_ratings(p, _rename_change())

    assert len(records) == 2
    assert {r["bucket"] for r in records} == {"venueLikes", "venueDislikes"}
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["venueLikes"][1]["name"] == NEW_NAME
    assert data["venueDislikes"][0]["name"] == NEW_NAME
    assert data["venueLikes"][0]["name"] == "Somewhere"      # other venue untouched
    assert data["venueLikes"][1]["url"].endswith(VID)        # url slug left alone


def test_patch_comments_renames_matching_entries(tmp_path):
    p = tmp_path / "comments.json"
    _write_comments(p)

    records = patch_comments(p, _rename_change())

    assert [r["checkin_id"] for r in records] == ["64c006e9cf915a3d4b036d1e"]
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["comments"]["64c006e9cf915a3d4b036d1e"]["venue"] == NEW_NAME
    assert data["comments"]["aaaa1111bbbb2222cccc3333"]["venue"] == "Other place"
    assert data["comments"]["64c006e9cf915a3d4b036d1e"]["items"][0]["text"]  # payload kept


def test_json_patchers_honour_dry_run_and_noop(tmp_path):
    ratings, comments = tmp_path / "r.json", tmp_path / "c.json"
    _write_ratings(ratings)
    _write_comments(comments)
    r_before, c_before = ratings.read_bytes(), comments.read_bytes()

    assert len(patch_ratings(ratings, _rename_change(), dry_run=True)) == 2
    assert len(patch_comments(comments, _rename_change(), dry_run=True)) == 1
    assert ratings.read_bytes() == r_before
    assert comments.read_bytes() == c_before

    # a diff without a name change must not touch either file
    coord_only = [{"venue_id": VID, "venue_name": OLD_NAME,
                   "fields": {"lat": ("47.05", "47.06")}}]
    assert patch_ratings(ratings, coord_only) == []
    assert patch_comments(comments, coord_only) == []
    assert ratings.read_bytes() == r_before


def test_detect_changes_feeds_patch_backfill(tmp_path):
    """End-to-end shape: a rename detected between snapshots patches backfill."""
    old = {VID: {"venue": OLD_NAME, "city": "Chișinău", "country": "Moldova",
                 "lat": "47.05", "lng": "28.87", "category": "Trail"}}
    new = {VID: {"venue": NEW_NAME, "city": "Chișinău", "country": "Moldova",
                 "lat": "47.05", "lng": "28.87", "category": "Trail"}}

    changes = detect_changes(old, new)
    assert changes and changes[0]["fields"] == {"venue": (OLD_NAME, NEW_NAME)}

    bf = tmp_path / "backfill.csv"
    _write_backfill(bf, [{"venue": OLD_NAME, "venue_id": VID, "checkin_id": "rf0095"}])
    assert len(patch_backfill(bf, changes)) == 1
    assert _read_backfill(bf)[0]["venue"] == NEW_NAME


def _write_snapshot(path, rows):
    cols = ["date", "venue", "venue_id", "city", "country", "lat", "lng", "category"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _row(ts, name, **kw):
    base = {"date": str(ts), "venue": name, "venue_id": VID, "city": "Chișinău",
            "country": "Moldova", "lat": "47.05", "lng": "28.87", "category": "Trail"}
    base.update(kw)
    return base


def test_rename_detected_when_only_the_newest_row_was_updated(tmp_path):
    """The freshest old row can already carry the new name — a mid-month rename.

    load_csv_by_venue keeps only that row, so a freshest-to-freshest compare sees
    nothing and tips/backfill/ratings/comments keep the stale name forever.
    """
    old_p, new_p = tmp_path / "old.csv", tmp_path / "new.csv"
    _write_snapshot(old_p, [_row(100, OLD_NAME), _row(200, OLD_NAME), _row(300, NEW_NAME)])
    _write_snapshot(new_p, [_row(100, NEW_NAME), _row(200, NEW_NAME), _row(300, NEW_NAME)])

    old, new = load_csv_by_venue(old_p), load_csv_by_venue(new_p)
    assert detect_changes(old, new) == []                       # the old behaviour

    changes = detect_changes(old, new, load_name_variants(old_p))
    assert len(changes) == 1
    assert changes[0]["fields"] == {"venue": (OLD_NAME, NEW_NAME)}


def test_variants_report_the_most_common_stale_spelling(tmp_path):
    old_p, new_p = tmp_path / "old.csv", tmp_path / "new.csv"
    _write_snapshot(old_p, [_row(100, "Rare old"), _row(200, OLD_NAME),
                            _row(300, OLD_NAME), _row(400, NEW_NAME)])
    _write_snapshot(new_p, [_row(400, NEW_NAME)])

    changes = detect_changes(load_csv_by_venue(old_p), load_csv_by_venue(new_p),
                             load_name_variants(old_p))

    assert changes[0]["fields"]["venue"] == (OLD_NAME, NEW_NAME)


def test_no_false_rename_when_the_snapshot_is_uniform(tmp_path):
    old_p, new_p = tmp_path / "old.csv", tmp_path / "new.csv"
    _write_snapshot(old_p, [_row(100, NEW_NAME), _row(200, NEW_NAME)])
    _write_snapshot(new_p, [_row(100, NEW_NAME), _row(200, NEW_NAME)])

    assert detect_changes(load_csv_by_venue(old_p), load_csv_by_venue(new_p),
                          load_name_variants(old_p)) == []


def test_variants_do_not_widen_non_name_fields(tmp_path):
    """Older rows legitimately carry stale coords — only the name check widens."""
    old_p, new_p = tmp_path / "old.csv", tmp_path / "new.csv"
    _write_snapshot(old_p, [_row(100, NEW_NAME, lat="1.0"), _row(200, NEW_NAME, lat="47.05")])
    _write_snapshot(new_p, [_row(200, NEW_NAME, lat="47.05")])

    assert detect_changes(load_csv_by_venue(old_p), load_csv_by_venue(new_p),
                          load_name_variants(old_p)) == []


def test_shared_timestamp_does_not_fake_a_merge(tmp_path):
    """Two venues can share one unix second — neither has vanished.

    Adolf Fredriks kyrkogård + kyrka both sit at 1519338605. With {ts: vid} the
    later row overwrote the earlier, the loser looked gone, and detect_merges
    reported a merge that made sync_to_d1 rewrite a live check-in onto the wrong
    venue_id.
    """
    yard, church = "4ddf95ef18388714bb6b061d", "4adcdaeef964a520ae5a21e3"
    rows = [{"date": "1519338605", "venue": "kyrkogård", "venue_id": yard},
            {"date": "1519338605", "venue": "kyrka", "venue_id": church}]
    old_p, new_p = tmp_path / "old.csv", tmp_path / "new.csv"
    _write_snapshot(old_p, rows)
    _write_snapshot(new_p, rows)

    assert detect_merges(load_csv_by_venue(old_p), old_p, new_p) == []


def test_real_merge_is_still_detected(tmp_path):
    old_p, new_p = tmp_path / "old.csv", tmp_path / "new.csv"
    _write_snapshot(old_p, [{"date": "111", "venue": "Old place", "venue_id": "a" * 24}])
    _write_snapshot(new_p, [{"date": "111", "venue": "New place", "venue_id": "b" * 24}])

    merges = detect_merges(load_csv_by_venue(old_p), old_p, new_p)

    assert [(m["venue_id"], m["new_venue_id"]) for m in merges] == [("a" * 24, "b" * 24)]


def test_merge_is_skipped_when_the_timestamp_is_ambiguous(tmp_path):
    """A gone venue whose only timestamp is shared has two candidate targets —
    skip rather than guess, since guessing rewrites check-ins."""
    old_p, new_p = tmp_path / "old.csv", tmp_path / "new.csv"
    _write_snapshot(old_p, [{"date": "111", "venue": "Old", "venue_id": "a" * 24}])
    _write_snapshot(new_p, [{"date": "111", "venue": "X", "venue_id": "b" * 24},
                            {"date": "111", "venue": "Y", "venue_id": "c" * 24}])

    assert detect_merges(load_csv_by_venue(old_p), old_p, new_p) == []
