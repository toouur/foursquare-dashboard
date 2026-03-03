"""
Dashboard Builder
Reads checkins.csv → produces index.html
Run: python build_dashboard.py
"""
import csv, json, os
from datetime import datetime, timezone
from collections import Counter

INPUT_CSV  = "checkins.csv"
OUTPUT_HTML = "index.html"

# ── City merge map: district/variant → canonical city name ────────
CITY_MERGE = {
    # Minsk
    'Минск':'Minsk','Мiнск':'Minsk','Мінск':'Minsk','минск':'Minsk',
    'Минск - Гродно':'Minsk','Minski Rayon':'Minsk','Минский р-н':'Minsk',
    'Минский район':'Minsk','Минская Обл.':'Minsk','Московский':'Minsk',
    # Saint Petersburg & suburbs
    'Санкт-Петербург':'Saint Petersburg','Санкт–Петербург':'Saint Petersburg',
    'Санкт-Петкрбург':'Saint Petersburg','Sankt-Peterburg':'Saint Petersburg',
    'город Кронштадт':'Saint Petersburg','Кронштадт':'Saint Petersburg',
    'Лахта':'Saint Petersburg','Петергоф':'Saint Petersburg',
    'Lomonosov':'Saint Petersburg','Ломоносов':'Saint Petersburg',
    'Pushkin':'Saint Petersburg','Peterhof':'Saint Petersburg',
    'Sestroretsk':'Saint Petersburg',
    # Moscow & suburbs
    'Москва':'Moscow','город Москва':'Moscow',
    'Химки':'Moscow','Khimki':'Moscow',
    'Zelenogradsk':'Moscow','Зеленоградск':'Moscow','Odintsovo':'Moscow',
    'Zelenogradskiy rayon':'Moscow',
    # Istanbul districts (exhaustive)
    'İstanbul':'Istanbul','Fatih':'Istanbul','Beyoğlu':'Istanbul',
    'Beşiktaş':'Istanbul','Kadıköy':'Istanbul','Üsküdar':'Istanbul',
    'Şişli':'Istanbul','Bakırköy':'Istanbul','Maltepe':'Istanbul',
    'Eminönü':'Istanbul','Sultanahmet':'Istanbul','Kağıthane':'Istanbul',
    'Sarıyer':'Istanbul','Arnavutköy':'Istanbul','Beykoz':'Istanbul',
    'Adalar':'Istanbul','Pendik':'Istanbul','Ataşehir':'Istanbul',
    'Bağcılar':'Istanbul','Esenler':'Istanbul','Kartal':'Istanbul',
    'Tuzla':'Istanbul','Ümraniye':'Istanbul','Sancaktepe':'Istanbul',
    'Sultangazi':'Istanbul','Eyüp':'Istanbul','Zeytinburnu':'Istanbul',
    'Avcılar':'Istanbul','Esenyurt':'Istanbul','Küçükçekmece':'Istanbul',
    'Büyükçekmece':'Istanbul','Çekmeköy':'Istanbul','Sultanbeyli':'Istanbul',
    'Başakşehir':'Istanbul','Bayrampaşa':'Istanbul','Gaziosmanpaşa':'Istanbul',
    'Güngören':'Istanbul','Istanbul Province':'Istanbul',
    'Vatan Caddesi,Fatih/İstanbul':'Istanbul','İstanbul Beşiktaş':'Istanbul',
    # Cairo & districts
    'Downtown, Cairo, Muḩāfaz̧at al Qāhirah':'Cairo',
    'Nasr City, Muḩāfaz̧at al Qāhirah':'Cairo',
    'Heliopolis, Muḩāfaz̧at al Qāhirah':'Cairo',
    'Al Azbakīyah, Muḩāfaz̧at al Qāhirah':'Cairo',
    'Al Haram, Muḩāfaz̧at al Jīzah':'Cairo',
    'Mit Ruhaynah, Muḩāfaz̧at al Jīzah':'Cairo',
    'Al Azbakīyah':'Cairo','Al Zamalek':'Cairo','Zamalek':'Cairo',
    'Heliopolis':'Cairo','New Cairo':'Cairo','Zahraa El Maadi':'Cairo',
    'Dokki':'Cairo','Giza':'Cairo','Islamic Cairo':'Cairo',
    'Coptic Cairo':'Cairo','Mohandeseen':'Cairo',
    "Bāb ash Sha'rīyah":'Cairo',"Rawḑ al Faraj":'Cairo',
    "Al 'Abbāsīyah":'Cairo','Bāb al Lūq':'Cairo',
    # Jakarta districts
    'Jakarta Barat':'Jakarta','Jakarta Pusat':'Jakarta','Jakarta Utara':'Jakarta',
    'Jakarta Selatan':'Jakarta','Jakarta Timur':'Jakarta','Jakarta Kota':'Jakarta',
    'South Jakarta':'Jakarta','West Jakarta':'Jakarta','Central Jakarta':'Jakarta',
    'Jakarta Selata':'Jakarta',
    # Hanoi districts
    'Hoàn Kiếm':'Hanoi','Tây Hồ':'Hanoi','Hai Bà Trưng':'Hanoi',
    'Ba Đình':'Hanoi','Đống Đa':'Hanoi','Hồ Tây':'Hanoi',
    # Ho Chi Minh City
    'Hồ Chí Minh':'Ho Chi Minh City','Thành phố Hồ Chí Minh':'Ho Chi Minh City',
    'Quận 3':'Ho Chi Minh City','Phường Phạm Ngũ Lão':'Ho Chi Minh City',
    # Beijing districts
    'Dongcheng':'Beijing','Xicheng Qu':'Beijing',
    '北京市朝陽區':'Beijing','Haidian':'Beijing',
    # Hong Kong districts
    'Tsim Sha Tsui':'Hong Kong','Tsim Sha Tsui East':'Hong Kong',
    'Kowloon':'Hong Kong','Kowloon City':'Hong Kong',
    'Central':'Hong Kong','Central and Western District':'Hong Kong',
    'Central District':'Hong Kong','Mong Kok to Tsim Sha Tsui':'Hong Kong',
    '佐敦':'Hong Kong',
    # Macau
    'Macao':'Macau','大堂':'Macau','望德堂區':'Macau','澳門 Macau':'Macau',
    # Kyiv
    'Київ':'Kyiv',
    # Chisinau
    'Кишинёв':'Chișinău','Chisinau':'Chișinău',
    # Mogilev
    'Могилёв':'Mogilev','Могилев':'Mogilev','Магiлёў':'Mogilev','Магілёў':'Mogilev',
    # Other Belarusian/CIS cities
    'Витебск':'Vitebsk','Бобруйск':'Bobruisk','Бабруйск':'Bobruisk',
    'Брест':'Brest','Гомель':'Gomel','Батуми':'Batumi','Львів':'Lviv',
    'Вильнюс':'Vilnius','Київ':'Kyiv',
    'Днiпропетровськ':'Dnipro','Днiпро':'Dnipro','Днепропетровск':'Dnipro',
    # Central Asia
    'Тошкент':'Tashkent','Ташкент':'Tashkent',
    'Алматы':'Almaty','Алма-Ата':'Almaty',
    'Нур-Султан':'Astana','Астана':'Astana',
    # Russia
    'Казань':"Kazan'",'Новосибирск':'Novosibirsk',
    'Красноярск':'Krasnoyarsk','город Красноярск':'Krasnoyarsk',
    'Екатеринбург':'Yekaterinburg',
    # Poland
    'Варшава':'Warsaw','Warszawa':'Warsaw','Warszawa-Praga Północ':'Warsaw',
    # Romania
    'București':'Bucharest','Sector  1':'Bucharest',
    # Other
    'Скопје':'Skopje','София':'Sofia',
    # Riga suburbs
    'Jaunmārupe':'Riga','Mārupe':'Riga','Mārupes Novads 10/1':'Riga',
    # Smarhon variants
    "Smarhon'":'Smarhon',"Smarhoń":'Smarhon','Smarhonski Rayon':'Smarhon',
    # Misc
    'Yerevan, Sakharov Square':'Yerevan',
    'РФ / РБ':'','Одесская обл.':'',
}

