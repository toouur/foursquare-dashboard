"""
Dashboard Builder v4
Reads checkins.csv → produces index.html
Run: python build_dashboard.py
"""
import csv, json, os
from datetime import datetime, timezone
from collections import Counter, defaultdict

INPUT_CSV   = "checkins.csv"
OUTPUT_HTML = "index.html"

# ── Hardcoded one-time fixes (by exact unix timestamp) ────────────
CHECKIN_COUNTRY_FIXES = {
    '1733952306': 'Argentina',   # Río de la Plata (was Uruguay)
    '1732467406': 'Brazil',      # Oceano Atlântico Sul
    '1676114577': 'Italy',       # Adriatic Sea (shout "with 塔妮雅")
}

# ── City merge map ─────────────────────────────────────────────────
CITY_MERGE = {
    # ── MINSK ─────────────────────────────────────────────────────
    'Минск':'Minsk','Мiнск':'Minsk','Мінск':'Minsk','минск':'Minsk','Mink':'Minsk',
    'Минск - Гродно':'Minsk','Minski Rayon':'Minsk','Минский р-н':'Minsk',
    'Минский район':'Minsk','Минская Обл.':'Minsk','Московский':'Minsk',
    # ── SAINT PETERSBURG ──────────────────────────────────────────
    'Санкт-Петербург':'Saint Petersburg','Санкт–Петербург':'Saint Petersburg',
    'Санкт-Петкрбург':'Saint Petersburg','Sankt-Peterburg':'Saint Petersburg',
    'город Кронштадт':'Saint Petersburg','Кронштадт':'Saint Petersburg',
    'Лахта':'Saint Petersburg','Петергоф':'Saint Petersburg','Peterhof':'Saint Petersburg',
    'Lomonosov':'Saint Petersburg','Ломоносов':'Saint Petersburg',
    'Pushkin':'Saint Petersburg','Sestroretsk':'Saint Petersburg',
    'Murino':'Saint Petersburg','Kudrovo':'Saint Petersburg',
    "Shlissel'burg":'Saint Petersburg','Округ Автово':'Saint Petersburg',
    'Лахтинский':'Saint Petersburg','gorod Petrodvorets':'Saint Petersburg',
    # ── MOSCOW ────────────────────────────────────────────────────
    'Москва':'Moscow','город Москва':'Moscow','Moskva':'Moscow',
    'Химки':'Moscow','Khimki':'Moscow','Zelenogradsk':'Moscow',
    'Зеленоградск':'Moscow','Odintsovo':'Moscow','Zelenogradskiy rayon':'Moscow',
    'Domodedovo':'Moscow','Domodedovsky Urban Okrug':'Moscow',
    'Vnukovo':'Moscow','Ryazanskiy rayon':'Moscow',
    'Kommunarka':'Moscow','Bulatnikovskoye':'Moscow',
    # ── ISTANBUL ──────────────────────────────────────────────────
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
    'Beyazit':'Istanbul','Topkapi':'Istanbul','Cankurtaran':'Istanbul',
    'Çengelköy':'Istanbul','İncirli':'Istanbul',
    # ── CAIRO ─────────────────────────────────────────────────────
    'Wust El-Balad':'Cairo','Kahire':'Cairo',
    'Downtown, Cairo, Muḩāfaz̧at al Qāhirah':'Cairo',
    'Nasr City, Muḩāfaz̧at al Qāhirah':'Cairo',
    'Heliopolis, Muḩāfaz̧at al Qāhirah':'Cairo',
    'Al Azbakīyah, Muḩāfaz̧at al Qāhirah':'Cairo',
    'Al Haram, Muḩāfaz̧at al Jīzah':'Cairo',
    'Mit Ruhaynah, Muḩāfaz̧at al Jīzah':'Cairo',
    'Al Azbakīyah':'Cairo','Al Zamalek':'Cairo','Zamalek':'Cairo',
    'Heliopolis':'Cairo','New Cairo':'Cairo','Zahraa El Maadi':'Cairo',
    'Dokki':'Cairo','Giza':'Cairo','Islamic Cairo':'Cairo',
    'Coptic Cairo':'Cairo','Mohandeseen':'Cairo','Mohandesin':'Cairo','Mohandessin':'Cairo',
    "Bāb ash Sha'rīyah":'Cairo',"Rawḑ al Faraj":'Cairo',
    "Al 'Abbāsīyah":'Cairo','Bāb al Lūq':'Cairo','Bab El Louk':'Cairo',
    'El Saiyida Zeinab':'Cairo','Al Sayeda Zaynab':'Cairo','Al Sayeda Zainab Sq.':'Cairo',
    'Al Nozha':'Cairo','Ghamrah':'Cairo','Ar Raml':'Cairo',
    'Madīnat al Muqaţţam':'Cairo','Al Muqattam':'Cairo','Al Moqattam':'Cairo','Muqattam':'Cairo',
    'Manshīyat Nāşir':'Cairo','Shubrā':'Cairo','Masr Al Qadima':'Cairo',
    'Al Gamaliyah':'Cairo','Al Gamaleyah':'Cairo','Jamāliyah':'Cairo',
    'Madīnat an Naşr':'Cairo','Al Matar':'Cairo',
    'Ad Doqi':'Cairo',"Al Ma'ādī":'Cairo',"Ma'ādī al Khabīrī":'Cairo',
    'Al Basātīn':'Cairo','Al Baghalah':'Cairo','Al Abageyah':'Cairo',
    'Al Haram':'Cairo','Abdeen':'Cairo','Dahshūr':'Cairo',
    'Al Khalīfah':'Cairo',"Al Qal'ah":'Cairo','Al Abājīyah':'Cairo',
    'Qesm Al Khalifah':'Cairo','Misr Al Qadimah':'Cairo','Misr El Qaddima':'Cairo',
    'Mohandiseen':'Cairo','Qasr El Dubara':'Cairo','Al Mosky':'Cairo',
    'Bab El Shaaria':'Cairo','Bab Al Shaaria':'Cairo','Bab Al Shearia':'Cairo',
    'Abbassiya':'Cairo','Al Zaher':'Cairo',"Al 'Ajūzah":'Cairo',
    'Gazirat Mit Oqbah':'Cairo',"Mīt 'Uqbah":'Cairo','Al Gomrok':'Cairo',
    'As Sayalah Sharq':'Cairo','Al Azaritah WA Ash Shatebi':'Cairo',
    'Shatby':'Cairo','Al-Shatbi':'Cairo',"Al 'Aţţārīn":'Cairo',
    'Ramsis':'Cairo','El Zabalin':'Cairo','El-Darb El-Ahmar':'Cairo',
    'Asad':'Cairo','Nazlat as Sammān':'Cairo','Kafr Nassar':'Cairo',
    'Nazlet El-Semman':'Cairo','Kafr Ghaţāţī':'Cairo','Shabrāmant':'Cairo',
    'Al Badrasheen':'Cairo','Helwan':'Cairo','El Moneeb':'Cairo',
    'Saqqara':'Cairo','El Sharq':'Cairo','Al Mansheyah Al Kubra':'Cairo',
    'Manshiyya':'Cairo','Worldwide':'Cairo','El-Gamaleya':'Cairo',
    'Attaba':'Cairo','Sheraton Al Matar':'Cairo','شيراتون المطار':'Cairo',
    'Ghayt Al Adah':'Cairo',"'Izbat al Baḩr":'Cairo',
    # ── JAKARTA ───────────────────────────────────────────────────
    'Jakarta Barat':'Jakarta','Jakarta Pusat':'Jakarta','Jakarta Utara':'Jakarta',
    'Jakarta Selatan':'Jakarta','Jakarta Timur':'Jakarta','Jakarta Kota':'Jakarta',
    'South Jakarta':'Jakarta','West Jakarta':'Jakarta','Central Jakarta':'Jakarta',
    'Jakarta Selata':'Jakarta','Kecamatan Pademangan':'Jakarta',
    'Kecamatan Gambir':'Jakarta','Kecamatan Tambora':'Jakarta',
    'Kecamatan Penjaringan':'Jakarta','Kecamatan Tebet':'Jakarta',
    'Kebayoran Baru':'Jakarta','Kecamatan Menteng':'Jakarta',
    'Kecamatan Kramat jati':'Jakarta','Kecamatan Sawah Besar':'Jakarta',
    'Sawah Besar':'Jakarta','Kecamatan Benda':'Jakarta','Menteng':'Jakarta',
    'Pademangan Barat':'Jakarta','Kecamatan Talang Ubi':'Jakarta',
    # ── BUENOS AIRES ──────────────────────────────────────────────
    'Ciudad Autónoma de Buenos Aire':'Buenos Aires',
    'Ciudad de Buenos AIres':'Buenos Aires',
    'Capital Federal':'Buenos Aires','San Telmo':'Buenos Aires',
    'Retiro':'Buenos Aires','Puerto Madero':'Buenos Aires',
    'Belgrano':'Buenos Aires','Belgrano C':'Buenos Aires',
    'Balvanera':'Buenos Aires','Chacarita':'Buenos Aires',
    'Flores':'Buenos Aires','AAQ':'Buenos Aires',
    # ── HANOI ─────────────────────────────────────────────────────
    'Hoàn Kiếm':'Hanoi','Tây Hồ':'Hanoi','Hai Bà Trưng':'Hanoi',
    'Ba Đình':'Hanoi','Đống Đa':'Hanoi','Hồ Tây':'Hanoi',
    'Hà Nội':'Hanoi','Hai Ba Trưng':'Hanoi','Sóc Sơn':'Hanoi','Thanh Xuân':'Hanoi',
    # ── HO CHI MINH CITY ──────────────────────────────────────────
    'Hồ Chí Minh':'Ho Chi Minh City','Thành phố Hồ Chí Minh':'Ho Chi Minh City',
    'Quận 3':'Ho Chi Minh City','Phường Phạm Ngũ Lão':'Ho Chi Minh City',
    'Hcm':'Ho Chi Minh City','Ho Chi Minh':'Ho Chi Minh City',
    'Hochiminh':'Ho Chi Minh City','Ben Nghe Ward':'Ho Chi Minh City',
    'Củ Chi':'Ho Chi Minh City','Tân Bình':'Ho Chi Minh City','Mekong':'Ho Chi Minh City',
    # ── BEIJING ───────────────────────────────────────────────────
    'Dongcheng':'Beijing','Dōngchéng':'Beijing','Xicheng Qu':'Beijing',
    '北京市朝陽區':'Beijing','Haidian':'Beijing','Jingshan':'Beijing',
    'Chē gōngzhuāng':'Beijing','Jinrongjie':'Beijing',
    # ── HONG KONG ─────────────────────────────────────────────────
    'Tsim Sha Tsui':'Hong Kong','Tsim Sha Tsui East':'Hong Kong',
    'Kowloon':'Hong Kong','Kowloon City':'Hong Kong',
    'Central':'Hong Kong','Central and Western District':'Hong Kong',
    'Central District':'Hong Kong','Mong Kok to Tsim Sha Tsui':'Hong Kong',
    '佐敦':'Hong Kong','Diamond Hill':'Hong Kong','Yau Ma Tei':'Hong Kong',
    'Sheung Wan':'Hong Kong','Tamar':'Hong Kong',
    # ── MACAU ─────────────────────────────────────────────────────
    'Macao':'Macau','大堂':'Macau','望德堂區':'Macau','澳門 Macau':'Macau',
    'Nossa Senhora do Carmo':'Macau','Sao Lazaro':'Macau',
    'Santo Antonio':'Macau','Sao Lourenco':'Macau','Taipa':'Macau',
    # ── SHANGHAI ──────────────────────────────────────────────────
    'Shanghái':'Shanghai',"Jing'an":'Shanghai','Baoshan':'Shanghai',
    'Hongkou':'Shanghai','Xuhui':'Shanghai',
    # ── PRAGUE ────────────────────────────────────────────────────
    'Praha1':'Prague','Praha 3':'Prague','Nové Město':'Prague',
    # ── ATHENS ────────────────────────────────────────────────────
    'Athens Center':'Athens','Athina':'Athens',
    # ── BUDAPEST ──────────────────────────────────────────────────
    'Budapest VIII. kerület':'Budapest','Budapest V. kerület':'Budapest',
    'Budapest XIV. kerület':'Budapest','Óbuda-Békásmegyer':'Budapest','Angyalföld':'Budapest',
    # ── COPENHAGEN ────────────────────────────────────────────────
    'København K':'Copenhagen','København NV':'Copenhagen',
    'København Ø':'Copenhagen','København V':'Copenhagen',
    'Christianshavn':'Copenhagen','Kopenhagen':'Copenhagen',
    'København':'Copenhagen','Hellerup':'Copenhagen',
    # ── BELGRADE ──────────────────────────────────────────────────
    'Beograd':'Belgrade','Belgrad':'Belgrade','Beograde':'Belgrade',
    'Zemun':'Belgrade','Opština Beograd-Zemun':'Belgrade',
    'Senjak':'Belgrade','Dorćol':'Belgrade',
    # ── SARAJEVO ──────────────────────────────────────────────────
    'Sarejevo':'Sarajevo','Sarajevo Canton':'Sarajevo',
    'Ilidža':'Sarajevo','Istočno Sarajevo':'Sarajevo',
    # ── KRAKOW ────────────────────────────────────────────────────
    'Kraków':'Krakow','Kraków-Śródmieście':'Krakow',
    # ── CHISINAU — two different Unicode chars that look identical ─
    # Canonical: Chișinău uses ș U+0219 (comma below)
    # Variant:   Chişinău uses ş U+015F (cedilla) — different code point!
    'Chişinău':'Chișinău',
    'Кишинёв':'Chișinău','Chisinau':'Chișinău',
    # ── KYIV ──────────────────────────────────────────────────────
    'Київ':'Kyiv',
    # ── MOGILEV ───────────────────────────────────────────────────
    'Могилёв':'Mogilev','Могилев':'Mogilev','Магiлёў':'Mogilev','Магілёў':'Mogilev',
    # ── VITEBSK ───────────────────────────────────────────────────
    'Витебск':'Vitebsk','Viciebsk':'Vitebsk','Vitsyebsk':'Vitebsk',
    # ── BOBRUISK ──────────────────────────────────────────────────
    'Бобруйск':'Bobruisk','Бабруйск':'Bobruisk',
    # ── BREST ─────────────────────────────────────────────────────
    'Брест':'Brest',
    # ── GOMEL ─────────────────────────────────────────────────────
    'Гомель':'Gomel','Homieĺ':'Gomel',
    # ── BORISOV ───────────────────────────────────────────────────
    'Борисов':'Borisov','Barysaŭ':'Borisov','Barysaw':'Borisov',
    # ── BATUMI ────────────────────────────────────────────────────
    'Батуми':'Batumi','Batum':'Batumi',
    # ── TASHKENT ──────────────────────────────────────────────────
    'Тошкент':'Tashkent','Ташкент':'Tashkent',
    # ── ALMATY ────────────────────────────────────────────────────
    'Алматы':'Almaty','Алма-Ата':'Almaty',
    # ── ASTANA ────────────────────────────────────────────────────
    'Нур-Султан':'Astana','Астана':'Astana','Nur-Sultan':'Astana',
    # ── WARSAW ────────────────────────────────────────────────────
    'Варшава':'Warsaw','Warszawa':'Warsaw',
    'Warszawa-Praga Północ':'Warsaw','Warsaw - Berlin':'Warsaw',
    # ── KAZAN ─────────────────────────────────────────────────────
    "Казань":"Kazan'",
    # ── RUSSIA ────────────────────────────────────────────────────
    'Новосибирск':'Novosibirsk','Красноярск':'Krasnoyarsk',
    'город Красноярск':'Krasnoyarsk','Екатеринбург':'Yekaterinburg',
    'город Омск':'Omsk','Ярославль':'Yaroslavl',
    # ── VILNIUS ───────────────────────────────────────────────────
    'Вильнюс':'Vilnius',
    # ── BUCHAREST ─────────────────────────────────────────────────
    'București':'Bucharest','Sector  1':'Bucharest',
    # ── SOFIA ─────────────────────────────────────────────────────
    'София':'Sofia',
    # ── SKOPJE ────────────────────────────────────────────────────
    'Скопје':'Skopje','Shkupi':'Skopje','Dolna Matka':'Skopje',
    # ── LVIV ──────────────────────────────────────────────────────
    'Львів':'Lviv',
    # ── DNIPRO ────────────────────────────────────────────────────
    'Днiпропетровськ':'Dnipro','Днiпро':'Dnipro','Днепропетровск':'Dnipro',
    # ── RIGA ──────────────────────────────────────────────────────
    'Jaunmārupe':'Riga','Mārupe':'Riga','Mārupes Novads 10/1':'Riga',
    'Rīga':'Riga','Centrs':'Riga','Lidosta "Rīga"':'Riga',
    # ── SMARHON ───────────────────────────────────────────────────
    "Smarhon'":'Smarhon',"Smarhoń":'Smarhon','Smarhonski Rayon':'Smarhon',
    # ── IASI ──────────────────────────────────────────────────────
    'Iaşi':'Iași','Iasi':'Iași',
    # ── YEREVAN ───────────────────────────────────────────────────
    'Yerevan, Sakharov Square':'Yerevan',
    # ── KUALA LUMPUR ──────────────────────────────────────────────
    '콸라룸푸르':'Kuala Lumpur','Куала-Лумпур':'Kuala Lumpur',
    'Kuala Lumpur City Center':'Kuala Lumpur',
    # ── LEUSENI ───────────────────────────────────────────────────
    'Albița - Leușeni':'Leușeni','Albița':'Leușeni',
    'Leuşeni':'Leușeni',           # cedilla ş → comma ș

    # ── CEDILLA FIXES — Romanian/Moldovan cities ──────────────────
    # Foursquare often stores ş (U+015F cedilla) instead of ș (U+0219 comma-below)
    # and ţ (U+0163 cedilla) instead of ț (U+021B comma-below)
    'Sighişoara':'Sighișoara',
    'Mediaş':'Mediaș',
    'Curtea de Argeş':'Curtea de Argeș',
    'Corneşti':'Cornești',
    'Focşani':'Focșani',
    'Botoşani':'Botoșani',
    'Giurgiuleşti':'Giurgiulești',
    'Dragomireşti':'Dragomirești',
    'Cimişlia':'Cimișlia',
    'Sighetu Marmaţiei':'Sighetu Marmației',
    'Coşniţa':'Coșnița',
    'Cârţişoara':'Cârțișoara',
    # ── MISC DROPS ────────────────────────────────────────────────
    'РФ / РБ':'','Одесская обл.':'',
    'Смоленская обл. - республика Беларусь':'','Смоленская обл.':'',
}

