// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * k6 load test — /api/search (Cloudflare Pages Function backed by D1).
 *
 * Deliberately modest: ~20 virtual users for two minutes against production.
 * The point is a latency/error baseline for the D1-backed endpoint (each
 * request fans out to six D1 queries), not stressing Cloudflare's edge.
 *
 * Run locally:   k6 run tests/load/search.js
 * Run in CI:     Actions → "k6 load test" → Run workflow
 */
import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE = __ENV.BASE_URL || 'https://4sq.pages.dev';

// Mix of hot venue/city terms, companions, tips text and a cold miss.
const QUERIES = ['minsk', 'coffee', 'warszawa', 'airport', 'metro',
                 'vilnius', 'pizza', 'museum', 'train', 'zzzznope'];

export const options = {
  stages: [
    { duration: '30s', target: 5 },
    { duration: '60s', target: 20 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_failed:   ['rate<0.01'],           // <1% errors
    http_req_duration: ['p(95)<1000', 'p(99)<2000'],
  },
};

export default function () {
  const q = QUERIES[Math.floor(Math.random() * QUERIES.length)];
  const res = http.get(`${BASE}/api/search?q=${q}`, {
    tags: { name: '/api/search' },
  });
  check(res, {
    'status 200':     r => r.status === 200,
    'json with keys': r => {
      try {
        const b = r.json();
        return 'venue' in b && 'companion' in b;
      } catch { return false; }
    },
  });
  sleep(Math.random() * 2 + 0.5);   // think time: 0.5–2.5 s per VU
}
