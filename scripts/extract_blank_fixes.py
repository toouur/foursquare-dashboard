"""
Parse blanks_output.txt and extract accepted city assignments for city_fixes.json.
Focused on Belarus + simple clear-cut fixes from other countries.
Outputs JSON-ready entries grouped by canonical city name.
"""
import re, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

INPUT = 'C:/tmp/blanks_output.txt'

# Already in city_fixes.json — load to avoid duplicates
with open('config/city_fixes.json', encoding='utf-8') as f:
    existing = set(k for k in json.load(f).keys() if not k.startswith('_'))

# ---- SKIP NAMES (district/region/highway markers) ----
SKIP_SET = {
    "Минский район", "Minski Rayon", "Hrodzyenski Rayon", "Logoyski Rayon",
    "Логойский р-н", "Минская Обл.", "Vitebsk Region", "Biahoml",
    "Kamyanyetski Rayon",
    # These map to cities >10km away or are ambiguous
    "д. Бенякони", "Страченята", "д. Страченята",
}
SKIP_PATTERNS = [
    r'район$', r'Rayon$', r'\sр-н$', r'область$', r'Обл\.$', r'\sRegion$',
    r'^д\.\s',  # "деревня" (village abbrev.)
]

# ---- CANONICAL NAME MAP (raw nearest-city → canonical) ----
# For Belarus city variants
BY_CANONICAL = {
    # Minsk variants
    "Mink": "Minsk", "Мiнск": "Minsk", "Минск": "Minsk",
    "Mińsk": "Minsk", "Мінск": "Minsk", "минск": "Minsk",
    "Минск - Гродно": "Minsk",
    # Vitebsk
    "Vitsyebsk": "Vitebsk", "Viciebsk": "Vitebsk",
    "Витебск": "Vitebsk", "Viciebsk": "Vitebsk",
    # Mogilev
    "Могилёв": "Mogilev", "Могилев": "Mogilev",
    "Магілёў": "Mogilev", "Magilëŭ": "Mogilev",
    # Grodno
    "Гродно": "Grodno", "Hrodna": "Grodno",
    # Gomel
    "Гомель": "Gomel", "Homieĺ": "Gomel",
    # Borisov
    "Борисов": "Borisov", "Barysaŭ": "Borisov",
    # Orsha
    "Orša": "Orsha", "Арша": "Orsha",
    # Baranovichi
    "Баранавічы": "Baranovichi", "Baranavičy": "Baranovichi",
    # Brest
    "Брэст": "Brest",
    # Polatsk
    "Полацк": "Polatsk", "Полоцк": "Polatsk",
    # Maladzyechna
    "Молодечно": "Maladzyechna", "Maladzechna": "Maladzyechna",
    # Lepel
    "Lyepyel'": "Lepel", "Лепель": "Lepel",
    # Pinsk
    "Пінск": "Pinsk",
    # Slutsk
    "Слуцк": "Slutsk",
    # Lida
    "Ліда": "Lida",
    # Salihorsk
    "Солигорск": "Salihorsk",
    # Zhodzina
    "Жодзіна": "Zhodzina",
    # Myadzyel
    "Мядзел": "Myadzyel",
    # Nyasvizh
    "Нясвіж": "Nyasvizh",
    # Navahrudak
    "Навагрудак": "Navahrudak",
    # Ashmyany
    "Ашмяны": "Ashmyany",
    # Pyershaje (village near Minsk)
    "Першаи": "Pyershaje",
    # Kalinkavichy
    "Калінкавічы": "Kalinkavichy",
    # Mazyr
    "Мазыр": "Mazyr",
    # Rečyca
    "Рэчыца": "Rečyca",
    # Braslaw
    "Браслаў": "Braslaw",
    # Vilyeyka
    "Вілейка": "Vilyeyka",
    # Slonim
    "Слонім": "Slonim",
    # Dzyarzhynsk
    "Дзяржынск": "Dzyarzhynsk",
    # Fanipol
    "Фаніпаль": "Fanipol",
    # Zhdanovichy
    "Ждановичи": "Zhdanovichy",
    # Kopishche
    "Копище": "Kopishche",
    # Luninyets
    "Лунінец": "Luninyets", "Лунинец": "Luninyets",
    # Byaroza
    "Бяроза": "Byaroza", "Biaroza": "Byaroza",
    # Pruzhany
    "Пружаны": "Pruzhany",
    # Kamianets
    "Камянец": "Kamianets",
    # Shchuchin
    "Шчучын": "Shchuchin", "Щучин": "Shchuchin",
    # Skidzyel'
    "Скідаль": "Skidzyel'",
    # Mir
    "Мір": "Mir",
    # Valozhyn
    "Воложин": "Valozhyn",
    # Mscislaw
    "Мсціслаў": "Mscislaŭ",
    # Krichev
    "Крычаў": "Kryčaŭ",
    # Bykhov
    "Быхаў": "Bychaŭ",
    # Bobruisk
    "Бабруйск": "Bobruisk",
    # Loyew
    "Лоеў": "Loyew",
    # Navapolatsk
    "Наваполацк": "Navapolatsk",
    # Minsk suburb names (Russian/Belarusian variants)
    "Щомыслица": "Shchomyslitsa",
    "Дегтяревка": "Degtaryovka",
    "Зелёное": "Zyelyonoye",
    # Asipovichy
    "Асіповічы": "Asipovichy",
    # Russia
    "Москва": "Moscow", "Санкт-Петербург": "Saint Petersburg",
    "Питер": "Saint Petersburg", "Piter": "Saint Petersburg",
}

