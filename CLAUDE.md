# CLAUDE.md

Repository guidance for Claude Code.

## Quick Commands

### Build dashboard (recommended Python)
```bash
/c/Users/toouur/AppData/Local/Programs/Python/Python314/python.exe scripts/build.py \
  --input C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --config-dir config --output-dir _site
```
> Local builds write generated HTML + JSON into `_site/` (gitignored) to keep the
> repo root clean. CI builds into its own clean runner and assembles/deploys `_site/`
> separately — the local folder is only for preview.

### Build with photos
```bash
/c/Users/toouur/AppData/Local/Programs/Python/Python314/python.exe scripts/build.py \
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

### Fetch Last.fm scrobbles (per-year "year in sound")
No CSV export — pulls `user.getRecentTracks` with `LASTFM_API_KEY`. Two-file model:
`lastfm_state.json` (full per-year artist→plays counters + month histogram + `last_ts`
watermark, ~700KB incremental cache) and `lastfm_years.json` (compact derived per-year
`{scrobbles, top_artist:{name,plays}, months:[12]}`, ~3KB, the build input). Each run
fetches only scrobbles after `last_ts` and folds them in. Exit 0 ok / 2 key-invalid /
1 transient; prints `KEY_VALID=` / `CHANGED=`. Runs weekly in CI (`lastfm.yml`, Sun
06:00 UTC, honors `UPDATES_PAUSED`, 5th data-repo writer with the BUG-014 rebase loop)
and commits both files to the data repo. `build.py` auto-discovers `lastfm_years.json`
next to `checkins.csv` and threads it to `gen_year_pages.build_page(music_by_year=...)`.
```bash
export LASTFM_API_KEY=...
# One-off bootstrap from a full local export (no key needed):
python scripts/fetch_lastfm.py --bootstrap path/to/all_scrobbles_api.json \
  --out   C:/Users/toouur/Documents/GitHub/foursquare-data/lastfm_years.json \
  --state C:/Users/toouur/Documents/GitHub/foursquare-data/lastfm_state.json
# Weekly incremental:
python scripts/fetch_lastfm.py --user TOOUUR \
  --out   C:/Users/toouur/Documents/GitHub/foursquare-data/lastfm_years.json \
  --state C:/Users/toouur/Documents/GitHub/foursquare-data/lastfm_state.json
python scripts/fetch_lastfm.py --user TOOUUR --check   # probe key, no write
```

### Build pre-2012 travel backfill ("refurbished" check-ins)
The Foursquare dataset starts in 2012; earlier travel (~2008+) is reconstructed at
country/city/day granularity from a hand-editable `backfill.yaml` (next to
`checkins.csv` in the data repo). `build_backfill.py` converts it to `backfill.csv`
in the exact 23-column check-in schema; `build.py` auto-discovers that sibling CSV and
merges its rows into `rows` (re-sorted by `date`), so every stat/KPI/map/trip/feed
shifts. Reconstructed rows carry `source_app="refurbished"` (the discriminator — real
rows are only Swarm/Foursquare/Pebble) and `checkin_id="rf<seq>"` (e.g. `rf0001`,
never collides with 24-char hex). Coords resolve explicit→existing-city-centroid (from
checkins.csv)→gazetteer→unresolved; coarse dates fill to noon UTC (month→1st,
year→Jul 1). Each reconstructed check-in renders with a `↺ reconstructed` badge (feed,
trips modal, index recent); a fully-reconstructed year page gets a hero-eyebrow badge.
`sync_to_d1.py --backfill backfill.csv` also ingests them so `/api/feed` + `/api/search`
return them (blank venue_id → no venue/FTS row; feed.js emits `source_app` as a 13th
tuple field, `_v=refurb` cache tag). **D1 reconcile is by CONTENT, not id-presence:**
the normal checkin sync is append-only (`INSERT OR IGNORE` — inserts a missing id but
never UPDATEs one already in D1), so a row re-edited behind a reused `rf` id served
stale in `/api/feed` (which reads venue/city/category denormalized off the checkins
row). `sync_to_d1.py` therefore holds `rf*` out of the append-only path and reconciles
them every sync via `DELETE FROM checkins WHERE id LIKE 'rf%'` + reinsert — cheap,
idempotent, self-healing, and it does NOT set `changed` (checkins carry no FTS index).
**Same venue + same day = one check-in:** the day-granularity model treats a
reconstructed day as a *presence*, not a per-post log, so multiple diary posts at one
venue on one day collapse to the **earliest** (mirrors the Polarsteps adapter's
same-day-repeat collapse). E.g. two 2-Aug-2008 posts at Str. Alecu Russo, 55 (20:00 +
22:00) → keep 20:00, drop 22:00. Sources funnel through `backfill.yaml`:
`import_polarsteps.py` (Polarsteps export ZIP → appends entries; PENDING user export),
LiveInternet diary, and manual entry.
```bash
python scripts/build_backfill.py \
  --yaml      C:/Users/toouur/Documents/GitHub/foursquare-data/backfill.yaml \
  --checkins  C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --out       C:/Users/toouur/Documents/GitHub/foursquare-data/backfill.csv
