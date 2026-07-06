# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
fetch_comments.py  –  Back-fill per-check-in COMMENTS (the reply thread posted
*under* a check-in) into comments.json.

This is distinct from a check-in's `shout` (the text you attach when posting,
already captured in checkins.csv). Comments are a separate Foursquare/Swarm
feature: `checkin.comments.items[]`, each with text / author / createdAt.

WHY TWO PHASES (rate-limit friendly):
  The bulk feed /users/self/checkins returns `comments.count` on every compact
  check-in — for free, ~1 call per 250 check-ins. The actual comment *text*
  only comes from the per-check-in detail endpoint /v2/checkins/{id}, a
  "regular" (≈500/hr) endpoint. So:

    Phase 1  SCAN  — walk the whole feed (~N/250 calls), note which check-ins
                     have comments.count > 0.
    Phase 2  FETCH — call /v2/checkins/{id} ONLY for those, pull items[].

  If only a few hundred of your 65K check-ins actually carry comments, the whole
  back-fill costs a few hundred detail calls instead of 65K — minutes, not days.

RESUMABLE & SAFE:
  - comments.json stores only check-ins that have comments, keyed by checkin_id.
  - A cid is re-fetched only if its feed comments.count changed (new reply) or it
    was never fetched. Re-runs skip already-captured threads.
  - On HTTP 429 / rate_limit the sweep backs off a few times, then saves progress
    and exits 0 so the NEXT run resumes exactly where it stopped.
  - --max-calls N caps Phase-2 detail calls per run for controlled/scheduled runs.

USAGE:
    # 1) Probe only — how many check-ins have comments? (Phase 1, no writes)
    python scripts/fetch_comments.py --token "$FOURSQUARE_TOKEN" --check

    # 2) Full back-fill into comments.json (Phase 1 + Phase 2)
    python scripts/fetch_comments.py --token "$FOURSQUARE_TOKEN" \
        --out C:/Users/toouur/Documents/GitHub/foursquare-data/comments.json

    # 3) Throttled run (e.g. 400 detail calls, then resume next time)
    python scripts/fetch_comments.py --token "$FOURSQUARE_TOKEN" \
        --out .../comments.json --max-calls 400

    # Fallback: some feeds omit comments.count — sweep EVERY check-in's detail
    # (expensive; only if --check reports the count field is missing).
    python scripts/fetch_comments.py --token "$FOURSQUARE_TOKEN" \
        --out .../comments.json --all

    # 4) Targeted back-fill of specific threads a Foursquare data export proves
    #    exist but comments.json is missing (e.g. friends' comments on old
    #    check-ins). Reads the id list, skips the feed scan, and tries the
    #    dedicated /checkins/{id}/comments endpoint first, then the /checkins/{id}
    #    detail endpoint. Writes a per-id outcome report so you can see which old
    #    ids are still age-gated (HTTP 403) versus recovered.
    python scripts/fetch_comments.py --token "$FOURSQUARE_TOKEN" \
        --out    .../comments.json \
        --targets .../comments_backfill_targets.json \
        --report  .../comments_backfill_report.json

Outputs:
  - Prints CHANGED=true/false to stdout (for GitHub Actions >> $GITHUB_OUTPUT).
  - Exit 0 normal (incl. graceful rate-limit stop); 1 on fatal error.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

FEED_API             = "https://api.foursquare.com/v2/users/self/checkins"
CHECKIN_API          = "https://api.foursquare.com/v2/checkins/{cid}"
CHECKIN_COMMENTS_API = "https://api.foursquare.com/v2/checkins/{cid}/comments"
API_V                = "20231201"
LIMIT                = 250
SLEEP                = 0.35   # between detail calls; ≈ existing overlaps-enrichment cadence


def resolve_token(cli_token: str | None) -> str:
    cli = (cli_token or "").strip()
    if cli:
        return cli
    return os.environ.get("FOURSQUARE_TOKEN", "").strip()


def _scrub(text: str, token: str) -> str:
    """Redact the OAuth token from any string before it is logged.

    requests puts the full request URL (including ?oauth_token=…) into exception
    messages, so never log a raw exception str — run it through this first.
    """
    return text.replace(token, "***") if token else text


