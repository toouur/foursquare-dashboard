"""
transform.py  –  Data cleaning and normalisation.

Loads mappings from config/ and applies them to raw CSV rows.
"""
from __future__ import annotations

import json
import yaml
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ── Config loader ──────────────────────────────────────────────────────────────

def load_mappings(config_dir: str | Path = "config") -> dict[str, Any]:
    """Load city_merge (YAML), country_fixes and categories (JSON) from config/."""

    config_dir = Path(config_dir)

    def _load_file(name: str) -> dict:
        path = config_dir / name
        if not path.exists():
            print(f"Config file not found: {path} — skipping")
            return {}
        with open(path, encoding="utf-8") as fh:
            if path.suffix == ".yaml" or path.suffix == ".yml":
                return yaml.safe_load(fh) or {}
            elif path.suffix == ".json":
                return json.load(fh)
            else:
                print(f"Unknown config file type for {path}; must be .json or .yaml")
                return {}

    country_fixes = _load_file("country_fixes.json")
    city_merge    = _load_file("city_merge.yaml")   # This one is YAML!
    cats_raw      = _load_file("categories.json")

    return {
        "country_fixes":   country_fixes,
        "city_merge":      city_merge,
        "category_groups": cats_raw.get("category_groups", {}),
        "explorer_groups": cats_raw.get("explorer_groups", {}),
    }


# ── Row-level transforms ───────────────────────────────────────────────────────

def apply_transforms(rows: list[dict], mappings: dict[str, Any]) -> list[dict]:
    """
    Apply country fixes and city merges in-place (modifies rows).
    Returns the same list for convenience.
    """
    country_fixes = mappings.get("country_fixes", {})
    city_merge    = mappings.get("city_merge", {})

    malformed_dates = 0
    for row in rows:
        # Country fix (keyed on unix timestamp string)
        ts = row.get("date", "").strip()
        if ts in country_fixes:
            row["country"] = country_fixes[ts]

        # City normalisation
        city = row.get("city", "").strip()
        row["city"] = city_merge.get(city, city)

        # Validate date
        if ts and not ts.lstrip("-").isdigit():
            malformed_dates += 1

    if malformed_dates:
        log.warning("%d rows had non-numeric 'date' values", malformed_dates)

    return rows


# ── Category helpers ───────────────────────────────────────────────────────────

def build_categorize_fn(category_groups: dict[str, list[str]]):
    """Return a function that maps a raw category string to a group name."""
    # Build a fast lookup: raw_cat_lower → group
    fast: dict[str, str] = {}
    for group, keywords in category_groups.items():
        for kw in keywords:
            fast[kw.lower()] = group

    def categorize(raw: str) -> str | None:
        cl = raw.lower()
        if cl in fast:
            return fast[cl]
        for kw_lower, group in fast.items():
            if kw_lower in cl or cl in kw_lower:
                return group
        return None

    return categorize
