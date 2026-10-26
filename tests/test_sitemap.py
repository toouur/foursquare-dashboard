# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""build.write_sitemap — every generated page, minus the private / shell ones."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from build import SITE_URL, write_sitemap


def test_sitemap_lists_pages_and_skips_private(tmp_path):
    for n in ("index.html", "trips.html", "year-2013.html", "country-md.html",
              "visits.html", "search.html", "venue.html", "googleabc.html"):
        (tmp_path / n).write_text("<html></html>", encoding="utf-8")
    extra = tmp_path / "elsewhere"
    extra.mkdir()
    (extra / "solution.html").write_text("x", encoding="utf-8")
    assert write_sitemap(tmp_path, extra=[extra / "solution.html", extra / "missing.html"]) == 5
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    root = ET.parse(tmp_path / "sitemap.xml").getroot()
    locs = [u.find("s:loc", ns).text for u in root.findall("s:url", ns)]
    assert locs[0] == SITE_URL
    assert set(locs[1:]) == {SITE_URL + n for n in ("trips.html", "year-2013.html", "country-md.html", "solution.html")}
    prio = {u.find("s:loc", ns).text: u.find("s:priority", ns).text for u in root.findall("s:url", ns)}
    assert prio[SITE_URL + "year-2013.html"] == "0.6" and prio[SITE_URL + "trips.html"] == "0.8"
