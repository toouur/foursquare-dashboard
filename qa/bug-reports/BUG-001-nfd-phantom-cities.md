# BUG-001 — NFD-encoded city names bypass every normalization rule and surface as phantom cities

| | |
|---|---|
| **Severity** | Major (silent data corruption, user-visible) |
| **Priority** | High |
| **Status** | Fixed + regression-tested + gated |
| **Component** | `scripts/transform.py` (city normalization pipeline) |
| **Environment** | Production data pipeline; data from Foursquare API v2 |
| **Found via** | Exploratory review of the city list after a Vietnam trip; count drift vs. expectations |

## Description

Foursquare sometimes returns diacritic city names in Unicode **NFD** (decomposed: base
character + combining mark) instead of **NFC** (precomposed). Example: *Sóc Sơn* arrived
as `o` + `U+0323` (combining dot below) rather than the precomposed `ợ` sequence used in
our config.

All string-keyed normalization rules (`city_merge.yaml`, canonical whitelist, skip sets)
store NFC keys. An NFD string is byte-different, so it **matches no rule at all** and
flows through the whole 5-stage pipeline untouched — appearing on the dashboard as a new,
visually identical city with a count of 1–2, while the "real" NFC city keeps the rest of
the count.

## Steps to Reproduce (pre-fix)

1. Take a check-in row whose `city` is NFD-encoded, e.g. in Python:
   `city = unicodedata.normalize("NFD", "Sóc Sơn")`.
2. Add an NFC-keyed mapping for that city to `config/city_merge.yaml`.
3. Run the transform pipeline (`scripts/build.py` or unit-level `transform` functions).
4. Inspect the distinct city set of the output.

## Expected

The NFD row is normalized by the `city_merge.yaml` rule like any other spelling variant;
one city, one combined count.

## Actual

Two "cities" that render identically: the NFC one (mapped) and an NFD phantom (unmapped,
count 1–2). Every downstream consumer — city stats, year pages, D1 rows — splits the count.

## Root cause

String-keyed lookups assumed a canonical Unicode form that the upstream API does not
guarantee. No normalization boundary existed between "data enters the pipeline" and
"string-keyed rules run".

## Fix

`transform.py` now NFC-normalizes **every** `city` value (and writes it back to the row)
*before* any string-keyed rule executes — one normalization boundary instead of fixing
individual rules.

## Regression coverage

- Unit tests in `tests/test_transform.py` feed NFD input through the pipeline and assert
  NFC output and rule matching.
- **Systemic gate:** `scripts/check_city_count.py` runs the real pipeline in hourly CI and
  hard-fails if any displayed city is non-NFC or if two spellings/encodings of one place
  survive to the output (fold-collision check), compared against a committed baseline.

## Lessons

A one-off fix would have recurred with the next diacritic city. The durable artifacts are
the normalization *boundary* and the CI *invariant gate*, not the patch. Note the CJK
counter-example documented in CLAUDE.md: CJK has no NFC/NFD variance, so a missed CJK city
is a plain mapping gap — the gate distinguishes the two cases.
