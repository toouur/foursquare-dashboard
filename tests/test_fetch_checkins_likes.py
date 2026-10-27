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


def test_own_like_and_non_friends_are_kept_apart(monkeypatch):
    # the API's own shapes (2026-10-27): a self-like has no group, only summary
    # "You"; people outside your friends are an "others" group with no items
    monkeypatch.setattr(fc, "LIKES_SEEN", {})
    fc.note_likes({"id": "self", "likes": {"count": 1, "groups": [], "summary": "You"}})
    fc.note_likes({"id": "mix", "likes": {"count": 4, "summary": "Iennifer, Bojan & 2 others", "groups": [
        {"type": "friends", "count": 2, "items": [{"id": "1", "firstName": "Iennifer"},
                                                  {"id": "2", "firstName": "Bojan", "lastName": "B"}]},
        {"type": "others", "count": 2, "items": []}]}})
    assert fc.LIKES_SEEN["self"] == {"n": 1, "by": [], "me": 1}
    assert fc.LIKES_SEEN["mix"] == {"n": 4, "by": [["1", "Iennifer"], ["2", "Bojan B"]]}
