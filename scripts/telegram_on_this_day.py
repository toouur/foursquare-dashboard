#!/usr/bin/env python3
"""Daily "on this day" story to Telegram.

For today's calendar date, finds every earlier year with check-ins on the same
day (checkins.csv + the sibling backfill.csv, run through the same transforms
the site uses), then posts one Telegram message:

  * a short narrative — places, weather, who you were with, home vs away —
    written by Claude when ANTHROPIC_API_KEY is set, otherwise a plain
    template, so the bot never stays silent because of the LLM;
  * a compact per-year list (city, weather, the day's places);
  * optionally an album of up to 4 photos from that day (photos.json + pix URL).

A check-in belongs to a day by its LOCAL date at the place it was made
(`metrics.trips._localise`), exactly like the index "on this day" card.
Home is era-aware (`_home_at`), so 2010 in Chișinău and 2018 in Minsk both read
as "home". "Home (private)" venues never leak a street name: they read "home".
Weather comes from the Open-Meteo archive (no key).

Exit codes: 0 sent / nothing to send / dry run, 1 Telegram send failed.

Usage:
  python scripts/telegram_on_this_day.py --csv private-data/checkins.csv \\
      --photos private-data/photos.json --pix-url "$R2_PUBLIC_URL" [--dry-run] [--date 2026-10-18]
Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY (optional),
     ANTHROPIC_MODEL (optional, default claude-sonnet-5-5).
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import logging
import os
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from transform import load_mappings, apply_transforms, build_blank_city_resolver  # noqa: E402
from metrics.trips import _parse_ts, _localise, _home_at  # noqa: E402
from metrics.companions import collect_companions  # noqa: E402

log = logging.getLogger("on_this_day")

HOME_CATEGORY = "Home (private)"
TG_LIMIT = 4096          # Telegram message hard limit
MAX_PLACES_PER_YEAR = 6
MAX_PHOTOS = 4
DEFAULT_MODEL = "claude-sonnet-5-5"

# WMO weather interpretation codes (Open-Meteo `weathercode`).
WMO = {
    0: ("☀️", "clear sky"), 1: ("🌤", "mostly clear"), 2: ("⛅", "partly cloudy"),
    3: ("☁️", "overcast"), 45: ("🌫", "fog"), 48: ("🌫", "freezing fog"),
    51: ("🌦", "light drizzle"), 53: ("🌦", "drizzle"), 55: ("🌧", "heavy drizzle"),
    56: ("🌧", "freezing drizzle"), 57: ("🌧", "freezing drizzle"),
    61: ("🌦", "light rain"), 63: ("🌧", "rain"), 65: ("🌧", "heavy rain"),
    66: ("🌧", "freezing rain"), 67: ("🌧", "freezing rain"),
    71: ("🌨", "light snow"), 73: ("🌨", "snow"), 75: ("❄️", "heavy snow"), 77: ("🌨", "snow grains"),
    80: ("🌦", "rain showers"), 81: ("🌧", "rain showers"), 82: ("⛈", "violent showers"),
    85: ("🌨", "snow showers"), 86: ("❄️", "heavy snow showers"),
    95: ("⛈", "thunderstorm"), 96: ("⛈", "thunderstorm with hail"), 99: ("⛈", "thunderstorm with hail"),
}


# ── Data loading ────────────────────────────────────────────────────────────

def load_settings(config_dir: Path) -> dict:
    path = config_dir / "settings.yaml"
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def home_config(settings: dict) -> tuple[str, list[tuple[int, str]]]:
    """(current home_city, sorted [(until_ts, city)]) — same parse as build.py."""
    trip_cfg = settings.get("trip_detection", {}) or {}
    history: list[tuple[int, str]] = []
    for hh in trip_cfg.get("home_history", []) or []:
        city = str(hh.get("city", "")).strip()
        until = str(hh.get("until", "")).strip()
        if city and until:
            ts = int(datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
            history.append((ts, city))
    history.sort(key=lambda p: p[0])
    return trip_cfg.get("home_city", "Minsk"), history


def load_rows(csv_path: Path, config_dir: Path) -> list[dict]:
    with open(csv_path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    backfill = csv_path.resolve().parent / "backfill.csv"
    if backfill.exists():
        with open(backfill, encoding="utf-8-sig") as fh:
            rows.extend(csv.DictReader(fh))
    rows.sort(key=lambda r: int(r.get("date") or 0))
    resolver = build_blank_city_resolver(config_dir / "city_merge_normalized_review.csv")
    return apply_transforms(rows, load_mappings(config_dir), blank_city_resolver=resolver)


def _float(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f else None


# ── Fact selection ──────────────────────────────────────────────────────────

def rows_on_this_day(rows: list[dict], today: date) -> dict[int, list[tuple[datetime, dict]]]:
    """{year: [(local_dt, row), …]} for earlier years whose LOCAL date is today's month/day."""
    out: dict[int, list[tuple[datetime, dict]]] = {}
    for r in rows:
        d = _parse_ts(r)
        if d is None:
            continue
        loc = _localise(d, _float(r.get("lat")), _float(r.get("lng")), r.get("country", ""))
        if (loc.month, loc.day) != (today.month, today.day) or loc.year >= today.year:
            continue
        out.setdefault(loc.year, []).append((loc, r))
    for items in out.values():
        items.sort(key=lambda p: p[0])
    return dict(sorted(out.items()))


