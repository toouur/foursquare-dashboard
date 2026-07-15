# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""import_liveinternet.py — Crawl the LiveInternet diary and turn EVERY post
into a reviewable, location-*predicted* ``backfill_liveinternet.yaml`` entry for
the pre-2012 travel backfill.

The Foursquare/Swarm data starts in 2012; the LiveInternet blog (2008-2013) is
the richest surviving record of the earlier years. There is no usable export
(the RSS is empty even when logged in), but the diary is paginated and public::

    https://www.liveinternet.ru/users/<user>/page<N>.html   # 200 even logged-out

Pages are windows-1251, chronological (page 1 = oldest), and clamp-repeat past
the last real page — so the crawler walks pages from 1 and stops as soon as a
page repeats the previous page's post-id set (or yields no posts). Each post
lives in a ``<div class="CONBL blo-fastcom_base" id="post<ID>">`` container with
a title (``a.TTL``), a Russian-genitive date (``span.GL_TXTSM.GL_MAR5B``), prose
body (``div.MESS``), category rubrics (``Рубрики:``), tags (``Метки:``) and an
optional now-playing line (``В колонках играет``).

**Every post is emitted** (not just obviously-travel ones): the diary rarely
names a city outright, so each entry carries a *predicted* ``city``/``country``
plus a ``confidence`` (high = an explicit place name matched, low = fell back to
the home base) and a ``cue`` string explaining the guess. Home base defaults to
Chișinău, Moldova — the working assumption being that a post with no travel
signal was written from home (a "poker" night is a poker room *in Chișinău*).
The reviewer then confirms/corrects/prunes.

Optional authentication mirrors the FR24 precedent: set ``LIRU_LOGIN`` in the
environment as ``email:password`` and the crawler attempts a session login for
any friends-only posts. The credential is read from the environment ONLY — never
written to a file, echoed, or logged. Anonymous crawling already returns the
public diary, so login is best-effort; a failed login logs a warning and
continues anonymously.

Like ``import_polarsteps.py`` this writes a **separate review file** (default
``backfill_liveinternet.yaml``), NEVER the live ``backfill.yaml`` — nothing
reaches the dashboard until the user reviews, prunes, and merges. lat/lng are
left blank so ``build_backfill.py`` resolves coords from the predicted city.

Usage::

    # anonymous public crawl (default):
    python scripts/import_liveinternet.py --user toouur \
      --out backfill_liveinternet.yaml
    # with login for friends-only posts (credential from env, never a flag):
    export LIRU_LOGIN='email@example.com:password'
    python scripts/import_liveinternet.py --user toouur
    # offline: parse pages already saved by a prior --cache-dir run:
    python scripts/import_liveinternet.py --from-cache scratchpad/li
