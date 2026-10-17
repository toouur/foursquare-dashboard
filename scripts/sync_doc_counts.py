#!/usr/bin/env python3
"""Keep the test / source-file counts quoted in README.md, CLAUDE.md and
solution.html in step with the repo.

The three documents describe the same suite in different words, and every
hand edit used to update one or two of them (README said 413 tests while
CLAUDE.md still said 404). This script measures the real numbers once and
rewrites every known phrase in all three:

  total      every collected test
  offline    tests NOT marked `live` (the CI unit gate)
  live_api   `live` and not `e2e`  (API contract)
  live_e2e   `live` and `e2e`      (Playwright smoke + axe-core a11y)
  e2e_smoke  live_e2e minus the a11y module
  a11y       tests in tests/test_a11y.py
  files      Python files under scripts/ (what mypy's `files = scripts` checks)

Counts come from pytest's own collection (in-process, nothing runs), so a
marker or a skip-at-import changes them exactly as it changes the suite.
`tests/test_layout_overflow.py` does `importorskip("playwright.sync_api")` at
module level, so collect with pytest-playwright installed or its tests vanish
from the totals.

Exit codes: 0 docs in sync (or rewritten), 1 drift found under --check,
2 a known phrase was not found — a doc was reworded and its rule here needs
updating, which must fail loudly instead of letting that doc drift again.

Usage:
  python scripts/sync_doc_counts.py            # rewrite in place
  python scripts/sync_doc_counts.py --check    # report drift, write nothing
"""
from __future__ import annotations

import argparse
import contextlib
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (file, pattern, count key). Each pattern holds exactly one named group `n`;
# only that span is replaced, so the surrounding wording is never touched.
RULES: list[tuple[str, str, str]] = [
    # README.md
    ("README.md", r"\b(?P<n>\d+)-test pytest suite", "total"),
    ("README.md", r"\| Offline unit \+ parity \| [^|]*\| (?P<n>\d+) \|", "offline"),
    ("README.md", r"\| API contract \| `live` \| (?P<n>\d+) \|", "live_api"),
    ("README.md", r"\| `live` \+ `e2e` \| (?P<n>\d+) \|", "live_e2e"),
    ("README.md", r"clean across all (?P<n>\d+) files", "files"),
    # CLAUDE.md
    ("CLAUDE.md", r"Offline suite \((?P<n>\d+) unit", "offline"),
    ("CLAUDE.md", r"Live suite \((?P<n>\d+) API contract", "live_api"),
    ("CLAUDE.md", r"\+ (?P<n>\d+) Playwright E2E", "e2e_smoke"),
    ("CLAUDE.md", r"\+ (?P<n>\d+) axe-core a11y", "a11y"),
    ("CLAUDE.md", r"pytest suite \((?P<n>\d+) tests:", "total"),
    ("CLAUDE.md", r"tests: (?P<n>\d+) offline,", "offline"),
    ("CLAUDE.md", r"offline, (?P<n>\d+) live API contract", "live_api"),
    ("CLAUDE.md", r"live API contract, (?P<n>\d+) live\+e2e", "live_e2e"),
    ("CLAUDE.md", r"0 errors / (?P<n>\d+) files", "files"),
    # solution.html
    ("solution.html", r"\b(?P<n>\d+)-test pytest suite", "total"),
    ("solution.html", r"offline ring \((?P<n>\d+) tests", "offline"),
    ("solution.html", r"<strong>(?P<n>\d+) API contract tests", "live_api"),
    ("solution.html", r"<strong>(?P<n>\d+) Playwright E2E smoke tests", "e2e_smoke"),
    ("solution.html", r"<strong>(?P<n>\d+)-test axe-core", "a11y"),
    ("solution.html", r"clean over all (?P<n>\d+) script files", "files"),
    ("solution.html", r"\b(?P<n>\d+) tests · unit", "total"),
]


class _Collector:
    def __init__(self) -> None:
        self.items: list = []

    def pytest_collection_modifyitems(self, items):
        self.items = list(items)


def measure() -> dict[str, int]:
    import pytest

    col = _Collector()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = pytest.main([str(ROOT / "tests"), "--collect-only", "-q",
                          "-p", "no:cacheprovider"], plugins=[col])
    if rc not in (0, 5) or not col.items:
        sys.stderr.write(buf.getvalue())
        raise SystemExit(f"pytest collection failed (exit {rc})")

    def has(item, name):
        return item.get_closest_marker(name) is not None

    live = [i for i in col.items if has(i, "live")]
    live_e2e = [i for i in live if has(i, "e2e")]
    a11y = [i for i in live_e2e if i.path.name == "test_a11y.py"]
    return {
        "total": len(col.items),
        "offline": len(col.items) - len(live),
        "live_api": len(live) - len(live_e2e),
        "live_e2e": len(live_e2e),
        "e2e_smoke": len(live_e2e) - len(a11y),
        "a11y": len(a11y),
        "files": sum(1 for _ in (ROOT / "scripts").rglob("*.py")),
    }


def apply(counts: dict[str, int], write: bool) -> tuple[list[str], list[str]]:
    """Return (changes, missing) — human-readable lines for each."""
    changes: list[str] = []
    missing: list[str] = []
    texts = {f: (ROOT / f).read_bytes().decode("utf-8")
             for f in sorted({r[0] for r in RULES})}
    for fname, pattern, key in RULES:
        new_val = str(counts[key])
        text = texts[fname]
        hits = 0

        def repl(m: re.Match) -> str:
            nonlocal hits
            hits += 1
            old = m.group("n")
            if old != new_val:
                changes.append(f"{fname}: {key} {old} -> {new_val}  [{pattern}]")
            s, e = m.span("n")
            base = m.start()
            return m.group(0)[:s - base] + new_val + m.group(0)[e - base:]

        texts[fname] = re.sub(pattern, repl, text)
        if not hits:
            missing.append(f"{fname}: no match for {key} rule  [{pattern}]")
    if write and changes:
        for fname, text in texts.items():
            path = ROOT / fname
            if path.read_bytes().decode("utf-8") != text:
                path.write_bytes(text.encode("utf-8"))  # keeps BOM + CRLF as-is
    return changes, missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit 1 instead of rewriting")
    args = ap.parse_args()

    counts = measure()
    print("counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    changes, missing = apply(counts, write=not args.check)
    for line in changes:
        print(("DRIFT " if args.check else "UPDATED ") + line)
    for line in missing:
        print("MISSING " + line, file=sys.stderr)
    print(f"DOC_COUNTS_CHANGED={'true' if changes else 'false'}")
    if missing:
        return 2
    if args.check and changes:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
