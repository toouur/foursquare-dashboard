# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for metrics.collect_companions and _build_companion_denylist.

These encode the companion-display contract shared by the Python side and the
JS mirror `collectCompanions()` in functions/api/feed.js:
  - three source columns: with_name, created_by_name, overlaps_name
  - "-" sentinel in overlaps means "none"
  - case-insensitive dedup, first-seen casing wins
"""
from metrics import collect_companions, _build_companion_denylist


class TestCollectCompanions:
    def test_empty_row(self):
        assert collect_companions({}) == []

    def test_with_name_only(self):
        assert collect_companions({"with_name": "Darya"}) == ["Darya"]

    def test_with_name_comma_split(self):
        row = {"with_name": "Darya, Joanna,Max"}
        assert collect_companions(row) == ["Darya", "Joanna", "Max"]

    def test_space_before_comma_normalised(self):
        # Foursquare sometimes emits "Name ,Name"
        row = {"with_name": "Darya ,Joanna"}
        assert collect_companions(row) == ["Darya", "Joanna"]

    def test_dash_sentinel_excluded(self):
        # Foursquare uses "-" as "no overlaps"
        assert collect_companions({"overlaps_name": "-"}) == []

    def test_dash_sentinel_among_real_names(self):
        row = {"overlaps_name": "Darya, -, Max"}
        assert collect_companions(row) == ["Darya", "Max"]

    def test_created_by_appended(self):
        row = {"with_name": "Darya", "created_by_name": "Max"}
        assert collect_companions(row) == ["Darya", "Max"]

    def test_case_insensitive_dedup_first_seen_casing_wins(self):
        row = {
            "with_name": "Darya",
            "created_by_name": "DARYA",
            "overlaps_name": "darya",
        }
        assert collect_companions(row) == ["Darya"]

    def test_all_three_sources_union(self):
        row = {
            "with_name": "Anna",
            "created_by_name": "Boris",
            "overlaps_name": "Clara, Anna",
        }
        assert collect_companions(row) == ["Anna", "Boris", "Clara"]

    def test_none_values_tolerated(self):
        row = {"with_name": None, "created_by_name": None, "overlaps_name": None}
        assert collect_companions(row) == []

    def test_whitespace_only_ignored(self):
        row = {"with_name": "  ,  , "}
        assert collect_companions(row) == []

    def test_order_preserved_with_before_created_by_before_overlaps(self):
        row = {
            "with_name": "Zoe",
            "created_by_name": "Adam",
            "overlaps_name": "Mia",
        }
        assert collect_companions(row) == ["Zoe", "Adam", "Mia"]


class TestCompanionDenylist:
    def test_full_and_first_names_collected_lowercase(self):
        rows = [{"with_name": "Joanna Smith"}]
        names = _build_companion_denylist(rows)
        assert "joanna smith" in names
        assert "joanna" in names

    def test_dash_and_short_tokens_skipped(self):
        rows = [{"overlaps_name": "-, A"}]
        names = _build_companion_denylist(rows)
        assert "-" not in names
        assert "a" not in names  # len < 2 skipped

    def test_all_three_columns_scanned(self):
        rows = [{
            "with_name": "Anna",
            "created_by_name": "Boris",
            "overlaps_name": "Clara",
        }]
        names = _build_companion_denylist(rows)
        assert {"anna", "boris", "clara"} <= names
