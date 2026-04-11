// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Cloudflare Pages Function — /api/search-venues?q=<query>[&ll=lat,lng]
 *
 * Proxy to Foursquare Places API v3 /places/search.
 * Requires FSQ_API_KEY as a Cloudflare Pages environment variable.
 *
 * Add in Cloudflare dashboard:
 *   Pages → foursquare-dashboard → Settings → Environment Variables
 *   FSQ_API_KEY = <your Foursquare Places API key>
 */

const HEADERS = { 'Content-Type': 'application/json' };

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const query  = (url.searchParams.get('q') || '').trim();
  const ll     = url.searchParams.get('ll') || '';
  const near   = url.searchParams.get('near') || '';
  const sort   = url.searchParams.get('sort') || '';
  const cursor = url.searchParams.get('cursor') || '';

  // Require either a text query (≥2 chars), a location, a city name, or a cursor (pagination)
  if (query.length < 2 && !ll && !near && !cursor) {
    return new Response(JSON.stringify({ results: [] }), { headers: HEADERS });
  }

  const apiKey = env.FSQ_API_KEY;
  if (!apiKey) {
    return new Response(JSON.stringify({ error: 'FSQ_API_KEY not configured' }), { status: 500, headers: HEADERS });
  }

  const limitRaw = parseInt(url.searchParams.get('limit') || '12', 10);
  const limit = Math.min(Math.max(limitRaw || 12, 1), 50).toString();

  const params = new URLSearchParams({
    limit,
    fields: 'fsq_place_id,name,latitude,longitude,location,categories',
  });
  if (query)  params.set('query', query);
  if (ll)     params.set('ll', ll);
  else if (near) params.set('near', near);
  // categories param is not forwarded — FSQ API ignores it on free tier;
  // caller applies category filtering client-side instead.
  if (sort)   params.set('sort', sort);
  if (cursor) params.set('cursor', cursor);

  const resp = await fetch(`https://places-api.foursquare.com/places/search?${params}`, {
    headers: {
      'Authorization': `Bearer ${apiKey}`,
      'X-Places-Api-Version': '2025-06-17',
    },
  });

  if (!resp.ok) {
    const txt = await resp.text();
    return new Response(JSON.stringify({ error: `Foursquare error ${resp.status}`, detail: txt }), { status: resp.status, headers: HEADERS });
  }

  const data = await resp.json();
  // Normalise new API shape to what the frontend expects
  const results = (data.results || []).map(p => ({
    fsq_id:   p.fsq_place_id,
    name:     p.name,
    geocodes: { main: { latitude: p.latitude, longitude: p.longitude } },
    location: p.location || {},
    categories: p.categories || [],
  }));
  const nextCursor = data.next || null;
  return new Response(JSON.stringify({ results, nextCursor }), { headers: HEADERS });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: HEADERS });
}
