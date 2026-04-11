// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Cloudflare Pages Function — /api/custom-list
 *
 * Manages user-added venues on top of existing Foursquare lists ("overlay").
 * Uses the list_venues_custom_additions table in D1 (never touched by sync_to_d1.py).
 *
 * GET    /api/custom-list?listId=X             → custom additions for list X
 * POST   /api/custom-list  body JSON           → add a venue
 * DELETE /api/custom-list?listId=X&venueId=Y  → remove a venue
 */

const HEADERS = {
  'Content-Type': 'application/json',
  'Cache-Control': 'no-store',
};

function jsonResp(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: HEADERS });
}

export async function onRequestGet({ env, request }) {
  if (!env.DB) return jsonResp({ error: 'DB not configured' }, 503);

  const url = new URL(request.url);
  const listId = url.searchParams.get('listId');
  if (!listId) return jsonResp({ error: 'listId required' }, 400);

  const { results } = await env.DB.prepare(
    'SELECT venue_id, venue_name, lat, lng, category, address, added_at ' +
    'FROM list_venues_custom_additions WHERE list_id = ? ORDER BY added_at DESC'
  ).bind(listId).all();

  return jsonResp(results || []);
}

export async function onRequestPost({ env, request }) {
  if (!env.DB) return jsonResp({ error: 'DB not configured' }, 503);

  let body;
  try { body = await request.json(); } catch { return jsonResp({ error: 'Invalid JSON' }, 400); }

  const { listId, venueId, venueName, lat, lng, category, address } = body || {};
  if (!listId || !venueId || !venueName) {
    return jsonResp({ error: 'listId, venueId, venueName required' }, 400);
  }

  const added_at = Math.floor(Date.now() / 1000);
  await env.DB.prepare(
    'INSERT OR REPLACE INTO list_venues_custom_additions ' +
    '(list_id, venue_id, venue_name, lat, lng, category, address, added_at) ' +
    'VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
  ).bind(listId, venueId, venueName,
    lat != null ? lat : null,
    lng != null ? lng : null,
    category || null,
    address || null,
    added_at
  ).run();

  return jsonResp({ ok: true });
}

export async function onRequestDelete({ env, request }) {
  if (!env.DB) return jsonResp({ error: 'DB not configured' }, 503);

  const url = new URL(request.url);
  const listId = url.searchParams.get('listId');
  const venueId = url.searchParams.get('venueId');
  if (!listId || !venueId) return jsonResp({ error: 'listId and venueId required' }, 400);

  await env.DB.prepare(
    'DELETE FROM list_venues_custom_additions WHERE list_id = ? AND venue_id = ?'
  ).bind(listId, venueId).run();

  return jsonResp({ ok: true });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: HEADERS });
}
