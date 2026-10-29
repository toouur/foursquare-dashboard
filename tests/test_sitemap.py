# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""seo.py — sitemap (extensionless URLs, real lastmod) and the per-page search/preview pass."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from build import SITE_URL
from seo import clean_url, finish_page, finish_site, page_lastmod, write_sitemap

NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
SITE = SITE_URL.rstrip("/")


def _urls(path):
    root = ET.parse(path).getroot()
    return {u.find("s:loc", NS).text: (u.find("s:lastmod", NS).text, u.find("s:priority", NS).text)
            for u in root.findall("s:url", NS)}


def test_clean_url():
    assert clean_url(SITE_URL, "index.html") == SITE + "/"
    assert clean_url(SITE_URL, "trips.html") == SITE + "/trips"
    assert clean_url(SITE, "city-minsk.html") == SITE + "/city-minsk"


def test_sitemap_lists_pages_extensionless_and_skips_private(tmp_path):
    for n in ("index.html", "trips.html", "year-2013.html", "country-md.html", "404.html",
              "visits.html", "search.html", "venue.html", "googleabc.html"):
        (tmp_path / n).write_text("<html></html>", encoding="utf-8")
    extra = tmp_path / "elsewhere"
    extra.mkdir()
    (extra / "solution.html").write_text("x", encoding="utf-8")
    n = write_sitemap(tmp_path, SITE_URL, {"year-2013.html": "2013-12-30"}, "2026-10-28",
                      extra=[extra / "solution.html", extra / "missing.html"])
    assert n == 5
    urls = _urls(tmp_path / "sitemap.xml")
    assert set(urls) == {SITE + p for p in ("/", "/trips", "/year-2013", "/country-md", "/solution")}
    assert urls[SITE + "/year-2013"] == ("2013-12-30", "0.6")
    assert urls[SITE + "/trips"] == ("2026-10-28", "0.8")


def test_page_lastmod_is_the_newest_checkin_there():
    rows = [{"date": "1388000000", "city": "Minsk", "country": "Belarus"},      # 2013-12-25
            {"date": "1700000000", "city": "Minsk", "country": "Belarus"},      # 2023-11-14
            {"date": "1600000000", "city": "Chișinău", "country": "Moldova"},   # 2020-09-13
            {"date": "", "city": "Minsk", "country": "Belarus"}]
    lm, newest = page_lastmod(rows, {"Minsk": "minsk"}, lambda c: c[:2].lower())
    assert newest == "2023-11-14"
    assert lm["city-minsk.html"] == "2023-11-14"
    assert lm["country-mo.html"] == "2020-09-13"
    assert lm["year-2013.html"] == "2013-12-25"
    assert "city-chisinau.html" not in lm          # no city page for it


PAGE = """﻿<!DOCTYPE html><html lang="en"><head><title>Minsk — Check-in Journal</title>
<meta name="description" content="20 check-ins in Minsk.">
<link rel="canonical" href="{site}/city-minsk.html">
<meta property="og:url" content="{site}/city-minsk.html">
<meta property="og:title" content="Minsk">
<meta name="twitter:card" content="summary">
</head><body><a href="{site}/index.html">home</a>
<img src="https://r2.example/pix/t/abc.jpg" alt="x"></body></html>"""


def test_finish_page_canonical_image_and_json_ld():
    out = finish_page(PAGE.format(site=SITE), "city-minsk.html", SITE_URL, SITE + "/og-card.png")
    assert not out.startswith("﻿")
    assert ".html" not in out.replace("og-card.png", "")
    assert f'<link rel="canonical" href="{SITE}/city-minsk">' in out
    assert f'href="{SITE}/"' in out
    # the page's own first photo, full size, wins over the generic card
    assert '<meta property="og:image" content="https://r2.example/pix/abc.jpg">' in out
    assert 'content="summary_large_image"' in out
    ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', out).group(1))
    assert ld["about"] == {"@type": "City", "name": "Minsk"} and ld["url"] == SITE + "/city-minsk"
    assert finish_page(out, "city-minsk.html", SITE_URL, SITE + "/og-card.png") == out   # idempotent


def test_finish_page_bare_page_gets_card_and_canonical():
    out = finish_page("<html><head><title>Stats</title></head><body></body></html>",
                      "stats.html", SITE_URL, SITE + "/og-card.png")
    assert f'<link rel="canonical" href="{SITE}/stats">' in out
    assert f'<meta property="og:image" content="{SITE}/og-card.png">' in out


def test_finish_site_writes_404_card_and_sitemap(tmp_path):
    (tmp_path / "trips.html").write_text("<html><head><title>T</title></head><body></body></html>",
                                         encoding="utf-8")
    rows = [{"date": "1700000000", "city": "Minsk", "country": "Belarus", "lat": "53.9", "lng": "27.56"}]
    n = finish_site(tmp_path, SITE_URL, rows, {}, lambda c: "by", "Check-in Journal", "1 check-in")
    assert n["sitemap"] == 2 and n["card"] == 1
    assert (tmp_path / "og-card.png").stat().st_size > 1000
    page_404 = (tmp_path / "404.html").read_text(encoding="utf-8")
    assert 'name="robots" content="noindex"' in page_404
    assert SITE + "/trips" in (tmp_path / "sitemap.xml").read_text(encoding="utf-8")
