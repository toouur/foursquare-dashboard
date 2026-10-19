#!/usr/bin/env python3
"""Check-in echo: a mini history story to Telegram for notable new check-ins.

Every new check-in is read against the whole history before it (checkins.csv +
backfill.csv, the same transforms as the site) and may raise events:

  new_country  first check-in ever in that country             → instant
  new_city     first check-in ever in that city                → instant
  city_back    first check-in in a city after ≥1 year away     → instant
  comeback     back at a venue after ≥5 years                  → instant
               … after ≥1 year                                 → digest
  milestone    50th / 100th / 250th / 500th / 1000th visit      → instant
               10th / 25th visit                               → digest
  total        every 1000th check-in overall                   → instant
  new_venue    a place never checked in before                 → digest

Two modes, both posting to the "on this day" channel:

  --mode instant  (after each hourly data update) one message per check-in with
                  an instant event, at most MAX_INSTANT per run, only for
                  check-ins newer than the stored watermark and fresher than
                  MAX_AGE_H — a backlog after an outage goes to the digest only.
  --mode digest   (once a day, for yesterday's local date) one message listing
                  every event of that day under a short narrative.

Text: Claude writes it (API with ANTHROPIC_API_KEY, else the Claude Code CLI
with CLAUDE_CODE_OAUTH_TOKEN), otherwise a template — the bot never stays
silent because of the LLM. Home (private) check-ins never raise events.

State (--state, checkin_echo_state.json in the data repo):
  {"last_ts": int, "instant": [checkin_id, …], "digests": ["YYYY-MM-DD", …]}
The first instant run without a state only records the watermark and posts
nothing, so switching the bot on never replays history. The watermark moves
only past check-ins whose message actually went out.

Exit codes: 0 sent / nothing to send / dry run, 1 a Telegram send failed.
Prints EVENTS=<n> (messages it would send) and SENT=true/false.

Usage:
  python scripts/telegram_checkin_echo.py --mode instant --csv private-data/checkins.csv \\
      --state private-data/checkin_echo_state.json [--dry-run] [--plan]
  python scripts/telegram_checkin_echo.py --mode digest  --csv private-data/checkins.csv \\
      --state private-data/checkin_echo_state.json [--date 2026-10-18] [--dry-run]
Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN
     (optional), ANTHROPIC_MODEL (optional).
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from telegram_on_this_day import (  # noqa: E402
    DEFAULT_EMOJI, DEFAULT_MODEL, HOME_CATEGORY, _float, clean_shout, load_icons,
    load_rows, place_name, tg_send,
)
from metrics.trips import _parse_ts, _localise  # noqa: E402
from metrics.companions import collect_companions  # noqa: E402

log = logging.getLogger("checkin_echo")

INSTANT_MILESTONES = {50, 100, 250, 500, 1000}
DIGEST_MILESTONES = {10, 25}
COMEBACK_INSTANT_DAYS = 5 * 365
COMEBACK_DIGEST_DAYS = 365
TOTAL_STEP = 1000
MAX_INSTANT = 3          # instant messages per run; anything beyond waits for the digest
MAX_AGE_H = 36           # an older check-in is news for the digest, not a ping
KEEP_IDS = 300           # instant ids remembered (the digest marks them as already told)
KEEP_DIGESTS = 120
INSTANT_KINDS = ("new_country", "new_city", "city_back", "comeback_long", "milestone_big", "total")
REFURB = "refurbished"
DIGEST_LINES = 15        # event lines in one digest; the rest is summarised
# Places passed through rather than visited: a new street or bus line is true
# but dull, so it sorts last in a digest instead of crowding out a new bar.
PASS_THROUGH = frozenset({
    "Road", "Street", "Intersection", "Bridge", "Bus Line", "Bus Stop", "Tram Station",
    "Metro Station", "Light Rail Station", "Train", "Taxi", "Neighborhood", "City", "Town",
    "Village", "County", "State", "Country", "Highway", "Moving Target",
})
RANK = {"new_country": 0, "new_city": 1, "city_back": 1, "total": 2, "milestone_big": 3, "comeback_long": 4,
        "milestone": 5, "comeback": 6, "new_venue": 7}


# ── History index ───────────────────────────────────────────────────────────

class History:
    """Rows in time order with each check-in's local datetime precomputed."""

    def __init__(self, rows: list[dict]):
        self.rows: list[dict] = []
        self.local: list[datetime] = []
        for r in rows:
            d = _parse_ts(r)
            if d is None:
                continue
            self.rows.append(r)
            self.local.append(_localise(d, _float(r.get("lat")), _float(r.get("lng")),
                                        r.get("country", "")))
        self.pos = {r.get("checkin_id"): i for i, r in enumerate(self.rows) if r.get("checkin_id")}

    def is_real(self, i: int) -> bool:
        r = self.rows[i]
        return (r.get("source_app") or "") != REFURB and r.get("category") != HOME_CATEGORY


