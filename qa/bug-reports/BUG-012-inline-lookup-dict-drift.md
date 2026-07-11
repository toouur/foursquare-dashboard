# BUG-012 — Copy-pasted lookup dicts drift apart: shouts page had icons for 40 of 559 categories

| | |
|---|---|
| **Severity** | Minor per symptom (missing icons/flags), Major as a maintenance defect — 10+ divergent copies of the same data guaranteed inconsistency |
| **Priority** | Medium |
| **Status** | Fixed (single-source config extraction) |
| **Component** | `templates/*.tmpl` (inline `CAT_ICON` / `CTRY_CODE`/ISO2 dicts), `scripts/build.py` post-process pass |
| **Environment** | All generated pages that render category icons or country flags |
| **Found via** | Visual pass on the shouts page — the vast majority of cards showed the default 📍 pin |

## Description

Category→emoji icons and country→ISO-3166 flag codes started life as JS object literals
pasted inline into whichever template needed them. Every new page copied the dict from an
older page — and from that moment the copies evolved independently:

- The **shouts** page's `CAT_ICON` copy had **40 entries while the dataset had 559
  categories** — ~93 % of cards fell through to the default pin.
- The country-code dict existed in **9 templates plus `gen_photos.py`**, with copies
  diverging by dozens of entries (112 entries in the consolidated set); which countries
  showed a flag depended on which page you were looking at.

No single copy was "the bug" — the architecture (N copies, zero synchronization) was.

## Steps to Reproduce (pre-fix)

1. Build the site; open `/shouts`.
2. Observe near-universal default pins where category icons should be.
3. Compare `CAT_ICON` in the shouts template vs the index template: two different dicts.
4. Same exercise with `CTRY_CODE` across any two of the 9 templates.

## Expected

One canonical category→icon map and one country→flag map, rendered identically on every
page; adding an entry is a one-line change that takes effect everywhere.

## Actual

Each page had a private, stale snapshot; coverage varied page-to-page; fixing an icon
meant finding and editing up to 10 files (and, in practice, missing some).

## Root cause

Copy-paste reuse of *data* between templates. Unlike copy-pasted code, copy-pasted data
gives no signal when it drifts — pages render "successfully" with whatever subset they
have, so the divergence only grows.

## Fix

Both lookups were extracted to single-source config files —
`config/category_icons.json` (category → `[emoji, color]`) and
`config/country_flags.json` (country → ISO alpha-2) — injected by a **post-process pass
in `build.py`**: after every generator runs, every output HTML gets `{{CAT_ICON_JSON}}` /
`{{CTRY_CODE_JSON}}` substituted centrally. Generators don't thread the data through
their kwargs; templates just carry the placeholder. Same treatment was applied to the
country-alias map (`config/country_aliases.json`, formerly `CTRY_NORM` inline in
`gen_tips.py`). Additions are now one-line config edits.

## Regression coverage

`scripts/validate_html.py` (pre-deploy gate) fails on any leftover `{{PLACEHOLDER}}`, so
a page that misses the post-process pass can't deploy. The E2E smoke suite loads the
shouts page among its zero-pageerror checks.

## Lessons

Duplicated *data* is worse than duplicated code: it fails silently, per-copy, with
degraded output instead of errors. The moment a literal is pasted into a second file, it
wants to be a config file — and a centralized post-process injection step is cheaper than
threading shared data through every generator's signature.
