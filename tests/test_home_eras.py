# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Distance-from-home must be measured against the home of the DAY.

Home moved Chișinău → Minsk (2012-09-01) → Chișinău (2026-07-29). A single flat
centroid would score ~14 years of Minsk life as a permanent 900 km journey and
the Minsk-era stay-at-home days as "nomad" days. These tests pin the era split.
"""
from datetime import datetime, timezone

from conftest import make_row
from metrics import process

MINSK = ("53.9045", "27.5615")
CHISINAU = ("46.999", "28.857")

# Era boundary used by the fixtures: 2012-09-01, mirroring settings.yaml.
CUTOVER = int(datetime(2012, 9, 1, tzinfo=timezone.utc).timestamp())
DAY = 86400


def _rows():
    """Two at-home days, one per era: Chișinău before the move, Minsk after."""
    return [
        make_row(CUTOVER - 10 * DAY, city="Chișinău", country="Moldova",
                 category="Cafe", venue="Home cafe", venue_id="a" * 24,
                 lat=CHISINAU[0], lng=CHISINAU[1]),
        make_row(CUTOVER + 10 * DAY, city="Minsk", country="Belarus",
                 category="Cafe", venue="Home cafe", venue_id="b" * 24,
                 lat=MINSK[0], lng=MINSK[1]),
    ]


def _run(**over):
    kw = {
        "home_city": "Chișinău",
        "home_history": [(CUTOVER, "Chișinău"), (10 ** 11, "Minsk")],
    }
    kw.update(over)
    stats, _ = process(_rows(), {}, **kw)
    return dict(stats["distance_from_home"])


def test_each_era_measures_against_its_own_home():
    by_day = _run()
    assert len(by_day) == 2
    # Both days are at-home days in their own era → ~0 km from home.
    assert all(km < 20 for km in by_day.values()), by_day


def test_nomad_days_zero_when_both_days_are_at_home():
    stats, _ = process(_rows(), {}, home_city="Chișinău",
                       home_history=[(CUTOVER, "Chișinău"), (10 ** 11, "Minsk")])
    assert stats["nomad_kpis"]["nomad_days"] == 0
    assert stats["nomad_kpis"]["total_days"] == 2


def test_flat_home_misreads_the_other_era():
    # Without home_history every row is scored against today's home, so the
    # Minsk day reads as a 773 km trip (the real Chișinău–Minsk distance).
    # This is the bug the eras fix.
    by_day = _run(home_history=None)
    assert max(by_day.values()) > 700, by_day
