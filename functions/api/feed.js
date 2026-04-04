// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Cloudflare Pages Function — /api/feed
 *
 * GET /api/feed?offset=0&limit=200
 *   Returns paginated check-in records with timezone-aware local date/time.
 *   Response: { items: [...], total: N, has_more: bool }
 *   Each item: [ts, local_date, local_time, venue, city, country, category, venue_id, lat, lng, checkin_id]
 *
 * GET /api/feed?ym=1
 *   Returns only { ym_index: {"YYYY-MM": rowIndex, ...}, total: N }
 *   Used by feed.html to seed the calendar jump without fetching all data.
 */

const HEADERS = {
  'Content-Type': 'application/json',
  'Cache-Control': 'public, max-age=300',
};

// Country → IANA timezone (mirrors gen_feed.py COUNTRY_TZ)
const COUNTRY_TZ = {
  'Belarus':'Europe/Minsk','Moldova':'Europe/Chisinau','Poland':'Europe/Warsaw',
  'Ukraine':'Europe/Kyiv','Italy':'Europe/Rome','Romania':'Europe/Bucharest',
  'Lithuania':'Europe/Vilnius','Germany':'Europe/Berlin','Türkiye':'Europe/Istanbul',
  'Turkey':'Europe/Istanbul','China':'Asia/Shanghai','Spain':'Europe/Madrid',
  'Georgia':'Asia/Tbilisi','France':'Europe/Paris','India':'Asia/Kolkata',
  'Latvia':'Europe/Riga','Portugal':'Europe/Lisbon','Iran':'Asia/Tehran',
  'Egypt':'Africa/Cairo','Japan':'Asia/Tokyo','United Kingdom':'Europe/London',
  'Czechia':'Europe/Prague','Czech Republic':'Europe/Prague','Hungary':'Europe/Budapest',
  'Austria':'Europe/Vienna','Switzerland':'Europe/Zurich','Netherlands':'Europe/Amsterdam',
  'Belgium':'Europe/Brussels','Slovakia':'Europe/Bratislava','Bulgaria':'Europe/Sofia',
  'Greece':'Europe/Athens','Croatia':'Europe/Zagreb','Serbia':'Europe/Belgrade',
  'Estonia':'Europe/Tallinn','Finland':'Europe/Helsinki','Sweden':'Europe/Stockholm',
  'Norway':'Europe/Oslo','Denmark':'Europe/Copenhagen','Kazakhstan':'Asia/Almaty',
  'Uzbekistan':'Asia/Tashkent','Azerbaijan':'Asia/Baku','Armenia':'Asia/Yerevan',
  'Israel':'Asia/Jerusalem','Jordan':'Asia/Amman','Thailand':'Asia/Bangkok',
  'Vietnam':'Asia/Ho_Chi_Minh','Indonesia':'Asia/Jakarta','South Korea':'Asia/Seoul',
  'Taiwan':'Asia/Taipei','Singapore':'Asia/Singapore','Malaysia':'Asia/Kuala_Lumpur',
  'Pakistan':'Asia/Karachi','Nepal':'Asia/Kathmandu','Mongolia':'Asia/Ulaanbaatar',
  'Morocco':'Africa/Casablanca','Tunisia':'Africa/Tunis','South Africa':'Africa/Johannesburg',
  'New Zealand':'Pacific/Auckland','Holy See (Vatican City State)':'Europe/Rome',
  'San Marino':'Europe/Rome','Monaco':'Europe/Monaco','Malta':'Europe/Malta',
  'Cyprus':'Asia/Nicosia','Iceland':'Atlantic/Reykjavik','Ireland':'Europe/Dublin',
  'Slovenia':'Europe/Ljubljana','North Macedonia':'Europe/Skopje','Albania':'Europe/Tirane',
  'Montenegro':'Europe/Podgorica','Bosnia and Herzegovina':'Europe/Sarajevo',
  'Kosovo':'Europe/Belgrade','Tajikistan':'Asia/Dushanbe','Kyrgyzstan':'Asia/Bishkek',
  'Turkmenistan':'Asia/Ashgabat','Qatar':'Asia/Qatar','UAE':'Asia/Dubai',
  'United Arab Emirates':'Asia/Dubai','Saudi Arabia':'Asia/Riyadh','Iraq':'Asia/Baghdad',
  'Lebanon':'Asia/Beirut','Hong Kong':'Asia/Hong_Kong','Macao':'Asia/Macau','Macau':'Asia/Macau',
};

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

