# Data-model invariants

Home eras, trip-name healing, city normalization and late companion tags. Moved out of CLAUDE.md, which keeps the rules in brief.

## Home is a TIMELINE, not a constant

The dataset spans 20 years and home moved. `config/settings.yaml` →
`trip_detection.home_history` declares the eras; `trip_detection.home_city` names the
current/final one:

```yaml
trip_detection:
  home_city: Chișinău          # current era (from the last `until` onward)
  home_history:
    - city: Chișinău
      until: "2012-09-01"      # exclusive, UTC midnight
      venues: [50fbc23ae4b0afd6fe382f57]   # Str. Alecu Russo, 55
    - city: Minsk
      until: "2026-07-29"
```

Each entry names the home for every ts strictly BEFORE its `until`; at/after the last
`until`, `home_city` applies. Runtime shape:
`[(1346457600, 'Chișinău'), (1785283200, 'Minsk')]`.

- **Anything that measures distance from / membership in "home" MUST be era-aware.**
  Resolvers: `metrics.trips._home_at(ts, home_city, home_history)` (reference impl) and
  `metrics.stats.home_at_ts(ts)`, a closure built once over the era centroids. Never read
  `home_city` alone. A single flat centroid scored ~14 years of ordinary Minsk life as a
  permanent 773 km journey (the real Chișinău–Minsk centroid distance) and marked those
  stay-at-home days as nomad days — `nomad_days` read 4125 / 84.6 % instead of the correct
  1694 / 34.7 %. Fixed across three call sites (trip furthest-point KPI, the daily
  `distance_from_home` series feeding `nomad_kpis`, the per-year "farthest reach"
  narrative); pinned by `tests/test_home_eras.py`, whose third test asserts a deliberately
  flat config MISREADS the other era so the bug cannot quietly return.
- **`home_venue_ids` is a UNION across ALL eras**, not per-era. Venue
  `50fbc23ae4b0afd6fe382f57` is load-bearing twice in `trips.py`: as the homecoming target
  of the home-arrival extension, and in the `ends_at_home` guard.
- **Adding an era**: append to `home_history` AND move `home_city` to the new current city;
  the previous final era needs its own `until` row or its years get scored against the
  wrong city.
- `gen_guide.py` resolves home at the latest check-in's moment via `_home_at` (it used to
  read the flat `home_city`); pinned by `tests/test_guide_home_era.py`.

## Trip names self-heal (`config/trip_names.json`)

A trip's name is keyed by its start ts (`_name_ts`), so ANY rule that moves a start
(new departure hub, taxi before the airport, a platform counted as a station) orphans
the key: the hand-written name silently falls back to an auto-name. `scripts/metrics/
trip_names.py:heal_trip_names` runs inside `detect_trips` (before naming) and decides
per orphan key:
- **MIGRATED** — the old start is still a check-in of the trip, or sits ≤24 h before
  its new start, and no other key competes: the name moves to the new key.
- **REMOVED** — no trip at that time any more, the covering trip is already named, or
  a competing key explained the shift better ("superseded").
- **SUSPICIOUS** — shift >24 h, two named trips now one (merge), or an unexplained
  shift: the key is KEPT untouched and reported. Never guess a name onto a trip.
