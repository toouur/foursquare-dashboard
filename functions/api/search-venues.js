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
  const query = (url.searchParams.get('q') || '').trim();
  const ll    = url.searchParams.get('ll') || '';
  const cat   = url.searchParams.get('cat') || '';
  const sort  = url.searchParams.get('sort') || '';

  // Require either a text query (≥2 chars) or a location for nearby search
  if (query.length < 2 && !ll) {
    return new Response(JSON.stringify({ results: [] }), { headers: HEADERS });
  }

  const apiKey = env.FSQ_API_KEY;
  if (!apiKey) {
    return new Response(JSON.stringify({ error: 'FSQ_API_KEY not configured' }), { status: 500, headers: HEADERS });
  }

  const params = new URLSearchParams({
    limit: '12',
    fields: 'fsq_place_id,name,latitude,longitude,location,categories,rating,stats',
  });
  if (query) params.set('query', query);
  if (ll)    params.set('ll', ll);
  if (cat)   params.set('categories', cat);
  if (sort)  params.set('sort', sort);

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
    rating:   p.rating ?? null,
    stats:    p.stats ?? null,
  }));
  return new Response(JSON.stringify({ results }), { headers: HEADERS });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: HEADERS });
}