# then a normal build auto-merges the sibling backfill.csv; sync to D1 with:
python scripts/sync_to_d1.py --csv .../checkins.csv --backfill .../backfill.csv [other --* flags]
```

### Fetch photos from export
```bash
/c/Users/toouur/AppData/Local/Programs/Python/Python314/python.exe scripts/fetch_photos.py \
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
# Or via GitHub Actions: Actions → "Resync D1 (manual)" → mode: force → tick the tables to reset

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
**Every file that denormalizes the venue name must be passed, or it silently rots.**
`checkins.csv` is only the source of truth — the same name is copied into `tips.json`,
`backfill.csv`, `venueRatings.json` and `comments.json`. `--backfill` matters most:
reconstructed `rf*` rows reuse the SAME real venue_ids, so a rename that lands in
`checkins.csv` would leave one venue_id with two names in every build — and in
`/api/feed` permanently, since rf* rows are reconciled on every sync and the stale name
gets re-seeded each run. `archive-checkins.yml` passes all four automatically.

**`[geo_pinned]` opts a reconstructed row out of GEO syncing.** `TRACKED` is
`venue, city, country, lat, lng, category`, and `patch_backfill()` pushes all of them
into every `rf*` row sharing the venue_id — which silently clobbers geography that was
localised on purpose. Foursquare pins one coordinate per venue, but a reconstructed row
may sit elsewhere: a river checked in upstream of the card's point (`Râul Nistru` is
pinned at Vadul lui Vodă, rf1008 stands at the Dubăsari dam), or a country row placed at
the actual border crossing instead of the capital. Put the literal marker `[geo_pinned]`
anywhere in that row's `shout` and `city/country/lat/lng` survive the sync; `venue` and
`category` still follow, because one venue_id must never carry two display names. Rows
with a blank `venue_id` are already immune — the patcher skips them entirely.
`refresh_backfill_venue.py` honors the same marker (its `GEO_FIELDS` also covers
`state`, `neighborhood` and `address`, because it writes those too — a row pinned to the
Otaci crossing must not keep its city while inheriting the country card's state). All 41
`Country` rows in `backfill.csv` carry the marker: Foursquare pins a country to its
capital or centroid, so an unpinned refresh would drag every border crossing back to
Kyiv/Moscow — see `country_checkins_audit.md` in the data repo.

**The diff is snapshot-vs-refetch, so never "pre-fix" a rename by hand.** The workflow
snapshots `checkins.csv` at the START of its run and only then does the `--full`
re-fetch. Correcting the name in the CSV yourself makes both sides identical, the diff
comes out empty, and D1 keeps the stale name forever (the check-in sync is append-only).
Let the workflow see the old name and do the rename itself. **A PARTIAL pre-fix is just
as fatal**: `load_csv_by_venue()` keeps only the freshest row per venue, so one row left
on the new name defeats the whole comparison. `load_name_variants()` now widens the NAME
check to every spelling the old snapshot uses (coords stay freshest-vs-freshest — older
rows legitimately carry stale coordinates), which also fixes the real-world case of a
venue renamed mid-month and re-visited before the archive run.

**`archive-checkins.yml` MUST pass `--backfill` to `sync_to_d1.py`.** The venues table is
reconciled against the rows the run loads, and 31 reconstructed venues (Gin Do &
Contrabass, Biblioteca M. V. Lomonosov, Zatoka, Leogrand, …) have NO real check-in at
all. Without the flag they read as orphans and are DELETEd every month, then re-inserted
by the next hourly run — `/api/search` loses those places in between, and the drift
warning compares D1 (real + `rf*`) against `checkins.csv` alone, inflating the reported
gap by the whole backfill (681 vs the 3 genuinely stale rows on 1 Aug 2026).
```bash
# 1. Diff old vs new snapshot, patch tips/backfill/ratings/comments, write diffs JSON
python scripts/sync_venue_changes.py \
  --old C:/Users/toouur/Documents/GitHub/foursquare-data/archive/checkins_PREV.csv \
  --new C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --tips C:/Users/toouur/Documents/GitHub/foursquare-data/tips.json \
  --backfill C:/Users/toouur/Documents/GitHub/foursquare-data/backfill.csv \
  --ratings  C:/Users/toouur/Documents/GitHub/foursquare-data/venueRatings.json \
  --comments C:/Users/toouur/Documents/GitHub/foursquare-data/comments.json \
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
Also available as `delete-checkin` workflow (Actions tab), which additionally deletes the
freed images from R2 and deploys the rebuilt site to Pages (the hourly rebuild is gated on
a *fetch* change, so a deletion would otherwise not reach the live HTML on its own).

**Always pass `--photos`** — photos.json is the only index of what's in R2, so a key left
behind by a deleted check-in renders on the photos page as a blank, dateless card.
```bash
python scripts/delete_checkin.py \
  --ids CHECKIN_ID1,CHECKIN_ID2 \
  --csv C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json \
  --dry-run   # optional

# Clean up keys stranded by earlier deletions (--ids may be omitted entirely):
python scripts/delete_checkin.py --prune-orphans \
  --csv C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json
```
`PHOTO_FILES` lists only images no longer referenced by *any* check-in — a photo inherited
by a re-created check-in is kept in R2 even though its stale key is dropped.

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
# Offline suite (259 unit + parity tests, no network/secrets, seconds) — run before committing script changes
/c/Users/toouur/AppData/Local/Programs/Python/Python314/python.exe -m pytest tests/ -m "not live" -q

# Live suite (22 API contract + 14 Playwright E2E + 8 axe-core a11y against the deployed site)
/c/Users/toouur/AppData/Local/Programs/Python/Python314/python.exe -m pytest tests/ -m live -q

# Lint (config: ruff.toml — E401/E701/E702/E731 deliberately ignored, house style)
/c/Users/toouur/AppData/Local/Programs/Python/Python314/python.exe -m ruff check scripts/ tests/

# Type-check (config: [mypy] in setup.cfg — pragmatic gradual-typing settings; must stay clean)
/c/Users/toouur/AppData/Local/Programs/Python/Python314/python.exe -m mypy

# Validate generated HTML (same gate CI runs before every deploy)
/c/Users/toouur/AppData/Local/Programs/Python/Python314/python.exe scripts/validate_html.py --dir _site
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
  `transform.py -> metrics/ -> template/gen scripts -> *.html`.

## Key Files

- Build/orchestration: `scripts/build.py`
- Normalization: `scripts/transform.py`
- Metrics/trips: `scripts/metrics/` — a **package**, not a module (`__init__.py` re-exports the
  public surface, so `import metrics` / `from metrics import process, detect_trips,
  collect_companions, …` keeps working): `stats.py` (`process` — aggregations/KPIs —
  `cross_dim_analysis`, the era-aware `home_at_ts` closure), `trips.py` (`detect_trips` + the
  8-pass extension pipeline, `_home_at`, `_COUNTRY_TZ`), `companions.py`
  (`collect_companions`), `shouts.py` (`shout_records`, `shout_analysis`,
  `merge_comments_into_shouts`).
- Tips generation: `scripts/gen_tips.py` (also exports `CTRY_NORM`, loaded from `config/country_aliases.json`)
- Photos generation: `scripts/gen_photos.py`
- Shouts page: `scripts/gen_shouts.py` (free-text shout archive, ~4.5k entries — 3.8k shouts analyzed + ~650 comment-only check-ins; ~980 carry comment threads)
- Comments fetch: `scripts/fetch_comments.py` (+ `fetch-comments.yml`) → `comments.json` in the data repo; `metrics.shouts.merge_comments_into_shouts` folds them into the shouts page (1.6k comments over ~980 check-ins)
- Concerts scrape: `scripts/scrape_forum_concerts.py` (forum gig-list crawl → concert history; feeds backfill candidates)
- Guide page: `scripts/gen_guide.py` (live nearby suggestions, 48h session history)
- Trip pages: `scripts/gen_trip_pages.py` (per-trip detail HTML)
- Country pages: `scripts/gen_country_pages.py` (per-country `country-<slug>.html`; self-contained like year pages — flags via `_flag()` + flag-icons CDN, no `{{SITE_CSS_LINK}}`, computed from `rows`+`stats_data`+`trips`). Slug = ISO alpha-2 lowercased when known else ASCII name-slug; `country_slug()` MUST mirror `countrySlug()` in `templates/index.html.tmpl` (the index country grid links here). The per-country trip cards list **all** the country's trips (no `[:12]` cap) and deep-link to `/trips.html#trip-<id>` — NOT `/trip-<id>.html` (that page is never generated; `gen_trip_pages.py` is dead code). `trips.html` has a `handleHash()` that opens the matching trip modal on load. Output gitignored (`country-*.html`).
- Syndication feeds: `scripts/gen_feeds.py` (RSS 2.0 `feed.xml` + JSON Feed 1.1 `feed.json`, newest ≤30 `recent` items; index advertises both via autodiscovery `<link>`s). Both gitignored.
- Check-in delete: `scripts/delete_checkin.py`
- D1 SQL dump: `scripts/gen_d1_dump.py` (bulk resync path)
- Tips fetch: `scripts/fetch_tips.py`
- Check-ins fetch: `scripts/fetch_checkins.py`
- Flights fetch: `scripts/fetch_flights.py` (FR24 login → diary CSV; weekly `fr24-flights.yml`)
- Last.fm fetch: `scripts/fetch_lastfm.py` (per-year scrobble aggregate; weekly `lastfm.yml`; feeds year-page "The year in sound" section via `gen_year_pages` `music_by_year`)
- Pre-2012 backfill: `scripts/build_backfill.py` (`backfill.yaml` → `backfill.csv`, 23-col schema, `source_app="refurbished"`, `rf<seq>` ids, centroid-reuse geocode); `build.py` auto-merges the sibling `backfill.csv`; `sync_to_d1.py --backfill` ingests to D1; source adapters `scripts/import_polarsteps.py` (Polarsteps export ZIP → backfill.yaml; PENDING user export) and `scripts/import_liveinternet.py` (LiveInternet diary crawl → candidate entries). `scripts/refresh_backfill_venue.py` (+ `refresh-backfill-venue.yml`) fills real Foursquare venue metadata onto a reconstructed row. **Both its endpoints can be shut at once**: `/v2/venues/{id}` is premium and 402s permanently for this token, and `/v2/venues/search` — the fallback — has been seen answering `402 credits_exhausted` too once the monthly call budget is spent (same budget the ratings fetch draws on). The run still exits 0 and commits nothing; the row keeps `[coords_approx:pending_api]` and must be retried after the quota resets on the 1st. Reconstructed rows badged in feed/trips/year-hero. Tests: `tests/test_backfill.py`.
- D1 sync: `scripts/sync_to_d1.py`, `scripts/d1_client.py`
- Tests: `tests/` — pytest suite (303 tests: 259 offline, 22 live API contract, 22 live+e2e): offline unit tests for transform/trips/companions/shouts/transport-mode/route-paths/year-covers/month-narrative/home-eras + a full build-integration smoke (`test_build_integration.py`: real build → validate_html gate + PWA/map_data + per-country-page/syndication-feed/on-this-day assertions), `live`-marked API contract tests, `live`+`e2e` Playwright smoke tests + axe-core a11y audit (`test_a11y.py`, fails on NEW critical/serious rules only — pre-existing debt lives in its `KNOWN_ISSUES` baseline), Py↔JS companion parity (extracts `collectCompanions` verbatim from feed.js, runs under node). Markers registered in `tests/conftest.py` (also has `make_row()` factory). CI: `.github/workflows/tests.yml` — lint (ruff+mypy) + unit on push/PR touching scripts/tests/functions/config/setup.cfg; live suite weekly (Mon 06:00 UTC) + manual dispatch only, so a site outage never blocks a push.
- HTML deploy gate: `scripts/validate_html.py` — runs in `update-dashboard.yml` before every deploy (required pages present, no leftover `{{PLACEHOLDER}}`, embedded JSON parses, min page size; skips `solution.html` which quotes placeholders as documentation).
- QA docs: `qa/` — `test-strategy.md` (risk analysis → pyramid → gates), `exploratory-checklist.md` (manual pre-release charter), `bug-reports/` (14 written-up real defects). `docs/` is gitignored — QA docs must live in `qa/`.
- Lint config: `ruff.toml` (E4/E7/E9+F; E401/E701/E702/E731 ignored deliberately).
- Type-check config: `setup.cfg` `[mypy]` — `files = scripts`, ignore_missing_imports, allow_redefinition, var-annotated disabled; tree is CLEAN (0 errors / 64 files) and must stay so (runs in the tests.yml lint job).
  NOTE: allow_redefinition does NOT cover names with an explicit annotation or cross-branch rebinds — rename instead.
  NOTE: a local checkout may report ~7 `Argument "params" to "get"` errors in the `fetch_*` scripts —
  that is drift from a newer locally-installed `requests` shipping its own inline types, not a code change.
- Other QA workflows: `lighthouse.yml` (weekly Mon 07:00 UTC, 4 pages, score floors perf≥60 / a11y+bp+seo≥85), `k6-load.yml` (manual, /api/search, fail >1% errors or p95>1s), `mutation.yml` (manual, mutmut over transform.py, config in setup.cfg `[mutmut]`).
- Failure alerting: `update-dashboard.yml` sends a Telegram message after 2 consecutive scheduled failures (secrets `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`; step no-ops if unset).
- Search API (Cloudflare Pages Function): `functions/api/search.js`
- Other Pages Functions: `functions/api/{feed,search-venues,venue-tips,custom-list,health}.js` (feed.js also has `collectCompanions()` mirroring metrics.collect_companions; health.js = uptime probe: D1 count + latest check-in age + feed_meta total, 200 ok / 503 degraded)
- Pages config: `wrangler.toml`
- Config (canonical lookups — single source of truth, edited as one-line additions):
  - `config/country_aliases.json` — raw native country name → English canonical (was inline `CTRY_NORM` in gen_tips.py)
  - `config/country_flags.json` — English country → ISO 3166-1 alpha-2 (was inline CTRY_CODE/ISO2 dicts in 9 templates + gen_photos.py)
  - `config/category_icons.json` — Foursquare category → `[emoji, color]` (was inline CAT_ICON dicts in 6 templates)
  - `config/year_covers.json` — /years cover + narrative pins (loader: `scripts/year_covers.py`):
    `"2024": "<photo filename or checkin_id>"` pins the YEAR cover (index card + year-page hero + og:image);
    `"2024-07": "<filename or checkin_id>"` pins that month's timeline photo (claimed up front so the auto-picker can't reuse it, honored even on empty months);
    `"2024-07-note": "text"` replaces that month's auto narrative with hand-written text (plain text, HTML-escaped).
    Without a pin, year covers use a deterministic signature score (shout + companions + photo count, earliest-ts tie-break) so covers are stable across builds. Unresolvable pin values are reported at build and ignored.
