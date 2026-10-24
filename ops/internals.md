# Build, search and deploy internals

Full key-file map, implementation notes and deployment details. Moved out of CLAUDE.md.

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
  `trip_insights.py` (`trip_insights` — read-only views over the finished trip list for the
  trips.html analytics block: `trip_eras` (per home era: trips, days-away %, avg/median
  trip, top abroad country + gateway; an era owns the trips that START in it),
  `trip_gateways` (stations/airports that open/close trips — rides, roads and border
  crossings are not gateways), `trip_seasonality` (days away per year×month). Nothing
  here feeds back into detection or names.)
- Tips generation: `scripts/gen_tips.py` (also exports `CTRY_NORM`, loaded from `config/country_aliases.json`)
- Photos generation: `scripts/gen_photos.py`
- Shouts page: `scripts/gen_shouts.py` (free-text shout archive, ~4.5k entries — 3.8k shouts analyzed + ~650 comment-only check-ins; ~980 carry comment threads)
- Comments fetch: `scripts/fetch_comments.py` (+ `fetch-comments.yml`) → `comments.json` in the data repo; `metrics.shouts.merge_comments_into_shouts` folds them into the shouts page (1.6k comments over ~980 check-ins)
- Concerts scrape: `scripts/scrape_forum_concerts.py` (forum gig-list crawl → concert history; feeds backfill candidates)
- Guide page: `scripts/gen_guide.py` (live nearby suggestions, 48h session history)
- Trip pages: `scripts/gen_trip_pages.py` (per-trip detail HTML)
- Country pages: `scripts/gen_country_pages.py` (per-country `country-<slug>.html`; self-contained like year pages — flags via `_flag()` + flag-icons CDN, no `{{SITE_CSS_LINK}}`, computed from `rows`+`stats_data`+`trips`). Slug = ISO alpha-2 lowercased when known else ASCII name-slug; `country_slug()` MUST mirror `countrySlug()` in `templates/index.html.tmpl` (the index country grid links here). The per-country trip cards list **all** the country's trips (no `[:12]` cap) and deep-link to `/trips.html#trip-<id>` — NOT `/trip-<id>.html` (that page is never generated; `gen_trip_pages.py` is dead code). `trips.html` has a `handleHash()` that opens the matching trip modal on load. Output gitignored (`country-*.html`).
- Stats boards (stats.html): `metrics/milestones.py` (`compute_milestones` → `S.milestones`: the records & milestones board — every check-in labelled with the echo bot's rules, whose constants are mirrored and pinned against `telegram_checkin_echo.py` by `tests/test_milestones.py`) and `metrics/coverage.py` (`compute_coverage` → `S.coverage`: era-aware radius of gyration per year/month, drawn as a GitHub-contributions-style grid, plus distinct ~500 m cells per top city). Both are computed in `build.py` right after `process()`; refurbished rows are skipped by coverage.
- Venue pages: `scripts/gen_venue_page.py` + `metrics/venues.py` — one static `venue.html` that renders `#<venue_id>` from `venue_data_<xx>.json` (256 shards keyed by 32-bit FNV-1a of the id mod 256; the last hex digits of Foursquare ids are NOT uniform — one shard hit 889 KB — and `venueShard()` in the page mirrors `venue_shard()`, parity-tested under node). Every venue gets a dossier, `Home (private)` included. Linked from the stats milestones board and country-page top venues. Output gitignored.
- Compare page: `scripts/gen_compare.py` + `metrics/year_facts.py` — `compare.html?a=2015&b=era2` over `year_facts.json`: the same fact sheet for every local calendar year and every home era. Eras are computed from their own rows by ts, NOT summed from years (unique counts are not additive and era boundaries fall mid-year); `new_*` means first-ever in the whole history.
- Life timeline: `scripts/gen_life.py` + `metrics/life.py` — `life.html` ("Life in stripes") over `life_data.json`: home eras, country per day (RLE; a day without check-ins inherits the previous country, drawn faded), trips (each trip rect is an SVG `<a>` → `/trips.html#trip-<id>`; nothing else on the chart navigates), weekly density, top companions per month, top Last.fm artist per year (re-reads the sibling `lastfm_years.json`; pre-first-check-in years dropped — zero-timestamp scrobbles land in 1970).
- Cultural chronicle: `scripts/culture_sources.py` loads the data-repo siblings of checkins.csv — `imdb_export.csv` (IMDb Your Ratings export; the date is the RATING date, not the viewing date; days with ≥10 ratings are flagged `bulk`), `full_wine_list.csv` (Vivino export; ONLY rows with "Your rating"; `Scan date` is UTC — verified: scan-minus-nearest-bar-check-in peaks at 0 h) and `lastfm/` (raw scrobbles). `metrics/culture.py` joins them: a wine goes to the nearest check-in within 4 h, and a bar/restaurant wins only within 45 min (a "Corporate Cafeteria" 20 min earlier used to claim supermarket bottles); a film's `where` is cinema (movie-theatre check-in that day or ≤2 days before AND a new release) / road (inside a trip) / backlog / home; per-trip sound runs from the first to the last check-in. `scripts/gen_culture.py` writes `culture.html` + `culture_data.json`. build.py computes culture BEFORE the year pages, whose "The year in culture" section reads `culture_by_year`; the trips.html modal card "Sound · Taste · Frame" fetches the same JSON. Profile links: `settings.yaml` `culture:`. Neither IMDb nor Vivino can be fetched from CI — both sit behind AWS WAF and IMDb login is Amazon CAPTCHA/OTP — so a refresh means dropping a new export into the data repo.
- Visit counter (private): every page carries Cloudflare Web Analytics (no IPs, cities or uniques) AND our own `sendBeacon('/api/hit')`; a build.py post-process pass injects both into any page lacking them. `functions/api/hit.js` writes one D1 `visits` row per view: IP from CF-Connecting-IP, country/city/region/ASN/org/tz from `request.cf`, a random localStorage visitor id and an `owner` flag. Same-origin only, bots dropped, a visitor id must match the snippet's format, at most 60 views per IP per 10 min (no WAF rate limit is possible on pages.dev), paths starting `//` rejected, always 204, and 1 in 200 hits prunes rows older than 90 days. `functions/api/visits.js` serves aggregates ONLY with the `STATS_KEY` Pages secret in X-Stats-Key (unset = closed, 401). `visits.html` (`gen_visits.py`; noindex, not in nav or sitemap, robots-disallowed) asks for the key and marks its browser as the owner's. `visits-report.yml` (Mon 07:00 UTC, `scripts/visits_report.py`) prunes >90 days and posts the week to the PRIVATE `TELEGRAM_CHAT_ID`. D1 cost: 2 rows written per view (row + ts index).
- Travel atlas journal: `scripts/gen_travel_atlas.py` + `templates/travel_atlas.html.tmpl` + `config/travel_atlas.json` (standalone page via `build.py --travel-atlas-out`, `{{ATLAS_DATA}}` placeholder; see Quick Commands)
- Syndication feeds: `scripts/gen_feeds.py` (RSS 2.0 `feed.xml` + JSON Feed 1.1 `feed.json`, newest ≤30 `recent` items; index advertises both via autodiscovery `<link>`s). Both gitignored.
- Check-in delete: `scripts/delete_checkin.py`
- D1 SQL dump: `scripts/gen_d1_dump.py` (bulk resync path)
- Tips fetch: `scripts/fetch_tips.py`
- Check-ins fetch: `scripts/fetch_checkins.py`
- Flights fetch: `scripts/fetch_flights.py` (FR24 login → diary CSV; weekly `fr24-flights.yml`)
- Telegram "on this day": `scripts/telegram_on_this_day.py` (daily story post; `telegram-on-this-day.yml`)
- Telegram check-in echo: `scripts/telegram_checkin_echo.py` (instant + morning digest; `telegram-checkin-echo.yml`)
- Last.fm fetch: `scripts/fetch_lastfm.py` (per-year scrobble aggregate; weekly `lastfm.yml`; feeds year-page "The year in sound" section via `gen_year_pages` `music_by_year`)
- Pre-2012 backfill: `scripts/build_backfill.py` (`backfill.yaml` → `backfill.csv`, 23-col schema, `source_app="refurbished"`, `rf<seq>` ids, centroid-reuse geocode); `build.py` auto-merges the sibling `backfill.csv`; `sync_to_d1.py --backfill` ingests to D1; source adapters `scripts/import_polarsteps.py` (Polarsteps export ZIP → backfill.yaml; PENDING user export) and `scripts/import_liveinternet.py` (LiveInternet diary crawl → candidate entries). `scripts/refresh_backfill_venue.py` (+ `refresh-backfill-venue.yml`) fills real Foursquare venue metadata onto a reconstructed row. **Both its endpoints can be shut at once**: `/v2/venues/{id}` is premium and 402s permanently for this token, and `/v2/venues/search` — the fallback — has been seen answering `402 credits_exhausted` too once the monthly call budget is spent (same budget the ratings fetch draws on). The run still exits 0 and commits nothing; the row keeps `[coords_approx:pending_api]` and must be retried after the quota resets on the 1st. Reconstructed rows badged in feed/trips/year-hero. Tests: `tests/test_backfill.py`.
- D1 sync: `scripts/sync_to_d1.py`, `scripts/d1_client.py`
- HTML deploy gate: `scripts/validate_html.py` — runs in `update-dashboard.yml` before every deploy (required pages present, no leftover `{{PLACEHOLDER}}`, embedded JSON parses, min page size; skips `solution.html` which quotes placeholders as documentation).
- QA docs: `qa/` — `test-strategy.md` (risk analysis → pyramid → gates), `exploratory-checklist.md` (manual pre-release charter), `bug-reports/` (14 written-up real defects). `docs/` is gitignored — QA docs must live in `qa/`.
- Lint config: `ruff.toml` (E4/E7/E9+F; E401/E701/E702/E731 ignored deliberately).
  that is drift from a newer locally-installed `requests` shipping its own inline types, not a code change.
- Other QA workflows: `lighthouse.yml` (weekly Mon 07:00 UTC, 4 pages, score floors perf≥60 / a11y+bp+seo≥85), `k6-load.yml` (manual, /api/search, fail >1% errors or p95>1s), `mutation.yml` (manual, mutmut over transform.py, config in setup.cfg `[mutmut]`), `doc-counts.yml` (monthly 1st 07:30 UTC + manual: `scripts/sync_doc_counts.py` re-measures the test/script-file counts via pytest collection and rewrites every quoted count in README.md, CLAUDE.md and solution.html, committing only on change; `--check` reports drift without writing; a reworded count phrase exits 2 and is also caught on push by `tests/test_sync_doc_counts.py`).
- Failure alerting: `update-dashboard.yml` sends a Telegram message after 2 consecutive scheduled failures (secrets `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`; step no-ops if unset).
- Search API (Cloudflare Pages Function): `functions/api/search.js`
- **`/api/search` and `/api/feed` must never throw.** An unhandled rejection in a Pages
  Function surfaces as a bare HTTP 500 `error code: 1101` whose body is not JSON, so the
  caller's `await res.json()` throws too and the page dies with a console error instead of
  an empty state. Both endpoints now contain the failure:
  - `search.js` wraps each of its five queries individually (`safe()`), counting failures.
    All failed => 503 `{error:'D1 unavailable', degraded:true}`; some failed => 200 with
    whatever did answer plus `degraded:true`. **Neither is edge-cached** — a partial or
    empty answer pinned for `CACHE_TTL` would outlive its cause. Only a complete 200 is
    stored. This is what keeps a missing `companions` table costing just the companion
    section instead of the whole endpoint.
  - `feed.js` wraps the whole handler; the 503 body keeps the success shape
    (`items: []`, `has_more:false`, `next_cursor:null`) so callers render an empty feed.
    Its `jsonResp()` now serves `no-store` on any non-200 — the 1 h `s-maxage` must never
    land on an error.
  Both were exercised for real: on 2026-09-03 the D1 free-tier daily row-read limit was
  reached and every query on the database began failing at once — `/api/search` and
  `/api/feed` returned 500, and only `/api/health` (which already had a `try/catch`) could
  report the actual cause.
- Other Pages Functions: `functions/api/{feed,search-venues,venue-tips,custom-list,health}.js` (feed.js also has `collectCompanions()` mirroring metrics.collect_companions; health.js = uptime probe: latest check-in age (`MAX(date)` only — an index lookup; the old `COUNT(*)` scanned all 70k rows on EVERY probe, and the count now comes from the `feed_meta.json` it fetches anyway) + feed_meta total, 200 ok / 503 degraded, with a 60 s edge cache via the explicit Cache API — `Cache-Control` alone does NOT edge-cache a Pages Function — on 200s only, so an outage is never cached; any query string, e.g. `?fresh=1`, is a distinct key and bypasses it)
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
  - `config/companion_countries.json` — companion name → home country for the flag on companions.html (`gen_companions.companion_country`). Not listed ⇒ the country MOST shared check-ins are in — never the latest one, which flipped Darya/Nikita/the Bryksenkovs to Moldova after the 29.07.2026 move. The build logs every companion with ≥20 shared check-ins that has no entry. Tests: `tests/test_companion_countries.py`.
  - `config/companion_fixes.json` — per-`checkin_id` companion override (`with_name`/`with_id`/`overlaps_name`/`overlaps_id`), for a Swarm `with` tag added after our snapshot; applied in `transform.apply_transforms()` and reconciled into D1 on every sync. See "Late companion tags" below.
- Config (other): `config/city_merge.yaml`, `config/city_fixes.json`, `config/venue_fixes.json`, `config/city_canonical.yaml`, `config/country_fixes.json`, `config/categories.json`, `config/settings.yaml`

- Tests and type-check config: see CLAUDE.md "Key Files" (the quoted counts are kept there by `sync_doc_counts.py`).

## Stable Implementation Notes

- All generators read plain `templates/*.tmpl` files. (The old base64-embedded `_TMPL_B64` templates were fully removed — no generator embeds one anymore.)
- `fetch_tips.py --sweep` is required to recover tips missing from `/users/self/tips` (often closed/deleted venue cases).
- Tips UI normalization path: country via `CTRY_NORM` (loaded from `config/country_aliases.json`), city via `city_merge.yaml`.
- `window._catIcon = catIcon` is used so index tips can reuse category icon logic from check-ins block.
- Build placeholders are simple string substitution (`{{PLACEHOLDER}}`), not Jinja.
- **Post-process placeholder pass** in `build.py`: after every generator runs, every output HTML file gets `{{CTRY_CODE_JSON}}` and `{{CAT_ICON_JSON}}` substituted from `config/country_flags.json` and `config/category_icons.json`. Generators don't thread these through their kwargs — they just leave the placeholder literally in the template.
- **Shared site.css** is extracted to `assets/site.css` (the side-nav rail, once byte-identical across 12 templates). Post-process copies it to the output root under a content-hashed name `site-<sha1_8>.css` and substitutes `{{SITE_CSS_LINK}}` in every page that carries the placeholder.
- **Index page weight (map lazy-load)**: the four heavy map layers (`unique_places`, `venues_heatmap`, `explorer_groups`, `country_centroids`) are split out of the inline `const S={…}` into a separate `map_data.json` (written by `build()`), fetched lazily by index.html on first scroll near the `#s-map` card (IntersectionObserver, 400px rootMargin) — cut index.html from ~5.6 MB to ~1.1 MB. Those keys are used ONLY by index.html; `S.unique_places_count`/`by_year`/`explorer_cats` scalars stay inline.
- **trips.html / companions.html weight**: per-trip check-ins are NOT inlined in `TRIPS` — `build._write_trip_checkin_shards` writes them to `trip_checkins_<id//10>.json` (positional rows under an `f` field header, no `datetime`), and `loadTripCheckins()` in trips.html fetches the shard when a trip modal opens (cache-busted via `?v={{TRIP_CK_VER}}`, a sha1 of the shard bodies). trips.html's `{{STATS}}` is cut to `_TRIPS_STATS_KEYS`, and companions.html's SDATA to `gen_companions.SOCIAL_KEYS`. Result: trips.html went from ~21 MB to ~190 KB and companions.html from ~7.6 MB to ~2.4 MB. A key a template starts reading must be added to its whitelist, otherwise it arrives `undefined`.
- **Route-geometry warm is split across two workflows** (`build.py --routes-max-fetch N`): road-following trip polylines are fetched from OSRM/BRouter and cached in `private-data/routes_cache.json` (persisted back to the data repo). Fetching is the slow part of the build, so it's tiered:
  - `update-dashboard.yml` (hourly + push-verify + manual) builds with `--routes-max-fetch 0` — it reads the cached polylines but makes ZERO OSRM/BRouter calls, keeping the latency-sensitive poller entirely off the network-bound path. Tradeoff: a brand-new check-in's road polyline appears a day late (on the next `warm-routes.yml` cache) instead of the same build. (This was previously a small `--routes-max-fetch 40` floor so a fresh route landed the same build; dropped to 0 once `warm-routes.yml` was draining the whole backlog daily anyway.)
  - `warm-routes.yml` (separate job: daily 03:00 UTC + `workflow_dispatch` with a `max_fetch` input, default 400) drains the historical backlog — hundreds of rate-limited fetches. It only warms + commits `routes_cache.json` to the data repo (no build/validate/deploy); the drained polylines ship on the next scheduled `update-dashboard` run, which reads the fresh cache. Scheduled at 03:00 (ahead of the hourly reads) and honors the `UPDATES_PAUSED` pause switch. Without this cap a cold cache would fetch tens of thousands of segments in one run and time out.
- **Historic enrichment (photos + late friend-overlaps) is split off the hourly path into `recheck-enrich.yml`** (daily 04:15 UTC + `workflow_dispatch`, honors `UPDATES_PAUSED`). Foursquare adds photos and friend-overlap tags to a check-in *after* it was first posted (a friend who was there tags it hours/days later); catching those needs a re-scan of recent check-ins, which was the slow part of the hourly job. The hourly `update-dashboard.yml` now fetches **new** items only (its `fetch_checkins.py` call dropped `--recheck-recent-hours 48` and its `fetch_photos.py` call dropped `--recheck-days 1`) — a brand-new check-in is still fully enriched on arrival; only the historic re-scan moved out. `recheck-enrich.yml` re-scans the last 48h (`fetch_checkins.py --recheck-recent-hours 48`) + last 2 days of photos (`fetch_photos.py --recheck-days 2`), uploads new pix to R2, commits the data repo (BUG-014 5-attempt rebase-and-retry loop — it is the **4th** data-repo writer), then rebuilds + validates + deploys + syncs D1 exactly like the hourly job so the enrichment reaches BOTH the static pages and the D1-backed feed. No tips fetch, no Telegram alert.
  - **The overlaps must reach D1, not just the static rebuild.** `sync_to_d1.py` check-ins are append-only (`INSERT OR IGNORE`; the selection filter also excludes already-synced IDs), so a friend-overlap the recheck adds to an *already-synced* check-in never propagates to `/api/feed` (which reads D1) — the static pages (index/trips/companions, rebuilt from CSV) would show the companion but the feed would not. The new **`--fix-overlaps-hours N`** sync flag closes that gap: it windows to check-ins from the last N hours, chunk-queries D1 for their current `overlaps_name`/`overlaps_id`, and emits targeted `UPDATE checkins SET overlaps_* WHERE id=` for any that drifted — mirroring the proven `--fix-city-country` machinery (byte-chunked `d1._raw_with_retry`). Brand-new windowed rows aren't in D1 yet → skipped by the fix pass, carried by the normal insert. It never touches the `changed`/FTS flag (overlaps aren't indexed). `recheck-enrich.yml` passes `--fix-overlaps-hours 48`.
