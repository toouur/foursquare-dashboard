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
 */

const HEADERS = {
  'Content-Type': 'application/json',
  'Cache-Control': 'no-store',
};

export async function onRequestGet({ request, env }) {
  if (!env.DB) {
    return jsonResp({ error: 'DB binding not configured', venue: [], city: [], trip: [], tip: [], companion: [] }, 503);
  }

  const url = new URL(request.url);
  const q   = (url.searchParams.get('q') || '').trim();

  if (q.length < 2) {
    return jsonResp({ venue: [], city: [], trip: [], tip: [], companion: [] });
  }

  const like  = `%${q}%`;
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);

  // Returns true if all query words appear in the joined string
  function matches(...parts) {
    const t = parts.filter(Boolean).join(' ').toLowerCase();
    return words.every(w => t.includes(w));
  }

  // Run all D1 queries in parallel
  const [venueRes, cityRes, tipRes, compRes, overlapRes] = await Promise.all([
    env.DB.prepare(
      'SELECT name, category, city, country, checkin_count ' +
      'FROM venues WHERE name LIKE ?1 OR city LIKE ?1 OR category LIKE ?1 ' +
      'ORDER BY checkin_count DESC LIMIT 60'
    ).bind(like).all(),

    env.DB.prepare(
      'SELECT city, country, COUNT(*) AS cnt FROM venues ' +
      'WHERE city LIKE ?1 OR country LIKE ?1 ' +
      'GROUP BY city, country ORDER BY cnt DESC LIMIT 40'
    ).bind(like).all(),

    env.DB.prepare(
      'SELECT venue, text, city, country FROM tips ' +
      'WHERE venue LIKE ?1 OR text LIKE ?1 OR city LIKE ?1 LIMIT 60'
    ).bind(like).all(),

    // with_name + created_by_name merged, then overlaps_name separately
    // (overlaps_name is comma-separated so we split in JS)
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
    venue: (venueRes.results || [])
      .filter(v => matches(v.name, v.city, v.country, v.category))
      .map(v => ({
        t:   'venue',
        n:   v.name,
        c:   v.city   || null,
        co:  v.country || null,
        cat: v.category || null,
        cnt: v.checkin_count || 0,
      })),

    city: (cityRes.results || [])
      .filter(c => c.city && matches(c.city, c.country))
      .map(c => ({
        t:   'city',
        n:   c.city,
        co:  c.country || null,
        cnt: c.cnt     || 0,
      })),

    // Trips are not yet stored in D1 — placeholder for future Phase 3
    trip: [],

    tip: (tipRes.results || [])
      .filter(t => matches(t.venue, t.text, t.city, t.country))
      .map(t => ({
        t:  'tip',
        n:  t.venue  || null,
        tx: (t.text  || '').slice(0, 120),
        c:  t.city   || null,
        co: t.country || null,
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
