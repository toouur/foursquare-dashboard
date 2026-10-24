# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
fetch_vivino.py — every rated wine of a PUBLIC Vivino profile → vivino_ratings.json.

No login, no export: Vivino's own JSON API serves a public profile's activity
feed (`api.vivino.com/users/<id>/activities`, newest first, `limit` + `start_from`
paging). Each "reviewed user-vintage" activity is one rating — the same 804 rows
the CSV data export lists as rated, plus what the export lacks: the RATING time
(`review.created_at`) next to the scan time (`scanned_at`), so a wine scanned at
the table and rated a week later is still placed at the table, and the 62 wines
the export has no date for get one. The same wine rated on several days is
several ratings (the export made those look like duplicates).

The whole feed is re-walked on every run (~17 requests for ~800 ratings): edits
and deletions on Vivino are mirrored for free. A walk that comes back more than
10 % smaller than the file on disk is treated as an API failure and nothing is
written. Exit 0 ok / 1 failure; prints CHANGED=true|false.

    python scripts/fetch_vivino.py --user 39350853 --out $DATA/vivino_ratings.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

log = logging.getLogger("fetch_vivino")

API = "https://api.vivino.com/users/{user}/activities"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PAGE = 50
MAX_PAGES = 400
MIN_RATIO = 0.9
WINE_TYPES = {1: "Red Wine", 2: "White Wine", 3: "Sparkling", 4: "Rosé Wine", 7: "Dessert Wine", 24: "Fortified Wine"}
CONFIG = Path(__file__).resolve().parent.parent / "config" / "country_flags.json"


def _get(url: str, retries: int = 4):
    import requests  # noqa: PLC0415
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=30)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                raise requests.ConnectionError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt >= retries:
                raise
            log.warning("Vivino request failed (%s) — retry in %ds", e, 2 ** attempt)
            time.sleep(2 ** attempt)


def walk(user: str, get=_get) -> list[dict]:
    """All activities of the profile, newest first."""
    out: list[dict] = []
    start = None
    for _ in range(MAX_PAGES):
        url = f"{API.format(user=user)}?limit={PAGE}" + (f"&start_from={start}" if start else "")
        page = get(url) or []
        if not page:
            break
        out.extend(page)
        start = page[-1]["id"]
        time.sleep(0.3)
    return out


def _img(loc) -> str:
    return ("https:" + loc) if isinstance(loc, str) and loc.startswith("//") else (loc or "")


def wine_url(v: dict, w: dict) -> str:
    """Link by WINE id (`/w/<id>?year=`): Vivino resolves every one of those, while
    `/wines/<vintage id>` (what its own CSV export uses) errors for many vintages."""
    year = "" if str(v.get("year") or "") in ("", "0", "None") else str(v.get("year"))
    if w.get("id"):
        return f"https://www.vivino.com/w/{w['id']}" + (f"?year={year}" if year else "")
    return f"https://www.vivino.com/wines/{v['id']}" if v.get("id") else ""


def to_rating(act: dict, iso2_to_name: dict[str, str]) -> dict | None:
    """One reviewed user-vintage activity → the row culture_sources.load_vivino returns."""
    if act.get("object_type") != "user-vintage":
        return None
    o = act.get("object") or {}
    rev = o.get("review") or {}
    if rev.get("rating") is None:
        return None
    v = o.get("vintage") or {}
    w = v.get("wine") or {}
    reg = w.get("region") or {}
    winery = w.get("winery") or {}
    code = (reg.get("country") or "").lower()
    return {
        "id": str(o.get("id") or ""),
        "wine": w.get("name") or v.get("name") or "",
        "winery": winery.get("name") or "",
        "vintage": "" if str(v.get("year") or "") in ("", "0", "None") else str(v.get("year")),
        "region": reg.get("name") or "",
        "country": iso2_to_name.get(code, code.upper()),
        "type": WINE_TYPES.get(int(w.get("type_id") or 0), "Unknown"),
        "rating": float(rev["rating"]),
        "avg": (v.get("statistics") or {}).get("ratings_average"),
        "review": (rev.get("note") or "").strip(),
        "scanned_at": o.get("scanned_at"),
        "rated_at": rev.get("created_at"),
        "url": wine_url(v, w),
        "label": _img((o.get("image") or {}).get("location") or (v.get("image") or {}).get("location")),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mirror a public Vivino profile's ratings.")
    ap.add_argument("--user", default="39350853", help="numeric Vivino user id (andrei.patsiomkin)")
    ap.add_argument("--out", required=True, help="vivino_ratings.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    try:
        names = {v.lower(): k for k, v in json.loads(CONFIG.read_text(encoding="utf-8")).items()}
    except Exception:
        names = {}
    try:
        acts = walk(args.user)
    except Exception as e:
        log.error("Vivino walk failed: %s", e)
        print("CHANGED=false")
        return 1
    rows = [r for r in (to_rating(a, names) for a in acts) if r]
    rows.sort(key=lambda r: (r["rated_at"] or "", r["id"]))
    out = Path(args.out)
    old: list = []
    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8")).get("ratings", [])
        except Exception:
            old = []
    if old and len(rows) < MIN_RATIO * len(old):
        log.error("Walk returned %d ratings, the file holds %d — treated as an API failure, nothing written",
                  len(rows), len(old))
        print("CHANGED=false")
        return 1
    changed = rows != old
    if changed:
        # one rating per line: a weekly run's git diff is just the new or edited ratings
        body = ",\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows)
        out.write_text(f'{{"user":{json.dumps(args.user)},"ratings":[\n{body}\n]}}\n', encoding="utf-8", newline="\n")
        log.info("Vivino: %d ratings written (%+d)", len(rows), len(rows) - len(old))
    else:
        log.info("Vivino: %d ratings, no change", len(rows))
    print(f"CHANGED={'true' if changed else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
