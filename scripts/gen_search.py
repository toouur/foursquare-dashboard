# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_search.py — Generate search-index.json and search.html."""
import json
from collections import defaultdict
from pathlib import Path


def build_page(csv_path, config_dir, out_path, tmpl_path=None, **kwargs):
    rows      = kwargs.get("rows", [])
    all_tips  = kwargs.get("all_tips", [])
    trips     = kwargs.get("trips", [])
    metrics   = kwargs.get("metrics", {})

    items = []

    # ── Venues ────────────────────────────────────────────────────────────────
    vm = defaultdict(lambda: {"name": "", "city": "", "country": "", "category": "", "cnt": 0})
    for r in rows:
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        vname = r.get("venue", "").strip()
        if not vname:
            continue
        vm[vid]["name"]     = vname
        vm[vid]["city"]     = r.get("city", "")
        vm[vid]["country"]  = r.get("country", "")
        vm[vid]["category"] = r.get("category", "")
        vm[vid]["cnt"]     += 1
    for v in vm.values():
        q = " ".join(filter(None, [v["name"], v["city"], v["country"], v["category"]])).lower()
        items.append({"t": "venue", "n": v["name"], "c": v["city"], "co": v["country"],
                      "cat": v["category"], "cnt": v["cnt"], "q": q})

    # ── Cities ────────────────────────────────────────────────────────────────
    # metrics["cities"] = [[city_name, count, country], ...]
    for entry in metrics.get("cities", []):
        if not entry:
            continue
        name    = entry[0] if len(entry) > 0 else ""
        cnt     = entry[1] if len(entry) > 1 else 0
        country = entry[2] if len(entry) > 2 else ""
        if not name:
            continue
        q = " ".join(filter(None, [name, country])).lower()
        items.append({"t": "city", "n": name, "co": country, "cnt": cnt, "q": q})

    # ── Trips ────────────────────────────────────────────────────────────────
    for trip in trips:
        name = trip.get("name", "").strip()
        if not name:
            continue
        cities_str   = " ".join(trip.get("cities", []))
        countries_str = " ".join(trip.get("countries", []))
        d    = trip.get("start_date", "")[:7]  # YYYY-MM
        cnt  = trip.get("checkin_count", 0)
        q = " ".join(filter(None, [name, cities_str, countries_str])).lower()
        items.append({"t": "trip", "n": name, "d": d, "cnt": cnt, "q": q})

    # ── Tips ─────────────────────────────────────────────────────────────────
    for tip in all_tips:
        venue   = (tip.get("venue") or "").strip()
        text    = (tip.get("text") or "").strip()
        city    = (tip.get("city") or "").strip()
        country = (tip.get("country") or "").strip()
        if not venue and not text:
            continue
        q = " ".join(filter(None, [venue, text, city, country])).lower()
        items.append({"t": "tip", "n": venue, "tx": text[:120], "c": city, "co": country, "q": q})

    # ── Companions ───────────────────────────────────────────────────────────
    # metrics["companions"] = [[name, count], ...]
    seen = set()
    for entry in metrics.get("companions", []):
        if not entry:
            continue
        name = entry[0] if len(entry) > 0 else ""
        cnt  = entry[1] if len(entry) > 1 else 0
        if not name or name in seen:
            continue
        seen.add(name)
        items.append({"t": "companion", "n": name, "cnt": cnt, "q": name.lower()})

    # ── Write search-index.json ───────────────────────────────────────────────
    idx_path = Path(out_path).parent / "search-index.json"
    idx_path.write_text(
        json.dumps({"v": 1, "items": items}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"search-index.json -> {idx_path}  ({idx_path.stat().st_size // 1024}KB, {len(items):,} items)")

    # ── Write search.html ────────────────────────────────────────────────────
    TEMPLATE = Path(tmpl_path).read_text(encoding="utf-8")
    Path(out_path).write_text(TEMPLATE, encoding="utf-8")
    print(f"search.html -> {out_path}  ({Path(out_path).stat().st_size // 1024}KB)")