# ── store I/O ──────────────────────────────────────────────────────────────────
def load_store(out_path: Path) -> dict:
    if not out_path.exists():
        return {"_meta": {}, "comments": {}}
    try:
        data = json.loads(out_path.read_text(encoding="utf-8"))
        data.setdefault("_meta", {})
        data.setdefault("comments", {})
        return data
    except Exception as exc:  # noqa: BLE001 — corrupt file shouldn't crash a run
        log.warning("Could not read %s (%s) — starting fresh.", out_path, exc)
        return {"_meta": {}, "comments": {}}


def save_store(out_path: Path, store: dict) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── API helpers ────────────────────────────────────────────────────────────────
class RateLimited(Exception):
    """Raised when the API reports we've exhausted the hourly quota."""


def _is_rate_limit(resp: requests.Response) -> bool:
    if resp.status_code == 429:
        return True
    try:
        code = resp.json().get("meta", {}).get("code")
    except ValueError:
        code = None
    return code == 429


def request_feed(token: str, params: dict, retries: int = 120) -> dict:
    """One page of the check-in feed, with the same 500-nudge resilience the
    main fetcher uses during beforeTimestamp pagination."""
    for attempt in range(retries):
        try:
            resp = requests.get(
                FEED_API,
                params={"oauth_token": token, "v": API_V, **params},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Network error contacting Foursquare API: {_scrub(str(exc), token)}"
            ) from exc

        if resp.status_code == 500 and "beforeTimestamp" in params:
            params["beforeTimestamp"] = int(params["beforeTimestamp"]) - 1
            if attempt % 10 == 0:
                log.warning("500 near beforeTimestamp; nudging back (attempt %d/%d)",
                            attempt + 1, retries)
            time.sleep(1)
            continue

        if _is_rate_limit(resp):
            raise RateLimited("feed")

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"HTTP error from feed: {_scrub(str(exc), token)}") from exc

        data = resp.json()
        if data.get("meta", {}).get("code") != 200:
            raise RuntimeError(f"API error: {data.get('meta')}")
        return data.get("response", {}).get("checkins", {})

    raise RuntimeError("Too many API retries while scanning the feed")


def scan_feed(token: str) -> tuple[dict[str, dict], int, bool]:
    """Phase 1. Walk the whole feed newest→oldest.

    Returns (targets, scanned, count_field_seen):
      targets          — {cid: {"count", "venue", "venue_id", "ts"}} for
                          check-ins whose comments.count > 0
      scanned          — total check-ins seen
      count_field_seen — True if ANY compact check-in carried a `comments` field
                         (False ⇒ feed omits counts, caller should use --all)
    """
    targets: dict[str, dict] = {}
    scanned = 0
    count_field_seen = False
    before_ts: int | None = None

    first = request_feed(token, {"limit": 1})
    total = first.get("count", 0)
    log.info("Phase 1 scan: server reports %s check-ins.", total)

    while True:
        params: dict[str, int] = {"limit": LIMIT}
        if before_ts:
            params["beforeTimestamp"] = before_ts
        data = request_feed(token, params)
        items = data.get("items", [])
        if not items:
            break

        for ci in items:
            scanned += 1
            comments = ci.get("comments")
            if comments is not None:
                count_field_seen = True
            count = (comments or {}).get("count", 0)
            if count and count > 0:
                cid = str(ci.get("id", "") or "")
                if not cid:
                    continue
                venue = ci.get("venue", {})
                # The bulk feed frequently inlines comments.items[] right here —
                # harvest them so we never have to call the /checkins/{id} detail
                # endpoint (which 403s permanently on older check-ins).
                # NB: use a distinct name — do NOT rebind the page-level `items`
                # list, which pagination still needs below for beforeTimestamp.
                inline = extract_comments(ci)
                targets[cid] = {
                    "count": count,
                    "venue": venue.get("name", ""),
                    "venue_id": str(venue.get("id", "") or ""),
                    "ts": ci.get("createdAt"),
                    "items": inline,
                }

        log.info("  scanned %d / %d  (with comments so far: %d)",
                 scanned, total, len(targets))
        if scanned >= total:
            break
        before_ts = int(items[-1]["createdAt"]) - 1
        time.sleep(SLEEP)

    return targets, scanned, count_field_seen


def extract_comments(ci: dict) -> list[dict]:
    """Pull comments.items[] from a full check-in object into our stored shape."""
    out: list[dict] = []
    for c in ci.get("comments", {}).get("items", []):
        u = c.get("user", {})
        author = (u.get("firstName", "") + " " + u.get("lastName", "")).strip()
        out.append({
            "text": c.get("text", ""),
            "at": c.get("createdAt"),
            "author": author,
            "author_id": str(u.get("id", "") or ""),
        })
    return out


