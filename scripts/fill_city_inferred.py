# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
One-off migration: add city_inferred column to checkins.csv.

Reads the existing CSV and the blank-city review CSV, applies the same
two-pass blank-city resolver used at build time, then writes city_inferred=1
for rows whose city was inferred (and persists the inferred city into the CSV),
and city_inferred=0 for all other rows.

Run once after adding city_inferred to the FIELDS schema in fetch_checkins.py.

Usage:
    python scripts/fill_city_inferred.py \\
        --csv  C:/path/to/checkins.csv \\
        --review-csv C:/path/to/blank_city_review.csv \\
        --config-dir config \\
        [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

# Must match fetch_checkins.FIELDS — city_inferred inserted before checkin_id
FIELDS = [
    "date", "venue", "venue_id", "venue_url", "city", "state", "country",
    "neighborhood", "lat", "lng", "address", "category", "shout",
    "source_app", "source_url", "with_name", "with_id",
    "created_by_name", "created_by_id",
    "overlaps_name", "overlaps_id",
    "city_inferred",
    "checkin_id",
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv",        required=True, help="Path to checkins.csv")
    ap.add_argument("--review-csv", required=True,
                    help="Path to blank-city review CSV (decision_source=BLANK_CITY_INFERRED rows)")
    ap.add_argument("--config-dir", default="config", help="Config directory for city_merge.yaml")
    ap.add_argument("--dry-run",    action="store_true", help="Report counts without writing")
    args = ap.parse_args()

    csv_path    = Path(args.csv)
    review_path = Path(args.review_csv)

    if not csv_path.exists():
        sys.exit(f"CSV not found: {csv_path}")
    if not review_path.exists():
        sys.exit(f"Review CSV not found: {review_path}")

    # Import transform helpers
    sys.path.insert(0, str(Path(__file__).parent))
    from transform import build_blank_city_resolver, load_mappings, apply_transforms

    log.info("Loading mappings from %s …", args.config_dir)
    mappings = load_mappings(args.config_dir)

    log.info("Building blank-city resolver from %s …", review_path)
    resolver = build_blank_city_resolver(review_path)

    log.info("Reading %s …", csv_path)
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    log.info("  %d rows loaded", len(rows))

    # Apply transforms (sets city_inferred on each row in-place)
    apply_transforms(rows, mappings, blank_city_resolver=resolver)

    filled    = sum(1 for r in rows if r.get("city_inferred") == "1")
    not_filled = len(rows) - filled
    log.info("city_inferred=1 (inferred): %d rows", filled)
    log.info("city_inferred=0 (FS data):  %d rows", not_filled)

    if args.dry_run:
        log.info("Dry run — nothing written.")
        return

    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    log.info("Written: %s (%d rows)", csv_path, len(rows))


if __name__ == "__main__":
    main()
