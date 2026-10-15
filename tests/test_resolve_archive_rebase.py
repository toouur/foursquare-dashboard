# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""resolve_archive_rebase — the archive snapshot meets a newer hourly commit."""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess

import pytest

from resolve_archive_rebase import merge_checkins, merge_photos

FIELDS = ["date", "venue", "venue_id", "checkin_id"]


def _csv(*rows) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(FIELDS)
    w.writerows(rows)
    return out.getvalue()


def test_snapshot_wins_new_rows_kept_deleted_dropped():
    archive = _csv(["1", "New name", "v1", "a"], ["3", "Bar", "v3", "c"])
    upstream = _csv(["1", "Old name", "v1", "a"], ["2", "Gone", "v2", "b"],
                    ["3", "Bar", "v3", "c"], ["4", "Fresh", "v4", "d"])
    merged = list(csv.DictReader(io.StringIO(merge_checkins(archive, upstream, {"b"}))))
    assert [(r["checkin_id"], r["venue"]) for r in merged] == [
        ("a", "New name"), ("c", "Bar"), ("d", "Fresh")]


def test_csv_duplicate_rows_survive():
    archive = _csv(["1", "X", "v", "a"], ["1", "X", "v", "a"])
    assert merge_checkins(archive, archive, set()).count("\r\n1,X,v,a") == 2


def test_photos_take_upstream_minus_deleted():
    up = json.dumps({"a": ["1.jpg"], "b": ["2.jpg"], "d": ["4.jpg"]})
    assert json.loads(merge_photos(up, {"b"})) == {"a": ["1.jpg"], "d": ["4.jpg"]}


@pytest.fixture
def repo(tmp_path):
    def git(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, check=False,
                              capture_output=True, text=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t"); git("config", "user.name", "t")
    git("config", "commit.gpgsign", "false")
    return tmp_path, git


def test_real_rebase_conflict_is_resolved(repo):
    import pathlib
    import sys
    path, git = repo
    script = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "resolve_archive_rebase.py"
    base = _csv(["1", "Old", "v1", "a"], ["2", "Gone", "v2", "b"])
    (path / "checkins.csv").write_text(base, newline="")
    (path / "photos.json").write_text('{"a":[],"b":["b.jpg"]}')
    git("add", "."); git("commit", "-qm", "base")
    git("checkout", "-qb", "archive")
    (path / "checkins.csv").write_text(_csv(["1", "New", "v1", "a"]), newline="")
    (path / "photos.json").write_text('{"a":[]}')
    git("commit", "-qam", "snapshot")
    git("checkout", "-q", "main")
    (path / "checkins.csv").write_text(base + "3,Fresh,v3,c\r\n", newline="")
    (path / "photos.json").write_text('{"a":[],"b":["b.jpg"],"c":["c.jpg"]}')
    git("commit", "-qam", "hourly")
    git("checkout", "-q", "archive")
    assert git("rebase", "main").returncode != 0          # the collision CI hits
    (path / "deleted.txt").write_text("b\n")
    res = subprocess.run([sys.executable, str(script), "--deleted", str(path / "deleted.txt")],
                         cwd=path, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    assert subprocess.run(["git", "rebase", "--continue"], cwd=path, capture_output=True,
                          env={**os.environ, "GIT_EDITOR": "true"}).returncode == 0
    rows = list(csv.DictReader(open(path / "checkins.csv", newline="")))
    assert [(r["checkin_id"], r["venue"]) for r in rows] == [("a", "New"), ("c", "Fresh")]
    assert json.loads((path / "photos.json").read_text()) == {"a": [], "c": ["c.jpg"]}
