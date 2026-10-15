# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Resolve a rebase conflict between the archive snapshot and a newer data commit.

archive-checkins.yml commits a full re-fetch of checkins.csv a few minutes after
it checked the data repo out. If the hourly job commits a new check-in (and its
photo) in between, the push is rejected and a plain `git pull --rebase` collides
on the very same files. Both sides are known, so the conflict has one right
answer:

  checkins.csv  the archive's version (the fresh API mirror) plus every row the
                other side added that the archive does not have, minus the
                check-ins confirmed deleted in Swarm;
  photos.json   the other side's version (it may hold new photos) minus the
                deleted check-ins — the archive only ever removes keys from it.

Any other conflicted file is not ours to guess: exit 1 and let the caller abort.

Run inside a stopped rebase (stage 2 = upstream, stage 3 = the archive commit).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
from pathlib import Path

RESOLVABLE = {"checkins.csv", "photos.json"}


def merge_checkins(archive_csv: str, upstream_csv: str, deleted: set[str]) -> str:
    archive_csv, upstream_csv = archive_csv.lstrip("\ufeff"), upstream_csv.lstrip("\ufeff")
    arch = list(csv.DictReader(io.StringIO(archive_csv)))
    up = list(csv.DictReader(io.StringIO(upstream_csv)))
    fields = list(csv.DictReader(io.StringIO(archive_csv)).fieldnames or [])
    have = {r.get("checkin_id", "") for r in arch}
    added = [r for r in up if r.get("checkin_id") and r["checkin_id"] not in have]
    rows = [r for r in arch + added if r.get("checkin_id", "") not in deleted]
    rows.sort(key=lambda r: int(r.get("date") or 0))   # stable: CSV duplicates keep order
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore", lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


def merge_photos(upstream_json: str, deleted: set[str]) -> str:
    photos = json.loads(upstream_json)
    for cid in deleted:
        photos.pop(cid, None)
    return json.dumps(photos, ensure_ascii=False, separators=(",", ":"))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deleted", default="", help="file with deleted checkin ids, one per line")
    args = ap.parse_args()
    deleted: set[str] = set()
    if args.deleted and Path(args.deleted).exists():
        deleted = {ln.strip() for ln in Path(args.deleted).read_text().splitlines() if ln.strip()}

    conflicted = [f for f in _git("diff", "--name-only", "--diff-filter=U").split() if f]
    other = sorted(set(conflicted) - RESOLVABLE)
    if other:
        print(f"cannot resolve: {', '.join(other)}")
        return 1
    for f in conflicted:
        upstream, archive = _git("show", f":2:{f}"), _git("show", f":3:{f}")
        if f == "checkins.csv":
            merged = merge_checkins(archive, upstream, deleted)
        else:
            merged = merge_photos(upstream, deleted)
        with open(f, "w", encoding="utf-8", newline="") as fh:
            fh.write(merged)
        _git("add", f)
        print(f"resolved {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