function getTz(country, lng) {
  if (COUNTRY_TZ[country]) return COUNTRY_TZ[country];
  if (country === 'Russia') {
    if (lng == null) return 'Europe/Moscow';
    if (lng < 60)  return 'Europe/Moscow';
    if (lng < 73)  return 'Asia/Yekaterinburg';
    if (lng < 84)  return 'Asia/Omsk';
    if (lng < 98)  return 'Asia/Krasnoyarsk';
    if (lng < 115) return 'Asia/Irkutsk';
    if (lng < 130) return 'Asia/Yakutsk';
    if (lng < 142) return 'Asia/Vladivostok';
    return 'Asia/Magadan';
  }
  if (country === 'Brazil') {
    if (lng == null) return 'America/Sao_Paulo';
    if (lng > -40) return 'America/Fortaleza';
    if (lng > -48) return 'America/Sao_Paulo';
    return 'America/Manaus';
  }
  if (country === 'United States') {
    if (lng == null) return 'America/New_York';
    if (lng > -75)  return 'America/New_York';
    if (lng > -90)  return 'America/Chicago';
    if (lng > -110) return 'America/Denver';
    return 'America/Los_Angeles';
  }
  if (country === 'Australia') {
    if (lng == null) return 'Australia/Sydney';
    if (lng < 129)  return 'Australia/Perth';
    if (lng < 138)  return 'Australia/Darwin';
    if (lng < 142)  return 'Australia/Adelaide';
    return 'Australia/Sydney';
  }
  if (lng != null) {
    const off = Math.round(lng / 15);
    const tz = `Etc/GMT${off <= 0 ? '+' : ''}${-off}`;
    return tz;
  }
  return 'UTC';
}

function formatLocal(ts, tz) {
  try {
    const d = new Date(ts * 1000);
    const fmt = new Intl.DateTimeFormat('en-GB', {
      timeZone: tz,
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: false,
    });
    const parts = fmt.formatToParts(d);
    const p = {};
    for (const { type, value } of parts) p[type] = value;
    // "03 Mar 2025" and "14:30"
    const dateStr = `${p.day} ${p.month} ${p.year}`;
    const timeStr = `${p.hour}:${p.minute}`;
    return [dateStr, timeStr];
  } catch {
    const d = new Date(ts * 1000);
    return [
      `${String(d.getUTCDate()).padStart(2,'0')} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`,
      `${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`,
    ];
  }
}

export async function onRequestGet({ request, env }) {
  if (!env.DB) {
    return jsonResp({ error: 'DB binding not configured' }, 503);
  }

  const url    = new URL(request.url);
  const wantYm = url.searchParams.get('ym') === '1';

  if (wantYm) {
    // Return ym_index + total only (no item data)
    const totalRes = await env.DB.prepare('SELECT COUNT(*) AS n FROM checkins').all();
    const total = totalRes.results?.[0]?.n || 0;

    // Fetch all timestamps in descending order to build ym_index
    const tsRes = await env.DB.prepare(
      'SELECT date FROM checkins ORDER BY date DESC'
    ).all();
    const ym_index = {};
    (tsRes.results || []).forEach((row, i) => {
      const d = new Date(row.date * 1000);
      const ym = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}`;
      if (!(ym in ym_index)) ym_index[ym] = i;
    });
    return jsonResp({ ym_index, total });
  }

  const offset = Math.max(0, parseInt(url.searchParams.get('offset') || '0', 10));
  const limit  = Math.min(1000, Math.max(1, parseInt(url.searchParams.get('limit') || '200', 10)));

  const [dataRes, totalRes] = await Promise.all([
    env.DB.prepare(
      'SELECT date, venue, city, country, category, venue_id, lat, lng, id ' +
      'FROM checkins ORDER BY date DESC LIMIT ?1 OFFSET ?2'
    ).bind(limit, offset).all(),
    env.DB.prepare('SELECT COUNT(*) AS n FROM checkins').all(),
  ]);

  const total   = totalRes.results?.[0]?.n || 0;
  const rows    = dataRes.results || [];
  const tzCache = {};

  const items = rows.map(r => {
    const lng = r.lng != null ? r.lng : null;
    const key = `${r.country}|${lng != null ? Math.round(lng) : ''}`;
    if (!(key in tzCache)) tzCache[key] = getTz(r.country, lng);
    const [dateStr, timeStr] = formatLocal(r.date, tzCache[key]);
    return [
      r.date,
      dateStr,
      timeStr,
      r.venue     || '',
      r.city      || '',
      r.country   || '',
      r.category  || '',
      r.venue_id  || '',
      r.lat  != null ? Math.round(r.lat  * 10000) / 10000 : null,
      r.lng  != null ? Math.round(r.lng  * 10000) / 10000 : null,
      r.id        || '',
    ];
  });

  return jsonResp({ items, total, has_more: offset + rows.length < total });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: HEADERS });
}

function jsonResp(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: HEADERS });
}