def place_name(r: dict) -> str:
    if (r.get("category") or "") == HOME_CATEGORY:
        return "home"
    return (r.get("venue") or "").strip() or "somewhere"


def summarize_year(year: int, items: list[tuple[datetime, dict]], today: date,
                   home_city: str, home_history: list[tuple[int, str]],
                   photos: dict[str, list[str]] | None = None) -> dict:
    """Plain facts about one year's day — the only input the narrative sees."""
    ts0 = int(items[0][1].get("date") or 0)
    home = _home_at(ts0, home_city, home_history)
    city_counts = Counter((r.get("city") or "").strip() for _, r in items if (r.get("city") or "").strip())
    # Main city: most check-ins, ties go to the later one (where the day ended).
    main_city = ""
    if city_counts:
        best = max(city_counts.values())
        main_city = [c for c in (r.get("city", "").strip() for _, r in reversed(items)) if city_counts.get(c) == best][0]
    main_rows = [r for _, r in items if (r.get("city") or "").strip() == main_city] or [r for _, r in items]
    coords = [(_float(r.get("lat")), _float(r.get("lng"))) for r in main_rows]
    coords = [(a, b) for a, b in coords if a is not None and b is not None]
    lat = sum(a for a, _ in coords) / len(coords) if coords else None
    lng = sum(b for _, b in coords) / len(coords) if coords else None
    country = next(((r.get("country") or "").strip() for r in main_rows if (r.get("country") or "").strip()), "")

    places: list[dict] = []
    seen: set[str] = set()
    companions: list[str] = []
    shouts: list[str] = []
    photo_files: list[str] = []
    for loc, r in items:
        name = place_name(r)
        key = name.lower()
        if key not in seen:
            seen.add(key)
            places.append({"time": loc.strftime("%H:%M"), "name": name,
                           "category": r.get("category") or "", "city": (r.get("city") or "").strip()})
        for c in collect_companions(r):
            if c.lower() not in {x.lower() for x in companions}:
                companions.append(c)
        s = (r.get("shout") or "").replace("[geo_pinned]", "").strip()
        if s and not s.startswith("[") and s not in shouts:
            shouts.append(s)
        if photos:
            photo_files.extend(photos.get(r.get("checkin_id") or "", []))

    cities = list(dict.fromkeys(p["city"] for p in places if p["city"]))
    countries = list(dict.fromkeys((r.get("country") or "").strip() for _, r in items if (r.get("country") or "").strip()))
    return {
        "year": year,
        "years_ago": today.year - year,
        "home_city": home,
        # A day based in the home city reads as home even with a side trip (a dacha).
        "at_home": bool(main_city) and main_city == home,
        "main_city": main_city,
        "country": country,
        "cities": cities,
        "countries": countries,
        "checkins": len(items),
        "places": places,
        "companions": companions,
        "shouts": shouts[:3],
        "reconstructed": all((r.get("source_app") or "") == "refurbished" for _, r in items),
        "lat": lat, "lng": lng,
        "photos": photo_files,
        "weather": None,
    }


# ── Weather ─────────────────────────────────────────────────────────────────

def fetch_weather(lat: float | None, lng: float | None, day: date, session=requests) -> dict | None:
    if lat is None or lng is None:
        return None
    try:
        resp = session.get("https://archive-api.open-meteo.com/v1/archive", params={
            "latitude": round(lat, 3), "longitude": round(lng, 3),
            "start_date": day.isoformat(), "end_date": day.isoformat(),
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
            "timezone": "auto",
        }, timeout=20)
        resp.raise_for_status()
        d = resp.json().get("daily") or {}
        tmax, tmin = d.get("temperature_2m_max", [None])[0], d.get("temperature_2m_min", [None])[0]
        if tmax is None or tmin is None:
            return None
        code = d.get("weathercode", [None])[0]
        icon, desc = WMO.get(int(code), ("🌡", "")) if code is not None else ("🌡", "")
        return {"tmax": round(tmax), "tmin": round(tmin),
                "precip_mm": round(d.get("precipitation_sum", [0])[0] or 0, 1),
                "icon": icon, "desc": desc}
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        log.warning("weather lookup failed for %s: %s", day, exc)
        return None


