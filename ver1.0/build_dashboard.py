"""
Dashboard Builder v5
Reads checkins.csv → produces index.html
Run: python build_dashboard.py
"""
import csv, json, os
from datetime import datetime, timezone
from collections import Counter, defaultdict

INPUT_CSV   = "checkins.csv"
OUTPUT_HTML = "index.html"

# ── Hardcoded one-time fixes (by exact unix timestamp) ─────────────────────────
CHECKIN_COUNTRY_FIXES = {
    '1733952306': 'Argentina',   # Río de la Plata (was Uruguay)
    '1732467406': 'Brazil',      # Oceano Atlântico Sul
    '1676114577': 'Italy',       # Adriatic Sea (shout "with 塔妮雅")
}

# ── City merge map ──────────────────────────────────────────────────────────────
# Format: 'variant_to_drop': 'canonical_name'
# Empty string = drop the city label (noise / uninformative)
CITY_MERGE = {

    # ── MINSK & SUBURBS ─────────────────────────────────────────────────────────
    'Минск':'Minsk','Мiнск':'Minsk','Мінск':'Minsk','минск':'Minsk','Mink':'Minsk',
    'Mińsk':'Minsk','Минск - Гродно':'Minsk','Minski Rayon':'Minsk',
    'Минский р-н':'Minsk','Минский район':'Minsk','Минская Обл.':'Minsk',
    # Minsk suburbs / districts (confirmed within ~20km of city center)
    'Московский':'Minsk',       # district in Minsk
    'Kopishche':'Minsk',        # NE suburb ~10km
    'Kopishcha':'Minsk',        # same place, variant spelling
    'Домошаны':'Minsk',         # ~15km NE of Minsk center
    'Samakhvalavichy':'Minsk',  # ~20km S of Minsk — check if you want separate
    'Прилуки':'Minsk',          # SW suburb ~15km
    'Priluki':'Minsk',          # same
    'Щомыслица':'Minsk',        # SW suburb ~15km
    'Городище':'Minsk',         # same coords as Haradzišča
    'Haradzišča':'Minsk',       # SW suburb ~15km
    'Kolodishchi':'Minsk',      # E suburb ~15km
    'Ozertso':'Minsk',          # SW ~15km
    'Zhdanovichy':'Minsk',      # NW ~10km
    'Borovaya':'Minsk',         # NE ~12km
    'Borovlyany':'Minsk',       # NE ~15km
    'Гонолес':'Minsk',          # NW ~15km
    'Astrashytski Haradok':'Minsk',  # NE ~20km — borderline
    'Valer\'yanovo':'Minsk',    # NE ~17km
    'Machulishchy':'Minsk',     # S ~15km
    'Мачулищи':'Minsk',         # same
    'Новая Боровая':'Minsk',    # NE ~12km
    'Новая Боровая':'Minsk',

    # ── SAINT PETERSBURG & SUBURBS ──────────────────────────────────────────────
    'Санкт-Петербург':'Saint Petersburg','Санкт–Петербург':'Saint Petersburg',
    'Санкт-Петкрбург':'Saint Petersburg','Sankt-Peterburg':'Saint Petersburg',
    'город Кронштадт':'Saint Petersburg','Кронштадт':'Saint Petersburg',
    'Лахта':'Saint Petersburg','Петергоф':'Saint Petersburg',
    'Peterhof':'Saint Petersburg','Lomonosov':'Saint Petersburg',
    'Ломоносов':'Saint Petersburg','Pushkin':'Saint Petersburg',
    'Sestroretsk':'Saint Petersburg','Murino':'Saint Petersburg',
    'Kudrovo':'Saint Petersburg',"Shlissel'burg":'Saint Petersburg',
    'Округ Автово':'Saint Petersburg','Лахтинский':'Saint Petersburg',
    'gorod Petrodvorets':'Saint Petersburg',
    'Kupchino Municipal Okrug':'Saint Petersburg',    # SPb district
    'Malaya Okhta Municipal Okrug':'Saint Petersburg',# SPb district
    'Ladozhskoye Ozero':'Saint Petersburg',           # Lake Ladoga, SPb outskirts
    'Kirovsk':'Saint Petersburg',                     # ~35km S of SPb on Neva

    # ── MOSCOW & SUBURBS ─────────────────────────────────────────────────────────
    'Москва':'Moscow','город Москва':'Moscow','Moskva':'Moscow',
    'Химки':'Moscow','Khimki':'Moscow','Zelenogradsk':'Moscow',
    'Зеленоградск':'Moscow','Odintsovo':'Moscow','Zelenogradskiy rayon':'Moscow',
    'Domodedovo':'Moscow','Domodedovsky Urban Okrug':'Moscow',
    'Vnukovo':'Moscow','Ryazanskiy rayon':'Moscow',
    'Kommunarka':'Moscow','Bulatnikovskoye':'Moscow',
    'Golitsino':'Moscow',   # ~45km W of Moscow, confirm if you want separate

    # ── ISTANBUL & DISTRICTS ─────────────────────────────────────────────────────
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

    # ── CAIRO & DISTRICTS ────────────────────────────────────────────────────────
    'Downtown':'Cairo',          # confirmed: all entries Egypt ~30.05,31.24
    'Bab Al Louq':'Cairo',       # confirmed: Egypt ~30.048,31.241
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
    'Al Haram':'Cairo','Abdeen':'Cairo','Abdin':'Cairo','Dahshūr':'Cairo',
    'Al Khalīfah':'Cairo',"Al Qal'ah":'Cairo','Al Qalaa':'Cairo','Al Abājīyah':'Cairo',
    'Qesm Al Khalifah':'Cairo','Misr Al Qadimah':'Cairo','Misr El Qaddima':'Cairo',
    'Misr al-Qadima':'Cairo','Misr Al Qadima':'Cairo',
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
    'Bulaq':'Cairo',            # confirmed: Egypt ~30.1, 31.2

    # ── JAKARTA & KECAMATAN ───────────────────────────────────────────────────────
    'Kecamatan Taman Sari':'Jakarta',  # confirmed: coords -6.16, 106.82
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
    'Tangerang':'Jakarta',      # ~25km W of Jakarta — check if you want separate
    'Benda':'Jakarta',          # Tangerang district, near Jakarta
    'Cilegon':'Jakarta',        # ~90km W — confirm if you want separate

    # ── BUENOS AIRES & DISTRICTS ─────────────────────────────────────────────────
    'Ciudad Autónoma de Buenos Aire':'Buenos Aires',
    'Ciudad de Buenos AIres':'Buenos Aires',
    'Capital Federal':'Buenos Aires','San Telmo':'Buenos Aires',
    'Retiro':'Buenos Aires','Puerto Madero':'Buenos Aires',
    'Belgrano':'Buenos Aires','Belgrano C':'Buenos Aires',
    'Balvanera':'Buenos Aires','Chacarita':'Buenos Aires',
    'Flores':'Buenos Aires','AAQ':'Buenos Aires',

    # ── MENDOZA (Argentina) ──────────────────────────────────────────────────────
    'Las Heras':'Mendoza',     # Mendoza suburb (-32.8, -68.8) confirmed
    'Villa Nueva':'Mendoza',   # confirmed -32.9, -68.8
    'M5500GMF':'Mendoza',      # postal code for Mendoza
    'Ciudad':'Mendoza',        # "Ciudad" -32.9, -68.8 = Mendoza

    # ── SANTIAGO, CHILE & SUBURBS ─────────────────────────────────────────────────
    'Providencia':'Santiago',    # Santiago commune
    'Recoleta':'Santiago',       # Santiago commune
    'Estación Central':'Santiago',
    'Pudahuel':'Santiago',       # airport commune
    'Quinta Normal':'Santiago',
    'Maipú':'Santiago',
    'La Florida':'Santiago',
    'Vitacura':'Santiago',
    'Santiago de Chile\u200b':'Santiago',  # has zero-width space
    'Metropolitana':'Santiago',  # Región Metropolitana (likely Santiago)
    'Puente Alto':'Santiago',    # Santiago metro

    # ── HANOI & DISTRICTS ────────────────────────────────────────────────────────
    'Hoàn Kiếm':'Hanoi','Tây Hồ':'Hanoi','Hai Bà Trưng':'Hanoi',
    'Ba Đình':'Hanoi','Đống Đa':'Hanoi','Hồ Tây':'Hanoi',
    'Hà Nội':'Hanoi','Hai Ba Trưng':'Hanoi','Sóc Sơn':'Hanoi','Thanh Xuân':'Hanoi',
    'Trúc Bạch':'Hanoi',  # Hanoi district, confirmed 21.0,105.8

    # ── HO CHI MINH CITY ─────────────────────────────────────────────────────────
    'Hồ Chí Minh':'Ho Chi Minh City','Thành phố Hồ Chí Minh':'Ho Chi Minh City',
    'Quận 3':'Ho Chi Minh City','Phường Phạm Ngũ Lão':'Ho Chi Minh City',
    'Hcm':'Ho Chi Minh City','Ho Chi Minh':'Ho Chi Minh City',
    'Hochiminh':'Ho Chi Minh City','Ben Nghe Ward':'Ho Chi Minh City',
    'Củ Chi':'Ho Chi Minh City','Tân Bình':'Ho Chi Minh City','Mekong':'Ho Chi Minh City',

    # ── PHNOM PENH ────────────────────────────────────────────────────────────────
    'Daun Penh':'Phnom Penh',   # central district, confirmed 11.6, 104.9
    'Phnom Pehn':'Phnom Penh',  # typo
    'Chroy Changvar':'Phnom Penh', # confirmed 11.6, 104.9

    # ── BEIJING & DISTRICTS ───────────────────────────────────────────────────────
    'Dongcheng':'Beijing','Dōngchéng':'Beijing','Xicheng Qu':'Beijing',
    '北京市朝陽區':'Beijing','Haidian':'Beijing','Jingshan':'Beijing',
    'Chē gōngzhuāng':'Beijing','Jinrongjie':'Beijing',
    'Langfang':'Beijing',    # ~60km SE of Beijing — borderline, check if you want separate
    'Badaling':'Beijing',    # Great Wall area, ~70km NW — check if you want separate

    # ── HARBIN ───────────────────────────────────────────────────────────────────
    'Ha Er Bin Shi':'Harbin',   # confirmed 45.8, 126.6
    'Haerbin Shi':'Harbin',     # same
    '哈尔滨':'Harbin',           # Chinese name

    # ── HONG KONG & DISTRICTS ─────────────────────────────────────────────────────
    'Tsim Sha Tsui':'Hong Kong','Tsim Sha Tsui East':'Hong Kong',
    'Kowloon':'Hong Kong','Kowloon City':'Hong Kong',
    'Central':'Hong Kong','Central and Western District':'Hong Kong',
    'Central District':'Hong Kong','Mong Kok to Tsim Sha Tsui':'Hong Kong',
    '佐敦':'Hong Kong','Diamond Hill':'Hong Kong','Yau Ma Tei':'Hong Kong',
    'Sheung Wan':'Hong Kong','Tamar':'Hong Kong',
    'The Peak':'Hong Kong',     # confirmed 22.3, 114.1 — Hong Kong landmark
    'Jordan':'Hong Kong',       # Jordan district, confirmed 22.3, 114.2
    'Se':'Hong Kong',           # likely Sé/Macau? coords 22.2, 113.6 → actually Macau SE

    # ── MACAU ─────────────────────────────────────────────────────────────────────
    'Macao':'Macau','大堂':'Macau','望德堂區':'Macau','澳門 Macau':'Macau',
    'Nossa Senhora do Carmo':'Macau','Sao Lazaro':'Macau',
    'Santo Antonio':'Macau','Sao Lourenco':'Macau','Taipa':'Macau',

    # ── SHANGHAI & DISTRICTS ──────────────────────────────────────────────────────
    'Shanghái':'Shanghai',"Jing'an":'Shanghai','Jing\'an':'Shanghai',
    'Baoshan':'Shanghai','Hongkou':'Shanghai','Xuhui':'Shanghai',
    'Guang Zhou Shi':'Guangzhou',  # confirmed 23.3, 113.3

    # ── GUANGZHOU ────────────────────────────────────────────────────────────────
    'Guang Zhou Shi':'Guangzhou',

    # ── SINGAPORE DISTRICTS ───────────────────────────────────────────────────────
    'Changi Village':'Singapore',  # confirmed 1.3, 104.0
    'Marina':'Singapore',
    'Thomson':'Singapore',
    'Clarke Quay':'Singapore',
    'City':'Singapore',           # "City" with Singapore country
    'Jalan Besar':'Singapore',
    'Sentosa Island':'Singapore', # Sentosa is part of Singapore city

    # ── DUBAI & DISTRICTS ─────────────────────────────────────────────────────────
    'Bur Dubai':'Dubai',          # confirmed Dubai district
    'Deira':'Dubai',              # confirmed Dubai district
    'Al Rigga':'Dubai',           # confirmed Dubai district
    'Al Karama':'Dubai',          # confirmed Dubai district
    'Palm Jumeirah':'Dubai',      # confirmed Dubai area
    'Dubai International Airport':'Dubai',  # Dubai airport

    # ── ATHENS & SUBURBS ──────────────────────────────────────────────────────────
    'Athina':'Athens','Athens Center':'Athens',
    'Spata':'Athens',             # Athens airport (Eleftherios Venizelos), confirmed
    'Néa Filadélfeia':'Athens',   # confirmed Athens suburb 38.0, 23.7
    'Νέας Φιλαδέλφειας':'Athens', # Greek spelling same
    'Nea Ionia':'Athens',         # Athens suburb
    'Acropolis':'Athens',         # landmark in Athens city
    'Thissio':'Athens',           # Athens neighborhood
    'Gyzi':'Athens',              # Athens neighborhood
    'Αργυρούπολης':'Athens',      # Greek spelling of Argyroupoli
    'Πετράλωνα':'Athens',         # Athens neighborhood
    'Ν. Κόσμος':'Athens',         # Athens neighborhood
    'Paianía':'Athens',           # Athens outskirts 37.9, 23.9 — confirm if you want separate
    'Kallithea':'Athens',         # Athens suburb
    'Nea Smyrni':'Athens',        # Athens suburb
    'Νέας Σμύρνης':'Athens',      # Greek spelling
    'Glyfada':'Athens',           # Athens coastal suburb
    'Elliniko':'Athens',          # Athens suburb (former airport area)
    'Argyroupoli':'Athens',       # Athens suburb
    'Alimos':'Athens',            # Athens suburb
    'Voula':'Athens',             # Athens coastal suburb
    'Marousi':'Athens',           # Athens northern suburb
    'Pangrati':'Athens',          # Athens neighborhood
    'Koukaki':'Athens',           # Athens neighborhood
    'Palaio Faliro':'Athens',     # Athens coastal suburb, 21 checkins
    'Koukaki':'Athens',

    # ── COPENHAGEN & AIRPORT ──────────────────────────────────────────────────────
    'København K':'Copenhagen','København NV':'Copenhagen',
    'København Ø':'Copenhagen','København V':'Copenhagen',
    'Christianshavn':'Copenhagen','Kopenhagen':'Copenhagen',
    'København':'Copenhagen','Hellerup':'Copenhagen',
    'Kastrup':'Copenhagen',       # Copenhagen airport (CPH), confirmed

    # ── ROME & AIRPORT ────────────────────────────────────────────────────────────
    'Fiumicino':'Rome',           # Rome airport (FCO), confirmed

    # ── VENICE & ISLANDS ─────────────────────────────────────────────────────────
    'Venezia':'Venice',
    'Burano':'Venice',            # Venice lagoon island
    'Murano':'Venice',            # Venice lagoon island
    'Lido':'Venice',              # Venice Lido island
    'Tessera':'Venice',           # Venice airport (VCE Marco Polo)

    # ── BOLOGNA & AIRPORT ─────────────────────────────────────────────────────────
    'Borgo Panigale':'Bologna',   # Bologna airport district, confirmed 44.5, 11.3

    # ── PORTO & SUBURBS ───────────────────────────────────────────────────────────
    'Vila Nova de Gaia':'Porto',  # confirmed 41.1, -8.6, across Douro from Porto
    'Matosinhos':'Porto',         # confirmed 41.2, -8.7, Porto suburb
    'Maia':'Porto',               # confirmed 41.2, -8.7, Porto suburb
    'Foz do Douro':'Porto',
    'Foz do douro':'Porto',
    'Vila Nova Gaia':'Porto',

    # ── LISBON & SUBURBS ─────────────────────────────────────────────────────────
    'Moscavide':'Lisbon',         # confirmed 38.8, -9.1, Lisbon suburb
    'Camarate':'Lisbon',          # confirmed 38.8, -9.1, near Lisbon airport
    'Algés':'Lisbon',             # confirmed 38.7, -9.2, Lisbon suburb
    'Cacilhas':'Lisbon',          # confirmed 38.7, -9.1, across Tagus
    'Almada':'Lisbon',            # confirmed 38.7, -9.1, across Tagus
    'Alfama':'Lisbon',            # Lisbon historic district

    # ── OSLO & AIRPORT ───────────────────────────────────────────────────────────
    'Gardermoen':'Oslo',          # Oslo airport (OSL), confirmed 60.2, 11.1

    # ── PRAGUE ────────────────────────────────────────────────────────────────────
    'Praha1':'Prague','Praha 3':'Prague','Nové Město':'Prague',

    # ── ATHENS ────────────────────────────────────────────────────────────────────
    # (see above section)

    # ── BUDAPEST ──────────────────────────────────────────────────────────────────
    'Budapest VIII. kerület':'Budapest','Budapest V. kerület':'Budapest',
    'Budapest XIV. kerület':'Budapest','Óbuda-Békásmegyer':'Budapest','Angyalföld':'Budapest',

    # ── BELGRADE & SUBURBS ────────────────────────────────────────────────────────
    'Beograd':'Belgrade','Belgrad':'Belgrade','Beograde':'Belgrade',
    'Zemun':'Belgrade','Opština Beograd-Zemun':'Belgrade',
    'Senjak':'Belgrade','Dorćol':'Belgrade',

    # ── SARAJEVO ──────────────────────────────────────────────────────────────────
    'Sarejevo':'Sarajevo','Sarajevo Canton':'Sarajevo',
    'Ilidža':'Sarajevo','Istočno Sarajevo':'Sarajevo',

    # ── KRAKOW ────────────────────────────────────────────────────────────────────
    'Kraków':'Krakow','Kraków-Śródmieście':'Krakow',
    'Краків':'Krakow',

    # ── DELHI / NEW DELHI ─────────────────────────────────────────────────────────
    'Delhi':'New Delhi',          # confirmed: all Delhi entries are New Delhi venues

    # ── KOLKATA ───────────────────────────────────────────────────────────────────
    'Hāora':'Kolkata',            # Howrah, confirmed 22.6, 88.3 — across Hooghly from Kolkata
    'Uttarpāra':'Kolkata',        # confirmed 22.7, 88.3, Kolkata metro area

    # ── MUMBAI ────────────────────────────────────────────────────────────────────
    'Thāne':'Mumbai',             # confirmed 19.2, 72.8, Mumbai metro
    'Borivli':'Mumbai',           # confirmed 19.2, 72.9, Mumbai suburb

    # ── BAKU & AIRPORT ────────────────────────────────────────────────────────────
    'Binǝ':'Baku',                # airport district (ǝ = U+01DD)
    'Binə':'Baku',                # same (ə = U+0259)
    'Şimal':'Baku',               # airport area / district
    'Bakı':'Baku',                # Azerbaijani spelling
    'Səbayıl':'Baku',             # city district

    # ── CHISINAU — two different Unicode chars that look identical ──────────────
    # Canonical: Chișinău uses ș U+0219 (comma below)
    # Variant:   Chişinău uses ş U+015F (cedilla) — different code point!
    'Chişinău':'Chișinău',
    'Кишинёв':'Chișinău','Chisinau':'Chișinău',

    # ── KYIV ──────────────────────────────────────────────────────────────────────
    'Київ':'Kyiv',

    # ── UZHHOROD ──────────────────────────────────────────────────────────────────
    'Ужгород':'Uzhhorod',         # Cyrillic spelling
    'Uzhhorodskyi raion':'Uzhhorod',  # district

    # ── MOGILEV ───────────────────────────────────────────────────────────────────
    'Могилёв':'Mogilev','Могилев':'Mogilev','Магiлёў':'Mogilev','Магілёў':'Mogilev',

    # ── VITEBSK ───────────────────────────────────────────────────────────────────
    'Витебск':'Vitebsk','Viciebsk':'Vitebsk','Vitsyebsk':'Vitebsk',

    # ── BOBRUISK ──────────────────────────────────────────────────────────────────
    'Бобруйск':'Bobruisk','Бабруйск':'Bobruisk',

    # ── BREST ─────────────────────────────────────────────────────────────────────
    'Брест':'Brest',

    # ── GOMEL ─────────────────────────────────────────────────────────────────────
    'Гомель':'Gomel','Homieĺ':'Gomel',

    # ── BORISOV ───────────────────────────────────────────────────────────────────
    'Борисов':'Borisov','Barysaŭ':'Borisov','Barysaw':'Borisov',

    # ── BATUMI ────────────────────────────────────────────────────────────────────
    'Батуми':'Batumi','Batum':'Batumi',

    # ── TASHKENT ──────────────────────────────────────────────────────────────────
    'Тошкент':'Tashkent','Ташкент':'Tashkent',

    # ── ALMATY ────────────────────────────────────────────────────────────────────
    'Алматы':'Almaty','Алма-Ата':'Almaty',

    # ── ASTANA ────────────────────────────────────────────────────────────────────
    'Нур-Султан':'Astana','Астана':'Astana','Nur-Sultan':'Astana',

    # ── WARSAW ────────────────────────────────────────────────────────────────────
    'Варшава':'Warsaw','Warszawa':'Warsaw',
    'Warszawa-Praga Północ':'Warsaw','Warsaw - Berlin':'Warsaw',

    # ── KAZAN ─────────────────────────────────────────────────────────────────────
    "Казань":"Kazan'",'Kazan':'Kazan\'',

    # ── RUSSIA ────────────────────────────────────────────────────────────────────
    'Новосибирск':'Novosibirsk','Красноярск':'Krasnoyarsk',
    'город Красноярск':'Krasnoyarsk','Екатеринбург':'Yekaterinburg',
    'город Омск':'Omsk','Ярославль':'Yaroslavl',
    'Тверь':'Tver',
    'Нижний Новгород':'Nizhny Novgorod',
    'Смоленск':'Smolensk',

    # ── VILNIUS ───────────────────────────────────────────────────────────────────
    'Вильнюс':'Vilnius',

    # ── BUCHAREST ─────────────────────────────────────────────────────────────────
    'București':'Bucharest','Sector  1':'Bucharest',

    # ── SOFIA ─────────────────────────────────────────────────────────────────────
    'София':'Sofia',

    # ── SKOPJE ────────────────────────────────────────────────────────────────────
    'Скопје':'Skopje','Shkupi':'Skopje','Dolna Matka':'Skopje',

    # ── LVIV ──────────────────────────────────────────────────────────────────────
    'Львів':'Lviv',

    # ── DNIPRO ────────────────────────────────────────────────────────────────────
    'Днiпропетровськ':'Dnipro','Днiпро':'Dnipro','Днепропетровск':'Dnipro',

    # ── RIGA & SUBURBS ────────────────────────────────────────────────────────────
    'Jaunmārupe':'Riga','Mārupe':'Riga','Mārupes Novads 10/1':'Riga',
    'Rīga':'Riga','Centrs':'Riga','Lidosta "Rīga"':'Riga',

    # ── SMARHON ───────────────────────────────────────────────────────────────────
    "Smarhon'":'Smarhon',"Smarhoń":'Smarhon','Smarhonski Rayon':'Smarhon',

    # ── IASI ──────────────────────────────────────────────────────────────────────
    'Iaşi':'Iași','Iasi':'Iași',

    # ── YEREVAN ───────────────────────────────────────────────────────────────────
    'Yerevan, Sakharov Square':'Yerevan',

    # ── KUALA LUMPUR & SUBURBS ────────────────────────────────────────────────────
    '콸라룸푸르':'Kuala Lumpur','Куала-Лумпур':'Kuala Lumpur',
    'Kuala Lumpur City Center':'Kuala Lumpur',
    'Batu Caves':'Kuala Lumpur',  # confirmed 3.2, 101.7, KL suburb
    'Petaling Jaya':'Kuala Lumpur', # confirmed 3.2, 101.6, KL suburb
    'Ampang':'Kuala Lumpur',       # confirmed 3.2, 101.8, KL suburb
    'Hulu Klang':'Kuala Lumpur',   # KL suburb
    'Gombak':'Kuala Lumpur',       # KL suburb

    # ── LEUSENI ───────────────────────────────────────────────────────────────────
    'Albița - Leușeni':'Leușeni','Albița':'Leușeni',
    'Leuşeni':'Leușeni',           # cedilla ş → comma ș

    # ── ROMANIAN/MOLDOVAN CEDILLA FIXES ──────────────────────────────────────────
    # Foursquare inconsistently uses ş (U+015F cedilla) instead of ș (U+0219 comma)
    # and ţ (U+0163 cedilla) instead of ț (U+021B comma)
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
    'Hârşova':'Hârșova',
    'Târgu Mureş':'Târgu Mureș',
    'Borşa':'Borșa',

    # ── MISC DROPS (noise, ambiguous, pure region names) ─────────────────────────
    'РФ / РБ':'','Одесская обл.':'',
    'Смоленская обл. - республика Беларусь':'','Смоленская обл.':'',
    'Красноярский край':'',
    'Ленинградская обл.':'',
    'Псковская область':'',
}

