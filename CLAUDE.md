# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Build the dashboard
```bash
# Full build (all pages) — data lives in private repo alongside checkins.csv
/c/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe scripts/build.py \
  --input C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --config-dir config --output-dir .

# Standard python3 also works if PyYAML is installed there
python scripts/build.py --input data/checkins.csv --config-dir config --output-dir .
```
> **Note:** The system `python3` may be Python 3.14 (Windows Store) without PyYAML. Use the Python 3.12 path above if `import yaml` fails.

### Build with photos (local)
```bash
# Enables inline photos in trips.html, photos.html gallery, recent photos on index.html
/c/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe scripts/build.py \
  --input C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --config-dir config --output-dir . \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json

# Build with Cloudflare R2 URL (for CI / deployed site)
/c/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe scripts/build.py \
  --input C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --config-dir config --output-dir . \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json \
  --pix-url "https://pub-5514667a5da04a75986022e39efc7118.r2.dev/pix"
```

### Fetch photos from Foursquare data export
```bash
# Incremental — only new check-ins since last index (auto-detects cutoff from photos.json)
/c/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe scripts/fetch_photos.py \
  --token "$FOURSQUARE_TOKEN" \
  --export path/to/export/photos/ \
  --csv C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json \
  --pix-dir C:/Users/toouur/Documents/GitHub/foursquare-data/pix/
```

### Fetch check-ins from Foursquare API
```bash
python scripts/fetch_checkins.py --token "$FOURSQUARE_TOKEN" --csv data/checkins.csv
python scripts/fetch_checkins.py --full   # Force full re-fetch
```

### Refresh a renamed/merged/moved venue in checkins.csv
```bash
# Venue renamed (same ID, new name/location info)
python scripts/refresh_venue.py --token "$FOURSQUARE_TOKEN" --csv data/checkins.csv \
  --venue-id 4d8f90e3cb9b224b49d99d41

# Venue merged (old ID → new ID; fetches info from new ID, updates venue_id in CSV)
python scripts/refresh_venue.py --token "$FOURSQUARE_TOKEN" --csv data/checkins.csv \
  --venue-id OLD_VENUE_ID --new-venue-id NEW_VENUE_ID

# Preview changes without writing
python scripts/refresh_venue.py ... --dry-run
```

### Fetch tips from Foursquare API
```bash
# Incremental (new tips only)
python scripts/fetch_tips.py --token "$FOURSQUARE_TOKEN" --out data/tips.json

# Full re-fetch + venue sweep (finds tips on closed/deleted venues)
python scripts/fetch_tips.py --full --sweep --csv data/checkins.csv --out data/tips.json
```

Tips live in the private data repo at `data/tips.json` (same folder as `checkins.csv`).
The venue sweep adds tips whose venues are NOT returned by `/users/self/tips` — these are automatically marked `closed=True`.
To recover tips from closed venues using browser cookies (one-time):
```bash
python scripts/find_closed_venue_tips.py --token "$FOURSQUARE_TOKEN" --cookies cookies.txt --csv data/checkins.csv --tips data/tips.json
```

### Preview locally
```bash
python -m http.server 8000   # then open http://localhost:8000
```

### Dump all raw Foursquare category strings seen in data
```bash
python scripts/build.py --input data/checkins.csv --config-dir config --output-dir . --cat-list
```

## Architecture

### Data flow
```
Foursquare API
  → scripts/fetch_checkins.py     # writes/updates data/checkins.csv
  → scripts/fetch_tips.py         # writes/updates data/tips.json
  → scripts/fetch_photos.py       # writes/updates data/photos.json; downloads pix/ files
  → scripts/build.py              # orchestrator:
      ├── scripts/transform.py    # normalise city/country names
      ├── scripts/metrics.py      # compute all aggregations + trip detection
      ├── templates/index.html.tmpl  → index.html  (includes TIPS_RECENT, recent 30 photos)
      ├── templates/trips.html.tmpl  → trips.html  (inline photo thumbnails per check-in)
      ├── scripts/gen_companions.py  → companions.html
      ├── scripts/gen_feed.py        → feed.html
      ├── scripts/gen_photos.py      → photos.html  (full gallery, city filter, tip photos)
      ├── scripts/gen_tips.py        → tips.html    (reads tips.json, tip photo cards)
      ├── scripts/gen_venues.py      → venues.html
      └── scripts/gen_worldcities.py → world_cities.html
```

