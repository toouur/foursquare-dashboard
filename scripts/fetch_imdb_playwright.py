#!/usr/bin/env python3
"""Experimental Playwright-based IMDb refresh. Uses normal sign-in; never bypasses challenges."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright
import fetch_imdb as parser

USER = parser.USER
RATINGS_URL = parser.URL.format(user=USER)
SIGNIN_URL = "https://www.imdb.com/registration/signin/"


def note(message: str) -> None:
    print(f"::notice title=IMDb Playwright::{message}" if os.getenv("GITHUB_ACTIONS") == "true" else message)


def main() -> int:
    username = os.getenv("IMDB_USERNAME", "").strip()
    password = os.getenv("IMDB_PASSWORD", "")
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "imdb_ratings.json")
    if not username or not password:
        print("::error title=IMDb Playwright::Set IMDB_USERNAME and IMDB_PASSWORD secrets")
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="en-US")
        page = context.new_page()
        try:
            page.goto(SIGNIN_URL, wait_until="domcontentloaded", timeout=60000)
            # IMDb may show its account-provider chooser first.
            for selector in ('a[href*="ap/signin"]', 'a:has-text("Sign in with IMDb")',
                             'button:has-text("Sign in with IMDb")'):
                try:
                    page.locator(selector).first.click(timeout=2500)
                    break
                except Exception:
                    pass
            page.wait_for_timeout(1000)
            email = page.locator('input[name="email"], input[type="email"]').first
            email.wait_for(state="visible", timeout=15000)
            email.fill(username)
            page.locator('input[name="password"], input[type="password"]').first.fill(password)
            page.locator('input[type="submit"], button[type="submit"], button:has-text("Sign in")').first.click()
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)

            # Do not attempt to solve or evade CAPTCHA/WAF/MFA challenges.
            text = (page.title() + " " + page.locator("body").inner_text(timeout=10000))[:12000].lower()
            if any(x in text for x in ("captcha", "robot check", "security challenge", "verify you are human")):
                print("::error title=IMDb Playwright::IMDb presented a security challenge; stopping without bypass")
                return 2
            if "password" in page.url.lower() or "signin" in page.url.lower():
                print("::error title=IMDb Playwright::Sign-in did not complete; check credentials or account verification")
                return 2

            page.goto(RATINGS_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
            html = page.content()
            if "x-amzn-waf-action" in html.lower() or "robot check" in html.lower() or "captcha" in html.lower():
                print("::error title=IMDb Playwright::IMDb challenged the browser request; no data was written")
                return 2
            match = parser.NEXT.search(html)
            if not match:
                print(f"::error title=IMDb Playwright::Ratings page has no __NEXT_DATA__ (HTTP page title: {page.title()[:120]})")
                return 1
            data = json.loads(match.group(1))
            items = parser.find_items(data)
            if not items:
                pp = data.get("props", {}).get("pageProps", {}) or {}
                edges = ((pp.get("mainColumnData") or {}).get("advancedTitleSearch") or {}).get("edges") or []
                titles = [e.get("node", {}).get("title") for e in edges if isinstance(e.get("node", {}).get("title"), dict)]
                cookie = "; ".join(f"{c['name']}={c['value']}" for c in context.cookies("https://www.imdb.com"))
                if titles and cookie:
                    err = parser.add_user_ratings(requests, titles, {
                        "User-Agent": page.evaluate("() => navigator.userAgent"),
                        "Cookie": cookie, "Accept-Language": "en-US,en;q=0.9"})
                    if err:
                        note("GraphQL rating lookup failed: " + err[:180])
                    items = parser.find_items(data)
            rows = [parser.to_row(node) for _, node in items]
            rows = [row for row in rows if row.get("id") and row.get("rating")]
            old = {}
            if output.exists():
                previous = json.loads(output.read_text(encoding="utf-8"))
                previous_rows = previous.get("ratings")
                if not isinstance(previous_rows, list):
                    raise ValueError("existing ratings file has invalid schema")
                old = {row["id"]: row for row in previous_rows if isinstance(row, dict) and isinstance(row.get("id"), str)}
                if previous_rows and not old:
                    raise ValueError("existing ratings file has no valid IDs")
            minimum = min(5, len(old)) if old else 5
            if len(rows) < minimum:
                print(f"::error title=IMDb Playwright::Only {len(rows)} ratings parsed (minimum {minimum}); preserving previous file")
                return 1
            merged = dict(old)
            for row in rows:
                if not row.get("date") and row["id"] in old:
                    row["date"] = old[row["id"]].get("date", "")
                merged[row["id"]] = {**old.get(row["id"], {}), **{k: v for k, v in row.items() if v not in (None, "", [])}}
            body = ",\\n".join(json.dumps(v, ensure_ascii=False, sort_keys=True)
                               for v in sorted(merged.values(), key=lambda v: (v.get("date", ""), v["id"])))
            output.write_text('{"ratings":[\\n' + body + "\\n]}\\n", encoding="utf-8")
            print(f"CHANGED={'true' if merged != old else 'false'}")
            note(f"browser refresh parsed {len(rows)} ratings; total stored: {len(merged)}")
            return 0
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