# ── Category groups for summary bar chart ─────────────────────────
CATEGORY_GROUPS = {
    'Food & Drink': [
        'Coffee Shop','Café','Cafeteria','Corporate Cafeteria','Bakery',
        'Restaurant','Fast Food Restaurant','Pizzeria','Burger Joint',
        'Asian Restaurant','Italian Restaurant','Chinese Restaurant',
        'Seafood Restaurant','Steakhouse','Sushi Restaurant','Breakfast Spot',
        'Diner','Food Court','Food Truck','Bar','Pub','Beer Bar','Wine Bar',
        'Cocktail Bar','Lounge','Nightclub','Brewery','Beer Garden','Dive Bar',
        'Sports Bar','Donut Shop','Ice Cream Shop','Dessert Shop','Juice Bar',
        'Tea Room','Sandwich Place','Noodle House','Ramen Restaurant',
        'Tapas Restaurant','BBQ Joint','Greek Restaurant','Turkish Restaurant',
        'French Restaurant','American Restaurant','Hookah Bar','College Cafeteria',
    ],
    'Transport': [
        'Metro Station','Bus Station','Bus Stop','Bus Line','Rail Station',
        'Train Station','Airport','Airport Terminal','Airport Gate','Airport Lounge',
        'Taxi','Ferry','Port','Harbor or Marina','Parking','Fuel Station',
        'Border Crossing','Tram Station','Platform','Airport Service',
        'International Airport',
    ],
    'Culture & Sights': [
        'Monument','Historic and Protected Site','Museum','Art Museum',
        'History Museum','Science Museum','Art Gallery','Theater','Cinema',
        'Concert Hall','Opera House','Cultural Center','Library',
        'Church','Cathedral','Mosque','Synagogue','Temple','Shrine',
        'Castle','Palace','Ruins','Archaeological Site','Outdoor Sculpture',
        'Fountain','Memorial Site','Landmark','Tourist Attraction',
        'Sculpture Garden','College Arts Building',
    ],
    'Nature & Outdoors': [
        'Park','Garden','Botanical Garden','National Park','Nature Preserve',
        'Beach','Lake','River','Mountain','Forest','Hiking Trail','Waterfall',
        'Scenic Lookout','Pedestrian Plaza','Plaza','Bridge','Waterfront',
        'Other Great Outdoors','Island',
    ],
    'Shopping': [
        'Supermarket','Department Store','Shopping Mall','Convenience Store',
        'Grocery Store','Market','Clothing Store','Electronics Store',
        'Bookstore','Pharmacy','Hardware Store','Furniture Store',
        'Miscellaneous Store','Gift Shop','Food and Beverage Retail',
        'Big Box Store','Liquor Store',
    ],
    'Home & Office': [
        'Home (private)','Apartment or Condo','Office','Corporate Office',
        'Coworking Space','Housing Development',
    ],
    'Hotels & Lodging': [
        'Hotel','Motel','Hostel','Bed & Breakfast','Resort','Inn','Vacation Rental',
    ],
    'Sport & Fitness': [
        'Gym','Fitness Center','Sports Club','Stadium','Arena',
        'Swimming Pool','Tennis Court','Basketball Court','Golf Course',
        'Yoga Studio','Spa',
    ],
}

