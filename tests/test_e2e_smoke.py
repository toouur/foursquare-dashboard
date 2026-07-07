# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Playwright E2E smoke tests against the live site (https://4sq.pages.dev).

Uses the pytest-playwright `page` fixture (chromium, headless).
Marked `live` + `e2e`; deselect offline with:  pytest -m "not live"
Run just these with:                           pytest -m e2e

Smoke scope only: pages render, no unreplaced {{PLACEHOLDER}}s leak through
the build, no uncaught JS exceptions, global search + feed actually work
end-to-end (static HTML → Pages Function → D1).
"""
import re

import pytest

pytestmark = [pytest.mark.live, pytest.mark.e2e]

BASE = "https://4sq.pages.dev"

# Unreplaced build placeholders like {{CTRY_CODE_JSON}} — the build.py
# post-process pass must have substituted all of them.
PLACEHOLDER_RE = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")

PAGES = [
    "/",
    "/feed.html",
    "/trips.html",
    "/tips.html",
    "/shouts.html",
    "/companions.html",
    "/stats.html",
    "/venues.html",
]


def goto(page, path, **kw):
    kw.setdefault("wait_until", "domcontentloaded")
    try:
        return page.goto(BASE + path, **kw)
    except Exception as e:  # net::ERR_* — offline, DNS, etc.
        pytest.skip(f"live site unreachable: {e}")


class TestPagesRender:
    @pytest.mark.parametrize("path", PAGES)
    def test_page_loads_clean(self, page, path):
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        resp = goto(page, path)
        assert resp is not None and resp.status == 200, f"{path} → {resp and resp.status}"
        html = page.content()
        leftovers = PLACEHOLDER_RE.findall(html)
        assert not leftovers, f"unreplaced placeholders on {path}: {leftovers[:5]}"
        assert not errors, f"uncaught JS errors on {path}: {errors[:3]}"

    def test_index_kpis_populated(self, page):
        goto(page, "/")
        # Embedded data really made it into the page: km KPI is a number.
        txt = page.locator("#kpiKmNum").inner_text(timeout=15000)
        assert re.search(r"\d", txt), f"kpiKmNum empty: {txt!r}"


class TestGlobalSearch:
    def test_search_returns_results(self, page):
        goto(page, "/")
        page.click(".gs-trigger")
        page.fill("#gsInput", "minsk")
        # Debounced fetch to /api/search → rendered .gs-item anchors
        page.wait_for_selector("#gsResults .gs-item", timeout=20000)
        assert page.locator("#gsResults .gs-item").count() >= 1
        # inner_text() reflects CSS text-transform — compare case-insensitively
        heading = page.locator("#gsResults .gs-section-hd").first.inner_text()
        assert heading.lower() in {"venues", "cities", "trips", "tips", "people"}

    def test_search_no_results_message(self, page):
        goto(page, "/")
        page.click(".gs-trigger")
        page.fill("#gsInput", "zzqxjwv")
        page.wait_for_selector("#gsResults .gs-empty", timeout=20000)
        assert "No results" in page.locator("#gsResults .gs-empty").inner_text()

    def test_escape_closes_overlay(self, page):
        goto(page, "/")
        page.click(".gs-trigger")
        page.wait_for_selector("#gsOverlay.open", timeout=10000)
        page.keyboard.press("Escape")
        # Closed overlay is hidden — wait on class state, not visibility
        page.wait_for_function(
            "!document.getElementById('gsOverlay').classList.contains('open')",
            timeout=10000)


class TestFeed:
    def test_feed_cards_render_from_api(self, page):
        goto(page, "/feed.html")
        # Virtual scroll fills #vsWin with cards once /api/feed responds.
        page.wait_for_selector("#vsWin .fc-venue", timeout=30000)
        assert page.locator("#vsWin .fc-venue").count() >= 5

    def test_feed_scroll_renders_more(self, page):
        goto(page, "/feed.html")
        page.wait_for_selector("#vsWin .fc-venue", timeout=30000)
        first = page.locator("#vsWin .fc-venue").first.inner_text()
        # The virtual scroller is the #feedScroll container, not the window
        page.evaluate("document.getElementById('feedScroll').scrollTop = 6000")
        page.wait_for_function(
            "t => { const el = document.querySelector('#vsWin .fc-venue');"
            "       return el && el.innerText !== t; }",
            arg=first, timeout=10000)
        assert page.locator("#vsWin .fc-venue").count() >= 1
