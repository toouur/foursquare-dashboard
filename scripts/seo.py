#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""seo.py — the last pass over the built site, for search engines and link previews.

Cloudflare Pages answers every `/<page>.html` with a 308 to `/<page>`. Pages that
named the `.html` address as their canonical (and a sitemap that listed it) sent
Google in a circle: the canonical is a redirect to the page that names it. So the
absolute site URLs in every page become the extensionless ones, and the sitemap
lists those with a real lastmod (the page's newest check-in, not the build day).

It also gives every page a preview image (og:image: the page's first photo, else
a generated card with the check-in dot map), a WebPage JSON-LD block where the
page has none, and writes 404.html — without it Pages serves index.html with a
200 for any unknown path, which Google reads as endless copies of the home page.
"""

from __future__ import annotations

import html
import json
import math
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

# Not for search engines: the private visit counter, the search shell, the venue
# page (one empty shell filled from #id) and the 404 page.
SITEMAP_SKIP = {"visits.html", "search.html", "venue.html", "index.html", "404.html"}
OG_CARD = "og-card.png"
SITE_NAME = "Check-in Journal"


def clean_url(site_url: str, name: str) -> str:
    """The address Pages serves `name` at: `trips.html` → `<site>/trips`, index → `<site>/`."""
    site = site_url.rstrip("/")
    stem = name[:-5] if name.endswith(".html") else name
    return f"{site}/" if stem in ("index", "") else f"{site}/{stem}"


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def page_lastmod(rows: list[dict], city_pages: dict[str, str],
                 country_slug: Callable[[str], str]) -> tuple[dict[str, str], str]:
    """({page name: date of its newest check-in}, the newest check-in overall).

    City, country and year pages change only when a check-in lands there; the
    rest (home, trips, stats…) with every new check-in."""
    newest: dict[str, int] = {}

    def bump(name: str, ts: int) -> None:
        if ts > newest.get(name, 0):
            newest[name] = ts

    last = 0
    for r in rows:
        try:
            ts = int(r.get("date") or 0)
        except (TypeError, ValueError):
            continue
        if ts <= 0:
            continue
        last = max(last, ts)
        bump(f"year-{_day(ts)[:4]}.html", ts)
        country = (r.get("country") or "").strip()
        if country:
            bump(f"country-{country_slug(country)}.html", ts)
        slug = city_pages.get((r.get("city") or "").strip())
        if slug:
            bump(f"city-{slug}.html", ts)
    overall = _day(last) if last else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {n: _day(t) for n, t in newest.items()}, overall


def _sitemap_priority(name: str) -> str:
    if name.startswith(("year-", "country-")):
        return "0.6"
    return "0.5" if name == "solution.html" else "0.8"


def write_sitemap(out_dir: Path, site_url: str, lastmod: dict[str, str], default_day: str,
                  extra: list | None = None) -> int:
    """sitemap.xml for every generated page (+ tracked extras that exist); returns the URL count."""
    names = {p.name for p in out_dir.glob("*.html")} | {Path(p).name for p in (extra or []) if Path(p).exists()}
    pages = sorted(n for n in names if n not in SITEMAP_SKIP and not n.startswith("google"))
    urls = [f"  <url><loc>{clean_url(site_url, 'index.html')}</loc><lastmod>{default_day}</lastmod>"
            "<priority>1.0</priority></url>"]
    urls += [f"  <url><loc>{clean_url(site_url, n)}</loc><lastmod>{lastmod.get(n, default_day)}</lastmod>"
             f"<priority>{_sitemap_priority(n)}</priority></url>" for n in pages]
    (out_dir / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) + "\n</urlset>\n", encoding="utf-8")
    return len(urls)


def make_og_card(rows: list[dict], out_path: Path, headline: str, subline: str) -> bool:
    """A 1200×630 link-preview card: every check-in as a dot on a world grid, plus two lines of text."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    w, h = 1200, 630
    img = Image.new("RGB", (w, h), (11, 13, 19))
    dots = Image.new("L", (w, h), 0)
    # Equirectangular around where the check-ins are (1st–99th percentile, so one
    # far-off trip does not shrink the rest to a speck), above the text band.
    map_h, pad = 470, 28
    pts = []
    for r in rows:
        try:
            pts.append((float(r["lat"]), float(r["lng"])))
        except (KeyError, TypeError, ValueError):
            continue
    if pts:
        las, los = sorted(p[0] for p in pts), sorted(p[1] for p in pts)
        lo0, lo1 = los[len(los) // 100], los[-1 - len(los) // 100]
        la0, la1 = las[len(las) // 100], las[-1 - len(las) // 100]
        kx = math.cos(math.radians((la0 + la1) / 2))       # keep the shapes, not the box
        scale = min((w - 2 * pad) / max((lo1 - lo0) * kx, 1e-6), (map_h - 2 * pad) / max(la1 - la0, 1e-6))
        cx, cy = (lo0 + lo1) / 2, (la0 + la1) / 2
        seen: dict[tuple[int, int], int] = {}
        for la, lo in pts:
            x, y = int(w / 2 + (lo - cx) * kx * scale), int(map_h / 2 - (la - cy) * scale)
            if 0 <= x < w and 0 <= y < map_h:
                seen[(x // 3, y // 3)] = seen.get((x // 3, y // 3), 0) + 1
        dd = ImageDraw.Draw(dots)
        for (gx, gy), n in seen.items():
            x, y, rad = gx * 3 + 1, gy * 3 + 1, 2 if n < 5 else 3
            dd.ellipse((x - rad, y - rad, x + rad, y + rad), fill=min(255, 110 + int(40 * math.log(n + 1))))
    img.paste(Image.new("RGB", (w, h), (232, 184, 109)), mask=dots)
    d = ImageDraw.Draw(img)
    d.rectangle((0, map_h, w, h), fill=(18, 21, 31))
    d.line((0, map_h, w, map_h), fill=(232, 184, 109), width=2)

    def font(size: int):
        try:
            return ImageFont.load_default(size=size)
        except TypeError:                        # Pillow < 10.1: no sizes
            return ImageFont.load_default()
    d.text((48, map_h + 22), headline, font=font(58), fill=(205, 213, 240))
    d.text((50, map_h + 98), subline, font=font(28), fill=(135, 144, 180))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return True


_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_DESC = re.compile(r'<meta name="description" content="([^"]*)"')
_PIX_IMG = re.compile(r'<img[^>]*\ssrc="(https?://[^"]+/pix/[^"]+\.(?:jpe?g|png|webp))"', re.I)


def _json_ld(name: str, title: str, desc: str, url: str, site_url: str) -> str:
    page: dict = {"@context": "https://schema.org", "@type": "WebPage", "name": title, "url": url,
                  "isPartOf": {"@type": "WebSite", "name": SITE_NAME, "url": clean_url(site_url, "index.html")}}
    if desc:
        page["description"] = desc
    place = title.split(" — ")[0].strip()
    if name.startswith("city-"):
        page["about"] = {"@type": "City", "name": place}
    elif name.startswith("country-"):
        page["about"] = {"@type": "Country", "name": place}
    body = json.dumps(page, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/ld+json">{body}</script>'


def finish_page(text: str, name: str, site_url: str, card_url: str) -> str:
    """One page's search/preview fixes; idempotent."""
    site = re.escape(site_url.rstrip("/"))
    text = text.lstrip("﻿")
    # absolute links to our own pages → the extensionless address Pages serves
    text = re.sub(site + r"/([A-Za-z0-9_-]+)\.html", lambda m: clean_url(site_url, m.group(1) + ".html"), text)
    if "</head>" not in text:
        return text
    head, rest = text.split("</head>", 1)
    url = clean_url(site_url, name)
    title = html.unescape(m.group(1).strip()) if (m := _TITLE.search(head)) else SITE_NAME
    desc = html.unescape(m.group(1)) if (m := _DESC.search(head)) else ""
    add = []
    if 'rel="canonical"' not in head:
        add.append(f'<link rel="canonical" href="{url}">')
    if "og:title" not in head:
        add.append(f'<meta property="og:title" content="{html.escape(title)}">')
        add.append(f'<meta property="og:url" content="{url}">')
        add.append('<meta property="og:type" content="website">')
        if desc:
            add.append(f'<meta property="og:description" content="{html.escape(desc)}">')
    if "og:site_name" not in head:
        add.append(f'<meta property="og:site_name" content="{SITE_NAME}">')
    if "og:image" not in head:
        m = _PIX_IMG.search(rest)
        image = m.group(1).replace("/pix/t/", "/pix/") if m else card_url
        if image:
            add.append(f'<meta property="og:image" content="{html.escape(image)}">')
            if image == card_url:
                add.append('<meta property="og:image:width" content="1200">'
                           '<meta property="og:image:height" content="630">')
    if "og:image" in head or any("og:image" in a for a in add):
        head = head.replace('<meta name="twitter:card" content="summary">',
                            '<meta name="twitter:card" content="summary_large_image">')
        if "twitter:card" not in head:
            add.append('<meta name="twitter:card" content="summary_large_image">')
    if "application/ld+json" not in head:
        add.append(_json_ld(name, title, desc, url, site_url))
    return head + "".join(add) + "</head>" + rest if add else head + "</head>" + rest


PAGE_404 = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not found — {site_name}</title>
<meta name="description" content="This page does not exist.">
<meta name="robots" content="noindex">
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0b0d13;
color:#cdd5f0;font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;text-align:center;padding:24px}}
h1{{font-size:56px;margin:0;color:#e8b86d;letter-spacing:.04em}}p{{color:#8790b4;margin:8px 0 24px}}
a{{color:#4ecdc4;text-decoration:none;margin:0 10px}}a:hover{{text-decoration:underline}}
</style>
</head>
<body>
<main>
<h1>404</h1>
<p>No check-ins at this address.</p>
<nav><a href="/">Home</a><a href="/trips">Trips</a><a href="/world_cities">Cities</a><a href="/search">Search</a></nav>
</main>
</body>
</html>
"""


def finish_site(out_dir: Path, site_url: str, rows: list[dict], city_pages: dict[str, str],
                country_slug: Callable[[str], str], headline: str, subline: str,
                extra: list | None = None) -> dict[str, int]:
    """Run every step over a built site; returns counters for the build log."""
    card_url = ""
    if make_og_card(rows, out_dir / OG_CARD, headline, subline):
        card_url = f"{site_url.rstrip('/')}/{OG_CARD}"
    (out_dir / "404.html").write_text(PAGE_404.format(site_name=SITE_NAME), encoding="utf-8")
    pages = 0
    for p in out_dir.glob("*.html"):
        if p.name == "404.html" or p.name.startswith("google"):
            continue
        txt = p.read_text(encoding="utf-8")
        new = finish_page(txt, p.name, site_url, card_url)
        if new != txt:
            p.write_text(new, encoding="utf-8")
            pages += 1
    for feed in ("feed.xml", "feed.json"):
        fp = out_dir / feed
        if fp.exists():
            txt = fp.read_text(encoding="utf-8")
            site = re.escape(site_url.rstrip("/"))
            new = re.sub(site + r"/([A-Za-z0-9_-]+)\.html", lambda m: clean_url(site_url, m.group(1) + ".html"), txt)
            if new != txt:
                fp.write_text(new, encoding="utf-8")
    lastmod, newest = page_lastmod(rows, city_pages, country_slug)
    urls = write_sitemap(out_dir, site_url, lastmod, newest, extra=extra)
    return {"pages": pages, "sitemap": urls, "card": int(bool(card_url))}
