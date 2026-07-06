# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
fetch_flights.py  –  Fetch the FlightRadar24 flight-diary CSV export using a
stored browser session cookie, and write it where build.py expects it
(flights.csv next to checkins.csv).

WHY A COOKIE (not username/password):
  FR24 has no public API for the personal flight diary. The diary export at
  https://my.flightradar24.com/settings/export is a normal authenticated web
  download: your logged-in browser presents a session cookie and receives a
  CSV. An unattended machine (CI, or this script) has no browser session, so it
  must present that same cookie. A scripted username/password login would only
  produce the same cookie anyway, while being far more likely to hit
  Cloudflare/CAPTCHA/2FA and exposing a much larger secret. See the "flights"
  discussion in CLAUDE.md / project memory.

HOW TO GRAB THE COOKIE (once):
  1. Log in at https://my.flightradar24.com in your browser.
  2. Open DevTools → Network tab → reload the page.
  3. Click the document request → Request Headers → copy the ENTIRE value of
     the `Cookie:` header (it's a long "a=1; b=2; ..." string).
  4. Store it as env var FR24_COOKIE (locally) or GitHub Actions secret
     FR24_COOKIE (CI). Never commit it.

USAGE:
    # Local (bash):
    export FR24_COOKIE='<the whole Cookie header string>'
    python scripts/fetch_flights.py --out C:/Users/.../foursquare-data/flights.csv

    # Just probe whether the cookie is still valid (no write):
    python scripts/fetch_flights.py --check

EXIT / OUTPUT CONTRACT (for CI + expiry monitoring):
  - Prints `COOKIE_VALID=true|false` and `CHANGED=true|false` to stdout.
  - Exit 0  : cookie valid, CSV fetched (written unless --check).
  - Exit 2  : cookie missing/expired/unauthorized (login page or 401/403).
  - Exit 1  : other/unexpected error (network, unreadable response).
  The distinct exit-2 lets a monitor pinpoint the day the cookie expired.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

# The "DOWNLOAD CSV" button on https://my.flightradar24.com/settings/export
# points here; this is the endpoint that actually streams the diary CSV
# (Content-Disposition: attachment; filename="flightdiary_*.csv"). Hitting
# /settings/export directly only returns the settings *page* (HTML).
EXPORT_URL = "https://my.flightradar24.com/public-scripts/export"

# A logged-in export starts with the FR24 diary header (order/case per FR24).
# We only require the first two columns so a schema tweak on their side does
# not falsely read as "expired".
_CSV_SIGNATURE = ("date", "flight number")

# Browser-ish headers. The Cookie is the only thing that authenticates; the
# rest just make the request look like a normal document load.
_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://my.flightradar24.com/settings/export",  # page the CSV button lives on
}


def resolve_cookie(cli_cookie: str | None) -> str:
    cli = (cli_cookie or "").strip()
    if cli:
        return cli
    return os.environ.get("FR24_COOKIE", "").strip()


def _looks_like_csv(text: str) -> bool:
    """True if the body is the diary CSV (not an HTML login redirect)."""
    head = text.lstrip("\r\n").lower()
    if head.startswith("<") or "<html" in head[:2000]:
        return False
    first_line = head.splitlines()[0] if head else ""
    return all(col in first_line for col in _CSV_SIGNATURE)


def fetch_export(cookie: str, timeout: int = 30) -> tuple[str, str]:
    """Fetch the export.

    Returns (status, body) where status is one of:
      "ok"       — body is the CSV text
      "expired"  — cookie missing/invalid/unauthorized (401/403 or login HTML)
      "error"    — unexpected (caller should treat as transient)
    """
    if not cookie:
        return ("expired", "")
    headers = dict(_BASE_HEADERS, Cookie=cookie)
    try:
        # Do NOT auto-follow to the login page as if it were success; capture it.
        resp = requests.get(EXPORT_URL, headers=headers, timeout=timeout,
                            allow_redirects=True)
    except requests.RequestException as exc:
        log.error("Request failed: %s", exc)
        return ("error", "")

    if resp.status_code in (401, 403):
        log.warning("HTTP %s — cookie rejected (expired/insufficient).", resp.status_code)
        return ("expired", "")
    if resp.status_code != 200:
        log.error("HTTP %s from export endpoint.", resp.status_code)
        return ("error", "")

    text = resp.text
    if _looks_like_csv(text):
        return ("ok", text)
    # 200 but not CSV → almost always the login/marketing page = not logged in.
    log.warning("200 OK but body is not the diary CSV (login redirect?). "
                "First 120 chars: %r", text.lstrip()[:120])
    return ("expired", "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch FR24 flight-diary CSV via session cookie.")
    ap.add_argument("--cookie", help="FR24 Cookie header (else env FR24_COOKIE).")
    ap.add_argument("--out", help="Destination flights.csv path (omit with --check).")
    ap.add_argument("--check", action="store_true",
                    help="Only probe cookie validity; do not write a file.")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    cookie = resolve_cookie(args.cookie)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    status, body = fetch_export(cookie, timeout=args.timeout)

    if status == "expired":
        print("COOKIE_VALID=false")
        print("CHANGED=false")
        log.warning("[%s] FR24 cookie is NOT valid (missing or expired).", now)
        return 2
    if status == "error":
        print("COOKIE_VALID=unknown")
        print("CHANGED=false")
        log.error("[%s] Transient error fetching export; leaving existing CSV untouched.", now)
        return 1

    # status == "ok"
    n_legs = max(0, len([ln for ln in body.lstrip().splitlines() if ln.strip()]) - 1)
    print("COOKIE_VALID=true")
    log.info("[%s] FR24 cookie VALID — export has %d flight rows.", now, n_legs)

    if args.check or not args.out:
        print("CHANGED=false")
        return 0

    out_path = Path(args.out)
    prev = out_path.read_text(encoding="utf-8-sig") if out_path.exists() else None
    if prev is not None and prev.lstrip("\r\n") == body.lstrip("\r\n"):
        print("CHANGED=false")
        log.info("No change vs existing %s", out_path)
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    print("CHANGED=true")
    log.info("Wrote %s (%d rows).", out_path, n_legs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
