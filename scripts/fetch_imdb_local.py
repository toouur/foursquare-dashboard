#!/usr/bin/env python3
"""Refresh IMDb ratings from a user-authenticated, visible local Chromium session.

This is intended for a person's own desktop browser session. It does not solve,
hide, or bypass CAPTCHA, MFA, WAF, or other security challenges. The user completes
sign-in manually, then the script reads the ratings page and validates the result
before replacing the output file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

import fetch_imdb as parser


SIGNIN_URL = "https://www.imdb.com/registration/signin/"
WAF_MARKERS = ("captcha", "robot check", "security challenge", "verify you are human",
               "automated access", "x-amzn-waf-action")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="Path to imdb_ratings.json (existing file is preserved on failure)")
    ap.add_argument("--profile", default=".imdb-playwright-profile",
                    help="Local Chromium profile directory; keep private and do not commit it")
    ap.add_argument("--user", default=parser.USER)
    args = ap.parse_args()

    output = Path(args.out).expanduser().resolve()
    profile = Path(args.profile).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(SIGNIN_URL, wait_until="domcontentloaded", timeout=60000)
            print("\nA normal Chromium window is open.")
            print("Sign in to IMDb yourself in that window. Complete any verification normally.")
            print("When you can see that you're signed in, return here and press Enter.")
            input()
            page.goto(parser.URL.format(user=args.user), wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1200)
            html = page.content()
            title = page.title()
            body = " ".join(page.locator("body").inner_text(timeout=10000).split())[:2000]
            combined = (title + " " + page.url + " " + body + " " + html[:5000]).lower()
            if any(marker in combined for marker in WAF_MARKERS) or "x-amzn-waf-action" in html.lower():
                print("IMDb is showing a security challenge/block. Stopping without trying to bypass it.")
                return 2
            if page.url.startswith(SIGNIN_URL) or "sign in" in title.lower() and "ratings" not in page.url.lower():
                print("The session doesn't appear to be signed in. Please sign in and run again.")
                return 2
            match = parser.NEXT.search(html)
            if not match:
                print(f"No IMDb page data found (title={title!r}, URL={page.url!r}). Existing file preserved.")
                return 1
            data = json.loads(match.group(1))
            items = parser.find_items(data)
            if not items:
                pp = data.get("props", {}).get("pageProps", {}) or {}
                edges = ((pp.get("mainColumnData") or {}).get("advancedTitleSearch") or {}).get("edges") or []
                titles = [(e.get("node") or {}).get("title") for e in edges]
                titles = [t for t in titles if isinstance(t, dict)]
                cookies = context.cookies("https://www.imdb.com")
                cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                if titles and cookie_header:
                    error = parser.add_user_ratings(
                        requests, titles,
                        {"User-Agent": page.evaluate("() => navigator.userAgent"),
                         "Cookie": cookie_header, "Accept-Language": "en-US,en;q=0.9"},
                    )
                    if error:
                        print(f"IMDb ratings API didn't return usable data: {error}")
                    items = parser.find_items(data)

            rows = [parser.to_row(node) for _, node in items]
            rows = [row for row in rows if row.get("id") and row.get("rating")]
            old: dict[str, dict] = {}
            if output.exists():
                previous = json.loads(output.read_text(encoding="utf-8"))
                previous_rows = previous.get("ratings")
                if not isinstance(previous_rows, list):
                    raise ValueError("Existing ratings file has invalid schema; refusing to overwrite")
                old = {row["id"]: row for row in previous_rows
                       if isinstance(row, dict) and isinstance(row.get("id"), str)}
                if previous_rows and not old:
                    raise ValueError("Existing ratings file has no valid IDs; refusing to overwrite")

            minimum = min(5, len(old)) if old else 5
            if len(rows) < minimum:
                print(f"Only {len(rows)} ratings parsed; minimum is {minimum}. Existing file preserved.")
                return 1

            merged = dict(old)
            for row in rows:
                if not row.get("date") and row["id"] in old:
                    row["date"] = old[row["id"]].get("date", "")
                merged[row["id"]] = {
                    **old.get(row["id"], {}),
                    **{k: v for k, v in row.items() if v not in (None, "", [])},
                }

            payload = {"ratings": sorted(
                merged.values(), key=lambda row: (row.get("date", ""), row["id"])
            )}
            temp = output.with_suffix(output.suffix + ".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            json.loads(temp.read_text(encoding="utf-8"))  # verify before replacing
            temp.replace(output)
            print(f"Success: parsed {len(rows)} ratings; {len(merged)} ratings stored in {output}")
            print("Next: review the diff, then commit imdb_ratings.json to the private data repository.")
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
