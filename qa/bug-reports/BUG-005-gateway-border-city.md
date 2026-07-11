# BUG-005 — Border-crossing check-ins carry the wrong country: city from one side, country from the other

| | |
|---|---|
| **Severity** | Minor–Major (data quality: corrupts country/city stats and trip transitions) |
| **Priority** | Medium |
| **Status** | Fixed via per-venue overrides + documented decision rule |
| **Component** | Upstream data (Foursquare venue metadata) → `config/venue_fixes.json` |
| **Environment** | Production data; any border crossing / gateway venue |
| **Found via** | Exploratory review of trip pages: a Poland→Belarus trip showed an impossible country sequence |

## Description

Foursquare tags "gateway" venues (border crossings, sometimes airports) with mixed-side
metadata. Real example on the Terespol↔Brest crossing (river Bug): the venue
*Belarus-Poland Border Crossing* returned `city = Terespol` (a **Polish** town) with
`country = Belarus`. Every check-in there put a Belarusian check-in inside a Polish city,
producing phantom city/country combinations and garbled trip transition sequences
(PL → BY → PL → BY within one hour of driving in a straight line).

## Steps to Reproduce

1. Check in at a border-crossing venue whose Foursquare metadata mixes sides
   (e.g. `city=Terespol`, `country=Belarus`).
2. Run the build; inspect the check-in's resolved city/country and the trip's country
   sequence on the trip page.

## Expected

Each gateway venue is attributed to exactly one consistent (city, country) pair, and a
land crossing renders as one clean A→B transition.

## Actual

City from one country, country from the other; trip pages show impossible country
ping-pong around every crossing.

## Root cause

Upstream metadata quality. Gateways sit *on* the line; Foursquare's geocoding picks city
and country independently and can pick them from different sides.

## Fix — including the tempting wrong fix that was rejected

Rejected first idea: attribute a crossing to "the side you are entering". Analysis showed
it cannot work: an override is per-**venue** (one venue_id → one fixed city/country applied
to every check-in, both directions), so it cannot know travel direction — and
direction-tagging would double-count (entering Belarus, both the crossing *and* the first
real Belarusian stop would appear as Brest) and be wrong on every return trip.

Adopted rule (documented in CLAUDE.md so future gateways get the same treatment):

1. **Assign each gateway venue to the physical side it sits on**, decided by its
   coordinates; the trip's own sequence of distinct stops then shows the direction.
2. On-the-line ties are pinned to whichever side makes the transition read as a single
   clean A↔B step in *both* directions without duplicating a real city.
3. Implemented as **one `venue_fixes.json` entry per venue** (highest-priority override,
   applies to all past and future check-ins) — never per-timestamp fixes per trip.
4. Related: blank-city motorway venues *between* gateways would snap to the nearest big
   city (≤90 km centroid match); those get per-timestamp pins to the nearest real town
   instead, because a road-spanning venue has no single correct city.

## Regression coverage

- `tests/test_transform.py` pins that `venue_fixes.json` beats every lower normalization
  stage (the mechanism the fix rides on).
- `scripts/check_city_config.py` (CI gate) validates every `venue_fixes.json` entry:
  24-char hex venue_id, non-empty city/country.
- `scripts/check_city_count.py` catches a new phantom city if an unhandled gateway appears.

## Lessons

Not every bug is in your code — but the *policy* for handling bad upstream data must be
yours, written down, and mechanically validated, or each new occurrence gets a fresh
inconsistent hand-fix. The rejected-fix analysis is kept in the docs deliberately: the
"attribute by direction" idea is attractive enough that it would be re-proposed.
