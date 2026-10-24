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

    plus, with ``--scrobbles-dir``, the RAW layer the cultural chronicle reads:
    every scrobble as one TSV line ``ts  artist  track  album`` (UTC unix seconds,
    sorted) in ``scrobbles_<YYYY>.tsv`` per UTC year — ~16 MB for ~280k
    scrobbles. Repeats are KEPT: Last.fm really stores the same track twice in
    the same second now and then, and counts both (dedup on (ts, artist, track)
    lost 4 of them in the first seed, 2018/2020).
    Each run re-reads the last ``OVERLAP_DAYS`` before the newest scrobble on
    disk and REPLACES that window with what the API returns: devices scrobble
    late with the original timestamp (offline plays sync hours or days later),
    and a "newer than the watermark" fetch misses those for good — the first
    seed found 5 such plays in 2026 that the weekly runs never saw. A window
    reply more than 10 % smaller than what is on disk is treated as a transient
    API failure and nothing is written (``WINDOW_SHRANK``).
    With a raw layer the year state is REBUILT from it on every run, so the two
    can no longer drift; ``LATE_SCROBBLES=<n>`` reports the late arrivals.
    An empty directory (or ``--reseed-raw``) walks the whole history once
    (~1400 API pages, 15-25 min).

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


# ── Raw scrobble layer (scrobbles_<YYYY>.tsv) ─────────────────────────────────

RAW_FIELDS = ("ts", "artist", "track", "album")


