# BUG-009 — Local build without `--photos`/`--pix-url` silently wipes the photos feed

| | |
|---|---|
| **Severity** | Minor today (footgun on a manual path); was Major pre-cutover, when the broken output could be committed and auto-deployed |
| **Priority** | Low |
| **Status** | Open — accepted, mitigated by architecture + checklist (no code guard) |
| **Component** | `scripts/build.py` CLI contract; `index.html` recent-photos strip |
| **Environment** | Local developer build |
| **Found via** | Recent-photos strip empty on the live site after a local rebuild was published |

## Description

`build.py` treats photo inputs as optional: built without `--photos` and `--pix-url`, it
emits `const photos=[]` into `index.html` — a *valid* page with an empty recent-photos
strip. No warning distinguishes "you chose a photo-less build" from "you forgot two
flags". Under the original deploy model (generated HTML committed to git,
auto-deployed — see BUG-007) a routine local rebuild-and-commit for an unrelated change
shipped the empty strip to production.

## Steps to Reproduce

1. `python scripts/build.py --input checkins.csv --config-dir config --output-dir _site`
   (no `--photos`, no `--pix-url`).
2. Open the built `index.html`.

## Expected

Either the photos feed is populated, or the build states loudly that it is producing a
degraded page (ideally requiring an explicit `--no-photos` opt-in for that).

## Actual

Build succeeds silently; `const photos=[]`; the strip renders empty. All placeholder and
JSON validation passes — the output is *well-formed*, just wrong.

## Root cause

An optional CLI flag whose omission produces output indistinguishable (to every automated
gate) from a correct build. "Optional input" and "silently degraded output" were
conflated.

## Why it's accepted rather than code-fixed

The June 2026 cutover removed the damage path: generated HTML is no longer committed, and
production is built only by CI, which always passes both flags. A bad local build now
reaches prod only via a deliberate manual `wrangler pages deploy` from that folder.
Residual risk is covered by:

- CLAUDE.md **Known Gotchas** documents the footgun for anyone (human or agent) building locally;
- the exploratory checklist explicitly separates the diagnosis: *empty strip on a local
  build = built without `--photos/--pix-url`; empty strip on prod = real incident*.

## Regression coverage

None automated by design (the state is legal). The E2E smoke suite runs against
production, where CI's flags make the state unreachable.

## Lessons

If omitting a flag yields output that passes every validator while missing a feature,
that's a latent production incident — validators check well-formedness, not intent. When
the fix isn't worth the code, write the *diagnosis* down where the confused person will
look (the checklist line telling local-vs-prod apart is the cheapest possible mitigation).
