# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Cross-page layout-overflow guard (offline, no network, no secrets).

Builds the real site once (same tiny synthetic CSV as test_build_integration),
then renders every generated `*.html` in headless Chromium at a mobile and a
desktop viewport and fails on layout-overflow defects:

  A. no horizontal PAGE overflow (documentElement.scrollWidth > innerWidth) —
     the general "something is wider than the screen" bug;
  B. no vertically-CLIPPED fixed-height flex rows — e.g. the `.years` bar column
     on country/year pages, where a tall bar + its count/label got cut off by an
     `overflow-y` that computes to non-visible (the real country BY-YEAR defect);
  C. no heading (<h1>/<h2>) is horizontally CLIPPED — its single-line content
     cut off by a too-narrow / nowrap box (the masthead-style defect). A heading
     that WRAPS to several lines is fine (many hero headings do by design), so
     this flags clipping, not wrapping.

Playwright drives a local `file://` render, so there are no secrets and nothing
hits the network. Marked `e2e` but NOT `live`; it therefore runs in the default
`pytest -m "not live"` offline suite *when Playwright + a browser are present*,
and skips cleanly otherwise (importorskip + a guarded launch) so the plain unit
job never hard-fails on a missing browser binary.
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

# Whole module skips if the Playwright python package isn't installed.
sync_api = pytest.importorskip("playwright.sync_api")

# Reuse the build fixture's row/env helpers (same tests/ dir → importable).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_build_integration import (  # noqa: E402
    BUILD, CONFIG_DIR, CSV_HEADER, REPO, _UTF8_ENV, _make_fixture_rows, _ts,
)

pytestmark = pytest.mark.e2e

VIEWPORTS = {"mobile": (390, 844), "desktop": (1280, 900)}

# Detector runs in the page. Returns a list of human-readable violation strings.
_DETECTOR = r"""
() => {
  const V = [];
  const de = document.documentElement;
  // A) horizontal page overflow (tolerate 2px sub-pixel rounding)
  if (de.scrollWidth > window.innerWidth + 2) {
    V.push('page-h-overflow: scrollWidth=' + de.scrollWidth +
           ' > innerWidth=' + window.innerWidth);
  }
  // B) vertically-clipped fixed-height flex rows (.years bar columns)
  document.querySelectorAll('.years').forEach((el, i) => {
    if (el.scrollHeight > el.clientHeight + 2) {
      V.push('.years[' + i + '] clips content vertically: scrollHeight=' +
             el.scrollHeight + ' > clientHeight=' + el.clientHeight);
    }
  });
  // C) no heading is horizontally CLIPPED (text cut off inside its own box).
  //    A heading that wraps to several lines is fine (by design); one whose
  //    single-line content overflows its width — nowrap, or narrower than its
  //    text — is the masthead-style defect.
  document.querySelectorAll('h1, h2').forEach((h) => {
    if (h.offsetParent === null) return;          // display:none
    // Skip screen-reader-only / collapsed headings (sr-only pattern clips a
    // ~1px box on purpose) — only real, laid-out headings can be a defect.
    if (h.clientWidth < 24 || h.clientHeight < 8) return;
    if (h.scrollWidth > h.clientWidth + 2) {
      V.push(h.tagName.toLowerCase() + ' clipped horizontally: scrollWidth=' +
             h.scrollWidth + ' > clientWidth=' + h.clientWidth +
             ' text=' + JSON.stringify(h.textContent.trim().slice(0, 40)));
    }
  });
  return V;
}
"""


@pytest.fixture(scope="module")
def built_site(tmp_path_factory):
    """Run the real build once into a temp dir (mirrors test_build_integration)."""
    work = tmp_path_factory.mktemp("overflow_build")
    data_dir = work / "data"
    data_dir.mkdir()
    out_dir = work / "_site"
    out_dir.mkdir()

    csv_path = data_dir / "checkins.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_HEADER)
        w.writeheader()
        w.writerows(_make_fixture_rows())

    tips = [
        {"ts": _ts(2019, 3, 3), "venue": "Кафе Грай",
         "venue_id": "4f34cbf7e4b013ba9d42b100", "city": "Minsk",
         "country": "Belarus", "text": "Great coffee."},
    ]
    (data_dir / "tips.json").write_text(
        json.dumps(tips, ensure_ascii=False), encoding="utf-8")

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


@pytest.fixture(scope="module")
def browser():
    """Headless Chromium; skips the whole module if no browser binary is installed."""
    try:
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:  # browser not installed / cannot launch in this env
        pytest.skip(f"Playwright chromium unavailable: {e}")
    yield b
    b.close()
    pw.stop()


@pytest.mark.parametrize("vp_name", list(VIEWPORTS))
def test_no_layout_overflow(browser, built_site, vp_name):
    width, height = VIEWPORTS[vp_name]
    page = browser.new_page(viewport={"width": width, "height": height})

    problems = []
    try:
        for html in sorted(built_site.glob("*.html")):
            page.goto(html.as_uri(), wait_until="domcontentloaded", timeout=30000)
            # Let fonts/layout settle so wrap + scrollWidth measurements are stable.
            page.wait_for_timeout(150)
            vios = page.evaluate(_DETECTOR)
            for v in vios:
                problems.append(f"[{vp_name}] {html.name}: {v}")
    finally:
        page.close()

    assert not problems, "layout overflow defects:\n  " + "\n  ".join(problems)
