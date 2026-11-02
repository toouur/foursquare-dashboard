# Last.fm artist normalization

**Project:** `toouur/foursquare-dashboard`  
**Data repository:** `toouur/foursquare-data`  
**Last.fm account:** `TOOUUR`  
**Status:** implementation brief; audit and implementation not yet started

## Context

Last.fm exposes artist names as strings. The same artist can therefore appear under multiple spellings, while different artists can have deceptively similar names. This fragments artist counts and distorts lifetime rankings, yearly rankings, and the music-related parts of the culture timeline.

Examples already observed in the listening-history analysis:

- `MONETOCHKA` / `Монеточка`
- `Zемфира` / `Земфира`

These are examples to investigate, not blanket permission to merge every similar-looking name.

## First: audit the current implementation

Before changing code, inspect the actual repository structure and its project instructions. In particular, locate and read the current versions of:

- `CLAUDE.md`, if present;
- pipeline and internal documentation;
- the Last.fm fetch/sync implementation;
- culture data-source and metric builders;
- all consumers of Last.fm artist names;
- existing tests and CI workflows.

Do not assume paths listed in this brief exist. Search the repository and follow its current architecture and conventions.

Map every place where artist names are grouped, counted, sorted, or displayed. Include at least:

- lifetime artist statistics;
- yearly top artists and year-in-sound;
- culture pages and year pages;
- music diary / trip-level “Sound · Taste · Frame” features;
- artist summaries on the life timeline;
- any exports or generated JSON consumed by the frontend.

Write a short audit and proposed design before implementing.

## Design requirements

### 1. Preserve raw history

- Raw Last.fm scrobble files are the source of truth.
- Never rewrite original artist strings in raw TSV/JSON exports.
- Never delete, deduplicate, or collapse individual scrobbles.
- Do not change API fetching, overlap windows, watermarks, late-scrobble handling, or sync semantics as part of this task.
- Normalization must be a deterministic derived layer.
- If no alias configuration exists, behavior must remain equivalent to the current implementation.

### 2. Explicit canonical artist mapping

Introduce a maintainable, version-controlled mapping using project conventions. Each canonical artist should have:

- a stable canonical identifier, if appropriate;
- one canonical display name;
- an explicit list of exact source-name aliases;
- a short rationale or note for non-obvious merges.

Start by auditing the real raw history for candidate aliases. Verify candidates against reliable artist identity evidence where possible. Do not silently add uncertain merges.

Initial candidates to investigate:

- `MONETOCHKA` → `Монеточка`
- `Zемфира` → `Земфира`

### 3. Conservative automatic normalization

Safe automatic normalization may include:

- Unicode NFC normalization;
- trimming leading/trailing whitespace;
- collapsing repeated whitespace;
- case-insensitive matching where it does not erase meaningful distinctions.

Do **not** automatically merge based only on:

- transliteration similarity;
- punctuation removal;
- substring or fuzzy matching;
- `feat.`, `featuring`, `with`, `&`, or `x` parsing;
- similar names or matching tokens.

These transformations can conflate separate artists, collaborations, bands, solo projects, and namesakes. Keep uncertain cases separate and report them for human review.

Treat mixed Cyrillic/Latin strings carefully. A single character may be visually similar but a different Unicode code point.

### 4. One canonical identity layer for analytics

All artist-level aggregation should use the canonical identity layer consistently. Display canonical names in user-facing views, while retaining original source strings for traceability and debugging.

Do not alter scrobble-level totals or monthly listening counts. The normalization layer must only affect artist identity/grouping.

### 5. Audit and diagnostics

Generate a human-readable report containing:

- number of distinct raw artist-name strings;
- number of canonical artist identities after explicit mapping;
- number of aliases mapped;
- every applied merge, showing canonical name and original variants;
- unresolved suspicious variants and why they were not merged;
- potential candidates requiring manual review.

Avoid presenting fuzzy candidates as confirmed duplicates.

### 6. Tests

Add tests for:

- Unicode NFC equivalence;
- case and whitespace normalization;
- Cyrillic, Latin, and mixed-script strings;
- explicit alias mappings;
- preservation of distinct artists with similar names;
- collaboration/feature strings remaining safe;
- absent or empty mapping preserving legacy behavior;
- preservation of raw source values;
- deterministic repeated builds;
- correct lifetime and per-year aggregation after normalization;
- unchanged total scrobble and monthly counts.

Repeated listens must remain repeated listens.

### 7. Compatibility and pipeline safety

- Preserve existing JSON contracts unless a coordinated migration is necessary.
- If generated data formats change, update all consumers together.
- Keep the pipeline deterministic and offline-testable.
- Do not introduce network lookups into the normal build path.
- Do not modify the private data repository or rewrite raw history as part of this task.

## Definition of Done

1. Complete and report the code/data-flow audit.
2. Propose the normalization architecture before implementation.
3. Implement a separate canonical artist layer.
4. Build an initial alias map from actual history, with evidence for each merge.
5. Produce a report of confirmed merges and unresolved candidates.
6. Add regression tests covering the cases above.
7. Run the repository's documented lint, type-check, offline test suite, and local build commands. Discover the actual commands from project documentation rather than assuming them.
8. Inspect the generated culture/music pages and verify that all artist-level views use canonical identities.
9. Update documentation with the mapping format and the safe procedure for adding aliases.

In the final report include:

- confirmed aliases merged;
- raw-name count versus canonical-artist count;
- unresolved candidates;
- any changes to lifetime/yearly top-artist rankings;
- test, lint, type-check, and build results;
- any remaining limitations.

**Core principle: prefer leaving two potentially identical artists separate over incorrectly merging two different artists.**