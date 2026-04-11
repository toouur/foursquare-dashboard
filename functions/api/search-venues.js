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

  if (query.length < 2) {
    return new Response(JSON.stringify({ results: [] }), { headers: HEADERS });
  }

  const apiKey = env.FSQ_API_KEY;
  if (!apiKey) {
    return new Response(JSON.stringify({ error: 'FSQ_API_KEY not configured' }), { status: 500, headers: HEADERS });
  }

  const params = new URLSearchParams({
    query,
    limit: '8',
    fields: 'fsq_id,name,geocodes,location,categories',
  });
  const ll = url.searchParams.get('ll');
  if (ll) params.set('ll', ll);

  const resp = await fetch(`https://api.foursquare.com/v3/places/search?${params}`, {
    headers: {
      'Authorization': apiKey,
      'X-Places-Api-Version': '1970-01-01',
    },
  });

  if (!resp.ok) {
    const txt = await resp.text();
    return new Response(JSON.stringify({ error: `Foursquare error ${resp.status}`, detail: txt }), { status: resp.status, headers: HEADERS });
  }

  const data = await resp.json();
  return new Response(JSON.stringify(data), { headers: HEADERS });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: HEADERS });
}
