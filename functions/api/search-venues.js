// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Cloudflare Pages Function — /api/search-venues?q=<query>[&ll=lat,lng]
 *
 * Server-side proxy to Foursquare Places API v3 /places/search.
 * Requires FSQ_API_KEY (or falls back to FOURSQUARE_TOKEN) as a Pages secret.
 *
 * Add in Cloudflare dashboard:
 *   Pages → foursquare-dashboard → Settings → Environment Variables
 *   FSQ_API_KEY = <your Foursquare Places API key>
 */

const HEADERS = { 'Content-Type': 'application/json' };

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const query = (url.searchParams.get('q') || '').trim();

  if (query.length < 2) {
    return new Response(JSON.stringify({ results: [] }), { headers: HEADERS });
  }

  const token = env.FSQ_API_KEY || env.FOURSQUARE_TOKEN;
  if (!token) {
    return new Response(JSON.stringify({ error: 'No API key configured' }), { status: 500, headers: HEADERS });
  }

  const params = new URLSearchParams({
    query,
    limit: '8',
    fields: 'fsq_id,name,geocodes,location,categories',
  });
  const ll = url.searchParams.get('ll');
  if (ll) params.set('ll', ll);

  const resp = await fetch(`https://api.foursquare.com/v3/places/search?${params}`, {
    headers: { Authorization: token },
  });
  const data = await resp.json();

  return new Response(JSON.stringify(data), { status: resp.status, headers: HEADERS });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: HEADERS });
}
