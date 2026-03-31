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
searchable cities & venues · venue loyalty · category explorer · recent check-ins with historical weather ·
tips page with country/city tabs, map, closed/deleted-venue detection, view counts, and filter buttons ·
**photo gallery** with 21 000+ check-in photos hosted on Cloudflare R2, country/city accordion filter,
lazy loading, lightbox, and inline tip photos.

---

## Project layout

```
.
├── scripts/
│   ├── fetch_checkins.py        # Fetch check-ins from Foursquare API → data/checkins.csv
│   ├── fetch_tips.py            # Fetch tips → data/tips.json (incremental + venue sweep)
│   ├── fetch_photos.py          # Fetch check-in photos from Foursquare data export → data/photos.json
│   ├── transform.py             # Data cleaning: country fixes, city normalisation
│   ├── metrics.py               # All aggregation + trip-detection logic
│   ├── build.py                 # CLI entry point: checkins.csv → index.html + trips.html
│   ├── gen_companions.py        # Generates companions.html
│   ├── gen_feed.py              # Generates feed.html (infinite-scroll with weather)
│   ├── gen_photos.py            # Generates photos.html (full gallery, city filter, tip photos)
│   ├── gen_tips.py              # Generates tips.html (country/city tabs, map, CLOSED badges)
│   ├── gen_venues.py            # Generates venues.html (top 500 venues)
│   ├── gen_worldcities.py       # Generates world_cities.html
│   └── find_closed_venue_tips.py  # One-time utility: find tips on closed venues via browser cookies
├── data/
│   ├── checkins.csv          # Your check-in data — gitignored, lives in private repo
│   ├── tips.json             # Your tips data — gitignored, lives in private repo alongside checkins.csv
│   └── photos.json           # Photo index {checkin_id: [filenames]} — gitignored, lives in private repo
├── workers/
│   └── checkin-poller/       # Cloudflare Worker: polls Foursquare every minute,
│       ├── worker.js         #   triggers GitHub Actions on new check-in
│       └── wrangler.toml
├── config/
│   ├── settings.yaml              # home_city, trip_detection thresholds
│   ├── city_merge.yaml            # Raw Foursquare city names → canonical names
│   ├── categories.json            # Category groupings for charts + explorer
│   ├── city_fixes.json            # Per-timestamp city overrides
│   ├── country_fixes.json         # Per-timestamp country overrides
│   ├── trip_names.json            # Trip name overrides (keyed by _name_ts)
│   ├── trip_tags.json             # Trip tags, e.g. ["bicycle"] (keyed by _name_ts)
│   ├── trip_exclude.json          # Trip start timestamps to exclude entirely
│   ├── trip_start_overrides.json  # Force trip start at an earlier timestamp
│   └── trip_end_overrides.json    # Force trip end at a specific timestamp
├── templates/
│   ├── index.html.tmpl       # Template for index.html
│   ├── trips.html.tmpl       # Template for trips.html
│   └── tips.html.tmpl        # Template for tips.html
├── index.html                # Main dashboard (built by CI, committed)
├── trips.html                # Trip journal (built by CI, committed)
├── companions.html           # Companions page (built by CI)
├── feed.html                 # Check-in feed (built by CI)
├── tips.html                 # Tips page (built by CI)
├── venues.html               # Top venues (built by CI)
├── world_cities.html         # World cities explorer (built by CI)
├── requirements.txt          # Python deps (requests, pyyaml, timezonefinder)
├── netlify.toml              # Netlify config (builds disabled — CI-only deploys)
└── wrangler.jsonc            # Cloudflare Pages config
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

**Option B — Cloudflare Pages**
1. Connect the repo in the Cloudflare dashboard
2. Build command: *(leave empty — HTML is pre-built by CI)*
3. Build output: `/` (repo root)
4. The `wrangler.jsonc` is already configured

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

# Fetch tips (incremental)
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

# Tips: force full re-fetch + venue sweep (finds tips on closed venues)
python scripts/fetch_tips.py --full --sweep --csv data/checkins.csv

# Tips: recover tips missing from API using a Foursquare data export
# 1. Download your export from foursquare.com/settings/data-export
# 2. Locate tips.json inside the extracted archive
# 3. Run find_closed_venue_tips.py to cross-check and import missing tips:
python scripts/find_closed_venue_tips.py \
  --token "$FOURSQUARE_TOKEN" --cookies cookies.txt \
  --csv data/checkins.csv --tips data/tips.json
# Then verify closed/deleted status of new tips against venue pages (requires cookies)

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

Venue diff is done by `scripts/sync_venue_changes.py`. It compares these fields per venue_id: `venue`, `city`, `country`, `lat`, `lng`, `category`.

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

## Data flow

```
data/checkins.csv
  → transform.py (city_merge.yaml, city_fixes.json, country_fixes.json)
  → metrics.py (categories.json, settings.yaml)
  → build.py (templates/*.tmpl → *.html)
  → gen_*.py (embedded templates → *.html)

data/photos.json + data/pix/           (optional, local only)
  → build.py --photos → gen_photos.py → photos.html
                       → trips.html (inline thumbnails)
                       → index.html (recent 30 photos section)

Cloudflare R2 (pix/ prefix)            (deployed site)
  → build.py --pix-url → same pages with R2 URLs instead of local file:// URIs
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