def _ord(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _years_days(days: int) -> str:
    if days >= 365:
        y = days // 365
        return f"{y} year{'s' if y != 1 else ''}"
    if days >= 60:
        return f"{days // 30} months"
    return f"{days} day{'s' if days != 1 else ''}"


def facts_for(h: History, i: int) -> dict:
    """Everything the history says about check-in i, before it happened."""
    r, now = h.rows[i], h.local[i]
    vid, cat = r.get("venue_id", ""), r.get("category", "")
    city, country = r.get("city", ""), r.get("country", "")
    name = place_name(r)
    same: list[int] = []
    cat_venues: set[str] = set()
    last_cat: int | None = None
    chain: set[str] = set()
    seen_city = False
    last_city: int | None = None
    countries: set[str] = set()
    for j in range(i):
        p = h.rows[j]
        pv = p.get("venue_id", "")
        if vid and pv == vid:
            same.append(j)
        elif cat and p.get("category") == cat:
            cat_venues.add(pv or place_name(p))
            last_cat = j
            if pv and place_name(p) == name:
                chain.add(pv)
        if city and p.get("city") == city:
            seen_city, last_city = True, j
        if p.get("country"):
            countries.add(p["country"])
    seen_country = country in countries
    comp = collect_companions(r)
    together = Counter(c for j in same for c in collect_companions(h.rows[j]))
    f: dict = {
        "venue": name, "category": cat, "city": city, "country": country,
        "local_time": now.strftime("%Y-%m-%d %H:%M"),
        "companions": comp, "shout": clean_shout(r.get("shout")),
        "visit_no": len(same) + 1,
        "checkin_no": i + 1,
        "category_places_before": len(cat_venues),
        "same_name_elsewhere": len(chain),
        "first_in_city": bool(city) and not seen_city,
        "first_in_country": bool(country) and not seen_country,
        "country_no": len(countries) + (0 if seen_country else 1),
    }
    if same:
        first, last = h.local[same[0]], h.local[same[-1]]
        f["first_visit"] = first.strftime("%Y-%m-%d")
        f["last_visit"] = last.strftime("%Y-%m-%d")
        f["days_since_last"] = (now.date() - last.date()).days
        f["visits_by_year"] = dict(sorted(Counter(h.local[j].year for j in same).items()))
        f["with_here_before"] = {c: together[c] for c in comp if together.get(c)}
    if last_city is not None:
        f["days_since_city"] = (now.date() - h.local[last_city].date()).days
        f["last_in_city"] = h.local[last_city].strftime("%Y-%m-%d")
    if last_cat is not None:
        lc = h.rows[last_cat]
        f["last_in_category"] = {"venue": place_name(lc), "city": lc.get("city", ""),
                                 "date": h.local[last_cat].strftime("%Y-%m-%d")}
    return f


def events(f: dict) -> list[str]:
    """Event kinds raised by one check-in's facts, strongest first."""
    out: list[str] = []
    if f["first_in_country"]:
        out.append("new_country")
    if f["first_in_city"]:
        out.append("new_city")
    elif (f.get("days_since_city") or 0) >= COMEBACK_DIGEST_DAYS:
        out.append("city_back")
    gap = f.get("days_since_last")
    if gap is not None and gap >= COMEBACK_INSTANT_DAYS:
        out.append("comeback_long")
    elif gap is not None and gap >= COMEBACK_DIGEST_DAYS:
        out.append("comeback")
    if f["visit_no"] in INSTANT_MILESTONES:
        out.append("milestone_big")
    elif f["visit_no"] in DIGEST_MILESTONES:
        out.append("milestone")
    if f["category"] in PASS_THROUGH:
        # A street or a bus line is never worth a ping on its own.
        out = [{"comeback_long": "comeback", "milestone_big": "milestone"}.get(k, k) for k in out]
    if f["checkin_no"] % TOTAL_STEP == 0:
        out.append("total")
    if f["visit_no"] == 1:
        out.append("new_venue")
    return out


def is_instant(kinds: list[str]) -> bool:
    return any(k in INSTANT_KINDS for k in kinds)


# ── Text ────────────────────────────────────────────────────────────────────

def headline(f: dict, kinds: list[str]) -> str:
    """One plain line saying why this check-in is news."""
    k = kinds[0]
    if k == "new_country":
        return f"New country — your #{f['country_no']}: {f['country']}"
    if k == "new_city":
        return f"First time ever in {f['city']}"
    if k == "city_back":
        return f"Back in {f['city']} after {_years_days(f['days_since_city'])} (last time {f['last_in_city']})"
    if k in ("comeback", "comeback_long"):
        return (f"Back after {_years_days(f['days_since_last'])} "
                f"(last time {f['last_visit']}, visit #{f['visit_no']})")
    if k in ("milestone", "milestone_big"):
        return f"Visit #{f['visit_no']} (first one {f['first_visit']})"
    if k == "total":
        return f"Check-in #{f['checkin_no']:,} overall"
    n = f["category_places_before"]
    extra = (f", your {_ord(f['same_name_elsewhere'] + 1)} {f['venue']}"
             if f["same_name_elsewhere"] else "")
    return (f"New place — {_ord(n + 1)} {f['category']}{extra}" if n else
            f"New place — your first ever {f['category'] or 'spot like this'}")


def digest_rank(f: dict, kinds: list[str]) -> tuple:
    """Sort key: strongest event first, pass-through places last, longer comebacks first."""
    return (RANK.get(kinds[0], 9), f["category"] in PASS_THROUGH, -(f.get("days_since_last") or 0))


def template_instant(f: dict, kinds: list[str]) -> str:
    lines = [headline(f, kinds) + "."]
    if f.get("with_here_before"):
        lines.append("; ".join(f"With {c} here {n + 1} times now" for c, n in f["with_here_before"].items()) + ".")
    lc = f.get("last_in_category")
    if f["visit_no"] == 1 and lc:
        lines.append(f"Last {f['category']} before this: {lc['venue']}, {lc['city']} ({lc['date']}).")
    return " ".join(lines)


INSTANT_PROMPT = """You write a one-message "check-in echo" for one person's private Telegram \
channel: they just checked in somewhere, and their own history makes it notable. The facts (JSON) \
were computed from every check-in BEFORE this one. "events" says why it is notable: new_country / \
new_city = first time ever there; city_back = first check-in in this city after a year or more \
away ("days_since_city"); comeback_long = back at this exact place after 5+ years \
("days_since_last"); milestone_big = a round visit number ("visit_no"); total = a round overall \
check-in number. "visits_by_year" counts earlier visits; "with_here_before" counts earlier visits \
here together with someone tagged now; "shout" is what they wrote (often Russian — paraphrase in \
English). "category_places_before" is how many other places of that category they visited before.

Write {words} words in English, second person ("you"), warm and a little wry, like a friend who \
remembers everything. Lead with what makes it notable, add one or two telling details from the \
history. Never invent anything the facts don't support. Plain text only, no markdown, no \
hashtags, at most 1 emoji.

Facts:
{facts}"""

DIGEST_PROMPT = """You write a short evening-after recap for one person's private Telegram \
channel about their check-ins on {day}. Below (JSON) is every notable check-in of that day, each \
with "events" (new_venue = a place never visited before; comeback = back after a year or more; \
milestone = 10th/25th visit; new_city / new_country / comeback_long / milestone_big / total = \
bigger news) and facts computed from all earlier history, plus "day" totals.

Write {words} words in English, second person, warm and a little wry, as one flowing paragraph \
that picks the two or three best moments and ties the day together. A list of every event is \
appended after your text, so don't enumerate them all. Never invent anything. Plain text only, \
no markdown, at most 1 emoji.

Facts:
{facts}"""


def _llm(prompt: str, session=requests, run=subprocess.run) -> tuple[str | None, str]:
    """(text, source) via the API, else the Claude Code CLI; (None, "template") on any failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    model = os.environ.get("ANTHROPIC_MODEL", "").strip()
    if api_key:
        try:
            resp = session.post("https://api.anthropic.com/v1/messages", headers={
                "x-api-key": api_key, "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }, json={"model": model or DEFAULT_MODEL, "max_tokens": 600,
                     "messages": [{"role": "user", "content": prompt}]}, timeout=90)
            resp.raise_for_status()
            text = "".join(b.get("text", "") for b in resp.json().get("content", [])
                           if b.get("type") == "text").strip()
            if text:
                return text, "claude-api"
        except (requests.RequestException, ValueError) as exc:
            log.warning("Claude API failed, using template: %s", exc)
        return None, "template"
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        exe = shutil.which("claude")
        if not exe:
            log.warning("claude CLI not found, using template")
            return None, "template"
        cmd = [exe, "-p", "--output-format", "text"] + (["--model", model] if model else [])
        try:
            res = run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=180)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("claude CLI failed, using template: %s", exc)
            return None, "template"
        if res.returncode == 0 and (res.stdout or "").strip():
            return res.stdout.strip(), "claude-cli"
        log.warning("claude CLI exited %s, using template", res.returncode)
    return None, "template"


def _icon(icons: dict[str, str], cat: str) -> str:
    return icons.get(cat, DEFAULT_EMOJI)


def instant_message(f: dict, kinds: list[str], icons: dict[str, str], use_llm: bool = True) -> str:
    words = "30-60"
    text = None
    if use_llm:
        text, _ = _llm(INSTANT_PROMPT.format(words=words, facts=json.dumps(
            {**f, "events": kinds}, ensure_ascii=False, indent=1)))
    head = (f"{_icon(icons, f['category'])} <b>{_esc(f['venue'])}</b>"
            + (f" · {_esc(f['city'])}" if f["city"] else ""))
    return f"{head}\n{_esc(text or template_instant(f, kinds))}"


def digest_message(day: date, items: list[tuple[dict, list[str], bool]], totals: dict,
                   icons: dict[str, str], use_llm: bool = True) -> str:
    items = sorted(items, key=lambda it: digest_rank(it[0], it[1]))
    text = None
    if use_llm:
        facts = {"day": totals, "notable": [{**f, "events": k} for f, k, _ in items[:25]]}
        text, _ = _llm(DIGEST_PROMPT.format(day=day.isoformat(), words="40-90",
                                            facts=json.dumps(facts, ensure_ascii=False, indent=1)))
    lines = [f"🗓 <b>{day:%A, %d %B}</b> · {totals['checkins']} check-ins, "
             f"{totals['places']} places, {totals['new_places']} new"]
    if text:
        lines += ["", _esc(text)]
    lines.append("")
    for f, kinds, told in items[:DIGEST_LINES]:
        mark = " ✓" if told else ""
        lines.append(f"{_icon(icons, f['category'])} {_esc(f['venue'])}"
                     f" — {_esc(headline(f, kinds))}{mark}")
    rest = Counter("new places" if k[0] == "new_venue" else "comebacks" if k[0] == "comeback"
                   else "more" for _, k, _ in items[DIGEST_LINES:])
    if rest:
        lines.append("+ " + ", ".join(f"{n} {what}" for what, n in rest.most_common()))
    msg = "\n".join(lines)
    return msg if len(msg) <= 4000 else msg[:3990].rstrip() + "\n…"


# ── State ───────────────────────────────────────────────────────────────────

def load_state(path: Path | None) -> dict | None:
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("unreadable state %s — treating as missing", path)
        return None


def save_state(path: Path, state: dict) -> None:
    state["instant"] = state.get("instant", [])[-KEEP_IDS:]
    state["digests"] = sorted(set(state.get("digests", [])))[-KEEP_DIGESTS:]
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ── Modes ───────────────────────────────────────────────────────────────────

def plan_instant(h: History, state: dict, now_ts: float) -> tuple[list[tuple[int, dict, list[str]]], int]:
    """([(row index, facts, kinds)] to post, new watermark)."""
    last_ts = int(state.get("last_ts") or 0)
    todo: list[tuple[int, dict, list[str]]] = []
    watermark = last_ts
    for i, r in enumerate(h.rows):
        ts = int(r.get("date") or 0)
        if ts <= last_ts or (r.get("source_app") or "") == REFURB:
            continue
        watermark = max(watermark, ts)
        if not h.is_real(i) or not 0 <= now_ts - ts <= MAX_AGE_H * 3600:
            continue
        f = facts_for(h, i)
        kinds = events(f)
        if is_instant(kinds):
            todo.append((i, f, kinds))
    return todo[:MAX_INSTANT], watermark


def plan_digest(h: History, day: date) -> tuple[list[tuple[int, dict, list[str]]], dict]:
    idx = [i for i in range(len(h.rows)) if h.local[i].date() == day
           and (h.rows[i].get("source_app") or "") != REFURB]
    todo, places, new = [], set(), 0
    for i in idx:
        places.add(h.rows[i].get("venue_id") or place_name(h.rows[i]))
        if not h.is_real(i):
            continue
        f = facts_for(h, i)
        kinds = events(f)
        new += f["visit_no"] == 1
        if kinds:
            todo.append((i, f, kinds))
    return todo, {"checkins": len(idx), "places": len(places), "new_places": new}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=("instant", "digest"), required=True)
    ap.add_argument("--csv", required=True, help="checkins.csv (backfill.csv next to it is merged)")
    ap.add_argument("--config-dir", default=str(root / "config"))
    ap.add_argument("--state", default="", help="checkin_echo_state.json")
    ap.add_argument("--date", default="", help="digest: YYYY-MM-DD (default: yesterday in --tz)")
    ap.add_argument("--tz", default="Europe/Chisinau")
    ap.add_argument("--plan", action="store_true", help="print EVENTS=<n> only, send nothing")
    ap.add_argument("--dry-run", action="store_true", help="print the messages, send nothing")
    ap.add_argument("--no-llm", action="store_true", help="template text only")
    args = ap.parse_args()

    state_path = Path(args.state) if args.state else None
    state = load_state(state_path)
    h = History(load_rows(Path(args.csv), Path(args.config_dir)))
    icons = load_icons(Path(args.config_dir))

    if args.mode == "instant":
        if state is None:
            last = max((int(r.get("date") or 0) for r in h.rows
                        if (r.get("source_app") or "") != REFURB), default=0)
            print(f"No state yet — watermark set to {last}, nothing posted.")
            print("EVENTS=0")
            if state_path and not args.plan and not args.dry_run:
                save_state(state_path, {"last_ts": last, "instant": [], "digests": []})
            print("SENT=false")
            return 0
        todo, watermark = plan_instant(h, state, time.time())
        print(f"EVENTS={len(todo)}")
        if args.plan:
            return 0
        msgs = [(i, instant_message(f, k, icons, use_llm=not args.no_llm)) for i, f, k in todo]
    else:
        day = (date.fromisoformat(args.date) if args.date
               else datetime.now(ZoneInfo(args.tz)).date() - timedelta(days=1))
        state = state or {"last_ts": 0, "instant": [], "digests": []}
        if day.isoformat() in state.get("digests", []):
            print(f"Digest for {day} already sent.")
            print("EVENTS=0")
            return 0
        todo, totals = plan_digest(h, day)
        print(f"EVENTS={1 if todo else 0}")
        if args.plan:
            return 0
        told = set(state.get("instant", []))
        items = [(f, k, h.rows[i].get("checkin_id") in told) for i, f, k in todo]
        msgs = [(-1, digest_message(day, items, totals, icons, use_llm=not args.no_llm))] if todo else []

    if args.dry_run:
        for _, m in msgs:
            print("────")
            print(m)
        print("SENT=false")
        return 0
    if not msgs:
        if args.mode == "instant" and state_path:
            state["last_ts"] = watermark
            save_state(state_path, state)
        print("SENT=false")
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        return 1
    for i, text in msgs:
        if not tg_send(token, chat, [text], []):
            if args.mode == "instant" and state_path:
                # Everything before the failed check-in went out; retry it next run.
                state["last_ts"] = int(h.rows[i].get("date") or 0) - 1
                save_state(state_path, state)
            print("SENT=false")
            return 1
        if args.mode == "instant":
            state["instant"] = state.get("instant", []) + [h.rows[i].get("checkin_id", "")]
    if state_path:
        if args.mode == "instant":
            state["last_ts"] = watermark
        else:
            state["digests"] = state.get("digests", []) + [day.isoformat()]
        save_state(state_path, state)
    print("SENT=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
