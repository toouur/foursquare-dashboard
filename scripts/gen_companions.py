# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

#!/usr/bin/env python3
"""Generate companions.html on every build."""
import csv, json, logging, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


SOCIAL_KEYS = ('solo_vs_group_totals', 'companions', 'solo_vs_group_by_year', 'companion_countries')

log = logging.getLogger(__name__)


def load_companion_countries(config_dir) -> dict:
    """config/companion_countries.json: name -> home country (keys starting with _ ignored)."""
    p = Path(config_dir) / "companion_countries.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k: str(v).strip() for k, v in data.items()
            if not k.startswith("_") and str(v).strip()}


def companion_country(name: str, rows: list, overrides: dict) -> str:
    """The flag country for one companion.

    The configured home country wins. Otherwise the country MOST of the shared
    check-ins are in — not the latest one, which flipped Darya, Nikita and the
    Bryksenkovs to Moldova the moment home moved to Chișinău (29.07.2026).
    """
    if name in overrides:
        return overrides[name]
    c = Counter((r.get('country') or '').strip() for r in rows)
    c.pop('', None)
    return c.most_common(1)[0][0] if c else ''


def build_page(csv_path, config_dir, out_path, tmpl_path=None, social_data=None):
    TEMPLATE = Path(tmpl_path).read_text(encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).parent))
    from transform import load_mappings, apply_transforms, build_blank_city_resolver
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8')))
    mappings = load_mappings(config_dir)
    review_csv = Path(config_dir) / "city_merge_normalized_review.csv"
    blank_resolver = build_blank_city_resolver(review_csv)
    rows = apply_transforms(rows, mappings, blank_city_resolver=blank_resolver)
    name_to_uid = {}
    for r in rows:
        names = [n.strip() for n in r.get('with_name','').split(',') if n.strip()]
        ids = [i.strip() for i in r.get('with_id','').split(',') if i.strip()]
        for n, i in zip(names, ids):
            if n not in name_to_uid:
                name_to_uid[n] = i
        cb_name = r.get('created_by_name', '').strip()
        cb_id = r.get('created_by_id', '').strip()
        if cb_name and cb_id and cb_name not in name_to_uid:
            name_to_uid[cb_name] = cb_id
        ov_names = [n.strip() for n in r.get('overlaps_name','').split(',') if n.strip()]
        ov_ids = [i.strip() for i in r.get('overlaps_id','').split(',') if i.strip()]
        for n, i in zip(ov_names, ov_ids):
            if n not in name_to_uid:
                name_to_uid[n] = i
    comp_rows = defaultdict(list)
    for r in rows:
        seen_in_row = set()
        for name in [n.strip() for n in r.get('with_name','').split(',') if n.strip()]:
            comp_rows[name].append(r)
            seen_in_row.add(name)
        cb_name = r.get('created_by_name', '').strip()
        if cb_name and cb_name not in seen_in_row:
            comp_rows[cb_name].append(r)
            seen_in_row.add(cb_name)
        for name in [n.strip() for n in r.get('overlaps_name','').split(',') if n.strip() and n.strip() != '-']:
            if name not in seen_in_row:
                comp_rows[name].append(r)
                seen_in_row.add(name)
    comp_sorted = sorted(comp_rows.items(), key=lambda x: -len(x[1]))
    comp_data = []
    overrides = load_companion_countries(config_dir)
    for name, checkins in comp_sorted[:30]:
        ci = []
        for r in sorted(checkins, key=lambda x: int(x.get('date',0) or 0), reverse=True):
            ts = int(r.get('date',0) or 0)
            if not ts: continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            ci.append([ts, dt.strftime('%d %b %Y'), dt.strftime('%H:%M'),
                       r.get('venue',''), r.get('city',''), r.get('country',''),
                       r.get('category',''), r.get('venue_id','')])
        comp_data.append([name, len(checkins), ci, name_to_uid.get(name, ''),
                          companion_country(name, checkins, overrides)])
        if name not in overrides and len(checkins) >= 20:
            log.info("companions: %r (%d shared) has no home country in "
                     "companion_countries.json — flag from shared check-ins: %s",
                     name, len(checkins), comp_data[-1][4])
    comp_json = json.dumps(comp_data, ensure_ascii=False, separators=(',',':'))
    # The page reads only these keys; the caller hands over the whole stats dict
    # (unique_places + venues_heatmap alone were ~4.4 MB of dead weight inline).
    if social_data is not None:
        social_data = {k: social_data[k] for k in SOCIAL_KEYS if k in social_data}
    social_json = json.dumps(social_data, ensure_ascii=False, separators=(',',':')) if social_data is not None else 'null'
    html = TEMPLATE.replace('COMP_DATA_PLACEHOLDER', comp_json)
    html = html.replace('SOCIAL_STATS_PLACEHOLDER', social_json)
    Path(out_path).write_text(html, encoding='utf-8')
    print(f"companions.html -> {out_path}  ({Path(out_path).stat().st_size//1024}KB)")

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--input', required=True)
    p.add_argument('--config-dir', default='config')
    p.add_argument('--output', default='companions.html')
    a = p.parse_args()
    build_page(a.input, a.config_dir, a.output)
