# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""fetch_checkins --full: check-ins deleted in Swarm are found safely.

A row is reported deleted only when the full fetch reached the server total,
the batch is small, and Swarm itself answers "Invalid checkin id" for it.
"""
from __future__ import annotations

import csv
import json
import sys

import pytest
import requests

import fetch_checkins as fc


def _row(cid, ts, vid="v" * 24, venue="Cafe"):
    r = {f: "" for f in fc.FIELDS}
    r.update(checkin_id=cid, date=str(ts), venue_id=vid, venue=venue, city_inferred="0")
    return r


class _Resp:
    def __init__(self, status, detail=""):
        self.status_code = status
        self._detail = detail

    def json(self):
        return {"meta": {"code": self.status_code, "errorDetail": self._detail}}


class TestCandidates:
    def test_matched_by_checkin_id_not_by_venue(self):
        # A venue merge keeps the id and changes venue_id: an update, not a deletion.
        existing = [_row("a", 1), _row("b", 2, vid="old" + "x" * 21), _row("c", 3)]
        fetched = [_row("a", 1), _row("b", 2, vid="new" + "x" * 21)]
        cands, skip = fc.swarm_deletion_candidates(existing, fetched, complete=True)
        assert [r["checkin_id"] for r in cands] == ["c"] and skip == ""

    def test_incomplete_fetch_is_never_acted_on(self):
        cands, skip = fc.swarm_deletion_candidates([_row("a", 1)], [], complete=False)
        assert len(cands) == 1 and "server total" in skip

    def test_mass_disappearance_is_refused(self):
        existing = [_row(str(i), i) for i in range(fc.MAX_AUTO_DELETE + 1)]
        cands, skip = fc.swarm_deletion_candidates(existing, [], complete=True)
        assert len(cands) == fc.MAX_AUTO_DELETE + 1 and "API fault" in skip

    def test_csv_duplicate_rows_are_one_candidate(self):
        cands, _ = fc.swarm_deletion_candidates([_row("a", 1), _row("a", 1)], [], complete=True)
        assert len(cands) == 1


class TestConfirm:
    def test_only_invalid_id_answers_count_as_deleted(self, monkeypatch):
        monkeypatch.setattr(fc, "SLEEP", 0)
        answers = {
            "gone": _Resp(400, "Invalid checkin id"),
            "g404": _Resp(404),
            "live": _Resp(200),
            "auth": _Resp(401, "OAuth token invalid or revoked."),
            "bad":  _Resp(400, "Must provide a valid parameter"),
        }

        def get(url, **_):
            cid = url.rsplit("/", 1)[-1]
            if cid == "net":
                raise requests.ConnectionError("boom")
            return answers[cid]

        rows = [_row(c, i) for i, c in enumerate(["gone", "g404", "live", "auth", "bad", "net"])]
        gone = fc.confirm_deleted("t", rows, getter=get)
        assert [r["checkin_id"] for r in gone] == ["gone", "g404"]


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fc.FIELDS)
        w.writeheader()
        w.writerows(rows)


@pytest.mark.parametrize("complete", [True, False])
def test_full_mode_end_to_end(tmp_path, monkeypatch, complete):
    csv_path = tmp_path / "checkins.csv"
    out = tmp_path / "deleted.txt"
    keep = _row("a", 100)
    keep["overlaps_name"], keep["overlaps_id"] = "Friend", "42"
    merged_old = _row("b", 200, vid="old" + "x" * 21, venue="Old name")
    dead = _row("c", 300)
    _write_csv(csv_path, [keep, merged_old, dead])
    (tmp_path / "checkins_anomalies.json").write_text(json.dumps(
        {"missing": [_row("stale", 5)], "duplicates": []}), encoding="utf-8")

    fetched = [_row("a", 100), _row("b", 200, vid="new" + "x" * 21, venue="New name")]
    monkeypatch.setattr(fc, "IS_CI", True)
    monkeypatch.setattr(fc, "SLEEP", 0)
    monkeypatch.setattr(fc, "fetch_full_timestamp", lambda token: (fetched, complete))
    probed = []

    def get(url, **_):
        probed.append(url)
        return _Resp(400, "Invalid checkin id")

    monkeypatch.setattr(fc.requests, "get", get)
    monkeypatch.setattr(sys, "argv", ["fetch_checkins.py", "--token", "t", "--csv", str(csv_path),
                                      "--full", "--deleted-out", str(out)])
    fc.main()

    rows = {r["checkin_id"]: r for r in csv.DictReader(open(csv_path, encoding="utf-8"))}
    # The merge reached the CSV, the overlap survived, and the row is only
    # REPORTED — removal (CSV + D1 + photos + R2) is delete_checkin.py's job.
    assert rows["b"]["venue"] == "New name" and rows["b"]["venue_id"].startswith("new")
    assert rows["a"]["overlaps_name"] == "Friend"
    assert "c" in rows
    anomalies = json.loads((tmp_path / "checkins_anomalies.json").read_text(encoding="utf-8"))
    if complete:
        assert out.read_text(encoding="utf-8") == "c\n"
        assert anomalies["missing"] == []          # stale accumulation wiped
    else:
        assert out.read_text(encoding="utf-8") == "" and probed == []
        assert [r["checkin_id"] for r in anomalies["missing"]] == ["stale"]  # untouched
