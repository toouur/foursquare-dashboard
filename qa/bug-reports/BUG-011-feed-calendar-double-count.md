# BUG-011 — Feed calendar double-counts months after jump navigation

| | |
|---|---|
| **Severity** | Minor (wrong counts in the calendar overlay; no data loss) |
| **Priority** | Medium |
| **Status** | Fixed |
| **Component** | `feed.html` client (`renderCal`, virtual-scroll state machine), `feed_meta.json` |
| **Environment** | Browser, `/feed` page with calendar navigation |
| **Found via** | Manual exploratory pass on the feed's navigation paths (jump to old month → back to Latest) |

## Description

The feed is a contiguous-array virtual scroll: one `ALL` array, `loadFwd()` appending
older items, `loadRev()` prepending newer ones. Its calendar overlay shows per-month
check-in counts. The original `renderCal` **accumulated** counts locally as batches
loaded — a counter incremented per item seen.

Navigation (`goYMD`, `goLatest`, `goOldest`) resets the virtual-scroll state: bumps the
load-generation counter, clears `ALL`, refetches around the target. But the accumulated
calendar counter survived resets — so items refetched after a jump were counted *again*.
Jump to July 2019 and back to Latest, and every month you passed through now showed
inflated counts; each further navigation inflated them more.

## Steps to Reproduce (pre-fix)

1. Open `/feed`, open the calendar — note a month's count.
2. Jump via calendar to an old month; let batches load.
3. Return via "Latest"; reopen the calendar.
4. The months loaded twice now show roughly doubled counts.

## Expected

Calendar counts are facts about the dataset — identical regardless of how the user
navigated before opening the calendar.

## Actual

Counts grew with every navigation cycle that re-loaded overlapping ranges.

## Root cause

Derived-state accumulation across a state reset that didn't reset it. The counter
conflated "items that exist in month M" (a dataset fact) with "items I have loaded for
month M" (a session artifact). The virtual-scroll rewrite made resets *frequent*, turning
a latent design flaw into a visible bug.

## Fix

Counting was removed from the client entirely: `renderCal` now reads authoritative
per-month counts from `YM_IDX[ym]` in **`feed_meta.json`** — a static file generated at
build time from the full dataset. The client never counts anything; navigation resets
can't corrupt what isn't accumulated. (Same file later absorbed `flight_days` for the
transport-mode icons — static metadata beats client bookkeeping.)

## Regression coverage

`tests/test_e2e_smoke.py` exercises the feed's jump navigation (calendar → old month →
Latest) among its Playwright flows; the exploratory checklist keeps a dedicated line for
this path ("historically fragile") because state-reset bugs mutate rather than repeat.

## Lessons

Anything accumulated client-side must be enumerated in every state-reset path — or better,
not accumulated at all: when a value is a dataset fact, compute it once at build time and
ship it as data. The cheapest fix for fragile bookkeeping is deleting the bookkeeping.
