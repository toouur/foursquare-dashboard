#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""check_city_drift.py — surface raw city values in the latest CSV that
the normalization pipeline doesn't recognise.

Helps catch new Foursquare city spellings before they silently pass
through and show up as raw native names on the dashboard.

A "drifted" raw city is one that:
  - has a non-blank `city` field, AND
  - the city is not present as a key in `config/city_merge.yaml`, AND
  - the city is not already the canonical English name (i.e., it's not
    a value already in city_merge.yaml), AND
  - the row is not covered by a per-timestamp / per-id override in
    `config/city_fixes.json`.

Exit code 0 if no drift; 1 if drift found. Suitable as a CI gate.

Usage:
    python scripts/check_city_drift.py \\
        --csv data/checkins.csv \\
        --config-dir config
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path


def _is_cyrillic_or_non_ascii(s: str) -> bool:
    """A drifted name is interesting if it looks foreign-script (CJK,
    Cyrillic, Arabic) or contains diacritics. Pure-ASCII names usually
    pass through OK even if not in the merge map."""
    for ch in s:
        if ord(ch) > 127:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--top", type=int, default=50, help="Report top-N drifted")
    ap.add_argument("--min-count", type=int, default=3,
                    help="Ignore drifts seen fewer than N times")
    ap.add_argument("--ascii-too", action="store_true",
                    help="Also report ASCII drifts (default: only non-ASCII)")
    args = ap.parse_args()

    cfg = Path(args.config_dir)

    # Load city_merge.yaml — keys are raw, values are canonical
    try:
        import yaml
    except ImportError:
        print("ERROR: pyyaml required (pip install pyyaml)", file=sys.stderr)
        return 2

    cm_path = cfg / "city_merge.yaml"
    merge_keys: set[str] = set()
    merge_values: set[str] = set()
    if cm_path.exists():
        data = yaml.safe_load(cm_path.read_text(encoding="utf-8")) or {}
        for k, v in data.items():
            if isinstance(k, str) and k.strip():
                merge_keys.add(k.strip())
            if isinstance(v, str) and v.strip():
                merge_values.add(v.strip())

    # Load city_fixes.json — keys are unix-ts or 24-char hex IDs
    cf_path = cfg / "city_fixes.json"
    fixed_keys: set[str] = set()
    if cf_path.exists():
        for k, v in json.loads(cf_path.read_text(encoding="utf-8")).items():
            if k.startswith("_"):
                continue
            fixed_keys.add(k)

    # Load canonical city set (for blank-resolver coverage)
    canonical_set: set[str] = set()
    canonical_path = cfg / "city_canonical.yaml"
    if canonical_path.exists():
        try:
            cd = yaml.safe_load(canonical_path.read_text(encoding="utf-8")) or {}
            canonical_set |= set(cd.get("valid_canonical", []))
        except Exception:
            pass

    # Scan CSV
    drift: Counter = Counter()
    drift_examples: dict[str, list[str]] = {}
    with open(args.csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw = (row.get("city") or "").strip()
            if not raw:
                continue
            ts = (row.get("date") or "").strip()
            cid = (row.get("checkin_id") or "").strip()
            # Skip if this row is overridden by city_fixes.json
            if ts in fixed_keys or cid in fixed_keys:
                continue
            # Skip if the raw value is already canonical (a value of city_merge.yaml)
            if raw in merge_values or raw in canonical_set:
                continue
            # Skip if mapped through city_merge.yaml
            if raw in merge_keys:
                continue
            # Optional ASCII gate
            if not args.ascii_too and not _is_cyrillic_or_non_ascii(raw):
                continue
            drift[raw] += 1
            if raw not in drift_examples:
                # Sample one (ts, venue) for context
                drift_examples[raw] = [
                    ts,
                    (row.get("venue") or "").strip()[:40],
                    (row.get("country") or "").strip(),
                ]

    flagged = [(c, n) for c, n in drift.most_common(args.top) if n >= args.min_count]

    if not flagged:
        print(f"OK — no drift detected ({len(drift)} below min-count={args.min_count})")
        return 0

    print(f"DRIFT — {len(flagged)} raw city names not in city_merge.yaml / city_canonical.yaml:")
    print(f"  (min-count={args.min_count}, non-ASCII only={'no' if args.ascii_too else 'yes'})\n")
    for name, n in flagged:
        ex = drift_examples.get(name, ["", "", ""])
        print(f"  {n:5} × {name!r}   (e.g. ts={ex[0]}, '{ex[1]}', country={ex[2]!r})")
    print("\nTo fix: add the canonical mapping to config/city_merge.yaml, e.g.")
    print(f'    "{flagged[0][0]}": "Canonical English"')
    return 1


if __name__ == "__main__":
    sys.exit(main())
