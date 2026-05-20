import csv, json, math, sys, re, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

CHECKINS = 'C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv'
CITY_FIXES = 'config/city_fixes.json'
COUNTRY_FIXES = 'config/country_fixes.json'
CITY_MERGE = 'config/city_merge.yaml'

with open(CITY_FIXES) as f:
    city_fixes = {k: v for k, v in json.load(f).items() if not k.startswith('_')}
with open(COUNTRY_FIXES) as f:
    country_fixes = {k: v for k, v in json.load(f).items() if not k.startswith('_')}

city_merge_map = {}
with open(CITY_MERGE, encoding='utf-8') as f:
    content = f.read()
cur_target = None
for line in content.splitlines():
    m = re.match(r'^  (\S.*?):\s*$', line)
    if m:
        cur_target = m.group(1).strip()
    m2 = re.match(r'^\s+-\s+(.+)$', line)
    if m2 and cur_target:
        city_merge_map[m2.group(1).strip()] = cur_target

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

rows = []
with open(CHECKINS, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        rows.append(row)

country_cities = {}
country_blanks = {}
known_cities = {}

for row in rows:
    ts = row.get('checkin_id', '')
    country = row.get('country', '')
    city = row.get('city', '')
    lat_s = row.get('lat', '')
    lng_s = row.get('lng', '')

    if ts in country_fixes:
        country = country_fixes[ts]

    if ts in city_fixes:
        city = city_fixes[ts]
    else:
        if city in city_merge_map:
            city = city_merge_map[city]

    if not country:
        continue

    if country not in country_cities:
        country_cities[country] = {}
    if country not in country_blanks:
        country_blanks[country] = []
    if country not in known_cities:
        known_cities[country] = {}

    if city:
        country_cities[country][city] = country_cities[country].get(city, 0) + 1
        if lat_s and lng_s:
            try:
                lat, lng = float(lat_s), float(lng_s)
                if city not in known_cities[country]:
                    known_cities[country][city] = []
                known_cities[country][city].append((lat, lng))
            except:
                pass
    else:
        try:
            lat, lng = float(lat_s), float(lng_s)
            country_blanks[country].append((ts, lat, lng))
        except:
            country_blanks[country].append((ts, None, None))

results = []
for country, blanks in country_blanks.items():
    if blanks:
        total = sum(country_cities.get(country, {}).values()) + len(blanks)
        results.append((country, len(blanks), total, blanks))

results.sort(key=lambda x: x[1])

for country, n_blanks, total, blanks in results:
    cities = known_cities.get(country, {})
    city_centroids = {}
    for city, coords in cities.items():
        lats = [c[0] for c in coords]
        lngs = [c[1] for c in coords]
        city_centroids[city] = (sum(lats)/len(lats), sum(lngs)/len(lngs))

    print(f'=== {country}: {n_blanks} blanks / {total} total ===')
    city_counts = dict(sorted(country_cities.get(country, {}).items(), key=lambda x: -x[1]))
    print(f'  cities: {city_counts}')
    for ts, lat, lng in blanks:
        if lat is None:
            print(f'  ts={ts} [no coords]')
            continue
        nearest = None
        best_dist = 9999
        for city, (clat, clng) in city_centroids.items():
            d = haversine(lat, lng, clat, clng)
            if d < best_dist:
                best_dist = d
                nearest = city
        print(f'  ts={ts} lat={lat:.4f} lng={lng:.4f} -> {nearest} ({best_dist:.1f}km)')
