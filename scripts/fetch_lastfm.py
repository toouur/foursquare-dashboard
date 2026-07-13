# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
fetch_lastfm.py  –  Maintain the compact per-year Last.fm scrobble aggregate that
the year-story pages read (``lastfm_years.json`` next to checkins.csv), plus its
incremental state cache (``lastfm_state.json``).

WHY TWO FILES
    The raw scrobble history is ~273k rows (46 MB) — far too big to commit or to
    thread through the build. Instead we keep:

      * ``lastfm_state.json`` — the incremental cache. Per year it stores the FULL
        artist→plays counter + a 12-slot month histogram + the scrobble total, and
        a ``last_ts`` watermark. This lets each run fetch only NEW scrobbles (since
        ``last_ts``) and fold them in, then recompute the #1 artist exactly. A few
        hundred KB, safe to commit.
      * ``lastfm_years.json`` — the tiny derived aggregate the build reads: per year
        ``{scrobbles, top_artist:{name,plays}, months:[12]}``. ~19 years, a few KB.

MODES
  1. BOOTSTRAP (one-off seed) — ``--bootstrap path/to/all_scrobbles_api.json``.
     Reads an existing full export ({username, scrobbles:[{track,artist,album,
     date(ms-epoch)}]}) and builds state + aggregate from scratch. No network.
  2. INCREMENTAL (the weekly CI job) — needs ``LASTFM_API_KEY`` (secret) and a
     ``--user``. Calls ``user.getRecentTracks`` from ``last_ts`` forward, paginating,
     folds new plays into the existing state, rewrites both files.
  3. ``--check`` — probe API auth only (one tiny call), write nothing.

EXIT / OUTPUT CONTRACT (for CI, mirrors fetch_flights.py):
  - Prints ``KEY_VALID=true|false`` and ``CHANGED=true|false`` to stdout.
  - Exit 0 : ok (aggregate written unless --check / no change).
  - Exit 2 : auth invalid — bad/absent API key (Last.fm error 10 / missing key).
  - Exit 1 : other/unexpected error (network, unreadable response).

USAGE:
    # One-off seed from the local full export (no key needed):
    python scripts/fetch_lastfm.py --bootstrap \
        C:/Users/.../lastfm-dashboard/raw_snapshot/all_scrobbles_api.json \
        --out  C:/Users/.../foursquare-data/lastfm_years.json \
        --state C:/Users/.../foursquare-data/lastfm_state.json

    # Weekly incremental (bash):
    export LASTFM_API_KEY=...
    python scripts/fetch_lastfm.py --user TOOUUR \
        --out  C:/Users/.../foursquare-data/lastfm_years.json \
        --state C:/Users/.../foursquare-data/lastfm_state.json

    # Probe auth only:
    python scripts/fetch_lastfm.py --user TOOUUR --check
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

API_ROOT = "https://ws.audioscrobbler.com/2.0/"
DEFAULT_USER = "TOOUUR"


# ── State <-> aggregate helpers ────────────────────────────────────────────────

def _empty_year() -> dict:
    return {"scrobbles": 0, "artists": {}, "months": [0] * 12}


def add_scrobble(state: dict, artist: str, ts: int) -> None:
    """Fold one scrobble (unix seconds, UTC) into the running state in place."""
    artist = (artist or "").strip()
    if not artist:
        return
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    yr = str(dt.year)
    y = state["years"].setdefault(yr, _empty_year())
    y["scrobbles"] += 1
    y["months"][dt.month - 1] += 1
    y["artists"][artist] = y["artists"].get(artist, 0) + 1
    state["total"] = state.get("total", 0) + 1
    if ts > state.get("last_ts", 0):
        state["last_ts"] = ts


