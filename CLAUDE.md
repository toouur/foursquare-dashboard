# CLAUDE.md

Repository guidance for Claude Code.

## Quick Commands

### Build dashboard (recommended Python)
```bash
/c/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe scripts/build.py \
  --input C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --config-dir config --output-dir _site
```
> Local builds write generated HTML + JSON into `_site/` (gitignored) to keep the
> repo root clean. CI builds into its own clean runner and assembles/deploys `_site/`
> separately — the local folder is only for preview.

### Build with photos
```bash
/c/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe scripts/build.py \
  --input C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --config-dir config --output-dir _site \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json \
  --pix-url "https://pub-5514667a5da04a75986022e39efc7118.r2.dev/pix"
```

### Fetch check-ins
```bash
python scripts/fetch_checkins.py --token "$FOURSQUARE_TOKEN" --csv data/checkins.csv
python scripts/fetch_checkins.py --full
```

### Fetch tips
```bash
python scripts/fetch_tips.py --token "$FOURSQUARE_TOKEN" --out data/tips.json
python scripts/fetch_tips.py --full --sweep --csv data/checkins.csv --out data/tips.json
```

### Fetch flights (FlightRadar24 diary)
No FR24 API. `fetch_flights.py` logs in with `FR24_LOGIN=email:password` (plain
JSON login, no CAPTCHA), does the `my.flightradar24.com` SSO handshake, and
downloads the diary CSV from `/public-scripts/export`. Login mints a fresh
session each run — nothing to expire. `FR24_COOKIE` (full `Cookie:` header) is a
fallback used only when `FR24_LOGIN` is unset. Exit 0 valid / 2 auth-invalid /
1 transient; prints `COOKIE_VALID=` / `CHANGED=`. Runs weekly in CI
(`fr24-flights.yml`, Sun 05:00 UTC) and commits `flights.csv` to the data repo.
```bash
export FR24_LOGIN='email@example.com:password'
python scripts/fetch_flights.py --out C:/Users/toouur/Documents/GitHub/foursquare-data/flights.csv
python scripts/fetch_flights.py --check   # probe auth, no write
```

### Fetch photos from export
```bash
/c/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe scripts/fetch_photos.py \
  --token "$FOURSQUARE_TOKEN" \
  --export path/to/export/photos/ \
  --csv C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json \
  --pix-dir C:/Users/toouur/Documents/GitHub/foursquare-data/pix/
```

### Back-fill photos onto old check-ins
Use to add a photo to a historical check-in (e.g. so the year-page month
timeline shows its own picture instead of borrowing from a neighbour).
```bash
# Single
python scripts/add_photo.py \
  --token "$FOURSQUARE_TOKEN" \
  --checkin-id 5f8a... \
  --photo path/to/image.jpg \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json \
  --pix-dir C:/Users/toouur/Documents/GitHub/foursquare-data/pix/

# Batch (CSV with header: checkin_id,photo_path)
python scripts/add_photo.py --token "$FOURSQUARE_TOKEN" --batch backfill.csv \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json \
  --pix-dir C:/Users/toouur/Documents/GitHub/foursquare-data/pix/
```

### D1 sync (manual / local)
```bash
export CF_D1_TOKEN=your_token
export CF_ACCOUNT_ID=your_account_id
export CF_D1_DATABASE_ID=52210bd9-a019-415e-8f12-6a73b42278f9
python scripts/sync_to_d1.py \
  --csv     C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --tips    C:/Users/toouur/Documents/GitHub/foursquare-data/tips.json \
  --ratings C:/Users/toouur/Documents/GitHub/foursquare-data/venueRatings.json \
  --lists   C:/Users/toouur/Documents/GitHub/foursquare-data/lists.json \
  --trips   trips_meta.json
```

