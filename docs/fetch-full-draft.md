# Draft: Add `--full` support to `fetch_checkins.py`

## Objective
- Keep scheduled updates incremental (every 2 hours).
- Allow manual full refetch with `--full`.
- Rebuild dashboard after fetch (already in workflow).

## Current state
- `fetch_checkins.py` currently supports incremental updates only.
- It reads max timestamp from CSV and requests only newer check-ins.
- Workflow already runs every 2 hours and rebuilds dashboard.

## Proposed CLI
Add flags to `fetch_checkins.py`:
- `--full` (boolean): force full fetch from API, ignoring existing CSV timestamp.
- `--dry-run` (optional): print intended mode and counts without writing CSV.

Behavior:
- `--full` absent + CSV exists → incremental.
- `--full` absent + CSV missing → full.
- `--full` present → full regardless of CSV.

## Proposed fetch strategy
For robustness (especially large histories and CI):
- Full local: offset pagination.
- Full CI: `beforeTimestamp` pagination to avoid offset ceiling behavior.
- Incremental: `afterTimestamp` with paging until exhausted.

## Deduplication recommendation
Current dedupe in `fetch_checkins.py` uses only `date`.
Recommended dedupe key:
- `(venue_id, date)`
Fallback:
- If no `venue_id`, fallback to `(venue_name, date)` or raw `date`.

## CHANGED output contract
Keep emitting:
- `CHANGED=true` when CSV content changed.
- `CHANGED=false` when no data delta.

This preserves workflow interoperability.

## Workflow recommendation
Keep scheduled job unchanged:
- cron: every 2 hours.
- run `python fetch_checkins.py --token "$SWARM_TOKEN" --csv checkins.csv`.
- run `python build.py ...` afterward.

Optional improvement:
- Add `workflow_dispatch` input (e.g. `full_sync`) and pass `--full` when selected.

## About near-realtime “recent check-ins”
Swarm/Foursquare personal check-ins are typically polled, not pushed via webhook in this setup.
Practical options:
- Keep 2-hour polling (current).
- Increase frequency (e.g., every 30 min) with API/action-run tradeoffs.

## Implementation checklist
1. Add `--full` arg parsing.
2. Add full-fetch code path and pagination helpers.
3. Improve dedupe key.
4. Keep `CHANGED=` marker semantics.
5. (Optional) add workflow dispatch input for full sync.
