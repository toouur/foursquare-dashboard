# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_tips.py — Generate tips.html from data/tips.json."""
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Foursquare API returns country names in local language; map them to English
# so they match the CTRY_CODE flag dict and the rest of the dashboard.
CTRY_NORM = {
    "Беларусь": "Belarus",
    "Россия": "Russia",
    "Україна": "Ukraine",
    "Republica Moldova": "Moldova",
    "Italia": "Italy",
    "Polska": "Poland",
    "România": "Romania",
    "مصر": "Egypt",
    "Lietuva": "Lithuania",
    "Ўзбекістон": "Uzbekistan",
    "Ўзбекистон": "Uzbekistan",
    "Հայաստան": "Armenia",
    "Κύπρος": "Cyprus",
    "Ελλάδα": "Greece",
    "Қазақстан": "Kazakhstan",
    "Türkiye": "Turkey",
    "Србија": "Serbia",
    "Latvija": "Latvia",
    "Magyarország": "Hungary",
    "Hrvatska": "Croatia",
    "Deutschland": "Germany",
    "España": "Spain",
    "Danmark": "Denmark",
    "საქართველო": "Georgia",
    "Sverige": "Sweden",
    "Suomi": "Finland",
    "Norge": "Norway",
    "Česká republika": "Czech Republic",
    "Österreich": "Austria",
    "България": "Bulgaria",
    "Bosna i Hercegovina": "Bosnia and Herzegovina",
    "Slovenija": "Slovenia",
    "Slovensko": "Slovakia",
    "Кыргызстан": "Kyrgyzstan",
    "Severna Makedonija": "North Macedonia",
    "Eesti": "Estonia",
    "ایران": "Iran",
    "Việt Nam": "Vietnam",
}


def build_page(csv_path, config_dir, out_path, tmpl_path, tips_path=None, pix_url=""):
    TEMPLATE = Path(tmpl_path).read_text(encoding="utf-8")

    tips_file = Path(tips_path) if tips_path else Path(csv_path).parent / "tips.json"

    if not tips_file.exists():
        html = (TEMPLATE
                .replace("TIPS_DATA_PLACEHOLDER", "[]")
                .replace("TABS_DATA_PLACEHOLDER", "{}"))
        Path(out_path).write_text(html, encoding="utf-8")
        print(f"tips.html -> {out_path}  (no tips data — run fetch_tips.py first)")
        return

    # Load city_merge for city name normalization (same as checkins pipeline)
    city_merge: dict = {}
    config_path = Path(config_dir) if config_dir else Path(csv_path).parent.parent / "config"
    city_merge_path = config_path / "city_merge.yaml"
    if city_merge_path.exists():
        try:
            import yaml
            city_merge = yaml.safe_load(city_merge_path.read_text(encoding="utf-8")) or {}
        except Exception:
            pass

    tips = json.loads(tips_file.read_text(encoding="utf-8"))
    tips.sort(key=lambda t: -t.get("ts", 0))

    for t in tips:
        ts = t.get("ts", 0)
        if ts:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            t["date"] = dt.strftime("%d %b %Y")
            t["time"] = dt.strftime("%H:%M")
        else:
            t["date"] = ""
            t["time"] = ""

        # Normalized English country and city for tab filtering + flag display
        raw_country = t.get("country") or ""
        raw_city = t.get("city") or ""
        t["nc"] = CTRY_NORM.get(raw_country, raw_country)
        t["nci"] = city_merge.get(raw_city, raw_city)

        # Resolve photo URL
        if t.get("photo") and pix_url:
            t["photo_url"] = pix_url.rstrip("/") + "/" + t["photo"]
        else:
            t["photo_url"] = ""

    # Build TABS_DATA: {country: {total, cities: [[city, count], ...]}}
    country_counts: Counter = Counter()
    city_counts: dict[str, Counter] = defaultdict(Counter)
    for t in tips:
        nc = t["nc"]
        nci = t["nci"]
        if nc:
            country_counts[nc] += 1
            if nci:
                city_counts[nc][nci] += 1

    tabs: dict = {}
    for country, total in sorted(country_counts.items(), key=lambda x: -x[1]):
        cities = sorted(city_counts[country].items(), key=lambda x: -x[1])
        tabs[country] = {"total": total, "cities": cities}

    tabs_json = json.dumps(tabs, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tips_json = json.dumps(tips, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = (TEMPLATE
            .replace("TIPS_DATA_PLACEHOLDER", tips_json)
            .replace("TABS_DATA_PLACEHOLDER", tabs_json))
    Path(out_path).write_text(html, encoding="utf-8")
    size = Path(out_path).stat().st_size // 1024
    print(f"tips.html -> {out_path}  ({size}KB, {len(tips):,} tips)")