# ---- DISTANCE THRESHOLDS ----
# Large cities: 8km; everything else: 5.5km
LARGE_CANONICAL = {
    "Minsk", "Grodno", "Brest", "Vitebsk", "Mogilev", "Gomel",
    "Baranovichi", "Borisov", "Orsha", "Maladzyechna", "Pinsk",
    "Polatsk", "Salihorsk", "Lepel", "Slonim",
}

# ---- VALID CITY NAMES (accept as-is after canonical check) ----
# These are real place names in Belarus that are acceptable to use
BY_VALID_NAMES = {
    "Minsk", "Grodno", "Brest", "Vitebsk", "Mogilev", "Gomel",
    "Baranovichi", "Borisov", "Orsha", "Maladzyechna", "Pinsk",
    "Polatsk", "Navapolatsk", "Salihorsk", "Slutsk", "Lepel", "Slonim",
    "Vitebsk Region",  # handled by skip
    "Lida", "Valozhyn", "Mscislaŭ", "Kryčaŭ", "Bychaŭ",
    "Rečyca", "Loyew", "Myadzyel", "Nyasvizh", "Navahrudak",
    "Asipovichy", "Vilyeyka", "Kopyś", "Shklov", "Zhodzina",
    "Kalinkavichy", "Mazyr", "Braslaw", "Dzyarzhynsk", "Fanipol",
    "Zhdanovichy", "Kopishche", "Luninyets", "Byaroza", "Pruzhany",
    "Kamianets", "Kamianiuki", "Shchuchin", "Skidzyel'", "Mir",
    "Ashmyany", "Pyershaje", "Rakov", "Borovlyany", "Zaslawye",
    "Astrashytski Haradok", "Kolodishchi", "Valer'yanovo", "Machulishchy",
    "Ptich'", "Atolino",
    # Minsk suburb names (even Russian-named ones are OK for Belarus)
    "Shchomyslitsa", "Degtaryovka", "Zyelyonoye",
    # Russian places (from Russia section)
    "Saint Petersburg", "Moscow", "Krasnoyarsk",
    # Other countries
    "Stockholm", "Taipa", "Vatican City",
    "Vaduz", "Schaan", "Prague", "Rotterdam", "Doha",
    "Tirana", "Bratislava", "Dubai", "Oslo", "Sandefjord",
    # Locations that appear in blanks which are district-level but still usable
    "Новая Гута", "Хотислав",
    # Small Minsk suburbs from session summary
    "Сеница", "Прилуки", "Заречье-1", "Новая Боровая", "Kreva",
    "Домошаны", "Samakhvalavichy", "Чики", "Старое село", "Гонолес",
    "Grigorovshchina", "Maladzyechna",
}

# ---- PARSE blanks_output.txt ----
line_re = re.compile(r'ts=(\S+)\s+lat=([\d.-]+)\s+lng=([\d.-]+)\s+->\s+(.+?)\s+\(([\d.]+)km\)')

results = {}  # canonical_city → list of ids

with open(INPUT, encoding='utf-8') as f:
    for line in f:
        m = line_re.search(line)
        if not m:
            continue
        ts, lat_s, lng_s, raw_city, dist_s = m.groups()
        dist = float(dist_s)

        if ts in existing:
            continue

        # Skip district/region names
        if raw_city in SKIP_SET:
            continue
        skip = False
        for pat in SKIP_PATTERNS:
            if re.search(pat, raw_city):
                skip = True
                break
        if skip:
            continue

        # Canonicalize
        canonical = BY_CANONICAL.get(raw_city, raw_city)
        if canonical is None:
            continue

        # Check if this is a valid name we accept
        if canonical not in BY_VALID_NAMES:
            continue

        # Distance threshold
        max_km = 8.0 if canonical in LARGE_CANONICAL else 5.5
        if dist > max_km:
            continue

        if canonical not in results:
            results[canonical] = []
        results[canonical].append(ts)

# ---- OUTPUT ----
print(f"// Total cities: {len(results)}, total entries: {sum(len(v) for v in results.values())}")
for city in sorted(results.keys()):
    ids = results[city]
    print(f"\n  // === {city} ({len(ids)} entries) ===")
    for ts in ids:
        print(f'  "{ts}": "{city}",')
