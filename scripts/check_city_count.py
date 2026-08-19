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
import os
import sys
import unicodedata
from collections import Counter
from pathlib import Path

_APOS = {"’": "'", "‘": "'"}


def _fold(s: str) -> str:
    """Collapse encoding/case/apostrophe/diacritic variants of the same place name.

    Diacritics are stripped so an ASCII spelling and its accented twin collide
    ('Dusseldorf' vs 'Dūsseldorf' vs 'Düsseldorf'). Without this, the only
    duplicates caught were pure encoding/case variants, and every
    ASCII-vs-accent pair passed the gate as two separate cities.
    """
    s = unicodedata.normalize("NFC", s)
    for a, b in _APOS.items():
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
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


def load_shape_patterns(config_dir: str) -> list:
    """Patterns that mark a string as NOT a settlement name.

    `city_canonical.yaml: skip_patterns` already lists the administrative-unit
    shapes the blank-city resolver refuses to adopt ("… район", "… область",
    "д. X"). The same shapes must never reach a DISPLAYED city either — a
    `city_fixes` value bypasses city_merge entirely (transform.py returns early),
    so nothing downstream would catch them. Reusing that list keeps one source of
    truth; the extras below cover shapes the resolver never sees because they
    arrive as explicit overrides.
    """
    import re
    import yaml
    pats: list = []
    canon = Path(config_dir) / "city_canonical.yaml"
    if canon.exists():
        try:
            cd = yaml.safe_load(canon.read_text(encoding="utf-8")) or {}
            pats += [re.compile(p) for p in (cd.get("skip_patterns") or [])]
        except Exception:
            pass
    pats += [
        re.compile(r"\s[-—]\s"),          # "Sejny - Lazdijai" — a pair of places
        re.compile(r"/"),                  # "РФ / РБ" — a border label
        re.compile(r"\bProvince\b", re.I),
        re.compile(r"^(stancyja|станция|ст\.)\s", re.I),
        re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]"),   # CJK from a localised card
    ]
    return pats


def is_not_a_city(city: str, shape_patterns: list,
                  canonical_values: set[str] | None = None) -> bool:
    """True when the displayed name looks like an administrative unit, a pair of
    places or a station label rather than a settlement.

    A name that is the TARGET of a city_merge mapping (or whitelisted in
    valid_canonical) is approved by construction: the house style deliberately
    keeps region labels such as "Smolensk Region" when the check-in genuinely
    happened in the region with no finer settlement, and real names can carry an
    odd shape (the bilingual Swiss city "Biel/Bienne"). Only unvouched names are
    flagged.
    """
    if canonical_values and city in canonical_values:
        return False
    return any(p.search(city) for p in shape_patterns)


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
    # venue_fixes.json pins a city per venue_id and outranks every other rule
    # (transform.py applies it first). A label chosen there — e.g. the M1 border
    # post deliberately assigned to "Vitebsk Region" on the Belarusian side — is
    # as vouched as a city_merge target and must not read as an unvouched shape.
    vf = cfg / "venue_fixes.json"
    if vf.exists():
        try:
            import json as _json
            for entry in (_json.loads(vf.read_text(encoding="utf-8")) or {}).values():
                if isinstance(entry, dict):
                    city = (entry.get("city") or "").strip()
                    if city:
                        values.add(city)
        except Exception:
            pass
    return keys, values


