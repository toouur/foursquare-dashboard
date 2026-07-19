#!/usr/bin/env python3
"""Scrape concert-related threads from forum.md using a real browser (Playwright).

forum.md serves HTTP 403 to plain HTTP clients (curl / requests / WebFetch) — it
has JS-based anti-bot protection — so this uses the pre-installed Chromium via
Playwright to render pages like a normal browser.

Run in a Claude Code cloud session whose environment has network access set to
**Full** or **Custom** with `forum.md` + `*.forum.md` allowed (the default
`Trusted` policy blocks the host at the egress proxy).

Usage:
    python scripts/scrape_forum_concerts.py                 # concerts rubric -> JSON on stdout
    python scripts/scrape_forum_concerts.py --out out.json  # write to file
    python scripts/scrape_forum_concerts.py --url https://m.forum.md/ru/themes/ --list-sections
    python scripts/scrape_forum_concerts.py --thread https://forum.md/ru/2141709   # dump one thread

Chromium is found automatically (PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers is
preconfigured in the cloud image). No `playwright install` needed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

# Rubric ("рубрика") listing pages worth scanning for concert content.
CONCERTS_RUBRIC = "https://forum.md/ru/koncerti/"   # dedicated "Концерты" section
THEMES_INDEX = "https://m.forum.md/ru/themes/"       # top-level section index

# Keywords that mark a thread as concert/gig related (case-insensitive, RU + EN).
CONCERT_KW = re.compile(
    r"концерт|koncert|выступ|гастрол|тур\b|fest|фест|афиш|билет|"
    r"live|gig|group|групп|band|singer|певиц|певец|музык|music",
    re.IGNORECASE,
)


def _new_page(pw, headless=True):
    browser = pw.chromium.launch(headless=headless)
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        ),
        locale="ru-RU",
        viewport={"width": 412, "height": 915},
    )
    return browser, ctx.new_page()


def _goto(page, url, wait_ms=2500):
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    # let anti-bot JS settle / lazy content render
    page.wait_for_timeout(wait_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass


def _extract_links(page, base_host_ok=("forum.md",)):
    """Return [{title, url, meta}] for anchor tags that look like thread links.

    forum.md thread URLs look like /ru/<digits>[/<page>]. We keep anchors whose
    href matches that shape and whose visible text is non-trivial.
    """
    anchors = page.eval_on_selector_all(
        "a[href]",
        """els => els.map(a => ({
            href: a.href,
            text: (a.textContent || '').trim().replace(/\\s+/g, ' '),
            title: a.getAttribute('title') || ''
        }))""",
    )
    thread_re = re.compile(r"/ru/(\d{4,})(?:/\d+)?/?$")
    seen, out = set(), []
    for a in anchors:
        href = a["href"]
        if not any(h in href for h in base_host_ok):
            continue
        m = thread_re.search(href)
        if not m:
            continue
        text = a["text"] or a["title"]
        if len(text) < 4:
            continue
        key = m.group(1)
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": key, "title": text, "url": href})
    return out


def scrape_listing(url, only_concerts=False, headless=True):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _new_page(pw, headless=headless)
        try:
            _goto(page, url)
            page_title = page.title()
            links = _extract_links(page)
        finally:
            browser.close()

    if only_concerts:
        links = [x for x in links if CONCERT_KW.search(x["title"])]
    return {"source": url, "page_title": page_title, "count": len(links), "threads": links}


def scrape_thread(url, headless=True):
    """Dump a single thread: title + first N posts' text."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _new_page(pw, headless=headless)
        try:
            _goto(page, url)
            title = page.title()
            # forum.md post bodies — selector may need tuning once we see real DOM.
            posts = page.eval_on_selector_all(
                ".message, .post, .msg, [class*='message'], [class*='post']",
                "els => els.slice(0, 20).map(e => (e.innerText || '').trim()).filter(t => t.length > 20)",
            )
        finally:
            browser.close()
    return {"source": url, "page_title": title, "posts": posts}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=CONCERTS_RUBRIC,
                    help=f"listing URL to scrape (default: {CONCERTS_RUBRIC})")
    ap.add_argument("--thread", help="scrape a single thread URL instead of a listing")
    ap.add_argument("--list-sections", action="store_true",
                    help=f"scrape the top-level themes index ({THEMES_INDEX}) instead")
    ap.add_argument("--all", action="store_true",
                    help="keep ALL threads from the listing (default: concert-keyword filtered)")
    ap.add_argument("--no-headless", action="store_true", help="show the browser (debug)")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    headless = not args.no_headless

    try:
        if args.thread:
            data = scrape_thread(args.thread, headless=headless)
        elif args.list_sections:
            data = scrape_listing(THEMES_INDEX, only_concerts=False, headless=headless)
        else:
            data = scrape_listing(args.url, only_concerts=not args.all, headless=headless)
    except Exception as e:  # noqa: BLE001 — surface the real cause (403 / timeout / missing browser)
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        print("Hints: (1) environment Network access must be Full or Custom(forum.md,*.forum.md); "
              "(2) if it's a 403/Cloudflare challenge the site is blocking automation — "
              "try --no-headless or a different entry URL.", file=sys.stderr)
        return 1

    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {data.get('count', len(data.get('posts', [])))} items -> {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
