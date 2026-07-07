# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Shared pytest fixtures. Puts scripts/ on sys.path so tests import the
modules exactly the way build.py does (flat `import transform`, not a package).
"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: hits the live https://4sq.pages.dev API (deselect with -m \"not live\")")
    config.addinivalue_line(
        "markers",
        "e2e: browser-based Playwright smoke tests against the live site")


def make_row(ts, city="", country="", category="", venue="", venue_id="",
             lat="", lng="", checkin_id="", **extra):
    """Minimal check-in row shaped like a checkins.csv DictReader row."""
    row = {
        "date": str(ts),
        "city": city,
        "country": country,
        "category": category,
        "venue": venue,
        "venue_id": venue_id,
        "lat": lat,
        "lng": lng,
        "checkin_id": checkin_id,
    }
    row.update(extra)
    return row
