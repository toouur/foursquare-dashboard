# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
Tests for sync_to_d1.dedupe_against_d1 — the guard against two overlapping syncs
each inserting the same check-in.

checkins has no UNIQUE on id (seq is the PK) so the CSV's own duplicate rows
survive, which is exactly why the guard has to count copies instead of testing
existence: `WHERE NOT EXISTS` would collapse a legitimately twice-listed check-in
to a single row.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sync_to_d1 import dedupe_against_d1  # noqa: E402


def _row(cid, ts=1):
    return [cid, ts, "venue-id"]


def test_row_already_in_d1_is_skipped():
    """The July 2026 case: source lists it once, D1 already stored it."""
    rows = [_row("6a5be7db967f10482d3415b8")]

    assert dedupe_against_d1(rows, rows, {"6a5be7db967f10482d3415b8": 1}) == []


def test_source_duplicate_is_inserted_twice():
    """Twelve ids are duplicated in checkins.csv itself — both copies must land."""
    rows = [_row("5280bf6f11d20d93b9257df0"), _row("5280bf6f11d20d93b9257df0")]

    assert dedupe_against_d1(rows, rows, {}) == rows


def test_source_duplicate_half_present_inserts_the_remainder():
    rows = [_row("dup"), _row("dup")]

    assert dedupe_against_d1(rows, rows, {"dup": 1}) == [rows[0]]


def test_source_duplicate_fully_present_inserts_nothing():
    rows = [_row("dup"), _row("dup")]

    assert dedupe_against_d1(rows, rows, {"dup": 2}) == []


def test_unknown_ids_pass_through():
    rows = [_row("brand-new"), _row("another")]

    assert dedupe_against_d1(rows, rows, {"unrelated": 5}) == rows


def test_d1_holding_more_than_the_source_never_goes_negative():
    rows = [_row("over")]

    assert dedupe_against_d1(rows, rows, {"over": 9}) == []


def test_candidates_may_be_a_subset_of_the_source():
    """The fast path hands over only rows newer than the watermark, while the
    copy budget must still be computed from the whole source."""
    source = [_row("dup", 1), _row("dup", 2), _row("old", 1)]
    candidates = [_row("dup", 2)]

    assert dedupe_against_d1(candidates, source, {"dup": 1}) == candidates
    assert dedupe_against_d1(candidates, source, {"dup": 2}) == []


def test_fallback_path_semantics_full_source_against_d1_counts():
    """The set-difference fallback now feeds the whole source through the same
    helper. Whatever D1 is short of gets inserted — including the second copy of
    a check-in the CSV lists twice, which the old `id not in existing_ids`
    filter discarded along with the first."""
    source = [_row("only-in-csv"), _row("dup"), _row("dup"), _row("synced")]
    d1_counts = {"dup": 1, "synced": 1}

    kept = dedupe_against_d1(source, source, d1_counts)

    assert [r[0] for r in kept] == ["only-in-csv", "dup"]
