# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
fetch_photos.py — Fetch photos for new check-ins from Foursquare API.

Maintains photos.json: {checkin_id: [filename, ...]}

New check-ins (not yet in photos.json) are checked via the Foursquare API.
If photos are found, filenames are recorded. If --pix-dir is given, image
files are also downloaded there.

The script is CI-safe without --pix-dir (just updates the JSON index).
Run locally with --pix-dir to sync the actual image files.

Usage:
  # Update photos.json only (CI / GitHub Actions):
  python scripts/fetch_photos.py --token $TOKEN --csv data/checkins.csv --out data/photos.json

  # Update photos.json + download new images locally:
  python scripts/fetch_photos.py --token $TOKEN --csv data/checkins.csv --out data/photos.json --pix-dir data/pix/
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_API = "https://api.foursquare.com/v2"
_V   = "20231010"
_SIZE = "original"   # photo size: original | 1920x1440 | 960x720 | 800x600 | 300x300
_SLEEP = 0.5         # seconds between API calls


def _get(url: str) -> dict | None:
    """Make a Foursquare API GET request. Returns parsed JSON or None on error."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        code = e.code
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception:
            pass
        if code == 403:
            print(f"  403 quota/auth — stopping. Body: {body}", file=sys.stderr)
            return "QUOTA"
        if code == 404:
            return None   # check-in deleted / not found — mark as processed (no photos)
        print(f"  HTTP {code} — {body}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  Request error: {exc}", file=sys.stderr)
        return None


def _fetch_checkin_photos(checkin_id: str, token: str) -> list[tuple[str, str]] | str | None:
    """
    Returns list of (download_url, filename) for photos on this check-in,
    or "QUOTA" if quota exceeded, or None on unrecoverable error.
    Empty list = check-in exists but has no photos.
    """
    url = f"{_API}/checkins/{checkin_id}?oauth_token={token}&v={_V}"
    data = _get(url)
    if data == "QUOTA":
        return "QUOTA"
    if not data:
        return []   # 404 or error — treat as no photos

    try:
        photos = data["response"]["checkin"]["photos"]["items"]
    except (KeyError, TypeError):
        return []

    result = []
    for p in photos:
        prefix = p.get("prefix", "")
        suffix = p.get("suffix", "")
        if not prefix or not suffix:
            continue
        dl_url  = prefix + _SIZE + suffix
        filename = suffix.lstrip("/")   # e.g. "29447180_AbCdEf123.jpg"
        result.append((dl_url, filename))
    return result


def _download(url: str, dest: Path) -> bool:
    """Download url to dest. Returns True on success."""
    try:
        urllib.request.urlretrieve(url, dest)
        return True
    except Exception as exc:
        print(f"  Download failed {dest.name}: {exc}", file=sys.stderr)
        if dest.exists():
            dest.unlink()
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Foursquare check-in photos, update photos.json")
    parser.add_argument("--token",   required=True,  help="Foursquare OAuth token")
    parser.add_argument("--csv",     required=True,  help="Path to checkins.csv")
    parser.add_argument("--out",     required=True,  help="Path to photos.json (read + write)")
    parser.add_argument("--pix-dir", default=None,   help="If set, download image files here")
    parser.add_argument("--sleep",   type=float, default=_SLEEP,
                        help=f"Seconds between API calls (default {_SLEEP})")
    parser.add_argument("--limit",   type=int, default=0,
                        help="Max new check-ins to process this run (0 = unlimited)")
    parser.add_argument("--recheck-days", type=int, default=0,
                        help="Re-check already-indexed check-ins from the last N days for late-added photos")
    args = parser.parse_args()

    photos_path = Path(args.out)
    pix_dir     = Path(args.pix_dir) if args.pix_dir else None

    if pix_dir:
        pix_dir.mkdir(parents=True, exist_ok=True)

    # Load existing photos.json
    if photos_path.exists():
        photos_by_checkin: dict[str, list[str]] = json.loads(
            photos_path.read_text(encoding="utf-8")
        )
    else:
        photos_by_checkin = {}

    # Load check-in IDs from CSV (preserve insertion order → newest first after sort)
    with open(args.csv, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    # Sort newest first so new check-ins are processed first
    rows.sort(key=lambda r: int(r.get("date", 0) or 0), reverse=True)

    # Auto-detect export cutoff: max timestamp of check-ins already in photos.json
    known = set(photos_by_checkin.keys())
    cutoff_ts = max(
        (int(r.get("date", 0) or 0) for r in rows if r.get("checkin_id", "").strip() in known),
        default=0,
    )
    if cutoff_ts:
        cutoff_str = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    else:
        cutoff_str = "none"
    print(f"Export cutoff: {cutoff_ts} ({cutoff_str})", file=sys.stderr)

    # Check-ins to re-check: already indexed but within --recheck-days window
    recheck_ids: set[str] = set()
    if args.recheck_days:
        now_ts = int(datetime.now(tz=timezone.utc).timestamp())
        recheck_from = now_ts - args.recheck_days * 86400
        recheck_ids = {
            r["checkin_id"].strip() for r in rows
            if r.get("checkin_id", "").strip()
            and r["checkin_id"].strip() in known
            and int(r.get("date", 0) or 0) >= recheck_from
        }
        if recheck_ids:
            print(f"Re-check window: last {args.recheck_days} days → {len(recheck_ids)} already-indexed check-in(s)",
                  file=sys.stderr)

    # Only process check-ins newer than export cutoff and not yet indexed,
    # plus any already-indexed ones in the recheck window.
    pending = [r["checkin_id"] for r in rows
               if r.get("checkin_id", "").strip()
               and (
                   (r["checkin_id"].strip() not in known and int(r.get("date", 0) or 0) > cutoff_ts)
                   or r["checkin_id"].strip() in recheck_ids
               )]

    if args.limit:
        pending = pending[:args.limit]

    print(f"photos.json: {len(photos_by_checkin):,} check-ins already indexed, "
          f"{len(pending):,} new to check", file=sys.stderr)

    if not pending:
        print("CHANGED=false")
        return

    added_photos = 0
    added_checkins = 0
    downloaded = 0
    quota_hit = False

    for i, cid in enumerate(pending):
        result = _fetch_checkin_photos(cid, args.token)

        if result == "QUOTA":
            print(f"  Quota exceeded after {i} check-ins — stopping.", file=sys.stderr)
            quota_hit = True
            break

        photos: list[tuple[str, str]] = result or []
        filenames: list[str] = []

        for dl_url, fname in photos:
            filenames.append(fname)
            if pix_dir:
                dest = pix_dir / fname
                if not dest.exists():
                    ok = _download(dl_url, dest)
                    if ok:
                        downloaded += 1
                        print(f"  ↓ {fname}", file=sys.stderr)

        prev_filenames = photos_by_checkin.get(cid, [])
        new_count = len([f for f in filenames if f not in prev_filenames])
        photos_by_checkin[cid] = filenames
        if new_count > 0:
            note = f" (+{new_count} new)" if cid in recheck_ids else ""
            print(f"  {cid}: {len(filenames)} photo(s){note}", file=sys.stderr)
            added_photos += new_count
            added_checkins += 1

        if i % 50 == 49:
            # Save incrementally every 50 check-ins in case of interruption
            photos_path.write_text(
                json.dumps(photos_by_checkin, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8"
            )
            print(f"  … saved progress ({i+1}/{len(pending)})", file=sys.stderr)

        time.sleep(args.sleep)

    # Final save
    photos_path.write_text(
        json.dumps(photos_by_checkin, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8"
    )

    processed = min(i + 1 if not quota_hit else i, len(pending))
    print(
        f"Done: checked {processed:,} check-ins, "
        f"found photos on {added_checkins:,}, "
        f"{added_photos:,} total photos"
        + (f", {downloaded:,} downloaded" if pix_dir else ""),
        file=sys.stderr
    )

    # Emit GitHub Actions output
    changed = added_photos > 0
    print(f"CHANGED={'true' if changed else 'false'}")
    if changed:
        print(f"NEW_PHOTOS={added_photos}")


if __name__ == "__main__":
    main()
