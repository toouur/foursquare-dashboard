# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

#!/usr/bin/env python3
"""Generate venues.html - all unique venues page.

The page inlines only the TOP_N most visited venues (with a photo each) and
writes the whole list to venues_data.json, fetched the first time the visitor
searches, filters, re-sorts or scrolls past the top — inlining all ~34k venues
(3.5 MB) made the page take 12-16 s to open.
"""
import csv, hashlib, json, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

TOP_N = 500
PRIVATE_CATS = ("Home (private)",)   # never shown with a photo


def build_page(csv_path, config_dir, out_path, tmpl_path=None, photos_by_checkin=None, pix_url=""):
    TEMPLATE = Path(tmpl_path).read_text(encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).parent))
    from transform import load_mappings, apply_transforms
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8')))
    mappings = load_mappings(config_dir)
    rows = apply_transforms(rows, mappings)
    photos_by_checkin = photos_by_checkin or {}
    vm = defaultdict(lambda: {'count':0,'name':'','city':'','country':'','category':'','vid':'','years':set(),'photo':('', 0)})
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
        pics = photos_by_checkin.get(r.get('checkin_id', '')) or []
        if pics and ts >= vm[vid]['photo'][1]:          # the latest photo taken there
            vm[vid]['photo'] = (pics[0], ts)
    venues = sorted(vm.values(), key=lambda v: -v['count'])
    vout = [[v['name'],v['city'],v['country'],v['category'],v['count'],sorted(v['years']),v['vid']] for v in venues]
    body = json.dumps(vout, ensure_ascii=False, separators=(',',':'))
    out_dir = Path(out_path).parent
    (out_dir / "venues_data.json").write_text(body, encoding="utf-8")
    ver = hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]
    top = [row + [v['photo'][0] if v['category'] not in PRIVATE_CATS else '']
           for row, v in zip(vout[:TOP_N], venues[:TOP_N])]
    meta = {"total": len(vout), "ten_plus": sum(1 for v in vout if v[4] >= 10),
            "categories": len({v[3] for v in vout if v[3]}),
            "max_years": max((len(v[5]) for v in vout), default=0),
            "cats": sorted(((c, n) for c, n in Counter(v[3] for v in vout).items() if c), key=lambda x: -x[1])[:14]}
    html = (TEMPLATE.replace('VENUES_DATA_PLACEHOLDER', json.dumps(top, ensure_ascii=False, separators=(',',':')))
            .replace('VENUES_META_PLACEHOLDER', json.dumps(meta, ensure_ascii=False, separators=(',',':')))
            .replace('VENUES_VER_PLACEHOLDER', json.dumps(ver))
            .replace('PIX_URL_PLACEHOLDER', json.dumps(pix_url or "")))
    Path(out_path).write_text(html, encoding='utf-8')
    print(f"venues.html -> {out_path}  ({Path(out_path).stat().st_size//1024}KB, top {len(top)} of {len(venues)} venues; "
          f"venues_data.json {len(body)//1024}KB)")

