# Deploy runbook

How the dashboard is built and shipped, plus the one-time cutover and the
git-history reclaim. Lives outside `/docs/` because that path is gitignored.

## Architecture

- **Generated HTML is NOT committed to git.** It used to be (hourly), which
  bloated `.git` to ~4.2 GB. The site is now deployed by **direct upload** to
  Cloudflare Pages via wrangler.
- Source of truth: `scripts/` + `config/` + `templates/` + `functions/` in this
  repo, and the private data repo `toouur/foursquare-data` (`checkins.csv`,
  `tips.json`, `photos.json`, `venueRatings.json`, `lists.json`).
- CI (`.github/workflows/update-dashboard.yml`, hourly) fetches data → rebuilds
  HTML → syncs D1 → assembles a clean `_site/` → `wrangler pages deploy _site`.
- Cloudflare Pages project: **`4sq`**. D1 database: **`swarmdata`**
  (`52210bd9-a019-415e-8f12-6a73b42278f9`), bound as `DB`.

### What ships in `_site/`
Assembled by the "Assemble deploy directory" step — root asset globs only
(`*.html *.json *.xml *.svg *.txt *.csv`) plus `assets/` and `functions/`.
Subdirectories like `private-data/`, `scripts/`, `config/` are **never** copied
(globs match files, not dirs), with a defensive `rm -rf` as backstop. Static
pages (`solution.html`, `sitemap.xml`, `robots.txt`, `favicon.svg`) stay tracked
in git; everything else under `_site/` is regenerated each run.

## Required configuration

| Kind     | Name                | Notes |
|----------|---------------------|-------|
| Secret   | `CF_D1_TOKEN`       | Cloudflare API token. Needs **Account → D1 → Edit** AND **Account → Cloudflare Pages → Edit**. Reused for both D1 sync and Pages deploy. |
| Secret   | `CF_ACCOUNT_ID`     | Cloudflare account id (`CLOUDFLARE_ACCOUNT_ID` for wrangler). |
| Secret   | `FOURSQUARE_TOKEN`  | Foursquare OAuth token (fetch steps). |
| Secret   | `DATA_REPO_PAT`     | PAT with Contents R/W on `toouur/foursquare-data`. |
| Secret   | `R2_*`              | R2 photo upload + `R2_PUBLIC_URL` for build. |
| Secret   | `NETLIFY_*`         | Monthly Netlify mirror deploy. |
| Variable | `UPDATES_PAUSED`    | `true` halts the `update` + `netlify-monthly` jobs (see Pause). |

The Pages **D1 binding** must also be set in the dashboard: Pages → `4sq` →
Settings → Functions → D1 database bindings → Variable `DB` → `swarmdata`.
Pages **git auto-deploy must stay disabled** (Settings → Builds & deployments),
otherwise a push of the HTML-less tip would publish a broken site.

## Normal operation

Nothing manual. The hourly run deploys automatically. To force a deploy: Actions
→ "Update check-in dashboard" → Run workflow. Verify the **Deploy to Cloudflare
Pages** step prints a deployment URL.

## One-time cutover checklist (status: in progress)

1. [x] Add **Pages:Edit** scope to `CF_D1_TOKEN`.
2. [x] Disable Pages git auto-deploy in the CF dashboard.
3. [ ] `git push origin main` (deploy refactor is safe to land now — auto-deploy off).
4. [ ] Run the workflow manually; confirm the Pages deploy step succeeds and the
       live site renders (`index.html`, `search.html` → D1 Function, `feed.html`
       → `feed_meta.json`).
5. [ ] Reclaim history (below).

## Pause switch

Set repo variable `UPDATES_PAUSED=true` (Settings → Secrets and variables →
Actions → **Variables**) to stop scheduled/dispatch runs. Set to `false` or
delete to resume. Use it whenever you rewrite history or do maintenance.

## Reclaim git history (Part B — one-time, destructive)

Removes the old committed HTML/JSON blobs from **all** history. Rewrites every
commit SHA and requires a force-push — coordinate it (other clones must
re-clone; open PRs need rebasing). Run only after the cutover is confirmed.

```bash
# 1. Pause CI so it doesn't commit mid-rewrite.
#    Set repo variable UPDATES_PAUSED=true and let in-flight runs finish.

# 2. Fresh clone + tool.
brew install git-filter-repo            # or: pip install git-filter-repo
git clone https://github.com/toouur/foursquare-dashboard.git fsq-rewrite
cd fsq-rewrite

# 3. Purge generated blobs from every commit (static pages are NOT listed → kept).
git filter-repo --invert-paths \
  --path index.html --path trips.html --path feed.html --path companions.html \
  --path world_cities.html --path venues.html --path tips.html --path photos.html \
  --path stats.html --path search.html --path guide.html --path ratings.html \
  --path lists.html --path flights.html --path shouts.html --path years.html \
  --path-glob 'year-*.html' --path-glob 'trip-*.html' \
  --path trips_meta.json --path feed_meta.json --path venues_filter.json \
  --path city_review.csv

# 4. Force-push the rewritten history.
git remote add origin https://github.com/toouur/foursquare-dashboard.git
git push --force --all  origin
git push --force --tags origin

# 5. Resume CI: set UPDATES_PAUSED=false (or delete the variable).
```

Expect `.git` to drop from ~4.2 GB to tens of MB. Re-clone locally afterward
(`rm -rf` the old clone, fresh `git clone`) so your working copy matches.

## Rollback

- **Bad Pages deploy:** Cloudflare dashboard → Pages → `4sq` → Deployments →
  roll back to a previous deployment.
- **Need git-based deploys back:** re-enable git auto-deploy and re-add
  `pages_build_output_dir = "."` to `wrangler.toml`; but HTML is no longer
  committed, so you'd also have to revert the gitignore/untracking commit
  (`a20fc7e3`) for that to serve anything.
