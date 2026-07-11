# BUG-003 — Hourly ratings refetch burns the monthly API quota in 3 days; ratings then 402 for the rest of the month

| | |
|---|---|
| **Severity** | Major (one data category frozen for weeks; unrecoverable until quota reset) |
| **Priority** | High |
| **Status** | Fixed (throttle + early-stop pagination); quota documented |
| **Component** | `.github/workflows/update-dashboard.yml` (ratings fetch step) |
| **Environment** | GitHub Actions hourly pipeline → Foursquare API v2 `/users/self/venuelikes` |
| **Found via** | CI logs: fetch step started returning HTTP 402 on every run mid-month |

## Description

`/users/self/venuelikes` is the only endpoint that still returns rating data (the
okays/dislikes endpoints 402 permanently). What was **not documented anywhere** by the
vendor: calls to it are metered against a **monthly premium-call quota** (~220 calls,
established empirically) that resets on the 1st and returns `402 Payment Required` once
spent.

Each ratings fetch re-paginates the full list (~7–8 calls). The hourly pipeline therefore
spent ~180 calls/day and exhausted the month's budget in roughly **3 days**, after which
every ratings fetch failed with 402 until the next month — with no way to backfill.

## Steps to Reproduce

1. Schedule a full `venuelikes` re-paginate (~7–8 calls) hourly.
2. Observe responses across ~3 days.

## Expected

Either the endpoint is not quota-metered (nothing in the docs said otherwise), or the
pipeline's call budget is sized to the real quota.

## Actual

HTTP 402 on every `venuelikes` call from day ~3 until the 1st of the next month. Ratings
data frozen; no recovery path within the month.

## Root cause

Two stacked causes:
1. **Undocumented vendor quota** — discoverable only empirically.
2. **Pipeline design assumed calls were free** — full re-pagination hourly for a dataset
   that changes a few times per month.

## Fix

- **Throttle:** the ratings step now fires only at 04:00 UTC when
  `day_of_year % 3 == 0` (≈ every 3 days ⇒ ~80 calls/month ≈ 36 % of budget, leaving
  headroom for manual runs). `workflow_dispatch` bypasses the gate deliberately.
- **Early-stop pagination:** stop as soon as a page contains only already-known items.
- **Runbook:** quota size, reset day, and the "you cannot backfill an exhausted month —
  wait for the 1st or use the data-export + `--force-ratings` path" procedure are
  documented in CLAUDE.md and project memory.

## Regression coverage / prevention

Not unit-testable (vendor-side quota). Prevention is architectural: the throttle is code,
not convention; the empirical quota number is written down where the next editor of the
workflow will see it; `/api/health` + the Telegram 2-strike alert surface a stuck pipeline.

## Lessons

Treat third-party API calls as a budget even when no quota is documented — meter
consumption *before* the vendor meters it for you. When a limit is discovered empirically,
record the number and the reset semantics immediately; they are expensive to re-learn.
