# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

#!/usr/bin/env python3
"""Make the small copies the pages show in grids, tiles and covers.

Every page used to load the original photo (0.5–2.4 MB) for a 200 px tile —
photos.html alone pulled ~40 MB on a phone. A thumb is the same file name under
pix/t/ in R2, at most THUMB_PX on its long edge; the lightbox still opens the
original. The pages fall back to the original while a thumb does not exist yet
(see the head snippet build.py injects), so thumbs can lag behind uploads.

Two modes:

  # thumbs for the photos fetched in this run (pix/ is CI-only)
  python scripts/make_thumbs.py --src-dir private-data/pix --out-dir private-data/pix/t

  # backlog: originals that have no thumb in R2 yet, read from two `aws s3 ls` listings
  python scripts/make_thumbs.py --originals pix.txt --thumbs t.txt \
      --pix-url https://pub-….r2.dev/pix --out-dir out/t --max 6000

Prints THUMBS_MADE=<n> for the workflow.
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

THUMB_PX = 640
QUALITY = 72
EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def thumb_url(src: str) -> str:
    """The pix/t/ copy of a pix/ URL (same rule as pixT() in the pages); anything else unchanged."""
    if "/pix/" in src and "/pix/t/" not in src:
        return src.replace("/pix/", "/pix/t/", 1)
    return src


def make_thumb(data: bytes) -> bytes:
    """JPEG at most THUMB_PX on the long edge, EXIF rotation applied."""
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail((THUMB_PX, THUMB_PX), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=QUALITY, optimize=True, progressive=True)
        return out.getvalue()


def listed_keys(path: str) -> set[str]:
    """File names from an `aws s3 ls` listing (last column), sub-folders skipped."""
    keys = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if not parts or parts[0] == "PRE":
            continue
        name = parts[-1]
        if "/" not in name and Path(name).suffix.lower() in EXTS:
            keys.add(name)
    return keys


def missing_keys(originals: set[str], thumbs: set[str]) -> list[str]:
    """Originals without a thumb, sorted so a capped run always picks the same batch."""
    return sorted(originals - thumbs)


def from_dir(src: Path, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(src.iterdir()):
        if not f.is_file() or f.suffix.lower() not in EXTS:
            continue
        dst = out / f.name
        if dst.exists():
            continue
        try:
            dst.write_bytes(make_thumb(f.read_bytes()))
            n += 1
        except Exception as e:   # a broken image must not stop the run
            print(f"  skip {f.name}: {e}", file=sys.stderr)
    return n


def from_r2(keys: list[str], pix_url: str, out: Path, workers: int = 8) -> int:
    import requests
    out.mkdir(parents=True, exist_ok=True)
    base = pix_url.rstrip("/")
    session = requests.Session()

    def one(key: str) -> bool:
        for attempt in range(3):
            try:
                r = session.get(f"{base}/{key}", timeout=60)
                if r.status_code == 404:
                    return False
                r.raise_for_status()
                (out / key).write_bytes(make_thumb(r.content))
                return True
            except Exception as e:
                if attempt == 2:
                    print(f"  skip {key}: {e}", file=sys.stderr)
                time.sleep(2 * (attempt + 1))
        return False

    with ThreadPoolExecutor(workers) as ex:
        return sum(ex.map(one, keys))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--src-dir", help="make thumbs for every image in this folder")
    ap.add_argument("--originals", help="`aws s3 ls` listing of pix/")
    ap.add_argument("--thumbs", help="`aws s3 ls` listing of pix/t/")
    ap.add_argument("--pix-url", help="public base URL of pix/ (originals are downloaded from it)")
    ap.add_argument("--max", type=int, default=6000, help="backlog: at most this many thumbs per run")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    out = Path(a.out_dir)
    if a.src_dir:
        src = Path(a.src_dir)
        n = from_dir(src, out) if src.is_dir() else 0
    elif a.originals and a.thumbs and a.pix_url:
        todo = missing_keys(listed_keys(a.originals), listed_keys(a.thumbs))
        print(f"thumbs missing: {len(todo)} — making {min(len(todo), a.max)} this run", file=sys.stderr)
        n = from_r2(todo[:a.max], a.pix_url, out, a.workers)
    else:
        ap.error("give --src-dir, or --originals + --thumbs + --pix-url")
    print(f"THUMBS_MADE={n}")


if __name__ == "__main__":
    main()