# ── Country merge map ─────────────────────────────────────────────
COUNTRY_MERGE = {
    'Uruguay': 'Argentina',
}

def fix_country(row):
    c = row['country'].strip()
    v = row['venue'].strip()
    if not c:
        if 'Atlântico' in v or 'Atlantic' in v: return 'Brazil'
        if 'Adriatic' in v: return 'Italy'
    return COUNTRY_MERGE.get(c, c)

def process(csv_path):
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8')))

    for r in rows:
        r['country'] = fix_country(r)
        city = r['city'].strip()
        r['city'] = CITY_MERGE.get(city, city)

    dates = [datetime.fromtimestamp(int(r['date']), tz=timezone.utc)
             for r in rows if r['date'].strip()]

    countries = Counter(r['country'] for r in rows if r['country'].strip())
    cities    = Counter(r['city']    for r in rows if r['city'].strip())
    venues    = Counter(r['venue']   for r in rows if r['venue'].strip())
    by_year   = Counter(d.year for d in dates)
    by_month  = Counter((d.year, d.month) for d in dates)
    by_hour   = Counter(d.hour for d in dates)
    by_dow    = Counter(d.weekday() for d in dates)

    all_coords = []
    for r in rows:
        try: all_coords.append([round(float(r['lat']),5), round(float(r['lng']),5)])
        except: pass

    seen = set()
    unique_places = []
    for r in rows:
        lat, lng = r['lat'].strip(), r['lng'].strip()
        if lat and lng:
            key = (lat, lng)
            if key not in seen:
                seen.add(key)
                unique_places.append([float(lat), float(lng), r['venue'].strip()])

    return {
        'total':         len(rows),
        'date_min':      str(min(dates).date()),
        'date_max':      str(max(dates).date()),
        'by_year':       sorted([(str(k), v) for k,v in by_year.items()]),
        'by_month':      sorted([(f'{k[0]}-{k[1]:02d}', v) for k,v in by_month.items()]),
        'by_hour':       [(k, v) for k,v in sorted(by_hour.items())],
        'by_dow':        [(k, v) for k,v in sorted(by_dow.items())],
        'countries':     [[c.strip(), n] for c,n in countries.most_common()],
        'cities':        cities.most_common(),
        'venues':        venues.most_common(500),
        'unique_places': unique_places,
        'all_coords':    all_coords,
    }

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Foursquare Check-in Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
<style>
:root{--bg:#0b0d13;--card:#12151f;--card2:#181c28;--border:#222738;--gold:#e8b86d;--teal:#4ecdc4;--muted:#4a5270;--text:#cdd5f0;--text2:#7a85a8;}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;}
header{padding:52px 56px 36px;background:linear-gradient(160deg,#0f1220 0%,#0b0d13 70%);border-bottom:1px solid var(--border);display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:28px;}
header h1{font-family:'Playfair Display',serif;font-size:clamp(2.8rem,5vw,5rem);font-weight:900;line-height:1;letter-spacing:-0.02em;background:linear-gradient(130deg,#f5d48a 0%,#e8b86d 45%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
header .sub{margin-top:8px;font-family:'DM Mono',monospace;font-size:0.72rem;letter-spacing:0.18em;text-transform:uppercase;color:var(--muted);}
.updated{font-family:'DM Mono',monospace;font-size:0.62rem;color:var(--muted);margin-top:4px;}
.kpis{display:flex;gap:36px;flex-wrap:wrap;align-items:flex-end;}
.kpi .num{font-family:'Playfair Display',serif;font-size:2.4rem;font-weight:700;color:var(--gold);line-height:1;}
.kpi .lbl{font-family:'DM Mono',monospace;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.14em;color:var(--muted);margin-top:5px;}
.grid{padding:36px 56px 72px;display:grid;grid-template-columns:1fr 1fr;gap:20px;max-width:1500px;}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:26px 30px;position:relative;overflow:hidden;}
.card::after{content:'';position:absolute;top:0;left:24px;right:24px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:0.35;}
.card.full{grid-column:1/-1;}
.card-title{font-family:'DM Mono',monospace;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.2em;color:var(--gold);margin-bottom:18px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.card-title em{opacity:0.5;font-style:normal;}
.map-tabs{display:flex;gap:8px;}
.map-tab{padding:5px 13px;border-radius:6px;font-family:'DM Mono',monospace;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.1em;cursor:pointer;border:1px solid var(--border);background:var(--card2);color:var(--text2);transition:all 0.2s;}
.map-tab.active{background:var(--gold);color:#0b0d13;border-color:var(--gold);}
.search-box{width:100%;background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:8px 14px;color:var(--text);font-family:'DM Sans',sans-serif;font-size:0.85rem;margin-bottom:14px;outline:none;transition:border-color 0.2s;}
.search-box:focus{border-color:var(--gold);}
.bar-list{display:flex;flex-direction:column;gap:7px;max-height:680px;overflow-y:auto;padding-right:4px;}
.bar-list::-webkit-scrollbar{width:3px;}
.bar-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
.bar-row{display:grid;grid-template-columns:28px 150px 1fr 62px;align-items:center;gap:10px;}
.bar-row .rank{font-family:'DM Mono',monospace;font-size:0.58rem;color:var(--muted);text-align:right;}
.bar-row .name{font-size:0.81rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text);}
.bar-row .track{height:5px;background:var(--card2);border-radius:3px;overflow:hidden;}
.bar-row .fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--gold),var(--teal));}
.bar-row .cnt{font-family:'DM Mono',monospace;font-size:0.68rem;color:var(--muted);text-align:right;}
.bar-row.hidden{display:none;}
.country-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;}
.country-item{background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:10px 14px;display:flex;align-items:center;gap:8px;}
.country-item .rank{font-family:'DM Mono',monospace;font-size:0.58rem;color:var(--muted);width:22px;flex-shrink:0;}
.country-item .cname{font-size:0.82rem;color:var(--text);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.country-item .ccount{font-family:'DM Mono',monospace;font-size:0.72rem;color:var(--teal);flex-shrink:0;}
.map-wrap{position:relative;}
#map{height:600px;border-radius:8px;}
.map-status{position:absolute;bottom:14px;left:50%;transform:translateX(-50%);background:rgba(11,13,19,0.9);border:1px solid var(--border);border-radius:8px;padding:7px 16px;font-family:'DM Mono',monospace;font-size:0.68rem;color:var(--gold);pointer-events:none;transition:opacity 0.5s;white-space:nowrap;z-index:999;}
@media(max-width:900px){header{padding:28px 20px;flex-direction:column;align-items:flex-start;}.grid{padding:20px;grid-template-columns:1fr;}.card.full{grid-column:1;}}
</style>
</head>
<body>
<header>
  <div>
    <h1>Check-in Journal</h1>
    <p class="sub">Foursquare &nbsp;&middot;&nbsp; {{DATE_MIN}} &ndash; {{DATE_MAX}}</p>
    <p class="updated">Updated {{UPDATED}}</p>
  </div>
  <div class="kpis">
    <div class="kpi"><div class="num">{{TOTAL}}</div><div class="lbl">Check-ins</div></div>
    <div class="kpi"><div class="num">{{COUNTRIES}}</div><div class="lbl">Countries</div></div>
    <div class="kpi"><div class="num">{{CITIES}}</div><div class="lbl">Cities</div></div>
    <div class="kpi"><div class="num">{{PLACES}}</div><div class="lbl">Unique Places</div></div>
  </div>
</header>
<div class="grid">
  <div class="card">
    <div class="card-title">Check-ins by Year</div>
    <canvas id="yearChart" height="210"></canvas>
  </div>
  <div class="card">
    <div class="card-title">Monthly Trend</div>
    <canvas id="monthChart" height="210"></canvas>
  </div>
  <div class="card">
    <div class="card-title">Hour of Day</div>
    <canvas id="hourChart" height="200"></canvas>
  </div>
  <div class="card">
    <div class="card-title">Day of Week</div>
    <canvas id="dowChart" height="200"></canvas>
  </div>
  <div class="card full">
    <div class="card-title">All {{COUNTRIES}} Countries <em>&middot; sorted by check-ins</em></div>
    <div class="country-grid" id="countriesGrid"></div>
  </div>
  <div class="card">
    <div class="card-title">All {{CITIES}} Cities <em>&middot; scroll &amp; search</em></div>
    <input class="search-box" type="text" placeholder="Search cities..." oninput="filterList('citiesList',this.value)">
    <div class="bar-list" id="citiesList"></div>
  </div>
  <div class="card">
    <div class="card-title">Top 500 Venues <em>&middot; scroll &amp; search</em></div>
    <input class="search-box" type="text" placeholder="Search venues..." oninput="filterList('venuesList',this.value)">
    <div class="bar-list" id="venuesList"></div>
  </div>
  <div class="card full">
    <div class="card-title">
      Map <em>&middot;</em>
      <div class="map-tabs">
        <div class="map-tab active" id="tabHeat" onclick="switchMap('heat')">Heatmap &middot; {{TOTAL}} check-ins</div>
        <div class="map-tab" id="tabDots" onclick="switchMap('dots')">Dots &middot; {{PLACES}} unique places</div>
      </div>
    </div>
    <div class="map-wrap">
      <div id="map"></div>
      <div class="map-status" id="mapStatus">Loading heatmap...</div>
    </div>
  </div>
</div>
<script>
const S = {{STATS}};
Chart.defaults.color='#7a85a8';Chart.defaults.borderColor='#1e2335';
Chart.defaults.font.family="'DM Mono',monospace";Chart.defaults.font.size=11;
const PALETTE=['#e63946','#f4831f','#e8b86d','#f5d48a','#a8d8a8','#4ecdc4','#45b7d1','#96ceb4','#ff6b9d','#c44dff','#4d79ff','#ff4d4d','#ffaa00','#00c9a7'];
new Chart(document.getElementById('yearChart'),{type:'bar',data:{labels:S.by_year.map(x=>x[0]),datasets:[{data:S.by_year.map(x=>x[1]),backgroundColor:S.by_year.map((_,i)=>PALETTE[i%PALETTE.length]),borderRadius:5,borderWidth:0}]},options:{responsive:true,plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>' '+ctx.parsed.y.toLocaleString()+' check-ins'}}},scales:{x:{grid:{display:false}},y:{grid:{color:'#1a1e2e'}}}}});
new Chart(document.getElementById('monthChart'),{type:'line',data:{labels:S.by_month.map(x=>x[0]),datasets:[{data:S.by_month.map(x=>x[1]),borderColor:'#4ecdc4',backgroundColor:'rgba(78,205,196,0.07)',borderWidth:2,pointRadius:0,fill:true,tension:0.4}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{maxTicksLimit:12,maxRotation:0}},y:{grid:{color:'#1a1e2e'}}}}});
new Chart(document.getElementById('hourChart'),{type:'bar',data:{labels:S.by_hour.map(x=>x[0]+':00'),datasets:[{data:S.by_hour.map(x=>x[1]),backgroundColor:S.by_hour.map(x=>{const m=Math.max(...S.by_hour.map(y=>y[1]));return `rgba(78,205,196,${(0.2+0.8*(x[1]/m)).toFixed(2)})`;}),borderRadius:4,borderWidth:0}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{grid:{color:'#1a1e2e'}}}}});
const DOW=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
new Chart(document.getElementById('dowChart'),{type:'bar',data:{labels:S.by_dow.map(x=>DOW[x[0]]),datasets:[{data:S.by_dow.map(x=>x[1]),backgroundColor:S.by_dow.map(x=>x[0]>=4?'rgba(78,205,196,0.75)':'rgba(232,184,109,0.55)'),borderRadius:4,borderWidth:0}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{grid:{color:'#1a1e2e'}}}}});
document.getElementById('countriesGrid').innerHTML=S.countries.map(([n,c],i)=>`<div class="country-item"><span class="rank">#${i+1}</span><span class="cname" title="${n}">${n}</span><span class="ccount">${c.toLocaleString()}</span></div>`).join('');
function barList(id,data){const max=data[0][1];document.getElementById(id).innerHTML=data.map(([name,count],i)=>`<div class="bar-row" data-name="${name.toLowerCase().replace(/"/g,'')}"><span class="rank">#${i+1}</span><span class="name" title="${name}">${name}</span><div class="track"><div class="fill" style="width:${(count/max*100).toFixed(1)}%"></div></div><span class="cnt">${count.toLocaleString()}</span></div>`).join('');}
barList('citiesList',S.cities);
barList('venuesList',S.venues);
function filterList(id,q){document.getElementById(id).querySelectorAll('.bar-row').forEach(r=>r.classList.toggle('hidden',q.length>0&&!r.dataset.name.includes(q.toLowerCase())));}
const map=L.map('map',{preferCanvas:true}).setView([30,15],2);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{attribution:'&copy; OpenStreetMap &copy; CARTO',subdomains:'abcd',maxZoom:19}).addTo(map);
const status=document.getElementById('mapStatus');
let heatLayer=null,dotLayer=null,currentMode='heat';
const coords=S.all_coords;
const cellCount={};
coords.forEach(p=>{const k=Math.round(p[0]*20)+'_'+Math.round(p[1]*20);cellCount[k]=(cellCount[k]||0)+1;});
const sorted=Object.values(cellCount).sort((a,b)=>a-b);
const p95=sorted[Math.floor(sorted.length*0.95)];
const heatPts=coords.map(p=>{const k=Math.round(p[0]*20)+'_'+Math.round(p[1]*20);return[p[0],p[1],Math.min(cellCount[k],p95)/p95];});
heatLayer=L.heatLayer(heatPts,{radius:14,blur:16,maxZoom:18,max:1.0,gradient:{'0.0':'#000033','0.25':'#0a3d6b','0.5':'#e8b86d','0.75':'#ff7700','1.0':'#ff1100'}}).addTo(map);
status.textContent='Heatmap · '+coords.length.toLocaleString()+' check-ins';
setTimeout(()=>status.style.opacity='0',2500);
function buildDots(){if(dotLayer)return;status.style.opacity='1';const pts=S.unique_places;let i=0;dotLayer=L.layerGroup();function chunk(){const end=Math.min(i+3000,pts.length);for(;i<end;i++)L.circleMarker([pts[i][0],pts[i][1]],{radius:3,color:'#e8b86d',fillColor:'#e8b86d',fillOpacity:0.65,weight:0}).bindTooltip(pts[i][2]||'',{direction:'top',opacity:0.9}).addTo(dotLayer);status.textContent='Plotting '+i.toLocaleString()+' / '+pts.length.toLocaleString()+'...';if(i<pts.length)requestAnimationFrame(chunk);else{if(currentMode==='dots')dotLayer.addTo(map);status.style.opacity='0';}}requestAnimationFrame(chunk);}
function switchMap(mode){currentMode=mode;document.getElementById('tabHeat').classList.toggle('active',mode==='heat');document.getElementById('tabDots').classList.toggle('active',mode==='dots');if(mode==='heat'){if(dotLayer)map.removeLayer(dotLayer);heatLayer.addTo(map);status.textContent='Heatmap · '+coords.length.toLocaleString()+' check-ins';status.style.opacity='1';setTimeout(()=>status.style.opacity='0',2500);}else{map.removeLayer(heatLayer);if(dotLayer)dotLayer.addTo(map);else buildDots();}}
</script>
</body>
</html>"""

def build(data, out_path):
    places_count = f"{len(data['unique_places']):,}"
    html = TEMPLATE
    html = html.replace('{{DATE_MIN}}',  data['date_min'])
    html = html.replace('{{DATE_MAX}}',  data['date_max'])
    html = html.replace('{{TOTAL}}',     f"{data['total']:,}")
    html = html.replace('{{COUNTRIES}}', str(len(data['countries'])))
    html = html.replace('{{CITIES}}',    f"{len(data['cities']):,}")
    html = html.replace('{{PLACES}}',    places_count)
    html = html.replace('{{UPDATED}}',   datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    html = html.replace('{{STATS}}',     json.dumps(data, ensure_ascii=False))
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Built → {out_path}  ({len(html)//1024:,} KB)")

if __name__ == '__main__':
    if not os.path.exists(INPUT_CSV):
        print(f"ERROR: {INPUT_CSV} not found. Run foursquare_checkins.py first.")
        exit(1)
    print(f"Processing {INPUT_CSV}...")
    data = process(INPUT_CSV)
    print(f"  {data['total']:,} check-ins · {len(data['countries'])} countries · {len(data['cities']):,} cities · {len(data['unique_places']):,} places")
    build(data, OUTPUT_HTML)
    print("Done!")
