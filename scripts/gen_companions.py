#!/usr/bin/env python3
"""Generate companions.html on every build."""
import csv, json, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def build_page(csv_path, config_dir, out_path, tmpl_path=None):
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
    comp_rows = defaultdict(list)
    for r in rows:
        for name in [n.strip() for n in r.get('with_name','').split(',') if n.strip()]:
            comp_rows[name].append(r)
    comp_sorted = sorted(comp_rows.items(), key=lambda x: -len(x[1]))
    comp_data = []
    for name, checkins in comp_sorted[:30]:
        ci = []
        for r in sorted(checkins, key=lambda x: int(x.get('date',0) or 0), reverse=True):
            ts = int(r.get('date',0) or 0)
            if not ts: continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            ci.append([ts, dt.strftime('%d %b %Y'), dt.strftime('%H:%M'),
                       r.get('venue',''), r.get('city',''), r.get('country',''),
                       r.get('category',''), r.get('venue_id','')])
        comp_data.append([name, len(checkins), ci, name_to_uid.get(name, '')])
    comp_json = json.dumps(comp_data, ensure_ascii=False, separators=(',',':'))
    html = TEMPLATE.replace('COMP_DATA_PLACEHOLDER', comp_json)
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