- **PWA** (manifest + service worker): sources are `templates/manifest.webmanifest.tmpl` + `templates/sw.js.tmpl`; post-process renders both into the output root, stamping `{{SW_VERSION}}` in sw.js with the build UTC timestamp (`%Y%m%d%H%M%S`) so the cache name `foursq-<version>` busts on every deploy, and injects `<link rel="manifest">` + `<meta name="theme-color" content="#0b0d13">` + the SW registration before `</head>` of every generated page (idempotent — skips a page already carrying `rel="manifest"`). SW strategy: cache-first for same-origin static assets (css/js/svg/png/woff), network-first for navigations/HTML with cache→shell fallback, and pass-through (never cache) for `/api/*` and the big data blobs (`feed_meta`/`map_data`/`routes`/`trips_meta`/`trip_checkins_<n>`.json). Generated root artifacts (`manifest.webmanifest`, `sw.js`, `site-*.css`, `map_data.json`, `trip_checkins_*.json`) are gitignored and copied into `_site` by the CI cp globs (which now include `./*.js ./*.webmanifest ./site-*.css`).
- Companion display reads from THREE columns (`with_name`, `created_by_name`, `overlaps_name`) — combined via `metrics.collect_companions()` on the Python side and the JS mirror `collectCompanions()` in `functions/api/feed.js`. The case-insensitive dedup uses first-seen casing; the Foursquare `-` sentinel for overlaps is excluded.
- Search is served by `functions/api/search.js` (Cloudflare Pages Function at `/api/search?q=`). It queries D1 directly — no static `search-index.json` is generated or committed.
- **Search backend is SQLite FTS5** (not `LIKE '%q%'`). `d1_schema.sql` defines three external-content FTS5 virtual tables — `venues_fts` / `tips_fts` / `trips_fts` (store only the inverted index; display columns read from the base table via `rowid`). `sync_to_d1.py` keeps them fresh with `INSERT INTO <t>_fts(<t>_fts) VALUES('rebuild')` after each sync that wrote **that index's own base table** (a per-table `fts_dirty` map — NOT the coarse `changed` flag, which is true for any write at all: since `checkins` has no FTS table, an ordinary “3 new check-ins” run used to rebuild all three indexes for nothing) (or when the index is empty) — a rebuild, NOT triggers, because `apply_schema()` splits the schema on `;` and would shred a `BEGIN..END` trigger body. **The rebuild is verified, not trusted**: after the `('rebuild')` call, sync re-counts the FTS index vs its base table (`_count()` helper); if the index is still empty while the base has rows (a silent `rebuild` no-op, or an index stranded by the wrangler bulk-resync path which never touches FTS), it hard-repopulates via `('delete-all')` + `INSERT INTO <t>_fts(rowid, <cols>) SELECT rowid, <cols> FROM <base>` (a plain write that always lands on D1). The empty-index check runs every sync as `SELECT rowid FROM <t>_fts LIMIT 1` (~1 row read; the old `_count()` scanned the whole index — ~20k rows/sync, ~200k/day, to answer a yes/no), so a stranded index self-heals on the next run without any manual step — this is the fix for "search returns nothing" when prod FTS indexes are empty but base tables have data. `search.js` builds a MATCH expression (each query word `"word"*` prefix-matched, AND-ed; city search scopes tokens to `{city country}`), ranks venues/tips by `bm25()`, and returns the same `{venue,city,trip,tip,companion}` short-key shape. Companions have NO FTS table. They are precomputed by `sync_to_d1.py:build_companion_rows` into the small `companions(name, name_lc, cnt)` table (hash-gated via `sync_state`) and matched with a `LIKE` over `name_lc` — pre-lowercased because SQLite's LIKE only folds case for ASCII, so a Cyrillic name would otherwise never match. This replaced three `LIKE '%q%'` scans of the 70k-row checkins table (~210k rows read per keystroke) that were the single largest consumer of the daily read budget.
- `sync_to_d1.py` is incremental: checkins append-only, venues only for touched IDs, tips/ratings/lists gated by `--tips-changed` / `--ratings-changed` / `--lists-changed` flags (CI passes fetch step outputs). Trips are the exception: both `update-dashboard.yml` and `recheck-enrich.yml` pass `--trips-changed true` on every sync, because trip names/boundaries also move on config-only changes the fetch flag never sees; the trips sync is hash-gated (`sync_state`) and, after the upsert, drops the stale id tail (`DELETE FROM trips WHERE id > N`). **Row counts are cached per UTC day for ALL tables** (`daily_counts` in `sync_state`): a non-census sync re-counts only the tables it wrote (`touched`), so the before/after shrink check no longer costs ~62k rows read per sync (list_venues ~18.6k + venue_changes ~6k + ratings ~3.8k + … counted twice). `d1_client.USAGE` sums `meta.rows_read/rows_written` and the sync prints `D1 usage this run: …` at the end — use it to measure any read-budget change.
- **Check-in inserts are capped by COPY COUNT, never by id-existence** (`dedupe_against_d1`). `checkins` has no UNIQUE on `id` — `seq` is the PK so the CSV's own duplicate rows survive — which means `INSERT OR IGNORE` cannot reject a repeat. Both candidate paths read a snapshot of D1 (a `max_date` watermark, or a per-id count), so two syncs whose windows overlap (`update-dashboard` hourly vs `recheck-enrich` at 04:15, which also syncs D1) each saw the same rows as new and each inserted them — four July 2026 rows ended up stored twice. The guard caps each id at `source_count - d1_count`. Counting is load-bearing: `WHERE NOT EXISTS` / `id not in existing_ids` semantics would collapse the **12 check-ins that checkins.csv legitimately lists twice** (Суперпрод, Гуманитарный факультет БГУ, …) to one row each. It runs on the incremental path only (≤1000 candidate rows, D1 non-empty); the set-difference fallback feeds the whole source through the same helper.
- **Ratings fetch is throttled inside the hourly job.** `/users/self/venuelikes` (the only rating endpoint that returns data — okays/dislikes 402 permanently) is metered by a MONTHLY premium-call quota (~220 calls, empirical) that resets on the 1st and 402s once spent; each fetch is ~7-8 calls (full re-paginate). The "Fetch venue ratings" step in `update-dashboard.yml` therefore only fires at 04:00 UTC when `$((10#$(date -u +%j) % 3)) == 0` (≈ every 3 days, ~80 calls/mo ≈ 36% of budget). `workflow_dispatch` bypasses the gate; the throttle CANNOT backfill an already-exhausted month (wait for the 1st, or use the data-export + `--force-ratings` path). See memory `project_ratings_likes_quota.md`.
- Companion search covers all three source fields: `with_name`, `created_by_name` (UNION query), and `overlaps_name` (comma-separated, split in JS).
- `/api/feed` cache header: `public, max-age=60, s-maxage=3600, stale-while-revalidate=600`. Schema-shape changes propagate immediately via the `_v=<tag>` query param on every fetch URL — bump the tag (currently `_v=refurb`) whenever the response tuple shape changes, otherwise edge nodes serve old tuple lengths to the destructure for up to an hour.
- Feed (`feed.html` / `functions/api/feed.js`) uses a **contiguous-array virtual scroll**:
  - Single `ALL` array; init fetches the 100 newest items only (`revDone=true` from the start).
  - `loadFwd()` appends older items (`?cursor=TS`, 50 at a time); `loadRev()` prepends newer items (`?after=TS`) with scroll-position correction to prevent viewport jumps.
  - `buildPos()` estimates `totalH` as `loadedH + remaining*AVG_ITEM_H`, with `Math.max` guard to prevent shrinking during incremental loads.
  - Navigation (`goYMD`, `goLatest`, `goOldest`) resets state: `_loadGen++`, clears `ALL`, `totalH=0`, then `buildPos()`. The generation counter discards in-flight stale fetches.
  - `renderCal` uses authoritative `YM_IDX[ym]` counts from `feed_meta.json` (not an accumulated local counter) to prevent double-counting after state resets.
  - `feed_meta.json` (static, generated at build) provides calendar month counts and total — no D1 query needed.
  - **No background preload loop** — data loads only when the user scrolls near the edge.


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
- **Shared data-repo pushes are concurrency-hardened.** Nine workflows commit to
  `toouur/foursquare-data` `main` — `update-dashboard.yml` (hourly), `warm-routes.yml`
  (daily 03:00 UTC), `recheck-enrich.yml` (daily 04:15 UTC), `fr24-flights.yml` (weekly
  Sun 05:00), `lastfm.yml` (weekly Sun 06:00), `telegram-on-this-day.yml` (daily 06:00,
  `otd_stories.json`), `telegram-checkin-echo.yml` (dispatched by each hourly poll with new check-ins + daily 05:45,
  `checkin_echo_state.json`), `vivino.yml` (weekly Sun 07:15, `vivino_ratings.json`), `imdb.yml` (weekly Sun 07:30, `imdb_ratings.json`). Overlapping runs (e.g. the 03:00
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

## Local D1 dev (Wrangler)

```bash
npx wrangler pages dev . --d1 DB=52210bd9-a019-415e-8f12-6a73b42278f9
# Tests /api/search against the remote D1 database locally
```

## Known Gotchas

- System Python may miss dependencies; prefer Python 3.12 path above.
- CSS `:visited` does not reliably support CSS variables; use literal color values when needed.
- Chart/config brace balance errors break pages silently; validate after edits.
- Companion name overrides require exact `with_name` string matches.
- Building locally without `--photos` and `--pix-url` will emit `const photos=[]` in `index.html`, wiping the recent photos feed. Always pass both args when rebuilding. (Generated HTML is no longer committed, so a bad local build can't leak into git — but it would still deploy if you ran `wrangler pages deploy` from it.)
- **CARTO basemaps need an API key.** Since Sep 2026 `basemaps.cartocdn.com` answers keyless requests with an "API KEY REQUIRED" watermark tile (2.5 KB, ETag `wm-…`), whatever the Referer. Every Leaflet tile URL ends in `?key={{CARTO_KEY}}` (`{{{{CARTO_KEY}}}}` in `gen_flights.py`, which renders through `str.format`); `build.py` fills it in every `*.html` from `settings.yaml` `basemap.carto_key`, with env `CARTO_API_KEY` taking precedence, and logs a warning when the key is empty. The key is public by design and **domain-restricted** at CARTO (free non-commercial key, 5M req/mo, https://carto.com/basemaps/apikey). It lists bare hostnames, and `*.x` does not cover the bare `x`. A local preview on `localhost` needs its own key via `CARTO_API_KEY`. The free terms require visible "© OpenStreetMap © CARTO" attribution, so no CARTO map may set `attributionControl:false`. Guarded by `tests/test_basemap_tiles.py`. (Commit 113cff61 briefly switched to Esri's keyless Dark Gray Canvas; reverted for looks.)
- The D1 binding (`DB`) must be configured in the Cloudflare Pages dashboard (Settings → Functions → D1 database bindings). Without it, `/api/search` returns 503.
