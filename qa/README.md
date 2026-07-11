# QA

Quality documentation for this project. The automated suite itself lives in
[`tests/`](../tests/) (219 pytest tests) and is documented file-by-file in the main
[README](../README.md#tests); this directory holds the thinking around it.

| Document | What it is |
|----------|------------|
| [test-strategy.md](test-strategy.md) | Risk analysis → test pyramid (175 unit / 22 API / 14 E2E / 8 a11y), quality gates per lifecycle stage, test-data strategy, and the honest list of what is deliberately **not** tested and why |
| [exploratory-checklist.md](exploratory-checklist.md) | Manual pre-release charter: what a human checks that automation can't (visual layout, generated prose quality, post-deploy spot checks) |
| [bug-reports/](bug-reports/) | Thirteen real defects found (and all but one fixed) in this project, written up Jira-style: repro, expected/actual, root cause, fix, regression coverage, and lessons |

## Bug report index

| ID | Title | Severity |
|----|-------|----------|
| [BUG-001](bug-reports/BUG-001-nfd-phantom-cities.md) | NFD-encoded city names bypass every normalization rule → phantom cities | Major |
| [BUG-002](bug-reports/BUG-002-feed-edge-cache-shape.md) | Edge cache serves old API tuple shape for 1 h after deploy → broken feed | Major |
| [BUG-003](bug-reports/BUG-003-ratings-quota-burn.md) | Hourly refetch burns undocumented monthly API quota in 3 days → 402 for weeks | Major |
| [BUG-004](bug-reports/BUG-004-year-straddle-trip-end.md) | Trips straddling New Year vanish from the January narrative | Minor |
| [BUG-005](bug-reports/BUG-005-gateway-border-city.md) | Border-crossing venues get city from one country, country from the other | Minor–Major |
| [BUG-006](bug-reports/BUG-006-fr24-cookie-expiry.md) | FR24 flights pipeline dies in hours: cookie expires, "export" URL returns HTML | Major |
| [BUG-007](bug-reports/BUG-007-git-history-bloat.md) | Hourly commits of generated HTML bloat the repo to 4–7 GB → history rewrite | Critical |
| [BUG-008](bug-reports/BUG-008-nb-steals-bike-segments.md) | Naive-Bayes layer reclassifies all 194 bicycle segments as Train | Major |
| [BUG-009](bug-reports/BUG-009-local-build-empty-photos.md) | Local build without `--photos` silently wipes the photos feed | Minor (open, mitigated) |
| [BUG-010](bug-reports/BUG-010-d1-force-resync-partial-state.md) | Full D1 resync over HTTP API leaves the database partially populated at 65 K rows | Major |
| [BUG-011](bug-reports/BUG-011-feed-calendar-double-count.md) | Feed calendar double-counts months after jump navigation | Minor |
| [BUG-012](bug-reports/BUG-012-inline-lookup-dict-drift.md) | Copy-pasted lookup dicts drift: shouts page had icons for 40 of 559 categories | Minor–Major |
| [BUG-013](bug-reports/BUG-013-d1-watermark-sync-drift.md) | Watermark-based D1 sync silently drops backdated check-ins, keeps orphaned venues | Major |
