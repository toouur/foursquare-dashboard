# Local IMDb ratings refresh

Use this when IMDb blocks both GitHub Actions and ordinary HTTP requests. The script opens a visible Chromium window on your own computer and lets you sign in manually. It does not solve or bypass CAPTCHA, MFA, WAF, or other security checks.

## Setup (Windows PowerShell)

1. Clone `toouur/foursquare-dashboard` and your private `toouur/foursquare-data` repository next to each other.
2. From the `foursquare-dashboard` folder, install the local dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install playwright requests
python -m playwright install chromium
```

3. Run the refresh (adjust the sibling folder name if yours differs):

```powershell
python scripts/fetch_imdb_local.py --out ..\foursquare-data\imdb_ratings.json --profile "$env:USERPROFILE\.imdb-playwright-profile"
```

A visible Chromium window opens. Sign in to IMDb yourself, complete any normal verification, return to the PowerShell window, and press Enter. The script reads the ratings page and validates the result before replacing the JSON file. If it encounters a block or too few ratings, it leaves the existing file unchanged.

The browser profile is stored under your Windows user profile, not in either repository. It contains login/session data: do not share it, upload it, or commit it. To force a fresh sign-in, close Chromium and remove that profile folder yourself.

## Commit the updated data

After a successful run, inspect the private data repo:

```powershell
cd ..\foursquare-data
git diff -- imdb_ratings.json
git status --short
```

If the diff looks right, commit and push the JSON from the private data repository. Do not commit the browser profile.
