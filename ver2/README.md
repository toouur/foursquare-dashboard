# 📍 Foursquare Check-in Dashboard

A self-updating personal dashboard for your Foursquare/Swarm check-in history.

**Live at:** `https://YOUR_USERNAME.github.io/foursquare-dashboard/`

## Features
- Heatmap + dot map of all check-ins
- Charts: by year, month, hour, day of week
- All countries, cities (searchable), top 500 venues
- Auto-updates every Monday via GitHub Actions

---

## Setup (one time, ~10 minutes)

### 1. Fork or create this repo
Make it **private** if you don't want your check-in history public.  
Make it **public** if you want a free GitHub Pages live site.

### 2. Get your Foursquare OAuth token
1. Go to [foursquare.com/developers/apps](https://foursquare.com/developers/apps)
2. Create an app (or use existing)
3. Set Redirect URI to `https://localhost`
4. Open this URL in your browser (replace `YOUR_CLIENT_ID`):
   ```
   https://foursquare.com/oauth2/authenticate?client_id=YOUR_CLIENT_ID&response_type=token&redirect_uri=https://localhost
   ```
5. After approving, copy the `access_token` from the redirect URL

### 3. Add token as GitHub Secret
1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `FOURSQUARE_TOKEN`
4. Value: your token from step 2
5. Save

### 4. Enable GitHub Pages
1. Go to repo → **Settings** → **Pages**
2. Source: **Deploy from a branch**
3. Branch: `main` / `(root)`
4. Save → your site will be at `https://YOUR_USERNAME.github.io/foursquare-dashboard/`

### 5. Run the first build
1. Go to **Actions** tab in your repo
2. Click **Weekly Dashboard Update**
3. Click **Run workflow** → **Run workflow**
4. Wait ~2 minutes for it to complete
5. Visit your GitHub Pages URL 🎉

---

## Running locally
```bash
pip install requests

# Fetch check-ins (set your token first)
export FOURSQUARE_TOKEN=your_token_here
python foursquare_checkins.py

# Build dashboard
python build_dashboard.py

# Open index.html in your browser
open index.html
```

## Files
| File | Purpose |
|------|---------|
| `foursquare_checkins.py` | Fetches check-ins from API → `checkins.csv` |
| `build_dashboard.py` | Builds `index.html` from `checkins.csv` |
| `index.html` | The dashboard (auto-generated, committed by Actions) |
| `checkins.csv` | Your check-in data (committed by Actions) |
| `.github/workflows/update.yml` | Auto-update schedule (every Monday 08:00 UTC) |

## Changing update frequency
Edit `.github/workflows/update.yml`, the `cron` line:
```yaml
- cron: '0 8 * * 1'   # Monday 08:00 UTC
- cron: '0 8 1 * *'   # 1st of every month
- cron: '0 8 * * *'   # Daily
```
