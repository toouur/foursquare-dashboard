# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
enrich_overlaps.py — Backfill overlaps_name/overlaps_id for historical check-ins.

Fetches individual check-in details via /checkins/{id} and writes results
immediately to the CSV after each row. Designed for a one-time local run.

Resume: already-processed rows (overlaps_id != "") are skipped automatically.

Error handling:
  - 401 (auth)  → quit immediately
  - 403 (access denied) → mark as "-", continue
  - Other errors → pause PAUSE_MINUTES, retry same row up to MAX_RETRIES times,
                   then mark as "-" and continue. After MAX_PAUSES consecutive
                   pause cycles with no progress → quit.

Usage:
    python scripts/enrich_overlaps.py --token TOKEN --csv data/checkins.csv
    python scripts/enrich_overlaps.py --token-file G:/FoursquareDashboardClaude/local_parsing/token.txt \
                                      --csv G:/FoursquareDashboardClaude/local_parsing/checkins.csv
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

API_V        = "20231201"
SLEEP        = 0.35   # seconds between calls
PAUSE_MINUTES = 30    # pause on transient errors
MAX_RETRIES  = 3      # retries per row before marking as "-"
MAX_PAUSES   = 3      # consecutive pause cycles before quitting


def load_csv(path: Path) -> tuple[list[dict], list[str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = reader.fieldnames or []
    return rows, list(fields)


def save_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    os.replace(tmp, path)


def fetch_overlaps(token: str, checkin_id: str) -> tuple[str, str] | None:
    """Return (overlaps_name, overlaps_id) or None on transient error.
    Raises SystemExit on 401. Returns ("-", "-") on 403.
    """
    resp = requests.get(
        f"https://api.foursquare.com/v2/checkins/{checkin_id}",
        params={"oauth_token": token, "v": API_V},
        timeout=30,
    )
    if resp.status_code == 401:
        log.error("401 Unauthorized — token expired or revoked. Quitting.")
        raise SystemExit(1)
    if resp.status_code == 403:
        return "-", "-"
    resp.raise_for_status()

    ci = resp.json().get("response", {}).get("checkin", {})
    overlap_items = ci.get("overlaps", {}).get("items", [])
    overlap_users = [item.get("user", {}) for item in overlap_items if item.get("user")]
    name = ", ".join(
        (u.get("firstName", "") + " " + u.get("lastName", "")).strip()
        for u in overlap_users
    ).strip()
    uid = ", ".join(str(u.get("id", "")) for u in overlap_users) or "-"
    return name, uid


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill overlaps for historical check-ins")
    parser.add_argument("--token",      default="",  help="Foursquare OAuth token")
    parser.add_argument("--token-file", default="",  help="Path to file containing the token")
    parser.add_argument("--csv",        default="G:/FoursquareDashboardClaude/local_parsing/checkins.csv",
                        help="Path to checkins.csv")
    parser.add_argument("--sleep",      type=float, default=SLEEP,
                        help=f"Seconds between API calls (default {SLEEP})")
    parser.add_argument("--pause",      type=int,   default=PAUSE_MINUTES,
                        help=f"Minutes to pause on transient errors (default {PAUSE_MINUTES})")
    args = parser.parse_args()

    token = args.token.strip()
    if not token and args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    if not token:
        token_file = Path(args.csv).parent / "token.txt"
        if token_file.exists():
            token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        log.error("No token provided. Use --token or --token-file.")
        raise SystemExit(1)

    csv_path = Path(args.csv)
    rows, fields = load_csv(csv_path)

    # Add new fields if missing
    for f in ("overlaps_name", "overlaps_id", "checkin_id"):
        if f not in fields:
            fields.append(f)
            for r in rows:
                r.setdefault(f, "")

    to_do = [r for r in rows if r.get("checkin_id", "").strip() and r.get("overlaps_id", "") == ""]
    total = len(to_do)
    log.info("Rows to enrich: %d / %d total", total, len(rows))

    if not total:
        log.info("Nothing to do — all rows already processed.")
        return

    done = 0
    found = 0
    consecutive_pauses = 0

    for row in to_do:
        cid = row["checkin_id"]
        retries = 0
        success = False

        while not success and retries <= MAX_RETRIES:
            try:
                name, uid = fetch_overlaps(token, cid)
                row["overlaps_name"] = name if name != "-" else ""
                row["overlaps_id"]   = uid
                save_csv(csv_path, rows, fields)
                done += 1
                consecutive_pauses = 0
                if name and name != "-":
                    found += 1
                    log.info("[%d/%d] overlaps: %s @ %s", done, total, name, row.get("venue", ""))
                elif done % 500 == 0:
                    log.info("[%d/%d] progress checkpoint", done, total)
                success = True
                time.sleep(args.sleep)

            except SystemExit:
                raise
            except Exception as exc:
                retries += 1
                if retries > MAX_RETRIES:
                    log.warning("[%d/%d] giving up on %s after %d retries: %s — marking as skipped",
                                done + 1, total, cid, MAX_RETRIES, exc)
                    row["overlaps_id"] = "-"
                    save_csv(csv_path, rows, fields)
                    done += 1
                    consecutive_pauses += 1
                    success = True
                else:
                    log.warning("Error on %s (attempt %d/%d): %s — pausing %d min …",
                                cid, retries, MAX_RETRIES, exc, args.pause)
                    time.sleep(args.pause * 60)

        if consecutive_pauses >= MAX_PAUSES:
            log.error("%d consecutive failures — quitting. Re-run to resume.", MAX_PAUSES)
            break

    log.info("Done. %d/%d processed, %d had overlapping friends.", done, total, found)


if __name__ == "__main__":
    main()
