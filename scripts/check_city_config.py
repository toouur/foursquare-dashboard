"""
Consistency check for city normalization configs.

Validates:
  1. city_canonical.yaml: every canonical_map value is in valid_canonical
  2. city_canonical.yaml: every large_canonical entry is in valid_canonical
  3. city_canonical.yaml: every threshold key is in valid_canonical
  4. city_fixes.json:     keys (timestamps) are unique numeric ids; values
                          look like reasonable city names (no leading/trailing space).
  5. city_merge.yaml:     every raw key has a non-empty canonical value.

Exits non-zero on failure so CI can gate merges.
"""
from __future__ import annotations
import json, re, sys, io
from pathlib import Path
import yaml

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / 'config' / 'city_canonical.yaml'
CITY_FIXES = ROOT / 'config' / 'city_fixes.json'
VENUE_FIXES = ROOT / 'config' / 'venue_fixes.json'
CITY_MERGE = ROOT / 'config' / 'city_merge.yaml'

errors: list[str] = []
warnings: list[str] = []


def check_canonical():
    cfg = yaml.safe_load(CANONICAL.read_text(encoding='utf-8'))
    cmap = cfg.get('canonical_map', {})
    valid = set(cfg.get('valid_canonical', []))
    large = set(cfg.get('large_canonical', []))
    thresh = cfg.get('thresholds', {}) or {}

    missing = sorted({v for v in cmap.values() if v not in valid})
    if missing:
        errors.append(
            f'city_canonical.yaml: canonical_map values missing from valid_canonical: {missing}'
        )

    bad_large = sorted(large - valid)
    if bad_large:
        errors.append(
            f'city_canonical.yaml: large_canonical entries missing from valid_canonical: {bad_large}'
        )

    bad_thresh = sorted(set(thresh.keys()) - valid)
    if bad_thresh:
        errors.append(
            f'city_canonical.yaml: thresholds keys missing from valid_canonical: {bad_thresh}'
        )


def check_city_fixes():
    raw = json.loads(CITY_FIXES.read_text(encoding='utf-8'))
    # Accept either unix-timestamp (numeric) or 24-char hex Foursquare object id.
    key_re = re.compile(r'^(-?\d+|[0-9a-f]{24})$')
    bad_keys = [k for k in raw if not k.startswith('_') and not key_re.match(k)]
    if bad_keys:
        errors.append(f'city_fixes.json: non-numeric (non-comment) keys: {bad_keys[:5]}{"..." if len(bad_keys) > 5 else ""}')
    bad_vals = [
        (k, v) for k, v in raw.items()
        if not k.startswith('_') and isinstance(v, str) and v != v.strip()
    ]
    if bad_vals:
        errors.append(f'city_fixes.json: values with leading/trailing whitespace: {bad_vals[:5]}')


def check_venue_fixes():
    if not VENUE_FIXES.exists():
        return
    raw = json.loads(VENUE_FIXES.read_text(encoding='utf-8'))
    hex_re = re.compile(r'^[0-9a-f]{24}$')
    for k, v in raw.items():
        if k.startswith('_'):
            continue
        if not hex_re.match(k):
            errors.append(f'venue_fixes.json: key is not a 24-char hex venue_id: {k!r}')
            continue
        if not isinstance(v, dict):
            errors.append(f'venue_fixes.json: value for {k!r} must be an object')
            continue
        if not v.get('city') and not v.get('country'):
            errors.append(f'venue_fixes.json: {k!r} has neither city nor country')
        for fld in ('city', 'country'):
            val = v.get(fld)
            if val is not None and (not isinstance(val, str) or val != val.strip() or not val):
                errors.append(f'venue_fixes.json: {k!r} {fld} must be a non-empty trimmed string')


def check_city_merge():
    cm = yaml.safe_load(CITY_MERGE.read_text(encoding='utf-8'))
    if not isinstance(cm, dict):
        errors.append('city_merge.yaml: expected top-level mapping')
        return
    empties = [k for k, v in cm.items() if not v or not str(v).strip()]
    if empties:
        errors.append(f'city_merge.yaml: empty canonical for keys: {empties[:5]}')


def main() -> int:
    check_canonical()
    check_city_fixes()
    check_venue_fixes()
    check_city_merge()

    for w in warnings:
        print(f'WARN  {w}')
    for e in errors:
        print(f'ERROR {e}')

    if errors:
        print(f'\nFAIL: {len(errors)} error(s), {len(warnings)} warning(s)')
        return 1
    print(f'OK: all city normalization configs consistent ({len(warnings)} warning(s))')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
