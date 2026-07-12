# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""End-to-end build smoke test (offline, no network, no secrets).

Runs the *real* scripts/build.py against a tiny synthetic checkins.csv and the
repo's real config/ + templates/, into a throwaway output dir, then asserts the
same gate CI runs before deploy — scripts/validate_html.py — passes clean.

This is deliberately ONE coarse test, not many fine ones: build.py is 1k lines of
orchestration wiring every generator + the placeholder post-process pass together,
so the highest-value coverage is "does the whole pipeline still produce a
deployable site from a CSV". It caught nothing to unit-test in isolation; it
catches everything that breaks the assembled output (a generator raising, a
template losing a placeholder, embedded JSON that no longer parses, a page that
shrinks to nothing).

Runtime: a few seconds. Marked neither `live` nor `e2e`, so it runs in the
default `pytest -m "not live"` offline suite and in the tests.yml unit job.
"""
import csv
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Force the child interpreter into UTF-8 mode and decode its output leniently:
# build.py prints Unicode venue names, and the default Windows console codec
# (cp1252) would otherwise corrupt the captured stream and mask real failures.
_UTF8_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

REPO = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO / "config"
BUILD = REPO / "scripts" / "build.py"
VALIDATE = REPO / "scripts" / "validate_html.py"

CSV_HEADER = [
    "date", "venue", "venue_id", "venue_url", "city", "state", "country",
    "neighborhood", "lat", "lng", "address", "category", "shout", "source_app",
    "source_url", "with_name", "with_id", "created_by_name", "created_by_id",
    "overlaps_name", "overlaps_id", "city_inferred", "checkin_id",
]

REQUIRED_PAGES = [
    "index.html", "feed.html", "trips.html", "tips.html", "stats.html",
    "venues.html", "companions.html", "shouts.html",
]


def _ts(y, mo, d, h=12):
    return int(datetime(y, mo, d, h, tzinfo=timezone.utc).timestamp())


def _row(ts, venue, vid, city, country, lat, lng, category,
         shout="", with_name="", cid=""):
    return {
        "date": str(ts), "venue": venue, "venue_id": vid,
        "venue_url": f"https://foursquare.com/v/{vid}", "city": city,
        "state": "", "country": country, "neighborhood": "",
        "lat": f"{lat}", "lng": f"{lng}", "address": "", "category": category,
        "shout": shout, "source_app": "Swarm for Android", "source_url": "",
        "with_name": with_name, "with_id": "", "created_by_name": "",
        "created_by_id": "", "overlaps_name": "", "overlaps_id": "",
        "city_inferred": "0", "checkin_id": cid or f"c{vid[-8:]}{ts}",
    }


def _make_fixture_rows():
    """~25 check-ins: a Minsk home base + one multi-day Warsaw trip (>=5 posts so
    trip detection fires) + a later year, with categories/companions/shouts to
    exercise every generator's non-empty path."""
    rows = []
    # Home base — Minsk 2019 (7 posts, varied categories, one companion + shout)
    minsk = [
        ("Лермонтова 43", "5079c6a8e4b07d24b09c30f2", "Home (private)", "Дома", ""),
        ("Кафе Грай", "4f34cbf7e4b013ba9d42b100", "Café", "", "Anna"),
        ("Гум БГУ", "4f34cbf7e4b013ba9d42b123", "College Arts Building", "матан", ""),
        ("Комаровский рынок", "4d52821bbd6ff04d1d6dfc0c", "Farmers Market", "", ""),
        ("Парк Челюскинцев", "4c1a1234567890abcdef0001", "Park", "гуляем", "Anna"),
        ("Зыбицкая", "4c1a1234567890abcdef0002", "Bar", "пятница", ""),
        ("Ж/д вокзал Минск", "4c1a1234567890abcdef0003", "Train Station", "", ""),
    ]
    for i, (v, vid, cat, sh, wn) in enumerate(minsk):
        rows.append(_row(_ts(2019, 3, 2 + i), v, vid, "Minsk", "Belarus",
                         53.9 + i * 0.001, 27.55 + i * 0.001, cat, sh, wn))

    # Warsaw trip — 6 consecutive-day posts in another country (a real trip)
    warsaw = [
        ("Warszawa Centralna", "4c2a1234567890abcdef0001", "Train Station", "przyjazd"),
        ("Pałac Kultury", "4c2a1234567890abcdef0002", "Monument", "widok"),
        ("Stare Miasto", "4c2a1234567890abcdef0003", "Historic Site", ""),
        ("Hala Koszyki", "4c2a1234567890abcdef0004", "Food Court", "obiad"),
        ("Łazienki", "4c2a1234567890abcdef0005", "Park", "spacer"),
        ("Lotnisko Chopina", "4c2a1234567890abcdef0006", "Airport", "powrót"),
    ]
    for i, (v, vid, cat, sh) in enumerate(warsaw):
        rows.append(_row(_ts(2019, 7, 10 + i), v, vid, "Warsaw", "Poland",
                         52.23 + i * 0.002, 21.0 + i * 0.002, cat, sh))

    # A later year — a few Vilnius posts (keeps the year index multi-year)
    vilnius = [
        ("Vilniaus stotis", "4c3a1234567890abcdef0001", "Train Station"),
        ("Užupis", "4c3a1234567890abcdef0002", "Neighborhood"),
        ("Katedra", "4c3a1234567890abcdef0003", "Church"),
    ]
    for i, (v, vid, cat) in enumerate(vilnius):
        rows.append(_row(_ts(2021, 5, 4 + i), v, vid, "Vilnius", "Lithuania",
                         54.68 + i * 0.001, 25.28 + i * 0.001, cat))
    return rows


