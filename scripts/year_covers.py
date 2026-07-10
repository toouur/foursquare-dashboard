#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""year_covers.py — pick ONE stable "cover" photo per year.

Both the years index (card thumbnails + hero) and the per-year album pages
(hero lead image, og:image) need a representative photo for each year. The
old rule was "latest photo in the year wins", which drifts on every build:
each new check-in with a photo silently replaces the ongoing year's cover,
and even a back-filled historical photo could reshuffle a past year.

This module gives a single, deterministic cover per year that is STABLE
across builds, with a manual override:

Precedence
----------
1. ``config/year_covers.json`` override — ``{"2024": "<filename or checkin_id>"}``.
   The value may be a photo filename (exact) or a check-in id (its first photo
   is used). Pins that year's cover forever.
2. Deterministic "signature" score. A check-in that carries a shout, has more
   companions, and has more photos ranks highest — those are the memorable
   moments. Ties break by EARLIEST timestamp, then checkin_id, then filename.
   Every component is intrinsic to existing rows, so appending newer photos
   never reshuffles a completed year's cover (and only displaces the current
   year's cover if a strictly higher-signature moment appears).

The same ``config/year_covers.json`` file also carries per-MONTH keys, read by
the year album pages (gen_year_pages):

- ``"2024-07": "<filename or checkin_id>"`` — pins that month's timeline photo.
- ``"2024-07-note": "free text"`` — hand-written note that REPLACES the
  auto-generated month narrative (plain text, HTML-escaped at render).

Public API
----------
``select_year_covers(rows, photos_by_checkin, config_dir, pix_url="")``
    → ``{year: {"fname", "src", "checkin_id", "ts", "override"}}``
``load_month_cover_overrides(config_dir)`` → ``{(year, month): value}``
``load_month_notes(config_dir)`` → ``{(year, month): text}``
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_MONTH_KEY_RE = re.compile(r"^(\d{4})-(\d{1,2})$")
_NOTE_KEY_RE = re.compile(r"^(\d{4})-(\d{1,2})-note$")


def _read_covers_json(config_dir: str | Path) -> dict:
    p = Path(config_dir) / "year_covers.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def load_cover_overrides(config_dir: str | Path) -> dict[int, str]:
    """Read config/year_covers.json → {year:int -> filename_or_checkin_id:str}.

    Month ("YYYY-MM") and note ("YYYY-MM-note") keys live in the same file but
    are ignored here — int() fails on them, which is the filter.
    """
    out: dict[int, str] = {}
    for k, v in _read_covers_json(config_dir).items():
        try:
            yr = int(k)
        except (ValueError, TypeError):
            continue
        val = str(v).strip()
        if val:
            out[yr] = val
    return out


def _month_keyed(config_dir: str | Path, pattern: re.Pattern) -> dict[tuple[int, int], str]:
    out: dict[tuple[int, int], str] = {}
    for k, v in _read_covers_json(config_dir).items():
        m = pattern.match(str(k).strip())
        if not m:
            continue
        yr, mo = int(m.group(1)), int(m.group(2))
        val = str(v).strip()
        if 1 <= mo <= 12 and val:
            out[(yr, mo)] = val
    return out


def load_month_cover_overrides(config_dir: str | Path) -> dict[tuple[int, int], str]:
    """``"YYYY-MM"`` keys → {(year, month): filename_or_checkin_id}."""
    return _month_keyed(config_dir, _MONTH_KEY_RE)


def load_month_notes(config_dir: str | Path) -> dict[tuple[int, int], str]:
    """``"YYYY-MM-note"`` keys → {(year, month): hand-written narrative text}."""
    return _month_keyed(config_dir, _NOTE_KEY_RE)


def _companion_count(row: dict) -> int:
    try:
        from metrics import collect_companions
        return len(collect_companions(row))
    except Exception:
        return 0


def _src(fname: str, pix_url: str) -> str:
    return f"{pix_url.rstrip('/')}/{fname}" if pix_url and fname else fname


def select_year_covers(
    rows: list[dict],
    photos_by_checkin: dict[str, list[str]],
    config_dir: str | Path,
    pix_url: str = "",
) -> dict[int, dict]:
    """Return one stable cover per year. See module docstring for the rule."""
    overrides = load_cover_overrides(config_dir)

    cid_year: dict[str, int] = {}
    cid_row: dict[str, dict] = {}
    for r in rows:
        cid = (r.get("checkin_id") or "").strip()
        try:
            ts = int(r.get("date", 0) or 0)
        except (ValueError, TypeError):
            continue
        if cid and ts:
            cid_year[cid] = datetime.fromtimestamp(ts, tz=timezone.utc).year
            cid_row[cid] = r

    # Gather scored candidates per year + a filename → (year, cid, ts) index
    # so an override given as a bare filename can be resolved.
    cands: dict[int, list[dict]] = {}
    fname_index: dict[str, tuple[int, str, int]] = {}
    for cid, fnames in photos_by_checkin.items():
        yr = cid_year.get(cid)
        r = cid_row.get(cid)
        if not yr or not fnames or not r:
            continue
        ts = int(r.get("date", 0) or 0)
        has_shout = 1 if (r.get("shout") or "").strip() else 0
        comps = _companion_count(r)
        for fn in fnames:
            fname_index.setdefault(fn, (yr, cid, ts))
        cands.setdefault(yr, []).append({
            "fname": fnames[0],
            "checkin_id": cid,
            "ts": ts,
            # min() key: negate "bigger is better" fields, then EARLIEST ts,
            # then stable string tie-breaks — fully deterministic.
            "key": (-has_shout, -comps, -len(fnames), ts, cid, fnames[0]),
        })

    result: dict[int, dict] = {}
    for yr in set(cands) | set(overrides):
        chosen: dict | None = None
        ov = overrides.get(yr)
        if ov:
            if ov in fname_index:  # override is a filename
                oyr, ocid, ots = fname_index[ov]
                chosen = {"fname": ov, "checkin_id": ocid, "ts": ots}
            elif ov in photos_by_checkin and photos_by_checkin[ov]:  # a checkin_id
                orow = cid_row.get(ov)
                ots = int(orow.get("date", 0) or 0) if orow else 0
                chosen = {"fname": photos_by_checkin[ov][0], "checkin_id": ov, "ts": ots}
        if chosen is None:
            lst = cands.get(yr) or []
            if not lst:
                continue
            best = min(lst, key=lambda c: c["key"])
            chosen = {"fname": best["fname"], "checkin_id": best["checkin_id"], "ts": best["ts"]}
        chosen["src"] = _src(chosen["fname"], pix_url)
        chosen["override"] = bool(ov)
        result[yr] = chosen
    return result
