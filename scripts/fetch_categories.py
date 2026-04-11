# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0
"""
Fetch the Foursquare Places API v3 category taxonomy and write it to
config/categories_fsq.json (committed to repo, served statically).

Usage:
    python scripts/fetch_categories.py --token YOUR_FSQ_API_KEY
    python scripts/fetch_categories.py  # reads $FSQ_API_KEY env var

The output is the raw /v3/places/categories response, which is a JSON object
with a top-level "categories" array of hierarchical category nodes:
  { "categories": [ { "id": "...", "name": "...", "categories": [...], "icon": {...} }, ... ] }
"""

import argparse
import json
import os
import sys
import urllib.request

OUT_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'categories_fsq.json')
URL = 'https://api.foursquare.com/v3/places/categories'


def fetch(token: str) -> dict:
    req = urllib.request.Request(
        URL,
        headers={
            'Authorization': token,
            'X-Places-Api-Version': '2025-06-17',
            'Accept': 'application/json',
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def count_nodes(cats: list) -> int:
    n = len(cats)
    for c in cats:
        n += count_nodes(c.get('categories') or [])
    return n


def main():
    ap = argparse.ArgumentParser(description='Fetch FSQ category taxonomy')
    ap.add_argument('--token', default=os.environ.get('FSQ_API_KEY', ''),
                    help='Foursquare API key (or set $FSQ_API_KEY)')
    args = ap.parse_args()

    if not args.token:
        print('ERROR: provide --token or set $FSQ_API_KEY', file=sys.stderr)
        sys.exit(1)

    print(f'Fetching {URL} …')
    data = fetch(args.token)
    cats = data.get('categories') or []
    if not cats:
        print('ERROR: empty response', file=sys.stderr)
        sys.exit(1)

    out = os.path.normpath(OUT_PATH)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))

    total = count_nodes(cats)
    print(f'Wrote {total} category nodes → {out}')


if __name__ == '__main__':
    main()