def weather_text(w: dict | None) -> str:
    if not w:
        return ""
    return f"{w['icon']} {w['tmax']}°/{w['tmin']}°"


# ── Narrative ───────────────────────────────────────────────────────────────

def _llm_facts(facts: list[dict]) -> list[dict]:
    keep = ("year", "years_ago", "home_city", "at_home", "cities", "countries",
            "checkins", "places", "companions", "shouts", "weather", "reconstructed")
    return [{k: f[k] for k in keep} for f in facts]


PROMPT = """You write a short daily "on this day" note for one person's private Telegram chat, \
based on their own check-in history. Today is {today}. Below are the facts for this calendar day \
in earlier years (JSON). Places are in local time; "home" is their home; "at_home" means the whole \
day was spent in their home city; "reconstructed" days are rebuilt from old diaries at day \
granularity, so their times are not real. Shouts are their own words at the time (often Russian).

Write {words} words in {lang}, second person ("you"), warm and a little wry, like a friend \
remembering with them. Weave a narrative: where they were, what the day looked like (use the \
weather), who they were with, how the places connect into a day. Lead with the most interesting \
year (travel, a notable place, company) — don't march through every year; quiet home days can be \
grouped in one line. Never invent events, feelings or details not supported by the facts. \
Plain text only: no markdown, no headings, no lists, no hashtags. At most 2 emoji.

Facts:
{facts}"""


def claude_story(facts: list[dict], today: date, lang: str, api_key: str,
                 model: str = DEFAULT_MODEL, session=requests) -> str | None:
    words = "60-90" if len(facts) == 1 else "120-180"
    prompt = PROMPT.format(today=today.strftime("%d %B %Y"), words=words, lang=lang,
                           facts=json.dumps(_llm_facts(facts), ensure_ascii=False, indent=1))
    try:
        resp = session.post("https://api.anthropic.com/v1/messages", headers={
            "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json",
        }, json={"model": model, "max_tokens": 700,
                 "messages": [{"role": "user", "content": prompt}]}, timeout=60)
        resp.raise_for_status()
        text = "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text")
        return text.strip() or None
    except (requests.RequestException, ValueError) as exc:
        log.warning("Claude narrative failed, using template: %s", exc)
        return None


def template_story(facts: list[dict]) -> str:
    """No-LLM fallback: a sentence per trip away, quiet home years grouped in one line."""
    def ago(n: int) -> str:
        return f"{n} year{'s' if n != 1 else ''} ago"

    lines = []
    home_years: list[dict] = []
    for f in reversed(facts):
        if f["at_home"] and not f["companions"]:
            home_years.append(f)
            continue
        where = f["main_city"] or "somewhere"
        if f["country"] and not f["at_home"]:
            where += f", {f['country']}"
        line = f"{ago(f['years_ago'])} you were " + ("at home in " if f["at_home"] else "in ") + where
        w = f["weather"]
        if w:
            line += f" ({w['desc']}, {w['tmax']}°)" if w["desc"] else f" ({w['tmax']}°)"
        names = [p["name"] for p in f["places"] if p["name"] != "home"][:3]
        if names:
            line += ", stopping at " + ", ".join(names)
        if f["companions"]:
            line += " — with " + ", ".join(f["companions"][:3])
        lines.append(line.rstrip(".") + ".")
    if home_years:
        cities = list(dict.fromkeys(f["main_city"] for f in home_years))
        years = ", ".join(str(f["year"]) for f in home_years)
        lines.append(f"Quiet home days in {' and '.join(cities)}: {years}.")
    return "\n".join(lines)


# ── Message formatting ──────────────────────────────────────────────────────

def year_block(f: dict) -> str:
    e = html.escape
    where = f["main_city"] or "—"
    if f["country"]:
        where += f", {f['country']}"
    head = f"<b>{f['year']}</b> · {f['years_ago']}y ago — {'🏠' if f['at_home'] else '📍'} {e(where)}"
    w = weather_text(f["weather"])
    if w:
        head += f" · {w}"
    if f["reconstructed"]:
        head += " · ↺"
    places = [p for p in f["places"]][:MAX_PLACES_PER_YEAR]
    body = ", ".join(e(p["name"]) for p in places)
    extra = len(f["places"]) - len(places)
    if extra > 0:
        body += f" +{extra}"
    if f["companions"]:
        body += " · with " + e(", ".join(f["companions"][:4]))
    return head + "\n" + body


