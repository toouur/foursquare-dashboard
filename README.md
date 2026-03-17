# Foursquare Check-in Dashboard

A self-updating personal dashboard for your Foursquare/Swarm check-in history.

**Features:** heatmap + dot map + country flag map · charts by year / month / hour / day of week ·
GitHub-style activity heatmap · travel timeline (Gantt) · trip journal with per-trip maps ·
searchable cities & venues · venue loyalty · category explorer · recent check-ins with historical weather.

---

## Project layout

```
.
├── scripts/
│   ├── fetch_checkins.py     # Fetch check-ins from Foursquare API → data/checkins.csv
│   ├── transform.py          # Data cleaning: country fixes, city normalisation
│   ├── metrics.py            # All aggregation + trip-detection logic
│   ├── build.py              # CLI entry point: checkins.csv → index.html + trips.html
│   ├── gen_companions.py     # Generates companions.html
│   ├── gen_feed.py           # Generates feed.html (infinite-scroll with weather)
│   ├── gen_venues.py         # Generates venues.html (top 500 venues)
│   └── gen_worldcities.py    # Generates world_cities.html
├── data/
│   └── checkins.csv          # Your check-in data — gitignored, lives in private repo
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
│   └── trips.html.tmpl       # Template for trips.html
├── index.html                # Main dashboard (built by CI, committed)
├── trips.html                # Trip journal (built by CI, committed)
├── companions.html           # Companions page (built by CI)
├── feed.html                 # Check-in feed (built by CI)
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

# Build dashboard
python scripts/build.py

# Preview in browser
python -m http.server 8000
```

**Common CLI options:**

```bash
# Force full re-fetch (ignore existing CSV)
python scripts/fetch_checkins.py --full

# Custom paths / home city
python scripts/build.py --input data/checkins.csv --home-city "Minsk" --config-dir config

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

## Data flow

```
data/checkins.csv
  → transform.py (city_merge.yaml, city_fixes.json, country_fixes.json)
  → metrics.py (categories.json, settings.yaml)
  → build.py (templates/*.tmpl → *.html)
  → gen_*.py (embedded templates → *.html)
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