- Config (other): `config/city_merge.yaml`, `config/city_fixes.json`, `config/venue_fixes.json`, `config/city_canonical.yaml`, `config/country_fixes.json`, `config/categories.json`, `config/settings.yaml`

## Home is a TIMELINE, not a constant

The dataset spans 20 years and home moved. `config/settings.yaml` →
`trip_detection.home_history` declares the eras; `trip_detection.home_city` names the
current/final one:

```yaml
trip_detection:
  home_city: Chișinău          # current era (from the last `until` onward)
  home_history:
    - city: Chișinău
      until: "2012-09-01"      # exclusive, UTC midnight
      venues: [50fbc23ae4b0afd6fe382f57]   # Str. Alecu Russo, 55
    - city: Minsk
      until: "2026-07-29"
```

Each entry names the home for every ts strictly BEFORE its `until`; at/after the last
`until`, `home_city` applies. Runtime shape:
`[(1346457600, 'Chișinău'), (1785283200, 'Minsk')]`.

- **Anything that measures distance from / membership in "home" MUST be era-aware.**
  Resolvers: `metrics.trips._home_at(ts, home_city, home_history)` (reference impl) and
  `metrics.stats.home_at_ts(ts)`, a closure built once over the era centroids. Never read
  `home_city` alone. A single flat centroid scored ~14 years of ordinary Minsk life as a
  permanent 773 km journey (the real Chișinău–Minsk centroid distance) and marked those
  stay-at-home days as nomad days — `nomad_days` read 4125 / 84.6 % instead of the correct
  1694 / 34.7 %. Fixed across three call sites (trip furthest-point KPI, the daily
  `distance_from_home` series feeding `nomad_kpis`, the per-year "farthest reach"
  narrative); pinned by `tests/test_home_eras.py`, whose third test asserts a deliberately
  flat config MISREADS the other era so the bug cannot quietly return.
