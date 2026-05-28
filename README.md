# Foursquare Check-in Dashboard

<div align="center">

[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Cloudflare Pages](https://img.shields.io/badge/deployed%20on-Cloudflare%20Pages-orange?style=flat-square&logo=cloudflare&logoColor=white)](https://pages.cloudflare.com/)
[![GitHub Actions](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF?style=flat-square&logo=github-actions&logoColor=white)](https://github.com/features/actions)

</div>

<div align="center">

### Support the project

<a href="https://wise.com/pay/me/andreip1207"><img src="https://img.shields.io/badge/Donate%20via-Wise-9fe870?style=for-the-badge&logo=wise&logoColor=black" alt="Donate via Wise" height="36"></a><br><a href="https://boosty.to/toouur/donate"><img src="https://img.shields.io/badge/Donate%20on-Boosty-f15f2c?style=for-the-badge&logo=boosty&logoColor=white" alt="Donate on Boosty" height="36"></a>

</div>

---

A self-updating personal dashboard for your Foursquare/Swarm check-in history.

**Features:** heatmap + dot map + country flag map · charts by year / month / hour / day of week ·
GitHub-style activity heatmap · travel timeline (Gantt) · trip journal with per-trip maps ·
trip analytics (duration distribution, countries per trip, longest trips leaderboard, furthest destination) ·
distance travelled per year · activity streaks · category mix drift · new countries timeline ·
venue loyalty · regular haunts · revisit intervals · venue visit frequency ·
**Hour × Category** and **Day-of-Week × Category** heatmaps in local time ·
**country hour profiles** (% per local hour, top 12 countries) ·
**shout text mining** — word frequency, language mix per year, top words per country, language detection (Cyrillic / Latin / mixed) ·
**Shouts page** — searchable archive of all real free-text comments (~3.5 k, infinite scroll, year / country filters) ·
**live full-text search** (venues, cities, tips, companions — powered by Cloudflare D1, no static index file) ·
**bidirectional infinite-scroll feed** (65 k+ check-ins, cursor-based D1 pagination, on-demand gap fill, virtual scroll) — each card shows companions from all three Foursquare sources (`with_name` / `created_by_name` / `overlaps_name`, deduped union) ·
category explorer · companions · recent check-ins with historical weather ·
tips page with country/city tabs, map, closed/deleted-venue detection, view counts, and filter buttons ·
**photo gallery** with 21 000+ check-in photos hosted on Cloudflare R2, country/city accordion filter,
lazy loading, lightbox, and inline tip photos ·
**live travel guide** (`guide.html`) — nearby suggestions based on your 48h session history.

---

## Project layout

```
.
├── scripts/
│   ├── fetch_checkins.py        # Fetch check-ins from Foursquare API → data/checkins.csv
│   ├── fetch_tips.py            # Fetch tips → data/tips.json (incremental in CI; sweep is manual/one-time)
│   ├── fetch_photos.py          # Fetch check-in photos from Foursquare data export → data/photos.json
│   ├── fetch_ratings.py         # Fetch venue ratings (likes/okays/dislikes) → venueRatings.json
│   ├── fetch_lists.py           # Fetch Foursquare lists → lists.json
│   ├── transform.py             # Data cleaning: country fixes, city normalisation
│   ├── fill_city_inferred.py    # Blank-city resolver: centroid Haversine match → city_inferred column
│   ├── analyze_blanks.py        # Lists remaining blank-city rows with nearest known centroid
│   ├── extract_blank_fixes.py   # Filters analyze_blanks output via city_canonical.yaml whitelist
│   ├── apply_blank_fixes.py     # Appends extracted entries to city_fixes.json (preserves format)
│   ├── gen_city_review.py       # Emits city_review.csv: spot-check window of recent inferred cities
│   ├── check_city_config.py     # CI gate: validates city_canonical.yaml + city_fixes.json + city_merge.yaml
│   ├── metrics.py               # All aggregation + trip-detection logic
│   ├── build.py                 # CLI entry point: checkins.csv → all HTML pages
│   ├── gen_companions.py        # Generates companions.html
│   ├── gen_feed.py              # Generates feed.html (bidirectional infinite-scroll, cursor-based D1 API)
│   ├── gen_guide.py             # Generates guide.html (live nearby suggestions, 48h session history)
│   ├── gen_lists.py             # Generates lists.html
│   ├── gen_photos.py            # Generates photos.html (full gallery, city filter, tip photos)
│   ├── gen_ratings.py           # Generates ratings.html
│   ├── gen_search.py            # Generates search.html (no longer writes search-index.json)
│   ├── gen_shouts.py            # Generates shouts.html (searchable archive of ~3.5k real free-text comments)
│   ├── gen_stats.py             # Generates stats.html (+ shout text-mining + Hour×Category & DOW×Category heatmaps)
│   ├── gen_tips.py              # Generates tips.html; also loads + exports CTRY_NORM from config/country_aliases.json
│   ├── gen_trip_pages.py        # Generates per-trip HTML pages (trip-N.html)
│   ├── gen_venues.py            # Generates venues.html (top 500 venues)
│   ├── gen_worldcities.py       # Generates world_cities.html
│   ├── sync_to_d1.py            # Incremental CI sync of all data to Cloudflare D1
│   ├── d1_client.py             # Low-level D1 HTTP client (batch upsert, schema apply)
│   ├── gen_d1_dump.py           # Generates SQL dump for bulk D1 resync via wrangler
│   ├── import_to_d1.py          # One-time bulk import to D1 (uses /raw endpoint)
│   ├── sync_venue_changes.py    # Diffs archived vs fresh checkins; patches tips.json metadata
│   ├── delete_checkin.py        # Removes check-in(s) by ID from CSV + D1 (and orphaned venues)
│   ├── refresh_venue.py         # Re-fetches a single venue's metadata from Foursquare
│   ├── add_venue_tip.py         # Adds a tip to a venue via Foursquare API
│   ├── rate_venue.py            # Sets like/okay/dislike on a venue via Foursquare API
│   ├── enrich_overlaps.py       # Backfills overlaps_name/overlaps_id on older check-ins
│   ├── fix_overlap_dupes.py     # Cleans duplicate entries in overlaps_* fields
│   └── find_closed_venue_tips.py  # One-time utility: find tips on closed venues via browser cookies
├── .github/workflows/
│   ├── update-dashboard.yml       # Hourly incremental: fetch + build + push + D1 sync
│   ├── archive-checkins.yml       # Manual: full re-fetch + venue-change sync (see below)
│   ├── delete-checkin.yml         # Manual: delete check-in by ID from CSV + D1 + rebuild
│   ├── resync-checkins-d1.yml     # Manual: wipe + reinsert checkins/venues via wrangler SQL dump
│   ├── resync-d1.yml              # Manual: force resync of tips/ratings/lists/trips tables
│   ├── resync-all.yml             # Manual: composite — runs checkins dump + every table force resync
│   ├── backup-d1.yml              # Manual: snapshot D1 to a downloadable SQL backup
│   ├── fix-city-country-d1.yml    # Manual: re-apply normalization (city_merge/country_fixes/city_inferred) to D1
│   ├── test-d1-schema.yml         # Manual: verifies D1 schema columns + indexes match expectations
│   ├── upsert-d1.yml              # Manual: incremental upsert (same as nightly, on demand)
│   ├── add-venue-tip.yml          # Manual: post a tip to a venue
│   ├── add-venue-rating.yml       # Manual: set like/okay/dislike on a venue
│   ├── add-checkin-photos.yml     # Manual: ingest new photos from a data export
│   ├── refresh-venue.yml          # Manual: re-fetch one venue's metadata
│   ├── fetch-venue-rating.yml     # Manual: resync venueRatings.json
│   ├── fetch-lists.yml            # Manual: resync lists.json
│   ├── fix-overlaps.yml           # Manual: run enrich_overlaps.py / fix_overlap_dupes.py
│   └── release.yml                # Release tagging
├── functions/
│   └── api/
│       ├── search.js            # /api/search?q= — D1-backed multi-facet search
│       ├── search-venues.js     # /api/search-venues — venue autocomplete
│       ├── feed.js              # /api/feed — cursor-paginated check-in feed
│       ├── venue-tips.js        # /api/venue-tips — tips for a given venue_id
│       └── custom-list.js       # /api/custom-list — custom curated lists
├── data/
│   ├── checkins.csv          # Your check-in data — gitignored, lives in private repo
│   ├── tips.json             # Your tips data — gitignored, lives in private repo
│   ├── photos.json           # Photo index {checkin_id: [filenames]} — gitignored, lives in private repo
│   ├── venueRatings.json     # Venue ratings — gitignored, lives in private repo
│   └── lists.json            # Foursquare lists — gitignored, lives in private repo
├── workers/
│   └── checkin-poller/       # Cloudflare Worker: polls Foursquare every minute,
│       ├── worker.js         #   triggers GitHub Actions on new check-in
│       └── wrangler.toml
├── config/
│   ├── settings.yaml              # home_city, trip_detection thresholds
│   │
│   │  # ── Canonical lookup tables (single source of truth — see "Canonical normalization layer" below) ──
│   ├── country_aliases.json       # Raw native country name → English canonical (Беларусь→Belarus, Тоҷикистон→Tajikistan)
│   ├── country_flags.json         # English country → ISO 3166-1 alpha-2 (used for flag-icons CSS)
│   ├── category_icons.json        # Foursquare category → [emoji, hex color] (559 entries)
│   │
│   │  # ── City / country normalization ──
│   ├── city_merge.yaml            # Raw Foursquare city names → canonical names
│   ├── city_canonical.yaml        # Blank-city resolver vocabulary: canonical map, thresholds, skip rules
│   ├── city_fixes.json            # Per-timestamp city overrides (accepts unix-ts OR 24-char hex id)
│   ├── country_fixes.json         # Per-timestamp country overrides
│   │
│   │  # ── Display / metrics ──
│   ├── categories.json            # Category groupings for charts + explorer
│   │
│   │  # ── Trips ──
│   ├── trip_names.json            # Trip name overrides (keyed by _name_ts)
│   ├── trip_tags.json             # Trip tags, e.g. ["bicycle"] (keyed by _name_ts)
│   ├── trip_exclude.json          # Trip start timestamps to exclude entirely
│   ├── trip_start_overrides.json  # Force trip start at an earlier timestamp
│   └── trip_end_overrides.json    # Force trip end at a specific timestamp
├── templates/
│   ├── index.html.tmpl       # Template for index.html
│   ├── trips.html.tmpl       # Template for trips.html
│   ├── tips.html.tmpl        # Template for tips.html
│   ├── search.html.tmpl      # Template for search.html (queries /api/search)
│   └── ...                   # One template per generated page
├── index.html                # Main dashboard (built by CI, committed)
├── trips.html                # Trip journal (built by CI, committed)
├── companions.html           # Companions page (built by CI)
├── feed.html                 # Check-in feed (built by CI)
├── tips.html                 # Tips page (built by CI)
├── venues.html               # Top venues (built by CI)
├── world_cities.html         # World cities explorer (built by CI)
├── ratings.html              # Venue ratings page (built by CI)
├── lists.html                # Foursquare lists page (built by CI)
├── search.html               # Search page — queries live D1 via /api/search (built by CI)
├── shouts.html               # Searchable archive of real free-text comments (~3.5k, infinite scroll, filters)
├── guide.html                # Live "what's around me" guide — 48h session history, nearby suggestions
├── trip-*.html               # Per-trip detail pages (~155, auto-generated)
├── requirements.txt          # Python deps (requests, pyyaml, timezonefinder)
├── netlify.toml              # Netlify config (builds disabled — CI-only deploys)
└── wrangler.toml             # Cloudflare Pages + D1 binding config
```

---

## Setup (~10 minutes)

### 1. Fork or clone this repo

The dashboard repo can be **public** — check-in data is stored separately in a private repo (see step 3).

### 2. Get your Foursquare OAuth token

1. Go to [foursquare.com/developers/apps](https://foursquare.com/developers/apps)
2. Create an app (or use an existing one)
3. Set **Redirect URI** to `https://localhost`
4. Open this URL in your browser (replace `YOUR_CLIENT_ID`):
   ```
   https://foursquare.com/oauth2/authenticate?client_id=YOUR_CLIENT_ID&response_type=token&redirect_uri=https://localhost
   ```
5. After approving, copy the `access_token` from the redirect URL

### 3. Create a private data repo

1. Create a **private** GitHub repo named `foursquare-data` (or any name)
2. Create a fine-grained PAT: GitHub → Settings → Developer settings → Fine-grained tokens
   - Repository access: only `foursquare-data`
   - Permissions → Contents: **Read and write**
3. Add secrets to this repo → **Settings** → **Secrets and variables** → **Actions**:
   - `FOURSQUARE_TOKEN` — your Foursquare OAuth token
   - `DATA_REPO_PAT` — the fine-grained PAT from step 2

### 4. Choose a deploy target

**Option A — GitHub Pages (simplest)**
1. Repo → **Settings** → **Pages**
2. Source: **Deploy from a branch** · Branch: `main` / `(root)`
3. Your site will be at `https://YOUR_USERNAME.github.io/REPO_NAME/`

**Option B — Cloudflare Pages** (recommended — includes live search via D1)
1. Connect the repo in the Cloudflare dashboard (Workers & Pages → Create → Pages → Connect to Git)
2. Build command: *(leave empty — HTML is pre-built by CI)*
3. Build output directory: `/` (repo root)
4. After first deploy: Settings → Functions → D1 database bindings → add `DB` → `swarmdata`
5. The `wrangler.toml` is already configured with the D1 binding for local dev

**Option C — Netlify**
1. Connect the repo in the Netlify dashboard
2. Deploys are triggered manually via the `netlify-monthly` GitHub Actions job
   (Netlify auto-builds on push are intentionally disabled in `netlify.toml`)

### 5. Run the first build

1. Push your `checkins.csv` to the private data repo
2. Go to the **Actions** tab → **Update check-in dashboard** → **Run workflow**
3. Wait ~2 minutes
4. Visit your live URL

### 6. (Optional) Near-instant updates via Cloudflare Worker

The `workers/checkin-poller/` directory contains a Cloudflare Worker that polls Foursquare every minute and triggers a GitHub Actions build within ~1 minute of a new check-in. Deploy it once with:

```bash
cd workers/checkin-poller
wrangler deploy --config wrangler.toml
wrangler secret put FOURSQUARE_TOKEN --config wrangler.toml
wrangler secret put GITHUB_TOKEN --config wrangler.toml   # PAT with workflow scope
```

---

## Running locally

```bash
pip install -r requirements.txt

# Fetch check-ins
export FOURSQUARE_TOKEN=your_token_here
python scripts/fetch_checkins.py

# Fetch tips (incremental — same as CI)
python scripts/fetch_tips.py --token "$FOURSQUARE_TOKEN" --out data/tips.json

# Fetch photos from Foursquare data export (only new check-ins since last index)
python scripts/fetch_photos.py \
  --token "$FOURSQUARE_TOKEN" \
  --export path/to/foursquare-export/photos/ \
  --csv data/checkins.csv \
  --photos data/photos.json \
  --pix-dir data/pix/

# Build dashboard (without photos — deployed site uses R2)
python scripts/build.py

# Build dashboard (with local photos)
python scripts/build.py --photos data/photos.json

# Preview in browser
python -m http.server 8000
```

**Common CLI options:**

```bash
# Force full re-fetch (ignore existing CSV)
python scripts/fetch_checkins.py --full

# Tips: force full re-fetch (all tips, no sweep)
python scripts/fetch_tips.py --full --out data/tips.json

# Tips: add a single known tip on a closed venue by its Foursquare tip ID
# Use this when you know a specific tip was missed (e.g. from a new export comparison)
python scripts/fetch_tips.py --add-tip-id 645265a53112b8775c114ecb

# Tips: one-time venue sweep — probes every venue in checkins.csv that has no tip yet
# Use this to bulk-discover tips on closed venues (not run in CI — manual exercise only)
# After the sweep, any sweep-discovered tip is automatically marked closed=True
python scripts/fetch_tips.py --full --sweep --csv data/checkins.csv

# Tips: bulk reconciliation via Foursquare data export
# Use when you suspect the API is missing tips compared to a fresh export
# 1. Download your export from foursquare.com/settings/data-export
# 2. Extract the archive — locate tips.json inside
# 3. Cross-check against current tips.json and import missing tips:
python scripts/find_closed_venue_tips.py \
  --token "$FOURSQUARE_TOKEN" --cookies cookies.txt \
  --csv data/checkins.csv --tips data/tips.json
# Requires browser session cookies to verify closed/deleted status via venue pages

# Custom paths / home city
python scripts/build.py --input data/checkins.csv --home-city "Minsk" --config-dir config

# Build with photos hosted on Cloudflare R2 (deployed site)
python scripts/build.py \
  --photos data/photos.json \
  --pix-url "https://pub-xxxx.r2.dev/pix"

# Dump a full list of raw Foursquare categories seen in your data
python scripts/build.py --cat-list
```

---

## Configuration

### `config/settings.yaml`

```yaml
trip_detection:
  home_city: Minsk       # Check-ins here are excluded from trips
  min_checkins: 5        # Minimum check-ins for a sequence to count as a trip
```

### `config/city_merge.yaml`

Maps raw Foursquare city strings to canonical names — handles Cyrillic,
alternate spellings, district names, transliteration variants, etc.

```yaml
"Минск": "Minsk"
"Minski Rayon": "Minsk"
"Москва": "Moscow"
```

### `config/country_fixes.json`

Per-timestamp country overrides for check-ins that Foursquare tagged to the
wrong country:

```json
{
  "1234567890": "Belarus"
}
```

### `config/categories.json`

Groups raw Foursquare category strings into display buckets for the
category chart and the Category Explorer widget.

### `config/city_canonical.yaml`

Single source of truth for the **blank-city resolver** (`extract_blank_fixes.py`).
Defines which raw nearest-city names the extractor accepts, what canonical name
they map to, and the distance thresholds (km) for accepting a centroid match.

```yaml
default_thresholds: { large_km: 8.0, small_km: 5.5 }
canonical_map:
  "Мiнск": "Minsk"
  "Витебск": "Vitebsk"
large_canonical: ["Minsk", "Brest", "Vitebsk"]   # use large_km bucket
valid_canonical: ["Minsk", "Brest", ...]         # whitelist accepted as-is
skip_set: ["Минский район", "Vitebsk Region"]    # never accept
skip_patterns: ['район$', 'Rayon$', '\sRegion$'] # regex blocklist
```

Unicode-fold fallback (NFKD + casefold + strip combining marks) catches
diacritic/transliteration variants without requiring explicit mappings.

---

## Canonical normalization layer

Three pure-data JSON files act as the single source of truth for cross-cutting lookups
that used to live inline in nine HTML templates and one Python generator. `build.py`
loads each file once, then a single post-process pass substitutes the corresponding
`{{...}}` placeholder in every generated HTML file. Adding a new country / category /
flag is a one-line edit to the JSON — no code changes anywhere.

| File | Maps | Replaces previously inlined |
|------|------|-----------------------------|
| `config/country_aliases.json` | Native name → English canonical (`"Беларусь":"Belarus"`, `"Тоҷикистон":"Tajikistan"`) | `CTRY_NORM` dict in `gen_tips.py` |
| `config/country_flags.json` | English country → ISO 3166-1 alpha-2 (`"Macao":"mo"`) | `CTRY_CODE` / `ISO2` dicts in 9 templates + `gen_photos.py` (was previously 112 entries, drifted between files) |
| `config/category_icons.json` | Foursquare category → `[emoji, hex color]` (559 entries) | `CAT_ICON` dicts in 6 templates (the shouts page only had 40 of them before extraction — that's why most of its cards showed the default pin) |

The post-process step also resolves a class of bug where two pages would show
different icons / flags for the same data because the inline dict had drifted in one
but not the other. After extraction, the only place to edit is the JSON.

---

## City normalization pipeline

Four stages run in priority order — first match wins per check-in row:

| Stage | File | Granularity | When |
|-------|------|-------------|------|
| 1. Country override | `config/country_fixes.json` | per-timestamp | Foursquare tagged wrong country |
| 2. City override | `config/city_fixes.json` | per-timestamp (or per-venue 24-char hex id) | known-bad city on a specific check-in |
| 3. String merge | `config/city_merge.yaml` | per-raw-string | already-non-blank city needs renaming |
| 4. Blank inference | `scripts/fill_city_inferred.py` | per-row centroid match | city was blank; nearest known cluster within threshold wins |

Rows filled by stage 4 get `city_inferred=1` in D1 so the provenance stays visible.
`scripts/gen_city_review.py` writes `city_review.csv` for spot-checking recent
inferred values; corrections you add to `config/city_fixes.json` or
`config/city_merge.yaml` will take precedence on the next build.

### Bulk blank-city recovery workflow

```bash
# 1. List remaining blank-city rows with the nearest known centroid:
python scripts/analyze_blanks.py > C:/tmp/blanks_output.txt
# 2. Filter against the canonical whitelist + per-city thresholds:
python scripts/extract_blank_fixes.py > C:/tmp/blank_fixes.txt
# 3. Review C:/tmp/blank_fixes.txt manually, then append to city_fixes.json:
python scripts/apply_blank_fixes.py
# 4. CI gate: make sure all configs stay consistent before committing:
python scripts/check_city_config.py
```

`check_city_config.py` runs in CI as a merge gate. It validates that every
`canonical_map` value is in `valid_canonical`, every `large_canonical` /
`thresholds` key is valid, that `city_fixes.json` keys are well-formed
(numeric ts or 24-char hex Foursquare object id), and that `city_merge.yaml`
has no empty canonicals.

---

## Full re-fetch and data integrity

### Fetch strategies

`fetch_checkins.py` uses two strategies depending on context:

| Strategy | When | How |
|----------|------|-----|
| **Incremental** | Default (CSV exists) | Fetches only check-ins newer than the latest timestamp in the CSV |
| **Full (offset)** | `--full` locally | Paginates via `?offset=N`; simple but silently capped at ~2,500 rows by Foursquare |
| **Full (timestamp)** | `--full` in CI | Walks backwards via `?beforeTimestamp=T`; no cap, handles full history |

The timestamp strategy is used in CI because it correctly handles histories longer than 2,500 check-ins. If the Foursquare API returns a quota/rate-limit error mid-fetch, partial results are saved to the CSV rather than discarded — the next run will continue from where the API stopped.

### Merge logic on full re-fetch

When a full re-fetch completes (or partially completes), results are merged with the existing CSV:

1. All existing rows are kept as the base (including any duplicate rows)
2. Fetched rows override existing rows where `(venue_id, date)` matches — this refreshes renamed/moved venue metadata
3. Rows returned by the API but absent from the existing CSV are appended as new check-ins
4. The merged result is sorted by timestamp and written back

### Anomaly tracking (`checkins_anomalies.json`)

Every full re-fetch writes a `checkins_anomalies.json` file next to `checkins.csv` in the private data repo. It records two categories of data quality issues and accumulates entries across runs:

**`duplicates`** — rows with the same `(venue_id, date)` key appearing more than once in the existing CSV. These are identical rows that were double-entered at some point. They are preserved in the CSV (not silently removed) and logged here for awareness. A `duplicate_checkins.csv` sidecar file is also written alongside `checkins.csv` for easy inspection.

**`missing`** — rows present in the existing CSV that the Foursquare API no longer returns. These are check-ins on venues that were deleted, merged into another venue, or otherwise removed from the API. They are preserved in the CSV and recorded here so you know which check-ins the API has "forgotten".

```json
{
  "_meta": {
    "description": "...",
    "updated": "2026-03-26",
    "duplicates_count": 12,
    "missing_count": 8
  },
  "duplicates": [ ...rows... ],
  "missing":    [ ...rows... ]
}
```

### Archive and venue-change sync (`archive-checkins` workflow)

The **Archive check-in snapshot** workflow (Actions tab → manual trigger) does a full re-fetch and automatically syncs venue metadata changes into `tips.json`:

1. Archives the current `checkins.csv` with a UTC timestamp (e.g. `archive/checkins_2026-03-26T12-00-00Z.csv`)
2. Does a full re-fetch of all check-ins from Foursquare
3. Diffs the archived CSV against the fresh one — detects renamed venues, moved locations, category changes
4. Patches any matching `tips.json` entries with updated venue metadata (no extra API calls — uses the already-fresh check-in data)
5. Commits the archive, updated `checkins.csv`, updated `tips.json`, and `checkins_anomalies.json` to the private data repo
6. **Syncs venue changes to D1** — applies targeted `UPDATE checkins SET field WHERE venue_id` for each changed venue and records an audit row in the `venue_changes` table

Venue diff is done by `scripts/sync_venue_changes.py`. It compares these fields per venue_id: `venue`, `city`, `country`, `lat`, `lng`, `category`.

After the archive workflow completes, run the D1 sync manually from the public repo:

```bash
# 1. Generate diffs JSON (--dry-run skips tips.json re-patch since it was already done by the workflow)
python scripts/sync_venue_changes.py \
  --old private-data/archive/checkins_PREV.csv \
  --new private-data/checkins.csv \
  --tips private-data/tips.json \
  --out  /tmp/venue_diffs.json \
  --dry-run

# 2. Apply to D1: targeted UPDATE checkins + insert audit rows into venue_changes
python scripts/sync_to_d1.py \
  --csv     private-data/checkins.csv \
  --tips    private-data/tips.json \
  --ratings private-data/venueRatings.json \
  --lists   private-data/lists.json \
  --trips   trips_meta.json \
  --venue-changes /tmp/venue_diffs.json
```

---

## Photos

`photos.json` is an index of `{checkin_id: [filenames]}` built from your Foursquare data export.
Photos are stored locally in `data/pix/` (gitignored) and served in the deployed site from **Cloudflare R2** (free tier: 10 GB storage, 10 M reads/month, zero egress cost).

### Fetching photos

1. Download your data export from `foursquare.com/settings/data-export`.
2. Extract the archive — it contains CSV files of your photos.
3. Run `fetch_photos.py` to index new photos and download the actual files:

```bash
python scripts/fetch_photos.py \
  --token "$FOURSQUARE_TOKEN" \
  --export path/to/export/photos/ \
  --csv data/checkins.csv \
  --photos data/photos.json \
  --pix-dir data/pix/
```

The script auto-detects the cutoff timestamp (the max timestamp of check-ins already in `photos.json`)
and only fetches photos for **newer** check-ins, making incremental runs fast.

### Tip photos

12 photos in the data export were discovered to belong to **tips** rather than check-ins.
These are stored as a `photo` field directly in `tips.json` entries (e.g. `"photo": "29447180_AbCdEf.jpg"`).
`gen_tips.py` computes `photo_url` at build time from `pix_url + "/" + tip["photo"]`.
Tip photos appear on tip cards in both `tips.html` and `index.html`.

### Cloudflare R2 setup

1. Create a bucket (e.g. `foursquare-photos`) in the Cloudflare R2 dashboard.
2. Enable the **Public Development URL** (`r2.dev` domain) on the bucket.
3. Upload your `pix/` folder: `aws s3 sync data/pix/ s3://foursquare-photos/pix/ --endpoint-url https://{account_id}.r2.cloudflarestorage.com`
4. Set `R2_PUBLIC_URL` to `https://pub-xxxx.r2.dev/pix` (include the `/pix` prefix).
5. Pass it to the build: `python scripts/build.py --photos data/photos.json --pix-url "$R2_PUBLIC_URL"`

In CI, the `update-dashboard` workflow uploads only **new** photos (those added since the last run)
using `aws s3 sync`, then rebuilds with `--pix-url` so the deployed site serves photos from R2.

Required GitHub secrets: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID`, `R2_BUCKET_NAME`, `R2_PUBLIC_URL`.

---

## Tips

### How tips are fetched in CI

The hourly CI run calls `fetch_tips.py` with no special flags — **incremental mode only**:

```bash
python scripts/fetch_tips.py --token "$FOURSQUARE_TOKEN" --out private-data/tips.json --csv private-data/checkins.csv
```

This hits `/users/self/tips` sorted by recency and stops as soon as it reaches a timestamp already in `tips.json`. It only adds tips on **active venues** — which is all that the normal flow needs. The `tips.json` dataset has been fully reconciled against the Foursquare data export (see below), so the only new tips arriving via CI are ones you genuinely just wrote.

### Adding a tip on a closed venue

If you write a tip on a venue and later discover it wasn't picked up (because the venue closed), add it by ID:

```bash
python scripts/fetch_tips.py --add-tip-id <tip_id> --out data/tips.json
```

The tip is fetched via `/v2/tips/{id}`, automatically marked `closed=True`, and merged into `tips.json`.

### One-time venue sweep (manual)

The `--sweep` mode probes every venue in `checkins.csv` that has **no tip yet** in the current `tips.json`, calling `/venues/{vid}/tips?filter=self` for each. This was used once to discover tips on closed venues that `/users/self/tips` silently omitted. Any tip found only via the sweep is marked `closed=True`.

This is **not run in CI** — it is a manual exercise for bulk recovery:

```bash
python scripts/fetch_tips.py --full --sweep --csv data/checkins.csv --out data/tips.json
```

The sweep skips venues that already have at least one tip in `tips.json` (matched by `venue_id`), so it only probes venues with zero known tips.

### Bulk reconciliation via Foursquare data export

If you want to verify nothing is missing against the authoritative Foursquare export:

1. Download your data export from `foursquare.com/settings/data-export`
2. Extract the archive — locate `tips.json` inside
3. Run `find_closed_venue_tips.py` to diff and import missing tips, verifying closed/deleted status via venue pages (requires browser session cookies)

```bash
python scripts/find_closed_venue_tips.py \
  --token "$FOURSQUARE_TOKEN" --cookies cookies.txt \
  --csv data/checkins.csv --tips data/tips.json
```

---

## Search (Cloudflare D1 + Pages Functions)

Search is powered by a **Cloudflare Pages Function** (`functions/api/search.js`) that queries a **D1 SQLite database** (`swarmdata`) at runtime. The old static `search-index.json` (6+ MB) is no longer generated — the search page opens instantly and queries on demand.

### What's stored in D1

| Table | Contents | Sync strategy |
|-------|----------|---------------|
| `checkins` | All check-ins (all source columns) | Append-only — only new rows inserted |
| `venues` | Venue metadata + visit counts | Updated only for venues touched by new check-ins |
| `tips` | All tips with counts | Full upsert only when tips changed (`--tips-changed`) |
| `ratings` | Venue likes (okays/dislikes return 402) | Full upsert only when ratings changed (`--ratings-changed`) |
| `lists` / `list_venues` | Foursquare lists + visited status | Full upsert only when checkins changed (`--lists-changed`) |
| `trips` | Trip metadata (name, dates, countries, cities) | Full upsert only when checkins changed (`--trips-changed`) |

### D1 sync setup

1. Create a D1 database in the Cloudflare dashboard: Workers & Pages → D1 → Create database → name it `swarmdata`
2. Note the database ID and add these secrets to your GitHub repo:
   - `CF_D1_TOKEN` — Cloudflare API token with D1 Edit permission
   - `CF_ACCOUNT_ID` — your Cloudflare account ID
   - `CF_D1_DATABASE_ID` — the database ID from step 1
3. Add the D1 binding to your Pages project (see Cloudflare Pages setup step 4 above)
4. Trigger a manual `workflow_dispatch` run to perform the initial full sync

### Search API

`GET /api/search?q=<query>` — returns JSON:
```json
{ "venue": [...], "city": [...], "trip": [], "tip": [...], "companion": [...] }
```

Companion results aggregate all three source fields: `with_name`, `created_by_name`, and `overlaps_name` (comma-separated in the DB, split at query time).

### Feed API

`functions/api/feed.js` serves `feed.html` with four endpoints:

| Endpoint | Purpose |
|----------|---------|
| `GET /api/feed?limit=N` | Newest N check-ins (cursor-scroll default) |
| `GET /api/feed?cursor=TS&limit=N` | N check-ins older than timestamp TS |
| `GET /api/feed?after=TS&limit=N` | N check-ins newer than TS (newest-first) — reverse gap fill |
| `GET /api/feed?oldest=1&limit=N` | N oldest check-ins (newest-first in response) |
| `GET /api/feed?month=YYYY-MM` | All check-ins in a calendar month |
| `GET /api/feed?resolve=TS` | Cursor that loads items older than TS |

`feed.html` uses a **contiguous-array virtual scroll** architecture:
- On init: 100 newest items fetched; all further loading is on-demand only.
- Scrolling toward the bottom triggers `loadFwd()` (50 older items, `?cursor=TS`); appended to `ALL`.
- Scrolling toward the top triggers `loadRev()` (50 newer items, `?after=TS`); prepended to `ALL` with scroll-position correction.
- Navigation jumps (`goYMD`, `goLatest`, `goOldest`) reset state with a generation counter (`_loadGen`) to discard in-flight stale fetches.
- `feed_meta.json` (static, built at CI time) provides calendar counts and total — no D1 query for those.

---

## Maintenance operations

Beyond the hourly `update-dashboard` job, several manual workflows handle data hygiene. Trigger them from the **Actions** tab.

### Delete a check-in (`delete-checkin` workflow)

Removes one or more check-ins by ID from `checkins.csv` (private data repo), deletes any orphaned venues, rebuilds the dashboard HTML, and syncs the deletion to D1.

Inputs:
- `checkin_ids` — comma-separated check-in IDs (e.g. `69e8b7321879ec52d271bd58,686a36759cc1064c129c0e72`)
- `dry_run` — `true` to preview without making changes

Locally:
```bash
python scripts/delete_checkin.py \
  --ids CHECKIN_ID1,CHECKIN_ID2 \
  --csv private-data/checkins.csv \
  --dry-run   # optional
```

### Bulk D1 resync via wrangler SQL dump (`resync-checkins-d1` workflow)

The Python batch-API sync path (`--force-checkins`) is unreliable for 65K rows — a single network failure leaves D1 in a partial state. The wrangler SQL dump path is the safe alternative: it generates one `.sql` file and executes it atomically against D1.

Use after:
- Stale-row cleanup (e.g. `delete_checkin.py` on a removed check-in)
- Archive dedup / manual `checkins.csv` correction
- Any case where `checkin_id` / `venue_id` columns drifted out of sync

```bash
# Locally (PowerShell — requires wrangler on PATH + CF_D1_TOKEN + CF_ACCOUNT_ID):
python scripts/gen_d1_dump.py \
  --csv private-data/checkins.csv \
  --out /tmp/checkins_venues_dump.sql
npx wrangler d1 execute swarmdata --file=/tmp/checkins_venues_dump.sql --remote
```

The workflow additionally runs a `SELECT COUNT(*)` verification query afterwards.

### Force resync individual tables (`resync-d1` workflow)

For tips / ratings / lists / trips that drifted (e.g. after a Foursquare data export reveals extra items), tick the tables to reset in the workflow dispatch inputs. Backed by `sync_to_d1.py --force-tips --force-ratings ...`.

### Venue-metadata hygiene

- `refresh-venue` — re-fetches one venue's metadata from Foursquare (when a venue gets renamed/moved and the hourly diff misses it)
- `fix-overlaps` — runs `enrich_overlaps.py` / `fix_overlap_dupes.py` to backfill or clean `overlaps_*` fields on older rows
- `add-venue-tip` / `add-venue-rating` — post a tip or set like/okay/dislike on a venue via the Foursquare API, then sync to D1

---

## Data flow

```
data/checkins.csv
  → transform.py (city_merge.yaml, city_fixes.json, country_fixes.json)
  → metrics.py (categories.json, settings.yaml; collect_companions, shout_records,
                shout_analysis, cross_dim_analysis)
  → build.py (templates/*.tmpl → *.html)
  → gen_*.py (templates/*.tmpl → *.html; some legacy generators still use _TMPL_B64)
  → build.py post-process pass:
       {{CTRY_CODE_JSON}} → config/country_flags.json
       {{CAT_ICON_JSON}}  → config/category_icons.json
       (applied to every generated HTML file, once, from the canonical source)

data/photos.json + data/pix/           (optional, local only)
  → build.py --photos → gen_photos.py → photos.html
                       → trips.html (inline thumbnails)
                       → index.html (recent 30 photos section)

Cloudflare R2 (pix/ prefix)            (deployed site)
  → build.py --pix-url → same pages with R2 URLs instead of local file:// URIs

data/checkins.csv + tips.json + ...    (CI sync, incremental)
  → sync_to_d1.py → Cloudflare D1 (swarmdata)
  → functions/api/search.js → /api/search?q= (live queries from search.html)
  → functions/api/feed.js   → /api/feed?cursor=N or ?after=N
                              (cursor-based feed; cards include `companions` array
                               built by collectCompanions() = JS mirror of Python's
                               collect_companions, union of with_name +
                               created_by_name + overlaps_name)
```

## Dependencies

- **Python 3.9+** (uses `zoneinfo` from stdlib)
- `requests>=2.31` — HTTP requests for Foursquare API
- `pyyaml>=6.0` — YAML config parsing
- `timezonefinder>=6.2` — Lat/lng to timezone resolution
- **Front-end** (loaded via CDN): Leaflet, Chart.js, Twemoji

---

## Changing the update schedule

Edit `.github/workflows/update-dashboard.yml`, the `cron` line:

```yaml
- cron: '0 */1 * * *'  # Every hour (default)
- cron: '0 8 * * 1'    # Every Monday at 08:00 UTC
- cron: '0 8 1 * *'    # 1st of every month
- cron: '0 8 * * *'    # Daily
```

---

## How trip detection works

A **trip** is any consecutive sequence of check-ins where `city != home_city`,
provided the sequence contains at least `min_checkins` entries. The trip name
is auto-generated from the most-visited countries/cities in that sequence.
Each trip gets a detail page in `trips.html` with a heatmap, timeline, and
category breakdown.

After the raw sequence is found, several extension passes run in order:

1. **Transport hub departure** — scans backward for Rail/Train/Bus Station or Airport within 24h; chains multiple hubs (e.g. Bus Station → Airport) up to 3h apart.
2. **Same-day departure** (if no hub found) — finds earliest `Transportation Service`, `Bus Line`, or `Parking`, or nearest `Fuel Station`, on the same UTC day.
3. **Arrival hub scan** — scans forward up to 24h for the first home-city transport hub.
4. **Neighborhood arrival fallback** — if no hub, extends to a `Neighborhood` check-in in home city within 24h.
5. **Home arrival extension** — extends to a `Home (private)` check-in within 5h (transit return) or 12h (car/local return).
6. **Forced end override** (`trip_end_overrides.json`) — manually extend a trip's end.
7. **Forced start override** (`trip_start_overrides.json`) — manually prepend earlier rows.
8. **Bicycle departure extension** — for trips tagged `"bicycle"` in `trip_tags.json`, scans backward up to 4h for outdoor/road check-ins (parks, trails, roads, etc.) belonging to the departure ride.

Trip names and tags are looked up by `_name_ts` — the timestamp of the first extended check-in, evaluated after steps 1–7 but before step 8.
