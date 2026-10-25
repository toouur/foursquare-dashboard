# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
update_device_names.py — refresh config/device_names.json from Google Play's
public "supported devices" list (model code → retail brand + marketing name).

The visit counter only gets the raw Android model code from client hints
(e.g. ELI-NX9, SM-S928B, 23090RA98G); visits.html and the Telegram report turn
it into "HONOR 200", "Samsung Galaxy S24 Ultra", "Redmi Note 13 Pro+ 5G".
Run by hand now and then (new phones appear every month); unknown codes are
simply shown raw.

    python scripts/update_device_names.py [--csv local.csv] [--out config/device_names.json]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
from collections import Counter
from pathlib import Path

import requests

log = logging.getLogger(__name__)

PLAY_CSV = "https://storage.googleapis.com/play_public/supported_devices.csv"
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "config" / "device_names.json"


def display_name(brand: str, marketing: str) -> str:
    """'Samsung' + 'Galaxy S24 Ultra' → 'Samsung Galaxy S24 Ultra'; 'Honor' + 'HONOR 200' → 'HONOR 200'."""
    brand, marketing = brand.strip(), marketing.strip()
    if not brand or marketing.casefold().startswith(brand.casefold()):
        return marketing
    return f"{brand} {marketing}"


def build_map(text: str) -> dict[str, str]:
    """{model code: name}. A code listed under several names keeps the most frequent one
    (ties: alphabetical); rows without a marketing name, or where it is just the code, are skipped."""
    names: dict[str, Counter] = {}
    for row in csv.DictReader(io.StringIO(text)):
        model = (row.get("Model") or "").strip()
        marketing = (row.get("Marketing Name") or "").strip()
        if not model or not marketing or marketing == model:
            continue
        names.setdefault(model, Counter())[display_name(row.get("Retail Branding") or "", marketing)] += 1
    return {m: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] for m, c in sorted(names.items())}


def decode(raw: bytes) -> str:
    # Google ships it as UTF-16 with a BOM; accept UTF-8 too for a hand-made copy
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--csv", help="local copy of supported_devices.csv instead of downloading it")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    if args.csv:
        raw = Path(args.csv).read_bytes()
    else:
        resp = requests.get(PLAY_CSV, timeout=60)
        resp.raise_for_status()
        raw = resp.content
    mapping = build_map(decode(raw))
    if len(mapping) < 1000:   # a truncated or changed download must not wipe the table
        log.error("only %d models parsed — refusing to overwrite %s", len(mapping), args.out)
        return 1
    Path(args.out).write_text(json.dumps(mapping, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
                              encoding="utf-8")
    log.info("%d model codes → %s", len(mapping), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