- **`home_venue_ids` is a UNION across ALL eras**, not per-era. Venue
  `50fbc23ae4b0afd6fe382f57` is load-bearing twice in `trips.py`: as the homecoming target
  of the home-arrival extension, and in the `ends_at_home` guard.
- **Adding an era**: append to `home_history` AND move `home_city` to the new current city;
  the previous final era needs its own `until` row or its years get scored against the
  wrong city.
- **Known remaining consumer**: `gen_guide.py` still derives `home_country`/`is_home` from
  the flat `home_city` — correct for the current era only.

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
`check_city_drift.py` is a **manual diagnostic — no workflow invokes it**; the CI
gate is `check_city_count.py` (see below).

`config/city_canonical.yaml` is the single source of truth for blank-row recovery:
`canonical_map` (raw→canonical), `valid_canonical` (whitelist), `large_canonical`
(km-bucket override), `thresholds`, `skip_set`, `skip_patterns`.
`scripts/check_city_config.py` is a CI gate verifying canonical_map values and
thresholds keys all exist in `valid_canonical`, city_fixes.json keys are
numeric ts or 24-char hex ids, and venue_fixes.json keys are 24-char hex with a
non-empty city/country.

### City NFC normalization + count-drift gate

- `transform.py` NFC-normalizes every `city` value (and writes it back to the row)
  **before** any string-keyed rule runs. Foursquare sometimes returns diacritic
  city names in NFD (decomposed base char + combining mark, e.g. "Sóc Sơn" as
  `o`+U+0323); NFD strings byte-mismatch the NFC keys in `city_merge.yaml`, so
  without this they bypass EVERY rule and resurface as phantom single-count cities.
  CJK has no NFC/NFD variance — a missing CJK mapping is a plain `city_merge` gap
  (e.g. traditional `北京市海淀區` needed its own entry alongside simplified `区`).
