#!/usr/bin/env python3
"""Keep the headline numbers in README.md (check-ins, places, countries, km,
photos, years, page counts) in step with the live site.

build.py writes `site_stats.json` next to the pages; this script reads the
deployed copy (or a local one) and rewrites only the numbers inside known
phrases, with the same engine and exit codes as sync_doc_counts.py:

  0 in sync (or rewritten), 1 drift under --check, 2 a phrase vanished.

Usage:
  python scripts/sync_readme_stats.py                         # live site
  python scripts/sync_readme_stats.py --stats _site/site_stats.json --check
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import sync_doc_counts

STATS_URL = "https://4sq.pages.dev/site_stats.json"
EARTH_KM = 40_075

RULES: list[tuple[str, str, str]] = [
    ("README.md", r"\*\*(?P<n>[\d,]+)\+ Foursquare/Swarm check-ins", "checkins_k"),
    ("README.md", r"spanning \*\*(?P<n>\d+) years and", "years"),
    ("README.md", r"years and (?P<n>\d+) countries\*\*", "countries"),
    ("README.md", r"a (?P<n>[\d,]+)-photo gallery", "photos_k"),
    ("README.md", r"\*\*(?P<n>[\d,]+) check-ins · ", "checkins"),
    ("README.md", r"· (?P<n>[\d,]+) unique places", "places"),
    ("README.md", r"unique places · (?P<n>\d+) countries", "countries"),
    ("README.md", r"countries · (?P<n>[\d,]+) km\*\*", "km"),
    ("README.md", r"\((?P<n>\d+)× around Earth\)", "earths"),
    ("README.md", r"\+ (?P<n>\d+) year albums", "year_pages"),
    ("README.md", r"\+ (?P<n>\d+) country pages", "country_pages"),
    ("README.md", r"for all (?P<n>\d+) countries", "country_pages"),
    ("README.md", r"\*\*(?P<n>[\d,]+)\+ photos\*\*", "photos_k"),
]


def load(src: str) -> dict:
    if src.startswith("http"):
        req = urllib.request.Request(src, headers={"User-Agent": "sync_readme_stats"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    return json.loads(Path(src).read_text(encoding="utf-8"))


def values(s: dict) -> dict[str, int | str]:
    """Raw stats → the strings the README prints (thousands rounded down)."""
    return {
        "checkins": f"{s['checkins']:,}",
        "checkins_k": f"{s['checkins'] // 1000 * 1000:,}",
        "places": f"{s['places']:,}",
        "countries": s["countries"],
        "km": f"{s['km']:,}",
        "earths": s["km"] // EARTH_KM,
        "photos_k": f"{s['photos'] // 1000 * 1000:,}",
        "years": s["years"],
        "year_pages": s["year_pages"],
        "country_pages": s["country_pages"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stats", default=STATS_URL, help="site_stats.json path or URL")
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit 1 instead of rewriting")
    args = ap.parse_args()

    stats = load(args.stats)
    if not stats.get("checkins") or not stats.get("km"):
        print(f"implausible stats, not touching README: {stats}", file=sys.stderr)
        return 2
    vals = values(stats)
    print("stats: " + ", ".join(f"{k}={v}" for k, v in vals.items()))
    changes, missing = sync_doc_counts.apply(vals, write=not args.check, rules=RULES)
    for line in changes:
        print(("DRIFT " if args.check else "UPDATED ") + line)
    for line in missing:
        print("MISSING " + line, file=sys.stderr)
    print(f"README_STATS_CHANGED={'true' if changes else 'false'}")
    if missing:
        return 2
    if args.check and changes:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