def _clean(v) -> str:
    """One TSV cell. Only the three characters that would break the line format
    are replaced; everything else (double spaces, edge spaces) stays as scrobbled."""
    return str(v or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")


OVERLAP_DAYS = 14
WINDOW_MIN_RATIO = 0.9


def load_raw(dir_: Path) -> dict[str, list[dict]]:
    """year → [row] for every scrobbles_<YYYY>.tsv in dir_ (repeats kept)."""
    out: dict[str, list[dict]] = {}
    for f in sorted(dir_.glob("scrobbles_*.tsv")):
        rows = out.setdefault(f.stem.split("_", 1)[1], [])
        # split on "\n" only: splitlines() would also break on \x85 / \u2028 inside a tag
        for i, line in enumerate(f.read_text(encoding="utf-8").split("\n")):
            if i == 0 or not line:
                continue                                   # header
            parts = (line.split("\t") + [""] * 4)[:4]
            try:
                ts = int(parts[0])
            except ValueError:
                log.warning("%s:%d unreadable ts %r — line skipped", f.name, i + 1, parts[0])
                continue
            rows.append({"ts": ts, "artist": parts[1], "track": parts[2], "album": parts[3]})
    return out


def raw_last_ts(raw: dict[str, list[dict]]) -> int:
    return max((r["ts"] for rows in raw.values() for r in rows), default=0)


def _year(ts: int) -> str:
    return str(datetime.fromtimestamp(ts, tz=timezone.utc).year)


def _row(sc: dict) -> dict | None:
    row = {k: _clean(sc.get(k)) for k in ("artist", "track", "album")}
    return {"ts": int(sc["ts"]), **row} if row["artist"] else None


def add_raw(raw: dict[str, list[dict]], sc: dict) -> bool:
    """Append one scrobble dict to the raw layer; False when it has no artist."""
    row = _row(sc)
    if row is None:
        return False
    raw.setdefault(_year(row["ts"]), []).append(row)
    return True


def _key(r: dict) -> tuple:
    return (r["ts"], r["artist"], r["track"], r["album"])


def replace_window(raw: dict[str, list[dict]], lo: int, hi: int, scrobbles: list[dict]) -> tuple[set[str], int, int]:
    """Make [lo, hi] hold exactly `scrobbles`. Returns (years touched, rows
    added, rows removed) — counted as multisets, so a repeat is a real row."""
    from collections import Counter
    new = [r for r in (_row(sc) for sc in scrobbles) if r is not None and lo <= r["ts"] <= hi]
    old = [r for rows in raw.values() for r in rows if lo <= r["ts"] <= hi]
    before, after = Counter(map(_key, old)), Counter(map(_key, new))
    added, removed = sum((after - before).values()), sum((before - after).values())
    if not added and not removed:
        return set(), 0, 0
    years = {_year(r["ts"]) for r in old + new}
    for y in years:
        raw[y] = [r for r in raw.get(y, []) if not lo <= r["ts"] <= hi]
    for r in new:
        raw.setdefault(_year(r["ts"]), []).append(r)
    return years, added, removed


def state_from_raw(raw: dict[str, list[dict]], user: str) -> dict:
    """The year state recomputed from the raw layer (the raw layer is the truth)."""
    st = new_state(user)
    for rows in raw.values():
        for r in rows:
            add_scrobble(st, r["artist"], r["ts"])
    return st


def write_raw(dir_: Path, raw: dict[str, list[dict]], years: set[str] | None = None) -> None:
    """Rewrite scrobbles_<YYYY>.tsv for `years` (all when None), sorted by ts."""
    dir_.mkdir(parents=True, exist_ok=True)
    for yr in sorted(years if years is not None else raw):
        rows = sorted(raw.get(yr, []), key=_key)
        f = dir_ / f"scrobbles_{yr}.tsv"
        if not rows:
            if f.exists():
                f.unlink()
            continue
        lines = ["\t".join(RAW_FIELDS)] + [
            f"{r['ts']}\t{r['artist']}\t{r['track']}\t{r['album']}" for r in rows]
        # newline="\n": the same bytes whether written in CI or by a local bootstrap on Windows
        f.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def raw_year_diff(raw: dict[str, list[dict]], state: dict) -> dict[str, tuple[int, int]]:
    """Years where the raw layer and the year state disagree: year → (raw, state)."""
    out = {}
    for yr in sorted(set(raw) | set(state.get("years", {}))):
        a = len(raw.get(yr, []))
        b = int(state.get("years", {}).get(yr, {}).get("scrobbles", 0))
        if a != b:
            out[yr] = (a, b)
    return out


# ── Bootstrap from a full local export ─────────────────────────────────────────

def bootstrap_from_export(path: Path, user: str, raw_layer: dict | None = None) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    scrobbles = raw.get("scrobbles", [])
    st = new_state(raw.get("username") or user)
    for s in scrobbles:
        ms = s.get("date")
        if not ms:
            continue
        add_scrobble(st, s.get("artist", ""), int(ms) // 1000)
        if raw_layer is not None:
            add_raw(raw_layer, {"ts": int(ms) // 1000, "artist": s.get("artist"),
                                "track": s.get("track"), "album": s.get("album")})
    log.info("Bootstrapped %d scrobbles across %d years (through ts %d)",
             st["total"], len(st["years"]), st["last_ts"])
    return st


# ── Last.fm API (incremental) ──────────────────────────────────────────────────

def _api_get(params: dict, retries: int = 4) -> dict:
    """One GET against the Last.fm 2.0 API returning parsed JSON. Requires
    `requests`; imported lazily so bootstrap/derive work without the dep.

    Network errors and 429/5xx are retried with backoff: the one-off raw-layer
    seed walks ~1400 pages, and one hiccup must not throw the whole walk away."""
    import time  # noqa: PLC0415

    import requests  # noqa: PLC0415
    params = {**params, "format": "json"}
    for attempt in range(retries + 1):
        try:
            r = requests.get(API_ROOT, params=params, timeout=30)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                raise requests.ConnectionError(f"HTTP {r.status_code}")
            break
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt >= retries:
                raise
            wait = 2 ** attempt
            log.warning("  Last.fm request failed (%s) — retry in %ds", e, wait)
            time.sleep(wait)
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


def _text(v) -> str:
    """getRecentTracks gives artist/album as {'#text': …} (or {'name': …} when
    extended); tolerate a bare string too."""
    if isinstance(v, dict):
        return v.get("#text") or v.get("name") or ""
    return v if isinstance(v, str) else ""


def fetch_since(api_key: str, user: str, from_ts: int, to_ts: int | None = None) -> list[dict]:
    """Return [{ts, artist, track, album}] for scrobbles strictly after `from_ts`.

    Uses user.getRecentTracks with `from` (inclusive) and paginates newest-first.
    `to` is pinned to the moment the walk starts: without it a scrobble landing
    mid-walk pushes every later page down by one row, and a long walk (the
    raw-layer seed) would repeat rows at page edges and lose others. Skips the
    now-playing row (no date). After the walk the row count is compared with the
    total the API reported on page 1 (`FETCH_TOTAL_MISMATCH` warning)."""
    import time  # noqa: PLC0415
    to_ts = int(time.time()) if to_ts is None else to_ts
    out: list[dict] = []
    page = 1
    total_pages = 1
    seen = 0
    reported: int | None = None
    while page <= total_pages:
        body = _api_get({
            "method": "user.getRecentTracks", "user": user, "api_key": api_key,
            "limit": 200, "page": page, "from": from_ts + 1, "to": to_ts,
        })
        rt = body.get("recenttracks", {})
        attr = rt.get("@attr", {})
        try:
            total_pages = int(attr.get("totalPages", 1)) or 1
        except (TypeError, ValueError):
            total_pages = 1
        if reported is None:
            try:
                reported = int(attr.get("total", 0))
            except (TypeError, ValueError):
                reported = 0
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
            if ts <= from_ts or ts > to_ts:
                continue
            out.append({"ts": ts, "artist": _text(t.get("artist")),
                        "track": t.get("name") or "", "album": _text(t.get("album"))})
            seen += 1
        if page == 1 or page % 50 == 0 or page == total_pages:
            log.info("  page %d/%d — %d new so far", page, total_pages, seen)
        page += 1
    if reported and reported != len(out):
        log.warning("FETCH_TOTAL_MISMATCH: API reported %d scrobbles in the window, got %d",
                    reported, len(out))
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
    ap.add_argument("--scrobbles-dir", required=False,
                    help="raw layer: scrobbles_<YYYY>.tsv per year (seeded from the full history when empty)")
    ap.add_argument("--reseed-raw", action="store_true",
                    help="ignore the raw layer on disk and walk the whole history again")
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
        raw_seed: dict | None = {} if args.scrobbles_dir else None
        st = bootstrap_from_export(Path(args.bootstrap), args.user, raw_seed)
        if args.scrobbles_dir and raw_seed is not None:
            write_raw(Path(args.scrobbles_dir), raw_seed)
            log.info("Wrote raw layer → %s (%d scrobbles)", args.scrobbles_dir,
                     sum(len(v) for v in raw_seed.values()))
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
    raw_dir = Path(args.scrobbles_dir) if args.scrobbles_dir else None
    raw = ({} if args.reseed_raw else load_raw(raw_dir)) if raw_dir else None
    import time  # noqa: PLC0415
    to_ts = int(time.time())
    if raw is not None:
        raw_ts = raw_last_ts(raw)
        if raw_ts == 0:
            log.info("No raw layer yet (or --reseed-raw) — walking the whole history once into %s", raw_dir)
        from_ts = max(0, raw_ts - OVERLAP_DAYS * 86400) if raw_ts else 0
    else:
        raw_ts = 0
        from_ts = int(st.get("last_ts", 0))
    try:
        new = fetch_since(args.api_key, args.user, from_ts, to_ts)
    except LastfmError as e:
        print("KEY_VALID=false" if e.code in (10, 4, 26) else "KEY_VALID=true")
        print("CHANGED=false")
        log.error("%s", e)
        return 2 if e.code in (10, 4, 26) else 1
    except Exception as e:
        print("KEY_VALID=true"); print("CHANGED=false")
        log.error("fetch failed: %s", e)
        return 1

    if raw is not None and raw_dir:
        on_disk = sum(1 for rows in raw.values() for r in rows if from_ts < r["ts"] <= raw_ts)
        fetched = sum(1 for sc in new if sc["ts"] <= raw_ts)
        if raw_ts and fetched < WINDOW_MIN_RATIO * on_disk:
            print("KEY_VALID=true"); print("CHANGED=false")
            log.error("WINDOW_SHRANK: the API returned %d scrobbles for a window that holds %d on disk "
                      "— treated as a transient failure, nothing written", fetched, on_disk)
            return 1
        touched, added, removed = replace_window(raw, from_ts + 1, to_ts, new)
        late = sum(1 for sc in new if sc["ts"] <= raw_ts) - on_disk
        print(f"LATE_SCROBBLES={max(0, late)}")
        changed = bool(touched)
        if changed:
            write_raw(raw_dir, raw, touched)
            st = state_from_raw(raw, st.get("user") or args.user)
            _write_json(state_path, st)
            if out_path:
                _write_json(out_path, derive_aggregate(st))
            log.info("Raw layer: +%d / -%d rows in %s; year state rebuilt — total %d",
                     added, removed, ", ".join(sorted(touched)), st["total"])
        else:
            log.info("No change in the last %d days — nothing written", OVERLAP_DAYS)
        print(f"RAW_YEAR_DIFF={len(raw_year_diff(raw, st))}")
    else:
        state_ts = int(st.get("last_ts", 0))
        fresh = [sc for sc in new if sc["ts"] > state_ts]
        for sc in fresh:
            add_scrobble(st, sc["artist"], sc["ts"])
        changed = bool(fresh)
        if changed:
            _write_json(state_path, st)
            if out_path:
                _write_json(out_path, derive_aggregate(st))
            log.info("Added %d scrobbles — total %d, last_ts %d", len(fresh), st["total"], st["last_ts"])
        else:
            log.info("No new scrobbles since ts %d — nothing written", from_ts)

    print("KEY_VALID=true")
    print(f"CHANGED={'true' if changed else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
