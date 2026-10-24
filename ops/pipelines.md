# Data pipelines and bots

Detailed notes for the fetchers, Telegram bots, pre-2012 backfill, travel atlas and photo tools. Moved out of CLAUDE.md to keep it short; CLAUDE.md keeps the one-line summaries and commands.

## Fetch flights (FlightRadar24 diary)
No FR24 API. `fetch_flights.py` logs in with `FR24_LOGIN=email:password` (plain
JSON login, no CAPTCHA), does the `my.flightradar24.com` SSO handshake, and
downloads the diary CSV from `/public-scripts/export`. Login mints a fresh
session each run — nothing to expire. `FR24_COOKIE` (full `Cookie:` header) is a
fallback used only when `FR24_LOGIN` is unset. Exit 0 valid / 2 auth-invalid /
1 transient; prints `COOKIE_VALID=` / `CHANGED=`. Runs weekly in CI
(`fr24-flights.yml`, Sun 05:00 UTC) and commits `flights.csv` to the data repo.
```bash
export FR24_LOGIN='email@example.com:password'
python scripts/fetch_flights.py --out C:/Users/toouur/Documents/GitHub/foursquare-data/flights.csv
python scripts/fetch_flights.py --check   # probe auth, no write
```

## Fetch Last.fm scrobbles (per-year "year in sound")
No CSV export — pulls `user.getRecentTracks` with `LASTFM_API_KEY`. Two-file model:
`lastfm_state.json` (full per-year artist→plays counters + month histogram + `last_ts`
watermark, ~700KB incremental cache) and `lastfm_years.json` (compact derived per-year
`{scrobbles, top_artist:{name,plays}, months:[12]}`, ~3KB, the build input). Each run
fetches only scrobbles after `last_ts` and folds them in. Exit 0 ok / 2 key-invalid /
1 transient; prints `KEY_VALID=` / `CHANGED=`. Runs weekly in CI (`lastfm.yml`, Sun
06:00 UTC, honors `UPDATES_PAUSED`, 5th data-repo writer with the BUG-014 rebase loop)
and commits both files to the data repo. `build.py` auto-discovers `lastfm_years.json`
next to `checkins.csv` and threads it to `gen_year_pages.build_page(music_by_year=...)`.

**Raw layer** (`--scrobbles-dir lastfm/`, for the cultural chronicle): every scrobble as
a TSV line `ts artist track album` (UTC seconds) in `lastfm/scrobbles_<YYYY>.tsv`, sorted,
de-duplicated on `(ts, artist, track)`; only `\t \r \n` inside a field become spaces —
names are otherwise byte-exact, files are written with `\n` even on Windows. Own
watermark (newest ts on disk): an empty dir means a one-off full-history walk
(~1400 pages, 15-25 min; the workflow has `timeout-minutes: 90`), and each layer folds
only scrobbles past its own watermark, so the year state is never double-counted.
`to` is pinned at walk start — without it a scrobble arriving mid-walk shifts every
later page by a row. Integrity signals: `FETCH_TOTAL_MISMATCH` (rows vs the API's
`total`) and `RAW_YEAR_DIFF` (raw per-year count vs the year state), both in the run
summary. The year state predates the raw layer and may itself be off (it was seeded
from an export), so a diff is a prompt to look, not proof the raw layer is wrong.
```bash
export LASTFM_API_KEY=...
# One-off bootstrap from a full local export (no key needed):
python scripts/fetch_lastfm.py --bootstrap path/to/all_scrobbles_api.json \
  --out   C:/Users/toouur/Documents/GitHub/foursquare-data/lastfm_years.json \
  --state C:/Users/toouur/Documents/GitHub/foursquare-data/lastfm_state.json
# Weekly incremental:
python scripts/fetch_lastfm.py --user TOOUUR \
  --out   C:/Users/toouur/Documents/GitHub/foursquare-data/lastfm_years.json \
  --state C:/Users/toouur/Documents/GitHub/foursquare-data/lastfm_state.json
python scripts/fetch_lastfm.py --user TOOUUR --check   # probe key, no write
```

