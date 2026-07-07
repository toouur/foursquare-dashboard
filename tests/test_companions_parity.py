# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Py↔JS parity test for companion collection.

`metrics.collect_companions` (Python, build-time pages) and
`collectCompanions` in functions/api/feed.js (Cloudflare Pages Function,
/api/feed) must produce identical companion lists for the same row —
otherwise the static pages and the live feed disagree about who was there.

The JS function is extracted verbatim from feed.js (brace matching, no
duplication of logic in the test) and executed with node on shared fixture
rows; results are compared field-for-field against the Python side.
Skips when node is not installed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import metrics

ROOT = Path(__file__).resolve().parents[1]
FEED_JS = ROOT / "functions" / "api" / "feed.js"

# Shared fixture rows — shaped like a D1/CSV row with the three companion
# signal columns. Chosen to hit every branch: comma lists, the " ," spacing
# quirk, the "-" overlaps sentinel, case-insensitive dedup across fields,
# whitespace trimming, empty/None inputs, and non-ASCII names.
ROWS = [
    {"with_name": "Joanna"},
    {"with_name": "Joanna, Piotr", "overlaps_name": "joanna"},
    {"with_name": "A ,B"},
    {"with_name": "A,,B"},
    {"overlaps_name": "-"},
    {"overlaps_name": "X, -, Y"},
    {"created_by_name": "  Vika  "},
    {"with_name": "Vika", "created_by_name": "vika"},
    {"created_by_name": "-"},
    {"with_name": "Аня, Björk", "overlaps_name": "аня, Piotr"},
    {"with_name": "Joanna", "created_by_name": "Piotr", "overlaps_name": "Vika, joanna"},
    {"with_name": None, "created_by_name": None, "overlaps_name": None},
    {"with_name": "", "created_by_name": "", "overlaps_name": ""},
    {},
]

# Ground truth for a few rows so the test also fails if BOTH sides drift
# together (parity alone would still pass).
EXPECTED = {
    0: ["Joanna"],
    1: ["Joanna", "Piotr"],
    2: ["A", "B"],
    4: [],
    5: ["X", "Y"],
    7: ["Vika"],
    10: ["Joanna", "Piotr", "Vika"],
    13: [],
}


def _node_exe():
    exe = shutil.which("node")
    if exe:
        return exe
    fallback = Path("C:/Program Files/nodejs/node.exe")
    return str(fallback) if fallback.exists() else None


def _extract_collect_companions() -> str:
    """Pull the collectCompanions function source out of feed.js verbatim."""
    src = FEED_JS.read_text(encoding="utf-8")
    start = src.index("function collectCompanions")
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("unbalanced braces extracting collectCompanions")


def _run_js(rows, tmp_path) -> list[list[str]]:
    node = _node_exe()
    if not node:
        pytest.skip("node not installed — cannot run the JS side")
    harness = tmp_path / "harness.js"
    harness.write_text(
        _extract_collect_companions()
        + "\nconst rows = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        + "process.stdout.write(JSON.stringify(rows.map(collectCompanions)));\n",
        encoding="utf-8")
    proc = subprocess.run(
        [node, str(harness)],
        input=json.dumps(ROWS).encode("utf-8"),
        capture_output=True, timeout=30)
    assert proc.returncode == 0, f"node failed: {proc.stderr.decode(errors='replace')}"
    return json.loads(proc.stdout.decode("utf-8"))


def test_python_matches_expected():
    for i, want in EXPECTED.items():
        assert metrics.collect_companions(ROWS[i]) == want, f"row {i}: {ROWS[i]}"


def test_js_matches_python(tmp_path):
    js_results = _run_js(ROWS, tmp_path)
    py_results = [metrics.collect_companions(r) for r in ROWS]
    for i, (py, js) in enumerate(zip(py_results, js_results)):
        assert py == js, f"row {i} diverged: py={py} js={js} row={ROWS[i]}"
