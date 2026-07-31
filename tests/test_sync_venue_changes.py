# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
Tests for sync_venue_changes.patch_backfill — reconstructed rows must follow the
same venue rename/move as the real check-ins, because they reuse the same
Foursquare venue_ids.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sync_venue_changes import detect_changes, patch_backfill  # noqa: E402

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
