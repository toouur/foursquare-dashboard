// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Cloudflare Pages Function — /api/search?q=<query>
 *
 * Requires D1 binding named "DB" pointing to the swarmdata database.
 * Add in Cloudflare dashboard:
 *   Pages → foursquare-dashboard → Settings → Functions → D1 database bindings
 *   Variable name: DB  |  D1 database: swarmdata
 *
 * Returns: { venue: [...], city: [...], trip: [], tip: [...], companion: [...] }
 * Each item shape mirrors the legacy search-index.json format for drop-in compatibility.
 *
 * Venues / cities / tips / trips are served by FTS5 external-content indexes
 * (venues_fts / tips_fts / trips_fts, created in d1_schema.sql, rebuilt by
 * sync_to_d1.py). Each query is prefix-matched (`token*`) and — for venues and
 * tips — ranked by bm25() relevance. Companions have no FTS table (they live in
 * the 65k-row checkins table across three comma-joined columns) and stay on LIKE.
 */

const HEADERS = {
  'Content-Type': 'application/json',
  'Cache-Control': 'no-store',
};

const EMPTY = { venue: [], city: [], trip: [], tip: [], companion: [] };

export async function onRequestGet({ request, env }) {
  if (!env.DB) {
    return jsonResp({ error: 'DB binding not configured', ...EMPTY }, 503);
  }

  const url = new URL(request.url);
  const q   = (url.searchParams.get('q') || '').trim();

  if (q.length < 2) {
    return jsonResp({ ...EMPTY });
  }

  const like  = `%${q}%`;
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);

  // FTS5 MATCH expression: each word becomes a prefix-matched phrase, AND-ed
  // together (every word must appear). Double-quotes are stripped (they delimit
  // FTS phrases); tokens with no letter/digit are dropped so we never emit an
  // empty `""` phrase, which is an FTS syntax error.
  const ftsTokens = words
    .map(w => w.replace(/"/g, ' ').trim())
    .filter(w => /[\p{L}\p{N}]/u.test(w));
  const ftsMatch  = ftsTokens.map(w => `"${w}"*`).join(' AND ');
  // City search restricts the same tokens to the city/country columns of venues_fts.
  const ftsCity   = ftsTokens.map(w => `{city country} : "${w}"*`).join(' AND ');

  // Empty MATCH (query was all punctuation) → skip the FTS-backed queries but
  // still run the LIKE-based companion lookups below.
  const noFts = { results: [] };
  const runFts = (sql, param) =>
    ftsMatch ? env.DB.prepare(sql).bind(param).all() : Promise.resolve(noFts);

  // Run all D1 queries in parallel
  const [venueRes, cityRes, tipRes, compRes, overlapRes, tripRes] = await Promise.all([
    runFts(
      'SELECT venues.name, venues.category, venues.city, venues.country, venues.checkin_count ' +
      'FROM venues_fts JOIN venues ON venues.rowid = venues_fts.rowid ' +
      'WHERE venues_fts MATCH ?1 ' +
      'ORDER BY bm25(venues_fts), venues.checkin_count DESC LIMIT 60',
      ftsMatch),

    runFts(
      'SELECT venues.city, venues.country, COUNT(*) AS cnt ' +
      'FROM venues_fts JOIN venues ON venues.rowid = venues_fts.rowid ' +
      'WHERE venues_fts MATCH ?1 AND venues.city IS NOT NULL ' +
      'GROUP BY venues.city, venues.country ORDER BY cnt DESC LIMIT 40',
      ftsCity),

    runFts(
      'SELECT tips.venue, tips.text, tips.city, tips.country ' +
      'FROM tips_fts JOIN tips ON tips.rowid = tips_fts.rowid ' +
      'WHERE tips_fts MATCH ?1 ORDER BY bm25(tips_fts) LIMIT 60',
      ftsMatch),

    // with_name + created_by_name merged, then overlaps_name separately
    // (overlaps_name is comma-separated so we split in JS). No FTS table for
    // checkins — companion search stays on LIKE.
    env.DB.prepare(
      'SELECT name, SUM(cnt) AS cnt FROM (' +
        'SELECT with_name AS name, COUNT(*) AS cnt FROM checkins ' +
        'WHERE with_name LIKE ?1 GROUP BY with_name ' +
        'UNION ALL ' +
        'SELECT created_by_name AS name, COUNT(*) AS cnt FROM checkins ' +
        'WHERE created_by_name LIKE ?1 GROUP BY created_by_name' +
      ') GROUP BY name ORDER BY cnt DESC LIMIT 20'
    ).bind(like).all(),

    env.DB.prepare(
      'SELECT overlaps_name, COUNT(*) AS cnt FROM checkins ' +
      "WHERE overlaps_name LIKE ?1 AND overlaps_name IS NOT NULL AND overlaps_name != '-' " +
      'GROUP BY overlaps_name LIMIT 60'
    ).bind(like).all(),

    runFts(
      'SELECT trips.id, trips.name, trips.start_date, trips.end_date, ' +
      'trips.checkin_count, trips.countries, trips.cities ' +
      'FROM trips_fts JOIN trips ON trips.rowid = trips_fts.rowid ' +
      'WHERE trips_fts MATCH ?1 ORDER BY trips.start_ts DESC LIMIT 20',
      ftsMatch),
  ]);

  // Merge overlaps_name (comma-separated) into companion counts
  const compMap = new Map();
  for (const c of (compRes.results || [])) {
    const name = (c.name || '').trim();
    if (!name) continue;
    compMap.set(name, (compMap.get(name) || 0) + (c.cnt || 0));
  }
  for (const row of (overlapRes.results || [])) {
    for (const part of (row.overlaps_name || '').split(',')) {
      const name = part.trim();
      if (!name || name === '-') continue;
      if (words.every(w => name.toLowerCase().includes(w))) {
        compMap.set(name, (compMap.get(name) || 0) + (row.cnt || 0));
      }
    }
  }

  return jsonResp({
    venue: (venueRes.results || []).map(v => ({
      t:   'venue',
      n:   v.name,
      c:   v.city   || null,
      co:  v.country || null,
      cat: v.category || null,
      cnt: v.checkin_count || 0,
    })),

    city: (cityRes.results || [])
      .filter(c => c.city)
      .map(c => ({
        t:   'city',
        n:   c.city,
        co:  c.country || null,
        cnt: c.cnt     || 0,
      })),

    tip: (tipRes.results || []).map(t => ({
      t:  'tip',
      n:  t.venue  || null,
      tx: (t.text  || '').slice(0, 120),
      c:  t.city   || null,
      co: t.country || null,
    })),

    trip: (tripRes.results || []).map(t => ({
      t:   'trip',
      n:   t.name,
      d:   t.start_date || null,
      cnt: t.checkin_count || 0,
      id:  t.id,
    })),

    companion: [...compMap.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 20)
      .map(([name, cnt]) => ({ t: 'companion', n: name, cnt })),
  });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: HEADERS });
}

function jsonResp(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: HEADERS });
}
