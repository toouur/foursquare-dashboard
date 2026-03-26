"""
fetch_checkins.py  –  Fetch check-ins from Foursquare/Swarm API and merge into
checkins.csv. Designed for GitHub Actions and local manual runs.

Usage examples:
    python scripts/fetch_checkins.py --token "$FOURSQUARE_TOKEN" --csv data/checkins.csv
    python scripts/fetch_checkins.py --full

Token resolution order:
    1) --token
    2) FOURSQUARE_TOKEN environment variable

Modes:
  - Incremental (default when CSV exists): fetch check-ins newer than latest CSV row.
  - Full (when --full is passed, or CSV does not exist): fetch complete history.

Outputs:
  - Always exits 0 for expected no-change situations.
  - Prints CHANGED=true/false to stdout for workflow conditions.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

API_BASE = "https://api.foursquare.com/v2/users/self/checkins"
API_V = "20231201"
LIMIT = 250
SLEEP = 0.35
IS_CI = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))

# ── CSV schema (must match existing checkins.csv) ──────────────────────────────
FIELDS = [
    "date", "venue", "venue_id", "venue_url", "city", "state", "country",
    "neighborhood", "lat", "lng", "address", "category", "shout",
    "source_app", "source_url", "with_name", "with_id",
]


def resolve_token(cli_token: str | None) -> str:
    cli = (cli_token or "").strip()
    if cli:
        return cli
    env_fsq = os.environ.get("FOURSQUARE_TOKEN", "").strip()
    if env_fsq:
        return env_fsq
    return ""


def load_existing(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with open(csv_path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def row_key(row: dict) -> tuple[str, str]:
    """Stable dedupe key: (venue_id, date)."""
    return (row.get("venue_id", "").strip(), str(row.get("date", "")).strip())


def request_checkins(token: str, params: dict, retries: int = 120) -> dict:
    """Fetch checkins with resilience for 500s during timestamp pagination."""
    for attempt in range(retries):
        try:
            resp = requests.get(
                API_BASE,
                params={"oauth_token": token, "v": API_V, **params},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Network error contacting Foursquare API: {exc}") from exc

        if resp.status_code == 500 and "beforeTimestamp" in params:
            params["beforeTimestamp"] = int(params["beforeTimestamp"]) - 1
            if attempt % 10 == 0:
                log.warning(
                    "500 near beforeTimestamp=%s; nudging 1s back (attempt %d/%d)",
                    params["beforeTimestamp"],
                    attempt + 1,
                    retries,
                )
            time.sleep(1)
            continue

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            detail = ""
            try:
                payload = resp.json()
                detail = f" | meta={payload.get('meta')}"
            except ValueError:
                detail = f" | body={resp.text[:300]}"
            raise RuntimeError(f"HTTP error from Foursquare API: {exc}{detail}") from exc

        data = resp.json()
        code = data.get("meta", {}).get("code")
        if code != 200:
            raise RuntimeError(f"API error: {data.get('meta')}")
        return data.get("response", {}).get("checkins", {})

    raise RuntimeError("Too many API retries while fetching check-ins")


def api_to_row(ci: dict) -> dict:
    venue = ci.get("venue", {})
    loc = venue.get("location", {})
    cats = venue.get("categories", [])
    primary = next((c for c in cats if c.get("primary")), cats[0] if cats else {})

    with_items = ci.get("with", [])
    with_names = ", ".join(
        (w.get("firstName", "") + " " + w.get("lastName", "")).strip()
        for w in with_items
    ).strip()
    with_ids = ", ".join(str(w.get("id", "")) for w in with_items)

    source = ci.get("source", {})
    vid = str(venue.get("id", "") or "")

    return {
        "date": str(ci.get("createdAt", "")),
        "venue": venue.get("name", ""),
        "venue_id": vid,
        "venue_url": f"https://foursquare.com/v/{vid}" if vid else "",
        "city": loc.get("city", ""),
        "state": loc.get("state", ""),
        "country": loc.get("country", ""),
        "neighborhood": loc.get("neighborhood", ""),
        "lat": str(loc.get("lat", "")),
        "lng": str(loc.get("lng", "")),
        "address": loc.get("address", ""),
        "category": primary.get("name", ""),
        "shout": ci.get("shout", ""),
        "source_app": source.get("name", ""),
        "source_url": source.get("url", ""),
        "with_name": with_names,
        "with_id": with_ids,
    }


def fetch_incremental(token: str, after_ts: int) -> list[dict]:
    rows: list[dict] = []
    before_ts: int | None = None

    while True:
        params: dict[str, int] = {"limit": LIMIT, "afterTimestamp": after_ts}
        if before_ts:
            params["beforeTimestamp"] = before_ts

        data = request_checkins(token, params)
        items = data.get("items", [])
        total = data.get("count", 0)

        if not items:
            break

        rows.extend(api_to_row(ci) for ci in items)
        log.info("Incremental fetched: %d / %d", len(rows), total)

        if len(rows) >= total:
            break

        before_ts = int(items[-1]["createdAt"]) - 1
        time.sleep(SLEEP)

    return rows


def fetch_full_offset(token: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0

    first = request_checkins(token, {"limit": 1})
    total = first.get("count", 0)
    log.info("Full fetch strategy: offset | server total=%s", total)

    while True:
        data = request_checkins(token, {"limit": LIMIT, "offset": offset})
        items = data.get("items", [])
        if not items:
            break

        rows.extend(api_to_row(ci) for ci in items)
        log.info("Full fetched: %d / %d", len(rows), total)

        if len(rows) >= total:
            break

        offset += LIMIT
        time.sleep(SLEEP)

    return rows


def fetch_full_timestamp(token: str) -> tuple[list[dict], bool]:
    """
    Fetch all check-ins walking backwards via beforeTimestamp.
    Returns (rows, completed).  completed=False when a quota/rate-limit error
    interrupts the fetch — partial rows are still returned so callers can
    merge them with the existing CSV rather than discarding the work done.
    """
    rows: list[dict] = []
    before_ts: int | None = None

    first = request_checkins(token, {"limit": 1})
    total = first.get("count", 0)
    log.info("Full fetch strategy: beforeTimestamp | server total=%s", total)

    while True:
        params: dict[str, int] = {"limit": LIMIT}
        if before_ts:
            params["beforeTimestamp"] = before_ts

        try:
            data = request_checkins(token, params)
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "rate_limit" in msg or "quota" in msg or "403" in msg:
                log.warning(
                    "Quota/rate-limit hit after %d rows — saving partial results.", len(rows)
                )
                return rows, False
            raise

        items = data.get("items", [])
        if not items:
            break

        rows.extend(api_to_row(ci) for ci in items)
        log.info("Full fetched: %d / %d", len(rows), total)

        if len(rows) >= total:
            break

        before_ts = int(items[-1]["createdAt"]) - 1
        time.sleep(SLEEP)

    return rows, True


def save_rows(csv_path: Path, rows: list[dict]) -> None:
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def max_timestamp(rows: list[dict]) -> int:
    valid = [int(r["date"]) for r in rows if str(r.get("date", "")).strip().isdigit()]
    return max(valid) if valid else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default="", help="Foursquare OAuth token")
    parser.add_argument("--csv", default="data/checkins.csv", help="Path to checkins CSV")
    parser.add_argument("--full", action="store_true", help="Force full re-fetch")
    args = parser.parse_args()

    token = resolve_token(args.token)
    if not token:
        log.error(
            "Missing API token. Provide --token, or set FOURSQUARE_TOKEN in environment."
        )
        print("CHANGED=false")
        return

    csv_path = Path(args.csv)
    existing_rows = load_existing(csv_path)
    existing_keys = {row_key(r) for r in existing_rows}

    after_ts = max_timestamp(existing_rows)
    do_full = args.full or not csv_path.exists() or not existing_rows

    if do_full:
        log.info("Mode: FULL %s", "(forced)" if args.full else "(csv missing/empty)")
        # Two strategies for a full fetch:
        #
        # fetch_full_offset  — uses ?offset=N pagination. Simple and reliable
        #   locally, but the Foursquare API silently caps offset at ~2,500,
        #   so histories longer than that are silently truncated.
        #
        # fetch_full_timestamp — walks backwards through history using
        #   ?beforeTimestamp=T.  No hard cap; handles large histories and the
        #   500-retry logic in request_checkins() covers occasional API gaps.
        #   Used in CI where a correct full history matters most.
        #   Returns (rows, completed); if quota is hit, partial rows are merged
        #   with existing CSV so the work done is not discarded.
        try:
            if IS_CI:
                fetched_rows, completed = fetch_full_timestamp(token)
                if not completed:
                    log.warning(
                        "Full fetch incomplete (quota). Merging %d partial rows with existing %d.",
                        len(fetched_rows), len(existing_rows),
                    )
            else:
                fetched_rows = fetch_full_offset(token)
                completed = True
        except RuntimeError as exc:
            log.error("Full fetch failed: %s", exc)
            print("CHANGED=false")
            return

        # Merge: existing rows as base, fetched rows override (fresher venue info)
        deduped: dict[tuple[str, str], dict] = {}
        for row in existing_rows:
            deduped[row_key(row)] = row
        for row in fetched_rows:
            deduped[row_key(row)] = row

        all_rows = list(deduped.values())
        all_rows.sort(key=lambda r: int(r.get("date", 0) or 0))

        changed = all_rows != existing_rows
        if changed:
            save_rows(csv_path, all_rows)
            log.info("Wrote %d total rows → %s", len(all_rows), csv_path)
        else:
            log.info("No content changes after full fetch.")

        print(f"CHANGED={'true' if changed else 'false'}")
        return

    log.info("Mode: INCREMENTAL (latest timestamp=%d)", after_ts)
    try:
        new_rows = fetch_incremental(token, after_ts)
    except RuntimeError as exc:
        log.error("Incremental fetch failed: %s", exc)
        print("CHANGED=false")
        return
    log.info("API returned %d candidate rows", len(new_rows))

    added = [r for r in new_rows if row_key(r) not in existing_keys]
    log.info("New (de-duped): %d", len(added))

    if not added:
        print("CHANGED=false")
        return

    all_rows = existing_rows + added
    all_rows.sort(key=lambda r: int(r.get("date", 0) or 0))
    save_rows(csv_path, all_rows)

    log.info("Wrote %d total rows → %s", len(all_rows), csv_path)
    print("CHANGED=true")


if __name__ == "__main__":
    main()