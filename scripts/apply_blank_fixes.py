"""
Read extract_blank_fixes.py output, parse the city groups, and append them
to config/city_fixes.json in the existing format.
"""
import re, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

INPUT = 'C:/Users/toouur/.claude/projects/c--Users-toouur-Documents-GitHub-foursquare-dashboard/e571609c-68bc-4a70-9576-8926fb73f1af/tool-results/bqcsfgmdr.txt'
CITY_FIXES = 'config/city_fixes.json'

# Parse extraction output
city_groups = []  # list of (city_name, [ids])
current_city = None
current_ids = []

city_hdr = re.compile(r'// === (.+?) \((\d+) entries\) ===')
entry_re  = re.compile(r'"([^"]+)": "([^"]+)"')

with open(INPUT, encoding='utf-8') as f:
    for line in f:
        m = city_hdr.search(line)
        if m:
            if current_city:
                city_groups.append((current_city, current_ids))
            current_city = m.group(1)
            current_ids = []
            continue
        m2 = entry_re.search(line)
        if m2 and current_city:
            current_ids.append(m2.group(1))

if current_city and current_ids:
    city_groups.append((current_city, current_ids))

# Load existing city_fixes.json as raw text to preserve structure
with open(CITY_FIXES, encoding='utf-8') as f:
    raw = f.read()

# Strip trailing }  and whitespace, then append
raw = raw.rstrip()
assert raw.endswith('}'), "Expected file to end with }"
raw = raw[:-1].rstrip()  # remove closing brace

# Add comma after last entry if missing
if not raw.endswith(','):
    raw += ','

# Build flat list of (key, value) pairs for all new entries
pairs = []
pairs.append(('_comment_blank_city_bulk_2026',
               'Bulk blank-city fixes extracted from analyze_blanks.py output; 82 cities, 1647 entries'))

for city, ids in city_groups:
    slug = re.sub(r'[^a-zA-Z0-9]', '_', city).lower().strip('_')
    pairs.append((f'_comment_blank_{slug}', f'{city}: {len(ids)} blank-city check-ins'))
    for ts in ids:
        pairs.append((ts, city))

# Emit all pairs with commas after every entry except the last
lines = ['']
for i, (k, v) in enumerate(pairs):
    comma = ',' if i < len(pairs) - 1 else ''
    lines.append(f'  "{k}": "{v}"{comma}')
lines.append('}')

new_raw = raw + '\n'.join(lines)

with open(CITY_FIXES, 'w', encoding='utf-8') as f:
    f.write(new_raw)

total = sum(len(ids) for _, ids in city_groups)
print(f"Done. Wrote {len(city_groups)} city sections, {total} entries to {CITY_FIXES}")