The same build renders the healed names. `build.py --trip-names-healed-out PATH` also
writes the heal-only file (no auto-added names) and emits `TRIP_NAMES_HEALED` /
`TRIP_NAMES_SUSPICIOUS`; `update-dashboard.yml` commits it ("Commit healed trip
names") and raises a `::warning::` on suspicious keys. Tests:
`tests/test_trip_names_heal.py`.
NOT healed: `trip_exclude.json` / `trip_tags.json` / `trip_start|end_overrides.json` are
keyed by start ts too. To audit an exclude key, build once without it and see whether a
trip appears (the dead `1767065128` — no effect either way — was removed 2026-10-13).
**Contained trips are dropped automatically** (`trips._drop_contained_trips`): a home-city
check-in mid-trip (a forest on the way back geotagged "Minsk") splits the raw sequence,
the first part's arrival extension swallows the second, and the second used to survive
as its own trip — the same check-ins shown twice. That was 21.10.2018 "Zaslawye,
Belarus" inside the Veloby bike trip, hidden by hand with exclude key `1540131430`,
removed 2026-10-13 as redundant (build identical with and without it).
Auto-name icon (`build._infer_icon`): ✈️ only for a REAL flight
(`metrics.trip_insights.trip_used_plane` — a "Plane" check-in, or an airport visit
spanning 100+ km; same city on both sides = seeing someone off, never ✈️), otherwise
the first/last-5 hub probe.

## City normalization pipeline (priority order)

0. `venue_fixes.json` — per-venue_id `{city, country}` override (HIGHEST priority).
   One entry pins a venue's city/country for **all past + future** check-ins.
1. `country_fixes.json` — per-ts country override.
2. `city_fixes.json` — per-ts city override.
3. `city_merge.yaml` — raw→canonical string map for non-blank rows.
4. `fill_city_inferred.py` — centroid Haversine match for blank rows; sets `city_inferred=1` in D1.

Note: `city_fixes.json` is applied **only by unix-ts** in `transform.py`. The
~1.6k 24-char-hex keys in it are `checkin_id`s used **only** by
`check_city_drift.py` to suppress already-reviewed rows — they are NOT applied as
overrides at build. To override a single venue use `venue_fixes.json` (by
venue_id); to override a single check-in use a ts key in `city_fixes.json`.
`check_city_drift.py` is a **manual diagnostic — no workflow invokes it**; the CI
gate is `check_city_count.py` (see below).

`config/city_canonical.yaml` is the single source of truth for blank-row recovery:
`canonical_map` (raw→canonical), `valid_canonical` (whitelist), `large_canonical`
(km-bucket override), `thresholds`, `skip_set`, `skip_patterns`.
`scripts/check_city_config.py` is a CI gate verifying canonical_map values and
thresholds keys all exist in `valid_canonical`, city_fixes.json keys are
numeric ts or 24-char hex ids, venue_fixes.json keys are 24-char hex with a
non-empty city/country, and companion_fixes.json keys are 24-char hex check-in
ids setting at least one companion column (see "Late companion tags" below).

### City NFC normalization + count-drift gate

- `transform.py` NFC-normalizes every `city` value (and writes it back to the row)
  **before** any string-keyed rule runs. Foursquare sometimes returns diacritic
  city names in NFD (decomposed base char + combining mark, e.g. "Sóc Sơn" as
  `o`+U+0323); NFD strings byte-mismatch the NFC keys in `city_merge.yaml`, so
  without this they bypass EVERY rule and resurface as phantom single-count cities.
  CJK has no NFC/NFD variance — a missing CJK mapping is a plain `city_merge` gap
  (e.g. traditional `北京市海淀區` needed its own entry alongside simplified `区`).
- `scripts/check_city_count.py` runs the real transform pipeline and compares the
  distinct normalized {city:count} set to `config/city_count_baseline.json`.
  **Pass `--backfill` — the site's city list is checkins.csv AND backfill.csv.**
  `build.py` merges the reconstructed rows before `apply_transforms`, so they reach the
  display through the same normalisation; checking checkins.csv alone under-reports the
  count and cannot see a duplicate that lives only in the backfill (`Затока` sitting next
  to `Zatoka`). Both CI callers pass it, and the baseline counts the merged set.
  HARD-fails (exit 1) on invariant bugs — a displayed city that is non-NFC or
  a fold-collision (two spellings/encodings of one place) — and on a NEW displayed
  name that is not a settlement at all: an administrative unit, a pair of places or
  a station label (`Antwerp Province`, `РФ / РБ`, `Sejny - Lazdijai`,
  `stancyja Hudahaj`). That shape check reuses `city_canonical.yaml: skip_patterns`
  so there is one source of truth, and it exempts names that are TARGETS of a
  city_merge mapping — the house style deliberately keeps `Smolensk Region` when a
  check-in genuinely happened in the region, and real names can carry an odd shape
  (`Biel/Bienne`). A non-settlement already present in the baseline reports as debt
  without blocking, so the gate guards against regressions rather than failing on
  history. **This check exists because `city_fixes.json` bypasses `city_merge`**:
  `transform.py` sets the city from it and returns early, so a raw value written
  there reaches the display verbatim and no other rule sees it. SOFT findings (exit 0,
  reported): an added city that count-pairs with a removed one (`RENAME?`, likely a
  Foursquare city rename that now needs a mapping) or a non-ASCII non-canonical
  addition (`REVIEW`), or a new name whose check-ins land within 5 km of an established
  city holding 20x more of them (`DISTRICT?`). That last one is pure geography and
  exists because a district reads as an ordinary city name: `Buiucani` is a sector of
  Chișinău and `Tirane` is Tirana without the final vowel — neither trips a
  fold-collision or a shape rule, and the plain `new` verdict printed nothing at all.
  It stays soft on purpose: a sweep over the whole set gave 10 hits, 8 of them real
  neighbouring settlements (Varnița by Bender, Geldrop by Eindhoven, Maltese Pietà and
  Swieqi). `--strict` makes SOFT block too; `--warn-only` never blocks.
  The fold key strips case, apostrophe variants **and diacritics**, so an ASCII
  spelling collides with its accented twin (`Dusseldorf` vs `Düsseldorf`) — without
  that they passed as two separate cities.
  `--auto-merge` is the **self-heal**: each fold-collision is appended to
  `config/city_merge.yaml` (`variant: keeper`, keeper = canonical/highest-count,
  ties toward the accented form) instead of failing the run, the counts are
  recomputed, and `AUTO_MERGED=<n>` is emitted so CI commits the file. Only
  provably-same-place spellings qualify, so it can never merge two real cities.
  The heal lands *after* the build, so a merged spelling first shows up in the
  next hourly rebuild. Both `update-dashboard.yml` (step 7a-bis/7a-ter) and
  `recheck-enrich.yml` (7b/7b-bis) run it this way, after HTML validate, before D1
  sync. **Refresh the baseline after an intentional city-set change** and commit it:
  `python scripts/check_city_count.py --csv private-data/checkins.csv --baseline
  config/city_count_baseline.json --update-baseline`.

### Gateway check-in rule (border crossings, airports)

Border/airport check-ins are "gateways" — Foursquare often tags them with a city
or country from the *wrong* side (e.g. "Belarus-Poland Border Crossing" comes back
`city=Terespol` (PL) but `country=Belarus`). Standard rule:

- **Assign each gateway venue to the PHYSICAL side it sits on** (decide by its
  coordinates), then let the trip's own sequence of distinct posts show the
  direction. Do **not** try to attribute a crossing to "the side you enter": a
  per-venue fix is one venue_id → one fixed city/country applied to *every*
  check-in both ways, so it cannot know travel direction. Tagging by direction
  would also double up (entering Belarus you'd get the crossing *and* the first
  Belarusian post both as Brest) and would be wrong on the return trip.
- Example (Terespol↔Brest on the Bug, three venues west→east): `Terespol Border
  Crossing` (lng 23.644) → Terespol, Poland; `Belarus-Poland Border Crossing`
  (lng 23.653, mid-river/on the line) → Terespol, Poland (tie on the line, pinned
  to PL so PL↔BY reads as one clean transition in both directions and never
  doubles the real Brest post); `Пункт пропуска «Брест»` (lng 23.660) → Brest,
  Belarus.
- Implement each gateway as a **single `venue_fixes.json` entry** (by venue_id), so
  every historical and future check-in at that venue is consistent automatically —
  do **not** add per-ts entries per trip.
- Transit points *between* gateways (motorway/rest-area venues with blank city)
  would otherwise snap to the nearest big-city centroid (≤90 km in
  `transform.build_blank_city_resolver`). Pin them to the nearest real town with a
  per-ts `city_fixes.json` entry (these venues span a whole road, so a venue_id
  rule would be wrong).

### Blank-city recovery loop
```bash
python scripts/analyze_blanks.py > C:/tmp/blanks_output.txt
python scripts/extract_blank_fixes.py > C:/tmp/blank_fixes.txt
python scripts/apply_blank_fixes.py
python scripts/check_city_config.py
```
To accept a new raw nearest-city name, add it to `canonical_map` in
`city_canonical.yaml` — do not edit `extract_blank_fixes.py`.

## Late companion tags (`config/companion_fixes.json`)

Swarm lets a friend be tagged onto a check-in **after** we already snapshotted it,
and **no fetch path ever re-reads `with`/`shout` for a row we already hold**:

- the hourly `fetch_checkins.py` is append-only (`added = [r for r in new_rows if
  row_key(r) not in existing_keys]`) — existing rows are never refreshed;
- `--recheck-recent-hours` only runs `enrich_overlaps`, which reads the `overlaps`
  field. **A `with` tag is a different field from an `overlaps` record** — a friend
  who was *tagged* but did not check in separately produces no overlap at all, so
  no recheck window (48 h or 4 weeks) can ever recover them;
- the monthly `archive-checkins.yml` `--full` re-fetch **replaces** each existing
  row with the API version (`fetch_checkins.py:511`), carrying over only
  `overlaps_*`. So hand-editing `checkins.csv` is silently undone next month.

`config/companion_fixes.json` is the durable fix — a per-`checkin_id` override
applied in `transform.apply_transforms()`, i.e. at the chokepoint both `build.py`
and `sync_to_d1.py` run rows through. `checkins.csv` stays a faithful API mirror
with nothing to preserve across a re-fetch.

```json
{ "6a78cbfb0ed66f3afd084001": { "with_name": "Ghennadi Rips", "with_id": "1416805649" } }
```

- Recognised fields: `with_name`, `with_id`, `overlaps_name`, `overlaps_id`
  (`transform.COMPANION_FIX_FIELDS`). Comma-separate for several people, keeping
  name and id order aligned. Only **non-empty** values override, so a partial entry
  can't blank a real value. Keys starting with `_` are ignored (comments).
- Applied **before** the `venue_fixes` branch in the row loop — that branch
  `continue`s once it sets a city, which would otherwise skip the companion fix on
  any venue carrying a city override.
- **D1 needs its own reconcile.** The check-in sync is append-only
  (`INSERT OR IGNORE`), so a row already in D1 keeps whatever it was first inserted
  with and `/api/feed` would never show the companion. `sync_to_d1.py` therefore
  runs an **always-on, un-windowed** companion-fixes pass (unlike the 48 h
  `--fix-overlaps-hours`): one `SELECT` over the handful of ids + targeted
  `UPDATE`s. Cheap, idempotent, self-healing — it also repairs a row reset by a
  force resync or the wrangler bulk-dump path. It deliberately does **not** set
  `changed` (checkins carry no FTS index).
- `check_city_config.py` validates the file (24-char hex keys, at least one known
  field, trimmed non-empty strings, matching `with_name`/`with_id` counts) — a
  typo'd key would otherwise silently match nothing.
- Coverage: `tests/test_transform.py::TestCompanionFixes` (incl. a test that the
  shipped config itself loads and validates).