def find_fold_collisions(counts: Counter, canonical_values: set[str]) -> dict[str, str]:
    """Return {redundant_spelling: keeper} for displayed cities that fold to the
    same key. The keeper is the canonical / highest-count spelling; ties break
    toward the accented form, matching the house convention in city_merge.yaml
    (accented spelling is canonical, ASCII maps onto it)."""
    groups: dict[str, list[str]] = {}
    for city in counts:
        groups.setdefault(_fold(city), []).append(city)

    merges: dict[str, str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        def rank(c: str) -> tuple:
            return (c in canonical_values, counts[c], not c.isascii())
        members.sort(key=rank, reverse=True)
        keep = members[0]
        for other in members[1:]:
            merges[other] = keep
    return merges


def find_invariant_hits(counts: Counter, canonical_values: set[str]) -> list[tuple[str, str]]:
    """Return [(city, reason)] for cities that are demonstrably normalization
    misses: non-NFC strings, or fold-collisions with another displayed city."""
    hits: list[tuple[str, str]] = []
    # non-NFC
    for city in counts:
        if city != unicodedata.normalize("NFC", city):
            hits.append((city, "non-NFC (decomposed diacritics)"))
    for other, keep in find_fold_collisions(counts, canonical_values).items():
        hits.append((other, f"fold-collision with {keep!r} (same place)"))
    return hits


def apply_auto_merge(merges: dict[str, str], config_dir: str) -> int:
    """Self-heal: append each {variant: keeper} pair to config/city_merge.yaml so
    the next build normalizes it away. Returns the number of rules written.

    Only touches spellings that are provably the same place (they fold to the
    same key), so this can never merge two genuinely distinct cities. Entries
    already present are skipped, making repeated runs idempotent.
    """
    cm = Path(config_dir) / "city_merge.yaml"
    if not merges or not cm.exists():
        return 0

    import yaml
    existing = yaml.safe_load(cm.read_text(encoding="utf-8")) or {}
    new = {k: v for k, v in merges.items() if k not in existing}
    if not new:
        return 0

    lines = []
    if "AUTO-MERGED" not in cm.read_text(encoding="utf-8"):
        lines += ["", "  # ══ AUTO-MERGED (check_city_count.py --auto-merge) ══"]
    for variant, keep in sorted(new.items()):
        lines.append(f'  "{variant}": "{keep}"  # auto — fold-collision with canonical spelling')

    # The file is CRLF; match it so the append doesn't produce mixed endings.
    eol = "\r\n" if "\r\n" in cm.read_bytes().decode("utf-8") else "\n"
    with cm.open("a", encoding="utf-8", newline="") as f:
        f.write(eol.join(lines) + eol)
    return len(new)


def judge_added(city: str, count: int, removed: dict[str, int],
                invariant_cities: set[str], canonical_values: set[str],
                shape_patterns: list | None = None) -> str:
    if city in invariant_cities:
        return "MISS"
    if shape_patterns and is_not_a_city(city, shape_patterns, canonical_values):
        return "NOT-A-CITY (administrative unit / pair / station, needs a city_merge rule)"
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
    ap.add_argument("--auto-merge", action="store_true",
                    help="Self-heal: write fold-collision duplicates into "
                         "config/city_merge.yaml instead of failing on them")
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

    # ── self-heal (optional) ─────────────────────────────────────────────────
    # Duplicate spellings are mechanically fixable: fold-collision proves the two
    # strings are the same place, so write the mapping and re-run the pipeline.
    # Whatever survives is a real problem worth reporting below.
    if args.auto_merge:
        merges = find_fold_collisions(counts, canonical_values)
        written = apply_auto_merge(merges, args.config_dir)
        if written:
            for variant, keep in sorted(merges.items()):
                print(f"AUTO-MERGE: {variant!r} → {keep!r}")
            print(f"Wrote {written} rule(s) to {args.config_dir}/city_merge.yaml")
            counts = compute_city_counts(args.csv, args.config_dir)
            _, canonical_values = load_canonical(args.config_dir)
            total_cities = len(counts)
            total_checkins = sum(counts.values())
        gho = os.environ.get("GITHUB_OUTPUT")
        if gho:
            with open(gho, "a", encoding="utf-8") as f:
                f.write(f"AUTO_MERGED={written}\n")

    # ── invariant checks (always) ────────────────────────────────────────────
    invariant_hits = find_invariant_hits(counts, canonical_values)
    invariant_cities = {c for c, _ in invariant_hits}
    shape_patterns = load_shape_patterns(args.config_dir)

    # A non-settlement name must be caught even when it was ALREADY displayed:
    # the baseline can predate the mistake, and then it never shows as "added".
    shape_all = sorted((c, n) for c, n in counts.items()
                       if is_not_a_city(c, shape_patterns, canonical_values))

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
    base_counts: dict[str, int] = {}
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
                v = judge_added(city, n, removed, invariant_cities,
                                canonical_values, shape_patterns)
                verdicts.append((city, v))
                print(f"    {n:5} × {city!r}  → {v}")
        if removed:
            print(f"\n  Removed ({len(removed)}):")
            for city, n in sorted(removed.items(), key=lambda kv: -kv[1]):
                print(f"    {n:5} × {city!r}")
    elif args.baseline:
        print(f"\n(no baseline at {args.baseline} yet — "
              f"run with --update-baseline to create one)")

    # Split by baseline: a name already displayed when the baseline was taken is
    # existing debt, not a regression, so it reports without failing the run. A
    # name that appeared since is exactly what this check exists to stop.
    shape_new = [(c, n) for c, n in shape_all if c not in base_counts]
    shape_old = [(c, n) for c, n in shape_all if c in base_counts]

    if shape_new:
        print(f"\nNOT A CITY — {len(shape_new)} NEW displayed name(s) are administrative "
              f"units, pairs of places or station labels:")
        for c, n in shape_new:
            print(f"    {n:5} × {c!r}")
        print("\nFix: add a mapping to config/city_merge.yaml. NOTE: a city_fixes.json "
              "value bypasses city_merge (transform.py returns early), so such a value "
              "must be written already-canonical.")
    if shape_old:
        print(f"\nNot-a-city debt — {len(shape_old)} name(s) already in the baseline "
              f"(reported, not blocking):")
        for c, n in shape_old:
            print(f"    {n:5} × {c!r}")

    # ── verdict ──────────────────────────────────────────────────────────────
    # HARD: invariant hits (or an added city that trips one). SOFT: RENAME?/REVIEW.
    hard = (bool(invariant_hits) or bool(shape_new)
            or any(v == "MISS" or v.startswith("NOT-A-CITY") for _, v in verdicts))
    soft = (bool(shape_old)
            or any(v.startswith("RENAME") or v.startswith("REVIEW") for _, v in verdicts))

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
