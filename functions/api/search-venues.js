// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Cloudflare Pages Function — /api/search-venues?q=<query>[&ll=lat,lng]
 *
 * Server-side proxy to Foursquare v2 /venues/search.
 * Uses FOURSQUARE_TOKEN (v2 OAuth token) — same one used by fetch_checkins.py.
 *
 * Add in Cloudflare dashboard:
 *   Pages → foursquare-dashboard → Settings → Environment Variables
 *   FOURSQUARE_TOKEN = <your Foursquare OAuth token>
 */

const HEADERS = { 'Content-Type': 'application/json' };

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const query = (url.searchParams.get('q') || '').trim();

  if (query.length < 2) {
    return new Response(JSON.stringify({ results: [] }), { headers: HEADERS });
  }

  const token = env.FOURSQUARE_TOKEN || env.FSQ_API_KEY;
  if (!token) {
    return new Response(JSON.stringify({ error: 'No API token configured' }), { status: 500, headers: HEADERS });
  }

  const params = new URLSearchParams({
    query,
    limit: '8',
    intent: 'global',
    v: '20231001',
    oauth_token: token,
  });
  const ll = url.searchParams.get('ll');
  if (ll) params.set('ll', ll);

  const resp = await fetch(`https://api.foursquare.com/v2/venues/search?${params}`);
  if (!resp.ok) {
    const txt = await resp.text();
    return new Response(JSON.stringify({ error: `Foursquare error ${resp.status}`, detail: txt }), { status: resp.status, headers: HEADERS });
  }
  const data = await resp.json();

  // Normalise to { results: [...] } shape matching what custom-list.js expects
  const venues = (data.response && data.response.venues) || [];
  const results = venues.map(v => ({
    fsq_id: v.id,
    name: v.name,
    geocodes: { main: { latitude: v.location?.lat, longitude: v.location?.lng } },
    location: {
      address: v.location?.address,
      locality: v.location?.city,
      country: v.location?.country,
      formatted_address: (v.location?.formattedAddress || []).join(', '),
    },
    categories: (v.categories || []).map(c => ({ name: c.name })),
  }));

  return new Response(JSON.stringify({ results }), { headers: HEADERS });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: HEADERS });
}