All HTML output is **pre-built and committed** — the site is purely static with client-side JS.

### Template system
- `templates/*.html.tmpl` use simple `{{PLACEHOLDER}}` substitution (not Jinja2)
- `gen_*.py` scripts embed their templates as **base64-encoded strings** (`_TMPL_B64`). To modify them: base64-decode the string, edit the HTML/CSS/JS, re-encode.

### Key scripts

| Script | Role |
|--------|------|
| `transform.py` | Apply city_merge.yaml, city_fixes.json, country_fixes.json; infer blank cities from CSV centroids |
| `metrics.py` | All aggregations, trip detection, timezone-aware local timestamps, `recent` last-30 check-ins |
| `build.py` | CLI entry: loads settings.yaml, calls transform → metrics → renders templates → calls gen_*.py; also loads tips.json for TIPS_RECENT section |
| `fetch_checkins.py` | Incremental or full Foursquare API fetch; exits with `CHANGED=true/false` env var; on full re-fetch writes `duplicate_checkins.csv` and updates `checkins_anomalies.json` |
| `sync_venue_changes.py` | Diffs two `checkins.csv` snapshots by venue_id; patches `tips.json` with updated venue metadata (no extra API calls) |
| `fetch_tips.py` | Incremental or full tips fetch from `/users/self/tips`; optional `--sweep` probes per-venue for tips on closed venues (auto-marks `closed=True`); captures `viewCount` → `view_count` |
| `fetch_photos.py` | Indexes photos from Foursquare data export into `photos.json`; auto-detects cutoff timestamp so only new check-ins are fetched; downloads jpg files into `pix/` |
| `gen_tips.py` | Builds tips.html from tips.json: normalises country names via `CTRY_NORM` dict, city names via city_merge.yaml, computes `TABS_DATA` for country/city tab filtering; shows tip photo cards |
| `gen_photos.py` | Builds photos.html: full gallery with lazy loading, country/city accordion filter, tip photos section, lightbox; injects `COUNTRIES_PLACEHOLDER` to avoid f-string conflicts with JSON |
| `find_closed_venue_tips.py` | One-time utility: uses browser cookies to scrape venue pages and recover tips that the API omits entirely |

### `fetch_checkins.py` — full re-fetch logic

**Modes**

| Mode | Trigger | Strategy |
|------|---------|----------|
| Incremental | CSV exists, no `--full` | `afterTimestamp=<max_ts>` — fetches only newer rows |
| Full (local) | `--full` outside CI | `fetch_full_offset`: `?offset=N` pagination — simple but Foursquare silently caps at ~2,500 rows |
| Full (CI) | `--full` inside CI | `fetch_full_timestamp`: walks backwards via `?beforeTimestamp=T` — no cap, handles full history |

**Quota handling**

`fetch_full_timestamp` catches 403/quota/rate-limit errors mid-fetch and returns `(partial_rows, completed=False)` instead of raising. The partial rows are merged with the existing CSV so work done is not lost. The `archive-checkins` workflow step uses `continue-on-error: true` so the diff and commit steps always run even on a partial fetch.

**500-retry logic** (`request_checkins`)

The Foursquare API sporadically returns HTTP 500 near certain `beforeTimestamp` values. `request_checkins` retries up to 120 times, nudging `beforeTimestamp` back by 1 second on each 500, before giving up.

**Merge logic on full re-fetch**

1. Count `(venue_id, date)` key occurrences in existing rows — any key appearing >1 time is a duplicate.
2. Write all duplicate rows to `duplicate_checkins.csv` next to `checkins.csv` and emit `WARNING`.
3. Build `fetched_map = {(venue_id, date): row}` from the API response.
4. `all_rows = [fetched_map.get(key, existing_row) for existing_row in existing_rows]` — preserves duplicates, updates venue metadata in-place.
5. Append genuinely new rows (keys in fetched but not in existing).
6. Sort by timestamp, write back.
7. Detect missing rows: keys in existing (deduplicated) that are absent from fetched → record in anomalies file.

