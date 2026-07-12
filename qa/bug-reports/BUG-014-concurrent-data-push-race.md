# BUG-014 — Concurrent scheduled jobs race to push the data repo → non-fast-forward rejection fails the build

| | |
|---|---|
| **Severity** | Major (a green data-fetch + build is discarded; the run goes red and the hourly deploy is skipped for that hour) |
| **Priority** | High |
| **Status** | Fixed (July 2026) — bounded rebase-and-retry loop added to all three data-repo push steps |
| **Component** | CI: `.github/workflows/update-dashboard.yml`, `warm-routes.yml`, `fr24-flights.yml` (all push to `toouur/foursquare-data`) |
| **Environment** | GitHub Actions, scheduled workflows committing to a shared private data repo |
| **Found via** | Red hourly run: `! [rejected] main -> main (fetch first)` in the "Commit updated data files to data repo" step |

## Description

The private data repo (`toouur/foursquare-data`) is written by **three** separate
workflows, all committing to `main`:

- `update-dashboard.yml` — hourly (+ push-verify + manual): commits `checkins.csv`,
  `tips.json`, `feed_meta.json`, etc.
- `warm-routes.yml` — daily 03:00 UTC: commits `routes_cache.json`.
- `fr24-flights.yml` — weekly Sunday 05:00 UTC: commits `flights.csv`.

Each job checks out the ref, works for a minute or more, then pushes. When two jobs
overlap — most reliably the 03:00 route-warm still running as the 03:00 hourly build
starts, or a manual dispatch alongside the schedule — whichever pushes **second** finds
its checkout base is no longer the remote tip, and Git rejects the push as
non-fast-forward. The step exits 1, the whole run goes red, and that hour's rebuild +
deploy is lost even though every fetch and transform succeeded.

## Steps to Reproduce

1. Trigger `warm-routes.yml` (drains a large route backlog — a slow, minutes-long push).
2. While it is running, let the hourly `update-dashboard.yml` reach its commit step.
3. The route-warm commit lands on `main` first.
4. The hourly job's bare `git push` fails:
   ```
   ! [rejected]        main -> main (fetch first)
   error: failed to push some refs to 'github.com/toouur/foursquare-data'
   ```

## Expected

Two jobs writing **different files** (`checkins.csv` vs `routes_cache.json` vs
`flights.csv`) never truly conflict; the later push should replay on top of the earlier
commit and succeed automatically.

## Actual

The later push fails hard, the run is marked failed, and the scheduled deploy for that
hour is skipped. On a bad overlap the failure recurs until the jobs happen to stop
overlapping.

## Root cause

The push steps assumed a single writer. `update-dashboard.yml` and `fr24-flights.yml`
issued a **bare `git push`** with no reconciliation; `warm-routes.yml` had only a
**single** `git push || (pull --rebase && push)` retry, which still loses if a third push
sneaks in during that one rebase window. None of them treated "someone else advanced the
ref" as an expected, recoverable condition.

## Fix

A **bounded rebase-and-retry loop**, identical in all three workflows' commit step, so the
concurrency contract is obvious from reading any one of them:

```bash
for i in 1 2 3 4 5; do
  if git push; then break; fi
  echo "push rejected (attempt $i) — rebasing onto latest main"
  git pull --rebase origin main
  [ "$i" = 5 ] && { echo "push still failing after rebase retries"; exit 1; }
done
```

Because the commits touch disjoint files, `--rebase` always applies cleanly and the retry
succeeds on the next attempt. The five-attempt ceiling means a *genuine* problem (a revoked
`DATA_REPO_PAT`, a truly diverged history) still fails loudly instead of looping forever.

A repo-wide `concurrency: { group: foursquare-data-push, cancel-in-progress: false }` was
considered and **rejected**: it would serialize the jobs, queuing the latency-sensitive
hourly check-in poller behind the daily backlog drain. Retrying on the loser of the race
keeps every job on the fast path — a lost race costs one extra fetch-and-replay, not a
queued wait.

## Regression coverage

Structural / operational: the retry loop is exercised in production every time two
scheduled jobs overlap (routinely at 03:00 UTC). There is no unit test — the behaviour is
a shell loop in workflow YAML, outside the Python suite — but the guard is uniform across
the three files so a future fourth writer can copy it verbatim. Challenge 19 in
`solution.html` documents the pattern for future maintainers.

## Lessons

A shared mutable ref written by more than one scheduler is a race by default, and "it
worked in testing" just means the jobs happened not to overlap that day. When writers
touch disjoint files, prefer **optimistic retry (rebase-and-replay)** over a global lock:
you get correctness without paying serialization latency on the job that can least afford
it. Make the guard identical across every writer so the invariant is self-documenting.
