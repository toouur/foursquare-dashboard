# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
fetch_lists.py — Incrementally fetch Foursquare user lists into lists.json.

Fetches all lists created by the user from the Foursquare v2 API.
Only re-fetches list items for lists whose updatedAt timestamp changed.

Usage:
    python scripts/fetch_lists.py --token "$FOURSQUARE_TOKEN"
    python scripts/fetch_lists.py --token "$FOURSQUARE_TOKEN" --out data/lists.json

Outputs:
  - Writes lists.json in the same format as the Foursquare data export.
  - Prints CHANGED=true/false to stdout (for GitHub Actions >> $GITHUB_OUTPUT).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

BASE      = "https://api.foursquare.com/v2"
API_V     = "20231201"
SLEEP     = 0.35
LIST_LIMIT = 50    # for /users/self/lists
ITEM_LIMIT = 200   # for /lists/{id}


def _get(url: str, params: dict, timeout: int = 30) -> dict:
    params.setdefault("v", API_V)
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    if d.get("meta", {}).get("code") != 200:
        raise RuntimeError(f"API error {url}: {d.get('meta')}")
    return d.get("response", {})


def fetch_list_directory(token: str) -> list[dict]:
    """Return list of {id, name, updatedAt, canonicalUrl, photo} for all user lists."""
    all_lists: list[dict] = []
    offset = 0
    while True:
        resp = _get(
            f"{BASE}/users/self/lists",
            {"oauth_token": token, "group": "created", "limit": LIST_LIMIT, "offset": offset},
        )
        items = resp.get("lists", {}).get("items", [])
        if not items:
            break
        for lst in items:
            all_lists.append({
                "id":          str(lst.get("id") or ""),
                "name":        (lst.get("name") or "").strip(),
                "canonicalUrl": (lst.get("canonicalUrl") or "").strip(),
                "updatedAt":   lst.get("updatedAt") or 0,
                "photo":       lst.get("photo") or {},
            })
        total = resp.get("lists", {}).get("count", 0)
        log.info("Fetched %d / %d lists", len(all_lists), total)
        if len(all_lists) >= total:
            break
        offset += LIST_LIMIT
        time.sleep(SLEEP)
    return all_lists


def fetch_list_items(token: str, list_id: str) -> list[dict]:
    """Fetch all venue items from a list."""
    items: list[dict] = []
    offset = 0
    # Get total count first
    probe = _get(
        f"{BASE}/lists/{list_id}",
        {"oauth_token": token, "limit": 1, "offset": 0},
    )
    total = probe.get("list", {}).get("listItems", {}).get("count", 0)

    while True:
        resp = _get(
            f"{BASE}/lists/{list_id}",
            {"oauth_token": token, "limit": ITEM_LIMIT, "offset": offset},
        )
        raw = resp.get("list", {}).get("listItems", {}).get("items", [])
        if not raw:
            break
        for item in raw:
            venue = item.get("venue") or {}
            vid = str(venue.get("id") or "").strip()
            if not vid:
                continue
            items.append({
                "createdAt": int(item.get("createdAt") or 0),
                "venue": {
                    "id":           vid,
                    "name":         (venue.get("name") or "").strip(),
                    "canonicalUrl": (venue.get("canonicalUrl") or "").strip(),
                    "location":     venue.get("location") or {},
                    "categories":   venue.get("categories") or [],
                },
            })
        log.info("  List %s: %d / %d items", list_id, len(items), total)
        if len(items) >= total:
            break
        offset += ITEM_LIMIT
        time.sleep(SLEEP)
    return items


def resolve_token(cli_token: str | None) -> str:
    cli = (cli_token or "").strip()
    if cli:
        return cli
    return os.environ.get("FOURSQUARE_TOKEN", "").strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default="", help="Foursquare OAuth token")
    parser.add_argument("--out",   default="", help="Output lists.json path (auto-resolved if omitted)")
    parser.add_argument("--full",  action="store_true", help="Re-fetch all lists regardless of updatedAt")
    args = parser.parse_args()

    token = resolve_token(args.token)
    if not token:
        log.error("Missing token. Provide --token or set FOURSQUARE_TOKEN.")
        print("CHANGED=false")
        return

    # Resolve output path
    if args.out:
        out_path = Path(args.out)
    else:
        candidates = [
            Path("data/lists.json"),
            Path("C:/Users/toouur/Documents/GitHub/foursquare-data/lists.json"),
        ]
        out_path = next((p for p in candidates if p.exists()), candidates[0])

    # Load existing
    existing_by_id: dict[str, dict] = {}
    if out_path.exists():
        try:
            raw = json.loads(out_path.read_text(encoding="utf-8"))
            existing_items = raw if isinstance(raw, list) else raw.get("items", [])
            for lst in existing_items:
                lid = str(lst.get("id") or "")
                if lid:
                    existing_by_id[lid] = lst
        except Exception as exc:
            log.warning("Could not read existing %s: %s", out_path, exc)

    log.info("Fetching list directory…")
    directory = fetch_list_directory(token)
    log.info("Found %d lists", len(directory))

    changed = False
    out_lists: list[dict] = []

    for meta in directory:
        lid = meta["id"]
        existing = existing_by_id.get(lid, {})
        existing_updated = existing.get("updatedAt", 0)
        fresh_updated = meta["updatedAt"]

        # Convert string timestamps from export format
        if isinstance(existing_updated, str):
            try:
                from datetime import datetime as _dt
                existing_updated = int(_dt.fromisoformat(existing_updated.replace("Z","")).timestamp())
            except Exception:
                existing_updated = 0

        needs_refetch = args.full or (fresh_updated != existing_updated) or ("listItems" not in existing)

        if needs_refetch:
            log.info("Fetching items for '%s' (updated: %s)", meta["name"], fresh_updated)
            try:
                list_items = fetch_list_items(token, lid)
                changed = True
            except Exception as exc:
                log.error("Failed to fetch items for list %s: %s", lid, exc)
                # Keep existing items
                list_items = (existing.get("listItems") or {}).get("items", [])
        else:
            log.info("Skipping '%s' (unchanged)", meta["name"])
            list_items = (existing.get("listItems") or {}).get("items", [])

        out_lists.append({
            "id":          lid,
            "name":        meta["name"],
            "canonicalUrl": meta["canonicalUrl"],
            "updatedAt":   fresh_updated,
            "photo":       meta["photo"],
            "listItems": {
                "count": len(list_items),
                "items": list_items,
            },
        })

    # Check if count of lists changed
    if len(out_lists) != len(existing_by_id):
        changed = True

    if changed:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps({"items": out_lists}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("Wrote %d lists → %s", len(out_lists), out_path)
    else:
        log.info("No changes detected in any list.")

    print(f"CHANGED={'true' if changed else 'false'}")


if __name__ == "__main__":
    main()
