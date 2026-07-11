# BUG-006 — FR24 flights pipeline dies within hours: session cookie expires, and the "export" endpoint returns an HTML page

| | |
|---|---|
| **Severity** | Major (unattended data pipeline broken; flight data silently goes stale) |
| **Priority** | High |
| **Status** | Fixed (auth redesign) + CI alarm on credential rejection |
| **Component** | `scripts/fetch_flights.py`, `.github/workflows/fr24-flights.yml` |
| **Environment** | GitHub Actions runner (unattended weekly fetch), FlightRadar24 web session |
| **Found via** | First CI runs after wiring the cookie-based fetcher — auth probe started failing the same day it was set up |

## Description

FlightRadar24 has **no personal-diary API**; the flight diary is fetched as an
authenticated web download. The first implementation stored a browser `Cookie:` header as
a secret (`FR24_COOKIE`) and replayed it in CI. Two independent defects made this design
dead on arrival:

1. **The login session behind the cookie expired in under ~9 hours** (PHPSESSID
   login-session expiry — verified *not* a Cloudflare challenge: `cf-mitigated` was absent).
   A weekly CI job with a secret that dies in hours means every scheduled run after the
   first fails, and "fix" means a human manually re-exporting browser cookies forever.
2. **The wrong export endpoint.** `GET /settings/export` — the URL visible in the browser
   address bar — returns the settings *page* (HTML), not the CSV. The real download behind
   the "DOWNLOAD CSV" button is `https://my.flightradar24.com/public-scripts/export`.
   The fetcher initially "succeeded" with HTTP 200 while saving an HTML document as
   `flights.csv`. That mistake cost the entire first debugging round.

## Steps to Reproduce (pre-fix)

1. Export a valid logged-in `Cookie:` header from the browser; store as `FR24_COOKIE`.
2. Run `fetch_flights.py` immediately — works.
3. Run it again ~9+ hours later — auth invalid (login page returned).
4. Separately: request `/settings/export` with a *valid* session — observe HTTP 200 with
   `Content-Type: text/html` and no CSV rows.

## Expected

An unattended weekly job downloads the current diary CSV with zero manual upkeep, and a
non-CSV response is treated as a failure, never written to the data file.

## Actual

Every run more than ~9 h after cookie export failed auth; and with the wrong endpoint the
job could "pass" while committing an HTML page as the flights dataset.

## Root cause

1. Session lifetime was assumed to match the cookie's cosmetic expiry attributes; the
   server-side PHPSESSID login session is what actually gates access, and it is short-lived.
2. The browser URL for a download page was assumed to be the download itself; the actual
   asset URL is only visible in the network tab when clicking the button.

## Fix

Auth was redesigned from *replaying* a session to *minting* one: secret `FR24_LOGIN`
(`email:password`) POSTs the plain JSON login API (no CAPTCHA), completes the
cross-subdomain SSO handshake on `my.flightradar24.com`, then downloads from
`/public-scripts/export`. A fresh session per run means **nothing can expire**.
`FR24_COOKIE` was demoted to a fallback. The script gained a strict exit contract for
unattended use — 0 valid / 2 auth-invalid / 1 transient — and the workflow alarms only on
exit 2 ("credentials rejected"), so a failure email means exactly one thing. The CSV
parser also handles the shape difference between modes (cookie mode prepends a blank
line; `lstrip("\r\n")` + `utf-8-sig`).

## Regression coverage

Operational rather than unit-level: `fetch_flights.py --check` probes auth without
writing; the weekly workflow fails loudly on exit 2 and only commits `flights.csv` when
content actually changed (`CHANGED=` token), so an HTML response can never be committed
silently again (it fails validation and exits 2).

## Lessons

For unattended jobs, prefer credentials that *mint* sessions over captured session
artifacts — anything copied out of a browser is on a countdown. And never trust
HTTP 200 + a filename: validate the *shape* of what came back (header row, content type)
before writing it over a dataset.