### D1 force resync (after data export / manual correction)
Use `--force-*` flags to DELETE and fully reinsert a table, bypassing CI change gates.
Use after Foursquare data export comparison (neutrals/dislikes updated), or when rows
need to be removed (un-rated venues, deleted tips, list overhaul).
```bash
# Force resync individual tables (combine as needed):
python scripts/sync_to_d1.py \
  --csv     C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --tips    C:/Users/toouur/Documents/GitHub/foursquare-data/tips.json \
  --ratings C:/Users/toouur/Documents/GitHub/foursquare-data/venueRatings.json \
  --lists   C:/Users/toouur/Documents/GitHub/foursquare-data/lists.json \
  --trips   trips_meta.json \
  --force-ratings --force-tips --force-trips --force-lists
# Or via GitHub Actions: Actions → "Force resync D1 tables" → tick the tables to reset

# Force resync checkins + venues (use after stale-row cleanup / archive dedup):
# WARNING: --force-checkins via Python API is UNRELIABLE for 65K rows (network failures
# leave D1 in partial state). Use the wrangler SQL dump approach instead:
#
#   1. Generate the dump:
python scripts/gen_d1_dump.py \
  --csv C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --out C:/tmp/checkins_venues_dump.sql
#
#   2. Execute via wrangler (PowerShell):
#      $env:PATH = "C:\Program Files\nodejs;" + $env:PATH
#      $env:CLOUDFLARE_API_TOKEN = "<token>"
#      npx wrangler d1 execute swarmdata --file="C:\tmp\checkins_venues_dump.sql" --remote
```

### After "Archive check-in snapshot" — sync venue changes to D1
Run after `sync_venue_changes.py` patches a new CSV snapshot. This applies targeted
`UPDATE checkins SET field WHERE venue_id` for each changed venue + records an audit row.
```bash
# 1. Diff old vs new snapshot, patch tips.json, write diffs JSON
python scripts/sync_venue_changes.py \
  --old C:/Users/toouur/Documents/GitHub/foursquare-data/archive/checkins_PREV.csv \
  --new C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --tips C:/Users/toouur/Documents/GitHub/foursquare-data/tips.json \
  --out  /tmp/venue_diffs.json

# 2. Apply diffs to D1 (targeted UPDATE + venue_changes audit table)
python scripts/sync_to_d1.py \
  --csv     C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --tips    C:/Users/toouur/Documents/GitHub/foursquare-data/tips.json \
  --ratings C:/Users/toouur/Documents/GitHub/foursquare-data/venueRatings.json \
  --lists   C:/Users/toouur/Documents/GitHub/foursquare-data/lists.json \
  --trips   trips_meta.json \
  --venue-changes /tmp/venue_diffs.json
```

### Delete check-in(s) by ID
Removes rows from CSV + D1, cleans orphaned venues. Use for deleted / accidental check-ins.
Also available as `delete-checkin` workflow (Actions tab).
```bash
python scripts/delete_checkin.py \
  --ids CHECKIN_ID1,CHECKIN_ID2 \
  --csv C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --dry-run   # optional
```

### Local D1 dev (Wrangler)
```bash
npx wrangler pages dev . --d1 DB=52210bd9-a019-415e-8f12-6a73b42278f9
# Tests /api/search against the remote D1 database locally
```

### Local preview
```bash
python -m http.server 8000 --directory _site
```

### Run tests / lint
```bash
# Offline suite (118 unit + parity tests, no network/secrets, seconds) — run before committing script changes
/c/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/ -m "not live" -q

# Live suite (22 API contract + 14 Playwright E2E + 8 axe-core a11y against the deployed site)
/c/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/ -m live -q

# Lint (config: ruff.toml — E401/E701/E702/E731 deliberately ignored, house style)
/c/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe -m ruff check scripts/ tests/

# Type-check (config: [mypy] in setup.cfg — pragmatic gradual-typing settings; must stay clean)
/c/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe -m mypy

# Validate generated HTML (same gate CI runs before every deploy)
/c/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe scripts/validate_html.py --dir _site
```

