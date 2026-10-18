#!/usr/bin/env python3
"""Daily "on this day" story to Telegram.

For today's calendar date, finds every earlier year with check-ins on the same
day (checkins.csv + the sibling backfill.csv, run through the same transforms
the site uses), then posts:

  * a narrative — places, weather, who you were with, home vs travel — written
    by Claude, otherwise a plain template, so the bot never stays silent
    because of the LLM. Claude is reached through the API when
    ANTHROPIC_API_KEY is set, else through the Claude Code CLI (`claude -p`)
    when CLAUDE_CODE_OAUTH_TOKEN is set — a Pro/Max subscription token from
    `claude setup-token`, no API billing;
  * every check-in of every year, newest year first: 🏠 home day / ✈️ travel
    day, local time, category emoji, venue, and the companions + shout on the
    exact check-in they belong to. Split over several messages when it outgrows
    Telegram's 4096-character limit;
  * one photo album per year that has photos (photos.json + pix URL, ≤10 each).

A check-in belongs to a day by its LOCAL date at the place it was made
(`metrics.trips._localise`), exactly like the index "on this day" card.
Home is era-aware (`_home_at`), so 2010 in Chișinău and 2018 in Minsk both read
as "home". "Home (private)" venues never leak a street name: they read "home"
in the home city of that era and "the flat" anywhere else (a rented place on a
trip). Weather comes from the Open-Meteo archive (no key).

Exit codes: 0 sent / nothing to send / dry run, 1 Telegram send failed.

Usage:
  python scripts/telegram_on_this_day.py --csv private-data/checkins.csv \\
      --photos private-data/photos.json --pix-url "$R2_PUBLIC_URL" [--dry-run] [--date 2026-10-18]
Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY (optional),
     CLAUDE_CODE_OAUTH_TOKEN (optional, used when there is no API key),
     ANTHROPIC_MODEL (optional, default claude-sonnet-5-5).
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import logging
import os
import shutil
import subprocess
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
ALBUM_MAX = 10           # sendMediaGroup takes 2-10 items
SHOUT_MAX = 200
DEFAULT_EMOJI = "📍"
DEFAULT_MODEL = "claude-sonnet-5-5"

# WMO weather interpretation codes (Open-Meteo `weather_code`).
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


def load_icons(config_dir: Path) -> dict[str, str]:
    """{category: emoji} from config/category_icons.json ({cat: [emoji, color]})."""
    path = config_dir / "category_icons.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: v[0] for k, v in raw.items() if isinstance(v, list) and v and not k.startswith("_")}


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


def place_name(r: dict, home_city: str = "") -> str:
    """Venue name; a private home reads "home" in that era's home city, "the flat" elsewhere."""
    if (r.get("category") or "") == HOME_CATEGORY:
        if not home_city or (r.get("city") or "").strip() == home_city:
            return "home"
        return "the flat"
    return (r.get("venue") or "").strip() or "somewhere"


def clean_shout(s: str | None) -> str:
    """The person's own words, minus machine markers ([geo_pinned], [coords_approx…])."""
    s = (s or "").replace("[geo_pinned]", "").strip()
    if not s or s.startswith("["):
        return ""
    return s if len(s) <= SHOUT_MAX else s[:SHOUT_MAX - 1].rstrip() + "…"


