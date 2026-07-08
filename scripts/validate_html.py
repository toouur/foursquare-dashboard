# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Post-build HTML validation gate.

Run right after build.py, before deploy. Fails (exit 1) when the generated
output is broken in ways that render silently in the browser:

  1. Unreplaced {{PLACEHOLDER}} tokens — a generator or the build.py
     post-process pass missed a substitution.
  2. Truncated pages — <script>/</script> tag count mismatch or a missing
     closing </html>.
  3. Embedded JSON payloads that don't parse — the canonical config blobs
     (ISO2 / CTRY_CODE / CAT_ICON) and the index photos feed are decoded
     in place with json.JSONDecoder.raw_decode.
  4. JSON side files (feed_meta.json, trips_meta.json, ...) that don't parse.
  5. Required pages missing or suspiciously small.

Usage:
    python scripts/validate_html.py [--dir .] [--quiet]
"""
import argparse
import json
import re
import sys
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")

# JS assignments whose right-hand side must be valid JSON after substitution.
JSON_MARKER_RE = re.compile(
    r"\b(?:const|var|let)\s+(ISO2|CTRY_CODE|CAT_ICON|photos)\s*=\s*")

REQUIRED_PAGES = [
    "index.html", "feed.html", "trips.html", "tips.html", "stats.html",
    "venues.html", "companions.html", "shouts.html",
]

# solution.html is the tracked write-up page — it quotes {{PLACEHOLDER}}
# syntax as documentation, so the placeholder check would false-positive.
SKIP_FILES = {"solution.html"}

# Google Search Console verification stubs are named google<token>.html and
# hold a single `google-site-verification:` line — valid, but not real HTML,
# so the structural checks below would false-positive on them.
GOOGLE_VERIFY_RE = re.compile(r"^google[0-9a-f]+\.html$")


def is_google_verification(path: Path) -> bool:
    if not GOOGLE_VERIFY_RE.match(path.name):
        return False
    return path.read_text(encoding="utf-8", errors="replace") \
        .lstrip().startswith("google-site-verification:")

MIN_PAGE_BYTES = 1024


def check_html(path: Path) -> list[str]:
    problems = []
    text = path.read_text(encoding="utf-8", errors="replace")

    leftovers = sorted(set(PLACEHOLDER_RE.findall(text)))
    if leftovers:
        problems.append(f"unreplaced placeholders: {', '.join(leftovers[:8])}")

    opens = len(re.findall(r"<script\b", text))
    closes = text.count("</script>")
    if opens != closes:
        problems.append(f"<script> mismatch: {opens} open vs {closes} close")

    if "</html>" not in text[-2000:]:
        problems.append("no closing </html> in the last 2000 chars (truncated?)")

    decoder = json.JSONDecoder()
    for m in JSON_MARKER_RE.finditer(text):
        # Only substituted JSON payloads start with [ or { — the same names
        # bound to plain JS expressions (e.g. querySelectorAll) are not ours.
        if text[m.end():m.end() + 1] not in "[{":
            continue
        try:
            decoder.raw_decode(text, m.end())
        except ValueError as e:
            line = text.count("\n", 0, m.start()) + 1
            problems.append(f"embedded JSON for '{m.group(1)}' (line {line}) "
                            f"does not parse: {e}")
    return problems


def check_json(path: Path) -> list[str]:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return []
    except ValueError as e:
        return [f"does not parse as JSON: {e}"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=".", help="build output dir (default: .)")
    ap.add_argument("--quiet", action="store_true",
                    help="print only problems, not per-file OK lines")
    args = ap.parse_args()

    out = Path(args.dir)
    failures: dict[str, list[str]] = {}

    for name in REQUIRED_PAGES:
        p = out / name
        if not p.exists():
            failures[name] = ["required page missing"]
        elif p.stat().st_size < MIN_PAGE_BYTES:
            failures[name] = [f"suspiciously small ({p.stat().st_size} bytes)"]

    html_files = sorted(out.glob("*.html"))
    json_files = sorted(out.glob("*.json"))
    if not html_files:
        print(f"FAIL: no *.html files found in {out.resolve()}", file=sys.stderr)
        return 1

    for p in html_files:
        if p.name in SKIP_FILES or is_google_verification(p):
            continue
        probs = check_html(p)
        if probs:
            failures.setdefault(p.name, []).extend(probs)
        elif not args.quiet:
            print(f"  ok  {p.name}")

    for p in json_files:
        probs = check_json(p)
        if probs:
            failures.setdefault(p.name, []).extend(probs)
        elif not args.quiet:
            print(f"  ok  {p.name}")

    if failures:
        print(f"\nFAIL: {len(failures)} file(s) with problems:", file=sys.stderr)
        for name in sorted(failures):
            for prob in failures[name]:
                print(f"  {name}: {prob}", file=sys.stderr)
        return 1

    print(f"OK: {len(html_files)} HTML + {len(json_files)} JSON files validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
