# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

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
import json
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
    "created_by_name", "created_by_id",
    "overlaps_name", "overlaps_id",
    "checkin_id",
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

    overlap_items = ci.get("overlaps", {}).get("items", [])
    overlap_users = [item.get("user", {}) for item in overlap_items if item.get("user")]
    overlaps_names = ", ".join(
        (u.get("firstName", "") + " " + u.get("lastName", "")).strip()
        for u in overlap_users
    ).strip()
    overlaps_ids = ", ".join(str(u.get("id", "")) for u in overlap_users)

    source = ci.get("source", {})
    vid = str(venue.get("id", "") or "")

    # created_by: populated only when a friend checked you in on your behalf
    created_by = ci.get("createdBy", {})
    owner_id = str(ci.get("user", {}).get("id", "") or "")
    creator_id = str(created_by.get("id", "") or "")
    if creator_id and creator_id != owner_id:
        created_by_name = (created_by.get("firstName", "") + " " + created_by.get("lastName", "")).strip()
        created_by_id = creator_id
    else:
        created_by_name = ""
        created_by_id = ""

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
        "created_by_name": created_by_name,
        "created_by_id": created_by_id,
        "overlaps_name": overlaps_names,
        "overlaps_id": overlaps_ids,
        "checkin_id": str(ci.get("id", "") or ""),
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


def enrich_overlaps(token: str, rows: list[dict], max_calls: int = 0) -> int:
    """Fetch individual check-in details to populate overlaps_name/overlaps_id.

    Only processes rows that have a checkin_id and empty overlaps_name.
    For full re-fetch, pass max_calls to cap API usage (0 = unlimited).
    Rows are processed newest-first so recent check-ins are filled first.
    Returns number of rows where overlaps were found.
    """
    to_enrich = [r for r in rows if r.get("checkin_id") and not r.get("overlaps_name", "").strip()]
    to_enrich.sort(key=lambda r: int(r.get("date", 0) or 0), reverse=True)
    if max_calls:
        to_enrich = to_enrich[:max_calls]

    if not to_enrich:
        return 0

    log.info("Enriching overlaps for %d check-in(s) via individual API calls …", len(to_enrich))
    found = 0
    for row in to_enrich:
        cid = row["checkin_id"]
        try:
            resp = requests.get(
                f"https://api.foursquare.com/v2/checkins/{cid}",
                params={"oauth_token": token, "v": API_V},
                timeout=30,
            )
            resp.raise_for_status()
            ci = resp.json().get("response", {}).get("checkin", {})
            overlap_items = ci.get("overlaps", {}).get("items", [])
            overlap_users = [item.get("user", {}) for item in overlap_items if item.get("user")]
            row["overlaps_name"] = ", ".join(
                (u.get("firstName", "") + " " + u.get("lastName", "")).strip()
                for u in overlap_users
            ).strip()
            row["overlaps_id"] = ", ".join(str(u.get("id", "")) for u in overlap_users)
            if row["overlaps_name"]:
                found += 1
                log.info("  overlaps found: %s @ %s — %s", row["overlaps_name"], row.get("venue", ""), cid)
        except Exception as exc:
            log.warning("Failed to enrich overlaps for checkin %s: %s", cid, exc)
        time.sleep(SLEEP)

    log.info("Overlaps enrichment done: %d/%d had overlapping friends.", found, len(to_enrich))
    return found


def save_rows(csv_path: Path, rows: list[dict]) -> None:
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def update_anomalies(
    csv_path: Path,
    dup_rows: list[dict],
    dup_key_count: int,
    missing_rows: list[dict],
) -> None:
    """
    Merge duplicate and missing rows into <csv_dir>/checkins_anomalies.json.
    Existing entries are preserved; new ones are added (keyed by venue_id+date).
    """
    anomaly_path = csv_path.parent / "checkins_anomalies.json"
    existing: dict = {}
    if anomaly_path.exists():
        try:
            existing = json.loads(anomaly_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            existing = {}

    # Merge duplicates: accumulate by (venue_id, date) key
    dup_index: dict[str, dict] = {
        f"{r.get('venue_id','')}|{r.get('date','')}" : r
        for r in existing.get("duplicates", [])
    }
    for r in dup_rows:
        k = f"{r.get('venue_id','')}|{r.get('date','')}"
        dup_index[k] = r

    # Merge missing: accumulate by (venue_id, date) key
    miss_index: dict[str, dict] = {
        f"{r.get('venue_id','')}|{r.get('date','')}" : r
        for r in existing.get("missing", [])
    }
    for r in missing_rows:
        k = f"{r.get('venue_id','')}|{r.get('date','')}"
        miss_index[k] = r

    import datetime as _dt
    today = _dt.date.today().isoformat()
    new_dup_pairs = len({k.split("|")[0] + "|" + k.split("|")[1] for k in dup_index}) // 1
    out = {
        "_meta": {
            "description": "Tracked CSV anomalies: duplicate rows and check-ins missing from API responses.",
            "updated": today,
            "duplicates_count": dup_key_count or len({
                f"{r.get('venue_id','')}|{r.get('date','')}" for r in dup_index.values()
            }),
            "missing_count": len(miss_index),
        },
        "duplicates": sorted(dup_index.values(), key=lambda r: int(r.get("date") or 0)),
        "missing":    sorted(miss_index.values(), key=lambda r: int(r.get("date") or 0)),
    }
    anomaly_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "Updated %s — %d duplicate row(s), %d missing row(s)",
        anomaly_path, len(dup_index), len(miss_index),
    )


