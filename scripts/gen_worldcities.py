#!/usr/bin/env python3
"""Generate world_cities.html on every build."""
import csv, json, re, sys
from pathlib import Path


def build_page(csv_path, config_dir, out_path, tmpl_path=None, cities_data=None):
    """cities_data: [[city, count, primary_country], ...] from metrics — same data as index.html.
    When provided, skip CSV re-read so counts and city names exactly match index.html
    (including blank-city-inferred rows). Falls back to CSV if not provided."""
    TEMPLATE = Path(tmpl_path).read_text(encoding="utf-8")
    if cities_data is not None:
        # Use pre-computed data from build.py (identical to index.html city counts)
        visited_json = json.dumps(
            {city: [cnt, country] for city, cnt, country in cities_data if city},
            ensure_ascii=False, separators=(',',':'))
    else:
        sys.path.insert(0, str(Path(__file__).parent))
        from transform import load_mappings, apply_transforms
        from collections import Counter, defaultdict
        rows = list(__import__('csv').DictReader(open(csv_path, encoding='utf-8')))
        mappings = load_mappings(config_dir)
        rows = apply_transforms(rows, mappings)
        city_counts = Counter(r.get('city','') for r in rows if r.get('city'))
        city_country_ctr: dict = defaultdict(Counter)
        for row in rows:
            cy = row.get('city', '').strip()
            co = row.get('country', '').strip()
            if cy and co:
                city_country_ctr[cy][co] += 1
        city_primary_country = {cy: ctr.most_common(1)[0][0] for cy, ctr in city_country_ctr.items()}
        visited_json = json.dumps(
            {city: [cnt, city_primary_country.get(city, '')] for city, cnt in city_counts.items()},
            ensure_ascii=False, separators=(',',':', ))

    # Extract WORLD_CITIES_100K from index.html.tmpl (sibling in templates dir)
    wc_data = '[]'
    index_tmpl = Path(tmpl_path).parent / 'index.html.tmpl'
    if index_tmpl.exists():
        tmpl_src = index_tmpl.read_text(encoding='utf-8')
        m = re.search(r'const WORLD_CITIES_100K=(\[[\s\S]*?\]);', tmpl_src, re.DOTALL)
        if m: wc_data = m.group(1)

    html = TEMPLATE.replace('WC_DATA_PLACEHOLDER', wc_data).replace('VISITED_DATA_PLACEHOLDER', visited_json)
    Path(out_path).write_text(html, encoding='utf-8')
    print(f"world_cities.html -> {out_path}  ({Path(out_path).stat().st_size//1024}KB)")

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--input', required=True)
    p.add_argument('--config-dir', default='config')
    p.add_argument('--output', default='world_cities.html')
    p.add_argument('--tmpl')
    a = p.parse_args()
    build_page(a.input, a.config_dir, a.output, a.tmpl)
