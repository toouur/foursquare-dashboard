# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Build the dashboard
```bash
# Full build (all pages)
/c/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe scripts/build.py --input data/checkins.csv --config-dir config --output-dir .

# Standard python3 also works if PyYAML is installed there
python scripts/build.py --input data/checkins.csv --config-dir config --output-dir .
```
> **Note:** The system `python3` may be Python 3.14 (Windows Store) without PyYAML. Use the Python 3.12 path above if `import yaml` fails.

### Fetch check-ins from Foursquare API
```bash
python scripts/fetch_checkins.py --token "$FOURSQUARE_TOKEN" --csv data/checkins.csv
python scripts/fetch_checkins.py --full   # Force full re-fetch
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
  → scripts/build.py              # orchestrator:
      ├── scripts/transform.py    # normalise city/country names
      ├── scripts/metrics.py      # compute all aggregations + trip detection
      ├── templates/index.html.tmpl  → index.html
      ├── templates/trips.html.tmpl  → trips.html
      ├── scripts/gen_companions.py  → companions.html
      ├── scripts/gen_feed.py        → feed.html
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
| `build.py` | CLI entry: loads settings.yaml, calls transform → metrics → renders templates → calls gen_*.py |
| `fetch_checkins.py` | Incremental or full Foursquare API fetch; exits with `CHANGED=true/false` env var |

### Configuration files (`config/`)

| File | Purpose |
|------|---------|
| `settings.yaml` | `home_city`, trip detection thresholds (`min_checkins`) |
| `city_merge.yaml` | Raw Foursquare city strings → canonical names (Cyrillic, transliterations, district names) |
| `city_fixes.json` | Per Unix-timestamp city overrides (highest priority, wins over city_merge) |
| `country_fixes.json` | Per Unix-timestamp country overrides |
| `categories.json` | Two-level grouping: raw category → `category_groups` → `explorer_groups` |

### City/country name normalization pattern
Canonical city names flow through three layers (each overrides the previous):
1. `city_merge.yaml` — bulk normalisation
2. `city_fixes.json` — per-check-in city override (keyed by Unix timestamp string)
3. `country_fixes.json` — per-check-in country override

### Timezone handling
`metrics.py` has a `_COUNTRY_TZ` dict mapping country names to IANA timezone IDs. This takes priority over lat/lng-based lookup (`timezonefinder`) and is necessary for countries that don't observe DST (e.g., Belarus → `Europe/Minsk` = UTC+3 year-round, not UTC+2 from coordinates).

### World-cities continent-aware matching
`index.html.tmpl` and `gen_worldcities.py` both have a `CTRY_CONT` JavaScript dict and a `matchVisited`/`getVisitCount` function that guards against false city matches across continents (e.g., Malta's "Rabat" ≠ Morocco's "Rabat"). Any change to this logic must be kept in sync between both files.

### CSS: background-clip text
The `header h1` gradient text uses `-webkit-text-fill-color:transparent` + `background-clip:text`. The background gradient only covers the element's **padding box**, so glyph descenders (especially "J" in Playfair Display 900) need sufficient `padding-bottom` to remain visible.

### CSS: `:visited` link colors
Browsers silently ignore `var()` CSS custom properties inside `:visited` rules (security restriction). Use **literal hex values** (e.g., `#e0e2ec`) in `:visited` selectors, not `var(--text)`.

## Deployment
- **Cloudflare Pages:** Auto-deploys on every push to `main` (no build step needed)
- **Netlify:** Manual deploys only on last day of month (free tier limit); `netlify.toml` has empty build command
- **GitHub Actions:** Runs every hour to fetch new check-ins and rebuild; secrets needed: `FOURSQUARE_TOKEN`, `NETLIFY_SITE_ID`, `NETLIFY_AUTH_TOKEN`