def fetch_checkin_detail(token: str, cid: str, timeout: int = 30) -> dict:
    """One /checkins/{id} detail call. Raises RateLimited on 429."""
    resp = requests.get(
        CHECKIN_API.format(cid=cid),
        params={"oauth_token": token, "v": API_V},
        timeout=timeout,
    )
    if _is_rate_limit(resp):
        raise RateLimited(cid)
    resp.raise_for_status()
    return resp.json().get("response", {}).get("checkin", {})


# ── targeted back-fill (recover the 441 threads the export proves exist) ─────────
def _comments_items_to_store(raw_items: list[dict]) -> list[dict]:
    """Map /checkins/{id}/comments response items[] into our stored shape.

    The dedicated comments endpoint returns the same comment objects the detail
    endpoint inlines under checkin.comments.items[] — user{} + text + createdAt.
    """
    out: list[dict] = []
    for c in raw_items or []:
        u = c.get("user", {})
        author = (u.get("firstName", "") + " " + u.get("lastName", "")).strip()
        out.append({
            "text": c.get("text", ""),
            "at": c.get("createdAt"),
            "author": author,
            "author_id": str(u.get("id", "") or ""),
        })
    return out


def fetch_checkin_comments(token: str, cid: str, timeout: int = 30) -> tuple[list[dict], int]:
    """Try the DEDICATED /checkins/{id}/comments listing endpoint.

    This is a different route than the /checkins/{id} detail endpoint that
    403s on pre-2024 check-ins; some age-gated check-ins still expose their
    comment thread here. Returns (items, http_status). Raises RateLimited on 429.
    On any non-200 (incl. 403) returns ([], status) so the caller can fall back.
    """
    resp = requests.get(
        CHECKIN_COMMENTS_API.format(cid=cid),
        params={"oauth_token": token, "v": API_V, "limit": 200},
        timeout=timeout,
    )
    if _is_rate_limit(resp):
        raise RateLimited(cid)
    if resp.status_code != 200:
        return [], resp.status_code
    try:
        body = resp.json()
    except ValueError:
        return [], resp.status_code
    if body.get("meta", {}).get("code") != 200:
        return [], resp.status_code
    raw = body.get("response", {}).get("comments", {}).get("items", [])
    return _comments_items_to_store(raw), 200


