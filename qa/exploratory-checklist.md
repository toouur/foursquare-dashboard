# Exploratory / Manual Pre-Release Checklist

The automated suite (see [test-strategy.md](test-strategy.md)) covers logic, contracts,
rendering smoke, and a11y. This checklist covers what automation is *bad* at: visual
judgment, prose quality, "does this feel right", and cross-cutting one-offs. It is a
**charter, not a script** — items say what to look at and what "wrong" tends to look like
here, based on bugs this project has actually had.

Run the relevant sections after template/generator/CSS changes, before deciding a change
is done. Full pass: ~15 minutes on a local build + ~5 on production after deploy.

## 1. Build output sanity (local `_site/`, 2 min)

- [ ] Build log: no `[warn]` lines about unresolved cover pins, missing photos, skipped rows.
- [ ] Page count in `_site/` looks right (`validate_html.py` enforces the required set, but
      eyeball for unexpected *extra* stale files).
- [ ] `grep -r "{{" _site/*.html` finds nothing (validator covers this; trust but verify
      after touching the placeholder post-process pass itself).
- [ ] Open 2–3 pages **from the local build**, not prod, so you see the change you made.

## 2. Index / dashboard

- [ ] Headline stats plausible vs. yesterday (check-ins only grow; countries change rarely —
      a jump means normalization drift, see BUG-001).
- [ ] Recent-photos strip populated. Empty strip on a local build = built without
      `--photos/--pix-url` (known footgun); empty on prod = real problem.
- [ ] Charts render; no silently-empty chart area (brace-balance errors kill a chart with
      no console error visible above the fold — open devtools console).

## 3. Feed (most stateful page — most manual-test value)

- [ ] Initial load shows newest items; scroll down loads older batches without viewport jumps.
- [ ] Jump via calendar to an old month, then "Latest" — no duplicated or missing items
      after the state reset (generation-counter area; historically fragile).
- [ ] After an API-shape deploy: hard-reload with devtools Network open, confirm fetch URLs
      carry the current `_v=` tag (BUG-002 class).
- [ ] Companion names render with correct casing and no `-` sentinel entries.

## 4. Trips + transport mode

- [ ] Open one recent trip page: country sequence is physically possible (no A→B→A→B
      ping-pong around border crossings — BUG-005 class).
- [ ] Transport-mode glyphs pass the sniff test: no flights between neighboring cities, no
      "walk" spanning 300 km.
- [ ] A trip straddling New Year appears in both years correctly (departure in year N,
      ending mentioned in January of N+1 — BUG-004 class).

## 5. Years pages (generated prose — automation can't judge "human")

- [ ] Read 2–3 month narratives on different years: no "(N×)" tallies, no sentence starting
      lowercase, no "and X and more" double-conjunction, city lists match their stated count.
- [ ] Month cover photos: each month's photo belongs to that month; a pinned cover
      (`config/year_covers.json`) actually shows the pinned photo.
- [ ] Drop-cap/hero layout at ~375 px width and desktop: no torn text, no clipped hero.

## 6. Tips / photos / shouts

- [ ] Tips: country flags correct for a few non-ASCII countries; closed/deleted badges render.
- [ ] Photos page: lightbox opens, images load from R2 (`/pix` URLs, not broken relative paths).

## 7. Cross-cutting

- [ ] Mobile viewport (~375 px) pass on index, feed, one year page: no horizontal scroll,
      no overlap.
- [ ] Dark/light: pages are dark-themed by design; check `:visited` link colors specifically
      (CSS variables don't work in `:visited` — literal-color regressions have shipped).
- [ ] Console: zero errors on index, feed, trips, one year page.
- [ ] `/api/health` returns 200 with fresh `latest_checkin_age`.

## 8. Post-deploy spot check (production, ~5 min)

- [ ] Hard-reload index + feed on prod after the hourly deploy lands.
- [ ] Search a venue, a companion, and a garbage string (`zzzznope`) — sane results, sane
      empty state, no 5xx.
- [ ] If a data/config change shipped: verify the specific row/city/venue it targeted.

## Recording findings

Anything found here that survives a second look becomes either a regression test (if
automatable) or a bug report in [bug-reports/](bug-reports/) (if it teaches something) —
several reports there started as checklist findings. A finding fixed on the spot with no
lesson needs neither; don't generate paperwork.