def summarize_year(year: int, items: list[tuple[datetime, dict]], today: date,
                   home_city: str, home_history: list[tuple[int, str]],
                   photos: dict[str, list[str]] | None = None,
                   icons: dict[str, str] | None = None) -> dict:
    """Plain facts about one year's day — the only input the narrative sees."""
    ts0 = int(items[0][1].get("date") or 0)
    home = _home_at(ts0, home_city, home_history)
    icons = icons or {}
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

    stops: list[dict] = []
    companions: list[str] = []
    shouts: list[str] = []
    photo_files: list[str] = []
    for loc, r in items:
        cat = r.get("category") or ""
        stop_photos = list((photos or {}).get(r.get("checkin_id") or "", []))
        stop_with = collect_companions(r)
        shout = clean_shout(r.get("shout"))
        stops.append({
            "time": loc.strftime("%H:%M"), "name": place_name(r, home), "category": cat,
            "city": (r.get("city") or "").strip(), "emoji": icons.get(cat, DEFAULT_EMOJI),
            "companions": stop_with, "shout": shout, "photos": stop_photos,
        })
        for c in stop_with:
            if c.lower() not in {x.lower() for x in companions}:
                companions.append(c)
        if shout and shout not in shouts:
            shouts.append(shout)
        photo_files.extend(stop_photos)

    cities = list(dict.fromkeys(s["city"] for s in stops if s["city"]))
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
        "stops": stops,
        "companions": companions,
        "shouts": shouts,
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
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
            "timezone": "auto",
        }, timeout=20)
        resp.raise_for_status()
        d = resp.json().get("daily") or {}
        tmax, tmin = d.get("temperature_2m_max", [None])[0], d.get("temperature_2m_min", [None])[0]
        if tmax is None or tmin is None:
            return None
        code = (d.get("weather_code") or d.get("weathercode") or [None])[0]
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
    keep = ("year", "years_ago", "home_city", "at_home", "main_city", "cities", "countries",
            "checkins", "companions", "weather", "reconstructed")
    out = []
    for f in facts:
        d = {k: f[k] for k in keep}
        d["stops"] = [{k: s[k] for k in ("time", "name", "category", "city", "companions", "shout")
                       if s[k] or k in ("time", "name")} for s in f["stops"]]
        out.append(d)
    return out


PROMPT = """You write the daily "on this day" story for one person's private Telegram channel, \
based on their own check-in history. Today is {today}. Below are the facts for this calendar day \
in earlier years (JSON).

How to read them: "stops" is every check-in of that day in order, with LOCAL time; each stop's \
"companions" are the people tagged on that exact check-in and "shout" is what the person wrote \
there at the time (often Russian — quote or paraphrase it, translated if needed). "home" is their \
home; "the flat" is a rented place or lodging while travelling. "at_home" means the day was based \
in their home city of that era ("home_city" — it moved over the years); otherwise it was a travel \
day. "reconstructed" days are rebuilt from old diaries at day granularity, so their times are not \
real — don't mention clock times for them.

Write {words} words in {lang}, second person ("you"), warm and a little wry, like an old friend \
flipping through a photo album with them. Tell it as a story: open with the most interesting year \
(travel, a notable place, good company), follow the shape of that day — morning to evening, how \
one place led to the next, the weather, who joined when — then move through the other years more \
briefly; quiet home days can share one sentence. Short paragraphs separated by a blank line are \
welcome. Never invent events, feelings or details the facts don't support. Plain text only: no \
markdown, no headings, no lists, no hashtags. At most 2 emoji.

Facts:
{facts}"""


def _story_prompt(facts: list[dict], today: date, lang: str) -> str:
    words = "80-120" if len(facts) == 1 else "150-250"
    return PROMPT.format(today=today.strftime("%d %B %Y"), words=words, lang=lang,
                         facts=json.dumps(_llm_facts(facts), ensure_ascii=False, indent=1))


def claude_story(facts: list[dict], today: date, lang: str, api_key: str,
                 model: str = DEFAULT_MODEL, session=requests) -> str | None:
    prompt = _story_prompt(facts, today, lang)
    try:
        resp = session.post("https://api.anthropic.com/v1/messages", headers={
            "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json",
        }, json={"model": model, "max_tokens": 1200,
                 "messages": [{"role": "user", "content": prompt}]}, timeout=90)
        resp.raise_for_status()
        text = "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text")
        return text.strip() or None
    except (requests.RequestException, ValueError) as exc:
        log.warning("Claude narrative failed, using template: %s", exc)
        return None


