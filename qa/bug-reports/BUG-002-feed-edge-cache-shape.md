# BUG-002 — Feed breaks for up to an hour after deploy: edge cache serves the old API tuple shape

| | |
|---|---|
| **Severity** | Major (feature dead in prod, self-heals only after cache TTL) |
| **Priority** | High |
| **Status** | Fixed + prevention rule in CLAUDE.md / exit criteria |
| **Component** | `functions/api/feed.js` + `feed.html` client |
| **Environment** | Production (Cloudflare edge cache), immediately after a deploy that changed the feed response shape |
| **Found via** | Post-deploy exploratory check of the feed page |

## Description

`/api/feed` responses are compact JSON tuples (arrays, not objects) and are cached at the
edge: `Cache-Control: public, max-age=60, s-maxage=3600, stale-while-revalidate=600`.

A deploy extended the tuple with a new field (companions). The new client destructures the
new length; Cloudflare edge nodes kept serving the **old, shorter tuples** for up to an
hour (`s-maxage=3600`). Result: the freshly deployed client destructured `undefined` and
the feed rendered broken items — but only in regions/nodes with a warm cache, and only
until TTL expiry, making it intermittent and confusing.

## Steps to Reproduce (pre-fix)

1. Warm the edge cache: load `feed.html` so `/api/feed?...` is cached with shape v1.
2. Deploy a change that alters the response tuple shape (e.g. append a field) together
   with a client that expects shape v2.
3. Reload `feed.html` from the same region within the `s-maxage` window.

## Expected

Client and API shape always agree — either the new client gets new-shape data, or shape
changes are versioned so old cache entries can never be handed to a new client.

## Actual

New client + cached old-shape response → destructuring misalignment → feed items render
with wrong/missing fields for up to 1 hour, varying by edge node.

## Root cause

Cache key did not encode the response schema version. HTML (client) and API (data) have
independent caching lifetimes, so "deploy both together" does not synchronize them.

## Fix

Every feed fetch URL carries a schema-version query param, e.g. `_v=companions`. Bumping
the tag on any shape change makes the new client's URLs miss the old cache entries
entirely. The old entries age out unused.

## Regression coverage / prevention

- Live API contract tests pin the current tuple length and field order.
- Process rule (CLAUDE.md + test-strategy exit criteria): **any** response-shape change
  must bump the `_v=` tag in the same commit — reviewed as part of definition-of-done.

## Lessons

Classic cache-versioning failure mode, easy to reintroduce because it is invisible in
local testing (no edge cache) and in CI (fresh fetches). The durable mitigation is the
versioned cache key plus a written rule at the exact place a future editor will look.