- `scripts/check_city_count.py` runs the real transform pipeline and compares the
  distinct normalized {city:count} set to `config/city_count_baseline.json`.
  HARD-fails (exit 1) on invariant bugs — a displayed city that is non-NFC or
  a fold-collision (two spellings/encodings of one place) — and on a NEW displayed
  name that is not a settlement at all: an administrative unit, a pair of places or
  a station label (`Antwerp Province`, `РФ / РБ`, `Sejny - Lazdijai`,
  `stancyja Hudahaj`). That shape check reuses `city_canonical.yaml: skip_patterns`
  so there is one source of truth, and it exempts names that are TARGETS of a
  city_merge mapping — the house style deliberately keeps `Smolensk Region` when a
  check-in genuinely happened in the region, and real names can carry an odd shape
  (`Biel/Bienne`). A non-settlement already present in the baseline reports as debt
  without blocking, so the gate guards against regressions rather than failing on
  history. **This check exists because `city_fixes.json` bypasses `city_merge`**:
  `transform.py` sets the city from it and returns early, so a raw value written
  there reaches the display verbatim and no other rule sees it. SOFT findings (exit 0,
  reported): an added city that count-pairs with a removed one (`RENAME?`, likely a
  Foursquare city rename that now needs a mapping) or a non-ASCII non-canonical
  addition (`REVIEW`). `--strict` makes SOFT block too; `--warn-only` never blocks.
  The fold key strips case, apostrophe variants **and diacritics**, so an ASCII
  spelling collides with its accented twin (`Dusseldorf` vs `Düsseldorf`) — without
  that they passed as two separate cities.
  `--auto-merge` is the **self-heal**: each fold-collision is appended to
  `config/city_merge.yaml` (`variant: keeper`, keeper = canonical/highest-count,
  ties toward the accented form) instead of failing the run, the counts are
  recomputed, and `AUTO_MERGED=<n>` is emitted so CI commits the file. Only
  provably-same-place spellings qualify, so it can never merge two real cities.
  The heal lands *after* the build, so a merged spelling first shows up in the
  next hourly rebuild. Both `update-dashboard.yml` (step 7a-bis/7a-ter) and
  `recheck-enrich.yml` (7b/7b-bis) run it this way, after HTML validate, before D1
  sync. **Refresh the baseline after an intentional city-set change** and commit it:
  `python scripts/check_city_count.py --csv private-data/checkins.csv --baseline
  config/city_count_baseline.json --update-baseline`.

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

