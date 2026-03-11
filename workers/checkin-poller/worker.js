/**
 * Cloudflare Worker — Foursquare check-in poller
 *
 * Runs every minute via cron trigger.
 * Compares the newest check-in timestamp against the last-seen value in KV.
 * If a new check-in is found, triggers a GitHub Actions workflow_dispatch so
 * the dashboard rebuilds and deploys within ~1–2 minutes of the check-in.
 *
 * Required Worker secrets (set via `wrangler secret put`):
 *   FOURSQUARE_TOKEN  — Foursquare OAuth token
 *   GITHUB_TOKEN      — GitHub PAT with "workflow" scope
 *
 * Required wrangler.toml variable:
 *   GITHUB_REPO       — "owner/repo" e.g. "alice/foursquare-dashboard"
 */

const FS_API_VERSION = "20231010";
const WORKFLOW_FILE  = "update-dashboard.yml";
const BRANCH         = "main";
const KV_KEY         = "last_checkin_ts";

export default {
  async scheduled(_event, env, _ctx) {
    const log = (msg) => console.log(`[checkin-poller] ${msg}`);

    // ── 1. Fetch the newest check-in from Foursquare ──────────────────────────
    const fsUrl =
      `https://api.foursquare.com/v2/users/self/checkins` +
      `?oauth_token=${env.FOURSQUARE_TOKEN}` +
      `&v=${FS_API_VERSION}` +
      `&limit=1&sort=newestfirst`;

    let newestTs;
    try {
      const resp = await fetch(fsUrl);
      if (!resp.ok) {
        log(`Foursquare API error: HTTP ${resp.status}`);
        return;
      }
      const data = await resp.json();
      const items = data?.response?.checkins?.items ?? [];
      if (items.length === 0) {
        log("No check-ins returned by Foursquare.");
        return;
      }
      newestTs = items[0].createdAt; // Unix timestamp (seconds)
    } catch (err) {
      log(`Foursquare fetch failed: ${err}`);
      return;
    }

    // ── 2. Compare with last-seen timestamp in KV ─────────────────────────────
    const lastTs = parseInt(await env.POLLER_KV.get(KV_KEY) ?? "0", 10);

    if (newestTs <= lastTs) {
      log(`No new check-ins (newest=${newestTs}, last=${lastTs}).`);
      return;
    }

    log(`New check-in detected: ts=${newestTs} (previous=${lastTs}). Triggering build…`);

    // ── 3. Trigger GitHub Actions workflow_dispatch ───────────────────────────
    const ghUrl =
      `https://api.github.com/repos/${env.GITHUB_REPO}` +
      `/actions/workflows/${WORKFLOW_FILE}/dispatches`;

    try {
      const ghResp = await fetch(ghUrl, {
        method: "POST",
        headers: {
          Authorization:          `Bearer ${env.GITHUB_TOKEN}`,
          Accept:                 "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type":         "application/json",
          "User-Agent":           "foursquare-checkin-poller/1.0",
        },
        body: JSON.stringify({ ref: BRANCH }),
      });

      if (ghResp.status === 204) {
        // ── 4. Persist new timestamp only after successful dispatch ──────────
        await env.POLLER_KV.put(KV_KEY, String(newestTs));
        log("workflow_dispatch triggered successfully.");
      } else {
        const body = await ghResp.text();
        log(`GitHub API error: HTTP ${ghResp.status} — ${body}`);
      }
    } catch (err) {
      log(`GitHub dispatch failed: ${err}`);
    }
  },
};
