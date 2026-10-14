# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""companions.html flag: configured home country, else the most frequent one."""
from __future__ import annotations

import json
from pathlib import Path

from gen_companions import companion_country, load_companion_countries

REPO = Path(__file__).resolve().parent.parent


def _rows(*countries):
    return [{"country": c} for c in countries]


def test_override_wins_over_check_ins():
    assert companion_country("Darya", _rows("Moldova", "Moldova"), {"Darya": "Belarus"}) == "Belarus"


def test_fallback_is_the_most_frequent_country_not_the_latest():
    # newest first, as gen_companions orders them: the latest is Moldova
    assert companion_country("X", _rows("Moldova", "Belarus", "Belarus", ""), {}) == "Belarus"


def test_no_country_at_all():
    assert companion_country("X", _rows("", ""), {}) == ""


def test_shipped_config_loads_and_every_country_has_a_flag(tmp_path):
    overrides = load_companion_countries(REPO / "config")
    assert overrides.get("Darya") == "Belarus"
    assert not any(k.startswith("_") for k in overrides)
    flags = json.loads((REPO / "config" / "country_flags.json").read_text(encoding="utf-8"))
    assert [v for v in overrides.values() if v not in flags] == []
    assert load_companion_countries(tmp_path) == {}   # missing file -> no overrides