- All generators read plain `templates/*.tmpl` files. (The old base64-embedded `_TMPL_B64` templates were fully removed — no generator embeds one anymore.)
- `fetch_tips.py --sweep` is required to recover tips missing from `/users/self/tips` (often closed/deleted venue cases).
- Tips UI normalization path: country via `CTRY_NORM` (loaded from `config/country_aliases.json`), city via `city_merge.yaml`.
- `window._catIcon = catIcon` is used so index tips can reuse category icon logic from check-ins block.
- Build placeholders are simple string substitution (`{{PLACEHOLDER}}`), not Jinja.
- **Post-process placeholder pass** in `build.py`: after every generator runs, every output HTML file gets `{{CTRY_CODE_JSON}}` and `{{CAT_ICON_JSON}}` substituted from `config/country_flags.json` and `config/category_icons.json`. Generators don't thread these through their kwargs — they just leave the placeholder literally in the template.
- **Shared site.css** is extracted to `assets/site.css` (the side-nav rail, once byte-identical across 12 templates). Post-process copies it to the output root under a content-hashed name `site-<sha1_8>.css` and substitutes `{{SITE_CSS_LINK}}` in every page that carries the placeholder.
- **Index page weight (map lazy-load)**: the four heavy map layers (`unique_places`, `venues_heatmap`, `explorer_groups`, `country_centroids`) are split out of the inline `const S={…}` into a separate `map_data.json` (written by `build()`), fetched lazily by index.html on first scroll near the `#s-map` card (IntersectionObserver, 400px rootMargin) — cut index.html from ~5.6 MB to ~1.1 MB. Those keys are used ONLY by index.html; `S.unique_places_count`/`by_year`/`explorer_cats` scalars stay inline.
- **Route-geometry warm is split across two workflows** (`build.py --routes-max-fetch N`): road-following trip polylines are fetched from OSRM/BRouter and cached in `private-data/routes_cache.json` (persisted back to the data repo). Fetching is the slow part of the build, so it's tiered:
  - `update-dashboard.yml` (hourly + push-verify + manual) builds with `--routes-max-fetch 0` — it reads the cached polylines but makes ZERO OSRM/BRouter calls, keeping the latency-sensitive poller entirely off the network-bound path. Tradeoff: a brand-new check-in's road polyline appears a day late (on the next `warm-routes.yml` cache) instead of the same build. (This was previously a small `--routes-max-fetch 40` floor so a fresh route landed the same build; dropped to 0 once `warm-routes.yml` was draining the whole backlog daily anyway.)
  - `warm-routes.yml` (separate job: daily 03:00 UTC + `workflow_dispatch` with a `max_fetch` input, default 400) drains the historical backlog — hundreds of rate-limited fetches. It only warms + commits `routes_cache.json` to the data repo (no build/validate/deploy); the drained polylines ship on the next scheduled `update-dashboard` run, which reads the fresh cache. Scheduled at 03:00 (ahead of the hourly reads) and honors the `UPDATES_PAUSED` pause switch. Without this cap a cold cache would fetch tens of thousands of segments in one run and time out.
