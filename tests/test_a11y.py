# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Accessibility audit of the deployed site (axe-core via Playwright).

Injects axe-core into every page from tests.test_e2e_smoke.PAGES and fails on
NEW WCAG 2.0/2.1 A + AA violations with impact critical or serious.
Minor/moderate findings and the KNOWN_ISSUES baseline are printed as warnings
but do not fail the suite — the goal is a regression gate, not a zero-findings
mandate.

Runs in the weekly live suite:  pytest tests/ -m live
"""
import pytest

from test_e2e_smoke import PAGES, goto

pytestmark = [pytest.mark.live, pytest.mark.e2e]

AXE_CDN = "https://cdn.jsdelivr.net/npm/axe-core@4.10.2/axe.min.js"

# Impacts that fail the build. "moderate"/"minor" are reported only.
FAIL_IMPACTS = {"critical", "serious"}

# Pre-existing debt (baseline audit 2026-07): reported as advisory, not failing.
# Fix a rule site-wide → remove it here so the gate starts protecting it.
KNOWN_ISSUES = {
    "color-contrast",                # every page, dominated by companions.html
    "image-alt",                     # index photo strip
    "link-name",                     # index icon links
    "scrollable-region-focusable",   # index + stats scroll panes
    "select-name",                   # shouts filter dropdowns
}

_RUN_AXE = """
async () => {
    const r = await axe.run(document, {
        runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
    });
    return r.violations.map(v => ({
        id: v.id,
        impact: v.impact,
        help: v.help,
        count: v.nodes.length,
        sample: v.nodes.slice(0, 3).map(n => n.target.join(' ')),
    }));
}
"""


def _inject_axe(page):
    try:
        page.add_script_tag(url=AXE_CDN)
    except Exception as e:  # CDN unreachable — treat like the site being down
        pytest.skip(f"axe-core CDN unreachable: {e}")


def _fmt(violations):
    lines = []
    for v in violations:
        lines.append(f"  [{v['impact']}] {v['id']}: {v['help']} "
                     f"({v['count']} nodes, e.g. {', '.join(v['sample'])})")
    return "\n".join(lines)


@pytest.mark.parametrize("path", PAGES)
def test_page_has_no_serious_a11y_violations(page, path):
    goto(page, path)
    page.wait_for_load_state("load")
    _inject_axe(page)
    violations = page.evaluate(_RUN_AXE)

    failing = [v for v in violations
               if v["impact"] in FAIL_IMPACTS and v["id"] not in KNOWN_ISSUES]
    advisory = [v for v in violations
                if v["impact"] not in FAIL_IMPACTS or v["id"] in KNOWN_ISSUES]
    if advisory:
        print(f"\n{path} — advisory/baseline (non-failing) findings:\n{_fmt(advisory)}")
    assert not failing, (
        f"{path} has {len(failing)} NEW critical/serious accessibility "
        f"violation(s):\n{_fmt(failing)}")
