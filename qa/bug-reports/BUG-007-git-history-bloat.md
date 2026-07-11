# BUG-007 — Hourly commits of generated HTML bloat the repository to 4–7 GB

| | |
|---|---|
| **Severity** | Critical (repo effectively unusable: multi-GB clones, slow pushes; fix required destructive history rewrite) |
| **Priority** | High |
| **Status** | Fixed (June 2026 architecture cutover) — every pre-cutover commit SHA is now invalid |
| **Component** | Deploy architecture: `.github/workflows/update-dashboard.yml`, `.gitignore`, Cloudflare Pages config |
| **Environment** | GitHub repository + Cloudflare Pages git auto-deploy |
| **Found via** | Operational pain: clone/fetch times and `.git` size trending toward 7 GB |

## Description

The original deploy model was "commit the generated site, let Cloudflare Pages
auto-deploy from git". The hourly update job therefore committed every regenerated
`index.html`, `trips.html`, `year-*.html`, `trip-*.html`, `feed_meta.json`, etc. — dozens
of files, some megabytes each, **24 times a day**. Git stores snapshots; even with delta
compression, hourly churn of large generated files grew `.git` monotonically to **4–7 GB**
within months. Every clone, fetch, and CI checkout paid the cost, and the commit log
became ~99 % machine noise.

This is a process/architecture defect rather than a code defect — but it is the most
expensive bug the project has had, because the only real fix was destructive.

## Steps to Reproduce (pre-fix)

1. Let the hourly job run for a few months with generated HTML tracked in git.
2. `git count-objects -vH` / clone the repo.
3. Observe multi-GB `.git` for a project whose source is a few MB.

## Expected

Repository size proportional to *source* (scripts, templates, config); build artifacts
live outside version control, like any compiled output.

## Actual

`.git` grew to 4–7 GB; history unusable for review; shallow clones required in CI as a
band-aid (`fetch-depth: 1`).

## Root cause

Build artifacts were treated as content because the deploy mechanism (Pages git
auto-deploy) *required* them in git. The deploy convenience decision silently implied
"unbounded binary-ish churn in history" and nobody priced that in at the start.

## Fix

Full cutover (June 2026):

- Generated HTML/JSON **gitignored** — never committed again. Local builds write to
  `_site/` (also ignored).
- CI now assembles a clean `_site/` and deploys via **direct upload**:
  `npx wrangler pages deploy _site --project-name 4sq`. Pages git auto-deploy is
  **permanently disabled** — a git-triggered build of the now-HTML-less tip would publish
  a broken site (this is a standing operational constraint, documented in `ops/deploy.md`).
- History was rewritten with `git-filter-repo` + force-push of `main`. **All pre-cutover
  SHAs became invalid**; every stale clone had to `git fetch && git reset --hard origin/main`.
- Static pages that stay tracked: `solution.html`, `sitemap.xml`, `robots.txt`, `favicon.svg`.

## Regression coverage

Structural: the `.gitignore` entries make regression impossible via the normal path, and
the deploy workflow never runs `git add` on build output. The HTML deploy gate
(`validate_html.py`) runs against `_site/` before upload, replacing the "it's in git so I
can see it" review comfort that committing HTML used to provide.

## Lessons

Deploy convenience can smuggle in an unbounded storage liability — "just commit the
build" is a decision about *history growth rate*, not just about deployment. Catching it
late turned a `.gitignore` line into a destructive history rewrite that invalidated every
SHA in issues, docs, and memory. Price artifact churn on day one.
