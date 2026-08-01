# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
add_backfill_photo.py — Attach local image files to reconstructed ("refurbished")
check-ins: downscale, upload to R2 under /pix, and record them in photos.json.

Why a separate tool: add_photo.py POSTs to Foursquare /v2/photos/add and is keyed
by a REAL 24-char check-in id, so it cannot serve `rf*` backfill rows (they have no
Foursquare check-in). The render path needs only three things, none of which involve
the Foursquare API:

  1. the object exists in R2 under the `pix/` prefix,
  2. photos.json maps the check-in id -> [filename, ...],
  3. a row with that check-in id is in the build input (checkins.csv / backfill.csv).

Nothing validates the filename shape — it is concatenated onto --pix-url — so
reconstructed photos get their own prefix (`backfill_`) and a content hash, which
makes re-runs idempotent (same bytes -> same key -> already in R2 -> skipped).

Local image files are NEVER committed: `pix/` is gitignored in the data repo and the
bytes live only in R2. This script is the local equivalent of what CI does with
fetch_photos.py --pix-dir + `aws s3 sync private-data/pix/ s3://$BUCKET/pix/`.

Credentials come from the environment only (never a command line):
  R2_ACCESS_KEY_ID / AWS_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY / AWS_SECRET_ACCESS_KEY
  R2_ENDPOINT  (or R2_ACCOUNT_ID -> https://<id>.r2.cloudflarestorage.com)
  R2_BUCKET_NAME  (default: foursquare-photos)

Usage:
  # one check-in, explicit files (order = render order)
  python scripts/add_backfill_photo.py --checkin-id rf0201 \\
      --photos private-data/photos.json \\
      --photo "C:/.../CIMG4672.JPG" --photo "C:/.../CIMG4704.JPG"

  # one check-in, a whole folder (sorted by EXIF capture time)
  python scripts/add_backfill_photo.py --checkin-id rf0201 --dir "C:/.../concert" \\
      --photos private-data/photos.json

  # many check-ins at once: CSV with header  checkin_id,photo_path
  python scripts/add_backfill_photo.py --batch map.csv --photos private-data/photos.json

  # propose a mapping to review by hand, then feed the edited file back via --batch
  python scripts/add_backfill_photo.py --suggest map.csv --dir "C:/.../photos" \\
      --backfill .../backfill.csv --photos private-data/photos.json

  # preview only
  python scripts/add_backfill_photo.py --checkin-id rf0201 --dir ... --dry-run
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

DEFAULT_BUCKET = "foursquare-photos"
R2_PREFIX = "pix/"
EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".bmp", ".tif", ".tiff"}
EXIF_DATETIME = 36867          # DateTimeOriginal — lives in the Exif SubIFD, not IFD0
EXIF_IFD = 0x8769              # pointer to that SubIFD
EXIF_DATETIME_FALLBACK = 306   # DateTime (file/edit time) in IFD0 — last resort
CACHE_CONTROL = "public, max-age=31536000, immutable"


# --------------------------------------------------------------------- images
def exif_ts(path: Path) -> str:
    """DateTimeOriginal as 'YYYY:MM:DD HH:MM:SS', or ''.

    `getexif()` only returns IFD0; DateTimeOriginal sits in the Exif SubIFD behind
    tag 0x8769, so it has to be opened explicitly — reading IFD0 alone silently
    returns '' for every camera original.
    """
    try:
        from PIL import Image
        with Image.open(path) as im:
            ex = im.getexif()
            val = ex.get_ifd(EXIF_IFD).get(EXIF_DATETIME) or ex.get(EXIF_DATETIME_FALLBACK)
            return str(val or "").strip().rstrip("\x00")
    except Exception:
        return ""


def process(path: Path, max_size: int, quality: int) -> bytes:
    """Downscale + re-encode to a web-sized JPEG (EXIF stripped, orientation applied).

    Matches the size class of the Foursquare-sourced objects already in R2
    (~120-420 KB); untouched camera originals here are 0.6-4.2 MB.
    """
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if max(im.size) > max_size:
            im.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def filename_for(checkin_id: str, blob: bytes, prefix: str) -> str:
    """Deterministic content-addressed name: re-running with the same image is a no-op."""
    return f"{prefix}_{checkin_id}_{hashlib.sha1(blob).hexdigest()[:8]}.jpg"