# ── Category groups for summary bar chart ──────────────────────────────────────
CATEGORY_GROUPS = {
    'Food & Drink': [
        'Coffee Shop','Café','Cafeteria','Corporate Cafeteria','Bakery',
        'Restaurant','Fast Food Restaurant','Pizzeria','Burger Joint',
        'Kebab Restaurant','Shawarma Restaurant','Doner Restaurant',
        'Italian Restaurant','Chinese Restaurant','Caucasian Restaurant',
        'Bar','Pub','Beer Bar','Wine Bar','Cocktail Bar','Lounge',
        'Beer Garden','Hookah Bar','Gastropub','Dive Bar','Sports Bar',
        'Donut Shop','Ice Cream Parlor','Dessert Shop','Juice Bar','Tea Room',
        'Sandwich Spot','Noodle Restaurant','Ramen Restaurant',
        'College Cafeteria','Food Court','Bistro','Diner','BBQ Joint',
        'Turkish Restaurant','French Restaurant','Greek Restaurant',
        'Romanian Restaurant','Eastern European Restaurant','Belarusian Restaurant',
        'Caucasian Restaurant','Modern European Restaurant',
        'Indian Restaurant','Asian Restaurant','Sushi Restaurant',
        'Vietnamese Restaurant','Middle Eastern Restaurant',
        'Breakfast Spot','Corporate Coffee Shop','Coffee Roaster',
        'Turkish Coffeehouse','Tapas Restaurant',
    ],
    'Transport': [
        'Metro Station','Bus Station','Bus Stop','Bus Line','Rail Station',
        'Airport','Airport Terminal','Airport Gate','Airport Lounge','Airport Service',
        'Taxi','Ferry','Harbor or Marina','Parking','Fuel Station',
        'Border Crossing','Tram Station','Platform','International Airport',
        'Light Rail Station','Boat or Ferry','Plane','Train',
        'Airport Ticket Counter','Airport Tram Station','Marine Terminal',
    ],
    'Culture & Sights': [
        'Monument','Historic and Protected Site','Museum','Art Museum',
        'History Museum','Science Museum','Art Gallery','Theater','Movie Theater',
        'Concert Hall','Opera House','Cultural Center','Library',
        'Church','Cathedral','Mosque','Synagogue','Temple','Shrine',
        'Buddhist Temple','Hindu Temple','Sikh Temple',
        'Castle','Palace','Ruins','Monastery',
        'Outdoor Sculpture','Sculpture Garden','Street Art','Public Art',
        'Fountain','Memorial Site','Landmark',
        'College Arts Building','Amphitheater',
    ],
    'Nature & Outdoors': [
        'Park','Garden','Botanical Garden','National Park','Nature Preserve',
        'Beach','Lake','River','Mountain','Forest','Hiking Trail','Waterfall',
        'Scenic Lookout','Pedestrian Plaza','Plaza','Bridge','Waterfront',
        'Other Great Outdoors','Island','Bike Trail','Campground',
        'State or Provincial Park','Urban Park','Canal',
    ],
    'Shopping': [
        'Supermarket','Department Store','Shopping Mall','Convenience Store',
        'Grocery Store','Market','Clothing Store','Electronics Store',
        'Bookstore','Pharmacy','Hardware Store','Furniture and Home Store',
        'Miscellaneous Store','Gift Store','Food and Beverage Retail',
        'Big Box Store','Liquor Store','Wine Store','Flea Market',
        'Farmers Market','Gourmet Store',
    ],
    'Home & Office': [
        'Home (private)','Apartment or Condo','Office','Corporate Office',
        'Coworking Space','Housing Development','Business Center',
    ],
    'Hotels & Lodging': [
        'Hotel','Motel','Hostel','Bed and Breakfast','Resort','Inn',
        'Vacation Rental','Lodging','Boarding House',
    ],
    'Sport & Fitness': [
        'Gym','Gym and Studio','Stadium','Arena','Swimming Pool',
        'Tennis Court','Basketball Court','Golf Course','Yoga Studio',
        'Spa','Soccer Field','Soccer Stadium','Tennis Stadium',
        'Track','Rock Climbing Spot',
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

# ── Category explorer groups (merged & display-ready) ──────────────────────────
# These are the pills shown in the Category Explorer section.
# Each group merges semantically related raw categories.
# Edit this dict to control what appears in the explorer.
from collections import OrderedDict
EXPLORER_GROUPS = OrderedDict([
    ('Bars, Pubs & Breweries', ['Bar','Beer Bar','Pub','Dive Bar','Sports Bar','Gastropub',
                            'Beer Garden','Irish Pub','Beach Bar',
                            'Brewery']),
    ('Cafés, Bakeries & Coffee',['Coffee Shop','Café','Tea Room','Turkish Coffeehouse',
                            'Corporate Coffee Shop','Coffee Roaster',
                            'Café, Coffee, and Tea House',
                            'Bakery','Pastry Shop','Donut Shop']),
    ('Parks',             ['Park','National Park','Urban Park',
                            'State or Provincial Park','Nature Preserve']),
    ('Plazas',            ['Plaza','Pedestrian Plaza']),
    ('Metro Stations',    ['Metro Station','Light Rail Station']),
    ('Monuments',         ['Monument','Memorial Site']),
    ('Historic Sites',    ['Historic and Protected Site','Ruins','Castle',
                            'Palace','Monastery']),
    ('Outdoor Sculpture', ['Outdoor Sculpture','Sculpture Garden',
                            'Street Art','Public Art']),
    ('Rail Stations',     ['Rail Station','Tram Station','Platform']),
    ('Bus Stops',         ['Bus Station','Bus Stop','Bus Line']),
    ('Bridges',           ['Bridge']),
    ('Churches & Mosques',['Church','Cathedral','Mosque','Synagogue','Temple',
                            'Shrine','Buddhist Temple','Hindu Temple','Sikh Temple']),
    ('Museums',           ['Museum','Art Museum','History Museum',
                            'Science Museum','Art Gallery']),
    ('Restaurants',       ['Restaurant','Fast Food Restaurant','Pizzeria',
                            'Kebab Restaurant','Shawarma Restaurant','Doner Restaurant',
                            'Burger Joint','Italian Restaurant','Caucasian Restaurant',
                            'Romanian Restaurant','Eastern European Restaurant',
                            'Belarusian Restaurant','Middle Eastern Restaurant',
                            'Greek Restaurant','Turkish Restaurant']),
    ('Airports',          ['Airport','International Airport','Airport Terminal',
                            'Airport Gate','Airport Service','Airport Lounge',
                            'Airport Ticket Counter','Airport Tram Station']),
    ('Hotels',            ['Hotel','Inn','Motel','Resort','Lodging','Boarding House']),
    ('Hostels',           ['Hostel','Bed and Breakfast']),
    ('Supermarkets',      ['Supermarket','Grocery Store','Convenience Store']),
    ('Gardens',           ['Garden','Botanical Garden']),
    ('Beaches',           ['Beach']),
    ('Scenic Lookouts',   ['Scenic Lookout']),
    ('Rivers & Lakes',    ['River','Lake','Canal','Reservoir']),
    ('Waterfront',        ['Waterfront','Pier']),
    ('Hiking',            ['Hiking Trail','Bike Trail']),
    ('Shopping Malls',    ['Shopping Mall','Department Store','Big Box Store']),
    ('Bookstores',        ['Bookstore','Used Bookstore']),
    ('Fountains',         ['Fountain']),
    ('Border Crossings',  ['Border Crossing']),
    ('Banks',             ['Bank']),
    ('Gyms',              ['Gym','Gym and Studio']),
    ('Wine & Cocktail',   ['Wine Bar','Cocktail Bar','Lounge','Hookah Bar',
                            'Speakeasy','Whisky Bar']),
    ('Concerts & Theater',['Concert Hall','Opera House','Theater','Movie Theater']),
    ('Nightclubs',        ['Night Club','Dance Studio','Rock Club','Karaoke Bar']),
    ('Markets',           ['Market','Farmers Market','Flea Market','Gourmet Store']),
    ('Taxis',             ['Taxi','Taxi Stand']),
    ('Fuel Stations',     ['Fuel Station']),
    ('Clothing Stores',   ['Clothing Store','Shoe Store','Boutique']),
    ('Pharmacies',        ['Pharmacy','Drugstore']),
    ('Liquor Stores',     ['Liquor Store','Wine Store']),
    ('Campgrounds',       ['Campground','RV Park','Trailer Park']),
    ('Mountains',         ['Mountain']),
    ('Islands',           ['Island']),
    ('Cafeterias',        ['Corporate Cafeteria','Cafeteria','College Cafeteria']),
    ('Offices',           ['Office','Corporate Office','Coworking Space']),
    ('Parking',           ['Parking']),
    ('Cemeteries',        ['Cemetery']),
    ('Harbors & Marinas', ['Harbor or Marina']),
    ('Universities',      ['University','College Arts Building',
                            'College Academic Building','College Technology Building',
                            'College Library','College Residence Hall']),
    ('Stadiums',          ['Stadium','Soccer Stadium','Tennis Stadium',
                            'Hockey Stadium','Soccer Field']),
    ('Bakery / Sweets',   ['Ice Cream Parlor','Dessert Shop','Candy Store',
                            'Gelato Shop','Cupcake Shop','Chocolate Store']),
    ('Spas & Baths',      ['Spa','Bath House','Sauna']),
    ('Swimming Pools',    ['Swimming Pool','Gym Pool']),
])


def detect_trips(rows, home_city='Minsk', min_checkins=5):
    """Detect trips as consecutive sequences of check-ins outside home_city."""
    valid = [r for r in rows if r.get('date','').strip()]
    valid.sort(key=lambda r: int(r['date']))
    raw_trips, current = [], []
    for r in valid:
        if r.get('city','').strip() != home_city:
            current.append(r)
        else:
            if current: raw_trips.append(current)
            current = []
    if current: raw_trips.append(current)

    result = []
    for trip_rows in raw_trips:
        if len(trip_rows) < min_checkins:
            continue
        dates      = [datetime.fromtimestamp(int(r['date']), tz=timezone.utc) for r in trip_rows]
        countries_c = Counter(r.get('country','').strip() for r in trip_rows if r.get('country','').strip())
        cities_c    = Counter(r.get('city','').strip()    for r in trip_rows if r.get('city','').strip())
        top_countries = [c for c,_ in countries_c.most_common()]
        top_cities    = [c for c,_ in cities_c.most_common(3)]
        if len(top_countries) == 1:
            name = f"{top_cities[0] if top_cities else top_countries[0]}, {top_countries[0]}"
        elif len(top_countries) == 2:
            name = ' & '.join(top_countries[:2])
        else:
            name = f"{top_countries[0]} + {top_countries[1]} + {len(top_countries)-2} more"
        duration = (dates[-1].date() - dates[0].date()).days + 1
        # Build checkin list
        checkins = []
        for r in trip_rows:
            d = datetime.fromtimestamp(int(r['date']), tz=timezone.utc)
            try: lat = round(float(r.get('lat','')), 5)
            except: lat = None
            try: lng = round(float(r.get('lng','')), 5)
            except: lng = None
            checkins.append({'ts': int(r['date']), 'date': d.strftime('%Y-%m-%d'),
                'time': d.strftime('%H:%M'), 'datetime': d.strftime('%d %b %Y, %H:%M'),
                'venue': r.get('venue','').strip(), 'venue_id': r.get('venue_id','').strip(),
                'city': r.get('city','').strip(), 'country': r.get('country','').strip(),
                'category': r.get('category','').strip(), 'lat': lat, 'lng': lng})
        # Unique places (for map dots)
        seen_v = set(); unique_pts = []
        for r in trip_rows:
            vid = r.get('venue_id','').strip()
            if vid and vid not in seen_v:
                seen_v.add(vid)
                try: unique_pts.append([round(float(r['lat']),5), round(float(r['lng']),5), r.get('venue','').strip()])
                except: pass
        trip_cats = Counter(r.get('category','').strip() for r in trip_rows if r.get('category','').strip())
        result.append({
            'name': name, 'start_date': str(dates[0].date()), 'end_date': str(dates[-1].date()),
            'start_ts': int(trip_rows[0]['date']), 'start_year': dates[0].year,
            'duration': duration, 'countries': top_countries,
            'cities': [c for c,_ in cities_c.most_common()],
            'checkin_count': len(trip_rows), 'unique_places': len(seen_v),
            'checkins': checkins, 'coords': [[c['lat'],c['lng']] for c in checkins if c['lat'] and c['lng']],
            'unique_pts': unique_pts, 'top_cats': [[c,n] for c,n in trip_cats.most_common(10)],
        })
    result.sort(key=lambda t: t['start_ts'])
    # Assign sequential IDs
    for i, t in enumerate(result): t['id'] = i + 1
    return result


def process(csv_path):
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8')))

    # Apply country fixes then city merges
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

    # ── Venues: unique by venue_id, with city ──────────────────────────────────
    venue_by_id = {}  # venue_id -> {name, city, count}
    for r in rows:
        vid  = r.get('venue_id','').strip()
        name = r.get('venue','').strip()
        city = r.get('city','').strip()
        if not (vid and name):
            continue
        if vid not in venue_by_id:
            venue_by_id[vid] = {'name': name, 'city': city, 'count': 0}
        venue_by_id[vid]['count'] += 1
    venues_top500 = sorted(venue_by_id.values(), key=lambda x: -x['count'])[:500]
    venues_list   = [[v['name'], v['count'], v['city']] for v in venues_top500]

    # ── Companions (split combined entries) ───────────────────────────────────
    comp_raw = Counter()
    for r in rows:
        raw = r.get('with_name','').strip()
        if not raw: continue
        for name in [n.strip() for n in raw.replace(' ,',',').split(',')]:
            if name: comp_raw[name] += 1
    companions = [[n, c] for n, c in comp_raw.most_common(30)]

    # ── Year heatmap (day → count) ────────────────────────────────────────────
    heatmap = defaultdict(dict)
    for r in rows:
        try:
            d = datetime.fromtimestamp(int(r['date']), tz=timezone.utc)
            key = d.strftime('%Y-%m-%d')
            yr  = str(d.year)
            heatmap[yr][key] = heatmap[yr].get(key, 0) + 1
        except: pass
    heatmap = dict(sorted(heatmap.items()))

    # ── Discovery rate (new vs repeat per month) ──────────────────────────────
    _seen = set(); _mon = defaultdict(lambda: [0, 0])
    for r in sorted(rows, key=lambda r: int(r.get('date','0') or '0')):
        vid = r.get('venue_id','').strip() or r.get('venue','').strip()
        if not vid: continue
        try:
            d   = datetime.fromtimestamp(int(r['date']), tz=timezone.utc)
            key = f"{d.year}-{d.month:02d}"
        except: continue
        if vid not in _seen: _seen.add(vid); _mon[key][0] += 1
        else: _mon[key][1] += 1
    discovery_rate = sorted([[k, v[0], v[1]] for k, v in _mon.items()])

    # ── Venue loyalty (seen in 3+ different years) ────────────────────────────
    _vy = defaultdict(set); _vi = {}; _vc = defaultdict(int)
    for r in rows:
        vid = r.get('venue_id','').strip()
        if not vid: continue
        try: yr = datetime.fromtimestamp(int(r['date']), tz=timezone.utc).year
        except: continue
        _vy[vid].add(yr); _vc[vid] += 1
        if vid not in _vi: _vi[vid] = (r.get('venue','').strip(), r.get('city','').strip())
    loyal = []
    for vid, yrs in _vy.items():
        if len(yrs) >= 3:
            nm, cy = _vi[vid]
            loyal.append([nm, cy, sorted(yrs), _vc[vid]])
    loyal.sort(key=lambda x: (-len(x[2]), -x[3]))
    venue_loyalty = loyal[:100]

    # ── Trips ─────────────────────────────────────────────────────────────────
    trips = detect_trips(rows)
    timeline = [{'id':t['id'],'name':t['name'],'start':t['start_date'],'end':t['end_date'],
                 'days':t['duration'],'countries':t['countries'][:4],'count':t['checkin_count'],
                 'year':t['start_year']} for t in trips]

    # ── Recent 30 check-ins ────────────────────────────────────────────────────
    valid_rows = [r for r in rows if r.get('date','').strip()]
    recent_sorted = sorted(valid_rows, key=lambda r: int(r['date']), reverse=True)[:30]
    recent = []
    for r in recent_sorted:
        ts = int(r['date'])
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        try: lat = round(float(r.get('lat','')), 5)
        except: lat = None
        try: lng = round(float(r.get('lng','')), 5)
        except: lng = None
        recent.append({
            'ts':       ts,
            'date':     dt.strftime('%Y-%m-%d'),
            'time':     dt.strftime('%H:%M'),
            'datetime': dt.strftime('%d %b %Y, %H:%M'),
            'venue':    r.get('venue','').strip(),
            'venue_id': r.get('venue_id','').strip(),
            'city':     r.get('city','').strip(),
            'country':  r.get('country','').strip(),
            'category': r.get('category','').strip(),
            'lat':      lat,
            'lng':      lng,
        })
    by_year   = Counter(d.year for d in dates)
    by_month  = Counter((d.year, d.month) for d in dates)
    by_hour   = Counter(d.hour for d in dates)
    by_dow    = Counter(d.weekday() for d in dates)

    # Category summary groups
    cat_groups = Counter()
    for r in rows:
        cat = r.get('category','').strip()
        if cat:
            grp = categorize(cat)
            if grp:
                cat_groups[grp] += 1

    # ── Category Explorer: unique by venue_id, with city ───────────────────────
    # Build: raw_cat → {venue_id: {name, city, count}}
    cat_vid = defaultdict(dict)
    for r in rows:
        cat   = r.get('category','').strip()
        vid   = r.get('venue_id','').strip()
        venue = r.get('venue','').strip()
        city  = r.get('city','').strip()
        if not (cat and vid and venue):
            continue
        if vid not in cat_vid[cat]:
            cat_vid[cat][vid] = {'name': venue, 'city': city, 'count': 0}
        cat_vid[cat][vid]['count'] += 1

    # Merge groups → top 50 by checkin count, unique by venue_id
    explorer = {}
    for display_name, raw_cats in EXPLORER_GROUPS.items():
        combined = {}
        for rc in raw_cats:
            for vid, d in cat_vid.get(rc, {}).items():
                if vid not in combined:
                    combined[vid] = {'name': d['name'], 'city': d['city'], 'count': 0}
                combined[vid]['count'] += d['count']
        top50 = sorted(combined.values(), key=lambda x: -x['count'])[:50]
        if top50:
            explorer[display_name] = [[d['name'], d['city'], d['count']] for d in top50]

    explorer_cats = [k for k in EXPLORER_GROUPS.keys() if k in explorer]

    # Unique places count by venue_id (matches Swarm)
    seen_ids = set(); seen_coords = set(); unique_places = []
    for r in rows:
        vid = r.get('venue_id','').strip()
        lat = r.get('lat','').strip(); lng = r.get('lng','').strip()
        if vid:
            if vid not in seen_ids:
                seen_ids.add(vid)
                if lat and lng:
                    unique_places.append([float(lat), float(lng),
                                          r.get('venue','').strip()])
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
    countries_by_venues = sorted([[c, len(v)] for c,v in country_vids.items()],
                                   key=lambda x:-x[1])

    all_coords = []
    for r in rows:
        try: all_coords.append([round(float(r['lat']),5), round(float(r['lng']),5)])
        except: pass

    print(f"  Cities: {len(cities)} | Countries: {len(countries)} | Unique: {unique_count}")
    return {
        'total': len(rows),
        'date_min': str(min(dates).date()), 'date_max': str(max(dates).date()),
        'unique_places_count': unique_count,
        'by_year':   sorted([(str(k), v) for k,v in by_year.items()]),
        'by_month':  sorted([(f'{k[0]}-{k[1]:02d}', v) for k,v in by_month.items()]),
        'by_hour':   [(k, v) for k,v in sorted(by_hour.items())],
        'by_dow':    [(k, v) for k,v in sorted(by_dow.items())],
        'countries': [[c, n] for c,n in countries.most_common()],
        'countries_by_venues': countries_by_venues,
        'cities':    cities.most_common(),
        'venues':    venues_list,
        'cat_groups': cat_groups.most_common(),
        'recent':    recent,
        'explorer_cats': explorer_cats,
        'explorer':  explorer,
        'unique_places': unique_places,
        'all_coords': all_coords,
        'companions':    companions,
        'heatmap':       heatmap,
        'discovery_rate': discovery_rate,
        'venue_loyalty': venue_loyalty,
        'timeline':      timeline,
        'trips_count':   len(trips),
    }, trips


# ── HTML Template ───────────────────────────────────────────────────────────────
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
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

/* ── HEADER ── */
header{padding:52px 56px 36px;background:linear-gradient(160deg,#0f1220 0%,#0b0d13 70%);border-bottom:1px solid var(--border);display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:28px;}
header h1{font-family:'Playfair Display',serif;font-size:clamp(2.2rem,5vw,5rem);font-weight:900;line-height:1;letter-spacing:-0.02em;background:linear-gradient(130deg,#f5d48a 0%,#e8b86d 45%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
header .sub{margin-top:8px;font-family:'DM Mono',monospace;font-size:0.72rem;letter-spacing:0.18em;text-transform:uppercase;color:var(--muted);}
.updated{font-family:'DM Mono',monospace;font-size:0.62rem;color:var(--muted);margin-top:4px;}
.kpis{display:flex;gap:28px;flex-wrap:wrap;align-items:flex-end;}
.kpi .num{font-family:'Playfair Display',serif;font-size:2.2rem;font-weight:700;color:var(--gold);line-height:1;}
.kpi .lbl{font-family:'DM Mono',monospace;font-size:0.60rem;text-transform:uppercase;letter-spacing:0.14em;color:var(--muted);margin-top:5px;}

/* ── GRID ── */
.grid{padding:36px 56px 72px;display:grid;grid-template-columns:1fr 1fr;gap:20px;max-width:1500px;}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:26px 30px;position:relative;overflow:hidden;}
.card::after{content:'';position:absolute;top:0;left:24px;right:24px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:0.35;}
.card.full{grid-column:1/-1;}
.card-title{font-family:'DM Mono',monospace;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.2em;color:var(--gold);margin-bottom:18px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.card-title em{opacity:0.5;font-style:normal;}

/* ── TABS ── */
.tabs{display:flex;gap:6px;flex-wrap:wrap;}
.tab{padding:5px 13px;border-radius:6px;font-family:'DM Mono',monospace;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.1em;cursor:pointer;border:1px solid var(--border);background:var(--card2);color:var(--text2);transition:all 0.2s;}
.tab.active{background:var(--gold);color:#0b0d13;border-color:var(--gold);}
.pane{display:none;}.pane.active{display:block;}

/* ── SEARCH ── */
.search-box{width:100%;background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:8px 14px;color:var(--text);font-family:'DM Sans',sans-serif;font-size:0.85rem;margin-bottom:14px;outline:none;transition:border-color 0.2s;}
.search-box:focus{border-color:var(--gold);}

/* ── CATEGORY PILLS ── */
.cat-pills{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:18px;}
.cat-pill{padding:5px 14px;border-radius:20px;font-family:'DM Mono',monospace;font-size:0.60rem;text-transform:uppercase;letter-spacing:0.08em;cursor:pointer;border:1px solid var(--border);background:var(--card2);color:var(--text2);transition:all 0.2s;white-space:nowrap;}
.cat-pill.active{background:var(--teal);color:#0b0d13;border-color:var(--teal);}
.cat-pill:hover:not(.active){border-color:var(--teal);color:var(--teal);}

/* ── BAR LISTS ── */
.bar-list{display:flex;flex-direction:column;gap:7px;max-height:520px;overflow-y:auto;padding-right:4px;}
.bar-list::-webkit-scrollbar{width:3px;}
.bar-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
.bar-row{display:grid;grid-template-columns:28px 1fr 110px 58px;align-items:center;gap:8px;}
.bar-row .rank{font-family:'DM Mono',monospace;font-size:0.58rem;color:var(--muted);text-align:right;}
.bar-row .name{font-size:0.80rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text);}
.bar-row .name .city-tag{font-size:0.64rem;color:var(--muted);margin-left:5px;font-style:italic;}
.bar-row .track{height:5px;background:var(--card2);border-radius:3px;overflow:hidden;}
.bar-row .fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--gold),var(--teal));}
.bar-row .cnt{font-family:'DM Mono',monospace;font-size:0.66rem;color:var(--muted);text-align:right;}
.bar-row.hidden{display:none;}

/* ── COUNTRIES GRID ── */
.country-table{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;}
.country-item{background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:9px 12px;display:flex;align-items:center;gap:8px;}
.ci-rank{font-family:'DM Mono',monospace;font-size:0.58rem;color:var(--muted);width:22px;flex-shrink:0;}
.ci-name{font-size:0.82rem;color:var(--text);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.ci-count{font-family:'DM Mono',monospace;font-size:0.72rem;color:var(--teal);flex-shrink:0;}

/* ── MAP ── */
.map-wrap{position:relative;}
#map{height:600px;border-radius:8px;}
.map-status{position:absolute;bottom:14px;left:50%;transform:translateX(-50%);background:rgba(11,13,19,0.9);border:1px solid var(--border);border-radius:8px;padding:7px 16px;font-family:'DM Mono',monospace;font-size:0.68rem;color:var(--gold);pointer-events:none;transition:opacity 0.5s;white-space:nowrap;z-index:999;}

/* ── GITHUB CALENDAR HEATMAP ── */
.heatmap-outer{overflow-x:auto;padding-bottom:8px;}
.heatmap-outer::-webkit-scrollbar{height:3px;}
.heatmap-outer::-webkit-scrollbar-thumb{background:var(--border);}
.heatmap-year{display:flex;gap:14px;align-items:flex-start;margin-bottom:10px;}
.heatmap-label{font-family:'DM Mono',monospace;font-size:0.60rem;color:var(--muted);width:34px;flex-shrink:0;padding-top:4px;text-align:right;}
.heatmap-grid{display:flex;gap:3px;}
.heatmap-week{display:flex;flex-direction:column;gap:3px;}
.heatmap-cell{width:11px;height:11px;border-radius:2px;background:var(--card2);}
.heatmap-cell[data-v="0"]{background:#151820;}
.heatmap-cell[data-v="1"]{background:#1a3a1a;}
.heatmap-cell[data-v="2"]{background:#1e5c1e;}
.heatmap-cell[data-v="3"]{background:#c97a20;}
.heatmap-cell[data-v="4"]{background:#e8b86d;}
.heatmap-cell[data-v="5"]{background:#f5d48a;}
.heatmap-tooltip{position:fixed;pointer-events:none;background:rgba(11,13,19,0.96);border:1px solid var(--border);border-radius:6px;padding:5px 10px;font-family:'DM Mono',monospace;font-size:0.64rem;color:var(--text);z-index:9999;display:none;}
.heatmap-month-labels{display:flex;gap:3px;margin-left:48px;margin-bottom:4px;}
.hm-month{font-family:'DM Mono',monospace;font-size:0.55rem;color:var(--muted);text-transform:uppercase;}

/* ── TRAVEL TIMELINE ── */
.timeline-row{display:flex;align-items:center;gap:10px;margin-bottom:6px;position:relative;}
.tl-year-label{font-family:'DM Mono',monospace;font-size:0.62rem;color:var(--muted);width:38px;flex-shrink:0;text-align:right;}
.tl-track{flex:1;height:24px;background:var(--card2);border-radius:4px;position:relative;overflow:visible;}
.tl-bar{position:absolute;top:2px;height:20px;border-radius:3px;cursor:pointer;transition:filter 0.15s,opacity 0.15s;display:flex;align-items:center;overflow:hidden;min-width:4px;}
.tl-bar:hover{filter:brightness(1.3);z-index:10;}
.tl-bar-label{font-family:'DM Sans',sans-serif;font-size:0.60rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 5px;color:#0b0d13;font-weight:600;}
.tl-checkin-count{font-family:'DM Mono',monospace;font-size:0.58rem;color:var(--muted);width:48px;flex-shrink:0;}

/* ── COMPANIONS ── */
.companion-bar{display:grid;grid-template-columns:160px 1fr 52px;align-items:center;gap:8px;margin-bottom:6px;}
.companion-name{font-size:0.80rem;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.companion-track{height:5px;background:var(--card2);border-radius:3px;overflow:hidden;}
.companion-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--teal),#45b7d1);}
.companion-cnt{font-family:'DM Mono',monospace;font-size:0.66rem;color:var(--muted);text-align:right;}

/* ── DISCOVERY RATE ── */
/* (canvas handled by Chart.js) */

/* ── VENUE LOYALTY ── */
.loyalty-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:8px;max-height:500px;overflow-y:auto;padding-right:4px;}
.loyalty-grid::-webkit-scrollbar{width:3px;}
.loyalty-grid::-webkit-scrollbar-thumb{background:var(--border);}
.loyalty-item{background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:10px 14px;display:flex;align-items:center;gap:10px;}
.loyalty-name{flex:1;min-width:0;}
.loyalty-venue{font-size:0.80rem;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.loyalty-city{font-size:0.65rem;color:var(--muted);}
.loyalty-years{display:flex;gap:3px;flex-wrap:wrap;flex-shrink:0;}
.loyalty-yr{font-family:'DM Mono',monospace;font-size:0.55rem;padding:2px 5px;border-radius:3px;background:rgba(232,184,109,0.15);color:var(--gold);border:1px solid rgba(232,184,109,0.25);}
.loyalty-total{font-family:'DM Mono',monospace;font-size:0.66rem;color:var(--muted);width:26px;text-align:right;flex-shrink:0;}

/* ── TRIPS LINK ── */
.trips-link-card{display:flex;align-items:center;justify-content:space-between;padding:20px 26px;background:linear-gradient(135deg,#12151f 0%,#1a1f30 100%);border:1px solid var(--border);border-radius:14px;cursor:pointer;text-decoration:none;transition:border-color 0.2s;}
.trips-link-card:hover{border-color:var(--gold);}
.tlc-left{display:flex;flex-direction:column;gap:4px;}
.tlc-num{font-family:'Playfair Display',serif;font-size:2.4rem;font-weight:700;color:var(--gold);line-height:1;}
.tlc-label{font-family:'DM Mono',monospace;font-size:0.60rem;text-transform:uppercase;letter-spacing:0.18em;color:var(--muted);}
.tlc-arrow{font-size:1.6rem;color:var(--gold);opacity:0.7;}

@media (max-width:900px){
  .loyalty-grid{grid-template-columns:1fr;}
  .companion-bar{grid-template-columns:120px 1fr 44px;}
  .heatmap-cell{width:9px;height:9px;}
}
@media (max-width:520px){
  .heatmap-cell{width:7px;height:7px;}
  .companion-bar{grid-template-columns:100px 1fr 40px;}
}


.recent-section{padding:0 56px 28px;max-width:1500px;}
.recent-header{display:flex;align-items:baseline;gap:16px;margin-bottom:14px;}
.recent-title{font-family:'DM Mono',monospace;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.2em;color:var(--gold);}
.recent-sub{font-family:'DM Mono',monospace;font-size:0.58rem;color:var(--muted);}
.recent-scroll{display:flex;gap:14px;overflow-x:auto;padding-bottom:10px;scroll-snap-type:x mandatory;}
.recent-scroll::-webkit-scrollbar{height:3px;}
.recent-scroll::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
.recent-card{flex:0 0 220px;scroll-snap-align:start;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 18px 14px;display:flex;flex-direction:column;gap:6px;position:relative;overflow:hidden;transition:border-color 0.2s;}
.recent-card::after{content:'';position:absolute;top:0;left:20px;right:20px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:0.3;}
.recent-card:hover{border-color:var(--gold);}
.rc-venue{font-size:0.88rem;font-weight:600;color:var(--text);line-height:1.25;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
.rc-cat{font-family:'DM Mono',monospace;font-size:0.57rem;text-transform:uppercase;letter-spacing:0.1em;color:var(--teal);margin-top:2px;}
.rc-location{font-size:0.75rem;color:var(--text2);margin-top:4px;}
.rc-date{font-family:'DM Mono',monospace;font-size:0.60rem;color:var(--muted);margin-top:auto;padding-top:8px;}
.rc-weather{display:flex;align-items:center;gap:6px;margin-top:4px;}
.rc-weather-icon{font-size:1.3rem;line-height:1;}
.rc-weather-temp{font-family:'DM Mono',monospace;font-size:0.72rem;color:var(--gold);}
.rc-weather-desc{font-size:0.65rem;color:var(--muted);}
.rc-weather-loading{font-family:'DM Mono',monospace;font-size:0.60rem;color:var(--muted);animation:pulse 1.5s infinite;}
@keyframes pulse{0%,100%{opacity:0.4;}50%{opacity:1;}}
.recent-loading{font-family:'DM Mono',monospace;font-size:0.70rem;color:var(--muted);padding:20px 0;}
@media (max-width: 900px) {
  .recent-section{padding:0 14px 20px;}
  .recent-card{flex:0 0 185px;}
}
@media (max-width: 520px) {
  .recent-card{flex:0 0 160px;padding:14px 14px 10px;}
  .rc-venue{font-size:0.80rem;}
}


@media (max-width: 900px) {
  header{padding:28px 20px 24px;flex-direction:column;align-items:flex-start;gap:20px;}
  .kpis{gap:16px;}
  .kpi .num{font-size:1.8rem;}
  .kpi .lbl{font-size:0.58rem;}
  .grid{padding:14px 14px 48px;grid-template-columns:1fr;gap:14px;}
  .card{padding:18px 18px;}
  .card.full{grid-column:1;}
  .country-table{grid-template-columns:repeat(2,1fr);}
  #map{height:420px;}
  .bar-row{grid-template-columns:22px 1fr 80px 44px;gap:6px;}
  .bar-row .name{font-size:0.75rem;}
  .cat-pill{font-size:0.57rem;padding:4px 10px;}
}
@media (max-width: 520px) {
  header h1{font-size:2rem;}
  header .sub{font-size:0.62rem;letter-spacing:0.1em;}
  .kpis{gap:12px;}
  .kpi .num{font-size:1.5rem;}
  .country-table{grid-template-columns:1fr 1fr;}
  #map{height:320px;}
  .bar-row{grid-template-columns:18px 1fr 60px 36px;gap:5px;}
  .bar-row .name .city-tag{display:none;}
  .grid{padding:10px 10px 36px;}
  .card{padding:14px 14px;}
  .card-title{font-size:0.58rem;}
  .tabs .tab{font-size:0.56rem;padding:4px 9px;}
  .cat-pill{font-size:0.54rem;padding:3px 8px;}
}
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
    <div class="kpi"><div class="num">{{TRIPS}}</div><div class="lbl">Trips</div></div>
  </div>
</header>

<div class="recent-section">
  <div class="recent-header">
    <span class="recent-title">Recent Check-ins</span>
  </div>
  <div class="recent-scroll" id="recentScroll">
    <div class="recent-loading">Loading…</div>
  </div>
</div>

<div class="grid">
  <div class="card"><div class="card-title">Check-ins by Year</div><canvas id="yearChart" height="210"></canvas></div>
  <div class="card"><div class="card-title">Monthly Trend</div><canvas id="monthChart" height="210"></canvas></div>
  <div class="card"><div class="card-title">Hour of Day</div><canvas id="hourChart" height="200"></canvas></div>
  <div class="card"><div class="card-title">Day of Week</div><canvas id="dowChart" height="200"></canvas></div>

  <div class="card full">
    <div class="card-title">Activity Calendar <em>&middot; GitHub-style heatmap by day</em></div>
    <div id="heatmapTip" class="heatmap-tooltip"></div>
    <div class="heatmap-outer" id="heatmapCont"></div>
  </div>

  <div class="card full">
    <div class="card-title">Travel Timeline <em>&middot; trips away from Minsk &middot; click a bar for details</em></div>
    <div id="timelineCont"></div>
  </div>

  <a class="trips-link-card" href="trips.html">
    <div class="tlc-left">
      <div class="tlc-num">{{TRIPS}}</div>
      <div class="tlc-label">Trips documented &nbsp;&middot;&nbsp; view full trip journal →</div>
    </div>
    <div class="tlc-arrow">✈</div>
  </a>

  <div class="card full">
    <div class="card-title">
      All {{COUNTRIES}} Countries <em>&middot;</em>
      <div class="tabs">
        <div class="tab active" onclick="switchCountryTab('checkins',this)">By Check-ins</div>
        <div class="tab" onclick="switchCountryTab('places',this)">By Unique Places</div>
      </div>
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
    <div class="card-title">Top 500 Venues <em>&middot;&nbsp;unique by venue&nbsp;&middot;&nbsp; scroll &amp; search</em></div>
    <input class="search-box" type="text" placeholder="Search venues..." oninput="filterList('venuesList',this.value)">
    <div class="bar-list" id="venuesList"></div>
  </div>

  <div class="card">
    <div class="card-title">Top Companions <em>&middot; check-ins with others</em></div>
    <div id="companionsList"></div>
  </div>

  <div class="card">
    <div class="card-title">Discovery Rate <em>&middot; new vs revisited venues per month</em></div>
    <canvas id="discoveryChart" height="200"></canvas>
  </div>

  <div class="card full">
    <div class="card-title">Venue Loyalty <em>&middot; places visited in 3+ different years</em></div>
    <div class="loyalty-grid" id="loyaltyGrid"></div>
  </div>

  <div class="card full">
    <div class="card-title">Place Categories <em>&middot; by group</em></div>
    <canvas id="catChart" height="85"></canvas>
  </div>

  <div class="card full">
    <div class="card-title">Category Explorer <em>&middot; top 50 unique venues per category (by check-ins)</em></div>
    <div class="cat-pills" id="catPills"></div>
    <div class="bar-list" id="explorerList"></div>
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

// ── Charts ────────────────────────────────────────────────────────
new Chart(document.getElementById('yearChart'),{type:'bar',data:{labels:S.by_year.map(x=>x[0]),datasets:[{data:S.by_year.map(x=>x[1]),backgroundColor:S.by_year.map((_,i)=>PAL[i%PAL.length]),borderRadius:5,borderWidth:0}]},options:{responsive:true,plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>' '+ctx.parsed.y.toLocaleString()+' check-ins'}}},scales:{x:{grid:{display:false}},y:{grid:{color:'#1a1e2e'}}}}});
new Chart(document.getElementById('monthChart'),{type:'line',data:{labels:S.by_month.map(x=>x[0]),datasets:[{data:S.by_month.map(x=>x[1]),borderColor:'#4ecdc4',backgroundColor:'rgba(78,205,196,0.07)',borderWidth:2,pointRadius:0,fill:true,tension:0.4}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{maxTicksLimit:12,maxRotation:0}},y:{grid:{color:'#1a1e2e'}}}}});
new Chart(document.getElementById('hourChart'),{type:'bar',data:{labels:S.by_hour.map(x=>x[0]+':00'),datasets:[{data:S.by_hour.map(x=>x[1]),backgroundColor:S.by_hour.map(x=>{const m=Math.max(...S.by_hour.map(y=>y[1]));return`rgba(78,205,196,${(0.2+0.8*(x[1]/m)).toFixed(2)})`;}),borderRadius:4,borderWidth:0}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{grid:{color:'#1a1e2e'}}}}});
const DOW=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
new Chart(document.getElementById('dowChart'),{type:'bar',data:{labels:S.by_dow.map(x=>DOW[x[0]]),datasets:[{data:S.by_dow.map(x=>x[1]),backgroundColor:S.by_dow.map(x=>x[0]>=4?'rgba(78,205,196,0.75)':'rgba(232,184,109,0.55)'),borderRadius:4,borderWidth:0}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{grid:{color:'#1a1e2e'}}}}});
const CC=['#e8b86d','#4ecdc4','#e63946','#45b7d1','#a8d8a8','#c44dff','#f4831f','#96ceb4'];
new Chart(document.getElementById('catChart'),{type:'bar',data:{labels:S.cat_groups.map(x=>x[0]),datasets:[{data:S.cat_groups.map(x=>x[1]),backgroundColor:S.cat_groups.map((_,i)=>CC[i%CC.length]),borderRadius:5,borderWidth:0}]},options:{indexAxis:'y',responsive:true,plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>' '+ctx.parsed.x.toLocaleString()+' check-ins'}}},scales:{x:{grid:{color:'#1a1e2e'}},y:{grid:{display:false}}}}});

// ── GitHub Heatmap ─────────────────────────────────────────────────────────
(function(){
  const data=S.heatmap, tip=document.getElementById('heatmapTip');
  const cont=document.getElementById('heatmapCont');
  const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function level(v){if(!v||v===0)return 0;if(v<=2)return 1;if(v<=5)return 2;if(v<=10)return 3;if(v<=20)return 4;return 5;}
  Object.keys(data).sort().forEach(year=>{
    const d=data[year];
    const row=document.createElement('div'); row.className='heatmap-year';
    const lbl=document.createElement('div'); lbl.className='heatmap-label'; lbl.textContent=year; row.appendChild(lbl);
    const grid=document.createElement('div'); grid.className='heatmap-grid';
    let cur=new Date(parseInt(year),0,1);
    const end=new Date(parseInt(year),11,31);
    // pad to Monday start
    let dow=cur.getDay(); dow=(dow===0?6:dow-1);
    let week=document.createElement('div'); week.className='heatmap-week';
    for(let p=0;p<dow;p++){const blank=document.createElement('div');blank.className='heatmap-cell';blank.setAttribute('data-v','0');week.appendChild(blank);}
    while(cur<=end){
      if(cur.getDay()===1&&week.children.length>0){grid.appendChild(week);week=document.createElement('div');week.className='heatmap-week';}
      const ds=cur.getFullYear()+'-'+String(cur.getMonth()+1).padStart(2,'0')+'-'+String(cur.getDate()).padStart(2,'0');
      const v=d[ds]||0; const cell=document.createElement('div'); cell.className='heatmap-cell';
      cell.setAttribute('data-v',level(v));
      cell.addEventListener('mouseenter',e=>{if(v>0){tip.textContent=ds+': '+v+' check-in'+(v===1?'':'s');tip.style.display='block';}});
      cell.addEventListener('mousemove',e=>{tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY-28)+'px';});
      cell.addEventListener('mouseleave',()=>{tip.style.display='none';});
      week.appendChild(cell);
      cur.setDate(cur.getDate()+1);
    }
    if(week.children.length>0) grid.appendChild(week);
    row.appendChild(grid); cont.appendChild(row);
  });
})();

// ── Travel Timeline ─────────────────────────────────────────────────────────
(function(){
  const trips=S.timeline;
  const cont=document.getElementById('timelineCont');
  // Group by year
  const byYear={};
  trips.forEach(t=>{
    const yr=t.year;
    if(!byYear[yr]) byYear[yr]=[];
    byYear[yr].push(t);
  });
  const COLORS=['#e8b86d','#4ecdc4','#e63946','#45b7d1','#a8d8a8','#c44dff','#f4831f','#96ceb4','#ff6b9d','#4d79ff'];
  const years=Object.keys(byYear).sort();
  years.forEach(yr=>{
    const row=document.createElement('div'); row.className='timeline-row';
    const lbl=document.createElement('div'); lbl.className='tl-year-label'; lbl.textContent=yr; row.appendChild(lbl);
    const track=document.createElement('div'); track.className='tl-track';
    const yearStart=new Date(parseInt(yr),0,1).getTime()/1000;
    const yearEnd=new Date(parseInt(yr),11,31,23,59,59).getTime()/1000;
    const yearSpan=yearEnd-yearStart;
    byYear[yr].forEach((t,i)=>{
      const sTs=new Date(t.start+'T00:00:00Z').getTime()/1000;
      const eTs=new Date(t.end+'T23:59:59Z').getTime()/1000;
      const left=Math.max(0,((sTs-yearStart)/yearSpan)*100);
      const width=Math.max(0.3,((eTs-sTs)/yearSpan)*100);
      const bar=document.createElement('a');
      bar.className='tl-bar'; bar.href='trips.html#trip-'+t.id; bar.title=t.name+' ('+t.start+' – '+t.end+', '+t.count+' check-ins)';
      bar.style.left=left.toFixed(2)+'%'; bar.style.width=width.toFixed(2)+'%';
      bar.style.background=COLORS[i%COLORS.length];
      if(width>6){const lv=document.createElement('span');lv.className='tl-bar-label';lv.textContent=t.name;bar.appendChild(lv);}
      track.appendChild(bar);
    });
    row.appendChild(track);
    const cnt=document.createElement('div'); cnt.className='tl-checkin-count';
    const total=byYear[yr].reduce((s,t)=>s+t.count,0);
    cnt.textContent=total.toLocaleString(); row.appendChild(cnt);
    cont.appendChild(row);
  });
})();

// ── Companions ──────────────────────────────────────────────────────────────
(function(){
  const data=S.companions, max=data[0]?data[0][1]:1;
  document.getElementById('companionsList').innerHTML=data.map(([n,c])=>
    `<div class="companion-bar">
      <span class="companion-name" title="${n}">${n}</span>
      <div class="companion-track"><div class="companion-fill" style="width:${(c/max*100).toFixed(1)}%"></div></div>
      <span class="companion-cnt">${c.toLocaleString()}</span>
    </div>`
  ).join('');
})();

// ── Discovery Rate ─────────────────────────────────────────────────────────
new Chart(document.getElementById('discoveryChart'),{type:'bar',
  data:{labels:S.discovery_rate.map(x=>x[0]),
    datasets:[
      {label:'New venues',data:S.discovery_rate.map(x=>x[1]),backgroundColor:'rgba(78,205,196,0.75)',borderWidth:0,borderRadius:2},
      {label:'Revisits',  data:S.discovery_rate.map(x=>x[2]),backgroundColor:'rgba(232,184,109,0.45)',borderWidth:0,borderRadius:2},
    ]},
  options:{responsive:true,plugins:{legend:{display:true,labels:{color:'#7a85a8',font:{family:"'DM Mono',monospace",size:10}}},
    tooltip:{callbacks:{label:ctx=>' '+ctx.dataset.label+': '+ctx.parsed.y.toLocaleString()}}},
    scales:{x:{stacked:true,grid:{display:false},ticks:{maxTicksLimit:24,maxRotation:0,font:{size:10}}},
      y:{stacked:true,grid:{color:'#1a1e2e'}}}}});

// ── Venue Loyalty ──────────────────────────────────────────────────────────
(function(){
  const data=S.venue_loyalty;
  document.getElementById('loyaltyGrid').innerHTML=data.map(([name,city,years,total])=>
    `<div class="loyalty-item">
      <div class="loyalty-name">
        <div class="loyalty-venue" title="${name}">${name}</div>
        <div class="loyalty-city">${city||''}</div>
      </div>
      <div class="loyalty-years">${years.map(y=>`<span class="loyalty-yr">${y}</span>`).join('')}</div>
      <div class="loyalty-total">${total}</div>
    </div>`
  ).join('');
})();


const explorerData=S.explorer, explorerCats=S.explorer_cats;
let activeCat=explorerCats[0];
const pillsEl=document.getElementById('catPills');
explorerCats.forEach(cat=>{
  const p=document.createElement('div');
  p.className='cat-pill'+(cat===activeCat?' active':'');
  p.textContent=cat;
  p.onclick=()=>{
    document.querySelectorAll('.cat-pill').forEach(x=>x.classList.remove('active'));
    p.classList.add('active'); activeCat=cat; renderExplorer(cat);
  };
  pillsEl.appendChild(p);
});
function renderExplorer(cat){
  const data=explorerData[cat]||[];
  const max=data.length?data[0][2]:1;
  document.getElementById('explorerList').innerHTML=data.map(([name,city,count],i)=>
    `<div class="bar-row">
      <span class="rank">#${i+1}</span>
      <span class="name" title="${name} · ${city}">${name}<span class="city-tag">${city||''}</span></span>
      <div class="track"><div class="fill" style="width:${(count/max*100).toFixed(1)}%"></div></div>
      <span class="cnt">${count.toLocaleString()}</span>
    </div>`
  ).join('')||'<div style="color:var(--muted);padding:8px;font-size:0.85rem">No data</div>';
}
renderExplorer(activeCat);

// ── Countries ─────────────────────────────────────────────────────
function makeCountryGrid(data,id){
  const n=data.length, cols=3, rows=Math.ceil(n/cols), vis=[];
  for(let r=0;r<rows;r++) for(let c=0;c<cols;c++){const i=c*rows+r;if(i<n)vis.push([data[i],i+1]);}
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

// ── Bar Lists ─────────────────────────────────────────────────────
function barList(id,data){
  const max=data[0][1];
  document.getElementById(id).innerHTML=data.map(([n,c],i)=>
    `<div class="bar-row" data-name="${n.toLowerCase().replace(/"/g,'')}">
      <span class="rank">#${i+1}</span>
      <span class="name" title="${n}">${n}</span>
      <div class="track"><div class="fill" style="width:${(c/max*100).toFixed(1)}%"></div></div>
      <span class="cnt">${c.toLocaleString()}</span>
    </div>`
  ).join('');
}
barList('citiesList',S.cities);
// Venues: data is [name, count, city]
(function(){
  const data=S.venues, max=data[0][1];
  document.getElementById('venuesList').innerHTML=data.map(([n,c,city],i)=>
    `<div class="bar-row" data-name="${n.toLowerCase().replace(/"/g,'')}${city?' '+city.toLowerCase():''}">
      <span class="rank">#${i+1}</span>
      <span class="name" title="${n}${city?' · '+city:''}">${n}<span class="city-tag">${city||''}</span></span>
      <div class="track"><div class="fill" style="width:${(c/max*100).toFixed(1)}%"></div></div>
      <span class="cnt">${c.toLocaleString()}</span>
    </div>`
  ).join('');
})();
function filterList(id,q){
  document.getElementById(id).querySelectorAll('.bar-row').forEach(r=>
    r.classList.toggle('hidden',q.length>0&&!r.dataset.name.includes(q.toLowerCase()))
  );
}

// ── Recent Check-ins + Weather ─────────────────────────────────
(function(){
  const recent=S.recent;
  const WMO={
    0:['☀️','Clear'],1:['🌤️','Mainly clear'],2:['⛅','Partly cloudy'],3:['☁️','Overcast'],
    45:['🌫️','Fog'],48:['🌫️','Icy fog'],51:['🌦️','Light drizzle'],53:['🌦️','Drizzle'],55:['🌧️','Heavy drizzle'],
    61:['🌧️','Light rain'],63:['🌧️','Rain'],65:['🌧️','Heavy rain'],
    71:['🌨️','Light snow'],73:['❄️','Snow'],75:['❄️','Heavy snow'],77:['🌨️','Snow grains'],
    80:['🌦️','Rain showers'],81:['🌧️','Rain showers'],82:['⛈️','Violent showers'],
    85:['🌨️','Snow showers'],86:['❄️','Heavy snow showers'],
    95:['⛈️','Thunderstorm'],96:['⛈️','Thunderstorm+hail'],99:['⛈️','Thunderstorm+hail'],
  };
  const scrollEl=document.getElementById('recentScroll');
  // Render cards immediately (weather loads async)
  // Build Foursquare app URL: slug is lowercased name with spaces→hyphens, then encoded
  function fsUrl(r){
    if(!r.venue_id) return null;
    const slug=encodeURIComponent(r.venue.toLowerCase().replace(/\s+/g,'-'));
    return `https://app.foursquare.com/v/${slug}/${r.venue_id}`;
  }
  scrollEl.innerHTML=recent.map((r,i)=>{
    const url=fsUrl(r);
    const tag=url?'a':'div', href=url?` href="${url}" target="_blank" rel="noopener"`:'';
    return `<${tag}${href} class="recent-card" id="rc_${i}" style="${url?'text-decoration:none;cursor:pointer;':''}">
      <div class="rc-venue">${r.venue||'Unknown venue'}</div>
      <div class="rc-cat">${r.category||''}</div>
      <div class="rc-location">${[r.city,r.country].filter(Boolean).join(', ')}</div>
      <div class="rc-weather" id="rcw_${i}"><span class="rc-weather-loading">fetching weather…</span></div>
      <div class="rc-date">${r.datetime}</div>
    </${tag}>`;
  }).join('');

  // Fetch weather from Open-Meteo for each check-in (throttled)
  async function fetchWeather(r,i){
    if(!r.lat||!r.lng){
      document.getElementById('rcw_'+i).innerHTML='';
      return;
    }
    try{
      const url=`https://archive-api.open-meteo.com/v1/archive?latitude=${r.lat}&longitude=${r.lng}`+
        `&start_date=${r.date}&end_date=${r.date}&hourly=temperature_2m,weather_code&timezone=UTC`;
      const res=await fetch(url);
      const d=await res.json();
      const hour=parseInt(r.time.split(':')[0]);
      const temp=d.hourly?.temperature_2m?.[hour];
      const code=d.hourly?.weather_code?.[hour];
      const [icon,desc]=WMO[code]||['🌡️',''];
      const el=document.getElementById('rcw_'+i);
      if(el) el.innerHTML=`<span class="rc-weather-icon">${icon}</span><span class="rc-weather-temp">${temp!=null?Math.round(temp)+'°C':'—'}</span><span class="rc-weather-desc">${desc}</span>`;
    }catch(e){
      const el=document.getElementById('rcw_'+i);
      if(el) el.innerHTML='';
    }
  }
  // Stagger requests to avoid rate limiting
  recent.forEach((r,i)=>setTimeout(()=>fetchWeather(r,i), i*120));
})();

// ── Map ───────────────────────────────────────────────────────────
const map=L.map('map',{preferCanvas:true}).setView([30,15],2);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{
  attribution:'&copy; OpenStreetMap &copy; CARTO',subdomains:'abcd',maxZoom:19}).addTo(map);
const status=document.getElementById('mapStatus');
let heatLayer=null,dotLayer=null,currentMode='heat';
const coords=S.all_coords;
const cellCount={};
coords.forEach(p=>{const k=Math.round(p[0]*20)+'_'+Math.round(p[1]*20);cellCount[k]=(cellCount[k]||0)+1;});
const sortedC=Object.values(cellCount).sort((a,b)=>a-b);
const p95=sortedC[Math.floor(sortedC.length*0.95)];
heatLayer=L.heatLayer(coords.map(p=>{const k=Math.round(p[0]*20)+'_'+Math.round(p[1]*20);return[p[0],p[1],Math.min(cellCount[k],p95)/p95];}),
  {radius:14,blur:16,maxZoom:18,max:1.0,gradient:{'0.0':'#000033','0.25':'#0a3d6b','0.5':'#e8b86d','0.75':'#ff7700','1.0':'#ff1100'}}).addTo(map);
status.textContent='Heatmap · '+coords.length.toLocaleString()+' check-ins';
setTimeout(()=>status.style.opacity='0',2500);
function buildDots(){
  if(dotLayer)return; status.style.opacity='1';
  const pts=S.unique_places; let i=0; dotLayer=L.layerGroup();
  function chunk(){
    const end=Math.min(i+3000,pts.length);
    for(;i<end;i++) L.circleMarker([pts[i][0],pts[i][1]],{radius:3,color:'#e8b86d',fillColor:'#e8b86d',fillOpacity:0.65,weight:0})
      .bindTooltip(pts[i][2]||'',{direction:'top',opacity:0.9}).addTo(dotLayer);
    status.textContent='Plotting '+i.toLocaleString()+' / '+pts.length.toLocaleString()+'...';
    if(i<pts.length) requestAnimationFrame(chunk);
    else{if(currentMode==='dots')dotLayer.addTo(map);status.style.opacity='0';}
  }
  requestAnimationFrame(chunk);
}
function switchMap(mode){
  currentMode=mode;
  document.getElementById('tabHeat').classList.toggle('active',mode==='heat');
  document.getElementById('tabDots').classList.toggle('active',mode==='dots');
  if(mode==='heat'){if(dotLayer)map.removeLayer(dotLayer);heatLayer.addTo(map);
    status.textContent='Heatmap · '+coords.length.toLocaleString()+' check-ins';
    status.style.opacity='1';setTimeout(()=>status.style.opacity='0',2500);}
  else{map.removeLayer(heatLayer);if(dotLayer)dotLayer.addTo(map);else buildDots();}
}
</script>
</body>
</html>"""


TRIPS_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Trip Journal</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
<style>
:root{--bg:#0b0d13;--card:#12151f;--card2:#181c28;--border:#222738;--gold:#e8b86d;--teal:#4ecdc4;--muted:#4a5270;--text:#cdd5f0;--text2:#7a85a8;}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;}
a{color:inherit;}

/* ── TOP NAV ── */
.topnav{display:flex;align-items:center;gap:20px;padding:18px 56px;border-bottom:1px solid var(--border);background:var(--card);}
.topnav-logo{font-family:'Playfair Display',serif;font-size:1.1rem;font-weight:700;color:var(--gold);text-decoration:none;}
.topnav a{font-family:'DM Mono',monospace;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.14em;color:var(--muted);text-decoration:none;transition:color .2s;}
.topnav a:hover,.topnav a.active{color:var(--gold);}

/* ── VIEWS ── */
#listView,#detailView{min-height:calc(100vh - 57px);}
#detailView{display:none;}

/* ── LIST HEADER ── */
.list-header{padding:40px 56px 28px;display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:16px;}
.list-header h1{font-family:'Playfair Display',serif;font-size:clamp(1.8rem,4vw,3rem);font-weight:900;background:linear-gradient(130deg,#f5d48a 0%,#e8b86d 45%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.list-meta{font-family:'DM Mono',monospace;font-size:0.62rem;color:var(--muted);letter-spacing:.12em;}
.list-filters{padding:0 56px 20px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;}
.filter-search{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:8px 14px;color:var(--text);font-family:'DM Sans',sans-serif;font-size:.85rem;outline:none;min-width:220px;transition:border-color .2s;}
.filter-search:focus{border-color:var(--gold);}
.filter-pill{padding:5px 13px;border-radius:6px;font-family:'DM Mono',monospace;font-size:.60rem;text-transform:uppercase;letter-spacing:.1em;cursor:pointer;border:1px solid var(--border);background:var(--card2);color:var(--text2);transition:all .2s;}
.filter-pill.active{background:var(--gold);color:#0b0d13;border-color:var(--gold);}
.filter-pill:hover:not(.active){border-color:var(--gold);color:var(--gold);}

/* ── TRIPS GRID ── */
.trips-grid{padding:0 56px 72px;display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;}
.trip-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:22px 22px 18px;cursor:pointer;transition:border-color .2s,transform .15s;position:relative;overflow:hidden;text-decoration:none;display:block;}
.trip-card::after{content:'';position:absolute;top:0;left:20px;right:20px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:.3;}
.trip-card:hover{border-color:var(--gold);transform:translateY(-2px);}
.trip-card.hidden{display:none;}
.tc-num{font-family:'DM Mono',monospace;font-size:.56rem;color:var(--muted);margin-bottom:6px;}
.tc-name{font-size:1rem;font-weight:600;color:var(--text);line-height:1.3;margin-bottom:8px;}
.tc-dates{font-family:'DM Mono',monospace;font-size:.62rem;color:var(--teal);margin-bottom:10px;}
.tc-countries{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:12px;}
.tc-country{font-family:'DM Mono',monospace;font-size:.56rem;padding:3px 8px;border-radius:4px;background:rgba(78,205,196,.1);color:var(--teal);border:1px solid rgba(78,205,196,.2);}
.tc-stats{display:flex;gap:16px;}
.tc-stat{display:flex;flex-direction:column;gap:2px;}
.tc-stat-v{font-family:'DM Mono',monospace;font-size:.80rem;color:var(--gold);}
.tc-stat-l{font-size:.60rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;}

/* ── DETAIL VIEW ── */
.detail-back{display:flex;align-items:center;gap:8px;padding:18px 56px 0;font-family:'DM Mono',monospace;font-size:.62rem;color:var(--muted);cursor:pointer;text-transform:uppercase;letter-spacing:.12em;width:max-content;transition:color .2s;}
.detail-back:hover{color:var(--gold);}
.detail-back::before{content:'←';}
.detail-hero{padding:28px 56px 24px;display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:20px;border-bottom:1px solid var(--border);}
.detail-hero h2{font-family:'Playfair Display',serif;font-size:clamp(1.6rem,4vw,2.6rem);font-weight:900;background:linear-gradient(130deg,#f5d48a,#e8b86d,#b97c30);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;line-height:1.1;}
.detail-dates{font-family:'DM Mono',monospace;font-size:.65rem;color:var(--muted);margin-top:6px;}
.detail-kpis{display:flex;gap:24px;flex-wrap:wrap;}
.detail-kpi .num{font-family:'Playfair Display',serif;font-size:1.8rem;font-weight:700;color:var(--gold);line-height:1;}
.detail-kpi .lbl{font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);margin-top:4px;}
.detail-body{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:24px 56px 60px;max-width:1400px;}
.detail-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:22px 24px;position:relative;overflow:hidden;}
.detail-card::after{content:'';position:absolute;top:0;left:20px;right:20px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:.3;}
.detail-card.full{grid-column:1/-1;}
.detail-card-title{font-family:'DM Mono',monospace;font-size:.60rem;text-transform:uppercase;letter-spacing:.18em;color:var(--gold);margin-bottom:16px;}
#detailMap{height:380px;border-radius:8px;}
.detail-countries{display:flex;flex-wrap:wrap;gap:6px;}
.detail-country{background:var(--card2);border:1px solid var(--border);border-radius:6px;padding:6px 12px;font-size:.78rem;}
.detail-timeline{max-height:440px;overflow-y:auto;padding-right:4px;}
.detail-timeline::-webkit-scrollbar{width:3px;}
.detail-timeline::-webkit-scrollbar-thumb{background:var(--border);}
.tl-day{margin-bottom:18px;}
.tl-day-header{font-family:'DM Mono',monospace;font-size:.60rem;text-transform:uppercase;letter-spacing:.14em;color:var(--gold);margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid var(--border);}
.tl-checkin{display:flex;gap:10px;align-items:flex-start;padding:5px 0;}
.tl-checkin-time{font-family:'DM Mono',monospace;font-size:.60rem;color:var(--muted);flex-shrink:0;width:40px;}
.tl-checkin-info{flex:1;min-width:0;}
.tl-checkin-venue{font-size:.80rem;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.tl-checkin-venue a{color:inherit;text-decoration:none;}
.tl-checkin-venue a:hover{color:var(--gold);}
.tl-checkin-sub{font-size:.65rem;color:var(--muted);}
.cat-bar-row{display:flex;align-items:center;gap:8px;margin-bottom:6px;}
.cat-bar-name{font-size:.75rem;color:var(--text);flex:1;}
.cat-bar-track{width:100px;height:5px;background:var(--card2);border-radius:3px;overflow:hidden;}
.cat-bar-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--gold),var(--teal));}
.cat-bar-cnt{font-family:'DM Mono',monospace;font-size:.62rem;color:var(--muted);width:36px;text-align:right;}

@media(max-width:900px){
  .topnav,.list-header,.list-filters,.trips-grid,.detail-back,.detail-hero,.detail-body{padding-left:18px;padding-right:18px;}
  .trips-grid{grid-template-columns:1fr 1fr;}
  .detail-body{grid-template-columns:1fr;}
}
@media(max-width:520px){
  .trips-grid{grid-template-columns:1fr;}
  .detail-kpis{gap:14px;}
  .detail-kpi .num{font-size:1.4rem;}
}
</style>
</head>
<body>

<nav class="topnav">
  <a href="index.html" class="topnav-logo">Check-in Journal</a>
  <a href="index.html">← Dashboard</a>
  <a href="trips.html" class="active">Trips</a>
</nav>

<!-- ── LIST VIEW ── -->
<div id="listView">
  <div class="list-header">
    <div>
      <h1>Trip Journal</h1>
      <div class="list-meta">{{TOTAL_TRIPS}} trips &nbsp;·&nbsp; updated {{UPDATED}}</div>
    </div>
  </div>
  <div class="list-filters">
    <input class="filter-search" type="text" placeholder="Search trips…" id="tripSearch" oninput="filterTrips()">
    <div id="yearPills" style="display:flex;gap:6px;flex-wrap:wrap;"></div>
  </div>
  <div class="trips-grid" id="tripsGrid"></div>
</div>

<!-- ── DETAIL VIEW ── -->
<div id="detailView">
  <div class="detail-back" onclick="showList()">All trips</div>
  <div class="detail-hero">
    <div>
      <h2 id="detailName"></h2>
      <div class="detail-dates" id="detailDates"></div>
    </div>
    <div class="detail-kpis" id="detailKpis"></div>
  </div>
  <div class="detail-body">
    <div class="detail-card full">
      <div class="detail-card-title">Map</div>
      <div id="detailMap"></div>
    </div>
    <div class="detail-card">
      <div class="detail-card-title">Countries & Cities</div>
      <div id="detailCountries" class="detail-countries"></div>
    </div>
    <div class="detail-card">
      <div class="detail-card-title">Top Categories</div>
      <div id="detailCats"></div>
    </div>
    <div class="detail-card full">
      <div class="detail-card-title">Check-in Timeline</div>
      <div class="detail-timeline" id="detailTimeline"></div>
    </div>
  </div>
</div>

<script>
const TRIPS = {{TRIPS_JSON}};
let activeMap = null;

// ── Build the grid ──────────────────────────────────────────────────────────
function renderGrid(trips){
  const grid = document.getElementById('tripsGrid');
  grid.innerHTML = trips.map(t => `
    <a class="trip-card" id="card-trip-${t.id}" href="#trip-${t.id}" onclick="showTrip(${t.id});return false;">
      <div class="tc-num">Trip #${t.id}</div>
      <div class="tc-name">${t.name}</div>
      <div class="tc-dates">${t.start_date} &nbsp;–&nbsp; ${t.end_date}</div>
      <div class="tc-countries">${t.countries.slice(0,5).map(c=>`<span class="tc-country">${c}</span>`).join('')}</div>
      <div class="tc-stats">
        <div class="tc-stat"><div class="tc-stat-v">${t.duration}</div><div class="tc-stat-l">Days</div></div>
        <div class="tc-stat"><div class="tc-stat-v">${t.checkin_count.toLocaleString()}</div><div class="tc-stat-l">Check-ins</div></div>
        <div class="tc-stat"><div class="tc-stat-v">${t.unique_places.toLocaleString()}</div><div class="tc-stat-l">Places</div></div>
        <div class="tc-stat"><div class="tc-stat-v">${t.countries.length}</div><div class="tc-stat-l">Countries</div></div>
      </div>
    </a>`).join('');
}
renderGrid(TRIPS);

// ── Year filter pills ───────────────────────────────────────────────────────
const years = [...new Set(TRIPS.map(t => t.start_year))].sort();
const pillsEl = document.getElementById('yearPills');
let activeYear = null;
['All',...years].forEach(y => {
  const p = document.createElement('div');
  p.className = 'filter-pill' + (y==='All'?' active':'');
  p.textContent = y;
  p.onclick = () => {
    document.querySelectorAll('.filter-pill').forEach(x=>x.classList.remove('active'));
    p.classList.add('active');
    activeYear = y==='All' ? null : y;
    filterTrips();
  };
  pillsEl.appendChild(p);
});

function filterTrips(){
  const q = document.getElementById('tripSearch').value.toLowerCase();
  TRIPS.forEach(t => {
    const card = document.getElementById('card-trip-'+t.id);
    const matchYear = !activeYear || t.start_year === activeYear;
    const matchQ = !q || t.name.toLowerCase().includes(q)
      || t.countries.some(c=>c.toLowerCase().includes(q))
      || t.cities.some(c=>c.toLowerCase().includes(q))
      || t.start_date.includes(q);
    card.classList.toggle('hidden', !(matchYear && matchQ));
  });
}

// ── Show detail ─────────────────────────────────────────────────────────────
function showTrip(id){
  const t = TRIPS.find(x=>x.id===id);
  if(!t) return;
  history.pushState({trip:id},'','#trip-'+id);
  document.getElementById('listView').style.display = 'none';
  document.getElementById('detailView').style.display = 'block';
  document.getElementById('detailName').textContent = t.name;
  document.getElementById('detailDates').textContent = t.start_date + ' – ' + t.end_date;
  document.getElementById('detailKpis').innerHTML =
    `<div class="detail-kpi"><div class="num">${t.duration}</div><div class="lbl">Days</div></div>
     <div class="detail-kpi"><div class="num">${t.checkin_count.toLocaleString()}</div><div class="lbl">Check-ins</div></div>
     <div class="detail-kpi"><div class="num">${t.unique_places.toLocaleString()}</div><div class="lbl">Unique Places</div></div>
     <div class="detail-kpi"><div class="num">${t.countries.length}</div><div class="lbl">Countries</div></div>`;
  // Countries + cities
  document.getElementById('detailCountries').innerHTML =
    t.countries.map(c=>`<span class="detail-country">🌍 ${c}</span>`).join('')
    + '<br style="margin:8px 0">'
    + t.cities.slice(0,12).map(c=>`<span class="detail-country" style="background:rgba(232,184,109,.05);border-color:rgba(232,184,109,.15);color:var(--text2);">📍 ${c}</span>`).join('');
  // Categories
  const maxCat = t.top_cats[0]?t.top_cats[0][1]:1;
  document.getElementById('detailCats').innerHTML = t.top_cats.map(([cat,cnt])=>
    `<div class="cat-bar-row">
      <span class="cat-bar-name">${cat}</span>
      <div class="cat-bar-track"><div class="cat-bar-fill" style="width:${(cnt/maxCat*100).toFixed(1)}%"></div></div>
      <span class="cat-bar-cnt">${cnt}</span>
    </div>`).join('');
  // Timeline grouped by day
  const byDay = {};
  t.checkins.forEach(c=>{
    if(!byDay[c.date]) byDay[c.date] = [];
    byDay[c.date].push(c);
  });
  document.getElementById('detailTimeline').innerHTML = Object.keys(byDay).sort().map(day=>
    `<div class="tl-day">
      <div class="tl-day-header">${day} &nbsp;·&nbsp; ${byDay[day].length} check-ins</div>
      ${byDay[day].map(c=>{
        const fsUrl = c.venue_id ? `https://app.foursquare.com/v/${encodeURIComponent(c.venue.toLowerCase().replace(/\s+/g,'-'))}/${c.venue_id}` : null;
        return `<div class="tl-checkin">
          <div class="tl-checkin-time">${c.time}</div>
          <div class="tl-checkin-info">
            <div class="tl-checkin-venue">${fsUrl?`<a href="${fsUrl}" target="_blank" rel="noopener">${c.venue}</a>`:c.venue}</div>
            <div class="tl-checkin-sub">${[c.category,c.city,c.country].filter(Boolean).join(' · ')}</div>
          </div>
        </div>`;
      }).join('')}
    </div>`).join('');
  // Map
  if(activeMap){ activeMap.remove(); activeMap=null; }
  setTimeout(()=>{
    activeMap = L.map('detailMap',{preferCanvas:true});
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      {attribution:'© OpenStreetMap © CARTO',subdomains:'abcd',maxZoom:19}).addTo(activeMap);
    const coords = t.coords.filter(c=>c[0]&&c[1]);
    if(coords.length){
      const heat = L.heatLayer(coords,{radius:14,blur:16,maxZoom:18,
        gradient:{'0.0':'#000033','0.25':'#0a3d6b','0.5':'#e8b86d','0.75':'#ff7700','1.0':'#ff1100'}}).addTo(activeMap);
      const lats=coords.map(c=>c[0]), lngs=coords.map(c=>c[1]);
      activeMap.fitBounds([[Math.min(...lats),Math.min(...lngs)],[Math.max(...lats),Math.max(...lngs)]],{padding:[20,20]});
    }
  },50);
  window.scrollTo(0,0);
}

function showList(){
  document.getElementById('detailView').style.display = 'none';
  document.getElementById('listView').style.display = 'block';
  if(activeMap){ activeMap.remove(); activeMap=null; }
  history.pushState({},'','trips.html');
  window.scrollTo(0,0);
}

// Handle direct #trip-N links & back button
function handleHash(){
  const m = location.hash.match(/^#trip-(\d+)$/);
  if(m){ showTrip(parseInt(m[1])); }
  else { showList(); }
}
window.addEventListener('popstate', handleHash);
handleHash();
</script>
</body>
</html>"""


def build(data, trips, out_dir='.'):
    import os
    # ── index.html ──────────────────────────────────────────────────────────
    html = TEMPLATE
    html = html.replace('{{DATE_MIN}}',  data['date_min'])
    html = html.replace('{{DATE_MAX}}',  data['date_max'])
    html = html.replace('{{TOTAL}}',     f"{data['total']:,}")
    html = html.replace('{{COUNTRIES}}', str(len(data['countries'])))
    html = html.replace('{{CITIES}}',    f"{len(data['cities']):,}")
    html = html.replace('{{PLACES}}',    f"{data['unique_places_count']:,}")
    html = html.replace('{{UPDATED}}',   datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    html = html.replace('{{TRIPS}}',     str(data['trips_count']))
    html = html.replace('{{STATS}}',     json.dumps(data, ensure_ascii=False))
    idx_path = os.path.join(out_dir, 'index.html')
    with open(idx_path, 'w', encoding='utf-8') as f: f.write(html)
    print(f"Built → {idx_path}  ({len(html)//1024:,} KB)")

    # ── trips.html ──────────────────────────────────────────────────────────
    trips_html = TRIPS_TEMPLATE
    trips_html = trips_html.replace('{{TRIPS_JSON}}', json.dumps(trips, ensure_ascii=False))
    trips_html = trips_html.replace('{{TOTAL_TRIPS}}', str(len(trips)))
    trips_html = trips_html.replace('{{UPDATED}}', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    trips_path = os.path.join(out_dir, 'trips.html')
    with open(trips_path, 'w', encoding='utf-8') as f: f.write(trips_html)
    print(f"Built → {trips_path}  ({len(trips_html)//1024:,} KB)")


def save_category_list(csv_path, out_path):
    """Save full raw category list for review."""
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8')))
    cats = Counter(r.get('category','') for r in rows if r.get('category','').strip())
    lines = ["FULL CATEGORY LIST", "=" * 60,
             f"Total unique categories: {len(cats)}", ""]
    for cat, n in cats.most_common():
        lines.append(f"  {n:6,}  {cat}")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"Category list → {out_path}  ({len(cats)} categories)")


if __name__ == '__main__':
    if not os.path.exists(INPUT_CSV):
        print(f"ERROR: {INPUT_CSV} not found."); exit(1)
    print(f"Processing {INPUT_CSV}...")
    data, trips = process(INPUT_CSV)
    build(data, trips)
    save_category_list(INPUT_CSV, 'category_list.txt')
    print("Done!")
