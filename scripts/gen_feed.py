# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

#!/usr/bin/env python3
"""Generate feed.html with virtual scroll, local timezones, calendar jump."""
import csv, json, sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


COUNTRY_TZ = {'Belarus':'Europe/Minsk','Moldova':'Europe/Chisinau','Poland':'Europe/Warsaw','Ukraine':'Europe/Kyiv','Italy':'Europe/Rome','Romania':'Europe/Bucharest','Lithuania':'Europe/Vilnius','Germany':'Europe/Berlin','Türkiye':'Europe/Istanbul','Turkey':'Europe/Istanbul','China':'Asia/Shanghai','Spain':'Europe/Madrid','Georgia':'Asia/Tbilisi','France':'Europe/Paris','India':'Asia/Kolkata','Latvia':'Europe/Riga','Portugal':'Europe/Lisbon','Iran':'Asia/Tehran','Egypt':'Africa/Cairo','Japan':'Asia/Tokyo','United Kingdom':'Europe/London','Czechia':'Europe/Prague','Czech Republic':'Europe/Prague','Hungary':'Europe/Budapest','Austria':'Europe/Vienna','Switzerland':'Europe/Zurich','Netherlands':'Europe/Amsterdam','Belgium':'Europe/Brussels','Slovakia':'Europe/Bratislava','Bulgaria':'Europe/Sofia','Greece':'Europe/Athens','Croatia':'Europe/Zagreb','Serbia':'Europe/Belgrade','Estonia':'Europe/Tallinn','Finland':'Europe/Helsinki','Sweden':'Europe/Stockholm','Norway':'Europe/Oslo','Denmark':'Europe/Copenhagen','Kazakhstan':'Asia/Almaty','Uzbekistan':'Asia/Tashkent','Azerbaijan':'Asia/Baku','Armenia':'Asia/Yerevan','Israel':'Asia/Jerusalem','Jordan':'Asia/Amman','Thailand':'Asia/Bangkok','Vietnam':'Asia/Ho_Chi_Minh','Indonesia':'Asia/Jakarta','South Korea':'Asia/Seoul','Taiwan':'Asia/Taipei','Singapore':'Asia/Singapore','Malaysia':'Asia/Kuala_Lumpur','Pakistan':'Asia/Karachi','Nepal':'Asia/Kathmandu','Mongolia':'Asia/Ulaanbaatar','Morocco':'Africa/Casablanca','Tunisia':'Africa/Tunis','South Africa':'Africa/Johannesburg','New Zealand':'Pacific/Auckland','Holy See (Vatican City State)':'Europe/Rome','San Marino':'Europe/Rome','Monaco':'Europe/Monaco','Malta':'Europe/Malta','Cyprus':'Asia/Nicosia','Iceland':'Atlantic/Reykjavik','Ireland':'Europe/Dublin','Slovenia':'Europe/Ljubljana','North Macedonia':'Europe/Skopje','Albania':'Europe/Tirane','Montenegro':'Europe/Podgorica','Bosnia and Herzegovina':'Europe/Sarajevo','Kosovo':'Europe/Belgrade','Tajikistan':'Asia/Dushanbe','Kyrgyzstan':'Asia/Bishkek','Turkmenistan':'Asia/Ashgabat','Qatar':'Asia/Qatar','UAE':'Asia/Dubai','United Arab Emirates':'Asia/Dubai','Saudi Arabia':'Asia/Riyadh','Iraq':'Asia/Baghdad','Lebanon':'Asia/Beirut','Hong Kong':'Asia/Hong_Kong','Macao':'Asia/Macau','Macau':'Asia/Macau'}

def get_tz(country, lng):
    if country in COUNTRY_TZ: return COUNTRY_TZ[country]
    if country == 'Russia':
        if lng is None: return 'Europe/Moscow'
        if lng < 60: return 'Europe/Moscow'
        if lng < 73: return 'Asia/Yekaterinburg'
        if lng < 84: return 'Asia/Omsk'
        if lng < 98: return 'Asia/Krasnoyarsk'
        if lng < 115: return 'Asia/Irkutsk'
        if lng < 130: return 'Asia/Yakutsk'
        if lng < 142: return 'Asia/Vladivostok'
        return 'Asia/Magadan'
    if country == 'Brazil':
        if lng is None: return 'America/Sao_Paulo'
        return 'America/Fortaleza' if lng > -40 else 'America/Sao_Paulo' if lng > -48 else 'America/Manaus'
    if country == 'United States':
        if lng is None: return 'America/New_York'
        return 'America/New_York' if lng > -75 else 'America/Chicago' if lng > -90 else 'America/Denver' if lng > -110 else 'America/Los_Angeles'
    if country == 'Australia':
        if lng is None: return 'Australia/Sydney'
        return 'Australia/Perth' if lng < 129 else 'Australia/Darwin' if lng < 138 else 'Australia/Adelaide' if lng < 142 else 'Australia/Sydney'
    if lng is not None:
        off = round(lng / 15)
        try: ZoneInfo(f'Etc/GMT{-off:+d}'); return f'Etc/GMT{-off:+d}'
        except: pass
    return 'UTC'

def build_page(csv_path, config_dir, out_path, tmpl_path=None):
    TEMPLATE = Path(tmpl_path).read_text(encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).parent))
    from transform import load_mappings, apply_transforms
    from metrics import _localise
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8')))
    mappings = load_mappings(config_dir)
    rows = apply_transforms(rows, mappings)
    rows_sorted = sorted(rows, key=lambda r: int(r.get('date',0) or 0), reverse=True)
    tz_cache, feed, ym_index = {}, [], {}
    for r in rows_sorted:
        ts = int(r.get('date', 0) or 0)
        if not ts: continue
        country = r.get('country', '')
        lng = float(r['lng']) if r.get('lng') else None
        lat = float(r['lat']) if r.get('lat') else None
        k = (country, round(lng, 0) if lng else None)
        if k not in tz_cache: tz_cache[k] = get_tz(country, lng)
        try: dt = datetime.fromtimestamp(ts, tz=ZoneInfo(tz_cache[k]))
        except: dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        feed.append([ts, dt.strftime('%d %b %Y'), dt.strftime('%H:%M'),
                     r.get('venue',''), r.get('city',''), r.get('country',''),
                     r.get('category',''), r.get('venue_id',''),
                     round(lat,4) if lat else None, round(lng,4) if lng else None,
                     r.get('checkin_id','')])
        # Build YM index (UTC month for calendar)
        dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
        ym = f"{dt_utc.year}-{dt_utc.month:02d}"
        if ym not in ym_index:
            ym_index[ym] = len(feed) - 1
    feed_json = json.dumps(feed, ensure_ascii=False, separators=(',',':'))
    ym_json = json.dumps(ym_index, ensure_ascii=False, separators=(',',':'))
    html = TEMPLATE.replace('FEED_DATA_PLACEHOLDER', feed_json).replace('YM_INDEX_PLACEHOLDER', ym_json)
    Path(out_path).write_text(html, encoding='utf-8')
    print(f"feed.html -> {out_path}  ({Path(out_path).stat().st_size//1024}KB, {len(feed):,} check-ins)")
