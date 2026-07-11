# BUG-010 — Full check-ins resync over the HTTP API leaves D1 in a partial state at 65 K rows

| | |
|---|---|
| **Severity** | Major (production database left partially populated mid-operation; search/feed serve incomplete data until repaired) |
| **Priority** | High |
| **Status** | Fixed by replacing the mechanism (wrangler SQL-dump path); the API path is documented as unreliable and no longer used at this scale |
| **Component** | `scripts/sync_to_d1.py` (`--force-checkins`), `scripts/gen_d1_dump.py` |
| **Environment** | Cloudflare D1 over the REST API, ~65 K `checkins` rows + venues |
| **Found via** | A forced resync run failing partway through, leaving D1 with a fraction of the rows |

## Description

`sync_to_d1.py --force-checkins` implements "reset the table": DELETE everything, then
reinsert all rows in batched INSERT calls over the Cloudflare D1 HTTP API. At 65 K
check-ins this is hundreds of sequential HTTP requests, and **there is no transaction
spanning them** — each request commits independently. Any transient network failure in
the middle (observed in practice) aborts the script after the DELETE and a partial
reinsert, leaving the live database serving an arbitrary prefix of history. `/api/search`
and `/api/feed` keep answering — with silently incomplete data — until someone notices
and re-runs the whole fragile operation.

## Steps to Reproduce (pre-fix)

1. Run `sync_to_d1.py --force-checkins` against the production D1 with ~65 K rows.
2. Interrupt connectivity (or simply be unlucky — long request chains fail on their own).
3. Query `SELECT COUNT(*) FROM checkins` — a fraction of the expected count; the site is
   live in this state.

## Expected

A full resync is atomic-ish: either the new dataset lands completely, or the operation
fails without destroying what was there — and a half-applied state is at minimum detected
loudly.

## Actual

DELETE succeeded, reinsert died mid-stream; D1 held a partial table with nothing flagging
it beyond a script traceback on the operator's machine.

## Root cause

A destructive multi-request operation built on an API that offers no cross-request
transaction, with failure probability compounding per request. "Works on 1 K rows"
was extrapolated to 65 K, where the chain is ~an order of magnitude longer and a mid-chain
failure is destructive instead of merely annoying.

## Fix

The bulk path was rebuilt around wrangler: `gen_d1_dump.py` generates one SQL file
(DELETE + all INSERTs), executed as a single `npx wrangler d1 execute --file … --remote`
upload — one operation to succeed or fail, no long HTTP chain. CLAUDE.md marks the Python
`--force-checkins` path with an explicit **WARNING: unreliable for 65 K rows** so nobody
reaches for it again. Related safety nets added around the same period: count-mismatch
reconciliation fallbacks in the incremental sync (see BUG-013) detect drifted states.

## Regression coverage

Operational: the dump path is the documented runbook; incremental sync's count checks
would flag a partial table on the next hourly run instead of letting it persist silently.

## Lessons

Estimate destructive operations by *number of independently-failing steps*, not by row
count. If a mechanism can't be transactional, restructure it so the transactional
boundary moves to a layer that can (one file upload) — and leave a warning sign on the
broken road, because the convenient-looking path will be rediscovered.
