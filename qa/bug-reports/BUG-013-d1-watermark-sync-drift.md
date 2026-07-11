# BUG-013 — Incremental D1 sync silently drifts from source: backdated check-ins never insert, orphaned venues never delete

| | |
|---|---|
| **Severity** | Major (permanent, silent data loss/pollution in the production database feeding search and feed) |
| **Priority** | High |
| **Status** | Fixed + reconciliation fallbacks in the hourly job |
| **Component** | `scripts/sync_to_d1.py` (incremental check-ins + venues paths) |
| **Environment** | Hourly CI sync, CSV source of truth → Cloudflare D1 |
| **Found via** | Row-count comparison between the CSV and D1 during an audit — both directions of drift surfaced |

## Description

The incremental sync had two mirror-image defects, both consequences of syncing by a
heuristic instead of reconciling against the source of truth:

1. **Inserts gated by a `MAX(date)` watermark.** New check-ins were defined as "rows newer
   than the newest already in D1". Any check-in whose timestamp was ≤ the watermark —
   backdated posts, out-of-order rows, rows surfaced later by the
   `--recheck-recent-hours` sweep — was **never inserted, on any future run**. The gap was
   permanent and invisible: every subsequent sync reported success.
2. **Venues path upsert-only.** Venues orphaned by a merge, a venue_id reassignment, or an
   archive dedup were never deleted, so the D1 venue count drifted *above* the CSV's
   unique-venue count with stale rows forever.

One mechanism under-syncs, the other over-retains — same root cause, opposite symptoms.

## Steps to Reproduce (pre-fix)

1. Add a check-in to the CSV with a timestamp older than the newest row already in D1
   (e.g. a backdated post).
2. Run the sync — reports success; `SELECT COUNT(*)` shows the row absent. It will be
   absent forever.
3. Conversely, merge away a venue in the CSV; its D1 row survives every future sync.

## Expected

After every sync, D1 check-ins and venues are exactly the CSV's rows — additions,
backdated rows, and removals all converge, or a mismatch fails loudly.

## Actual

Backdated rows silently never arrived; deleted venues silently never left; both drifts
compounded over months with every run reporting success.

## Root cause

"Incremental" was implemented as *watermark/append-only* rather than *set difference*.
A watermark answers "what is newer?", not "what is missing?" — the two coincide only when
data arrives strictly in timestamp order, an assumption Foursquare data (backdating,
rechecks) does not honor. Deletion was simply out of the model.

## Fix

Reconciliation fallbacks keyed on cheap invariants: on a **row-count mismatch** the sync
performs a `checkin_id` **set-difference** (CSV vs D1) and inserts the missing rows
regardless of timestamp; the venues path mirrors it, deleting venue ids present in D1 but
absent from current check-in data (matching `delete_checkin.py`'s orphan cleanup). The
fast watermark path still handles the common case; the set-difference runs only when
counts disagree. (The same audit fixed a sibling silent failure: a 402/network error in
the ratings fetch reported `CHANGED=false` — indistinguishable from "nothing new" — and
froze likes for weeks; it now emits an explicit `LIKES_UNAVAILABLE` warning in CI.
See BUG-003 for the quota story.)

## Regression coverage

The count-mismatch invariant runs inside every hourly sync — drift now self-heals within
one cycle instead of accumulating. `/api/health` independently exposes the D1 count and
latest check-in age, so a stuck sync trips the uptime probe.

## Lessons

Append-only sync is a bet that the source never backdates and never deletes; when the bet
loses, it loses *silently*. Every incremental mechanism needs a reconciliation invariant
against the source of truth (counts are enough to trigger a full diff) — and any fetch
step must make "could not fetch" distinguishable from "nothing changed", or errors will
masquerade as calm.