def load_targets(path: Path) -> list[dict]:
    """Read the back-fill target list. Accepts either:
      - the .json produced alongside it: {"_meta":{…}, "targets":[{checkin_id,…}]}
        (or a bare list of such dicts), or
      - a .txt with one checkin_id per line (# comments allowed).
    Returns [{"checkin_id", "expected_count", "venue", "venue_id", "ts"}].
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        rows = data.get("targets", data) if isinstance(data, dict) else data
        out = []
        for r in rows:
            cid = str(r.get("checkin_id", "") or "").strip()
            if cid:
                out.append({
                    "checkin_id": cid,
                    "expected_count": r.get("expected_count", 0),
                    "venue": r.get("venue", ""),
                    "venue_id": str(r.get("venue_id", "") or ""),
                    "ts": r.get("ts"),
                })
        return out
    # plain-text: one id per line
    out = []
    for line in text.splitlines():
        cid = line.split("#", 1)[0].strip()
        if cid:
            out.append({"checkin_id": cid, "expected_count": 0,
                        "venue": "", "venue_id": "", "ts": None})
    return out


def run_targets(token: str, targets_path: Path, out_path: Path,
                report_path: Path | None, max_calls: int, sleep: float,
                now: str) -> int:
    """--targets mode: recover specific comment threads by checkin_id.

    For each target, try the dedicated /comments endpoint first, then fall back
    to the /checkins/{id} detail endpoint. Per-id outcome (which endpoint won,
    or the status that blocked it) is logged to a report so we can see exactly
    which old ids remain age-gated (403) after this pass.
    """
    targets = load_targets(targets_path)
    if not targets:
        log.error("No targets loaded from %s", targets_path)
        print("CHANGED=false")
        return 1
    log.info("--targets: %d checkin id(s) loaded from %s", len(targets), targets_path.name)

    store = load_store(out_path)
    stored = store["comments"]

    # Skip ids we already have a complete thread for (idempotent re-runs).
    worklist = []
    for t in targets:
        cid = t["checkin_id"]
        cur = stored.get(cid, {})
        exp = t.get("expected_count") or 0
        if cur.get("items") and (exp == 0 or len(cur["items"]) >= exp):
            continue
        worklist.append(t)
    already = len(targets) - len(worklist)
    if already:
        log.info("  %d already complete in comments.json — skipping.", already)
    if max_calls and max_calls > 0:
        worklist = worklist[:max_calls]

    report: dict[str, dict] = {}
    changed = False
    recovered = fresh_403 = empty = errors = 0

    for i, t in enumerate(worklist, 1):
        cid = t["checkin_id"]
        items: list[dict] = []
        won = None
        status_comments = status_detail = None

        # 1) dedicated comments-listing endpoint
        try:
            items, status_comments = fetch_checkin_comments(token, cid)
            if items:
                won = "comments"
        except RateLimited:
            log.warning("Rate-limited after %d call(s) — saving progress, resume next run.", i - 1)
            break
        except requests.RequestException as exc:
            status_comments = "net"
            log.debug("comments endpoint net error %s: %s", cid, _scrub(str(exc), token))

        # 2) fall back to the full detail endpoint
        if not items:
            try:
                ci = fetch_checkin_detail(token, cid)
                items = extract_comments(ci)
                status_detail = 200
                if items:
                    won = "detail"
                # detail carries venue/ts — enrich the target if it was blank
                v = ci.get("venue", {})
                if not t.get("venue"):
                    t["venue"] = v.get("name", "")
                if not t.get("venue_id"):
                    t["venue_id"] = str(v.get("id", "") or "")
                if not t.get("ts"):
                    t["ts"] = ci.get("createdAt")
            except RateLimited:
                log.warning("Rate-limited after %d call(s) — saving progress, resume next run.", i - 1)
                break
            except requests.HTTPError as exc:
                status_detail = exc.response.status_code if exc.response is not None else "?"
            except requests.RequestException as exc:
                status_detail = "net"
                log.debug("detail endpoint net error %s: %s", cid, _scrub(str(exc), token))

        if items:
            entry = {
                "count": t.get("expected_count") or len(items),
                "venue": t.get("venue", "") or stored.get(cid, {}).get("venue", ""),
                "venue_id": t.get("venue_id", "") or stored.get(cid, {}).get("venue_id", ""),
                "ts": t.get("ts") or stored.get(cid, {}).get("ts"),
                "items": items,
                "fetched_at": int(time.time()),
                "source": f"targets:{won}",
            }
            if stored.get(cid) != entry:
                stored[cid] = entry
                changed = True
            recovered += 1
            outcome = "recovered"
        else:
            outcome = "empty" if 200 in (status_comments, status_detail) else "blocked"
            if outcome == "empty":
                empty += 1
            elif 403 in (status_comments, status_detail):
                fresh_403 += 1
            else:
                errors += 1

        report[cid] = {
            "outcome": outcome,
            "won": won,
            "items": len(items),
            "expected": t.get("expected_count") or 0,
            "status_comments": status_comments,
            "status_detail": status_detail,
            "venue": t.get("venue", ""),
        }

        if i % 25 == 0:
            save_store(out_path, store)
            log.info("  %d/%d — recovered %d, 403 %d, empty %d, err %d",
                     i, len(worklist), recovered, fresh_403, empty, errors)
        time.sleep(sleep)

    store["_meta"]["targets_run"] = {
        "updated": now,
        "targets_file": targets_path.name,
        "attempted": len(worklist),
        "recovered": recovered,
        "still_403": fresh_403,
        "empty": empty,
        "errors": errors,
    }
    save_store(out_path, store)

    if report_path is not None:
        report_path.write_text(
            json.dumps({"_meta": store["_meta"]["targets_run"], "results": report},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("Per-id outcomes written to %s", report_path)

    log.info("[%s] --targets done: attempted %d, recovered %d thread(s), "
             "still-403 %d, empty %d, errors %d.",
             now, len(worklist), recovered, fresh_403, empty, errors)
    print(f"CHANGED={'true' if changed else 'false'}")
    return 0


# ── main ────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Back-fill per-check-in comments into comments.json")
    ap.add_argument("--token", default="", help="Foursquare OAuth token (else FOURSQUARE_TOKEN)")
    ap.add_argument("--out", default="", help="Path to comments.json (omit with --check)")
    ap.add_argument("--check", action="store_true",
                    help="Phase-1 probe only: report how many check-ins have comments; no writes.")
    ap.add_argument("--all", action="store_true",
                    help="Fallback: call detail for EVERY check-in, ignoring feed counts "
                         "(use only if --check shows the count field is missing).")
    ap.add_argument("--max-calls", type=int, default=0,
                    help="Cap Phase-2 detail calls this run (0 = unlimited). Resume next run.")
    ap.add_argument("--sleep", type=float, default=SLEEP, help="Seconds between detail calls.")
    ap.add_argument("--targets", default="",
                    help="Targeted back-fill: path to a list of checkin_ids to recover "
                         "(the comments_backfill_targets .json or .txt). Skips the feed "
                         "scan; tries /checkins/{id}/comments first, then /checkins/{id}.")
    ap.add_argument("--report", default="",
                    help="With --targets: write a per-id outcome JSON here (which endpoint "
                         "won, or the status — e.g. 403 — that blocked each id).")
    args = ap.parse_args()

    token = resolve_token(args.token)
    if not token:
        log.error("Missing API token. Provide --token or set FOURSQUARE_TOKEN.")
        print("CHANGED=false")
        return 1

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # ── targeted back-fill short-circuit (no feed scan) ──────────────────────
    if args.targets:
        if not args.out:
            log.error("--out is required with --targets.")
            print("CHANGED=false")
            return 1
        return run_targets(
            token,
            Path(args.targets),
            Path(args.out),
            Path(args.report) if args.report else None,
            args.max_calls,
            args.sleep,
            now,
        )

    # ── Phase 1: scan the feed for comment counts ────────────────────────────
    try:
        targets, scanned, count_field_seen = scan_feed(token)
    except RateLimited:
        log.error("Rate-limited during Phase-1 feed scan — try again later.")
        print("CHANGED=false")
        return 0
    except RuntimeError as exc:
        log.error("Feed scan failed: %s", exc)
        print("CHANGED=false")
        return 1

    total_comments = sum(t["count"] for t in targets.values())
    log.info("[%s] Phase 1 done: %d check-in(s) carry comments (%d comment(s) total) "
             "out of %d scanned.", now, len(targets), total_comments, scanned)

    if not count_field_seen and not args.all:
        log.warning("The feed returned NO `comments` field on any check-in. Counts are "
                    "unavailable, so the cheap scan can't target anything. Re-run with "
                    "--all to sweep every check-in's detail endpoint (expensive).")

    if args.check:
        print("CHANGED=false")
        return 0

    if not args.out:
        log.error("--out is required unless --check is given.")
        return 1

    out_path = Path(args.out)
    store = load_store(out_path)
    stored = store["comments"]

    # ── harvest feed-inlined threads (bypasses the 403 detail endpoint) ───────
    # Any target the bulk feed already handed us complete items for is stored
    # right now; the detail call would only risk a 403 for no new data.
    feed_harvested = 0
    feed_changed = False
    if not args.all:
        for cid, meta in targets.items():
            items = meta.get("items") or []
            if not items or len(items) < meta["count"]:
                continue  # missing/partial → leave for Phase 2 detail fetch
            entry = {
                "count": meta["count"],
                "venue": meta.get("venue", "") or stored.get(cid, {}).get("venue", ""),
                "venue_id": meta.get("venue_id", "") or stored.get(cid, {}).get("venue_id", ""),
                "ts": meta.get("ts") or stored.get(cid, {}).get("ts"),
                "items": items,
                "fetched_at": int(time.time()),
            }
            # Compare on content (ignore fetched_at churn).
            prev = stored.get(cid)
            if not prev or {k: prev.get(k) for k in ("count", "items")} != {"count": entry["count"], "items": items}:
                stored[cid] = entry
                feed_changed = True
            feed_harvested += 1
    if feed_harvested:
        log.info("Phase 1b: harvested %d complete thread(s) inline from the feed "
                 "(no detail call needed).", feed_harvested)

    # ── decide the Phase-2 worklist ──────────────────────────────────────────
    if args.all:
        # Fallback path: we have no reliable counts, so we must probe every
        # check-in. Build the id list from the feed scan's coverage instead —
        # but scan_feed only kept comment-bearing ids, so for --all we re-walk
        # to collect all ids.
        log.info("--all: collecting every check-in id for a full detail sweep …")
        worklist = _all_checkin_ids(token)
    else:
        # Only fetch check-ins we still lack complete items for. After the
        # feed-harvest pass above, a stored entry whose item count already
        # covers the target count needs no detail call.
        def _needs_detail(cid: str, meta: dict) -> bool:
            cur = stored.get(cid, {})
            if str(cur.get("count", -1)) == str(meta["count"]) \
                    and len(cur.get("items", [])) >= meta["count"]:
                return False
            return True
        worklist = [cid for cid, meta in targets.items() if _needs_detail(cid, meta)]

    if args.max_calls and args.max_calls > 0:
        worklist = worklist[: args.max_calls]

    if not worklist:
        if feed_changed:
            log.info("No detail calls needed — %d thread(s) came straight from the feed.",
                     feed_harvested)
        else:
            log.info("Nothing to fetch — comments.json already up to date.")
        _write_meta(store, scanned, targets, total_comments, now)
        save_store(out_path, store)
        print(f"CHANGED={'true' if feed_changed else 'false'}")
        return 0

    log.info("Phase 2: fetching detail for %d check-in(s) …", len(worklist))
    fetched = 0
    changed = feed_changed
    hit_limit = False
    for i, cid in enumerate(worklist, 1):
        try:
            ci = fetch_checkin_detail(token, cid, timeout=30)
        except RateLimited:
            hit_limit = True
            log.warning("Rate-limited after %d detail call(s) — saving progress, "
                        "resume next run.", fetched)
            break
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            log.warning("Detail fetch failed for %s: HTTP %s (skipping).", cid, status)
            time.sleep(args.sleep)
            continue
        except requests.RequestException as exc:
            log.warning("Network error for %s: %s (skipping, will retry next run).",
                        cid, _scrub(str(exc), token))
            time.sleep(args.sleep)
            continue

        fetched += 1
        items = extract_comments(ci)
        cnt = ci.get("comments", {}).get("count", len(items))
        if items:
            venue = ci.get("venue", {})
            entry = {
                "count": cnt,
                "venue": venue.get("name", "") or targets.get(cid, {}).get("venue", ""),
                "venue_id": str(venue.get("id", "") or "") or targets.get(cid, {}).get("venue_id", ""),
                "ts": ci.get("createdAt") or targets.get(cid, {}).get("ts"),
                "items": items,
                "fetched_at": int(time.time()),
            }
            if stored.get(cid) != entry:
                stored[cid] = entry
                changed = True
        else:
            # No items despite a positive count (e.g. deleted comments) — drop any stale entry.
            if cid in stored:
                del stored[cid]
                changed = True

        if i % 50 == 0:
            _write_meta(store, scanned, targets, total_comments, now)
            save_store(out_path, store)
            log.info("  checkpoint: %d/%d fetched, %d thread(s) stored.",
                     i, len(worklist), len(stored))
        time.sleep(args.sleep)

    _write_meta(store, scanned, targets, total_comments, now)
    save_store(out_path, store)
    log.info("[%s] Phase 2 done: %d detail call(s), %d check-in(s) with comments stored.%s",
             now, fetched, len(stored),
             "  (stopped early on rate limit — re-run to continue.)" if hit_limit else "")

    print(f"CHANGED={'true' if changed else 'false'}")
    return 0


def _write_meta(store: dict, scanned: int, targets: dict, total_comments: int, now: str) -> None:
    store["_meta"] = {
        "description": "Per-check-in comment threads (distinct from shouts). "
                       "Keyed by checkin_id; only check-ins with comments are stored.",
        "updated": now,
        "checkins_scanned": scanned,
        "checkins_with_comments": len(targets),
        "comments_total": total_comments,
        "stored_threads": len(store.get("comments", {})),
    }


def _all_checkin_ids(token: str) -> list[str]:
    """Collect every check-in id (used only by the --all fallback)."""
    ids: list[str] = []
    before_ts: int | None = None
    first = request_feed(token, {"limit": 1})
    total = first.get("count", 0)
    while True:
        params: dict[str, int] = {"limit": LIMIT}
        if before_ts:
            params["beforeTimestamp"] = before_ts
        data = request_feed(token, params)
        items = data.get("items", [])
        if not items:
            break
        ids.extend(str(ci.get("id", "") or "") for ci in items if ci.get("id"))
        if len(ids) >= total:
            break
        before_ts = int(items[-1]["createdAt"]) - 1
        time.sleep(SLEEP)
    return ids


if __name__ == "__main__":
    sys.exit(main())
