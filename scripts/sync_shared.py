#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""sync_shared.py — keep the code both dashboards share identical.

swarm-journal (Darya's dashboard) runs the same pipeline with its own data,
settings and a few deliberate differences (build.py, transform.py, the travel
atlas, D1). The files below have NO intended difference: a feature written here
is copied there instead of being ported by hand, and --check catches drift.

    python scripts/sync_shared.py --other ../swarm-journal --check   # exit 1 on drift
    python scripts/sync_shared.py --other ../swarm-journal --write   # copy ours over

Line endings are ignored (both repos carry a mix of CRLF and LF).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Identical in both repos by design. Per-site config (settings, fixes, trip names,
# privacy.json, summit_pins.json, capitals) and the files with deliberate
# differences are not in this list.
SHARED = (
    "scripts/fetch_geo_context.py",
    "scripts/gen_stats.py",
    "scripts/metrics/explore.py",
    "scripts/metrics/frontiers.py",
    "scripts/metrics/rhythms.py",
    "scripts/metrics/horizons.py",
    "config/regions_iso.json",
    "config/region_fixes.json",
    "config/categories.json",
    "config/venue_category_fixes.json",
    "config/continents.json",
    "config/chains.json",
    "config/unesco.json",
    "config/cuisines.json",
    "config/countries_geo.json",
    "config/day_skip_venues.json",
)


def _norm(p: Path) -> bytes:
    return p.read_bytes().replace(b"\r\n", b"\n")


def drift(other: Path) -> list[str]:
    out = []
    for rel in SHARED:
        a, b = ROOT / rel, other / rel
        if not a.exists():
            out.append(f"{rel}: missing here")
        elif not b.exists():
            out.append(f"{rel}: missing in {other.name}")
        elif _norm(a) != _norm(b):
            out.append(f"{rel}: differs")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--other", required=True, help="path to the other checkout (../swarm-journal)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = ap.parse_args()
    other = Path(args.other).resolve()
    if not other.is_dir():
        print(f"no such checkout: {other}", file=sys.stderr)
        return 2
    found = drift(other)
    if args.check:
        for line in found:
            print(line)
        print("shared files in sync" if not found else f"{len(found)} shared file(s) drifted")
        return 1 if found else 0
    for rel in SHARED:
        src = ROOT / rel
        if src.exists():
            dst = other / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(_norm(src))
    print(f"copied {len(SHARED)} shared files to {other}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
