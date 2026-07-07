"""
gen_city_review.py  —  Generate a city-assignment review CSV for manual decisions.

Scans all check-ins (after applying city_fixes + city_merge transforms), flags
candidates with potentially wrong city values, and writes a CSV with enough
context to make a decision without opening any other file.

Already-fixed entries (present in city_fixes.json) are excluded.

Usage:
    python scripts/gen_city_review.py \
        --csv  C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
        --out  city_review.csv

Output columns:
    flag_reasons      — comma-separated list of why this row was flagged
    timestamp         — unix timestamp (key for city_fixes.json)
    date              — human-readable UTC date
    venue             — venue name
    category          — Foursquare category
    city_raw          — city as it appears in the CSV
    city_final        — city after city_merge + city_fixes transforms
    country           — country
    lat, lng          — coordinates
    prev3             — 3 preceding check-ins: "date | venue | city_final | country"
    next3             — 3 following check-ins: same format
    suggested_city    — (empty — fill in during review)
    notes             — (empty — fill in during review)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


# ── Keyword patterns ───────────────────────────────────────────────────────────

BORDER_PATTERNS = re.compile(
    # Russian / Cyrillic border terms
    r"граница|пограничн|таможн|КПП|пункт пропуска|пункт пограничного|"
    # English border terms (multi-word or unambiguous)
    r"border crossing|border control|border post|border check|border point|"
    r"checkpoint|customs|immigration|"
    # Other languages
    r"zona de control|vamal|douane|zoll|mытница|granit|"
    r"entry point|frontier post",
    re.IGNORECASE,
)

WATER_CATEGORY_PATTERNS = re.compile(
    r"\bsea\b|\bocean\b|\bgulf\b|\bbay\b|\bfjord\b|"
    r"\blake\b|\breservoir\b|\briver\b|\bcanal\b|"
    r"море|озеро|залив|река|водохранилище|"
    r"beach|пляж",
    re.IGNORECASE,
)

REGION_CITY_PATTERNS = re.compile(
    r"\bregion\b|\boblast\b|\bкрай\b|\bобласть\b|\bрегион\b|"
    r"\brayon\b|\braion\b|\bdistrict\b|\bprovince\b|\bprefecture\b|"
    r"\bcounty\b|\bmunicipality\b",
    re.IGNORECASE,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _str(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _ts_to_human(ts: str) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts


def _apply_city_merge(city_raw: str, city_merge: dict) -> str:
    if not city_raw:
        return city_raw
    norm = city_raw.replace("’", "'").replace("‘", "'")
    return city_merge.get(norm, city_merge.get(city_raw, city_raw))


def _context_line(row: dict) -> str:
    parts = [
        _ts_to_human(row["date"]),
        row["venue"] or "",
        row["city_final"] or "(blank)",
        row["country"] or "",
    ]
    return " | ".join(parts)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv",        required=True, help="Path to checkins.csv")
    ap.add_argument("--config-dir", default="config", help="Config directory (default: config)")
    ap.add_argument("--out",        default="city_review.csv", help="Output CSV path")
    ap.add_argument("--context",    type=int, default=3, help="Context rows before/after (default: 3)")
    args = ap.parse_args()

    csv_path    = Path(args.csv)
    config_dir  = Path(args.config_dir)
    out_path    = Path(args.out)
    ctx_n       = args.context

    if not csv_path.exists():
        sys.exit(f"CSV not found: {csv_path}")

    # ── Load config ────────────────────────────────────────────────────────────
    city_fixes_path = config_dir / "city_fixes.json"
    city_merge_path = config_dir / "city_merge.yaml"

    city_fixes: dict[str, str] = {}
    if city_fixes_path.exists():
        raw = json.loads(city_fixes_path.read_text(encoding="utf-8"))
        city_fixes = {k: v for k, v in raw.items() if not k.startswith("_comment")}

    city_merge: dict[str, str] = {}
    if city_merge_path.exists():
        city_merge = yaml.safe_load(city_merge_path.read_text(encoding="utf-8")) or {}

    print(f"Loaded {len(city_fixes)} city_fixes, {len(city_merge)} city_merge entries", flush=True)

    # ── Load + transform all rows ──────────────────────────────────────────────
    rows: list[dict] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for raw_row in csv.DictReader(fh):
            ts      = _str(raw_row.get("date")) or ""
            city_r  = _str(raw_row.get("city")) or ""
            country = _str(raw_row.get("country")) or ""
            venue   = _str(raw_row.get("venue")) or ""
            cat     = _str(raw_row.get("category")) or ""

            # Apply city_fixes (highest priority)
            if ts in city_fixes:
                city_final = city_fixes[ts]
                fixed = True
            else:
                city_final = _apply_city_merge(city_r, city_merge)
                fixed = False

            rows.append({
                "date":       ts,
                "date_human": _ts_to_human(ts),
                "venue":      venue,
                "venue_id":   _str(raw_row.get("venue_id")) or "",
                "category":   cat,
                "city_raw":   city_r,
                "city_final": city_final,
                "country":    country,
                "lat":        _str(raw_row.get("lat")) or "",
                "lng":        _str(raw_row.get("lng")) or "",
                "checkin_id": _str(raw_row.get("checkin_id")) or "",
                "already_fixed": fixed,
            })

    print(f"Loaded {len(rows):,} rows", flush=True)

    # ── Flag candidates ────────────────────────────────────────────────────────
    flagged: list[tuple[int, list[str]]] = []  # (index, [reasons])

    for i, row in enumerate(rows):
        if row["already_fixed"]:
            continue

        reasons: list[str] = []

        # 1. Border / crossing venue name
        if BORDER_PATTERNS.search(row["venue"]):
            reasons.append("border_venue")

        # 2. Water category (cross-border water pins)
        if WATER_CATEGORY_PATTERNS.search(row["category"]):
            # Only flag if country differs from immediate neighbours
            prev_country = rows[i - 1]["country"] if i > 0 else None
            next_country = rows[i + 1]["country"] if i < len(rows) - 1 else None
            if row["country"] != prev_country or row["country"] != next_country:
                reasons.append("water_cross_border")

        # 3. Region-level city string (oblast, region, etc.)
        if row["city_final"] and REGION_CITY_PATTERNS.search(row["city_final"]):
            reasons.append("region_city")

        # 4. Blank city
        if not row["city_final"]:
            reasons.append("blank_city")

        # 5. Country surrounded by a different country (transit / border)
        if i > 0 and i < len(rows) - 1:
            prev_c = rows[i - 1]["country"]
            next_c = rows[i + 1]["country"]
            if prev_c and next_c and prev_c == next_c and prev_c != row["country"]:
                reasons.append("country_island")

        if reasons:
            flagged.append((i, reasons))

    print(f"Flagged {len(flagged):,} candidates (after excluding already-fixed rows)", flush=True)

    # ── Write review CSV ───────────────────────────────────────────────────────
    fieldnames = [
        "flag_reasons", "timestamp", "date", "venue", "category",
        "city_raw", "city_final", "country", "lat", "lng",
        "checkin_id",
        "prev3", "next3",
        "suggested_city", "notes",
    ]

    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()

        for idx, reasons in flagged:
            row = rows[idx]

            prev_lines = [
                _context_line(rows[j])
                for j in range(max(0, idx - ctx_n), idx)
            ]
            next_lines = [
                _context_line(rows[j])
                for j in range(idx + 1, min(len(rows), idx + 1 + ctx_n))
            ]

            writer.writerow({
                "flag_reasons": ", ".join(reasons),
                "timestamp":    row["date"],
                "date":         row["date_human"],
                "venue":        row["venue"],
                "category":     row["category"],
                "city_raw":     row["city_raw"],
                "city_final":   row["city_final"],
                "country":      row["country"],
                "lat":          row["lat"],
                "lng":          row["lng"],
                "checkin_id":   row["checkin_id"],
                "prev3":        "\n".join(prev_lines),
                "next3":        "\n".join(next_lines),
                "suggested_city": "",
                "notes":          "",
            })

    print(f"Written: {out_path}", flush=True)
    print("\nFlag breakdown:", flush=True)
    from collections import Counter
    counts: Counter = Counter()
    for _, rs in flagged:
        for r in rs:
            counts[r] += 1
    for reason, n in counts.most_common():
        print(f"  {reason:30s} {n:>5}", flush=True)


if __name__ == "__main__":
    main()
