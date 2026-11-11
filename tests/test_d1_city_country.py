# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""sync_to_d1.city_country_updates — D1 city/country drift is judged against D1 itself."""
from __future__ import annotations

from sync_to_d1 import city_country_updates


def row(cid, city, country, inferred=0):
    r = [""] * 23
    r[0], r[5], r[7], r[22] = cid, city, country, inferred
    return r


def lit(v):
    return "NULL" if v is None or v == "" else "'" + str(v).replace("'", "''") + "'"


def test_only_rows_that_differ_from_d1_are_updated():
    rows = [row("a", "Brest", "Belarus"), row("b", "Ungheni", "Romania"), row("c", "Minsk", "Belarus"),
            row("new", "Vilnius", "Lithuania")]
    d1 = {"a": ("Terespol", "Poland", 0),      # left over from a reverted rule: back to the raw value
          "b": ("Ungheni", "Romania", 0),       # in sync
          "c": ("Minsk", "Belarus", 1)}         # city_inferred drifted
    out = city_country_updates(rows, d1, lit)
    assert out == ["UPDATE checkins SET city='Brest',country='Belarus',city_inferred=0 WHERE id='a'",
                   "UPDATE checkins SET city='Minsk',country='Belarus',city_inferred=0 WHERE id='c'"]


def test_empty_and_null_are_equal():
    assert city_country_updates([row("a", "", "Belarus")], {"a": (None, "Belarus", "0")}, lit) == []