## Data and Build Model

- Site is static HTML generated by scripts. Generated pages are **NOT committed to git**
  (they are gitignored — committing them hourly bloated history to 4 GB+ and was removed
  via `git-filter-repo` in June 2026). CI rebuilds them and deploys via direct upload:
  `wrangler pages deploy _site` (see `ops/deploy.md` runbook).
- Static pages that stay tracked in git: `solution.html`, `sitemap.xml`, `robots.txt`, `favicon.svg`.
- Primary private data repo:
  `C:\Users\toouur\Documents\GitHub\foursquare-data`
  (contains `checkins.csv`, `tips.json`, `photos.json`, `pix/`).
- Main orchestrator: `scripts/build.py`.
- Core pipeline:
  `transform.py -> metrics.py -> template/gen scripts -> *.html`.

## Key Files

- Build/orchestration: `scripts/build.py`
- Normalization: `scripts/transform.py`
- Metrics/trips: `scripts/metrics.py` (also `collect_companions`, `shout_records`, `shout_analysis`, `cross_dim_analysis`)
- Tips generation: `scripts/gen_tips.py` (also exports `CTRY_NORM`, loaded from `config/country_aliases.json`)
- Photos generation: `scripts/gen_photos.py`
- Shouts page: `scripts/gen_shouts.py` (free-text comment archive, ~3.5k entries)
- Guide page: `scripts/gen_guide.py` (live nearby suggestions, 48h session history)
- Trip pages: `scripts/gen_trip_pages.py` (per-trip detail HTML)
- Check-in delete: `scripts/delete_checkin.py`
- D1 SQL dump: `scripts/gen_d1_dump.py` (bulk resync path)
- Tips fetch: `scripts/fetch_tips.py`
- Check-ins fetch: `scripts/fetch_checkins.py`
- Flights fetch: `scripts/fetch_flights.py` (FR24 login → diary CSV; weekly `fr24-flights.yml`)
- D1 sync: `scripts/sync_to_d1.py`, `scripts/d1_client.py`
- Tests: `tests/` — pytest suite (162 tests): offline unit tests for transform/trips/companions/shouts/transport-mode, `live`-marked API contract tests, `live`+`e2e` Playwright smoke tests + axe-core a11y audit (`test_a11y.py`, fails on NEW critical/serious rules only — pre-existing debt lives in its `KNOWN_ISSUES` baseline), Py↔JS companion parity (extracts `collectCompanions` verbatim from feed.js, runs under node). Markers registered in `tests/conftest.py` (also has `make_row()` factory). CI: `.github/workflows/tests.yml` — lint (ruff+mypy) + unit on push/PR touching scripts/tests/functions/config/setup.cfg; live suite weekly (Mon 06:00 UTC) + manual dispatch only, so a site outage never blocks a push.
- HTML deploy gate: `scripts/validate_html.py` — runs in `update-dashboard.yml` before every deploy (required pages present, no leftover `{{PLACEHOLDER}}`, embedded JSON parses, min page size; skips `solution.html` which quotes placeholders as documentation).
- Lint config: `ruff.toml` (E4/E7/E9+F; E401/E701/E702/E731 ignored deliberately).
- Type-check config: `setup.cfg` `[mypy]` — `files = scripts`, ignore_missing_imports, allow_redefinition, var-annotated disabled; tree is CLEAN (0 errors / 55 files) and must stay so (runs in the tests.yml lint job). NOTE: allow_redefinition does NOT cover names with an explicit annotation or cross-branch rebinds — rename instead.
- Other QA workflows: `lighthouse.yml` (weekly Mon 07:00 UTC, 4 pages, score floors perf≥60 / a11y+bp+seo≥85), `k6-load.yml` (manual, /api/search, fail >1% errors or p95>1s), `mutation.yml` (manual, mutmut over transform.py, config in setup.cfg `[mutmut]`).
- Failure alerting: `update-dashboard.yml` sends a Telegram message after 2 consecutive scheduled failures (secrets `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`; step no-ops if unset).
- Search API (Cloudflare Pages Function): `functions/api/search.js`
- Other Pages Functions: `functions/api/{feed,search-venues,venue-tips,custom-list,health}.js` (feed.js also has `collectCompanions()` mirroring metrics.collect_companions; health.js = uptime probe: D1 count + latest check-in age + feed_meta total, 200 ok / 503 degraded)
- Pages config: `wrangler.toml`
- Config (canonical lookups — single source of truth, edited as one-line additions):
  - `config/country_aliases.json` — raw native country name → English canonical (was inline `CTRY_NORM` in gen_tips.py)
  - `config/country_flags.json` — English country → ISO 3166-1 alpha-2 (was inline CTRY_CODE/ISO2 dicts in 9 templates + gen_photos.py)
  - `config/category_icons.json` — Foursquare category → `[emoji, color]` (was inline CAT_ICON dicts in 6 templates)
