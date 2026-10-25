# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""fetch_checkins likes sidecar: what a check-in's likes look like in checkin_likes.json."""
from __future__ import annotations

import json

import fetch_checkins as fc


def _ci(cid, n, people=()):
    return {"id": cid, "likes": {"count": n, "groups": [{"type": "friends", "count": len(people),
            "items": [{"id": uid, "firstName": f, "lastName": la} for uid, f, la in people]}] if people else []}}


def test_likes_are_merged_and_unliked_ones_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "LIKES_SEEN", {})
    path = tmp_path / "checkin_likes.json"
    path.write_text(json.dumps({"old": {"n": 1, "by": [["9", "Old"]]}, "gone": {"n": 1, "by": []}}), encoding="utf-8")
    fc.note_likes(_ci("a", 2, [("1", "Nikita", "Boyko"), ("2", "Luiz", "Merino")]))
    fc.note_likes(_ci("gone", 0))                         # a like was taken back
    fc.note_likes({"id": "nolikes"})                      # no likes block at all: leave the file alone
    assert fc.save_likes(path) is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"a": {"n": 2, "by": [["1", "Nikita Boyko"], ["2", "Luiz Merino"]]}, "old": {"n": 1, "by": [["9", "Old"]]}}
    assert fc.save_likes(path) is False                   # nothing new → unchanged


def test_api_to_row_records_likes_but_csv_row_has_no_likes(monkeypatch):
    monkeypatch.setattr(fc, "LIKES_SEEN", {})
    row = fc.api_to_row({**_ci("x1", 1, [("5", "Rene", "")]), "createdAt": 1, "venue": {"id": "v"}})
    assert fc.LIKES_SEEN == {"x1": {"n": 1, "by": [["5", "Rene"]]}}
    assert "likes" not in row and set(row) <= set(fc.FIELDS)