**`checkins_anomalies.json`** (written to private data repo next to `checkins.csv`)

Accumulates data quality issues across full re-fetch runs:

```json
{
  "_meta": { "updated": "YYYY-MM-DD", "duplicates_count": N, "missing_count": M },
  "duplicates": [ ...all rows whose (venue_id, date) appears >1 time... ],
  "missing":    [ ...rows present in CSV but not returned by API... ]
}
```

- `duplicates` — identical rows double-entered in the CSV. Preserved in CSV, flagged here.
- `missing` — check-ins on deleted/merged venues that Foursquare no longer returns. Preserved in CSV.
- Both lists accumulate (new entries merged in, existing entries never removed).

**`sync_venue_changes.py`** (called by `archive-checkins` workflow)

After a full re-fetch the archive workflow diffs the old and new `checkins.csv`:

1. Loads both CSVs, keeps the most-recent check-in per venue_id (highest `date`).
2. Compares `TRACKED = ["venue", "city", "country", "lat", "lng", "category"]` per venue_id.
3. Logs every changed venue with old→new values.
4. Patches matching `tips.json` entries in-place (lat/lng converted to `float` rounded to 5 dp).
5. Writes updated `tips.json` only if at least one tip changed.
6. Accepts `--dry-run` to report without writing.

### Configuration files (`config/`)

| File | Purpose |
|------|---------|
| `settings.yaml` | `home_city`, trip detection thresholds (`min_checkins`) |
| `city_merge.yaml` | Raw Foursquare city strings → canonical names (Cyrillic, transliterations, district names) |
| `city_fixes.json` | Per Unix-timestamp city overrides (highest priority, wins over city_merge) |
| `country_fixes.json` | Per Unix-timestamp country overrides |
| `categories.json` | Two-level grouping: raw category → `category_groups` → `explorer_groups` |
| `trip_names.json` | Trip name overrides, keyed by `_name_ts` (see below) |
| `trip_tags.json` | Trip tags (e.g. `["bicycle"]`), keyed by `_name_ts` |
| `trip_exclude.json` | Set of trip start timestamps to exclude entirely |
| `trip_end_overrides.json` | Force a trip to end at a specific timestamp; key = `ext_start_ts` |
| `trip_start_overrides.json` | Force a trip to start at an earlier timestamp; key = `ext_start_ts` |

### `_name_ts` key — how trip config files are keyed

All five `trip_*.json` files use the same key: **`_name_ts`**.

`_name_ts` = `int(ext[0]["date"])` — the timestamp of the **first check-in in the extended trip** — evaluated AFTER all departure/arrival extension passes (transport hub, same-day departure, arrival hub, neighborhood fallback, forced end override, forced start override) but **BEFORE** the bicycle departure extension.

In other words, the ordering is:
1. Transport hub departure scan
2. Same-day departure extension (if no hub found)
3. Arrival hub scan + neighborhood fallback
4. Home arrival extension
5. Forced end override (`trip_end_overrides`)
6. Forced start override (`trip_start_overrides`) — **`_name_ts` is read here**
7. Bicycle departure extension (reads `trip_tags` using `_name_ts` from step 6)

