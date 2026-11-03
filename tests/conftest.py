# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Shared pytest fixtures. Puts scripts/ on sys.path so tests import the
modules exactly the way build.py does (flat `import transform`, not a package).
"""
import os
import sys
from pathlib import Path

import pytest

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


# ── Skip guard ───────────────────────────────────────────────────────────────
# Live tests skip when the site, API or axe-core CDN is unreachable, so an
# outage can come back all green. With LIVE_MAX_SKIPS=N (set in tests.yml's
# live job) more than N skipped `live` tests fail the session. Unset locally,
# where an offline laptop should still skip quietly.
_live_skips: list[str] = []


def pytest_runtest_logreport(report):
    if report.skipped and "live" in report.keywords:
        _live_skips.append(report.nodeid)


def _skip_limit():
    raw = os.environ.get("LIVE_MAX_SKIPS", "").strip()
    return int(raw) if raw.isdigit() else None


def pytest_sessionfinish(session, exitstatus):
    limit = _skip_limit()
    if limit is not None and len(_live_skips) > limit and exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter):
    limit = _skip_limit()
    if limit is not None and len(_live_skips) > limit:
        terminalreporter.write_line(
            f"FAILED skip guard: {len(_live_skips)} live tests skipped (LIVE_MAX_SKIPS={limit}), "
            f"e.g. {_live_skips[0]}", red=True)


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
