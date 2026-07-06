# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
fetch_flights.py  –  Fetch the FlightRadar24 flight-diary CSV export and write
it where build.py expects it (flights.csv next to checkins.csv).

FR24 has no public API for the personal flight diary. The diary export at
https://my.flightradar24.com/public-scripts/export is a normal authenticated
web download. Two auth modes are supported, login preferred:

  1. LOGIN (preferred, fully automated) — secret FR24_LOGIN = "email:password".
     The script POSTs the plain JSON login API (no CAPTCHA), then does the
     cross-subdomain SSO handshake, minting a FRESH session every run. Nothing
     to expire, zero manual upkeep. This is what the weekly CI job uses.
  2. COOKIE (fallback) — secret/env FR24_COOKIE, a full browser `Cookie:`
     header. Only used when no FR24_LOGIN is present. The login session behind
     such a cookie expires within hours, so this is not viable unattended.

Login wins whenever FR24_LOGIN is set; cookie is the fallback.

USAGE:
    # Login mode (bash):
    export FR24_LOGIN='email@example.com:password'
    python scripts/fetch_flights.py --out C:/Users/.../foursquare-data/flights.csv

    # Just probe whether auth works (no write):
    python scripts/fetch_flights.py --check

    # Cookie fallback:
    export FR24_COOKIE='<the whole Cookie header string>'
    python scripts/fetch_flights.py --check

EXIT / OUTPUT CONTRACT (for CI):
  - Prints `COOKIE_VALID=true|false` and `CHANGED=true|false` to stdout
    (the COOKIE_VALID token name is kept for workflow back-compat; it means
    "auth valid" regardless of mode).
  - Exit 0  : auth valid, CSV fetched (written unless --check).
  - Exit 2  : auth invalid — bad credentials, or cookie missing/expired (401/403).
  - Exit 1  : other/unexpected error (network, unreadable response).
  The distinct exit-2 lets the CI alarm fire only on a real credential problem.
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

# Credential-login endpoints. Preferred over a stored cookie: the script logs
# in with email+password (secret FR24_LOGIN = "email:password"), which mints a
# fresh session every run — no cookie to expire. Login is a plain JSON API
# (no CAPTCHA); posting to LOGIN_URL sets shared *.flightradar24.com cookies,
# then a GET to HOME_URL performs the SSO handshake that authenticates the
# my.flightradar24.com PHPSESSID the diary export needs.
LOGIN_URL = "https://www.flightradar24.com/user/login"
HOME_URL = "https://my.flightradar24.com/"

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


def resolve_login(cli_login: str | None) -> str:
    cli = (cli_login or "").strip()
    if cli:
        return cli
    return os.environ.get("FR24_LOGIN", "").strip()


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


def login_and_fetch(login: str, timeout: int = 30) -> tuple[str, str]:
    """Log in with email:password, then fetch the diary export.

    Same (status, body) contract as fetch_export:
      "ok"       — body is the CSV text
      "expired"  — bad/missing credentials or the export came back un-authed
      "error"    — transient (network / unexpected HTTP)
    """
    if not login or ":" not in login:
        log.warning("FR24_LOGIN missing or malformed (want 'email:password').")
        return ("expired", "")
    email, password = login.split(":", 1)
    email = email.strip()

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": _BASE_HEADERS["User-Agent"],
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        # Prime cookies (_cfuvid / XSRF), then authenticate via the JSON API.
        sess.get(LOGIN_URL, timeout=timeout)
        resp = sess.post(
            LOGIN_URL,
            data={"email": email, "password": password,
                  "remember": "true", "type": "web"},
            headers={"Origin": "https://www.flightradar24.com",
                     "Referer": LOGIN_URL,
                     "X-Requested-With": "XMLHttpRequest"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        log.error("Login request failed: %s", exc)
        return ("error", "")

    try:
        ok = bool(resp.json().get("success"))
    except ValueError:
        ok = False
    if not ok:
        log.warning("FR24 login did not succeed (HTTP %s) — bad credentials "
                    "or blocked.", resp.status_code)
        return ("expired", "")

    try:
        # SSO handshake: visiting my.* upgrades its PHPSESSID to authenticated,
        # then the same jar downloads the diary export.
        sess.get(HOME_URL, timeout=timeout)
        ex = sess.get(
            EXPORT_URL,
            headers={"Accept": "text/csv,application/csv,text/plain,*/*",
                     "Referer": "https://my.flightradar24.com/settings/export"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        log.error("Export request failed: %s", exc)
        return ("error", "")

    if ex.status_code in (401, 403):
        log.warning("HTTP %s on export after login — session not accepted.",
                    ex.status_code)
        return ("expired", "")
    if ex.status_code != 200:
        log.error("HTTP %s from export endpoint.", ex.status_code)
        return ("error", "")
    if _looks_like_csv(ex.text):
        return ("ok", ex.text)
    log.warning("Logged in but export body is not the diary CSV.")
    return ("expired", "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch FR24 flight-diary CSV via login or session cookie.")
    ap.add_argument("--login", help="FR24 'email:password' (else env FR24_LOGIN). "
                                     "Preferred: mints a fresh session each run.")
    ap.add_argument("--cookie", help="FR24 Cookie header (else env FR24_COOKIE). "
                                     "Fallback when no login is provided.")
    ap.add_argument("--out", help="Destination flights.csv path (omit with --check).")
    ap.add_argument("--check", action="store_true",
                    help="Only probe auth validity; do not write a file.")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    login = resolve_login(args.login)
    cookie = resolve_cookie(args.cookie)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Credential login is preferred (no expiring cookie); cookie is the fallback.
    if login:
        mode = "login"
        status, body = login_and_fetch(login, timeout=args.timeout)
    else:
        mode = "cookie"
        status, body = fetch_export(cookie, timeout=args.timeout)

    if status == "expired":
        print("COOKIE_VALID=false")
        print("CHANGED=false")
        log.warning("[%s] FR24 auth (%s) is NOT valid (bad credentials or expired).",
                    now, mode)
        return 2
    if status == "error":
        print("COOKIE_VALID=unknown")
        print("CHANGED=false")
        log.error("[%s] Transient error fetching export; leaving existing CSV untouched.", now)
        return 1

    # status == "ok"
    n_legs = max(0, len([ln for ln in body.lstrip().splitlines() if ln.strip()]) - 1)
    print("COOKIE_VALID=true")
    log.info("[%s] FR24 auth VALID (%s) — export has %d flight rows.", now, mode, n_legs)

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