"""

from __future__ import annotations

import argparse
import html as _html
import json
import logging
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("import_liveinternet")

REFURB_SOURCE = "liveinternet"
BASE = "https://www.liveinternet.ru"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Russian genitive month name (stem) → 2-digit number. Stems avoid case endings.
_RU_MONTHS = [
    ("январ", "01"), ("феврал", "02"), ("март", "03"), ("апрел", "04"),
    ("мая", "05"), ("ма", "05"), ("июн", "06"), ("июл", "07"),
    ("август", "08"), ("сентябр", "09"), ("октябр", "10"), ("ноябр", "11"),
    ("декабр", "12"),
]

# Container start; each real diary post opens with exactly this div.
_CONTAINER_RE = re.compile(r'<div class="CONBL blo-fastcom_base" id="post(\d+)"')
_TITLE_RE = re.compile(r'class="TTL"[^>]*>\s*<b>(.*?)</b>', re.DOTALL)
# Date lives in a header span. Its second class varies by post type
# (GL_MAR5B on text posts, GL_MAR10B on photo/audio posts), so match any
# GL_TXTSM span and let the RU-date regex pick the one that carries the date.
_DATESPAN_RE = re.compile(
    r'<span class="GL_TXTSM[^"]*">(.*?)</span>', re.DOTALL)
_DATE_RE = re.compile(
    r'(\d{1,2})\s+([А-Яа-яЁё]+)\s+(\d{4})\s*г', re.IGNORECASE)
# Body: MESS block up to its closing <div class="B"></div> sentinel.
_BODY_RE = re.compile(
    r'<div class="[^"]*\bMESS\b[^"]*">(.*?)<div class="B">', re.DOTALL)
_MUSIC_RE = re.compile(
    r'В\s*колонках\s*играет\s*-\s*(.*?)</(?:nobr|em|div)>', re.DOTALL | re.IGNORECASE)
_RUBRICS_BLOCK_RE = re.compile(
    r'<b>Рубрики:.*?</b>(.*?)</table>', re.DOTALL)
_TAGS_BLOCK_RE = re.compile(r'<b>Метки:</b>(.*)', re.DOTALL)
_ANCHOR_TEXT_RE = re.compile(r'<a\b[^>]*>(.*?)</a>', re.DOTALL)
_TAG_ANCHOR_RE = re.compile(r'<a\b[^>]*\brel="tag"[^>]*>(.*?)</a>', re.DOTALL)
_TAG_MARK = "<b>Метки:</b>"


def _norm(s: str) -> str:
    """NFC-normalize + strip, mirroring build_backfill / transform."""
    return unicodedata.normalize("NFC", (s or "").strip())


def _strip_tags(fragment: str) -> str:
    """HTML fragment → plain text: <br> to newline, drop tags, unescape,
    collapse runs of blank space while keeping line breaks."""
    if not fragment:
        return ""
    txt = re.sub(r'(?i)<br\s*/?>', "\n", fragment)
    txt = re.sub(r'<[^>]+>', " ", txt)
    txt = _html.unescape(txt)
    lines = [re.sub(r'[ \t ]+', " ", ln).strip() for ln in txt.split("\n")]
    return _norm("\n".join(ln for ln in lines if ln))


def parse_ru_date(span_text: str) -> str | None:
    """Extract ``YYYY-MM-DD`` from a header span like
    ``Суббота, 25 Октября 2008 г. 23:09``. Returns None if unparseable."""
    m = _DATE_RE.search(span_text or "")
    if not m:
        return None
    day, month_word, year = m.group(1), m.group(2).lower(), m.group(3)
    mm = None
    for stem, num in _RU_MONTHS:
        if month_word.startswith(stem):
            mm = num
            break
    if mm is None:
        return None
    return f"{year}-{mm}-{int(day):02d}"


def parse_containers(html: str) -> list[dict[str, Any]]:
    """Parse a decoded page into a list of post dicts.

    Each dict: ``id, date, title, body, music, rubrics(list), tags(list),
    url``. Posts with no parseable date are still returned (date=None) so the
    caller can decide — but they are dropped from entries downstream.
    """
    posts: list[dict[str, Any]] = []
    starts = [m.start() for m in _CONTAINER_RE.finditer(html)]
    ids = [m.group(1) for m in _CONTAINER_RE.finditer(html)]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(html)
        chunk = html[start:end]
        pid = ids[i]

        title = ""
        mt = _TITLE_RE.search(chunk)
        if mt:
            title = _strip_tags(mt.group(1))

        date = None
        for md in _DATESPAN_RE.finditer(chunk):
            date = parse_ru_date(md.group(1))
            if date:
                break

        body = ""
        mb = _BODY_RE.search(chunk)
        if mb:
            raw = mb.group(1)
            raw = _MUSIC_RE.sub(" ", raw)  # keep music out of the prose
            body = _strip_tags(raw)

        music = ""
        mm = _MUSIC_RE.search(chunk)
        if mm:
            music = _strip_tags(mm.group(1))

        rubrics: list[str] = []
        mr = _RUBRICS_BLOCK_RE.search(chunk)
        if mr:
            rubrics = [_strip_tags(a) for a in _ANCHOR_TEXT_RE.findall(mr.group(1))]
            rubrics = [r for r in rubrics if r]

        tags: list[str] = []
        pos = chunk.find(_TAG_MARK)
        if pos != -1:
            tag_area = chunk[pos:]
            tags = [_strip_tags(a) for a in _TAG_ANCHOR_RE.findall(tag_area)]
            tags = [t for t in tags if t]

        posts.append({
            "id": pid,
            "date": date,
            "title": title,
            "body": body,
            "music": music,
            "rubrics": rubrics,
            "tags": tags,
            "url": f"{BASE}/users/{{user}}/post{pid}/",
        })
    return posts


# ---------------------------------------------------------------------------
# Location prediction
# ---------------------------------------------------------------------------

# Ordered, most-specific first: (compiled cue, city, country, human cue label).
# Cues are matched case-insensitively against title + body + tags + music. A
# match yields confidence "high" (an explicit place was named). Extend/override
# via a JSON file passed to --gazetteer (list of [pattern, city, country]).
_DEFAULT_GAZETTEER: list[tuple[str, str, str]] = [
    # --- Moldova / Transnistria (home region) ---
    (r"тирасспол|тирасполь|tiraspol", "Tiraspol", "Moldova"),
    (r"бендер\w*|бендеры|bender|tighina", "Bender", "Moldova"),
    (r"кишинёв\w*|кишинев\w*|кишинёва|chi[sș]in[aă]u|chisinau", "Chișinău", "Moldova"),
    (r"оргеев|orhei|орхей", "Orhei", "Moldova"),
    (r"бельцы|b[aă]l[tț]i|balti", "Bălți", "Moldova"),
    (r"комрат|comrat", "Comrat", "Moldova"),
    (r"сорок[аи]|soroca", "Soroca", "Moldova"),
    # --- Ukraine ---
    (r"одесс\w*|odesa|odessa", "Odesa", "Ukraine"),
    (r"ки[её]в\w*|kyiv|kiev", "Kyiv", "Ukraine"),
    (r"львов\w*|lviv|l[vw]ov", "Lviv", "Ukraine"),
    (r"харьков\w*|kharkiv|kharkov", "Kharkiv", "Ukraine"),
    (r"винниц\w*|vinnyts|vinnitsa", "Vinnytsia", "Ukraine"),
    # --- Belarus ---
    (r"минск\w*|minsk", "Minsk", "Belarus"),
    (r"брест\w*|brest", "Brest", "Belarus"),
    (r"гомел\w*|homyel|gomel", "Homyel", "Belarus"),
    # --- Russia ---
    (r"москв\w*|moscow", "Moscow", "Russia"),
    (r"санкт-петербург|петербург\w*|питер\b|saint\s*petersburg|st\.?\s*petersburg",
     "Saint Petersburg", "Russia"),
    (r"сочи\b|sochi", "Sochi", "Russia"),
    # --- Romania & wider Europe (interrail / trips) ---
    (r"бухарест\w*|bucharest|bucure[sș]ti", "Bucharest", "Romania"),
    (r"ясс[ыи]|ia[sș]i|iasi", "Iași", "Romania"),
    (r"клуж|cluj", "Cluj-Napoca", "Romania"),
    (r"прага\b|prague|praha", "Prague", "Czechia"),
    (r"варшав\w*|warsaw|warszawa", "Warsaw", "Poland"),
    (r"краков\w*|krak[oó]w|krakow|cracow", "Kraków", "Poland"),
    (r"берлин\w*|berlin", "Berlin", "Germany"),
    (r"вен[аеы]\b|vienna|wien", "Vienna", "Austria"),
    (r"будапешт\w*|budapest", "Budapest", "Hungary"),
    (r"стамбул\w*|istanbul", "Istanbul", "Turkey"),
    (r"париж\w*|paris\b", "Paris", "France"),
    (r"амстердам\w*|amsterdam", "Amsterdam", "Netherlands"),
    (r"барселон\w*|barcelona", "Barcelona", "Spain"),
    (r"рим\b|rome\b|roma\b", "Rome", "Italy"),
]

# Weaker, contextual cues → home region but flagged medium (e.g. Moldovan bands,
# "in my city"). These do NOT move the location off home; they raise confidence
# that home is right and record why. Matched after the explicit gazetteer.
_HOME_CONTEXT_CUES: list[str] = [
    r"в\s*мо[её]м\s*город",     # "in my city"
    r"мой\s*город",
    r"alternosfera", r"zdob\s*[sșş]i\s*zdub", r"g[aâ]ndul\s*m[aâ][tț]ei",
]


def compile_gazetteer(extra: list[tuple[str, str, str]] | None
                      ) -> list[tuple[re.Pattern[str], str, str]]:
    rows = list(_DEFAULT_GAZETTEER)
    if extra:
        rows = list(extra) + rows  # user rows take precedence
    return [(re.compile(pat, re.IGNORECASE), city, country) for pat, city, country in rows]


def infer_location(
    post: dict[str, Any],
    gazetteer: list[tuple[re.Pattern[str], str, str]],
    home_city: str,
    home_country: str,
    home_context: list[re.Pattern[str]],
) -> tuple[str, str, str, str]:
    """Predict ``(city, country, confidence, cue)`` for a post.

    Explicit gazetteer place-name match → high. No place but a home-context cue
    (own-city phrase, local band) → home + medium. Otherwise → home + low.
    """
    hay = " \n ".join([
        post.get("title") or "",
        post.get("body") or "",
        " ".join(post.get("tags") or []),
        post.get("music") or "",
    ])
    for rx, city, country in gazetteer:
        m = rx.search(hay)
        if m:
            return city, country, "high", f"matched '{m.group(0).strip()}'"
    for rx in home_context:
        m = rx.search(hay)
        if m:
            return home_city, home_country, "medium", f"home cue '{m.group(0).strip()}'"
    return home_city, home_country, "low", "no place cue — home base assumed"


def load_gazetteer_file(path: str | Path | None) -> list[tuple[str, str, str]] | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"--gazetteer file not found: {p}")
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    rows: list[tuple[str, str, str]] = []
    for row in data:
        if isinstance(row, (list, tuple)) and len(row) == 3:
            rows.append((str(row[0]), str(row[1]), str(row[2])))
    return rows


# ---------------------------------------------------------------------------
# Crawl
# ---------------------------------------------------------------------------

def _decode(raw: bytes) -> str:
    return raw.decode("windows-1251", "replace")


def login_session(user: str) -> Any:
    """Build a requests.Session (UA set). If LIRU_LOGIN=email:password is in the
    environment, attempt a best-effort LiveInternet login for friends-only
    posts. The credential is read from the env ONLY — never written or logged.
    Login failure is non-fatal: warn and continue anonymously."""
    import requests  # local import so --from-cache works without the dep

    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})

    cred = os.environ.get("LIRU_LOGIN", "")
    if not cred:
        log.info("no LIRU_LOGIN in env — crawling the public diary anonymously")
        return sess
    if ":" not in cred:
        log.warning("LIRU_LOGIN must be 'email:password' — ignoring, anonymous")
        return sess
    email, password = cred.split(":", 1)
    try:
        # LiveInternet's classic login endpoint. Best-effort; the public diary
        # is reachable without it, so we never hard-fail here.
        r = sess.post(f"{BASE}/journal.php", data={
            "action": "login", "member": email, "password": password,
            "returnurl": f"{BASE}/users/{user}/",
        }, timeout=30, allow_redirects=True)
        if sess.cookies and any("bbuserid" in c.name or "pass" in c.name.lower()
                                for c in sess.cookies):
            log.info("LiveInternet login: session established")
        else:
            log.warning("LiveInternet login: no auth cookie set (HTTP %s) — "
                        "continuing anonymously", r.status_code)
    except Exception as e:  # noqa: BLE001 - login is optional, never fatal
        log.warning("LiveInternet login failed (%s) — continuing anonymously",
                    type(e).__name__)
    return sess


def crawl(user: str, max_pages: int, sleep: float,
          cache_dir: str | Path | None) -> list[str]:
    """Fetch diary pages 1..N (windows-1251-decoded) until the post-id set
    repeats the previous page (clamp) or a page has no posts. Returns the list
    of decoded page HTML strings. Optionally saves each raw page to cache_dir."""
    sess = login_session(user)
    cache = Path(cache_dir) if cache_dir else None
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
    pages: list[str] = []
    prev_ids: list[str] | None = None
    for n in range(1, max_pages + 1):
        url = f"{BASE}/users/{user}/page{n}.html"
        try:
            r = sess.get(url, timeout=30)
        except Exception as e:  # noqa: BLE001
            log.warning("page%d fetch failed (%s) — stopping crawl", n, type(e).__name__)
            break
        if r.status_code != 200:
            log.info("page%d → HTTP %s — end of diary", n, r.status_code)
            break
        html = _decode(r.content)
        ids = _CONTAINER_RE.findall(html)
        if not ids:
            log.info("page%d has no posts — end of diary", n)
            break
        if ids == prev_ids:
            log.info("page%d repeats page%d (clamp) — end of diary", n, n - 1)
            break
        if cache:
            (cache / f"page{n}.html").write_bytes(r.content)
        pages.append(html)
        prev_ids = ids
        log.info("page%d: %d posts", n, len(ids))
        if sleep:
            time.sleep(sleep)
    return pages


def load_cache(cache_dir: str | Path) -> list[str]:
    """Load previously-saved raw pages (page1.html, page2.html, …) in order."""
    d = Path(cache_dir)
    files = sorted(d.glob("page*.html"),
                   key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    return [_decode(p.read_bytes()) for p in files]


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def build_entries(
    pages: list[str],
    user: str,
    gazetteer: list[tuple[re.Pattern[str], str, str]],
    home_city: str,
    home_country: str,
    before_year: int | None,
    excerpt_len: int,
) -> tuple[list[dict[str, Any]], int, int]:
    """Parse all pages → one predicted entry per post (deduped by post id).

    Returns ``(entries, total_posts, skipped_no_date)``. Posts at/after
    ``before_year`` are dropped (Foursquare era). Entries sorted by date."""
    home_ctx = [re.compile(p, re.IGNORECASE) for p in _HOME_CONTEXT_CUES]
    total = 0
    skipped = 0
    by_id: dict[str, dict[str, Any]] = {}
    for html in pages:
        for post in parse_containers(html):
            total += 1
            pid = post["id"]
            if pid in by_id:
                continue
            date = post["date"]
            if not date:
                skipped += 1
                continue
            year = int(date[:4])
            if before_year is not None and year >= before_year:
                continue
            city, country, conf, cue = infer_location(
                post, gazetteer, home_city, home_country, home_ctx)
            body = post.get("body") or ""
            excerpt = body[:excerpt_len].rstrip()
            if len(body) > excerpt_len:
                excerpt += "…"
            entry: dict[str, Any] = {
                "date": date,
                "city": city,
                "country": country,
                "confidence": conf,
                "cue": cue,
                "venue": "",
                "note": post.get("title") or "",
                "source": REFURB_SOURCE,
                "li_id": pid,
                "url": post["url"].replace("{user}", user),
            }
            if post.get("music"):
                entry["music"] = post["music"]
            if post.get("tags"):
                entry["tags"] = post["tags"]
            if excerpt:
                entry["body"] = excerpt
            by_id[pid] = entry
    entries = sorted(by_id.values(), key=lambda e: (e["date"], e["li_id"]))
    return entries, total, skipped


_KEY_ORDER = ["date", "city", "country", "confidence", "cue", "venue", "note",
              "source", "li_id", "url", "music", "tags", "body"]


def _ordered(entry: dict) -> dict:
    ordered = {k: entry[k] for k in _KEY_ORDER if k in entry}
    for k, v in entry.items():
        if k not in ordered:
            ordered[k] = v
    return ordered


def write_yaml(entries: list[dict], out_path: str | Path, append: bool) -> int:
    """Write entries to ``out_path`` as a YAML list.

    With ``append``, merge into an existing list, deduping by ``li_id`` (so a
    re-run doesn't double an already-reviewed post). Returns the NEW count."""
    out = Path(out_path)
    existing: list[dict] = []
    if append and out.exists():
        with open(out, encoding="utf-8") as fh:
            existing = yaml.safe_load(fh) or []
        if not isinstance(existing, list):
            raise SystemExit(f"{out} is not a YAML list — cannot --append")

    seen = {e.get("li_id") for e in existing if isinstance(e, dict)}
    fresh = [e for e in entries if e.get("li_id") not in seen]
    merged = existing + [_ordered(e) for e in fresh]

    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Reconstructed pre-2012 travel — LiveInternet diary import "
                 "(REVIEW BEFORE USE).\n")
        fh.write("# EVERY post is a PREDICTION: 'confidence' high=explicit place "
                 "named, medium=home\n")
        fh.write("# cue, low=home base assumed. 'cue' explains the guess. "
                 "Correct/prune, then\n")
        fh.write("# merge the good entries into the data repo's backfill.yaml. "
                 "'li_id'/'url'/'cue'/\n")
        fh.write("# 'music'/'tags'/'body'/'confidence' are provenance only; "
                 "build_backfill.py ignores them.\n")
        yaml.safe_dump(merged, fh, allow_unicode=True, sort_keys=False,
                       default_flow_style=False, width=100)
    return len(fresh)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="LiveInternet diary → reviewable, location-predicted "
                    "backfill.yaml entries")
    ap.add_argument("--user", default="toouur", help="LiveInternet username")
    ap.add_argument("--out", default="backfill_liveinternet.yaml",
                    help="review file to write (default: backfill_liveinternet.yaml)")
    ap.add_argument("--max-pages", type=int, default=60,
                    help="crawl safety cap (default 60; real diary ~18 pages)")
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="seconds between page fetches (politeness; default 1.0)")
    ap.add_argument("--cache-dir",
                    help="also save each fetched raw page here (for re-parsing offline)")
    ap.add_argument("--from-cache",
                    help="skip the network; parse pages already saved in this dir")
    ap.add_argument("--gazetteer",
                    help="JSON file: list of [regex, city, country] cues to prepend")
    ap.add_argument("--home-city", default="Chișinău", help="fallback home city")
    ap.add_argument("--home-country", default="Moldova", help="fallback home country")
    ap.add_argument("--before", type=int, default=2012,
                    help="keep posts strictly before this year (default 2012)")
    ap.add_argument("--all", action="store_true",
                    help="ignore --before and import every post (2012+ posts "
                         "overlap real Foursquare check-ins)")
    ap.add_argument("--excerpt-len", type=int, default=240,
                    help="max chars of body kept as a review excerpt (default 240)")
    ap.add_argument("--append", action="store_true",
                    help="merge into an existing --out file (dedup by li_id)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.append and Path(args.out).exists():
        log.error("%s already exists — pass --append to merge, or choose a new "
                  "--out (refusing to overwrite)", args.out)
        return 1

    if args.from_cache:
        pages = load_cache(args.from_cache)
        log.info("loaded %d cached page(s) from %s", len(pages), args.from_cache)
    else:
        pages = crawl(args.user, args.max_pages, args.sleep, args.cache_dir)
    if not pages:
        log.error("no diary pages parsed — nothing to write")
        return 1

    gazetteer = compile_gazetteer(load_gazetteer_file(args.gazetteer))
    before = None if args.all else args.before
    entries, total, skipped = build_entries(
        pages, args.user, gazetteer, args.home_city, args.home_country,
        before, args.excerpt_len)

    n_new = write_yaml(entries, args.out, args.append)

    window = "all years" if before is None else f"before {before}"
    log.info("Parsed %d post(s); kept %d entr(y/ies) [%s]%s.",
             total, len(entries), window,
             f", {skipped} had no parseable date" if skipped else "")
    log.info("Wrote %d new entr(y/ies) → %s", n_new, args.out)
    # Confidence + place summary so the review has a quick sanity check.
    by_conf: dict[str, int] = {}
    by_place: dict[str, int] = {}
    for e in entries:
        by_conf[e["confidence"]] = by_conf.get(e["confidence"], 0) + 1
        key = f"{e['city']}, {e['country']}"
        by_place[key] = by_place.get(key, 0) + 1
    log.info("Confidence: " + ", ".join(f"{k}={v}" for k, v in
             sorted(by_conf.items(), key=lambda kv: (-kv[1], kv[0]))))
    log.info("Predicted places (high/medium float to review first):")
    for place, n in sorted(by_place.items(), key=lambda kv: (-kv[1], kv[0])):
        log.info("  %3d  %s", n, place)
    log.info("REVIEW this file — EVERY row is a guess. Confirm/correct before "
             "merging into backfill.yaml. Nothing reaches the dashboard until you do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
