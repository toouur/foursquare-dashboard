# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Per-venue dossiers for venue.html (metrics package).

One record per venue_id: name/category/place as of the latest check-in, visit
count, first/last visit, visits per year, local hour histogram, companions met
there, photo count and EVERY visit (local time, shout, companions, all of its
photos, reconstructed flag), newest first. Rows without a venue_id (some
reconstructed ones) have no page.

Records are sharded by a 32-bit FNV-1a hash of the venue_id into
``venue_data_<xx>.json`` (256 files, ~80 KB each with the full history), so the page fetches one small
shard for ``venue.html#<venue_id>`` instead of the whole history. The last hex
digits of Foursquare ids are far from uniform (one shard reached 889 KB), hence
the hash; `venueShard()` in the venue page mirrors `venue_shard()`.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
from collections import Counter, defaultdict

from .companions import collect_companions
from .milestones import _f, _Localiser
from .trips import _parse_ts

REFURB = "refurbished"
MAX_COMPANIONS = 12


_MARKER = re.compile(r"\[[a-z_]+(?::[^\]]*)?\]")


def clean_shout(shout: str | None, people: list[str]) -> str:
    """The person's own words: machine markers ([geo_pinned], [coords_approx:…]) go,
    and so does Swarm's auto text "with A, B" when it only restates the companions
    the page already lists."""
    s = _MARKER.sub("", shout or "").strip()
    if people and re.fullmatch(r"with\s+.+", s, flags=re.I | re.S):
        named = [p.split()[0].lower() for p in people if p.split()]
        if all(any(n.startswith(w.lower()) or w.lower().startswith(n) for n in named)
               for w in re.split(r"[,\s]+(?:and\s+|&\s*)?", s[5:].strip()) if w):
            return ""
    return s


def venue_shard(venue_id: str) -> str:
    """Shard key: 32-bit FNV-1a of the (ASCII hex) id, mod 256, as 2 hex digits."""
    h = 0x811C9DC5
    for ch in venue_id:
        h = ((h ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
    return f"{h & 0xFF:02x}"


def compute_venues(rows: list[dict], photos_by_checkin: dict | None = None) -> dict[str, dict]:
    """venue_id → record (see module docstring). Visits are newest first."""
    photos_by_checkin = photos_by_checkin or {}
    loc = _Localiser()
    by_vid: dict[str, list[tuple[float, object, dict]]] = defaultdict(list)
    for r in rows:
        vid = (r.get("venue_id") or "").strip()
        d = _parse_ts(r)
        if not vid or d is None:
            continue
        ld = loc(d, _f(r.get("lat")), _f(r.get("lng")), (r.get("country") or "").strip())
        by_vid[vid].append((d.timestamp(), ld, r))

    out: dict[str, dict] = {}
    for vid, visits in by_vid.items():
        visits.sort(key=lambda t: t[0])
        last = visits[-1][2]
        years: Counter[int] = Counter()
        hours = [0] * 24
        comp: Counter[str] = Counter()
        n_photos = 0
        recent = []
        for ts, ld, r in visits:
            years[ld.year] += 1                      # type: ignore[attr-defined]
            hours[ld.hour] += 1                      # type: ignore[attr-defined]
            people = collect_companions(r)
            comp.update(people)
            pics = photos_by_checkin.get(r.get("checkin_id") or "") or []
            n_photos += len(pics)
            recent.append([ld.strftime("%Y-%m-%d %H:%M"),  # type: ignore[attr-defined]
                           clean_shout(r.get("shout"), people), people, list(pics),
                           1 if (r.get("source_app") or "") == REFURB else 0])
        recent.reverse()
        out[vid] = {
            "name": last.get("venue") or "",
            "cat": last.get("category") or "",
            "city": last.get("city") or "",
            "country": last.get("country") or "",
            "lat": _f(last.get("lat")),
            "lng": _f(last.get("lng")),
            "n": len(visits),
            "first": recent[-1][0][:10],
            "last": recent[0][0][:10],
            "years": {str(y): c for y, c in sorted(years.items())},
            "hours": hours,
            "companions": comp.most_common(MAX_COMPANIONS),
            "photos": n_photos,
            "v": recent,
        }
    return out


VISIT_FIELDS = ["local", "shout", "with", "photos", "rf"]


def write_venue_shards(venues: dict[str, dict], out_dir: str) -> str:
    """Write venue_data_<xx>.json (stale shards removed first); return a content hash."""
    for stale in glob.glob(os.path.join(out_dir, "venue_data_*.json")):
        os.remove(stale)
    shards: dict[str, dict] = defaultdict(dict)
    for vid, rec in venues.items():
        shards[venue_shard(vid)][vid] = rec
    h = hashlib.sha1()
    for key in sorted(shards):
        body = json.dumps({"f": VISIT_FIELDS, "v": shards[key]},
                          ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        h.update(body.encode("utf-8"))
        with open(os.path.join(out_dir, f"venue_data_{key}.json"), "w", encoding="utf-8") as fh:
            fh.write(body)
    return h.hexdigest()[:8]
