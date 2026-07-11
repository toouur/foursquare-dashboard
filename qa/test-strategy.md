# Test Strategy

**System under test:** [4sq.pages.dev](https://4sq.pages.dev) — a static dashboard generated
from 66,000+ Foursquare/Swarm check-ins by a Python pipeline, deployed to Cloudflare Pages,
with dynamic search/feed served by Pages Functions backed by a D1 (SQLite) database.

**Owner:** solo developer/QA. This document explains *what* is tested, *how*, *why in that
order*, and — just as important — what is deliberately **not** tested and why that tradeoff
is acceptable for this system.

---

## 1. What can break (risk analysis)

Everything below is ordered by (likelihood × user impact). The test suite is shaped by this
list, not the other way around.

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|-----------|------------|
| R1 | **Data-transform regressions** — city/country normalization, trip detection, companion collection silently produce wrong numbers | High: every page shows wrong stats; wrong data syncs to D1 | High: this logic changes most often | 175 offline unit tests; mutation testing on `transform.py` |
| R2 | **Broken generated HTML deployed** — unfilled `{{PLACEHOLDER}}`, truncated page, invalid embedded JSON | High: visibly broken production page | Medium: template edits are frequent | `validate_html.py` deploy gate — deploy is blocked, not just warned |
| R3 | **Data drift from the upstream API** — Foursquare renames cities, changes encodings (NFC/NFD), returns new anomalies | Medium: phantom cities, split counts | High: happened repeatedly ([BUG-001](bug-reports/BUG-001-nfd-phantom-cities.md), [BUG-005](bug-reports/BUG-005-gateway-border-city.md)) | `check_city_count.py` baseline gate in the hourly pipeline; config-integrity gate `check_city_config.py` |
| R4 | **API contract breaks** — `/api/search`, `/api/feed` change shape or die (D1 binding lost, schema drift) | High: search/feed dead in prod | Medium | 22 live API contract tests weekly + on demand; `/api/health` probed hourly |
| R5 | **Python ↔ JavaScript logic drift** — companion logic exists twice (build-time Python, runtime JS) | Medium: feed disagrees with static pages | Medium | Parity test that extracts the JS function *verbatim* from `feed.js` and runs both sides on the same fixtures under Node |
| R6 | **Silent pipeline death** — hourly CI job fails repeatedly and nobody notices | High: site goes stale | Medium | Telegram alert after 2 consecutive scheduled failures; `/api/health` returns 503 when data is stale |
| R7 | **Performance regressions** — D1-backed search gets slow, pages get heavy | Medium | Low–medium | k6 load test (thresholds: error rate < 1%, p95 < 1 s, p99 < 2 s); weekly Lighthouse with score floors (perf ≥ 60, a11y/bp/SEO ≥ 85) |
| R8 | **Accessibility regressions** | Medium | Medium: pages are regenerated constantly | 8 axe-core audits with a `KNOWN_ISSUES` baseline — **new** critical/serious violations fail, pre-existing debt is tracked, not ignored |
| R9 | **Quota/rate-limit exhaustion of upstream APIs** | Medium: data category stops updating for a month | Low after fix | Learned the hard way ([BUG-003](bug-reports/BUG-003-ratings-quota-burn.md)); fetches are now throttled and the throttle is documented |

## 2. Test pyramid — 219 tests

```
        8   axe-core a11y audits      (live, weekly)      ← slowest, broadest
       14   Playwright E2E smoke      (live, weekly)
       22   API contract tests        (live, weekly)
      175   offline unit/parity tests (every push, ~2 s)   ← fastest, most numerous
```

The split is enforced with pytest markers (`live`, `e2e`) registered in `tests/conftest.py`.

### Ring 1 — offline unit tests (175, run on every push, no network/secrets)

Target: the pure-Python transform layer, because that is where R1 lives and where changes
are most frequent. Highlights:

- **transform** (28): the five-stage city-normalization priority order is pinned with one
  test per override level beating the level below it; NFC normalization; real repo config
  is loaded once as a smoke test.
- **trip detection** (15): synthetic timelines; timezone chosen so DST can never shift a
  check-in across a date boundary and flake the test.
- **transport-mode classifier** (34): speed/dwell band edges, FR24 flight-window override,
  category anchors.
- **companions** (15) + **Py↔JS parity** (2): the parity test brace-extracts
  `collectCompanions()` from `functions/api/feed.js` and executes it under Node — zero
  logic duplicated in the test, so it cannot rot into testing a stale copy.
- **shouts** (24), **year covers** (12), **month narrative** (15), and friends: pure
  functions pinned by invariants (determinism, no "(N×)" tallies, list/count agreement),
  not by exact prose — so a phrasing tweak doesn't break 15 tests.

Design rules for this ring:
- **No I/O** except the two real-config smoke tests. Test data comes from a shared
  `make_row()` factory.
- **Pin invariants, not strings**, wherever output is presentation-ish.
- Must stay fast enough (< 5 s) that running it before every commit costs nothing.

### Ring 2 — API contract tests (22, `-m live`)

Run against **production** weekly (Mon 06:00 UTC) and on demand. They assert response
shape (keys, tuple lengths, types), cache headers, and edge cases (empty query, cursor
paging) for `/api/search`, `/api/feed`, `/api/health` and friends. Rationale: the
functions run on Cloudflare's runtime with a real D1 binding — mocking that locally would
test the mock. The `_v=` cache-version discipline these tests protect exists because of
[BUG-002](bug-reports/BUG-002-feed-edge-cache-shape.md).

### Ring 3 — E2E + a11y (14 Playwright + 8 axe-core, `-m live`)

Playwright smoke: each key page loads, renders its main content, has no console errors;
search round-trips a real query; feed virtual-scroll loads more items. axe-core: 8 pages
audited; failures gate on **new** critical/serious rules only — the pre-existing baseline
lives in `KNOWN_ISSUES` in `tests/test_a11y.py` where it is visible and reviewable, rather
than silently suppressed.

Deliberately scheduled weekly, not on push: a transient site outage must never block a
code push (the push gates are all offline).

## 3. Quality gates by lifecycle stage

| Stage | Gate | Blocks? |
|-------|------|---------|
| Pre-commit (local) | `ruff` + `mypy` (0 errors policy) + offline suite | By convention |
| Push / PR | `tests.yml`: same three gates in CI | Yes — merge |
| Hourly rebuild | `validate_html.py` (placeholders, JSON, min sizes) → `check_city_count.py` (city-set drift vs baseline) → then and only then D1 sync + deploy | Yes — deploy |
| Post-deploy, hourly | `/api/health` (D1 row count, latest check-in age, feed_meta total → 200/503) | Alerting |
| Post-deploy, on failure | Telegram message after 2 consecutive scheduled failures | Alerting |
| Weekly | Live suite (Mon 06:00) + Lighthouse audit with score floors (Mon 07:00) | Red run = investigate |
| On demand | k6 load test (`/api/search`, <1 % errors, p95 < 1 s); mutmut mutation testing over `transform.py` | Baseline / audit |

Mutation testing is manual-dispatch because it is slow and its value is periodic (audit
the *tests*, not the code); it has already paid for itself by exposing assertions that
passed for the wrong reason.

## 4. Test data strategy

- Unit tests use **synthetic rows** from a single `make_row()` factory — no real user data
  in the repo, no PII in fixtures, no giant CSV snapshots to maintain.
- Two smoke tests load the **real config** (city merge maps, canonical whitelist) to catch
  config/code disagreements that synthetic data can't.
- Live tests run against **real production data**, asserting shape and sanity (counts > 0,
  dates parse), never exact values that churn hourly.

## 5. What is deliberately NOT here — and why

Honest tradeoffs, reviewed rather than forgotten:

- **No staging environment.** The site is a static build + two read-only functions. The
  deploy gate (`validate_html.py`) inspects the *exact artifact* that will be uploaded,
  which for a static site is a stronger check than a staging click-through. Wrangler local
  dev (`wrangler pages dev`) covers function changes pre-merge. Cost of a staging Pages
  project + second D1: real; marginal risk reduction: small. Revisit if write endpoints
  ever appear.
- **No test-management tool (TestRail etc.).** One person, 219 automated tests as the
  system of record, exploratory charter in [exploratory-checklist.md](exploratory-checklist.md).
  A TCM layer would be pure ceremony here. The bug-report discipline is kept instead —
  see [bug-reports/](bug-reports/).
- **No load testing on a schedule.** k6 generates synthetic production traffic; running it
  hourly would be self-inflicted DDoS. It runs when search/D1 code changes.
- **No unit tests for template HTML/CSS.** Covered by the cheaper combination of
  `validate_html.py` (structure), Playwright (rendering), Lighthouse (quality), and the
  exploratory checklist (visual).

## 6. Exit criteria / definition of done for a change

1. `ruff` clean, `mypy` clean (0 errors across the tree), offline suite green.
2. If `scripts/` changed: full local rebuild + `validate_html.py` pass.
3. If templates/pages changed: the relevant items of the
   [exploratory checklist](exploratory-checklist.md) executed on the local build.
4. If `functions/` changed: live API tests or a `wrangler pages dev` probe.
5. If response shape changed: `_v=` cache tag bumped (see BUG-002) and contract tests updated
   *in the same commit*.
6. New behavior lands with tests pinning it; every bug fix lands with a regression test
   (each report in [bug-reports/](bug-reports/) links its test).