- **Historic enrichment (photos + late friend-overlaps) is split off the hourly path into `recheck-enrich.yml`** (daily 04:15 UTC + `workflow_dispatch`, honors `UPDATES_PAUSED`). Foursquare adds photos and friend-overlap tags to a check-in *after* it was first posted (a friend who was there tags it hours/days later); catching those needs a re-scan of recent check-ins, which was the slow part of the hourly job. The hourly `update-dashboard.yml` now fetches **new** items only (its `fetch_checkins.py` call dropped `--recheck-recent-hours 48` and its `fetch_photos.py` call dropped `--recheck-days 1`) — a brand-new check-in is still fully enriched on arrival; only the historic re-scan moved out. `recheck-enrich.yml` re-scans the last 48h (`fetch_checkins.py --recheck-recent-hours 48`) + last 2 days of photos (`fetch_photos.py --recheck-days 2`), uploads new pix to R2, commits the data repo (BUG-014 5-attempt rebase-and-retry loop — it is the **4th** data-repo writer), then rebuilds + validates + deploys + syncs D1 exactly like the hourly job so the enrichment reaches BOTH the static pages and the D1-backed feed. No tips fetch, no Telegram alert.
  - **The overlaps must reach D1, not just the static rebuild.** `sync_to_d1.py` check-ins are append-only (`INSERT OR IGNORE`; the selection filter also excludes already-synced IDs), so a friend-overlap the recheck adds to an *already-synced* check-in never propagates to `/api/feed` (which reads D1) — the static pages (index/trips/companions, rebuilt from CSV) would show the companion but the feed would not. The new **`--fix-overlaps-hours N`** sync flag closes that gap: it windows to check-ins from the last N hours, chunk-queries D1 for their current `overlaps_name`/`overlaps_id`, and emits targeted `UPDATE checkins SET overlaps_* WHERE id=` for any that drifted — mirroring the proven `--fix-city-country` machinery (byte-chunked `d1._raw_with_retry`). Brand-new windowed rows aren't in D1 yet → skipped by the fix pass, carried by the normal insert. It never touches the `changed`/FTS flag (overlaps aren't indexed). `recheck-enrich.yml` passes `--fix-overlaps-hours 48`.