def categorize(cat):
    for group, keywords in CATEGORY_GROUPS.items():
        if cat in keywords:
            return group
        cl = cat.lower()
        for kw in keywords:
            if kw.lower() in cl or cl in kw.lower():
                return group
    return None

EXPLORER_SKIP = {'City','Country','Worldwide','AH2','Road','Intersection',
                 'Neighborhood','Housing Development','Train'}


def process(csv_path):
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8')))

    for r in rows:
        ts = r.get('date','').strip()
        if ts in CHECKIN_COUNTRY_FIXES:
            r['country'] = CHECKIN_COUNTRY_FIXES[ts]
        city = r.get('city','').strip()
        r['city'] = CITY_MERGE.get(city, city)

    dates     = [datetime.fromtimestamp(int(r['date']), tz=timezone.utc)
                 for r in rows if r.get('date','').strip()]
    countries = Counter(r['country'] for r in rows if r.get('country','').strip())
    cities    = Counter(r['city']    for r in rows if r.get('city','').strip())
    venues_c  = Counter(r['venue']   for r in rows if r.get('venue','').strip())
    by_year   = Counter(d.year for d in dates)
    by_month  = Counter((d.year, d.month) for d in dates)
    by_hour   = Counter(d.hour for d in dates)
    by_dow    = Counter(d.weekday() for d in dates)

    cat_groups = Counter()
    for r in rows:
        cat = r.get('category','').strip()
        if cat:
            grp = categorize(cat)
            if grp:
                cat_groups[grp] += 1

    # Category explorer — top 55 raw categories, top 50 venues each
    raw_cats = Counter(r.get('category','') for r in rows if r.get('category','').strip())
    explorer_cats = [c for c,_ in raw_cats.most_common(200) if c not in EXPLORER_SKIP][:55]
    cat_venues = defaultdict(Counter)
    for r in rows:
        cat = r.get('category','').strip()
        venue = r.get('venue','').strip()
        if cat in explorer_cats and venue:
            cat_venues[cat][venue] += 1
    explorer = {cat: cat_venues[cat].most_common(50) for cat in explorer_cats}

    # Unique places by venue_id (matches Swarm count)
    seen_ids = set(); seen_coords = set(); unique_places = []
    for r in rows:
        vid = r.get('venue_id','').strip()
        lat = r.get('lat','').strip(); lng = r.get('lng','').strip()
        if vid:
            if vid not in seen_ids:
                seen_ids.add(vid)
                if lat and lng:
                    unique_places.append([float(lat), float(lng), r.get('venue','').strip()])
        elif lat and lng:
            key = (lat, lng)
            if key not in seen_coords:
                seen_coords.add(key)
                unique_places.append([float(lat), float(lng), r.get('venue','').strip()])
    unique_count = len(seen_ids) + len(seen_coords)

    country_vids = defaultdict(set)
    for r in rows:
        c = r.get('country','').strip()
        vid = r.get('venue_id','').strip() or r.get('venue','').strip()
        if c and vid: country_vids[c].add(vid)
    countries_by_venues = sorted([[c, len(v)] for c,v in country_vids.items()], key=lambda x:-x[1])

    all_coords = []
    for r in rows:
        try: all_coords.append([round(float(r['lat']),5), round(float(r['lng']),5)])
        except: pass

    print(f"  Cities: {len(cities)} | Countries: {len(countries)} | Unique: {unique_count}")
    return {
        'total': len(rows), 'date_min': str(min(dates).date()), 'date_max': str(max(dates).date()),
        'unique_places_count': unique_count,
        'by_year':   sorted([(str(k), v) for k,v in by_year.items()]),
        'by_month':  sorted([(f'{k[0]}-{k[1]:02d}', v) for k,v in by_month.items()]),
        'by_hour':   [(k, v) for k,v in sorted(by_hour.items())],
        'by_dow':    [(k, v) for k,v in sorted(by_dow.items())],
        'countries': [[c, n] for c,n in countries.most_common()],
        'countries_by_venues': countries_by_venues,
        'cities':    cities.most_common(),
        'venues':    venues_c.most_common(500),
        'cat_groups': cat_groups.most_common(),
        'explorer_cats': explorer_cats,
        'explorer':  {cat: explorer[cat] for cat in explorer_cats},
        'unique_places': unique_places,
        'all_coords': all_coords,
    }


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
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
.tabs{display:flex;gap:6px;flex-wrap:wrap;}
.tab{padding:5px 13px;border-radius:6px;font-family:'DM Mono',monospace;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.1em;cursor:pointer;border:1px solid var(--border);background:var(--card2);color:var(--text2);transition:all 0.2s;}
.tab.active{background:var(--gold);color:#0b0d13;border-color:var(--gold);}
.pane{display:none;}.pane.active{display:block;}
.search-box{width:100%;background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:8px 14px;color:var(--text);font-family:'DM Sans',sans-serif;font-size:0.85rem;margin-bottom:14px;outline:none;transition:border-color 0.2s;}
.search-box:focus{border-color:var(--gold);}
.cat-pills{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:18px;}
.cat-pill{padding:5px 14px;border-radius:20px;font-family:'DM Mono',monospace;font-size:0.6rem;text-transform:uppercase;letter-spacing:0.08em;cursor:pointer;border:1px solid var(--border);background:var(--card2);color:var(--text2);transition:all 0.2s;white-space:nowrap;}
.cat-pill.active{background:var(--teal);color:#0b0d13;border-color:var(--teal);}
.cat-pill:hover:not(.active){border-color:var(--teal);color:var(--teal);}
.bar-list{display:flex;flex-direction:column;gap:7px;max-height:520px;overflow-y:auto;padding-right:4px;}
.bar-list::-webkit-scrollbar{width:3px;}
.bar-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
.bar-row{display:grid;grid-template-columns:28px 1fr 120px 62px;align-items:center;gap:10px;}
.bar-row .rank{font-family:'DM Mono',monospace;font-size:0.58rem;color:var(--muted);text-align:right;}
.bar-row .name{font-size:0.81rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text);}
.bar-row .track{height:5px;background:var(--card2);border-radius:3px;overflow:hidden;}
.bar-row .fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--gold),var(--teal));}
.bar-row .cnt{font-family:'DM Mono',monospace;font-size:0.68rem;color:var(--muted);text-align:right;}
.bar-row.hidden{display:none;}
.country-table{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;}
.country-item{background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:9px 12px;display:flex;align-items:center;gap:8px;}
.ci-rank{font-family:'DM Mono',monospace;font-size:0.58rem;color:var(--muted);width:22px;flex-shrink:0;}
.ci-name{font-size:0.82rem;color:var(--text);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.ci-count{font-family:'DM Mono',monospace;font-size:0.72rem;color:var(--teal);flex-shrink:0;}
.map-wrap{position:relative;}
#map{height:600px;border-radius:8px;}
.map-status{position:absolute;bottom:14px;left:50%;transform:translateX(-50%);background:rgba(11,13,19,0.9);border:1px solid var(--border);border-radius:8px;padding:7px 16px;font-family:'DM Mono',monospace;font-size:0.68rem;color:var(--gold);pointer-events:none;transition:opacity 0.5s;white-space:nowrap;z-index:999;}
@media(max-width:900px){header{padding:28px 20px;flex-direction:column;align-items:flex-start;}.grid{padding:20px;grid-template-columns:1fr;}.card.full{grid-column:1;}.country-table{grid-template-columns:repeat(2,1fr);}}
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
  <div class="card"><div class="card-title">Check-ins by Year</div><canvas id="yearChart" height="210"></canvas></div>
  <div class="card"><div class="card-title">Monthly Trend</div><canvas id="monthChart" height="210"></canvas></div>
  <div class="card"><div class="card-title">Hour of Day</div><canvas id="hourChart" height="200"></canvas></div>
  <div class="card"><div class="card-title">Day of Week</div><canvas id="dowChart" height="200"></canvas></div>
  <div class="card full"><div class="card-title">Place Categories <em>&middot; by group</em></div><canvas id="catChart" height="85"></canvas></div>
  <div class="card full">
    <div class="card-title">Category Explorer <em>&middot; top 50 venues per category</em></div>
    <div class="cat-pills" id="catPills"></div>
    <div class="bar-list" id="explorerList"></div>
  </div>
  <div class="card full">
    <div class="card-title">
      All {{COUNTRIES}} Countries <em>&middot;</em>
      <div class="tabs"><div class="tab active" onclick="switchCountryTab('checkins',this)">By Check-ins</div><div class="tab" onclick="switchCountryTab('places',this)">By Unique Places</div></div>
    </div>
    <div class="pane active" id="pane-checkins"><div class="country-table" id="countriesCheckins"></div></div>
    <div class="pane" id="pane-places"><div class="country-table" id="countriesPlaces"></div></div>
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
    <div class="card-title">Map <em>&middot;</em>
      <div class="tabs">
        <div class="tab active" id="tabHeat" onclick="switchMap('heat')">Heatmap &middot; {{TOTAL}} check-ins</div>
        <div class="tab" id="tabDots" onclick="switchMap('dots')">Dots &middot; {{PLACES}} unique places</div>
      </div>
    </div>
    <div class="map-wrap"><div id="map"></div><div class="map-status" id="mapStatus">Loading heatmap...</div></div>
  </div>
</div>
<script>
const S={{STATS}};
Chart.defaults.color='#7a85a8';Chart.defaults.borderColor='#1e2335';
Chart.defaults.font.family="'DM Mono',monospace";Chart.defaults.font.size=11;
const PAL=['#e63946','#f4831f','#e8b86d','#f5d48a','#a8d8a8','#4ecdc4','#45b7d1','#96ceb4','#ff6b9d','#c44dff','#4d79ff','#ff4d4d','#ffaa00','#00c9a7'];
new Chart(document.getElementById('yearChart'),{type:'bar',data:{labels:S.by_year.map(x=>x[0]),datasets:[{data:S.by_year.map(x=>x[1]),backgroundColor:S.by_year.map((_,i)=>PAL[i%PAL.length]),borderRadius:5,borderWidth:0}]},options:{responsive:true,plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>' '+ctx.parsed.y.toLocaleString()+' check-ins'}}},scales:{x:{grid:{display:false}},y:{grid:{color:'#1a1e2e'}}}}});
new Chart(document.getElementById('monthChart'),{type:'line',data:{labels:S.by_month.map(x=>x[0]),datasets:[{data:S.by_month.map(x=>x[1]),borderColor:'#4ecdc4',backgroundColor:'rgba(78,205,196,0.07)',borderWidth:2,pointRadius:0,fill:true,tension:0.4}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{maxTicksLimit:12,maxRotation:0}},y:{grid:{color:'#1a1e2e'}}}}});
new Chart(document.getElementById('hourChart'),{type:'bar',data:{labels:S.by_hour.map(x=>x[0]+':00'),datasets:[{data:S.by_hour.map(x=>x[1]),backgroundColor:S.by_hour.map(x=>{const m=Math.max(...S.by_hour.map(y=>y[1]));return`rgba(78,205,196,${(0.2+0.8*(x[1]/m)).toFixed(2)})`;}),borderRadius:4,borderWidth:0}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{grid:{color:'#1a1e2e'}}}}});
const DOW=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
new Chart(document.getElementById('dowChart'),{type:'bar',data:{labels:S.by_dow.map(x=>DOW[x[0]]),datasets:[{data:S.by_dow.map(x=>x[1]),backgroundColor:S.by_dow.map(x=>x[0]>=4?'rgba(78,205,196,0.75)':'rgba(232,184,109,0.55)'),borderRadius:4,borderWidth:0}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{grid:{color:'#1a1e2e'}}}}});
const CC=['#e8b86d','#4ecdc4','#e63946','#45b7d1','#a8d8a8','#c44dff','#f4831f','#96ceb4'];
new Chart(document.getElementById('catChart'),{type:'bar',data:{labels:S.cat_groups.map(x=>x[0]),datasets:[{data:S.cat_groups.map(x=>x[1]),backgroundColor:S.cat_groups.map((_,i)=>CC[i%CC.length]),borderRadius:5,borderWidth:0}]},options:{indexAxis:'y',responsive:true,plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>' '+ctx.parsed.x.toLocaleString()+' check-ins'}}},scales:{x:{grid:{color:'#1a1e2e'}},y:{grid:{display:false}}}}});
// Explorer
const explorerData=S.explorer,explorerCats=S.explorer_cats;
let activeCat=explorerCats[0];
const pillsEl=document.getElementById('catPills');
explorerCats.forEach(cat=>{
  const p=document.createElement('div');
  p.className='cat-pill'+(cat===activeCat?' active':'');
  p.textContent=cat;
  p.onclick=()=>{document.querySelectorAll('.cat-pill').forEach(x=>x.classList.remove('active'));p.classList.add('active');activeCat=cat;renderExplorer(cat);};
  pillsEl.appendChild(p);
});
function renderExplorer(cat){
  const data=explorerData[cat]||[];
  const max=data.length?data[0][1]:1;
  document.getElementById('explorerList').innerHTML=data.map(([n,c],i)=>
    `<div class="bar-row"><span class="rank">#${i+1}</span><span class="name" title="${n}">${n}</span><div class="track"><div class="fill" style="width:${(c/max*100).toFixed(1)}%"></div></div><span class="cnt">${c.toLocaleString()}</span></div>`
  ).join('')||'<div style="color:var(--muted);padding:8px;font-size:0.85rem">No data</div>';
}
renderExplorer(activeCat);
// Countries
function makeCountryGrid(data,id){
  const n=data.length,cols=3,rows=Math.ceil(n/cols);
  const vis=[];
  for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){const i=c*rows+r;if(i<n)vis.push([data[i],i+1]);}
  document.getElementById(id).innerHTML=vis.map(([item,rank])=>
    `<div class="country-item"><span class="ci-rank">#${rank}</span><span class="ci-name" title="${item[0]}">${item[0]}</span><span class="ci-count">${item[1].toLocaleString()}</span></div>`
  ).join('');
}
makeCountryGrid(S.countries,'countriesCheckins');
makeCountryGrid(S.countries_by_venues,'countriesPlaces');
function switchCountryTab(name,el){
  document.querySelectorAll('.pane').forEach(p=>p.classList.remove('active'));
  document.getElementById('pane-'+name).classList.add('active');
  el.closest('.tabs').querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  el.classList.add('active');
}
function barList(id,data){
  const max=data[0][1];
  document.getElementById(id).innerHTML=data.map(([n,c],i)=>
    `<div class="bar-row" data-name="${n.toLowerCase().replace(/"/g,'')}"><span class="rank">#${i+1}</span><span class="name" title="${n}">${n}</span><div class="track"><div class="fill" style="width:${(c/max*100).toFixed(1)}%"></div></div><span class="cnt">${c.toLocaleString()}</span></div>`
  ).join('');
}
barList('citiesList',S.cities);
barList('venuesList',S.venues);
function filterList(id,q){document.getElementById(id).querySelectorAll('.bar-row').forEach(r=>r.classList.toggle('hidden',q.length>0&&!r.dataset.name.includes(q.toLowerCase())));}
// Map
const map=L.map('map',{preferCanvas:true}).setView([30,15],2);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{attribution:'&copy; OpenStreetMap &copy; CARTO',subdomains:'abcd',maxZoom:19}).addTo(map);
const status=document.getElementById('mapStatus');
let heatLayer=null,dotLayer=null,currentMode='heat';
const coords=S.all_coords;
const cellCount={};
coords.forEach(p=>{const k=Math.round(p[0]*20)+'_'+Math.round(p[1]*20);cellCount[k]=(cellCount[k]||0)+1;});
const sortedC=Object.values(cellCount).sort((a,b)=>a-b);
const p95=sortedC[Math.floor(sortedC.length*0.95)];
const heatPts=coords.map(p=>{const k=Math.round(p[0]*20)+'_'+Math.round(p[1]*20);return[p[0],p[1],Math.min(cellCount[k],p95)/p95];});
heatLayer=L.heatLayer(heatPts,{radius:14,blur:16,maxZoom:18,max:1.0,gradient:{'0.0':'#000033','0.25':'#0a3d6b','0.5':'#e8b86d','0.75':'#ff7700','1.0':'#ff1100'}}).addTo(map);
status.textContent='Heatmap · '+coords.length.toLocaleString()+' check-ins';
setTimeout(()=>status.style.opacity='0',2500);
function buildDots(){
  if(dotLayer)return;status.style.opacity='1';
  const pts=S.unique_places;let i=0;dotLayer=L.layerGroup();
  function chunk(){
    const end=Math.min(i+3000,pts.length);
    for(;i<end;i++)L.circleMarker([pts[i][0],pts[i][1]],{radius:3,color:'#e8b86d',fillColor:'#e8b86d',fillOpacity:0.65,weight:0}).bindTooltip(pts[i][2]||'',{direction:'top',opacity:0.9}).addTo(dotLayer);
    status.textContent='Plotting '+i.toLocaleString()+' / '+pts.length.toLocaleString()+'...';
    if(i<pts.length)requestAnimationFrame(chunk);
    else{if(currentMode==='dots')dotLayer.addTo(map);status.style.opacity='0';}
  }
  requestAnimationFrame(chunk);
}
function switchMap(mode){
  currentMode=mode;
  document.getElementById('tabHeat').classList.toggle('active',mode==='heat');
  document.getElementById('tabDots').classList.toggle('active',mode==='dots');
  if(mode==='heat'){if(dotLayer)map.removeLayer(dotLayer);heatLayer.addTo(map);status.textContent='Heatmap · '+coords.length.toLocaleString()+' check-ins';status.style.opacity='1';setTimeout(()=>status.style.opacity='0',2500);}
  else{map.removeLayer(heatLayer);if(dotLayer)dotLayer.addTo(map);else buildDots();}
}
</script>
</body>
</html>"""


def build(data, out_path):
    html = TEMPLATE
    html = html.replace('{{DATE_MIN}}',  data['date_min'])
    html = html.replace('{{DATE_MAX}}',  data['date_max'])
    html = html.replace('{{TOTAL}}',     f"{data['total']:,}")
    html = html.replace('{{COUNTRIES}}', str(len(data['countries'])))
    html = html.replace('{{CITIES}}',    f"{len(data['cities']):,}")
    html = html.replace('{{PLACES}}',    f"{data['unique_places_count']:,}")
    html = html.replace('{{UPDATED}}',   datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    html = html.replace('{{STATS}}',     json.dumps(data, ensure_ascii=False))
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Built → {out_path}  ({len(html)//1024:,} KB)")


if __name__ == '__main__':
    if not os.path.exists(INPUT_CSV):
        print(f"ERROR: {INPUT_CSV} not found."); exit(1)
    print(f"Processing {INPUT_CSV}...")
    data = process(INPUT_CSV)
    build(data, OUTPUT_HTML)
    print("Done!")