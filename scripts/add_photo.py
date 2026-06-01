# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
add_photo.py — Upload a photo to an existing Foursquare check-in.

Use this to back-fill old check-ins that never had a photo at the time,
so the year-page month timeline can show its OWN photo instead of
borrowing from a neighbouring month.

The script POSTs to /v2/photos/add with the check-in id; on success it
also patches photos.json locally so the next dashboard build picks up
the new photo immediately (the next CI fetch will re-confirm it from
the API).  Pass --pix-dir to also copy the source file into the local
pix/ directory under the filename Foursquare assigned, so a local
rebuild (no R2 round-trip) finds the image.

Usage:
  # Single check-in, single photo:
  python scripts/add_photo.py \\
      --token "$FOURSQUARE_TOKEN" \\
      --checkin-id 5f8a... \\
      --photo path/to/img.jpg \\
      --photos C:/.../foursquare-data/photos.json \\
      --pix-dir C:/.../foursquare-data/pix/

  # Batch: read pairs of (checkin_id, photo_path) from CSV (one per line):
  python scripts/add_photo.py \\
      --token "$FOURSQUARE_TOKEN" \\
      --batch backfill.csv \\
      --photos C:/.../foursquare-data/photos.json \\
      --pix-dir C:/.../foursquare-data/pix/

  # Dry run (no upload, just validate):
  python scripts/add_photo.py --checkin-id ... --photo ... --dry-run

The Foursquare API requires the check-in to be yours (the token's owner
must be the author of the check-in).  Photos may take a few minutes to
appear in the public check-in feed after upload.
"""
from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_API = "https://api.foursquare.com/v2"
_V   = "20231010"


def _build_multipart(photo_path: Path) -> tuple[bytes, str]:
    """Build a multipart/form-data body containing a single `photo` part.
    Returns (body_bytes, content_type)."""
    boundary = f"----add-photo-{int(time.time() * 1000)}"
    mime, _ = mimetypes.guess_type(str(photo_path))
    mime = mime or "image/jpeg"
    file_bytes = photo_path.read_bytes()
    lines = [
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="photo"; filename="{photo_path.name}"'.encode(),
        f"Content-Type: {mime}".encode(),
        b"",
        file_bytes,
        f"--{boundary}--".encode(),
        b"",
    ]
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


def add_photo(token: str, checkin_id: str, photo_path: Path) -> dict:
    """Upload `photo_path` to Foursquare attached to `checkin_id`.
    Returns the parsed `photo` object from the API response."""
    url = f"{_API}/photos/add?oauth_token={token}&v={_V}&checkinId={checkin_id}"
    body, ctype = _build_multipart(photo_path)
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": ctype,
            "Content-Length": str(len(body)),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode()[:400]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} from /photos/add: {body_text}") from e

    photo = data.get("response", {}).get("photo")
    if not photo:
        meta = data.get("meta", {})
        raise RuntimeError(f"Foursquare did not return a photo object: {meta}")
    return photo


def _patch_photos_json(photos_path: Path, checkin_id: str, suffix: str) -> None:
    """Append the new photo filename to photos.json under checkin_id."""
    if not photos_path.exists():
        photos_by_checkin: dict[str, list[str]] = {}
    else:
        photos_by_checkin = json.loads(photos_path.read_text(encoding="utf-8"))
    filename = suffix.lstrip("/")
    arr = photos_by_checkin.setdefault(checkin_id, [])
    if filename not in arr:
        arr.append(filename)
    photos_path.write_text(
        json.dumps(photos_by_checkin, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _copy_to_pix(photo_path: Path, pix_dir: Path, suffix: str) -> None:
    """Copy the local source image into pix/ under the Foursquare-assigned
    filename, so a local rebuild sees the new photo immediately."""
    pix_dir.mkdir(parents=True, exist_ok=True)
    target = pix_dir / suffix.lstrip("/")
    if not target.exists():
        shutil.copy2(photo_path, target)


def _process_one(
    token: str, checkin_id: str, photo_path: Path,
    photos_path: Path | None, pix_dir: Path | None, dry_run: bool,
) -> None:
    print(f"  → {checkin_id}  ←  {photo_path}")
    if dry_run:
        print("    (dry-run, skipping upload)")
        return
    photo = add_photo(token, checkin_id, photo_path)
    suffix = photo.get("suffix", "")
    photo_id = photo.get("id", "")
    if not suffix:
        print("    WARN: no suffix in response — cannot patch photos.json")
        return
    print(f"    uploaded id={photo_id} suffix={suffix}")
    if photos_path:
        _patch_photos_json(photos_path, checkin_id, suffix)
        print(f"    patched {photos_path.name}")
    if pix_dir:
        _copy_to_pix(photo_path, pix_dir, suffix)
        print(f"    copied to {pix_dir / suffix.lstrip('/')}")


def main() -> None:
    p = argparse.ArgumentParser(description="Upload a photo to an existing Foursquare check-in.")
    p.add_argument("--token", help="Foursquare OAuth token")
    p.add_argument("--checkin-id", help="Single check-in id")
    p.add_argument("--photo", help="Single image path")
    p.add_argument("--batch", help="CSV with header 'checkin_id,photo_path'")
    p.add_argument("--photos", help="Path to photos.json to patch on success")
    p.add_argument("--pix-dir", help="Local pix/ directory to mirror the upload into")
    p.add_argument("--sleep", type=float, default=0.8,
                   help="Seconds between uploads in batch mode (default 0.8)")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate inputs but don't upload")
    args = p.parse_args()

    if not args.dry_run and not args.token:
        p.error("--token is required unless --dry-run")
    photos_path = Path(args.photos) if args.photos else None
    pix_dir = Path(args.pix_dir) if args.pix_dir else None

    jobs: list[tuple[str, Path]] = []
    if args.batch:
        with open(args.batch, encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                cid = (row.get("checkin_id") or "").strip()
                pth = (row.get("photo_path") or "").strip()
                if not cid or not pth:
                    continue
                p_path = Path(pth)
                if not p_path.is_file():
                    print(f"  SKIP: {pth} not found (checkin {cid})", file=sys.stderr)
                    continue
                jobs.append((cid, p_path))
    elif args.checkin_id and args.photo:
        p_path = Path(args.photo)
        if not p_path.is_file():
            p.error(f"--photo not found: {args.photo}")
        jobs.append((args.checkin_id, p_path))
    else:
        p.error("Provide either --batch or both --checkin-id and --photo")

    print(f"Uploading {len(jobs)} photo(s){' [DRY RUN]' if args.dry_run else ''}…")
    success = 0
    for i, (cid, ppath) in enumerate(jobs):
        try:
            _process_one(args.token or "", cid, ppath, photos_path, pix_dir, args.dry_run)
            success += 1
        except Exception as exc:
            print(f"    ERROR: {exc}", file=sys.stderr)
        if i < len(jobs) - 1 and args.sleep > 0 and not args.dry_run:
            time.sleep(args.sleep)
    print(f"Done — {success}/{len(jobs)} succeeded")


if __name__ == "__main__":
    main()