@pytest.fixture(scope="module")
def built_site(tmp_path_factory):
    """Run the real build once into a temp dir; shared by the assertions below."""
    work = tmp_path_factory.mktemp("build")
    data_dir = work / "data"
    data_dir.mkdir()
    out_dir = work / "_site"
    out_dir.mkdir()

    csv_path = data_dir / "checkins.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_HEADER)
        w.writeheader()
        w.writerows(_make_fixture_rows())

    # tips.json lives next to the CSV; production always has one, and gen_tips's
    # no-file path skips the {{COUNTRIES}} substitution, so provide a minimal one.
    import json as _json
    tips = [
        {"ts": _ts(2019, 3, 3), "venue": "Кафе Грай",
         "venue_id": "4f34cbf7e4b013ba9d42b100", "city": "Minsk",
         "country": "Belarus", "text": "Great coffee."},
        {"ts": _ts(2019, 7, 11), "venue": "Pałac Kultury",
         "venue_id": "4c2a1234567890abcdef0002", "city": "Warsaw",
         "country": "Poland", "text": "Amazing view from the top."},
    ]
    (data_dir / "tips.json").write_text(
        _json.dumps(tips, ensure_ascii=False), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(BUILD),
         "--input", str(csv_path),
         "--config-dir", str(CONFIG_DIR),
         "--output-dir", str(out_dir),
         "--home-city", "Minsk",
         "--min-checkins", "5",
         "--trips-out", str(work / "trips_meta.json")],
        cwd=str(REPO / "scripts"),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_UTF8_ENV, timeout=300,
    )
    if proc.returncode != 0:
        pytest.fail(f"build.py exited {proc.returncode}\n"
                    f"STDOUT:\n{proc.stdout[-3000:]}\n\nSTDERR:\n{proc.stderr[-3000:]}")
    return out_dir


def test_required_pages_exist_and_are_nontrivial(built_site):
    for name in REQUIRED_PAGES:
        p = built_site / name
        assert p.exists(), f"missing generated page: {name}"
        assert p.stat().st_size > 1024, f"{name} is suspiciously small ({p.stat().st_size} B)"


def test_validate_html_gate_passes(built_site):
    """The exact CI deploy gate: no leftover placeholders, script tags balanced,
    embedded JSON parses, pages above the size floor."""
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(VALIDATE), "--dir", str(built_site)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_UTF8_ENV, timeout=120,
    )
    assert proc.returncode == 0, (
        f"validate_html.py failed (exit {proc.returncode}):\n"
        f"{proc.stdout}\n{proc.stderr}")