## Telegram "on this day" story (daily)
`telegram_on_this_day.py` posts every earlier year that has check-ins on today's date
(local date per check-in; "today" in `Europe/Chisinau`). Each year is headed 🏠 home or
✈️ travel (era-aware `_home_at`) with its weather from the Open-Meteo archive (no key),
then lists EVERY check-in with its local time; companions and shout sit inline on the
check-in they belong to. Venue names are shown exactly as checked in, "Home
(private)" venues included. On top sits a narrative: Claude
writes it via the API when `ANTHROPIC_API_KEY` (repo secret) is set (model
`ANTHROPIC_MODEL`, default `claude-sonnet-5-5`); else via the Claude Code CLI
(`claude -p`, prompt over stdin) when `CLAUDE_CODE_OAUTH_TOKEN` is set — a Pro/Max
subscription token from `claude setup-token`, no API billing; the workflow installs
`@anthropic-ai/claude-code` only when that secret exists. Otherwise a template does. Text splits into several
messages under Telegram's 4096-char limit (a long year continues with a "(cont.)"
head); then one photo album per year (≤10, `sendMediaGroup`) from photos.json.
Weather, Claude and photos fail soft; only a failed `sendMessage` exits 1. Runs daily
in CI (`telegram-on-this-day.yml`, 06:00 UTC, honours `UPDATES_PAUSED`),
using the existing `TELEGRAM_BOT_TOKEN`; it posts to `TELEGRAM_OTD_CHAT_ID` when set (e.g. a
channel with the bot as admin), else to the alerts chat `TELEGRAM_CHAT_ID`.
**Stories are drafts kept forever** in `otd_stories.json` in the data repo
(`--stories`; `{"YYYY-MM-DD": {story, source, created}}`, never pruned): after a
successful send a Claude story is stored under its date, and any rerun for that date
reuses the stored text instead of writing a new one — edit an entry's `story` by hand to
change what a rerun posts. `--regenerate` (workflow input `regenerate`) writes and stores
a fresh one. Template stories, dry runs and failed sends are never stored. The check-in
list, weather and photos are always rebuilt. The workflow commits the file, which makes it
the 6th data-repo writer (BUG-014 rebase loop).
Tests: `tests/test_telegram_on_this_day.py`.
```bash
python scripts/telegram_on_this_day.py --csv C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json \
  --pix-url "https://pub-5514667a5da04a75986022e39efc7118.r2.dev/pix" --dry-run [--date 2026-10-18]
```