def claude_cli_story(facts: list[dict], today: date, lang: str, model: str = "",
                     run=subprocess.run) -> str | None:
    """Same prompt through the Claude Code CLI (`claude -p`), authenticated by
    CLAUDE_CODE_OAUTH_TOKEN — a Pro/Max subscription instead of API billing.
    The prompt goes over stdin (no argv length limit). Any failure → None."""
    exe = shutil.which("claude")
    if not exe:
        log.warning("claude CLI not found, using template")
        return None
    cmd = [exe, "-p", "--output-format", "text"] + (["--model", model] if model else [])
    try:
        res = run(cmd, input=_story_prompt(facts, today, lang), capture_output=True,
                  text=True, encoding="utf-8", timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("claude CLI failed, using template: %s", exc)
        return None
    if res.returncode != 0:
        log.warning("claude CLI exited %s, using template: %s",
                    res.returncode, (res.stderr or res.stdout or "").strip()[:300])
        return None
    return (res.stdout or "").strip() or None


def _ago(n: int) -> str:
    return f"{n} year{'s' if n != 1 else ''} ago"


def template_story(facts: list[dict]) -> str:
    """No-LLM fallback: a paragraph per notable year, quiet home years grouped in one line."""
    paras = []
    home_years: list[dict] = []
    for f in reversed(facts):
        if f["at_home"] and not f["companions"] and not f["shouts"]:
            home_years.append(f)
            continue
        where = f["main_city"] or "somewhere"
        if f["country"] and not f["at_home"]:
            where += f", {f['country']}"
        if f["at_home"]:
            line = f"{_ago(f['years_ago'])}, a home day in {where}"
        else:
            line = f"{_ago(f['years_ago'])} you were in {where}"
        w = f["weather"]
        if w:
            line += f" — {w['desc']}, {w['tmax']}°" if w["desc"] else f" — {w['tmax']}°"
        line += "."
        stops = f["stops"]
        named = [s for s in stops if s["name"] not in ("home", "the flat", "somewhere")] or stops
        if len(stops) == 1 or f["reconstructed"]:
            line += f" {named[0]['name']}" + (f" and {len(stops) - 1} more" if len(stops) > 1 else "") + "."
        else:
            first, last = named[0], named[-1]
            line += (f" The day ran from {first['name']} ({first['time']}) to "
                     f"{last['name']} ({last['time']}) across {len(stops)} check-ins")
            others = [c for c in f["cities"] if c != f["main_city"]]
            if others:
                line += ", through " + ", ".join(others[:3])
            line += "."
        if f["companions"]:
            line += " With " + ", ".join(f["companions"][:4]).rstrip(".") + "."
        if f["shouts"]:
            line += f" You wrote: “{f['shouts'][0]}”"
        paras.append(line)
    if home_years:
        cities = list(dict.fromkeys(f["main_city"] for f in home_years))
        years = ", ".join(str(f["year"]) for f in home_years)
        paras.append(f"Quiet home days in {' and '.join(cities)}: {years}.")
    return "\n\n".join(paras)


# ── Message formatting ──────────────────────────────────────────────────────

def year_head(f: dict) -> str:
    where = f["main_city"] or "—"
    if f["country"]:
        where += f", {f['country']}"
    head = (f"{'🏠' if f['at_home'] else '✈️'} <b>{f['year']}</b> · {_ago(f['years_ago'])} — "
            f"{html.escape(where)}")
    w = weather_text(f["weather"])
    if w:
        head += f" · {w}"
    if f["reconstructed"]:
        head += " · ↺ reconstructed"
    return head


def stop_line(s: dict, main_city: str, reconstructed: bool) -> str:
    e = html.escape
    line = ("" if reconstructed else f"{s['time']} ") + f"{s['emoji']} {e(s['name'])}"
    if s["city"] and s["city"] != main_city:
        line += f" · <i>{e(s['city'])}</i>"
    if s["companions"]:
        line += " — with " + e(", ".join(s["companions"]))
    if s["shout"]:
        line += f" 💬 <i>{e(s['shout'])}</i>"
    if s["photos"]:
        line += " 📷"
    return line


def year_lines(f: dict) -> list[str]:
    return [year_head(f)] + [stop_line(s, f["main_city"], f["reconstructed"]) for s in f["stops"]]


def _cut(text: str) -> str:
    return text if len(text) <= TG_LIMIT else text[:TG_LIMIT - 1] + "…"


def build_messages(facts: list[dict], story: str, today: date) -> list[str]:
    """Header + story, then every year newest first, packed into ≤4096-char messages.

    Whole year blocks are kept together when they fit; a block that does not
    fit in an empty message flows line by line, repeating its head "(cont.)".
    """
    header = f"📅 <b>On this day — {today.day} {today.strftime('%B')}</b>"
    chunks: list[str] = []
    cur = _cut(header + "\n\n" + html.escape(story, quote=False))
    for f in reversed(facts):
        lines = year_lines(f)
        block = "\n".join(lines)
        if len(cur) + 2 + len(block) <= TG_LIMIT:
            cur += "\n\n" + block
            continue
        if len(block) <= TG_LIMIT:
            chunks.append(cur)
            cur = block
            continue
        # Too big for any one message: flow it line by line.
        sep = "\n\n"
        for i, line in enumerate(lines):
            line = _cut(line)
            if len(cur) + len(sep) + len(line) > TG_LIMIT:
                chunks.append(cur)
                cur = lines[0] + " <i>(cont.)</i>" if i else ""
                sep = "\n" if cur else ""
            cur += sep + line
            sep = "\n"
    if cur:
        chunks.append(cur)
    return [_cut(c) for c in chunks if c]


def photo_albums(facts: list[dict], pix: str) -> list[tuple[str, list[str]]]:
    """[(caption, urls≤10)] — one album per year with photos, newest year first."""
    if not pix:
        return []
    out = []
    for f in reversed(facts):
        if not f["photos"]:
            continue
        files = list(dict.fromkeys(f["photos"]))
        if len(files) > ALBUM_MAX:   # spread the picks over the whole day
            step = len(files) / ALBUM_MAX
            files = [files[int(i * step)] for i in range(ALBUM_MAX)]
        cap = f"{f['year']} · {f['main_city']}" if f["main_city"] else str(f["year"])
        out.append((cap, [f"{pix}/{p}" for p in files]))
    return out


# ── Telegram ────────────────────────────────────────────────────────────────

def tg_send(token: str, chat_id: str, texts: list[str], albums: list[tuple[str, list[str]]],
            session=requests) -> bool:
    api = f"https://api.telegram.org/bot{token}"
    for text in texts:
        resp = session.post(f"{api}/sendMessage", data={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true",
        }, timeout=30)
        if not resp.ok:
            log.error("sendMessage failed: %s %s", resp.status_code, resp.text[:300])
            return False
    for caption, urls in albums:
        try:
            if len(urls) == 1:
                r = session.post(f"{api}/sendPhoto", data={
                    "chat_id": chat_id, "photo": urls[0], "caption": caption}, timeout=60)
            else:
                media = [{"type": "photo", "media": u} for u in urls]
                media[0]["caption"] = caption
                r = session.post(f"{api}/sendMediaGroup",
                                 data={"chat_id": chat_id, "media": json.dumps(media)}, timeout=60)
            if not r.ok:   # photos are a bonus — the story already went out
                log.warning("photo send failed (%s): %s %s", caption, r.status_code, r.text[:300])
        except requests.RequestException as exc:
            log.warning("photo send failed (%s): %s", caption, exc)
    return True


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # a Windows console is cp1252
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
    ap.add_argument("--dry-run", action="store_true", help="print the messages, send nothing")
    args = ap.parse_args()

    today = (date.fromisoformat(args.date) if args.date
             else datetime.now(ZoneInfo(args.tz)).date())
    config_dir = Path(args.config_dir)
    home_city, home_history = home_config(load_settings(config_dir))
    icons = load_icons(config_dir)
    photos: dict[str, list[str]] = {}
    if args.photos and Path(args.photos).exists() and args.pix_url:
        photos = json.loads(Path(args.photos).read_text(encoding="utf-8"))

    by_year = rows_on_this_day(load_rows(Path(args.csv), config_dir), today)
    if not by_year:
        print(f"No check-ins on {today:%d %B} in earlier years — nothing to send.")
        print("SENT=false")
        return 0

    facts = [summarize_year(y, items, today, home_city, home_history, photos, icons)
             for y, items in by_year.items()]
    if not args.no_weather:
        for f in facts:
            f["weather"] = fetch_weather(f["lat"], f["lng"], date(f["year"], today.month, today.day))

    story = None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    model = os.environ.get("ANTHROPIC_MODEL", "").strip()
    if api_key:
        story = claude_story(facts, today, args.lang, api_key, model or DEFAULT_MODEL)
    elif os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        story = claude_cli_story(facts, today, args.lang, model)
    else:
        log.info("no ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN — template story")
    story = story or template_story(facts)
    messages = build_messages(facts, story, today)
    albums = photo_albums(facts, args.pix_url.rstrip("/"))

    if args.dry_run:
        for i, m in enumerate(messages, 1):
            print(f"──── message {i}/{len(messages)} ({len(m)} chars) ────")
            print(m)
        for caption, urls in albums:
            for u in urls:
                print("PHOTO", caption, u)
        print("SENT=false")
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        return 1
    ok = tg_send(token, chat, messages, albums)
    print(f"SENT={'true' if ok else 'false'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