- Config (other): `config/city_merge.yaml`, `config/city_fixes.json`, `config/venue_fixes.json`, `config/city_canonical.yaml`, `config/country_fixes.json`, `config/categories.json`, `config/settings.yaml`

## City normalization pipeline (priority order)

0. `venue_fixes.json` — per-venue_id `{city, country}` override (HIGHEST priority).
   One entry pins a venue's city/country for **all past + future** check-ins.
1. `country_fixes.json` — per-ts country override.
2. `city_fixes.json` — per-ts city override.
3. `city_merge.yaml` — raw→canonical string map for non-blank rows.
4. `fill_city_inferred.py` — centroid Haversine match for blank rows; sets `city_inferred=1` in D1.

Note: `city_fixes.json` is applied **only by unix-ts** in `transform.py`. The
~1.6k 24-char-hex keys in it are `checkin_id`s used **only** by
`check_city_drift.py` to suppress already-reviewed rows — they are NOT applied as
overrides at build. To override a single venue use `venue_fixes.json` (by
venue_id); to override a single check-in use a ts key in `city_fixes.json`.

`config/city_canonical.yaml` is the single source of truth for blank-row recovery:
`canonical_map` (raw→canonical), `valid_canonical` (whitelist), `large_canonical`
(km-bucket override), `thresholds`, `skip_set`, `skip_patterns`.
`scripts/check_city_config.py` is a CI gate verifying canonical_map values and
thresholds keys all exist in `valid_canonical`, city_fixes.json keys are
numeric ts or 24-char hex ids, and venue_fixes.json keys are 24-char hex with a
non-empty city/country.

### Gateway check-in rule (border crossings, airports)

Border/airport check-ins are "gateways" — Foursquare often tags them with a city
or country from the *wrong* side (e.g. "Belarus-Poland Border Crossing" comes back
`city=Terespol` (PL) but `country=Belarus`). Standard rule:

- **Assign each gateway venue to the PHYSICAL side it sits on** (decide by its
  coordinates), then let the trip's own sequence of distinct posts show the
  direction. Do **not** try to attribute a crossing to "the side you enter": a
  per-venue fix is one venue_id → one fixed city/country applied to *every*
  check-in both ways, so it cannot know travel direction. Tagging by direction
  would also double up (entering Belarus you'd get the crossing *and* the first
  Belarusian post both as Brest) and would be wrong on the return trip.
- Example (Terespol↔Brest on the Bug, three venues west→east): `Terespol Border
  Crossing` (lng 23.644) → Terespol, Poland; `Belarus-Poland Border Crossing`
  (lng 23.653, mid-river/on the line) → Terespol, Poland (tie on the line, pinned
  to PL so PL↔BY reads as one clean transition in both directions and never
  doubles the real Brest post); `Пункт пропуска «Брест»` (lng 23.660) → Brest,
  Belarus.