# ------------------------------------------------------------------------ R2
def r2_client(endpoint: str):
    import boto3
    from botocore.config import Config
    key = os.environ.get("R2_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID", "")
    sec = os.environ.get("R2_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    if not key or not sec:
        log.error("Missing R2 credentials: set R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY.")
        raise SystemExit(2)
    return boto3.client(
        "s3", endpoint_url=endpoint, region_name="auto",
        aws_access_key_id=key, aws_secret_access_key=sec,
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    )


def resolve_endpoint(cli: str) -> str:
    ep = (cli or os.environ.get("R2_ENDPOINT", "")).strip()
    if ep:
        return ep
    acct = os.environ.get("R2_ACCOUNT_ID", "").strip()
    if acct:
        return f"https://{acct}.r2.cloudflarestorage.com"
    log.error("Missing R2 endpoint: set R2_ENDPOINT or R2_ACCOUNT_ID (or pass --endpoint).")
    raise SystemExit(2)


def exists_in_r2(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------- inputs
def input_images(args: argparse.Namespace) -> list[Path]:
    """--photo / --dir, ordered by EXIF capture time (filename as tiebreak)."""
    files: list[Path] = [Path(p) for p in (args.photo or [])]
    if args.dir:
        found = [p for p in Path(args.dir).iterdir()
                 if p.is_file() and p.suffix.lower() in EXT]
        found.sort(key=lambda p: (exif_ts(p) or "9999", p.name.lower()))
        files.extend(found)
    return files


def collect_jobs(args: argparse.Namespace) -> list[tuple[str, Path]]:
    """-> [(checkin_id, image path)] in render order."""
    jobs: list[tuple[str, Path]] = []
    if args.batch:
        with open(args.batch, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                cid = (r.get("checkin_id") or "").strip()
                p = (r.get("photo_path") or "").strip()
                if cid and p:
                    jobs.append((cid, Path(p)))
        return jobs

    cid = (args.checkin_id or "").strip()
    if not cid:
        log.error("Pass --checkin-id with --photo/--dir, or use --batch.")
        raise SystemExit(2)
    files = input_images(args)
    if not files:
        log.error("No input images (--photo / --dir matched nothing).")
        raise SystemExit(2)
    return [(cid, f) for f in files]


def known_checkin_ids(paths: list[str]) -> set[str]:
    """Check-in ids present in the given CSVs — used to warn about orphan keys."""
    ids: set[str] = set()
    for p in paths:
        f = Path(p)
        if not f.exists():
            continue
        with open(f, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                cid = (r.get("checkin_id") or "").strip()
                if cid:
                    ids.add(cid)
    return ids


# -------------------------------------------------------------------- suggest
# Backfill rows are stored as unix ts read as UTC, while EXIF DateTimeOriginal is
# camera-local wall clock with no offset — so a row has to be rendered in ITS OWN
# local time before the two can be compared. metrics.trips._COUNTRY_TZ is not reused:
# it has no Russia key (20 backfill rows) and importing it warns about timezonefinder.
COUNTRY_TZ = {
    "Moldova": "Europe/Chisinau", "Belarus": "Europe/Minsk", "Ukraine": "Europe/Kyiv",
    "Russia": "Europe/Moscow", "Romania": "Europe/Bucharest", "Poland": "Europe/Warsaw",
    "Lithuania": "Europe/Vilnius", "Latvia": "Europe/Riga", "Estonia": "Europe/Tallinn",
    "Turkey": "Europe/Istanbul", "Bulgaria": "Europe/Sofia", "Hungary": "Europe/Budapest",
    "Germany": "Europe/Berlin", "Czechia": "Europe/Prague", "Austria": "Europe/Vienna",
    "Italy": "Europe/Rome", "France": "Europe/Paris", "Spain": "Europe/Madrid",
    "Greece": "Europe/Athens", "Georgia": "Asia/Tbilisi", "Egypt": "Africa/Cairo",
}


def row_tz(country: str, lng: str) -> tzinfo:
    """Timezone of a backfill row: country table, else a longitude guess. Never raises."""
    name = COUNTRY_TZ.get((country or "").strip())
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass
    try:
        return timezone(timedelta(hours=round(float(lng) / 15)))
    except (TypeError, ValueError):
        return timezone.utc


def photo_taken(path: Path) -> datetime | None:
    """EXIF DateTimeOriginal as a naive (camera-local) datetime."""
    raw = exif_ts(path)
    try:
        return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def load_rows(paths: list[str]) -> list[dict[str, str]]:
    """Rows from the given CSVs, each with a naive row-local `_local` datetime added."""
    out: list[dict[str, str]] = []
    for p in paths:
        f = Path(p)
        if not f.exists():
            log.warning("not found, skipped: %s", f)
            continue
        with open(f, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                if not (r.get("checkin_id") or "").strip():
                    continue
                try:
                    ts = int(r["date"])
                except (KeyError, ValueError):
                    continue
                tz = row_tz(r.get("country", ""), r.get("lng", ""))
                r["_local"] = datetime.fromtimestamp(ts, tz).replace(tzinfo=None).isoformat(" ")
                out.append(r)
    return out


def attached_hashes(index: dict[str, list[str]], prefix: str) -> dict[str, str]:
    """sha1[:8] -> checkin_id for photos already attached by this script."""
    pat = re.compile(rf"^{re.escape(prefix)}_(.+)_([0-9a-f]{{8}})\.jpg$")
    seen: dict[str, str] = {}
    for cid, names in index.items():
        for n in names:
            m = pat.match(n)
            if m:
                seen[m.group(2)] = cid
    return seen


SUGGEST_COLS = ["checkin_id", "photo_path", "photo_time", "checkin_time", "venue",
                "city", "gap_h", "day_delta", "confidence", "alternatives"]


def suggest(args: argparse.Namespace) -> None:
    """Propose a checkin_id,photo_path mapping by EXIF day — for human review, then --batch."""
    files = input_images(args)
    if not files:
        log.error("No input images (--photo / --dir matched nothing).")
        raise SystemExit(2)

    sources = [p for p in (args.backfill, args.csv) if p]
    if not sources:
        log.error("--suggest needs --backfill (and optionally --csv) to match against.")
        raise SystemExit(2)
    rows = load_rows(sources)
    log.info("%d candidate rows from %s", len(rows), ", ".join(sources))

    # Already-attached detection: re-encode each input and look up its content hash.
    index: dict[str, list[str]] = {}
    photos_path = Path(args.photos)
    if photos_path.exists():
        with open(photos_path, encoding="utf-8") as fh:
            index = json.load(fh)
    known = attached_hashes(index, args.prefix)

    window = timedelta(hours=args.window_hours)
    out: list[dict[str, str]] = []
    for src in files:
        rec = {c: "" for c in SUGGEST_COLS}
        rec["photo_path"] = str(src)

        taken = photo_taken(src)
        rec["photo_time"] = taken.isoformat(" ") if taken else ""

        if known:
            try:
                blob = process(src, args.max_size, args.quality)
                h = hashlib.sha1(blob).hexdigest()[:8]
            except Exception as exc:
                log.warning("cannot process %s: %s", src.name, exc)
                h = ""
            if h and h in known:
                rec["confidence"] = f"already:{known[h]}"
                out.append(rec)
                continue

        if not taken:
            rec["confidence"] = "no-exif"
            out.append(rec)
            continue

        # Row hours are synthetic placeholders, so the clock only RANKS same-day
        # candidates — the window is day-sized on purpose (rf0201's own photos sit
        # 19-21 h from its row time; a ±12 h window would match none of them).
        cands = []
        for r in rows:
            rt = datetime.fromisoformat(r["_local"])
            gap = rt - taken
            if abs(gap) <= window:
                cands.append((abs(gap), gap, r, rt))
        cands.sort(key=lambda c: (c[0], c[2]["checkin_id"]))

        if not cands:
            rec["confidence"] = "no-row"
            out.append(rec)
            continue

        _, gap, r, rt = cands[0]
        rec["checkin_id"] = r["checkin_id"]
        rec["checkin_time"] = rt.isoformat(" ")
        rec["venue"] = r.get("venue", "")
        rec["city"] = r.get("city", "")
        rec["gap_h"] = f"{gap.total_seconds() / 3600:+.1f}"
        rec["day_delta"] = str((rt.date() - taken.date()).days)
        rec["confidence"] = "unique" if len(cands) == 1 else f"pick-1-of-{len(cands)}"
        rec["alternatives"] = " | ".join(
            f"{c[2]['checkin_id']} {c[3].strftime('%H:%M')} {c[2].get('venue', '')}"
            for c in cands[1:6])
        out.append(rec)

    out.sort(key=lambda r: (r["photo_time"] or "9999", r["photo_path"]))
    dest = Path(args.suggest)
    with open(dest, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUGGEST_COLS)
        w.writeheader()
        w.writerows(out)

    picked = sum(1 for r in out if r["checkin_id"])
    tally: dict[str, int] = {}
    for r in out:
        tally[r["confidence"].split(":")[0]] = tally.get(r["confidence"].split(":")[0], 0) + 1
    log.info("Wrote %s — %d/%d photos matched (%s).", dest, picked, len(out),
             ", ".join(f"{k} {v}" for k, v in sorted(tally.items())))
    log.info("Review it by hand (blank checkin_id = skipped), then: --batch %s", dest)


# ----------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="Attach local photos to backfill check-ins (R2 + photos.json)")
    ap.add_argument("--checkin-id", default="", help="Target check-in id (e.g. rf0201)")
    ap.add_argument("--photo", action="append", help="Image file (repeatable; order preserved)")
    ap.add_argument("--dir", default="", help="Folder of images (sorted by EXIF capture time)")
    ap.add_argument("--batch", default="", help="CSV with header: checkin_id,photo_path")
    ap.add_argument("--suggest", default="", help="Write a candidate checkin_id,photo_path CSV here (no upload)")
    ap.add_argument("--window-hours", type=float, default=36.0,
                    help="--suggest: how far a row may sit from the photo (default: 36 — row hours are synthetic)")
    ap.add_argument("--photos", default="private-data/photos.json", help="Path to photos.json")
    ap.add_argument("--csv", default="", help="checkins.csv — validates the id exists (optional)")
    ap.add_argument("--backfill", default="", help="backfill.csv — validates the id exists (optional)")
    ap.add_argument("--pix-dir", default="", help="Also keep the processed JPEG here (optional)")
    ap.add_argument("--prefix", default="backfill", help="Filename prefix (default: backfill)")
    ap.add_argument("--bucket", default=os.environ.get("R2_BUCKET_NAME") or DEFAULT_BUCKET)
    ap.add_argument("--endpoint", default="", help="R2 S3 endpoint (else $R2_ENDPOINT / $R2_ACCOUNT_ID)")
    ap.add_argument("--max-size", type=int, default=1600, help="Longest edge in px (default: 1600)")
    ap.add_argument("--quality", type=int, default=82, help="JPEG quality (default: 82)")
    ap.add_argument("--replace", action="store_true",
                    help="Replace this check-in's photo list instead of appending")
    ap.add_argument("--no-upload", action="store_true", help="Skip R2, only update photos.json")
    ap.add_argument("--dry-run", action="store_true", help="Show what would happen, write nothing")
    args = ap.parse_args()

    if args.suggest:
        suggest(args)
        return

    jobs = collect_jobs(args)

    photos_path = Path(args.photos)
    if not photos_path.exists():
        log.error("photos.json not found: %s", photos_path)
        raise SystemExit(2)
    with open(photos_path, encoding="utf-8") as fh:
        index: dict[str, list[str]] = json.load(fh)

    # Orphan guard: gen_photos.py silently drops photos.json keys with no matching row.
    validate_against = [p for p in (args.csv, args.backfill) if p]
    if validate_against:
        known = known_checkin_ids(validate_against)
        for cid in {c for c, _ in jobs}:
            if cid not in known:
                log.warning("check-in %s is in no input CSV — its photos would be dropped at build.", cid)

    client = None
    if not (args.no_upload or args.dry_run):
        client = r2_client(resolve_endpoint(args.endpoint))

    pix_dir = Path(args.pix_dir) if args.pix_dir else None
    if pix_dir and not args.dry_run:
        pix_dir.mkdir(parents=True, exist_ok=True)

    if args.replace:
        for cid in {c for c, _ in jobs}:
            index[cid] = []

    uploaded, skipped, added = 0, 0, 0
    written: list[str] = []
    for cid, src in jobs:
        if not src.exists():
            log.warning("%s: missing file %s — skipped.", cid, src)
            continue
        try:
            blob = process(src, args.max_size, args.quality)
        except Exception as exc:
            log.warning("%s: cannot process %s: %s", cid, src.name, exc)
            continue
        name = filename_for(cid, blob, args.prefix)
        key = R2_PREFIX + name
        log.info("%s  %s  %.1f MB -> %.0f KB  %s",
                 cid, src.name, src.stat().st_size / 1e6, len(blob) / 1024, name)

        if args.dry_run:
            pass
        elif client is not None:
            if exists_in_r2(client, args.bucket, key):
                skipped += 1
            else:
                client.put_object(Bucket=args.bucket, Key=key, Body=blob,
                                  ContentType="image/jpeg", CacheControl=CACHE_CONTROL)
                uploaded += 1
        if pix_dir and not args.dry_run:
            (pix_dir / name).write_bytes(blob)

        lst = index.setdefault(cid, [])
        if name not in lst:
            lst.append(name)
            added += 1
        written.append(name)

    log.info("R2: %d uploaded, %d already present. photos.json: %d new entr%s.",
             uploaded, skipped, added, "y" if added == 1 else "ies")

    if args.dry_run:
        log.info("Dry run — nothing written.")
    elif added:
        tmp = photos_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, photos_path)
        log.info("Saved %s (%d check-ins indexed).", photos_path, len(index))
    else:
        log.info("photos.json unchanged.")

    print(f"CHANGED={'true' if added and not args.dry_run else 'false'}")
    print(f"PHOTO_FILES={','.join(written)}")


if __name__ == "__main__":
    main()