def derive_aggregate(state: dict) -> dict:
    """Collapse the full state into the compact per-year build input."""
    years_out: dict[str, dict] = {}
    for yr, y in state.get("years", {}).items():
        artists = y.get("artists", {})
        if artists:
            # Deterministic: most plays, then alphabetical to break ties.
            name, plays = max(artists.items(), key=lambda kv: (kv[1], _neg_key(kv[0])))
            top = {"name": name, "plays": plays}
        else:
            top = {"name": "", "plays": 0}
        years_out[yr] = {
            "scrobbles": y.get("scrobbles", 0),
            "top_artist": top,
            "months": y.get("months", [0] * 12),
        }
    return {
        "user": state.get("user", ""),
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_scrobbles": state.get("total", 0),
        "years": years_out,
    }


def _neg_key(s: str):
    """Sort helper: alphabetical-first tie-break inside a `max` on (plays, …)."""
    # max() wants the *smallest* name to win ties, so invert each code point.
    return tuple(-ord(c) for c in s)


def new_state(user: str) -> dict:
    return {"user": user, "last_ts": 0, "total": 0, "years": {}}


# ── Bootstrap from a full local export ─────────────────────────────────────────

def bootstrap_from_export(path: Path, user: str) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    scrobbles = raw.get("scrobbles", [])
    st = new_state(raw.get("username") or user)
    for s in scrobbles:
        ms = s.get("date")
        if not ms:
            continue
        add_scrobble(st, s.get("artist", ""), int(ms) // 1000)
    log.info("Bootstrapped %d scrobbles across %d years (through ts %d)",
             st["total"], len(st["years"]), st["last_ts"])
    return st


# ── Last.fm API (incremental) ──────────────────────────────────────────────────

def _api_get(params: dict) -> dict:
    """One GET against the Last.fm 2.0 API returning parsed JSON. Requires
    `requests`; imported lazily so bootstrap/derive work without the dep."""
    import requests  # noqa: PLC0415
    params = {**params, "format": "json"}
    r = requests.get(API_ROOT, params=params, timeout=30)
    # Last.fm returns HTTP 200 with an {error, message} body for auth/logic errors.
    try:
        body = r.json()
    except ValueError:
        r.raise_for_status()
        raise RuntimeError("Last.fm returned non-JSON response")
    if isinstance(body, dict) and body.get("error"):
        code = body.get("error") or 0
        raise LastfmError(int(code), body.get("message", ""))
    r.raise_for_status()
    return body


class LastfmError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(f"Last.fm error {code}: {message}")
        self.code = code


def check_auth(api_key: str, user: str) -> bool:
    """One tiny call to confirm the key works. Raises LastfmError(10) on bad key."""
    _api_get({"method": "user.getRecentTracks", "user": user,
              "api_key": api_key, "limit": 1})
    return True


def fetch_since(api_key: str, user: str, from_ts: int) -> list[tuple[str, int]]:
    """Return [(artist, unix_ts)] for scrobbles strictly after `from_ts`.

    Uses user.getRecentTracks with `from` (inclusive) and paginates. Skips the
    now-playing row (no date). Ordered newest-first by the API; we don't rely on
    order — the caller folds each into the state idempotently by ts filter."""
    out: list[tuple[str, int]] = []
    page = 1
    total_pages = 1
    seen = 0
    while page <= total_pages:
        body = _api_get({
            "method": "user.getRecentTracks", "user": user, "api_key": api_key,
            "limit": 200, "page": page, "from": from_ts + 1,
        })
        rt = body.get("recenttracks", {})
        attr = rt.get("@attr", {})
        try:
            total_pages = int(attr.get("totalPages", 1)) or 1
        except (TypeError, ValueError):
            total_pages = 1
        tracks = rt.get("track", [])
        if isinstance(tracks, dict):        # single-track responses aren't a list
            tracks = [tracks]
        for t in tracks:
            if t.get("@attr", {}).get("nowplaying") == "true":
                continue
            d = t.get("date", {})
            uts = d.get("uts") if isinstance(d, dict) else None
            if not uts:
                continue
            ts = int(uts)
            if ts <= from_ts:
                continue
            artist = ""
            a = t.get("artist", {})
            if isinstance(a, dict):
                artist = a.get("#text") or a.get("name") or ""
            elif isinstance(a, str):
                artist = a
            out.append((artist, ts))
            seen += 1
        log.info("  page %d/%d — %d new so far", page, total_pages, seen)
        page += 1
    return out


# ── IO ─────────────────────────────────────────────────────────────────────────

def _load_state(path: Path, user: str) -> dict:
    if path.exists():
        try:
            st = json.loads(path.read_text(encoding="utf-8"))
            st.setdefault("user", user)
            st.setdefault("years", {})
            st.setdefault("last_ts", 0)
            st.setdefault("total", 0)
            return st
        except Exception as e:
            log.warning("state load failed (%s) — starting fresh", e)
    return new_state(user)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Maintain the per-year Last.fm aggregate.")
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--out", required=False, help="lastfm_years.json (build input)")
    ap.add_argument("--state", required=False, help="lastfm_state.json (cache)")
    ap.add_argument("--bootstrap", help="Seed from a full all_scrobbles_api.json export")
    ap.add_argument("--api-key", default=os.environ.get("LASTFM_API_KEY", ""))
    ap.add_argument("--check", action="store_true", help="Probe auth only; write nothing")
    args = ap.parse_args(argv)

    out_path = Path(args.out) if args.out else None
    state_path = Path(args.state) if args.state else None

    # ── --check: auth probe only ───────────────────────────────────────────────
    if args.check:
        if not args.api_key:
            print("KEY_VALID=false"); print("CHANGED=false")
            log.error("No LASTFM_API_KEY provided")
            return 2
        try:
            check_auth(args.api_key, args.user)
        except LastfmError as e:
            print("KEY_VALID=false"); print("CHANGED=false")
            log.error("%s", e)
            return 2 if e.code in (10, 4, 26) else 1
        except Exception as e:
            print("KEY_VALID=false"); print("CHANGED=false")
            log.error("probe failed: %s", e)
            return 1
        print("KEY_VALID=true"); print("CHANGED=false")
        return 0

    # ── BOOTSTRAP ──────────────────────────────────────────────────────────────
    if args.bootstrap:
        st = bootstrap_from_export(Path(args.bootstrap), args.user)
        if state_path:
            _write_json(state_path, st)
            log.info("Wrote state → %s", state_path)
        if out_path:
            _write_json(out_path, derive_aggregate(st))
            log.info("Wrote aggregate → %s", out_path)
        print("KEY_VALID=true"); print("CHANGED=true")
        return 0

    # ── INCREMENTAL ────────────────────────────────────────────────────────────
    if not state_path:
        log.error("--state is required for incremental mode")
        return 1
    if not args.api_key:
        print("KEY_VALID=false"); print("CHANGED=false")
        log.error("No LASTFM_API_KEY provided")
        return 2

    st = _load_state(state_path, args.user)
    from_ts = int(st.get("last_ts", 0))
    try:
        new = fetch_since(args.api_key, args.user, from_ts)
    except LastfmError as e:
        print("KEY_VALID=false" if e.code in (10, 4, 26) else "KEY_VALID=true")
        print("CHANGED=false")
        log.error("%s", e)
        return 2 if e.code in (10, 4, 26) else 1
    except Exception as e:
        print("KEY_VALID=true"); print("CHANGED=false")
        log.error("fetch failed: %s", e)
        return 1

    for artist, ts in new:
        add_scrobble(st, artist, ts)

    changed = bool(new)
    if changed:
        _write_json(state_path, st)
        if out_path:
            _write_json(out_path, derive_aggregate(st))
        log.info("Added %d scrobbles — total %d, last_ts %d", len(new), st["total"], st["last_ts"])
    else:
        log.info("No new scrobbles since ts %d — nothing written", from_ts)

    print("KEY_VALID=true")
    print(f"CHANGED={'true' if changed else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