So:
- If a `trip_start_override` prepends earlier rows, `_name_ts` shifts to the new first row's timestamp. Keys in `trip_names.json` and `trip_tags.json` must use that shifted timestamp.
- `trip_start_overrides.json` key = `ext_start_ts` (the trip's first-row timestamp AFTER departure extension, BEFORE the override). Value = the timestamp to prepend from.
- `trip_end_overrides.json` key = `ext_start_ts` (same post-departure-extension start). Value = timestamp to extend to.
- Trip names can include emoji icons (✈️ 🚂 🚌 🚗 ⛺ 🛁) as suffixes.

### City/country name normalization pattern
Canonical city names flow through three layers (each overrides the previous):
1. `city_merge.yaml` — bulk normalisation
2. `city_fixes.json` — per-check-in city override (keyed by Unix timestamp string)
3. `country_fixes.json` — per-check-in country override

### Timezone handling
`metrics.py` has a `_COUNTRY_TZ` dict mapping country names to IANA timezone IDs. This takes priority over lat/lng-based lookup (`timezonefinder`) and is necessary for countries that don't observe DST (e.g., Belarus → `Europe/Minsk` = UTC+3 year-round, not UTC+2 from coordinates).

### Trip detection logic (`metrics.py` — `detect_trips()`)

Trips are consecutive non-home-city check-in sequences. After the raw sequence is found, several extension passes are applied in order:

**1. Transport hub departure (backward scan)**
Scans backward from the first non-home row through home-city and blank-city rows within 24h. Finds the **earliest** transport hub (`_TRANSPORT_CATEGORIES`: Rail Station, Train Station, Airport, Light Rail Station, Bus Station, Bus Terminal, Ferry Terminal) — chaining multiple different hubs (e.g. Bus Station → Airport) up to 3h apart. Same venue repeated keeps only the later occurrence. Respects `prev_end_idx` (won't scan into the previous trip's arrival rows).

**2. Same-day departure extension (if no hub found)**
Scans backward on the same UTC day as trip start through home-city and blank-city rows:
- `Transportation Service`, `Bus Line`, `Parking`: find **earliest** (keep scanning)
- `Fuel Station`: find **nearest** (stop at first)
Transportation Service/Bus Line/Parking wins over Fuel Station. Gap filter: if chosen departure is >4h before trip start, it's a previous-day activity — discarded.

**3. Arrival hub scan (forward scan)**
Scans forward up to 24h after last trip check-in through all rows (including intermediate non-home cities — e.g. fuel stops on the return leg). Finds the **nearest** (first) home-city transport hub and includes all rows up to it.

**4. Neighborhood arrival fallback**
If no transport hub found on arrival, looks for a `Neighborhood` check-in in home city within 24h. Aborts if `Home (private)` is found first. Also stops at any non-home, non-blank city.

**5. Home arrival extension**
Scans forward from the current trip end (after hub/neighborhood extension) looking for a `Home (private)` check-in in home_city. Window: 5h if an arrival hub was found, 12h otherwise. Stops at: non-home non-blank city (unless it's a `_ROADSIDE_CATS` category: Fuel Station, Gas Station, Rest Stop, Truck Stop, Road, Highway), or a `_NIGHTLIFE_CATS` category (bar, pub, etc.), or the time cap.

**6. Forced end override** (`trip_end_overrides`)
Key = `ext_start_ts` (post-departure-extension start). Value = timestamp to extend to.

**7. Forced start override** (`trip_start_overrides`)
Key = `ext_start_ts`. Value = earlier timestamp to prepend from. After applying, `_name_ts` = new `ext[0]["date"]`.

**`_name_ts` is recorded here** (= `int(ext[0]["date"])`) — used as key in `trip_names.json` and `trip_tags.json`.

**8. Bicycle departure extension**
For trips where `trip_tags[_name_ts]` contains `"bicycle"`: scans backward up to 4h from the current trip start through home-city rows (blank-city rows are skipped silently). Finds the **earliest** row with a `_BICYCLE_PASSTHROUGH_CATS` category as anchor, then includes all rows from anchor to trip start (including non-passthrough intermediates like sculptures or plazas passed en route).

`_BICYCLE_PASSTHROUGH_CATS`: Sports and Recreation, Road, Bridge, River, Lake, Waterfall, Park, Trail, Bike Trail, Other Great Outdoors, Beach, Reservoir, Bike Rental.

### Tips page (`gen_tips.py` + `templates/tips.html.tmpl`)

`tips.html` is built from `data/tips.json` (stored in the private data repo). Key details:

- **Country normalisation:** `gen_tips.py` has a `CTRY_NORM` dict mapping local-language country names (Cyrillic, Arabic, etc.) from the Foursquare API to English. This is also imported by `build.py` for the TIPS_RECENT section in index.html.
- **City normalisation:** `city_merge.yaml` (same as checkins pipeline) is applied to `t.city` → `t.nci`.
- **`TABS_DATA`:** `{country: {total, cities: [[city, count], …]}}` injected as a JS constant for client-side country/city tab filtering.
- **Closed venues:** Tips discovered via venue sweep have `closed=True` in tips.json. The template shows a red `CLOSED` badge next to the venue name. The `tc-loc` span uses `display:inline-flex` + `gap` for flag spacing.
- **Template:** `tips.html.tmpl` uses `{{TIPS_DATA_PLACEHOLDER}}` and `{{TABS_DATA_PLACEHOLDER}}` substitution.

### Tips in index.html (`build.py` TIPS_RECENT)

`build.py` loads `data/tips.json` (resolved next to `checkins.csv`), takes the 30 most recent, and injects them as `{{TIPS_RECENT}}` into `index.html.tmpl`. Each recent tip item includes `nc` (normalised country), `nci` (normalised city), and `closed` fields. The tip cards use `window._catIcon` (exported from the checkins IIFE) for category icons.

### Photos pipeline (`fetch_photos.py` + `gen_photos.py` + `--photos` flag)

**Data files (private repo):**
- `photos.json` — `{checkin_id: [filenames]}` index; 13 692 entries; filenames like `29447180_AbCdEf.jpg`
- `pix/` — 21 151 jpg files (gitignored, local only)
- **Tip photos:** 12 tips have a `photo` field in `tips.json` (e.g. `"photo": "29447180_AbCdEf.jpg"`); these were discovered as orphan files in the data export that belonged to tips, not check-ins

**Cloudflare R2 (deployed site):**
- Bucket: `foursquare-photos`, files stored at `pix/filename.jpg`
- Public URL: `https://pub-5514667a5da04a75986022e39efc7118.r2.dev`
- `--pix-url` passed to build must include the `/pix` prefix: `https://pub-....r2.dev/pix`
- CI uploads new photos via `aws s3 sync private-data/pix/ s3://${R2_BUCKET_NAME}/pix/`
- Secrets: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID`, `R2_BUCKET_NAME`, `R2_PUBLIC_URL`

**`fetch_photos.py` cutoff logic:**
- Finds max timestamp of check-ins already in `photos.json` (the cutoff)
- Only fetches photos for check-ins **newer** than that cutoff AND not already indexed
- Prevents re-downloading the entire history on each incremental run

**`gen_photos.py` architecture:**
- `build_page()` accepts `tips: list | None` parameter — tip photos shown in a separate section
- Builds `countries_data` hierarchy (country → cities with ≥5 photos) for the accordion filter
- Uses `COUNTRIES_PLACEHOLDER` injection (not f-string) to avoid `{` collisions with JSON content
- `galMode` variable (`'photos'` or `'tips'`) controls lightbox navigation context
- Variable name: loop uses `ctotal` (not `total`) to avoid overwriting the outer photo count
- Hero count links to tip photos section: `{total + len(tip_photos):,} photos · + N tip photos ↓`

**`build.py` photos integration:**
- `--photos path` loads `photos.json`; `--pix-url URL` overrides local `file:///` URI
- `_pix_dir_uri`: local `file:///` path (local builds) or R2 URL (CI builds)
- Recent photos: `"srcs": [_pix_dir_uri + "/" + f for f in _photos]` (list for multi-photo badge)
- Tip `photo` field: `pix_dir_uri + "/" + t["photo"]` if present and pix_url set
- `gen_photos.py` called with `tips=all_tips if _pix_dir_uri else []`

**`gen_tips.py` tip photo:**
- `photo_url = pix_url.rstrip("/") + "/" + t["photo"]` computed at build time, not stored in tips.json

**`index.html.tmpl` multi-photo cards:**
- `rcgCard`, `rcgPhoto` state (replaces single `rcgIdx`) — navigates within `photos[rcgCard].srcs`
- Badge: `+N` overlay on card when check-in has multiple photos
- Tip photo opens in new tab (`window.open`) rather than inline lightbox

### World-cities continent-aware matching
`index.html.tmpl` and `gen_worldcities.py` both have a `CTRY_CONT` JavaScript dict and a `matchVisited`/`getVisitCount` function that guards against false city matches across continents (e.g., Malta's "Rabat" ≠ Morocco's "Rabat"). Any change to this logic must be kept in sync between both files.

### CSS: background-clip text
The `header h1` gradient text uses `-webkit-text-fill-color:transparent` + `background-clip:text`. The background gradient only covers the element's **padding box**, so glyph descenders (especially "J" in Playfair Display 900) need sufficient `padding-bottom` to remain visible.

### CSS: `:visited` link colors
Browsers silently ignore `var()` CSS custom properties inside `:visited` rules (security restriction). Use **literal hex values** (e.g., `#e0e2ec`) in `:visited` selectors, not `var(--text)`.

## Data storage

`data/checkins.csv` and `data/tips.json` are **not committed** to this public repo. Both live in the private repo `toouur/foursquare-data` and are checked out during CI via `DATA_REPO_PAT`. Locally the files live at `data/checkins.csv` and `data/tips.json` (gitignored).

```bash
# Full re-fetch of check-ins
python scripts/fetch_checkins.py --full --token "$FOURSQUARE_TOKEN" --csv data/checkins.csv

# Full re-fetch of tips + venue sweep for closed-venue tips
python scripts/fetch_tips.py --full --sweep --token "$FOURSQUARE_TOKEN" --out data/tips.json --csv data/checkins.csv
```

**Closed-venue tip detection:** `fetch_tips.py --sweep` probes each venue in `checkins.csv` that isn't already in `tips.json` and marks any discovered tips as `closed=True`. The `find_closed_venue_tips.py` script offers a deeper alternative using actual browser session cookies to scrape venue pages (use once after a full sweep if tips are still missing).

**Data export cross-check:** The Foursquare account data export (`foursquare.com/settings/data-export`) can contain tips absent from both API strategies — these are tips whose venues were deleted from the Foursquare index entirely (not just closed). After importing such tips, verify closed/deleted status by fetching each venue page with session cookies: pages that embed `"closed":true` in `__NEXT_DATA__` (or raw HTML) → set `closed=True`; pages that load on the legacy `app.foursquare.com` renderer with no closed marker but whose tip is still absent from the API → the tip was deleted by a moderator, set `deleted=True` instead.

**`tips.json` fields:** `id`, `ts`, `text`, `venue`, `venue_id`, `city`, `country`, `lat`, `lng`, `category`, `agree_count`, `disagree_count`, `view_count`, `closed` (bool, default `False`), `deleted` (bool, only present when `True`), `photo` (filename string, only present for 12 tips with photos). `nc`, `nci`, and `photo_url` are computed at build time by `gen_tips.py`, not stored in the file.

**`photos.json` fields:** `{checkin_id: [filename, ...]}` — top-level keys are check-in IDs (strings), values are lists of photo filenames. Tip photos are NOT stored here; they live in `tips.json` under the `photo` field.

**View count updates:** `view_count` is refreshed for any tip re-fetched during a `--full` run. Incremental runs only fetch tips newer than the latest known timestamp, so `view_count` on old tips stays frozen until the next full re-fetch.

## Deployment
- **Cloudflare Pages:** Auto-deploys on every push to `main` (no build step needed)
- **Netlify:** Manual deploys only on last day of month (free tier limit); `netlify.toml` has empty build command
- **GitHub Actions:** Runs every hour to fetch new check-ins and rebuild; secrets needed: `FOURSQUARE_TOKEN`, `DATA_REPO_PAT`, `NETLIFY_SITE_ID`, `NETLIFY_AUTH_TOKEN`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID`, `R2_BUCKET_NAME`, `R2_PUBLIC_URL`
- **Cloudflare Worker (`workers/checkin-poller/`):** Polls Foursquare every minute; triggers `workflow_dispatch` on new check-in for near-instant deploys (~4–5 min latency). Secrets: `FOURSQUARE_TOKEN`, `GITHUB_TOKEN` (set via `wrangler secret put`)
