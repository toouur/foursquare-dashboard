#!/usr/bin/env python3
"""Scrape concert-related threads from forum.md.

The live forum.md serves fully server-rendered HTML to a plain HTTP client
(``requests``/curl), so the default engine is ``requests`` — fast, dependency-
light, and it works behind the Claude Code agent proxy (it honours HTTPS_PROXY +
the CA bundle out of the box). A ``--engine browser`` fallback (Playwright /
Chromium) is kept for the day the site adds a JS/anti-bot wall, but note that in
the cloud image the headless browser cannot reach the MITM egress proxy, so the
browser path only works in an environment with direct outbound access.

Real forum.md URL shapes (as of 2026):
    - a rubric listing:  https://forum.md/koncerti/         (page 1)
                         https://forum.md/koncerti/2 ...     (pagination)
    - a single thread:   https://forum.md/<numeric-id>       (root-level id)
Thread pages expose the first post in ``.content-article-body-text`` with a date
in ``.content-article-header-date_posted`` (``dd.mm.yyyy``).

Usage:
    python scripts/scrape_forum_concerts.py                    # concerts rubric -> JSON on stdout
    python scripts/scrape_forum_concerts.py --out out.json     # write to file
    python scripts/scrape_forum_concerts.py --max-pages 57     # crawl the whole rubric
    python scripts/scrape_forum_concerts.py --thread https://forum.md/862389   # dump one thread
    python scripts/scrape_forum_concerts.py --engine browser   # Playwright fallback
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import re
import sys
import time

# Rubric ("рубрика") listing base worth scanning for concert content.
CONCERTS_RUBRIC = "https://forum.md/koncerti/"   # dedicated "Концерты" section
BASE = "https://forum.md"

UA = ("Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

# Keywords that mark a thread as concert/gig related (case-insensitive, RU + EN + RO).
CONCERT_KW = re.compile(
    r"концерт|koncert|concert|выступ|гастрол|тур\b|fest|фест|festival|афиш|афиша|"
    r"билет|bilet|live|gig|group|групп|band|singer|певиц|певец|музык|music|"
    r"spectacol|trupa|cantare",
    re.IGNORECASE,
)

# A root-level thread link: href="/<digits>" (id >= 3 digits to skip nav noise).
_THREAD_A_RE = re.compile(
    r'<a[^>]+href="/(\d{3,})"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


# --------------------------------------------------------------------------- #
# requests engine (default)
# --------------------------------------------------------------------------- #
def _session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
    return s


def _fetch(sess, url: str, retries: int = 4) -> str:
    # forum.md occasionally returns a transient 502/503 from its edge; retry
    # with exponential backoff before giving up.
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            r = sess.get(url, timeout=45)
            if r.status_code in (429, 500, 502, 503, 504):
                r.raise_for_status()
            r.raise_for_status()
            # forum.md is UTF-8; decode via apparent/declared charset.
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception as e:  # noqa: BLE001 — retry transient failures
            last_exc = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise last_exc  # type: ignore[misc]


def extract_threads(html: str) -> list[dict]:
    """Return [{id, title, url}] for root-level thread anchors on a listing page."""
    seen: set[str] = set()
    out: list[dict] = []
    for tid, inner in _THREAD_A_RE.findall(html):
        if tid in seen:
            continue
        title = _clean(inner)
        if len(title) < 4:
            continue
        seen.add(tid)
        out.append({"id": tid, "title": title, "url": f"{BASE}/{tid}"})
    return out


def _rubric_page_url(rubric: str, page: int) -> str:
    rubric = rubric.rstrip("/")
    return rubric + "/" if page <= 1 else f"{rubric}/{page}"


def scrape_listing_requests(rubric, only_concerts=False, max_pages=1, sleep=1.0):
    sess = _session()
    all_threads: list[dict] = []
    seen_ids: set[str] = set()
    page_title = ""
    pages_done = 0
    for page in range(1, max_pages + 1):
        url = _rubric_page_url(rubric, page)
        html = _fetch(sess, url)
        if not page_title:
            m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
            page_title = _clean(m.group(1)) if m else ""
        threads = extract_threads(html)
        fresh = [t for t in threads if t["id"] not in seen_ids]
        if not fresh:
            break  # clamp-repeat / past the last page
        for t in fresh:
            seen_ids.add(t["id"])
        all_threads.extend(fresh)
        pages_done = page
        if page < max_pages:
            time.sleep(sleep)
    if only_concerts:
        all_threads = [t for t in all_threads if CONCERT_KW.search(t["title"])]
    return {"source": rubric, "engine": "requests", "page_title": page_title,
            "pages_scanned": pages_done, "count": len(all_threads),
            "threads": all_threads}


# Russian genitive month stems → month number (stems avoid case endings).
_RU_MONTHS = [
    ("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("мая", 5),
    ("ма", 5), ("июн", 6), ("июл", 7), ("август", 8), ("сентябр", 9),
    ("октябр", 10), ("ноябр", 11), ("декабр", 12),
]


def parse_date(date_str: str):
    """Return (iso_date | '', year | None) from a forum.md date string.

    Handles the RU header form ("8 апреля 2011, 11:50") and dd.mm.yyyy.
    """
    if not date_str:
        return "", None
    s = date_str.lower()
    # dd.mm.yyyy
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}", y
    # "<day> <ru-month> <year>"
    ym = re.search(r"\b(19|20)\d\d\b", s)
    year = int(ym.group(0)) if ym else None
    dm = re.search(r"\b(\d{1,2})\b", s)
    day = int(dm.group(1)) if dm else None
    month = None
    for stem, num in _RU_MONTHS:
        if stem in s:
            month = num
            break
    if year and month and day:
        return f"{year:04d}-{month:02d}-{day:02d}", year
    return "", year


def _parse_thread_html(html: str, url: str) -> dict:
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    title = _clean(m.group(1)) if m else ""
    dm = re.search(
        r'content-article-header-date_posted[^>]*>(.*?)</', html, re.S | re.I)
    date = _clean(dm.group(1)) if dm else ""
    iso, year = parse_date(date)
    bm = re.search(
        r'content-article-body-text[^>]*>(.*?)</(?:div|article|section)>',
        html, re.S | re.I)
    body = _clean(bm.group(1)) if bm else ""
    return {"source": url, "page_title": title, "date": date,
            "date_iso": iso, "year": year, "body": body[:4000]}


def scrape_thread_requests(url: str) -> dict:
    sess = _session()
    html = _fetch(sess, url)
    d = _parse_thread_html(html, url)
    d["engine"] = "requests"
    return d


def scrape_details(threads, sleep=0.3, before=None, progress=True):
    """Fetch each listed thread, parse date/year/body, optionally keep year<before.

    ``threads`` is the list from a listing scrape ([{id,title,url}, ...]).
    Returns {count, before, kept, threads:[enriched]} — threads that fail to
    parse a year are kept when ``before`` is set (so a missing date is never
    silently dropped) and flagged with ``year=None``.
    """
    sess = _session()
    out = []
    total = len(threads)
    for i, t in enumerate(threads, 1):
        url = t.get("url") or f"{BASE}/{t['id']}"
        try:
            html = _fetch(sess, url)
            info = _parse_thread_html(html, url)
        except Exception as e:  # noqa: BLE001 — record the failure, keep going
            info = {"source": url, "page_title": "", "date": "",
                    "date_iso": "", "year": None, "body": "", "error": str(e)}
        row = {"id": t["id"], "title": t.get("title", "") or info["page_title"],
               "url": url, "date": info["date"], "date_iso": info["date_iso"],
               "year": info["year"], "body": info["body"]}
        if "error" in info:
            row["error"] = info["error"]
        out.append(row)
        if progress and (i % 50 == 0 or i == total):
            print(f"  ... {i}/{total} fetched", file=sys.stderr)
        if i < total:
            time.sleep(sleep)
    if before is not None:
        kept = [r for r in out if r["year"] is None or r["year"] < before]
    else:
        kept = out
    return {"engine": "requests", "before": before, "fetched": len(out),
            "count": len(kept), "threads": kept}


# --------------------------------------------------------------------------- #
# browser engine (Playwright fallback — direct-egress environments only)
# --------------------------------------------------------------------------- #
def _new_page(pw, headless=True):
    exe = os.environ.get("PW_CHROMIUM_PATH") or "/opt/pw-browsers/chromium"
    launch_kwargs = {"headless": headless}
    if os.path.exists(exe):
        launch_kwargs["executable_path"] = exe
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}
    browser = pw.chromium.launch(**launch_kwargs)
    ctx = browser.new_context(user_agent=UA, locale="ru-RU",
                              viewport={"width": 412, "height": 915},
                              ignore_https_errors=True)
    return browser, ctx.new_page()


def _goto(page, url, wait_ms=2000):
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(wait_ms)


def scrape_listing_browser(rubric, only_concerts=False, headless=True):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser, page = _new_page(pw, headless=headless)
        try:
            _goto(page, _rubric_page_url(rubric, 1))
            page_title = page.title()
            html = page.content()
        finally:
            browser.close()
    threads = extract_threads(html)
    if only_concerts:
        threads = [t for t in threads if CONCERT_KW.search(t["title"])]
    return {"source": rubric, "engine": "browser", "page_title": page_title,
            "pages_scanned": 1, "count": len(threads), "threads": threads}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=CONCERTS_RUBRIC,
                    help=f"rubric listing base to scrape (default: {CONCERTS_RUBRIC})")
    ap.add_argument("--thread", help="scrape a single thread URL instead of a listing")
    ap.add_argument("--all", action="store_true",
                    help="keep ALL threads (default: concert-keyword filtered)")
    ap.add_argument("--max-pages", type=int, default=1,
                    help="how many rubric pages to crawl (requests engine; default 1)")
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="seconds between page fetches (requests engine)")
    ap.add_argument("--engine", choices=("requests", "browser"), default="requests",
                    help="fetch engine (default: requests; browser needs direct egress)")
    ap.add_argument("--details", action="store_true",
                    help="fetch each thread's date/body (heavy: one request per thread)")
    ap.add_argument("--in", dest="in_file",
                    help="reuse a saved listing JSON as the thread source for --details")
    ap.add_argument("--before", type=int,
                    help="with --details, keep only threads dated before this year")
    ap.add_argument("--no-headless", action="store_true", help="show the browser (debug)")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    try:
        if args.thread:
            data = scrape_thread_requests(args.thread)
        elif args.details:
            if args.in_file:
                with open(args.in_file, encoding="utf-8") as f:
                    threads = json.load(f).get("threads", [])
            else:
                listing = scrape_listing_requests(
                    args.url, only_concerts=not args.all,
                    max_pages=args.max_pages, sleep=args.sleep)
                threads = listing["threads"]
            data = scrape_details(threads, sleep=args.sleep, before=args.before)
        elif args.engine == "browser":
            data = scrape_listing_browser(
                args.url, only_concerts=not args.all,
                headless=not args.no_headless)
        else:
            data = scrape_listing_requests(
                args.url, only_concerts=not args.all,
                max_pages=args.max_pages, sleep=args.sleep)
    except Exception as e:  # noqa: BLE001 — surface the real cause
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        print("Hints: (1) requests engine needs forum.md reachable through the "
              "proxy/CA bundle; (2) the browser engine needs an environment with "
              "direct outbound egress (it cannot use the MITM proxy).",
              file=sys.stderr)
        return 1

    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        n = data.get("count", 1 if data.get("body") is not None else 0)
        print(f"wrote {n} item(s) -> {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
