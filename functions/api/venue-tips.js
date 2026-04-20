// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Cloudflare Pages Function — /api/venue-tips?fsq_id=<id>
 *
 * Proxies Foursquare Places API v3 /places/{fsq_id}/tips
 * Returns up to 3 tip texts for display in the nearby map popup.
 */

const HEADERS = { 'Content-Type': 'application/json' };

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const fsqId = (url.searchParams.get('fsq_id') || '').trim().replace(/[^a-zA-Z0-9]/g, '');

  if (!fsqId) {
    return new Response(JSON.stringify({ tips: [] }), { headers: HEADERS });
  }

  const apiKey = env.FSQ_API_KEY;
  if (!apiKey) {
    return new Response(JSON.stringify({ error: 'FSQ_API_KEY not configured' }), { status: 500, headers: HEADERS });
  }

  const params = new URLSearchParams({ limit: '3', sort: 'POPULAR', fields: 'text,created_at' });
  const resp = await fetch(`https://places-api.foursquare.com/places/${fsqId}/tips?${params}`, {
    headers: {
      'Authorization': `Bearer ${apiKey}`,
      'X-Places-Api-Version': '2025-06-17',
    },
  });

  if (!resp.ok) {
    return new Response(JSON.stringify({ tips: [] }), { headers: HEADERS });
  }

  const data = await resp.json();
  const tips = (data.items || [])
    .map(t => (t.text || '').trim())
    .filter(Boolean)
    .slice(0, 3);

  return new Response(JSON.stringify({ tips }), { headers: HEADERS });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: HEADERS });
}