def build_message(facts: list[dict], story: str, today: date) -> str:
    header = f"📅 <b>On this day — {today.day} {today.strftime('%B')}</b>"
    story_html = html.escape(story)
    blocks = [year_block(f) for f in reversed(facts)]
    msg = "\n\n".join([header, story_html, "\n\n".join(blocks)])
    # Over the limit: drop the oldest year blocks first, then trim the story.
    while len(msg) > TG_LIMIT and blocks:
        blocks.pop()
        msg = "\n\n".join([header, story_html, "\n\n".join(blocks + ["…"])])
    if len(msg) > TG_LIMIT:
        msg = msg[:TG_LIMIT - 1] + "…"
    return msg


def pick_photos(facts: list[dict], limit: int = MAX_PHOTOS) -> list[str]:
    """One photo per year, newest years first, then fill up round-robin."""
    pools = [list(f["photos"]) for f in reversed(facts) if f["photos"]]
    out: list[str] = []
    while pools and len(out) < limit:
        for pool in list(pools):
            if len(out) >= limit:
                break
            out.append(pool.pop(0))
            if not pool:
                pools.remove(pool)
    return out


# ── Telegram ────────────────────────────────────────────────────────────────

def tg_send(token: str, chat_id: str, text: str, photo_urls: list[str], session=requests) -> bool:
    api = f"https://api.telegram.org/bot{token}"
    resp = session.post(f"{api}/sendMessage", data={
        "chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true",
    }, timeout=30)
    if not resp.ok:
        log.error("sendMessage failed: %s %s", resp.status_code, resp.text[:300])
        return False
    if photo_urls:
        try:
            if len(photo_urls) == 1:
                r = session.post(f"{api}/sendPhoto", data={"chat_id": chat_id, "photo": photo_urls[0]}, timeout=60)
            else:
                media = [{"type": "photo", "media": u} for u in photo_urls]
                r = session.post(f"{api}/sendMediaGroup",
                                 data={"chat_id": chat_id, "media": json.dumps(media)}, timeout=60)
            if not r.ok:   # photos are a bonus — the story already went out
                log.warning("photo send failed: %s %s", r.status_code, r.text[:300])
        except requests.RequestException as exc:
            log.warning("photo send failed: %s", exc)
    return True


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", required=True, help="checkins.csv (backfill.csv next to it is merged)")
    ap.add_argument("--config-dir", default=str(root / "config"))
    ap.add_argument("--photos", default="", help="photos.json ({checkin_id: [file, …]})")
    ap.add_argument("--pix-url", default="", help="public base URL of the photo files")
    ap.add_argument("--date", default="", help="YYYY-MM-DD to post for (default: today in --tz)")
    ap.add_argument("--tz", default="Europe/Chisinau", help="timezone that defines 'today'")
    ap.add_argument("--lang", default="English", help="narrative language")
    ap.add_argument("--no-weather", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the message, send nothing")
    args = ap.parse_args()

    today = (date.fromisoformat(args.date) if args.date
             else datetime.now(ZoneInfo(args.tz)).date())
    config_dir = Path(args.config_dir)
    home_city, home_history = home_config(load_settings(config_dir))
    photos: dict[str, list[str]] = {}
    if args.photos and Path(args.photos).exists() and args.pix_url:
        photos = json.loads(Path(args.photos).read_text(encoding="utf-8"))

    by_year = rows_on_this_day(load_rows(Path(args.csv), config_dir), today)
    if not by_year:
        print(f"No check-ins on {today:%d %B} in earlier years — nothing to send.")
        print("SENT=false")
        return 0

    facts = [summarize_year(y, items, today, home_city, home_history, photos)
             for y, items in by_year.items()]
    if not args.no_weather:
        for f in facts:
            f["weather"] = fetch_weather(f["lat"], f["lng"], date(f["year"], today.month, today.day))

    story = None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        story = claude_story(facts, today, args.lang, api_key,
                             os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL)
    story = story or template_story(facts)
    message = build_message(facts, story, today)
    pix = args.pix_url.rstrip("/")
    photo_urls = [f"{pix}/{p}" for p in pick_photos(facts)] if pix else []

    if args.dry_run:
        print(message)
        for u in photo_urls:
            print("PHOTO", u)
        print("SENT=false")
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        return 1
    ok = tg_send(token, chat, message, photo_urls)
    print(f"SENT={'true' if ok else 'false'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