- Implement each gateway as a **single `venue_fixes.json` entry** (by venue_id), so
  every historical and future check-in at that venue is consistent automatically —
  do **not** add per-ts entries per trip.
- Transit points *between* gateways (motorway/rest-area venues with blank city)
  would otherwise snap to the nearest big-city centroid (≤90 km in
  `transform.build_blank_city_resolver`). Pin them to the nearest real town with a
  per-ts `city_fixes.json` entry (these venues span a whole road, so a venue_id
  rule would be wrong).

### Blank-city recovery loop
```bash
python scripts/analyze_blanks.py > C:/tmp/blanks_output.txt
python scripts/extract_blank_fixes.py > C:/tmp/blank_fixes.txt
python scripts/apply_blank_fixes.py
python scripts/check_city_config.py
```
To accept a new raw nearest-city name, add it to `canonical_map` in
`city_canonical.yaml` — do not edit `extract_blank_fixes.py`.

## Stable Implementation Notes

- Most generators now use plain `templates/*.tmpl` files; a few legacy ones still embed base64 templates (`_TMPL_B64`). Check before editing.
- `fetch_tips.py --sweep` is required to recover tips missing from `/users/self/tips` (often closed/deleted venue cases).
- Tips UI normalization path: country via `CTRY_NORM` (loaded from `config/country_aliases.json`), city via `city_merge.yaml`.
- `window._catIcon = catIcon` is used so index tips can reuse category icon logic from check-ins block.
- Build placeholders are simple string substitution (`{{PLACEHOLDER}}`), not Jinja.
- **Post-process placeholder pass** in `build.py`: after every generator runs, every output HTML file gets `{{CTRY_CODE_JSON}}` and `{{CAT_ICON_JSON}}` substituted from `config/country_flags.json` and `config/category_icons.json`. Generators don't thread these through their kwargs — they just leave the placeholder literally in the template.
- Companion display reads from THREE columns (`with_name`, `created_by_name`, `overlaps_name`) — combined via `metrics.collect_companions()` on the Python side and the JS mirror `collectCompanions()` in `functions/api/feed.js`. The case-insensitive dedup uses first-seen casing; the Foursquare `-` sentinel for overlaps is excluded.
- Search is served by `functions/api/search.js` (Cloudflare Pages Function at `/api/search?q=`). It queries D1 directly — no static `search-index.json` is generated or committed.
- `sync_to_d1.py` is incremental: checkins append-only, venues only for touched IDs, tips/ratings/lists gated by `--tips-changed` / `--ratings-changed` / `--lists-changed` flags (CI passes fetch step outputs).
- **Ratings fetch is throttled inside the hourly job.** `/users/self/venuelikes` (the only rating endpoint that returns data — okays/dislikes 402 permanently) is metered by a MONTHLY premium-call quota (~220 calls, empirical) that resets on the 1st and 402s once spent; each fetch is ~7-8 calls (full re-paginate). The "Fetch venue ratings" step in `update-dashboard.yml` therefore only fires at 04:00 UTC when `$((10#$(date -u +%j) % 3)) == 0` (≈ every 3 days, ~80 calls/mo ≈ 36% of budget). `workflow_dispatch` bypasses the gate; the throttle CANNOT backfill an already-exhausted month (wait for the 1st, or use the data-export + `--force-ratings` path). See memory `project_ratings_likes_quota.md`.
- Companion search covers all three source fields: `with_name`, `created_by_name` (UNION query), and `overlaps_name` (comma-separated, split in JS).
- `/api/feed` cache header: `public, max-age=60, s-maxage=3600, stale-while-revalidate=600`. Schema-shape changes propagate immediately via the `_v=<tag>` query param on every fetch URL — bump the tag (currently `_v=companions`) whenever the response tuple shape changes, otherwise edge nodes serve old tuple lengths to the destructure for up to an hour.
- Feed (`feed.html` / `functions/api/feed.js`) uses a **contiguous-array virtual scroll**:
  - Single `ALL` array; init fetches the 100 newest items only (`revDone=true` from the start).
  - `loadFwd()` appends older items (`?cursor=TS`, 50 at a time); `loadRev()` prepends newer items (`?after=TS`) with scroll-position correction to prevent viewport jumps.
  - `buildPos()` estimates `totalH` as `loadedH + remaining*AVG_ITEM_H`, with `Math.max` guard to prevent shrinking during incremental loads.
  - Navigation (`goYMD`, `goLatest`, `goOldest`) resets state: `_loadGen++`, clears `ALL`, `totalH=0`, then `buildPos()`. The generation counter discards in-flight stale fetches.
  - `renderCal` uses authoritative `YM_IDX[ym]` counts from `feed_meta.json` (not an accumulated local counter) to prevent double-counting after state resets.
  - `feed_meta.json` (static, generated at build) provides calendar month counts and total — no D1 query needed.
  - **No background preload loop** — data loads only when the user scrolls near the edge.

