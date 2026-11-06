# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""sweep_overlaps: which Swarm overlaps become companion_fixes entries."""
from __future__ import annotations

import json

import sweep_overlaps as so

ME = "29447180"


def _row(cid, ts=100, **kw):
    r = {"checkin_id": cid, "date": str(ts), "venue": "Cafe", "with_id": "", "with_name": "",
         "created_by_id": "", "created_by_name": "", "overlaps_id": "", "overlaps_name": ""}
    r.update(kw)
    return r


def _ci(cid, *ovs):
    return {"id": cid, "overlaps": {"items": [
        {"user": {"id": u}, **({"createdBy": {"id": cb}} if cb else {})} for u, cb in ovs]}}


def test_classify_rules():
    rows = {"a": _row("a"), "b": _row("b", with_id="7", with_name="Ann"),
            "c": _row("c"), "d": _row("d")}
    fixes = {"d": {"remove_ids": "9"}}
    items = [_ci("a", ("5", None)),             # friend's own check-in -> add
             _ci("b", ("7", ME)),               # already in with -> nothing
             _ci("c", ("8", ME)),               # I tagged, no longer in with -> report
             _ci("d", ("9", None)),             # removed on purpose -> never re-add
             _ci("zz", ("5", None))]            # not in CSV
    add, untagged, missing = so.classify(items, rows, fixes, ME)
    assert add == {"a": ["5"]}
    assert untagged == [("c", "8")]
    assert missing == ["zz"]


def test_write_fixes_appends_and_keeps_existing(tmp_path):
    p = tmp_path / "companion_fixes.json"
    p.write_text('{\n  "_comment": "x",\n  "old": {"remove_ids": "1"}\n}\n', encoding="utf-8")
    rows = {"a": _row("a", overlaps_id="3", overlaps_name="Bob"), "old": _row("old")}
    n = so.write_fixes(p, {"a": ["5"], "old": ["6"]}, rows, {"5": "Eve"})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert n == 1 and data["old"] == {"remove_ids": "1"}
    assert data["a"]["overlaps_id"] == "3, 5" and data["a"]["overlaps_name"] == "Bob, Eve"
    assert p.read_text(encoding="utf-8").startswith('{\n  "_comment": "x",\n  "old": {"remove_ids": "1"}')


def test_known_people_covers_non_friends():
    rows = [{"with_id": "1, 2", "with_name": "A, B", "overlaps_id": "-", "overlaps_name": "",
             "created_by_id": "3", "created_by_name": "C"},
            {"with_id": "4,5", "with_name": "only-one", "overlaps_id": "", "overlaps_name": ""}]
    likes = {"cid": [["21874795", "Ecaterina Albu"], ["1", "A again"]]}
    got = so.known_people(rows, likes)
    assert got == {"1": "A", "2": "B", "3": "C", "4": "", "5": "", "21874795": "Ecaterina Albu"}
