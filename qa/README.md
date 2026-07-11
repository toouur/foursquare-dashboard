# QA

Quality documentation for this project. The automated suite itself lives in
[`tests/`](../tests/) (219 pytest tests) and is documented file-by-file in the main
[README](../README.md#tests); this directory holds the thinking around it.

| Document | What it is |
|----------|------------|
| [test-strategy.md](test-strategy.md) | Risk analysis → test pyramid (175 unit / 22 API / 14 E2E / 8 a11y), quality gates per lifecycle stage, test-data strategy, and the honest list of what is deliberately **not** tested and why |
| [exploratory-checklist.md](exploratory-checklist.md) | Manual pre-release charter: what a human checks that automation can't (visual layout, generated prose quality, post-deploy spot checks) |
| [bug-reports/](bug-reports/) | Five real defects found and fixed in this project, written up Jira-style: repro, expected/actual, root cause, fix, regression coverage, and lessons |

## Bug report index

| ID | Title | Severity |
|----|-------|----------|
| [BUG-001](bug-reports/BUG-001-nfd-phantom-cities.md) | NFD-encoded city names bypass every normalization rule → phantom cities | Major |
| [BUG-002](bug-reports/BUG-002-feed-edge-cache-shape.md) | Edge cache serves old API tuple shape for 1 h after deploy → broken feed | Major |
| [BUG-003](bug-reports/BUG-003-ratings-quota-burn.md) | Hourly refetch burns undocumented monthly API quota in 3 days → 402 for weeks | Major |
| [BUG-004](bug-reports/BUG-004-year-straddle-trip-end.md) | Trips straddling New Year vanish from the January narrative | Minor |
| [BUG-005](bug-reports/BUG-005-gateway-border-city.md) | Border-crossing venues get city from one country, country from the other | Minor–Major |
