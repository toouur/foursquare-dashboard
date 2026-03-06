# 📍 Foursquare Check-in Dashboard

A self-updating personal dashboard for your Foursquare/Swarm check-in history.

**Features:** heatmap + dot map + country flag map · charts by year / month / hour / day of week ·
GitHub-style activity heatmap · travel timeline (Gantt) · trip journal with per-trip maps ·
searchable cities & venues · venue loyalty · category explorer · recent check-ins with historical weather.

---

## Project layout

```
.
├── fetch_checkins.py     # Fetch check-ins from Foursquare API → checkins.csv
├── transform.py          # Data cleaning: country fixes, city normalisation
├── metrics.py            # All aggregation + trip-detection logic
├── build.py              # CLI entry point: checkins.csv → index.html + trips.html
├── checkins.csv          # Your check-in data (committed by CI)
├── index.html            # Main dashboard (built by CI, committed)
├── trips.html            # Trip journal (built by CI, committed)
├── requirements.txt      # Python deps (requests, pyyaml)
├── netlify.toml          # Netlify config (builds disabled — CI-only deploys)
├── wrangler.jsonc        # Cloudflare Pages config
└── config/
    ├── settings.yaml     # home_city, trip_detection thresholds
    ├── city_merge.yaml   # Raw Foursquare city names → canonical names
    ├── country_fixes.json# Per-timestamp country overrides
    └── categories.json   # Category groupings for charts + explorer
```

---

## Setup (~10 minutes)

### 1. Fork or clone this repo

Make it **private** if you don't want your check-in history public.

### 2. Get your Foursquare OAuth token

1. Go to [foursquare.com/developers/apps](https://foursquare.com/developers/apps)
2. Create an app (or use an existing one)
3. Set **Redirect URI** to `https://localhost`
4. Open this URL in your browser (replace `YOUR_CLIENT_ID`):
   ```
   https://foursquare.com/oauth2/authenticate?client_id=YOUR_CLIENT_ID&response_type=token&redirect_uri=https://localhost
   ```
5. After approving, copy the `access_token` from the redirect URL

### 3. Add token as a GitHub Secret

1. Repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `FOURSQUARE_TOKEN`, Value: your token from step 2

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

1. Go to the **Actions** tab
2. Click **Weekly Dashboard Update** → **Run workflow**
3. Wait ~2 minutes
4. Visit your live URL 🎉

---

## Running locally

```bash
pip install -r requirements.txt

# Fetch check-ins
export FOURSQUARE_TOKEN=your_token_here
python fetch_checkins.py

# Build dashboard
python build.py

# Open in browser
open index.html
```

**Common CLI options:**

```bash
# Force full re-fetch (ignore existing CSV)
python fetch_checkins.py --full

# Custom paths / home city
python build.py --input checkins.csv --home-city "Minsk" --config-dir config

# Dump a full list of raw Foursquare categories seen in your data
python build.py --cat-list
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

## Changing the update schedule

Edit `.github/workflows/update.yml`, the `cron` line:

```yaml
- cron: '0 8 * * 1'   # Every Monday at 08:00 UTC (default)
- cron: '0 8 1 * *'   # 1st of every month
- cron: '0 8 * * *'   # Daily
```

---

## How trip detection works

A **trip** is any consecutive sequence of check-ins where `city ≠ home_city`,
provided the sequence contains at least `min_checkins` entries. The trip name
is auto-generated from the most-visited countries/cities in that sequence.
Each trip gets a detail page in `trips.html` with a heatmap, timeline, and
category breakdown.
