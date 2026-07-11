# BUG-004 — Trips that straddle New Year vanish from the January narrative

| | |
|---|---|
| **Severity** | Minor (wrong/incomplete generated text; no data corruption) |
| **Priority** | Medium |
| **Status** | Fixed + regression-tested |
| **Component** | `scripts/gen_year_pages.py` (month narrative composer) |
| **Environment** | Static build, any year page whose January ends a trip started in December |
| **Found via** | Exploratory read of generated year pages against known travel history |

## Description

The `/years/<year>` pages compose a prose narrative per month; one sentence covers trip
journeys ("…flew home, closing **Big Trip** after 47 days on the road"). Trips are
bucketed by **start year** for the year-level structures. The month composer looked up
"trips ending in month M" **only inside the current year's bucket** — so a trip that
started in December 2023 and ended in January 2024 was invisible to January 2024's
narrative: it lived in the 2023 bucket.

Exactly the months where a trip ending is most notable (returning home after New Year
abroad) were the ones that lost the sentence.

## Steps to Reproduce (pre-fix)

1. Have a trip with `start_date` in December of year N and `end_date` in January of year N+1.
2. Build the site and open `/years/<N+1>`.
3. Read the January narrative.

## Expected

January of year N+1 mentions the trip ending (name, duration), same as any same-year trip end.

## Actual

No mention. The trip's ending is silently absent from year N+1; the trip appears only in
year N (as a departure).

## Root cause

An indexing structure keyed by start-year was reused to answer an end-date question.
Classic boundary bug: correct for the ~95 % of trips contained within one calendar year,
wrong exactly at the year boundary.

## Fix

`trip_ends_by_mo` is built by scanning **all** trips and bucketing by
`(end_year, end_month)` with an explicit end-year guard, independent of the start-year
bucketing used elsewhere.

## Regression coverage

`tests/test_month_narrative.py::test_trip_ending_names_the_trip` feeds the composer a trip
with `start_date: 2023-11-20`, `end_date: 2024-01-05` and asserts the trip name appears
(in `<strong>`) in the 2024 narrative.

## Lessons

When a data structure is grouped by one attribute (start year), every query against a
*different* attribute (end date) needs its own index — reusing the existing grouping
"because it's already there" fails precisely at the group boundaries. Boundary values
(New Year straddles) belong in the first batch of test cases, not the last.