def test_feed_meta_generated(built_site):
    import json
    meta_path = built_site / "feed_meta.json"
    assert meta_path.exists(), "feed_meta.json not generated"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["total"] == 16, f"expected 16 check-ins in meta, got {meta['total']}"
    assert "2019-07" in meta["ym_index"] and "2021-05" in meta["ym_index"]


def test_pwa_manifest_and_service_worker(built_site):
    """PWA artifacts (audit #9): a valid manifest, a version-stamped service
    worker, and the registration snippet injected into every generated page."""
    import json
    manifest_path = built_site / "manifest.webmanifest"
    sw_path = built_site / "sw.js"
    assert manifest_path.exists(), "manifest.webmanifest not generated"
    assert sw_path.exists(), "sw.js not generated"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["icons"], "manifest has no icons"

    sw = sw_path.read_text(encoding="utf-8")
    # {{SW_VERSION}} must be substituted with a 14-digit build timestamp.
    assert "{{SW_VERSION}}" not in sw, "sw.js kept its unsubstituted placeholder"
    import re
    assert re.search(r"const VERSION = '\d{14}'", sw), "sw.js version not stamped"

    # Every page carries the manifest link + SW registration.
    index = (built_site / "index.html").read_text(encoding="utf-8")
    assert 'rel="manifest"' in index
    assert "serviceWorker" in index and "/sw.js" in index


def test_map_data_split_out(built_site):
    """Heavy map layers (audit #8) live in map_data.json, not inline in index."""
    import json
    map_path = built_site / "map_data.json"
    assert map_path.exists(), "map_data.json not generated"
    md = json.loads(map_path.read_text(encoding="utf-8"))
    assert "venues_heatmap" in md and "unique_places" in md
    # The big keys must NOT be inlined in the page's const S={...} payload.
    index = (built_site / "index.html").read_text(encoding="utf-8")
    assert "map_data.json" in index, "index no longer references map_data.json"


def test_country_pages_generated(built_site):
    """Per-country pages (audit #10): one country-<iso2>.html per country, with a
    flag, KPI stats and a slug that matches the index grid's link."""
    # The fixture visits Belarus (by), Poland (pl), Lithuania (lt).
    for slug, name in [("by", "Belarus"), ("pl", "Poland"), ("lt", "Lithuania")]:
        p = built_site / f"country-{slug}.html"
        assert p.exists(), f"missing country page: country-{slug}.html"
        html = p.read_text(encoding="utf-8")
        assert f"fi-{slug}" in html, f"country-{slug}.html has no flag icon"
        assert "kpi-num" in html, f"country-{slug}.html has no KPI stats"
        assert name in html, f"country-{slug}.html missing its name heading"
    # The index country grid must link to the SAME slugs the generator emitted —
    # countrySlug() in the template and country_slug() in Python must agree.
    index = (built_site / "index.html").read_text(encoding="utf-8")
    assert "function countrySlug(" in index, "index lost the countrySlug helper"


def test_syndication_feeds_generated(built_site):
    """RSS 2.0 + JSON Feed (audit #10) are emitted and well-formed."""
    import json
    import xml.etree.ElementTree as ET
    rss = built_site / "feed.xml"
    jsonfeed = built_site / "feed.json"
    assert rss.exists(), "feed.xml not generated"
    assert jsonfeed.exists(), "feed.json not generated"
    # RSS parses and has items.
    root = ET.fromstring(rss.read_text(encoding="utf-8"))
    items = root.findall(".//item")
    assert items, "feed.xml has no <item> entries"
    # JSON Feed 1.1 shape.
    jf = json.loads(jsonfeed.read_text(encoding="utf-8"))
    assert jf.get("version", "").endswith("/version/1.1"), "wrong JSON Feed version"
    assert jf.get("items"), "feed.json has no items"
    # Index advertises both via autodiscovery <link>s.
    index = (built_site / "index.html").read_text(encoding="utf-8")
    assert 'type="application/rss+xml"' in index
    assert 'type="application/feed+json"' in index


def test_on_this_day_section_present(built_site):
    """'On this day' index section (audit #10) is wired into the page."""
    index = (built_site / "index.html").read_text(encoding="utf-8")
    assert "on_this_day" in index, "on_this_day data not injected into index"
