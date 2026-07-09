#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""check_city_count.py — watch the *normalized* distinct-city set for drift.

Unlike check_city_drift.py (which scans RAW city values for coverage gaps),
this gate runs the full transform pipeline and inspects the actual set of
cities the dashboard would display. It answers the question the raw scan
can't: "did the count of distinct cities change, and if so, is a new entry a
genuinely new place or a normalization miss (e.g. Foursquare renamed a city,
or returned it in NFD, so it slipped past every string-keyed rule)?"

Two independent checks:

1. INVARIANTS (stateless, always run, hard-fail) — precise, low-false-positive
   signals that a displayed city is really a normalization miss:
     • non-NFC       — the city string isn't NFC-normalized (decomposed
                       diacritics). transform.py NFC-normalizes now, so this
                       should stay empty; a hit means a path bypassed it.
     • fold-collision — two distinct displayed cities collapse to the same key
                       under NFC + apostrophe-fold + casefold. One is redundant
                       (same place, different encoding/case/spelling), e.g. an
                       NFD "Sóc Sơn" living alongside the NFC one.

2. BASELINE DIFF (optional, --baseline) — compares the current {city: count}
   snapshot to a stored one, reports the count delta and the added/removed
   cities, and auto-judges each *added* city:
     • MISS      — trips an invariant above.
     • RENAME?   — its check-in count exactly matches a simultaneously-removed
                   city (Foursquare likely renamed the venue's city string).
     • REVIEW    — non-ASCII and not a known canonical/merge value.
     • new       — looks like a legitimate new place.

Severity split so the hourly deploy isn't blocked by normal travel:
  • HARD (exit 1) — invariant hits (non-NFC / fold-collision). These can't happen
    on correctly-normalized output, so they're real bugs worth stopping a deploy.
  • SOFT (exit 0, reported) — added-city verdicts RENAME?/REVIEW. Surfaced for a
    human to glance at; a genuinely new city is expected and must not block CI.
Use --strict to make SOFT findings exit 1 too. --warn-only forces exit 0 always.
Refresh the baseline after an intentional change with --update-baseline.

Usage:
    python scripts/check_city_count.py --csv data/checkins.csv --config-dir config
    python scripts/check_city_count.py --csv data/checkins.csv \\
        --baseline config/city_count_baseline.json
    python scripts/check_city_count.py --csv data/checkins.csv \\
        --baseline config/city_count_baseline.json --update-baseline
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

_APOS = {"’": "'", "‘": "'"}


def _fold(s: str) -> str:
    """Collapse encoding/case/apostrophe variants of the same place name."""
    s = unicodedata.normalize("NFC", s)
    for a, b in _APOS.items():
        s = s.replace(a, b)
    return s.casefold().strip()


def compute_city_counts(csv_path: str, config_dir: str) -> Counter:
    """Run the real transform pipeline and count displayed cities."""
    sys.path.insert(0, str(Path(__file__).parent))
    from transform import load_mappings, apply_transforms, build_blank_city_resolver

    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    mappings = load_mappings(config_dir)
    # Mirror build.py: the blank-city resolver reads the review CSV beside config/.
    review_csv = Path(config_dir) / "city_merge_normalized_review.csv"
    resolver = build_blank_city_resolver(review_csv)
    rows = apply_transforms(rows, mappings, blank_city_resolver=resolver)
    return Counter(r["city"] for r in rows if (r.get("city") or "").strip())


def load_canonical(config_dir: str) -> tuple[set[str], set[str]]:
    """Return (merge_keys, canonical_values) — the strings that are legitimately
    allowed to appear non-ASCII (targets of a mapping / whitelisted canonicals)."""
    import yaml
    cfg = Path(config_dir)
    keys: set[str] = set()
    values: set[str] = set()
    cm = cfg / "city_merge.yaml"
    if cm.exists():
        data = yaml.safe_load(cm.read_text(encoding="utf-8")) or {}
        for k, v in data.items():
            if isinstance(k, str) and k.strip():
                keys.add(k.strip())
            if isinstance(v, str) and v.strip():
                values.add(v.strip())
    canon = cfg / "city_canonical.yaml"
    if canon.exists():
        try:
            cd = yaml.safe_load(canon.read_text(encoding="utf-8")) or {}
            values |= set(cd.get("valid_canonical", []))
        except Exception:
            pass
    return keys, values


def find_invariant_hits(counts: Counter, canonical_values: set[str]) -> list[tuple[str, str]]:
    """Return [(city, reason)] for cities that are demonstrably normalization
    misses: non-NFC strings, or fold-collisions with another displayed city."""
    hits: list[tuple[str, str]] = []
    # non-NFC
    for city in counts:
        if city != unicodedata.normalize("NFC", city):
            hits.append((city, "non-NFC (decomposed diacritics)"))
    # fold-collision: group displayed cities by fold key
    groups: dict[str, list[str]] = {}
    for city in counts:
        groups.setdefault(_fold(city), []).append(city)
    for key, members in groups.items():
        if len(members) < 2:
            continue
        # Prefer the canonical / ASCII / highest-count spelling; flag the rest.
        def rank(c: str) -> tuple:
            return (c in canonical_values, c.isascii(), counts[c])
        members.sort(key=rank, reverse=True)
        keep = members[0]
        for other in members[1:]:
            hits.append((other, f"fold-collision with {keep!r} (same place)"))
    return hits


def judge_added(city: str, count: int, removed: dict[str, int],
                invariant_cities: set[str], canonical_values: set[str]) -> str:
    if city in invariant_cities:
        return "MISS"
    # rename: a removed city carried the same check-in count
    for rc, rn in removed.items():
        if rn == count:
            return f"RENAME? (was {rc!r}, {rn} check-ins)"
    if not city.isascii() and city not in canonical_values:
        return "REVIEW (non-ASCII, not a known canonical)"
    return "new"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--baseline", help="Path to city_count_baseline.json")
    ap.add_argument("--update-baseline", action="store_true",
                    help="Write the current snapshot to --baseline and exit 0")
    ap.add_argument("--strict", action="store_true",
                    help="Also exit 1 on soft findings (RENAME?/REVIEW added cities)")
    ap.add_argument("--warn-only", action="store_true",
                    help="Report but always exit 0 (non-blocking CI use)")
    args = ap.parse_args()

    try:
        import yaml  # noqa: F401  (used transitively; fail early if missing)
    except ImportError:
        print("ERROR: pyyaml required (pip install pyyaml)", file=sys.stderr)
        return 2

    counts = compute_city_counts(args.csv, args.config_dir)
    _, canonical_values = load_canonical(args.config_dir)
    total_cities = len(counts)
    total_checkins = sum(counts.values())

    # ── update-baseline short-circuit ────────────────────────────────────────
    if args.update_baseline:
        if not args.baseline:
            print("ERROR: --update-baseline requires --baseline", file=sys.stderr)
            return 2
        snapshot = {
            "total_cities": total_cities,
            "total_checkins": total_checkins,
            "counts": dict(sorted(counts.items())),
        }
        Path(args.baseline).write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"Baseline written: {args.baseline} "
              f"({total_cities} cities / {total_checkins} check-ins)")
        return 0

    # ── invariant checks (always) ────────────────────────────────────────────
    invariant_hits = find_invariant_hits(counts, canonical_values)
    invariant_cities = {c for c, _ in invariant_hits}

    print(f"Cities displayed: {total_cities}  (from {total_checkins} check-ins)")

    if invariant_hits:
        print(f"\nNORMALIZATION MISS — {len(invariant_hits)} displayed "
              f"city(ies) are encoding/spelling duplicates:")
        for city, reason in sorted(invariant_hits):
            print(f"  {counts[city]:5} × {city!r}  — {reason}")
        print("\nFix: add a mapping to config/city_merge.yaml (NFC key), or check "
              "that transform.py NFC-normalizes this field.")

    # ── baseline diff (optional) ─────────────────────────────────────────────
    verdicts: list[tuple[str, str]] = []
    if args.baseline and Path(args.baseline).exists():
        base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        base_counts = base.get("counts", {})
        added = {c: n for c, n in counts.items() if c not in base_counts}
        removed = {c: n for c, n in base_counts.items() if c not in counts}
        delta = total_cities - base.get("total_cities", len(base_counts))

        print(f"\nBaseline: {base.get('total_cities', len(base_counts))} cities "
              f"→ now {total_cities}  (Δ {delta:+d})")
        if added:
            print(f"\n  Added ({len(added)}):")
            for city, n in sorted(added.items(), key=lambda kv: -kv[1]):
                v = judge_added(city, n, removed, invariant_cities, canonical_values)
                verdicts.append((city, v))
                print(f"    {n:5} × {city!r}  → {v}")
        if removed:
            print(f"\n  Removed ({len(removed)}):")
            for city, n in sorted(removed.items(), key=lambda kv: -kv[1]):
                print(f"    {n:5} × {city!r}")
    elif args.baseline:
        print(f"\n(no baseline at {args.baseline} yet — "
              f"run with --update-baseline to create one)")

    # ── verdict ──────────────────────────────────────────────────────────────
    # HARD: invariant hits (or an added city that trips one). SOFT: RENAME?/REVIEW.
    hard = bool(invariant_hits) or any(v == "MISS" for _, v in verdicts)
    soft = any(v.startswith("RENAME") or v.startswith("REVIEW") for _, v in verdicts)

    if not hard and not soft:
        print("\nOK — no normalization misses.")
        return 0
    if soft and not hard:
        print("\nReview the added cities above (soft findings — not blocking).")
    if args.warn_only:
        print("\n(--warn-only: reporting only, exit 0)")
        return 0
    if hard:
        return 1
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