## Known Gotchas

- System Python may miss dependencies; prefer Python 3.12 path above.
- CSS `:visited` does not reliably support CSS variables; use literal color values when needed.
- Chart/config brace balance errors break pages silently; validate after edits.
- Companion name overrides require exact `with_name` string matches.
- Building locally without `--photos` and `--pix-url` will emit `const photos=[]` in `index.html`, wiping the recent photos feed. Always pass both args when rebuilding. (Generated HTML is no longer committed, so a bad local build can't leak into git — but it would still deploy if you ran `wrangler pages deploy` from it.)
- The D1 binding (`DB`) must be configured in the Cloudflare Pages dashboard (Settings → Functions → D1 database bindings). Without it, `/api/search` returns 503.

## Deployment Notes

- Deploy target: Cloudflare Pages, project `4sq`, via **direct upload** — the hourly
  `update-dashboard.yml` job fetches data, rebuilds HTML, syncs D1, assembles a clean
  `_site/` (root asset globs + `assets/` + `functions/`; never `scripts/`/`config/`/
  `private-data/`) and runs `npx wrangler@3 pages deploy _site --project-name 4sq`.
  **Pages git auto-deploy must stay DISABLED** — a git-triggered build of the HTML-less
  tip would publish a broken site. Full runbook: `ops/deploy.md` (lives outside `docs/`
  because that path is gitignored).
- Pause switch: repo Variable `UPDATES_PAUSED=true` halts the hourly update job and the
  monthly Netlify mirror job.
- Photo hosting: Cloudflare R2 under `/pix` prefix.
- Search backend: Cloudflare D1 database `swarmdata` (ID `52210bd9-a019-415e-8f12-6a73b42278f9`), queried by `functions/api/search.js`.
- D1 binding must be added manually in CF dashboard: Pages → 4sq → Settings → Functions → D1 database bindings → Variable: `DB`, database: `swarmdata`.
- Common required secrets in CI:
  `FOURSQUARE_TOKEN`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID`, `R2_BUCKET_NAME`, `R2_PUBLIC_URL`,
  `CF_D1_TOKEN`, `CF_ACCOUNT_ID`, `CF_D1_DATABASE_ID`, `DATA_REPO_PAT` (Contents R/W on
  toouur/foursquare-data), `NETLIFY_*` (monthly mirror).
  `CF_D1_TOKEN` needs BOTH Account→D1→Edit and Account→Cloudflare Pages→Edit — the same
  token is reused for the wrangler Pages deploy.

## Working Style

- Usually work on `main`.
- After data/config/template edits: rebuild, smoke-check generated HTML, then commit.
- After `scripts/` edits: run ruff + mypy + the offline pytest suite (`-m "not live"`) before committing — CI (`tests.yml`) runs the same three gates on push.