def max_timestamp(rows: list[dict]) -> int:
    valid = [int(r["date"]) for r in rows if str(r.get("date", "")).strip().isdigit()]
    return max(valid) if valid else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default="", help="Foursquare OAuth token")
    parser.add_argument("--csv", default="data/checkins.csv", help="Path to checkins CSV")
    parser.add_argument("--full", action="store_true", help="Force full re-fetch")
    parser.add_argument("--overlaps-limit", type=int, default=500,
                        help="Max individual API calls to enrich overlaps on full re-fetch (0=unlimited, default 500)")
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

        # Detect and log duplicates in existing CSV (same venue_id+date appearing >1 time)
        existing_key_count: dict[tuple[str, str], int] = {}
        for row in existing_rows:
            k = row_key(row)
            existing_key_count[k] = existing_key_count.get(k, 0) + 1
        dup_keys = {k for k, c in existing_key_count.items() if c > 1}
        if dup_keys:
            dup_log_path = csv_path.parent / "duplicate_checkins.csv"
            dup_rows = [r for r in existing_rows if row_key(r) in dup_keys]
            with open(dup_log_path, "w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(dup_rows)
            log.warning(
                "%d duplicate (venue_id, date) pair(s) in existing CSV (%d rows) — saved to %s",
                len(dup_keys), len(dup_rows), dup_log_path,
            )

        # Merge: preserve existing rows (including any duplicates); update venue info
        # from fetched rows where keys match; append genuinely new rows.
        fetched_map: dict[tuple[str, str], dict] = {row_key(r): r for r in fetched_rows}
        existing_key_set = {row_key(r) for r in existing_rows}
        all_rows = [fetched_map.get(row_key(r), r) for r in existing_rows]
        for row in fetched_rows:
            if row_key(row) not in existing_key_set:
                all_rows.append(row)
        all_rows.sort(key=lambda r: int(r.get("date", 0) or 0))

        # Detect missing: keys in existing that the API no longer returns
        fetched_key_set = {row_key(r) for r in fetched_rows}
        unique_existing: dict[tuple, dict] = {}
        for r in existing_rows:
            unique_existing.setdefault(row_key(r), r)
        missing_rows = [r for k, r in unique_existing.items() if k not in fetched_key_set]
        missing_rows.sort(key=lambda r: int(r.get("date") or 0))
        if missing_rows:
            log.warning("%d row(s) in existing CSV not returned by API — recorded in anomalies file.", len(missing_rows))

        # Update anomalies file (duplicates + missing)
        dup_rows_for_anomaly = [r for r in existing_rows if row_key(r) in dup_keys] if dup_keys else []
        update_anomalies(csv_path, dup_rows_for_anomaly, len(dup_keys), missing_rows)

        changed = all_rows != existing_rows
        if changed:
            save_rows(csv_path, all_rows)
            log.info("Wrote %d total rows → %s", len(all_rows), csv_path)
        else:
            log.info("No content changes after full fetch.")

        enriched = enrich_overlaps(token, all_rows, max_calls=args.overlaps_limit)
        if enriched or any(r.get("overlaps_name") for r in all_rows):
            save_rows(csv_path, all_rows)
            log.info("Saved %d overlap enrichment(s).", enriched)

        print(f"CHANGED={'true' if changed or enriched else 'false'}")
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

    # Enrich overlaps for newly added rows before saving (no cap — few rows in incremental)
    enrich_overlaps(token, added, max_calls=0)

    all_rows = existing_rows + added
    all_rows.sort(key=lambda r: int(r.get("date", 0) or 0))
    save_rows(csv_path, all_rows)

    log.info("Wrote %d total rows → %s", len(all_rows), csv_path)
    print("CHANGED=true")


if __name__ == "__main__":
    main()