## Telegram check-in echo (instant + morning digest)
`telegram_checkin_echo.py` tells EVERY new real check-in's story against all history before
it (checkins.csv + backfill.csv, same transforms): a repeat gets `visit` (visit number, first
visit, `recent_visits`, days since last, who you were here with before), a new place gets
`new_venue` (its rank in the category, the previous place of that kind, same-name chain
count). There is **no threshold**: labels — `new_country`, `new_city`, `city_back` (≥1 year
away from the city), `comeback`/`comeback_long` (≥1/≥5 years since the venue),
`milestone`/`milestone_big` (10/25 · 50/100/250/500/1000th visit), `total` (every 1000th) —
only colour the text and decide what the digest lists. Home (private) and reconstructed rows
are never told; `PASS_THROUGH` categories (Road, Bus Line, Taxi, …) never get the `_long`/
`_big` labels and sort last in the digest. **Trigger:** `update-dashboard.yml` step 6b
dispatches `telegram-checkin-echo.yml -f mode=instant` right after it commits new
check-ins (`continue-on-error`, needs `actions: write`), so a story lands about a minute after
the poller sees the check-in; the echo workflow has its own `concurrency` group, so two
polls never double-post. Instant posts ≤10 per run, only for check-ins newer than the
watermark and ≤36 h old. `--mode digest` (05:45 UTC, YESTERDAY's local date) lists that
day's labelled check-ins (plain repeat visits excluded), ≤15 lines strongest-first, the rest
summarised. Claude writes the text (API, else CLI), else a template. State
`checkin_echo_state.json` in the data repo (`last_ts` watermark, told ids, sent digest
dates): the first instant run without it only records the watermark — enabling the bot never
replays history — and the watermark moves only past messages that actually went out. Posts
to `TELEGRAM_OTD_CHAT_ID` (else `TELEGRAM_CHAT_ID`). 7th data-repo writer (BUG-014 rebase
loop). Manual dispatch defaults to `mode=digest`, `dry_run`. Tests:
`tests/test_telegram_checkin_echo.py`.
```bash
python scripts/telegram_checkin_echo.py --mode digest --csv C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --state C:/Users/toouur/Documents/GitHub/foursquare-data/checkin_echo_state.json --date 2026-10-18 --dry-run --no-llm
```

## Build pre-2012 travel backfill ("refurbished" check-ins)
The Foursquare dataset starts in 2012; earlier travel (~2008+) is reconstructed at
country/city/day granularity from a hand-editable `backfill.yaml` (next to
`checkins.csv` in the data repo). `build_backfill.py` converts it to `backfill.csv`
in the exact 23-column check-in schema; `build.py` auto-discovers that sibling CSV and
merges its rows into `rows` (re-sorted by `date`), so every stat/KPI/map/trip/feed
shifts. Reconstructed rows carry `source_app="refurbished"` (the discriminator — real
rows are only Swarm/Foursquare/Pebble) and `checkin_id="rf<seq>"` (e.g. `rf0001`,
never collides with 24-char hex). Coords resolve explicit→existing-city-centroid (from
checkins.csv)→gazetteer→unresolved; coarse dates fill to noon UTC (month→1st,
year→Jul 1). Each reconstructed check-in renders with a `↺ reconstructed` badge (feed,
trips modal, index recent); a fully-reconstructed year page gets a hero-eyebrow badge.
`sync_to_d1.py --backfill backfill.csv` also ingests them so `/api/feed` + `/api/search`
return them (blank venue_id → no venue/FTS row; feed.js emits `source_app` as a 13th
tuple field, `_v=refurb` cache tag). **D1 reconcile is by CONTENT, not id-presence:**
the normal checkin sync is append-only (`INSERT OR IGNORE` — inserts a missing id but
never UPDATEs one already in D1), so a row re-edited behind a reused `rf` id served
stale in `/api/feed` (which reads venue/city/category denormalized off the checkins
row). `sync_to_d1.py` therefore holds `rf*` out of the append-only path and reconciles
them by **delete + reinsert of the whole `rf` range** — idempotent, self-healing, and it
does NOT set `changed` (checkins carry no FTS index). Two D1-budget details:
the reconcile is **hash-gated through the `sync_state` table** (key `backfill`, the same
mechanism tips/ratings/trips/lists use) and only runs when the content hash moved OR D1's
`rf*` count disagrees with the source — an unchanged backfill wrote ~1383 rows × 5
(4 indexes + the row) × 2 (delete + insert) ≈ **13.8k rows written per sync, ~125k/day,
i.e. over the whole free-tier daily write cap by itself**; and the range predicate is
`id >= 'rf' AND id < 'rg'`, **not `LIKE 'rf%'`** — SQLite's case-insensitive LIKE cannot
use the BINARY-collated `idx_checkins_id`, so the LIKE form scanned all 70k rows. Real
check-in ids are 24-char hex, so nothing real can start with `r`.
**Same venue + same day = one check-in:** the day-granularity model treats a
reconstructed day as a *presence*, not a per-post log, so multiple diary posts at one
venue on one day collapse to the **earliest** (mirrors the Polarsteps adapter's
same-day-repeat collapse). E.g. two 2-Aug-2008 posts at Str. Alecu Russo, 55 (20:00 +
22:00) → keep 20:00, drop 22:00. Sources funnel through `backfill.yaml`:
`import_polarsteps.py` (Polarsteps export ZIP → appends entries; PENDING user export),
LiveInternet diary, and manual entry.
```bash
python scripts/build_backfill.py \
  --yaml      C:/Users/toouur/Documents/GitHub/foursquare-data/backfill.yaml \
  --checkins  C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --out       C:/Users/toouur/Documents/GitHub/foursquare-data/backfill.csv
# then a normal build auto-merges the sibling backfill.csv; sync to D1 with:
python scripts/sync_to_d1.py --csv .../checkins.csv --backfill .../backfill.csv [other --* flags]
```

## Travel atlas journal (standalone page, opt-in from build.py)
One self-contained HTML (data + JS + CSS inlined, ~5 MB) that reads like a magazine:
cover, world atlas (`#world-atlas`), a spread per country, a spread per trip, year
chronicle, paged photo archive with lightbox, place explorer, next-trip ideas.
**Its numbers ARE the dashboard's**: `build.py --travel-atlas-out PATH` hands
`gen_travel_atlas.write_page` the same `rows` / `stats` / `trips` objects the site renders
from — headline KPIs, countries, cities, years and trips come straight from
`metrics.process()`, the photo total uses build.py's `total_photos` formula (photos.json
files + tip photos), each country spread repeats `gen_country_pages` formulas, and the
place explorer keys places exactly like `unique_places_count` (venue_id, else coords
rounded to 3 dp — `build_payload` asserts the counts agree). Nothing is filtered: home
check-ins are included, like everywhere on the site. Written after the PWA/placeholder
passes, so the file stays clean; failure only logs a warning. No workflow passes the flag.
Hand-curated bits live in `config/travel_atlas.json` (ISO-numeric ids for the world-atlas
topojson, the 58 "Избранное" check-ins, one checked landmark per country — keys are the
CANONICAL English country names, e.g. `North Macedonia`, or the entry silently never
shows). Tests: `tests/test_travel_atlas.py`.
```bash
python scripts/build.py \
  --input C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --config-dir config --output-dir _site \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json \
  --pix-url "https://pub-5514667a5da04a75986022e39efc7118.r2.dev/pix" \
  --travel-atlas-out _site/travel-atlas.html
```

## Fetch photos from export
```bash
/c/Users/toouur/AppData/Local/Programs/Python/Python314/python.exe scripts/fetch_photos.py \
  --token "$FOURSQUARE_TOKEN" \
  --export path/to/export/photos/ \
  --csv C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json \
  --pix-dir C:/Users/toouur/Documents/GitHub/foursquare-data/pix/
```

## Back-fill photos onto old check-ins
Use to add a photo to a historical check-in (e.g. so the year-page month
timeline shows its own picture instead of borrowing from a neighbour).
```bash
# Single
python scripts/add_photo.py \
  --token "$FOURSQUARE_TOKEN" \
  --checkin-id 5f8a... \
  --photo path/to/image.jpg \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json \
  --pix-dir C:/Users/toouur/Documents/GitHub/foursquare-data/pix/

# Batch (CSV with header: checkin_id,photo_path)
python scripts/add_photo.py --token "$FOURSQUARE_TOKEN" --batch backfill.csv \
  --photos C:/Users/toouur/Documents/GitHub/foursquare-data/photos.json \
  --pix-dir C:/Users/toouur/Documents/GitHub/foursquare-data/pix/
```
