# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for transform.py: normalization priority order, haversine,
category grouping, config loading.

Priority order under test (documented in CLAUDE.md):
  0. venue_fixes.json  (per-venue_id, HIGHEST)
  1. country_fixes.json (per-ts)
  2. city_fixes.json    (per-ts)
  3. city_merge.yaml    (raw -> canonical, non-blank rows)
  4. blank_city_resolver (inference, blank rows only)
"""
import json

import pytest

from conftest import make_row
from transform import (
    _haversine,
    _parse_detail,
    apply_transforms,
    build_categorize_fn,
    load_mappings,
)


def mappings(**kw):
    base = {
        "country_fixes": {},
        "city_fixes": {},
        "venue_fixes": {},
        "city_merge": {},
    }
    base.update(kw)
    return base


class TestApplyTransformsPriority:
    def test_venue_fix_beats_everything(self):
        row = make_row(100, city="Terespol", country="Belarus", venue_id="a" * 24)
        m = mappings(
            venue_fixes={"a" * 24: {"city": "Terespol", "country": "Poland"}},
            country_fixes={"100": "Belarus"},   # would set Belarus — must lose
            city_fixes={"100": "Brest"},        # would set Brest — must lose
            city_merge={"Terespol": "Somewhere Else"},  # must not apply either
        )
        apply_transforms([row], m)
        assert row["country"] == "Poland"
        assert row["city"] == "Terespol"
        assert row["city_inferred"] == "0"

    def test_venue_fix_country_only_does_not_short_circuit_city(self):
        # A venue fix with only `country` still lets city_merge run.
        row = make_row(100, city="Kazan’", country="Belarus", venue_id="b" * 24)
        m = mappings(
            venue_fixes={"b" * 24: {"country": "Russia"}},
            city_merge={"Kazan'": "Kazan"},
        )
        apply_transforms([row], m)
        assert row["country"] == "Russia"
        assert row["city"] == "Kazan"

    def test_country_fix_by_ts(self):
        row = make_row(200, city="Brest", country="Poland")
        m = mappings(country_fixes={"200": "Belarus"})
        apply_transforms([row], m)
        assert row["country"] == "Belarus"

    def test_city_fix_by_ts_beats_city_merge(self):
        row = make_row(300, city="RawCity", country="Belarus")
        m = mappings(
            city_fixes={"300": "FixedCity"},
            city_merge={"RawCity": "MergedCity"},
        )
        apply_transforms([row], m)
        assert row["city"] == "FixedCity"
        assert row["city_inferred"] == "0"

    def test_city_merge_plain(self):
        row = make_row(400, city="Minsk City", country="Belarus")
        m = mappings(city_merge={"Minsk City": "Minsk"})
        apply_transforms([row], m)
        assert row["city"] == "Minsk"

    def test_turkiye_normalised_to_turkey(self):
        row = make_row(500, city="Istanbul", country="Türkiye")
        apply_transforms([row], mappings())
        assert row["country"] == "Turkey"

    def test_unmapped_city_left_alone(self):
        row = make_row(600, city="Novelty", country="Belarus")
        apply_transforms([row], mappings())
        assert row["city"] == "Novelty"
        assert row["city_inferred"] == "0"


class TestApostropheNormalisation:
    def test_rsqm_apostrophe_matches_ascii_merge_key(self):
        # Foursquare U+2019 vs city_merge U+0027 (e.g. Kazan', Smarhon')
        row = make_row(700, city="Smarhon’", country="Belarus")
        m = mappings(city_merge={"Smarhon'": "Smarhon"})
        apply_transforms([row], m)
        assert row["city"] == "Smarhon"

    def test_lsqm_ayn_matches_ascii_merge_key(self):
        # U+2018 used as Arabic ʿayn in transliterations
        row = make_row(701, city="Al Ma‘ādī", country="Egypt")
        m = mappings(city_merge={"Al Ma'ādī": "Maadi"})
        apply_transforms([row], m)
        assert row["city"] == "Maadi"

    def test_exact_raw_key_still_matches(self):
        # A merge key that itself contains U+2019 must keep working
        row = make_row(702, city="X’Y", country="")
        m = mappings(city_merge={"X’Y": "XY"})
        apply_transforms([row], m)
        assert row["city"] == "XY"


class TestBlankCityInference:
    def test_blank_city_filled_and_flagged(self):
        row = make_row(800, city="", country="Belarus")
        m = mappings()
        apply_transforms([row], m, blank_city_resolver=lambda r: "Lepel")
        assert row["city"] == "Lepel"
        assert row["city_inferred"] == "1"

    def test_inferred_name_runs_through_city_merge(self):
        # Resolver may return raw Cyrillic that still needs canonicalising
        row = make_row(801, city="", country="Belarus")
        m = mappings(city_merge={"Мачулищи": "Machulishchy"})
        apply_transforms([row], m,
                         blank_city_resolver=lambda r: "Мачулищи")
        assert row["city"] == "Machulishchy"
        assert row["city_inferred"] == "1"

    def test_resolver_returning_none_leaves_blank(self):
        row = make_row(802, city="", country="Belarus")
        apply_transforms([row], mappings(), blank_city_resolver=lambda r: None)
        assert row["city"] == ""
        assert row["city_inferred"] == "0"

    def test_non_blank_row_never_hits_resolver(self):
        def boom(r):
            raise AssertionError("resolver called for non-blank city")
        row = make_row(803, city="Minsk", country="Belarus")
        apply_transforms([row], mappings(), blank_city_resolver=boom)
        assert row["city"] == "Minsk"


class TestHaversine:
    def test_zero_distance(self):
        assert _haversine(53.9, 27.56, 53.9, 27.56) == 0.0

    def test_one_degree_longitude_at_equator(self):
        # 1° of longitude at the equator ≈ 111.19 km
        assert _haversine(0, 0, 0, 1) == pytest.approx(111.19, abs=0.1)

    def test_symmetry(self):
        d1 = _haversine(53.9, 27.56, 52.23, 21.01)   # Minsk -> Warsaw
        d2 = _haversine(52.23, 21.01, 53.9, 27.56)
        assert d1 == pytest.approx(d2)

    def test_minsk_warsaw_plausible(self):
        d = _haversine(53.9, 27.5667, 52.2297, 21.0122)
        assert 450 < d < 500


class TestParseDetail:
    def test_round_trip(self):
        detail = "2024-01-01T00:00:00Z::Cafe X || 2024-01-02T10:30:00Z::Bar Y"
        assert _parse_detail(detail) == [
            ("2024-01-01T00:00:00Z", "Cafe X"),
            ("2024-01-02T10:30:00Z", "Bar Y"),
        ]

    def test_empty(self):
        assert _parse_detail("") == []

    def test_malformed_item_skipped(self):
        assert _parse_detail("no-separator-here") == []

    def test_venue_name_containing_double_colon_kept_whole(self):
        got = _parse_detail("2024-01-01T00:00:00Z::Cafe :: Contra")
        assert got == [("2024-01-01T00:00:00Z", "Cafe :: Contra")]


class TestBuildCategorizeFn:
    def test_exact_match_case_insensitive(self):
        cat = build_categorize_fn({"Food": ["Restaurant", "Cafe"]})
        assert cat("restaurant") == "Food"
        assert cat("CAFE") == "Food"

    def test_substring_match_both_directions(self):
        cat = build_categorize_fn({"Food": ["Restaurant"]})
        assert cat("Italian Restaurant") == "Food"   # kw in raw
        assert cat("Restau") == "Food"               # raw in kw

    def test_no_match_returns_none(self):
        cat = build_categorize_fn({"Food": ["Restaurant"]})
        assert cat("Airport") is None


class TestLoadMappings:
    def test_missing_config_dir_yields_empty_maps(self, tmp_path):
        m = load_mappings(tmp_path / "does-not-exist")
        assert m["country_fixes"] == {}
        assert m["city_fixes"] == {}
        assert m["venue_fixes"] == {}
        assert m["city_merge"] == {}
        assert m["category_groups"] == {}

    def test_loads_json_and_yaml(self, tmp_path):
        (tmp_path / "country_fixes.json").write_text(
            json.dumps({"123": "Belarus"}), encoding="utf-8")
        (tmp_path / "city_merge.yaml").write_text(
            "Minsk City: Minsk\n", encoding="utf-8")
        (tmp_path / "categories.json").write_text(
            json.dumps({"category_groups": {"Food": ["Cafe"]}}), encoding="utf-8")
        m = load_mappings(tmp_path)
        assert m["country_fixes"] == {"123": "Belarus"}
        assert m["city_merge"] == {"Minsk City": "Minsk"}
        assert m["category_groups"] == {"Food": ["Cafe"]}

    def test_real_repo_config_loads(self):
        # Smoke: the actual config/ dir parses and has the expected shape.
        from pathlib import Path
        cfg = Path(__file__).resolve().parents[1] / "config"
        m = load_mappings(cfg)
        assert isinstance(m["city_merge"], dict) and m["city_merge"]
        assert isinstance(m["venue_fixes"], dict)
        assert isinstance(m["category_groups"], dict) and m["category_groups"]
