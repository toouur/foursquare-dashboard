#!/usr/bin/env python3
"""Generate venues.html - top 500 venues page."""
import csv, json, sys
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path


def build_page(csv_path, config_dir, out_path, tmpl_path=None):
    TEMPLATE = Path(tmpl_path).read_text(encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).parent))
    from transform import load_mappings, apply_transforms
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8')))
    mappings = load_mappings(config_dir)
    rows = apply_transforms(rows, mappings)
    vm = defaultdict(lambda: {'count':0,'name':'','city':'','country':'','category':'','vid':'','years':set()})
    for r in rows:
        vid = r.get('venue_id','').strip()
        if not vid: continue
        vname = r.get('venue','').strip()
        if not vname: continue
        vm[vid]['count'] += 1
        vm[vid]['name'] = vname
        vm[vid]['city'] = r.get('city','')
        vm[vid]['country'] = r.get('country','')
        vm[vid]['category'] = r.get('category','')
        vm[vid]['vid'] = vid
        ts = int(r.get('date',0) or 0)
        if ts: vm[vid]['years'].add(datetime.fromtimestamp(ts, tz=timezone.utc).year)
    venues = sorted(vm.values(), key=lambda v: -v['count'])[:500]
    vout = [[v['name'],v['city'],v['country'],v['category'],v['count'],sorted(v['years']),v['vid']] for v in venues]
    vj = json.dumps(vout, ensure_ascii=False, separators=(',',':'))
    html = TEMPLATE.replace('VENUES_DATA_PLACEHOLDER', vj)
    Path(out_path).write_text(html, encoding='utf-8')
    print(f"venues.html -> {out_path}  ({Path(out_path).stat().st_size//1024}KB, {len(venues)} venues)")