- **PWA** (manifest + service worker): sources are `templates/manifest.webmanifest.tmpl` + `templates/sw.js.tmpl`; post-process renders both into the output root, stamping `{{SW_VERSION}}` in sw.js with the build UTC timestamp (`%Y%m%d%H%M%S`) so the cache name `foursq-<version>` busts on every deploy, and injects `<link rel="manifest">` + `<meta name="theme-color" content="#0b0d13">` + the SW registration before `</head>` of every generated page (idempotent — skips a page already carrying `rel="manifest"`). SW strategy: cache-first for same-origin static assets (css/js/svg/png/woff), network-first for navigations/HTML with cache→shell fallback, and pass-through (never cache) for `/api/*` and the big data blobs (`feed_meta`/`map_data`/`routes`/`trips_meta`.json). Generated root artifacts (`manifest.webmanifest`, `sw.js`, `site-*.css`, `map_data.json`) are gitignored and copied into `_site` by the CI cp globs (which now include `./*.js ./*.webmanifest ./site-*.css`).
- Companion display reads from THREE columns (`with_name`, `created_by_name`, `overlaps_name`) — combined via `metrics.collect_companions()` on the Python side and the JS mirror `collectCompanions()` in `functions/api/feed.js`. The case-insensitive dedup uses first-seen casing; the Foursquare `-` sentinel for overlaps is excluded.
- Search is served by `functions/api/search.js` (Cloudflare Pages Function at `/api/search?q=`). It queries D1 directly — no static `search-index.json` is generated or committed.
- **Search backend is SQLite FTS5** (not `LIKE '%q%'`). `d1_schema.sql` defines three external-content FTS5 virtual tables — `venues_fts` / `tips_fts` / `trips_fts` (store only the inverted index; display columns read from the base table via `rowid`). `sync_to_d1.py` keeps them fresh with `INSERT INTO <t>_fts(<t>_fts) VALUES('rebuild')` after each sync that changed data (or when the index is empty) — a rebuild, NOT triggers, because `apply_schema()` splits the schema on `;` and would shred a `BEGIN..END` trigger body. **The rebuild is verified, not trusted**: after the `('rebuild')` call, sync re-counts the FTS index vs its base table (`_count()` helper); if the index is still empty while the base has rows (a silent `rebuild` no-op, or an index stranded by the wrangler bulk-resync path which never touches FTS), it hard-repopulates via `('delete-all')` + `INSERT INTO <t>_fts(rowid, <cols>) SELECT rowid, <cols> FROM <base>` (a plain write that always lands on D1). The empty-index check runs every sync (`fts_n <= 0`), so a stranded index self-heals on the next run without any manual step — this is the fix for "search returns nothing" when prod FTS indexes are empty but base tables have data. `search.js` builds a MATCH expression (each query word `"word"*` prefix-matched, AND-ed; city search scopes tokens to `{city country}`), ranks venues/tips by `bm25()`, and returns the same `{venue,city,trip,tip,companion}` short-key shape. Companions have NO FTS table (they live across three comma-joined columns of the 65k-row checkins table) and stay on `LIKE`.
- `sync_to_d1.py` is incremental: checkins append-only, venues only for touched IDs, tips/ratings/lists gated by `--tips-changed` / `--ratings-changed` / `--lists-changed` flags (CI passes fetch step outputs).
- **Check-in inserts are capped by COPY COUNT, never by id-existence** (`dedupe_against_d1`). `checkins` has no UNIQUE on `id` — `seq` is the PK so the CSV's own duplicate rows survive — which means `INSERT OR IGNORE` cannot reject a repeat. Both candidate paths read a snapshot of D1 (a `max_date` watermark, or a per-id count), so two syncs whose windows overlap (`update-dashboard` hourly vs `recheck-enrich` at 04:15, which also syncs D1) each saw the same rows as new and each inserted them — four July 2026 rows ended up stored twice. The guard caps each id at `source_count - d1_count`. Counting is load-bearing: `WHERE NOT EXISTS` / `id not in existing_ids` semantics would collapse the **12 check-ins that checkins.csv legitimately lists twice** (Суперпрод, Гуманитарный факультет БГУ, …) to one row each. It runs on the incremental path only (≤1000 candidate rows, D1 non-empty); the set-difference fallback feeds the whole source through the same helper.
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
- **Shared data-repo pushes are concurrency-hardened.** Five workflows commit to
  `toouur/foursquare-data` `main` — `update-dashboard.yml` (hourly), `warm-routes.yml`
  (daily 03:00 UTC), `recheck-enrich.yml` (daily 04:15 UTC), `fr24-flights.yml` (weekly
  Sun 05:00), `lastfm.yml` (weekly Sun 06:00). Overlapping runs (e.g. the 03:00
  route-warm still pushing as the 03:00 hourly build commits) caused non-fast-forward
  rejections (`! [rejected] main -> main (fetch first)`) that failed the run. Every
  data-repo commit step now wraps its push in the SAME bounded rebase-and-retry loop
  (`git push` → on failure `git pull --rebase origin main` → retry, 5 attempts, then
  `exit 1`). The commits touch disjoint files so rebases always apply cleanly. A repo-wide
  `concurrency` group was deliberately NOT used — it would queue the latency-sensitive
  hourly poller behind the daily backlog drain. See `qa/bug-reports/BUG-014-concurrent-data-push-race.md`.
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
