#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_year_pages.py — Per-year album pages with scroll-driven storytelling.

Each year produces year-YYYY.html with these sections, each fading in
as the user scrolls and each carrying its own atmospheric background
photo from that year (when photos are available):

  1. Hero          — giant year number, Ken-Burns photo cycle,
                     vivid 2-3 sentence narrative
  2. By the Numbers — animated KPI counters
  3. Month by Month — vertical timeline, one row per month with the
                     month's busiest venue/city + a thumbnail and the
                     month's peak shout
  4. Where you spent {year} — top venues / cities / categories
  5. New on the map — flag tiles for first-time countries
  6. Journeys — trip cards linking to trip-N.html
  7. In the air — flight cards with airline logos
  8. Memory — full photo grid + lightbox
  9. In your own voice — longest shouts
 10. Most-frequent companions

Backgrounds: every section after the hero gets a low-opacity year photo
fixed in the background; the photo cross-fades as the previous section
leaves the viewport (IntersectionObserver-driven).
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

CTRY_CODE: dict[str, str] = {}

# ── Activity buckets ────────────────────────────────────────────────────
# Two kinds:
#   • landmark = 1+ check-in is notable enough to call out by venue ("a
#     concert at X", "a poker night at Y")
#   • routine  = recurring background pattern; only the busiest one per
#     month is surfaced as the "rhythm" of the month.
# Match strings are lower-case substrings checked against the Foursquare
# category text — works across en/ru/local variants when Foursquare keeps
# the English category label.
ACTIVITY_BUCKETS: dict[str, dict] = {
    # Landmarks
    "concerts": {
        "match": ["music venue", "concert hall", "rock club", "amphitheater", "opera house", "jazz club"],
        "kind": "landmark",
        "phrases": ["a concert at", "live music at", "a gig at", "a show at"],
    },
    "theatre": {
        "match": ["theater", "theatre", "playhouse"],
        "kind": "landmark",
        "phrases": ["a play at", "a theatre night at", "stagelight at"],
    },
    "cinema": {
        "match": ["movie theater", "cinema"],
        "kind": "landmark",
        "phrases": ["a film at", "a cinema night at", "screening at"],
    },
    "museums": {
        "match": ["museum", "art gallery", "exhibition"],
        "kind": "landmark",
        "phrases": ["a museum afternoon at", "wandering through", "a gallery visit to"],
    },
    "monuments": {
        "match": ["monument", "historic", "castle", "palace"],
        "kind": "landmark",
        "phrases": ["walks past", "a slow visit to", "a stop at"],
    },
    "sports": {
        "match": ["stadium", "sports arena", "soccer field", "basketball court", "hockey arena", "athletic field"],
        "kind": "landmark",
        "phrases": ["a match at", "courtside at", "a game at"],
    },
    "poker": {
        "match": ["casino", "card room", "poker"],
        "kind": "landmark",
        "phrases": ["a poker night at", "tables at", "a late hand at"],
    },
    "nightclub": {
        "match": ["nightclub", "night club", "disco"],
        "kind": "landmark",
        "phrases": ["a night out at", "the dancefloor at", "the small hours at"],
    },
    "wellness": {
        "match": ["spa", "hammam", "sauna", "bathhouse"],
        "kind": "landmark",
        "phrases": ["spa hours at", "a steam at", "unwinding at"],
    },
    "outdoors": {
        "match": ["park", "garden", "beach", "trail", "scenic lookout", "mountain", "national park", "lake"],
        "kind": "landmark",
        "phrases": ["walks in", "long hours in", "outdoor stretches at"],
    },
    # Routines
    "coffee": {
        "match": ["coffee shop", "café", "cafe", "tea room", "bakery", "tea house"],
        "kind": "routine",
        "phrases": ["coffee runs and café mornings", "long afternoons over flat whites", "café-stop rhythm", "espresso punctuation"],
    },
    "dining": {
        "match": ["restaurant", "diner", "steakhouse", "bbq joint", "pizzeria", "bistro", "trattoria"],
        "kind": "routine",
        "phrases": ["restaurant dinners", "long table nights", "kitchen-to-kitchen rotation"],
    },
    "bars": {
        "match": ["bar", "pub", "brewery", "wine bar", "cocktail"],
        "kind": "routine",
        "phrases": ["evenings out", "bar lights and conversation", "after-work pints", "late nightcaps"],
    },
    "travel": {
        "match": ["airport", "plane", "hotel", "hostel", "train station", "rail station", "bus station"],
        "kind": "routine",
        "phrases": ["travel days and transit lounges", "on the move", "hotel-and-runway weeks"],
    },
    "work": {
        "match": ["office", "coworking", "business center"],
        "kind": "routine",
        "phrases": ["desk hours", "office days running into each other", "head-down work weeks"],
    },
    "home": {
        "match": ["home (private)", "residential"],
        "kind": "routine",
        "phrases": ["mostly at home", "anchored close to home", "a quiet rhythm at home"],
    },
    "shopping": {
        "match": ["shopping mall", "supermarket", "grocery", "market", "convenience store"],
        "kind": "routine",
        "phrases": ["weekend errands", "grocery loops", "shopping-mall rounds"],
    },
}

# Soft mood lexicon used to label the "feel" of a month based on shout
# wording (en/ru/be).  Imperfect but adds a little colour.
_POS_TOKENS = {
    "люблю", "любим", "обожаю", "круто", "класс", "классно", "прекрасно",
    "великолепно", "восторг", "amazing", "love", "loved", "great", "perfect",
    "best", "wonderful", "beautiful", "хорошо", "supercute", "лучший", "лучшая",
    "ВКУСНО", "вкусно", "🔥", "🥰", "❤", "👍",
}
_NEG_TOKENS = {
    "плохо", "ужас", "хрень", "не понравил", "не очень",
    "awful", "terrible", "worst", "hate", "bad", "👎",
}


def _load_flags(config_dir: Path) -> None:
    global CTRY_CODE
    if CTRY_CODE:
        return
    p = config_dir / "country_flags.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            CTRY_CODE = {k: v for k, v in data.items() if not k.startswith("_")}
        except Exception:
            CTRY_CODE = {}


def _flag(country: str) -> str:
    code = CTRY_CODE.get(country, "")
    return f'<span class="fi fi-{code.lower()}"></span>' if code else ""


def _esc(s) -> str:
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_BUCKET_RES: dict[str, list] = {}


def _bucket_for(category: str) -> str:
    """Return bucket key for a Foursquare category, or '' if no match.
    Word-boundary aware so 'park' doesn't catch 'parking' / 'parker'."""
    if not category:
        return ""
    import re as _re
    cl = category.lower()
    if not _BUCKET_RES:
        for key, cfg in ACTIVITY_BUCKETS.items():
            _BUCKET_RES[key] = [_re.compile(rf"\b{_re.escape(m)}\b") for m in cfg["match"]]
    for key, patterns in _BUCKET_RES.items():
        for pat in patterns:
            if pat.search(cl):
                return key
    return ""


def _mood_score(shouts: list[dict]) -> int:
    """Score the emotional tilt of a month from its shouts. Positive →
    upbeat; negative → grumbly; 0 → neutral / no shouts."""
    if not shouts:
        return 0
    s = 0
    for sh in shouts:
        t = (sh.get("text") or "").lower()
        for tok in _POS_TOKENS:
            if tok in t:
                s += 1
        for tok in _NEG_TOKENS:
            if tok in t:
                s -= 1
        # exclamation marks add a touch of intensity
        if "!" in t:
            s += min(1, t.count("!"))
    return s


def _mood_phrase(score: int, yr: int, mo: int) -> str:
    """Pick a short atmospheric phrase from the month's mood score."""
    if score >= 4:
        pool = ["a bright stretch", "an upbeat month", "the kind of weeks that hold up well"]
    elif score >= 2:
        pool = ["a buoyant month", "a warm note running through it", "good days adding up"]
    elif score <= -2:
        pool = ["a grumbly stretch", "a sharper month", "edges visible in the journal"]
    else:
        return ""
    return pool[(yr * 12 + mo) % len(pool)]


def _wedge_path(cx: float, cy: float, r_inner: float, r_outer: float,
                start_deg: float, end_deg: float) -> str:
    """SVG path for an annular wedge from start_deg → end_deg (degrees,
    0 = right, 90 = bottom; using SVG convention)."""
    a1, a2 = math.radians(start_deg), math.radians(end_deg)
    x1, y1 = cx + r_outer * math.cos(a1), cy + r_outer * math.sin(a1)
    x2, y2 = cx + r_outer * math.cos(a2), cy + r_outer * math.sin(a2)
    x3, y3 = cx + r_inner * math.cos(a2), cy + r_inner * math.sin(a2)
    x4, y4 = cx + r_inner * math.cos(a1), cy + r_inner * math.sin(a1)
    large = 1 if (end_deg - start_deg) > 180 else 0
    return (f"M{x1:.2f},{y1:.2f} A{r_outer},{r_outer} 0 {large},1 {x2:.2f},{y2:.2f} "
            f"L{x3:.2f},{y3:.2f} A{r_inner},{r_inner} 0 {large},0 {x4:.2f},{y4:.2f} Z")


def _render_year_clock(yr: int, mon_counter: Counter, months_short: list[str]) -> str:
    """Polar bar chart: 12 wedges of 30deg each, outer radius proportional
    to that month's check-in count.  Each wedge is clickable to scroll to
    that month's row."""
    cx = cy = 130
    r_inner = 56
    r_outer_max = 120
    max_v = max(mon_counter.values()) if mon_counter else 1
    if max_v == 0:
        max_v = 1
    total = sum(mon_counter.values())
    wedges: list[str] = []
    labels: list[str] = []
    for mo in range(12):
        v = mon_counter.get(mo + 1, 0)
        ratio = v / max_v if max_v else 0
        # min visible radius so empty months show as faint
        r_o = r_inner + (r_outer_max - r_inner) * (0.10 + 0.90 * ratio) if v else r_inner + 5
        start = mo * 30 - 90      # start at 12-o'clock, clockwise
        end = start + 30 - 1.2    # tiny gap between wedges
        path = _wedge_path(cx, cy, r_inner, r_o, start, end)
        opacity = 0.25 + 0.70 * ratio if v else 0.10
        # Gold gradient by intensity (lighter = quieter)
        fill = "url(#yc-grad)" if v else "rgba(255,255,255,.06)"
        wedges.append(
            f'<a href="#mo-{mo+1:02d}" aria-label="{months_short[mo]} {v} check-ins">'
            f'<path d="{path}" fill="{fill}" opacity="{opacity:.2f}" class="yc-wedge"/>'
            f'</a>'
        )
        # Outer month label
        ang = math.radians(start + 14)
        lx = cx + (r_outer_max + 16) * math.cos(ang)
        ly = cy + (r_outer_max + 16) * math.sin(ang)
        labels.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" class="yc-mlbl" text-anchor="middle" '
            f'dominant-baseline="middle">{months_short[mo]}</text>'
        )
    return (
        f'<svg viewBox="0 0 260 260" class="yearclock" role="img" aria-label="{yr} month activity ring">'
        f'<defs><linearGradient id="yc-grad" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="#f5d48a"/>'
        f'<stop offset="55%" stop-color="#e8b86d"/>'
        f'<stop offset="100%" stop-color="#b97c30"/>'
        f'</linearGradient></defs>'
        f'<circle cx="{cx}" cy="{cy}" r="{r_inner-3}" fill="none" stroke="rgba(232,184,109,.18)" stroke-width="1"/>'
        f'{"".join(wedges)}'
        f'{"".join(labels)}'
        f'<text x="{cx}" y="{cy-4}" text-anchor="middle" class="yc-total">{total:,}</text>'
        f'<text x="{cx}" y="{cy+14}" text-anchor="middle" class="yc-totlbl">CHECK-INS</text>'
        f'</svg>'
    )


def _compose_month_narrative(
    yr: int, mo: int,
    rows_m: list[dict], mon_venue_m: Counter, mon_city_m: Counter,
    mon_cat_m: Counter, shouts_m: list[dict],
) -> str:
    """Build a richer flowing narrative for a single month — picks up the
    busiest city, the dominant routine bucket (coffee / dining / travel /
    work / home), and any landmark events (concerts, museums, sports,
    poker, nightclubs, outdoor walks) by name.  Mood phrase from shouts
    closes the sentence when strong enough."""
    # Bucket the month's check-ins
    bucket_n: Counter = Counter()
    bucket_venue: dict[str, Counter] = defaultdict(Counter)
    # Track which city each venue lives in (most-common city for the
    # venue in this month) so we can annotate cross-city landmarks
    # honestly: "walks in Park X · Minsk" when the lead city differs.
    venue_city: dict[str, Counter] = defaultdict(Counter)
    for r in rows_m:
        b = _bucket_for(r.get("category") or "")
        v = (r.get("venue") or "").strip()
        c = (r.get("city") or "").strip()
        if v and c:
            venue_city[v][c] += 1
        if not b:
            continue
        bucket_n[b] += 1
        if v:
            bucket_venue[b][v] += 1

    routines = sorted(
        [(b, n) for b, n in bucket_n.items() if ACTIVITY_BUCKETS[b]["kind"] == "routine" and n > 0],
        key=lambda x: -x[1],
    )
    landmarks = sorted(
        [(b, n) for b, n in bucket_n.items() if ACTIVITY_BUCKETS[b]["kind"] == "landmark" and n > 0],
        key=lambda x: -x[1],
    )

    # Detect how concentrated the month is: if the top city has < 55% of
    # all check-ins, treat it as a multi-city month (otherwise the lead
    # "Minsk set the rhythm" hides week-long trips elsewhere).
    total_m = sum(mon_city_m.values()) or 1
    top_c = mon_city_m.most_common(1)[0] if mon_city_m else ("", 0)
    cities_significant = sum(1 for c, n in mon_city_m.items() if n / total_m >= 0.12)
    multi_city = bool(top_c[0]) and (top_c[1] / total_m) < 0.55
    second_c = mon_city_m.most_common(2)[1] if len(mon_city_m) >= 2 else ("", 0)

    salt = yr * 12 + mo

    bits: list[str] = []
    # Lead: shape depends on geographic spread.  Skip the travel routine
    # phrase when we're already saying "on the move" — otherwise the line
    # reads "on the move ... on the move ...".
    suppress_travel = False
    if multi_city and second_c[0]:
        if cities_significant >= 3:
            bits.append(
                f"Across <strong>{cities_significant}</strong> cities, "
                f"anchored in <strong>{_esc(top_c[0])}</strong>"
            )
        else:
            bits.append(
                f"Split between <strong>{_esc(top_c[0])}</strong> "
                f"and <strong>{_esc(second_c[0])}</strong>"
            )
        suppress_travel = True
        # Append routine flavour, but not the travel one
        if routines:
            for b, _ in routines:
                if suppress_travel and b == "travel":
                    continue
                ph = ACTIVITY_BUCKETS[b]["phrases"]
                bits.append(ph[salt % len(ph)])
                break
    elif top_c[0] and routines:
        b, _ = routines[0]
        ph = ACTIVITY_BUCKETS[b]["phrases"]
        bits.append(f"<strong>{_esc(top_c[0])}</strong> set the rhythm — {ph[salt % len(ph)]}")
    elif top_c[0]:
        bits.append(f"<strong>{_esc(top_c[0])}</strong> held most of it")
    elif routines:
        b, _ = routines[0]
        ph = ACTIVITY_BUCKETS[b]["phrases"]
        bits.append(ph[salt % len(ph)].capitalize())

    # Landmarks — pick top 2 by count, name venues.  Annotate the venue's
    # city when it differs from the month's lead city, so cross-city
    # months don't read as if everything happened in one place.
    lead_city = top_c[0] if top_c and not multi_city else ""
    landmark_bits: list[str] = []
    for b, n in landmarks[:2]:
        ph = ACTIVITY_BUCKETS[b]["phrases"]
        phrase = ph[(yr + mo + len(b)) % len(ph)]
        top_v = bucket_venue[b].most_common(1)
        if top_v and top_v[0][0]:
            venue = top_v[0][0]
            v_city = venue_city[venue].most_common(1)[0][0] if venue_city.get(venue) else ""
            label = f"<strong>{_esc(venue)}</strong>"
            # Only annotate if multi-city month and the venue is in a
            # different city than the lead — keeps single-city months clean.
            if v_city and lead_city and v_city != lead_city:
                label += f" <span style=\"color:var(--muted);font-style:normal;font-size:.85em;\">· {_esc(v_city)}</span>"
            elif v_city and multi_city:
                label += f" <span style=\"color:var(--muted);font-style:normal;font-size:.85em;\">· {_esc(v_city)}</span>"
            if n >= 2:
                landmark_bits.append(f"{phrase} {label} ({n}×)")
            else:
                landmark_bits.append(f"{phrase} {label}")
        elif n >= 2:
            landmark_bits.append(f"{n} {b}")
    if landmark_bits:
        bits.append("with " + " and ".join(landmark_bits[:2]))

    # Fallback if nothing else
    if not bits:
        top_v = mon_venue_m.most_common(1)[0] if mon_venue_m else ("", 0)
        if top_v[0]:
            bits.append(f"<strong>{_esc(top_v[0])}</strong> the recurring stop ({top_v[1]}×)")
        else:
            bits.append("A month in the rotation")

    # Mood from shouts
    mp = _mood_phrase(_mood_score(shouts_m), yr, mo)
    if mp:
        bits.append(mp)

    narr = ", ".join(bits) + "."
    return narr[0].upper() + narr[1:]


def _find_nearest_photo(yr: int, mo: int,
                        photos_by_yr_mo: dict[tuple[int, int], list[dict]],
                        months_full: list[str]) -> tuple[dict | None, str]:
    """Walk outward from `mo` looking for the nearest month with a photo
    in the same year. Returns (photo, label) — label is empty if photo
    is from the requested month, else 'from {month}'."""
    own = photos_by_yr_mo.get((yr, mo))
    if own:
        return own[0], ""
    for delta in range(1, 12):
        for d in (-delta, delta):
            cm = mo + d
            if cm < 1 or cm > 12:
                continue
            ph = photos_by_yr_mo.get((yr, cm))
            if ph:
                return ph[0], f"from {months_full[cm - 1]}"
    return None, ""


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="https://4sq.pages.dev/year-{year}.html">
<meta property="og:type" content="article">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;700;900&family=DM+Sans:wght@400;500;600;700&family=DM+Mono&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/flag-icon-css/6.6.6/css/flag-icons.min.css">
<style>
:root{{--bg:#0a0c12;--card:#12141c;--card2:#181a24;--border:#222738;--text:#e0e2ec;--muted:#7a85a8;--gold:#e8b86d;--teal:#4ec9b0;--rose:#e8778a;}}
body.light{{--bg:#e9e4dc;--card:#f0ece3;--card2:#e5e0d7;--border:#aaa49a;--gold:#7a3e00;--teal:#155c58;--muted:#54576a;--text:#15172a;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
html{{scroll-behavior:smooth;}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.55;overflow-x:hidden;}}
a{{color:var(--teal);}}

.crumb{{position:fixed;top:14px;left:18px;z-index:200;font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);background:rgba(18,21,31,.85);backdrop-filter:blur(8px);border:1px solid var(--border);padding:6px 12px;border-radius:6px;text-decoration:none;}}
.crumb:hover{{color:var(--gold);}}

/* ── Scroll progress bar ── */
.progbar{{position:fixed;top:0;left:0;height:3px;background:linear-gradient(90deg,#f5d48a,#e8b86d 50%,#b97c30);z-index:300;width:0%;transition:width .1s ease-out;box-shadow:0 0 12px rgba(232,184,109,.45);}}

/* ── Scrolling background photo (fixed, fades in per section) ── */
.scroll-bg{{position:fixed;inset:0;z-index:0;pointer-events:none;}}
.scroll-bg .sbg-img{{position:absolute;inset:0;background-size:cover;background-position:center;opacity:0;transition:opacity 1.4s ease;filter:brightness(.28) contrast(1.08) saturate(.85);transform:scale(1.05);}}
.scroll-bg .sbg-img.active{{opacity:1;animation:slowPan 24s ease-in-out infinite;}}
@keyframes slowPan{{0%{{transform:scale(1.05) translateY(0);}}50%{{transform:scale(1.12) translateY(-1.5%);}}100%{{transform:scale(1.05) translateY(0);}}}}
.scroll-bg::after{{content:'';position:absolute;inset:0;background:linear-gradient(180deg,rgba(10,12,18,0.55) 0%,rgba(10,12,18,0.92) 100%);}}

/* ── Hero ── */
.hero{{position:relative;height:100vh;min-height:580px;overflow:hidden;display:flex;align-items:center;justify-content:center;z-index:5;}}
.hero-bg{{position:absolute;inset:0;z-index:0;}}
.hero-bg .ph{{position:absolute;inset:0;background-size:cover;background-position:center;opacity:0;transition:opacity 1.6s ease;filter:brightness(.45) contrast(1.05);}}
.hero-bg .ph.active{{opacity:1;animation:kb 18s ease-in-out infinite;}}
@keyframes kb{{0%{{transform:scale(1.05);}}50%{{transform:scale(1.20);}}100%{{transform:scale(1.05);}}}}
.hero-overlay{{position:absolute;inset:0;background:radial-gradient(ellipse at center,rgba(10,12,18,0.10) 0%,rgba(10,12,18,0.85) 80%);z-index:1;}}
.hero-content{{position:relative;z-index:2;text-align:center;padding:24px;max-width:840px;}}
.hero-eyebrow{{font-family:'DM Mono',monospace;font-size:.65rem;text-transform:uppercase;letter-spacing:.32em;color:var(--gold);margin-bottom:18px;opacity:0;animation:fadeUp .9s .25s forwards;text-shadow:0 1px 6px rgba(0,0,0,.7);}}
.hero-yr{{font-family:'Playfair Display',serif;font-size:clamp(7rem,18vw,18rem);font-weight:900;line-height:0.92;letter-spacing:-0.035em;background:linear-gradient(150deg,#f5d48a 0%,#e8b86d 40%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;opacity:0;animation:fadeUp 1.1s .55s forwards;}}
.hero-vivid{{font-family:'Playfair Display',serif;font-size:clamp(1.05rem,2vw,1.5rem);font-weight:400;color:#f0f1f6;line-height:1.6;margin-top:24px;text-shadow:0 2px 18px rgba(0,0,0,0.6);max-width:760px;margin-left:auto;margin-right:auto;font-style:italic;opacity:0;animation:fadeUp 1.4s .95s forwards;}}
.hero-vivid strong{{color:var(--gold);font-style:normal;font-weight:500;}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(20px);}}to{{opacity:1;transform:translateY(0);}}}}
.hero-scroll{{position:absolute;bottom:28px;left:50%;transform:translateX(-50%);z-index:3;font-family:'DM Mono',monospace;font-size:.55rem;color:var(--muted);letter-spacing:.18em;text-transform:uppercase;animation:bob 2s ease-in-out infinite;opacity:0;animation:fadeUp 1.6s 1.4s forwards, bob 2s 2.4s infinite;}}
@keyframes bob{{0%,100%{{transform:translateX(-50%) translateY(0);}}50%{{transform:translateX(-50%) translateY(6px);}}}}

/* ── Sticky section header strip ── */
.sticky-strip{{position:sticky;top:0;z-index:60;background:linear-gradient(180deg,rgba(10,12,18,0.96) 0%,rgba(10,12,18,0.78) 100%);backdrop-filter:blur(10px);border-bottom:1px solid rgba(232,184,109,.15);padding:14px 28px;display:flex;align-items:baseline;justify-content:space-between;gap:14px;flex-wrap:wrap;}}
.sticky-strip .ssh-yr{{font-family:'Playfair Display',serif;font-size:1.45rem;font-weight:700;background:linear-gradient(150deg,#f5d48a,#e8b86d 50%,#b97c30);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;line-height:1;letter-spacing:-.01em;}}
.sticky-strip .ssh-sec{{font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.16em;color:var(--muted);}}
.sticky-strip .ssh-sec strong{{color:var(--gold);font-weight:500;}}

/* ── Sections ── */
.section{{padding:120px 28px;max-width:1200px;margin:0 auto;opacity:0;transform:translateY(50px);transition:opacity 1.1s cubic-bezier(.16,1,.3,1),transform 1.1s cubic-bezier(.16,1,.3,1);position:relative;z-index:5;}}
.section.in{{opacity:1;transform:translateY(0);}}
.section-h{{font-family:'DM Mono',monospace;font-size:.6rem;text-transform:uppercase;letter-spacing:.22em;color:var(--gold);margin-bottom:8px;}}
.section-title{{font-family:'Playfair Display',serif;font-size:clamp(1.9rem,3.6vw,3.2rem);font-weight:700;color:var(--text);margin-bottom:34px;line-height:1.1;letter-spacing:-0.015em;}}
.section-title em{{font-style:normal;color:var(--gold);font-weight:500;}}
.section-intro{{font-family:'Playfair Display',serif;font-size:1.08rem;color:#cdd5f0;max-width:720px;margin-bottom:36px;line-height:1.75;font-style:italic;font-weight:400;}}
.section-intro strong{{color:var(--gold);font-weight:500;font-style:normal;}}
@media(max-width:600px){{.section{{padding:70px 18px;}}.section-title{{font-size:1.65rem;margin-bottom:24px;}}.sticky-strip{{padding:10px 16px;}}}}

/* ── KPI tiles ── */
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:14px;}}
.kpi{{background:rgba(18,21,31,0.78);backdrop-filter:blur(6px);border:1px solid var(--border);border-radius:14px;padding:20px 18px;text-align:center;position:relative;overflow:hidden;}}
.kpi::after{{content:'';position:absolute;top:0;left:18px;right:18px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:.4;}}
.kpi-num{{font-family:'Playfair Display',serif;font-size:2.3rem;font-weight:700;color:var(--gold);line-height:1;}}
.kpi-lbl{{font-family:'DM Mono',monospace;font-size:.55rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);margin-top:7px;}}
.kpi-sub{{font-size:.66rem;color:var(--text);margin-top:5px;opacity:.7;}}

/* ── Year clock — polar bar at top of timeline ── */
.yearclock-wrap{{display:flex;flex-direction:column;align-items:center;gap:10px;margin:0 auto 60px;max-width:340px;animation:rotateIn 1.6s cubic-bezier(.22,1,.36,1) both;}}
@keyframes rotateIn{{from{{opacity:0;transform:rotate(-6deg) scale(.92);}}to{{opacity:1;transform:rotate(0) scale(1);}}}}
.yearclock{{width:100%;max-width:300px;height:auto;display:block;}}
.yc-wedge{{transition:opacity .2s,transform .2s;transform-origin:130px 130px;cursor:pointer;}}
.yc-wedge:hover{{opacity:1!important;transform:scale(1.04);filter:drop-shadow(0 0 8px rgba(232,184,109,.55));}}
.yc-mlbl{{font-family:'DM Mono',monospace;font-size:9px;fill:var(--muted);letter-spacing:.1em;text-transform:uppercase;}}
.yc-total{{font-family:'Playfair Display',serif;font-size:30px;fill:var(--gold);font-weight:700;}}
.yc-totlbl{{font-family:'DM Mono',monospace;font-size:7px;fill:var(--muted);letter-spacing:.18em;}}
.yc-caption{{font-family:'DM Mono',monospace;font-size:.55rem;color:var(--muted);letter-spacing:.16em;text-transform:uppercase;text-align:center;}}

/* ── Month timeline — alternating photo / text columns ── */
.months{{display:flex;flex-direction:column;gap:18px;}}
.mo{{display:grid;grid-template-columns:1fr 1fr;gap:30px;align-items:center;padding:14px 0;opacity:0;transform:translateY(40px);transition:opacity 1s cubic-bezier(.16,1,.3,1),transform 1s cubic-bezier(.16,1,.3,1);}}
.mo.in{{opacity:1;transform:translateY(0);}}
.mo:nth-child(even){{direction:rtl;}}
.mo:nth-child(even) > *{{direction:ltr;}}
.mo-photo{{aspect-ratio:4/3;border-radius:12px;background-size:cover;background-position:center;background-color:rgba(255,255,255,.03);position:relative;overflow:hidden;box-shadow:0 12px 30px rgba(0,0,0,.45);}}
.mo-photo::after{{content:'';position:absolute;inset:0;background:linear-gradient(135deg,transparent 60%,rgba(0,0,0,.25));pointer-events:none;}}
.mo-photo-empty{{aspect-ratio:4/3;border-radius:12px;background:linear-gradient(135deg,rgba(232,184,109,.05),rgba(78,201,176,.04));display:flex;align-items:center;justify-content:center;color:var(--muted);font-family:'DM Mono',monospace;font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;border:1px dashed rgba(255,255,255,.06);position:relative;overflow:hidden;}}
.mo-photo-empty::before{{content:'';position:absolute;inset:0;background:radial-gradient(circle at 30% 30%,rgba(232,184,109,.06),transparent 70%),radial-gradient(circle at 70% 70%,rgba(78,201,176,.05),transparent 70%);}}
.mo-photo.borrowed{{filter:grayscale(.45) brightness(.8) contrast(.95);}}
.mo-photo.borrowed::after{{content:'';position:absolute;inset:0;background:linear-gradient(135deg,rgba(0,0,0,.25) 0%,rgba(0,0,0,.55) 100%);pointer-events:none;}}
.mo-photo-tag{{position:absolute;top:10px;left:10px;z-index:2;background:rgba(10,12,18,.7);backdrop-filter:blur(4px);padding:3px 9px;border-radius:4px;font-family:'DM Mono',monospace;font-size:.5rem;color:rgba(232,184,109,.9);letter-spacing:.16em;text-transform:uppercase;border:1px solid rgba(232,184,109,.18);}}
.mo-mood{{display:inline-flex;align-items:center;gap:6px;margin-top:10px;padding:4px 10px;background:rgba(78,201,176,.10);border:1px solid rgba(78,201,176,.25);border-radius:10px;font-family:'DM Mono',monospace;font-size:.54rem;color:var(--teal);letter-spacing:.12em;text-transform:uppercase;}}
.mo-mood.warm{{background:rgba(232,184,109,.12);border-color:rgba(232,184,109,.3);color:var(--gold);}}
.mo-mood.dim{{background:rgba(232,119,138,.10);border-color:rgba(232,119,138,.25);color:var(--rose);}}
.mo-text-box{{position:relative;}}
.mo-num{{font-family:'Playfair Display',serif;font-size:5.5rem;font-weight:900;color:rgba(232,184,109,.16);line-height:.9;letter-spacing:-.04em;position:absolute;top:-26px;right:-4px;pointer-events:none;user-select:none;}}
.mo-name{{font-family:'Playfair Display',serif;font-size:1.8rem;font-weight:700;color:var(--gold);line-height:1.1;margin-bottom:4px;letter-spacing:-.01em;position:relative;z-index:1;}}
.mo-count{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);letter-spacing:.12em;margin-bottom:14px;font-weight:400;text-transform:uppercase;}}
.mo-narrative{{font-family:'Playfair Display',serif;font-size:1.05rem;line-height:1.7;color:var(--text);font-style:italic;font-weight:400;}}
.mo-narrative strong{{color:var(--gold);font-weight:500;font-style:normal;}}
.mo-shout{{margin-top:18px;padding:14px 0 14px 18px;border-left:2px solid var(--teal);font-family:'Playfair Display',serif;font-style:italic;font-size:.92rem;color:#e8e9ee;line-height:1.55;font-weight:400;position:relative;}}
.mo-shout::before{{content:'"';font-family:'Playfair Display',serif;font-size:3rem;color:rgba(78,201,176,.35);position:absolute;top:-12px;left:6px;line-height:1;font-style:normal;font-weight:700;}}
.mo-shout-meta{{font-family:'DM Mono',monospace;font-size:.55rem;color:var(--muted);margin-top:8px;font-style:normal;letter-spacing:.04em;}}
.mo.empty .mo-name{{color:var(--muted);}}
.mo.empty .mo-narrative{{color:var(--muted);}}
@media(max-width:700px){{.mo{{grid-template-columns:1fr;gap:16px;}}.mo:nth-child(even){{direction:ltr;}}.mo-num{{font-size:4rem;top:-18px;right:0;}}.mo-name{{font-size:1.45rem;}}}}

/* ── List items ── */
.three-col{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px;}}
.list-col-h{{font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.16em;color:var(--gold);margin-bottom:14px;}}
.item-list{{display:flex;flex-direction:column;gap:6px;}}
.item{{background:rgba(18,21,31,0.65);backdrop-filter:blur(6px);border:1px solid var(--border);border-radius:10px;padding:11px 14px;display:flex;align-items:center;gap:10px;transition:border-color .15s;}}
.item:hover{{border-color:rgba(232,184,109,.4);}}
.item .rank{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);width:22px;text-align:right;flex-shrink:0;}}
.item .name{{flex:1;font-size:.84rem;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.item .name a{{color:inherit;text-decoration:none;}}
.item .name a:hover{{color:var(--gold);}}
.item .cnt{{font-family:'DM Mono',monospace;font-size:.7rem;color:var(--teal);flex-shrink:0;}}

/* ── Flags ── */
.flag-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px;}}
.flag-item{{background:rgba(18,21,31,0.65);backdrop-filter:blur(6px);border:1px solid var(--border);border-radius:10px;padding:11px 16px;display:flex;align-items:center;gap:11px;}}
.flag-item .fi{{font-size:1.1em;border-radius:2px;}}
.flag-item .nm{{font-size:.82rem;font-weight:500;}}

/* ── Trip cards ── */
.trip-card{{background:rgba(18,21,31,0.78);backdrop-filter:blur(6px);border:1px solid var(--border);border-radius:14px;padding:20px 22px;text-decoration:none;color:var(--text);transition:border-color .2s,transform .2s;display:block;}}
.trip-card:hover{{border-color:var(--gold);transform:translateY(-2px);}}
.trip-name{{font-family:'Playfair Display',serif;font-size:1.2rem;font-weight:700;color:var(--gold);margin-bottom:6px;line-height:1.2;}}
.trip-meta{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);letter-spacing:.04em;}}
.trip-countries{{margin-top:8px;font-size:.78rem;color:var(--text);opacity:.85;}}

/* ── Flight cards (year page) ── */
.fl-card{{background:rgba(18,21,31,0.78);backdrop-filter:blur(6px);border:1px solid var(--border);border-radius:14px;padding:14px 18px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;}}
.fl-logo{{width:38px;height:38px;background:rgba(255,255,255,.94);border-radius:7px;padding:3px;object-fit:contain;flex-shrink:0;}}
.fl-logo-fb{{width:38px;height:38px;background:rgba(232,184,109,.15);border-radius:7px;display:flex;align-items:center;justify-content:center;color:var(--gold);font-size:1.05rem;flex-shrink:0;}}
.fl-route{{font-family:'Playfair Display',serif;font-size:1.25rem;font-weight:700;color:var(--gold);display:flex;align-items:baseline;gap:8px;}}
.fl-route .arrow{{font-family:'DM Sans',sans-serif;font-size:.9rem;color:var(--muted);font-weight:400;}}
.fl-fno{{font-family:'DM Mono',monospace;font-size:.66rem;color:var(--teal);background:rgba(78,201,176,.10);padding:2px 9px;border-radius:5px;}}
.fl-meta{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);letter-spacing:.04em;flex:1;display:flex;gap:14px;flex-wrap:wrap;min-width:0;}}

/* ── Photos ── */
.photo-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;}}
.photo{{aspect-ratio:1/1;background:var(--card2);border-radius:8px;overflow:hidden;position:relative;cursor:zoom-in;}}
.photo img{{width:100%;height:100%;object-fit:cover;transition:transform .5s ease;}}
.photo:hover img{{transform:scale(1.10);}}
.photo .ph-meta{{position:absolute;left:0;right:0;bottom:0;padding:6px 8px 8px;background:linear-gradient(transparent,rgba(0,0,0,.92));font-size:.62rem;color:#fff;opacity:0;transition:opacity .2s;}}
.photo:hover .ph-meta{{opacity:1;}}

/* ── Shouts — drop-quote style ── */
.shouts{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:18px;}}
.shout-card{{position:relative;padding:30px 20px 18px 26px;background:rgba(18,21,31,0.7);backdrop-filter:blur(8px);border:1px solid var(--border);border-radius:12px;border-left:3px solid var(--gold);}}
.shout-card::before{{content:'"';font-family:'Playfair Display',serif;font-size:5rem;font-weight:900;line-height:.8;color:rgba(232,184,109,.18);position:absolute;top:8px;left:14px;pointer-events:none;}}
.shout-text{{font-family:'Playfair Display',serif;font-style:italic;font-size:1.04rem;color:var(--text);line-height:1.6;font-weight:400;position:relative;}}
.shout-meta{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);margin-top:14px;letter-spacing:.06em;text-transform:uppercase;}}
.shout-meta strong{{color:var(--text);text-transform:none;letter-spacing:.04em;font-weight:500;}}

/* ── Lightbox ── */
.lb{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.94);z-index:9000;align-items:center;justify-content:center;cursor:zoom-out;padding:36px;}}
.lb.open{{display:flex;}}
.lb img{{max-width:100%;max-height:100%;border-radius:6px;}}

/* ── Year nav (prev/next) ── */
.year-nav{{display:flex;justify-content:space-between;gap:20px;padding:60px 28px 80px;max-width:1200px;margin:0 auto;position:relative;z-index:5;}}
.year-nav a{{display:flex;flex-direction:column;align-items:flex-start;gap:4px;background:rgba(18,21,31,0.78);backdrop-filter:blur(6px);border:1px solid var(--border);border-radius:12px;padding:14px 22px;text-decoration:none;color:var(--text);font-family:'DM Mono',monospace;font-size:.6rem;letter-spacing:.14em;text-transform:uppercase;transition:border-color .2s;}}
.year-nav a:hover{{border-color:var(--gold);}}
.year-nav a span:last-child{{font-family:'Playfair Display',serif;font-size:1.6rem;font-weight:700;color:var(--gold);}}
.year-nav .end{{text-align:right;align-items:flex-end;}}

/* ── Side nav ── */
.side-nav{{position:fixed;right:0;top:50%;transform:translateY(-50%);z-index:4000;display:flex;flex-direction:column;background:rgba(18,21,31,0.92);border:1px solid var(--border);border-right:none;border-radius:10px 0 0 10px;backdrop-filter:blur(10px);overflow:hidden;}}
.side-nav a{{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;width:52px;padding:6px 4px;color:var(--muted);text-decoration:none;}}
.side-nav a:hover{{color:var(--text);background:rgba(255,255,255,.06);}}
.side-nav .sn-icon{{font-size:.82rem;}}
.side-nav .sn-lbl{{font-family:'DM Mono',monospace;font-size:.42rem;text-transform:uppercase;letter-spacing:.05em;}}
@media(max-width:900px){{.side-nav{{display:none;}}}}
</style>
</head>
<body>

<a class="crumb" href="years.html">← All years</a>

<!-- Scroll progress bar -->
<div class="progbar" id="progBar"></div>

<!-- Scrolling background photo layer (fixed) -->
<div class="scroll-bg" id="scrollBg">{scroll_bg_imgs}</div>

<!-- HERO -->
<section class="hero" data-bg-index="0" data-sec-title="The year of {year}">
  <div class="hero-bg" id="heroBg">{hero_bg_html}</div>
  <div class="hero-overlay"></div>
  <div class="hero-content">
    <div class="hero-eyebrow">A year in review · {year}</div>
    <div class="hero-yr">{year}</div>
    <div class="hero-vivid">{vivid}</div>
  </div>
  <div class="hero-scroll">scroll to begin ↓</div>
</section>

<!-- Sticky section strip (appears after hero) -->
<div class="sticky-strip" id="stickyStrip" style="opacity:0;pointer-events:none;transition:opacity .4s ease;">
  <div class="ssh-yr">{year}</div>
  <div class="ssh-sec" id="sshSec"><strong>—</strong></div>
</div>

{kpi_section}
{months_section}
{top_section}
{new_countries_section}
{trips_section}
{flights_section}
{photos_section}
{shouts_section}
{companions_section}

{year_nav}

<div class="lb" id="lb" onclick="this.classList.remove('open')"><img id="lbImg" src="" alt=""></div>

<nav class="side-nav" aria-label="Page shortcuts">
  <a href="index.html"><span class="sn-icon">🏠</span><span class="sn-lbl">HOME</span></a>
  <a href="stats.html"><span class="sn-icon">📊</span><span class="sn-lbl">STATS</span></a>
  <a href="trips.html"><span class="sn-icon">✈</span><span class="sn-lbl">TRIPS</span></a>
  <a href="feed.html"><span class="sn-icon">📰</span><span class="sn-lbl">FEED</span></a>
</nav>

<script>
// ── Section fade-in ──
const sObs = new IntersectionObserver(es => es.forEach(e => e.isIntersecting && e.target.classList.add('in')), {{threshold: 0.06}});
document.querySelectorAll('.section, .mo').forEach(s => sObs.observe(s));

// ── Scroll progress bar ──
(function(){{
  const bar = document.getElementById('progBar');
  if (!bar) return;
  const update = () => {{
    const max = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    const pct = (window.scrollY / max) * 100;
    bar.style.width = pct + '%';
  }};
  window.addEventListener('scroll', update, {{passive: true}});
  window.addEventListener('resize', update);
  update();
}})();

// ── Sticky section strip ──
(function(){{
  const strip = document.getElementById('stickyStrip');
  const secEl = document.getElementById('sshSec');
  if (!strip || !secEl) return;
  const hero = document.querySelector('.hero');
  const sections = Array.from(document.querySelectorAll('[data-sec-title]'));
  // Track current section title for the sticky strip
  const titleMap = new Map();
  sections.forEach(s => titleMap.set(s, s.dataset.secTitle));
  // Show strip after the hero is past the viewport
  const heroObs = new IntersectionObserver(es => {{
    es.forEach(e => {{
      if (e.isIntersecting) {{
        strip.style.opacity = '0';
        strip.style.pointerEvents = 'none';
      }} else {{
        strip.style.opacity = '1';
        strip.style.pointerEvents = '';
      }}
    }});
  }}, {{threshold: 0}});
  heroObs.observe(hero);
  // Update sticky title based on the most-visible section
  const sObs2 = new IntersectionObserver(es => {{
    let best = null, bestScore = 0;
    sections.forEach(s => {{
      const r = s.getBoundingClientRect();
      const inview = Math.max(0, Math.min(window.innerHeight, r.bottom) - Math.max(0, r.top));
      if (inview > bestScore) {{ bestScore = inview; best = s; }}
    }});
    if (best) secEl.innerHTML = '<strong>' + (titleMap.get(best) || '') + '</strong>';
  }}, {{rootMargin: '-20% 0px -50% 0px', threshold: 0}});
  sections.forEach(s => sObs2.observe(s));
}})();

// ── KPI counters ──
function animateCount(el, target) {{
  const start = performance.now(), dur = 1300;
  function tick(t) {{
    const p = Math.min(1, (t - start) / dur);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(target * eased).toLocaleString();
    if (p < 1) requestAnimationFrame(tick);
  }}
  requestAnimationFrame(tick);
}}
const kObs = new IntersectionObserver(es => es.forEach(e => {{
  if (!e.isIntersecting) return;
  e.target.querySelectorAll('[data-count]').forEach(el => {{
    const v = +el.dataset.count;
    if (!isNaN(v)) animateCount(el, v);
    el.removeAttribute('data-count');
  }});
  kObs.unobserve(e.target);
}}), {{threshold: 0.3}});
document.querySelectorAll('.kpi-grid').forEach(g => kObs.observe(g));

// ── Scrolling background photo cycle ──
// Each section gets a data-bg-index; the corresponding img in #scrollBg becomes active.
(function(){{
  const sbgImgs = document.querySelectorAll('#scrollBg .sbg-img');
  if (!sbgImgs.length) return;
  let activeIdx = -1;
  function setActive(i) {{
    if (i === activeIdx) return;
    if (activeIdx >= 0 && sbgImgs[activeIdx]) sbgImgs[activeIdx].classList.remove('active');
    activeIdx = i;
    if (i >= 0 && sbgImgs[i]) sbgImgs[i].classList.add('active');
  }}
  const bgObs = new IntersectionObserver(es => {{
    let visible = null, score = 0;
    document.querySelectorAll('[data-bg-index]').forEach(el => {{
      const r = el.getBoundingClientRect();
      const inview = Math.max(0, Math.min(window.innerHeight, r.bottom) - Math.max(0, r.top));
      if (inview > score) {{ score = inview; visible = el; }}
    }});
    if (visible) setActive(+visible.dataset.bgIndex);
  }}, {{threshold: 0, rootMargin: '-30% 0px -30% 0px'}});
  document.querySelectorAll('[data-bg-index]').forEach(el => bgObs.observe(el));
  // Initial state
  setActive(0);
}})();

// ── Hero Ken-Burns ──
(function(){{
  const bg = document.getElementById('heroBg');
  if (!bg) return;
  const photos = bg.querySelectorAll('.ph');
  if (!photos.length) return;
  if (photos.length === 1) {{ photos[0].classList.add('active'); return; }}
  let i = 0; photos[0].classList.add('active');
  setInterval(() => {{ photos[i].classList.remove('active'); i = (i + 1) % photos.length; photos[i].classList.add('active'); }}, 5200);
}})();

window.openLB = src => {{ document.getElementById('lbImg').src = src; document.getElementById('lb').classList.add('open'); }};

// ── Airline-logo multi-CDN fallback (Kiwi → gstatic → Airhex → ✈) ──
function setLogo(img, iata) {{
  if (!iata) {{ img.style.display='none'; const fb=img.nextElementSibling; if(fb)fb.style.display='flex'; return; }}
  const cdns = [
    'https://images.kiwi.com/airlines/64/'+iata+'.png',
    'https://www.gstatic.com/flights/airline_logos/70px/'+iata+'.png',
    'https://content.airhex.com/content/logos/airlines_'+iata+'_70_70_s.png',
  ];
  let i = 0;
  img.onerror = () => {{ i++; if (i < cdns.length) img.src = cdns[i]; else {{ img.style.display='none'; const fb=img.nextElementSibling; if(fb)fb.style.display='flex'; }} }};
  img.src = cdns[0];
}}
document.querySelectorAll('img.fl-logo[id^="ypfl_"]').forEach(img => {{
  const iata = img.dataset.iata || '';
  setLogo(img, iata);
}});

(function(){{document.body.classList.toggle('light', localStorage.getItem('fsq-theme') === 'light');}})();
</script>
</body>
</html>
"""


def build_page(
    csv_path: str,
    config_dir: str,
    out_path: str,
    tmpl_path: str,
    rows: list | None = None,
    stats_data: dict | None = None,
    photos_by_checkin: dict | None = None,
    pix_url: str = "",
    trips: list | None = None,
    flight_history: dict | None = None,
    flights_data: list | None = None,
    **_extra,
) -> None:
    out_dir = Path(out_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    _load_flags(Path(config_dir))

    stats_data = stats_data or {}
    photos_by_checkin = photos_by_checkin or {}
    rows = rows or []
    trips = trips or []
    year_summaries = stats_data.get("year_summaries", [])
    if not year_summaries:
        return

    months_full = ["January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"]

    # Index rows by year
    by_year_rows: dict[int, list] = defaultdict(list)
    for r in rows:
        try:
            ts = int(r.get("date", 0) or 0)
        except ValueError:
            continue
        if not ts:
            continue
        yr = datetime.fromtimestamp(ts, tz=timezone.utc).year
        by_year_rows[yr].append(r)

    # Index photos by year via the checkin_id → date lookup
    cid_year: dict[str, int] = {}
    cid_row: dict[str, dict] = {}
    for r in rows:
        cid = (r.get("checkin_id") or "").strip()
        try:
            ts = int(r.get("date", 0) or 0)
        except ValueError:
            continue
        if cid and ts:
            cid_year[cid] = datetime.fromtimestamp(ts, tz=timezone.utc).year
            cid_row[cid] = r
    photos_by_year: dict[int, list[dict]] = defaultdict(list)
    photos_by_yr_mo: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for cid, fnames in photos_by_checkin.items():
        yr = cid_year.get(cid)
        r = cid_row.get(cid)
        if not yr or not fnames or not r:
            continue
        ts = int(r.get("date", 0) or 0)
        mo = datetime.fromtimestamp(ts, tz=timezone.utc).month if ts else 0
        for fn in fnames:
            entry = {
                "src": f"{pix_url.rstrip('/')}/{fn}" if pix_url else fn,
                "venue": r.get("venue", ""),
                "city": r.get("city", ""),
                "ts": ts,
                "checkin_id": cid,
            }
            photos_by_year[yr].append(entry)
            photos_by_yr_mo[(yr, mo)].append(entry)

    # Shouts by year+month (cleaned: no "with X"-only)
    import re as _re
    suffix_re = _re.compile(r"\s*[—\-–]\s*with\s+.+$", _re.IGNORECASE | _re.UNICODE)
    with_only_re = _re.compile(r"^\s*[—\-–]?\s*with\s+\S+", _re.IGNORECASE | _re.UNICODE)
    shouts_by_year: dict[int, list[dict]] = defaultdict(list)
    shouts_by_yr_mo: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for r in rows:
        s = (r.get("shout") or "").strip()
        if not s:
            continue
        clean = suffix_re.sub("", s).strip()
        if not clean or with_only_re.match(clean):
            continue
        try:
            ts = int(r.get("date", 0) or 0)
        except ValueError:
            continue
        if not ts:
            continue
        d = datetime.fromtimestamp(ts, tz=timezone.utc)
        rec = {"text": clean, "venue": r.get("venue", ""), "city": r.get("city", ""),
               "country": r.get("country", ""), "ts": ts}
        shouts_by_year[d.year].append(rec)
        shouts_by_yr_mo[(d.year, d.month)].append(rec)

    # Trips and flights by year
    trips_by_year: dict[int, list[dict]] = defaultdict(list)
    for t in trips:
        yr = t.get("start_year")
        if yr:
            trips_by_year[yr].append(t)
    flights_by_year: dict[int, list[dict]] = defaultdict(list)
    if flights_data:
        for f in flights_data:
            try:
                yr = int((f.get("date") or "")[:4])
            except (ValueError, IndexError):
                continue
            flights_by_year[yr].append(f)

    # All year list (for prev/next nav)
    all_years_sorted = sorted({ys["year"] for ys in year_summaries})

    # ── Render one page per year ─────────────────────────────────────────
    for ys in year_summaries:
        yr = ys["year"]
        rows_y = by_year_rows.get(yr, [])
        photos_y = sorted(photos_by_year.get(yr, []), key=lambda p: -p["ts"])
        shouts_y = shouts_by_year.get(yr, [])
        trips_y = sorted(trips_by_year.get(yr, []), key=lambda t: t.get("start_ts", 0))
        flights_y = sorted(flights_by_year.get(yr, []), key=lambda f: f.get("date", ""))

        # Build counters
        ven_y: Counter = Counter()
        cty_y: Counter = Counter()
        cat_y: Counter = Counter()
        comp_y: Counter = Counter()
        mon_y_counter: Counter = Counter()
        mon_venue: dict[int, Counter] = defaultdict(Counter)
        mon_city: dict[int, Counter] = defaultdict(Counter)
        mon_cat: dict[int, Counter] = defaultdict(Counter)
        try:
            from metrics import collect_companions as _cc
        except ImportError:
            _cc = lambda r: []
        for r in rows_y:
            vid = (r.get("venue_id") or "").strip()
            vn = (r.get("venue") or "").strip()
            if vid and vn:
                ven_y[(vid, vn)] += 1
            cy = (r.get("city") or "").strip()
            ct = (r.get("category") or "").strip()
            if cy: cty_y[cy] += 1
            if ct: cat_y[ct] += 1
            for n in _cc(r): comp_y[n] += 1
            try:
                ts_y = int(r.get("date", 0) or 0)
                if ts_y:
                    d_y = datetime.fromtimestamp(ts_y, tz=timezone.utc)
                    mo_y = d_y.month
                    mon_y_counter[mo_y] += 1
                    if vn: mon_venue[mo_y][vn] += 1
                    if cy: mon_city[mo_y][cy] += 1
                    if ct: mon_cat[mo_y][ct] += 1
            except ValueError:
                pass

        # HERO Ken-Burns photos (top 5 — most recent)
        hero_photos = photos_y[:5]
        hero_bg_html = "".join(
            f'<div class="ph" style="background-image:url(\'{_esc(p["src"])}\')"></div>'
            for p in hero_photos
        )

        # Scrolling background photos (one per non-hero section, picked spaced
        # out from the year's photos so each section has a different feel)
        section_count = 9  # roughly: kpi, months, top, newc, trips, flights, photos, shouts, comp
        bg_pool = photos_y[5:5 + section_count] if len(photos_y) > 5 else photos_y[:section_count]
        # Pad with cycle if fewer photos than sections
        while len(bg_pool) < section_count and photos_y:
            bg_pool += photos_y
        bg_pool = bg_pool[:section_count]
        scroll_bg_imgs = "".join(
            f'<div class="sbg-img" style="background-image:url(\'{_esc(p["src"])}\')"></div>'
            for p in bg_pool
        )

        # KPI section
        kpi_items: list[tuple[int, str, str]] = [
            (ys["total"], "Check-ins", ""),
            (ys["cities"], "Cities", ""),
            (ys["countries"], "Countries", ""),
            (ys["new_countries"], "New countries", "discovered for the first time"),
            (ys.get("distance_km", 0) or 0, "km travelled", "between check-ins"),
            (ys.get("trip_count", 0) or 0, "Trips", ""),
            (len(photos_y), "Photos", ""),
            (len(flights_y), "Flights", ""),
        ]
        kpi_html = ""
        for n, lbl, sub in kpi_items:
            if n is None:
                continue
            kpi_html += (
                f'<div class="kpi"><div class="kpi-num" data-count="{n}">0</div>'
                f'<div class="kpi-lbl">{_esc(lbl)}</div>'
                + (f'<div class="kpi-sub">{_esc(sub)}</div>' if sub else "")
                + "</div>"
            )
        kpi_section = (
            f'<section class="section" id="sec-kpi" data-bg-index="1" data-sec-title="By the Numbers">'
            f'<div class="section-h">By the numbers</div>'
            f'<div class="section-title">{yr}, distilled</div>'
            f'<div class="section-intro">A year measured in steps and stamps — eight ways to see the shape of it.</div>'
            f'<div class="kpi-grid">{kpi_html}</div>'
            f'</section>'
        )

        # ── Month-by-month timeline — alternating photo / text layout ──
        # Each month is a 2-column grid; even rows reverse via direction:rtl.
        # Empty months borrow a photo from the nearest non-empty month so
        # the visual rhythm stays consistent.
        months_short = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        # Group rows by month for richer narrative composition
        rows_by_mo: dict[int, list] = defaultdict(list)
        for r in rows_y:
            try:
                ts_r = int(r.get("date", 0) or 0)
                if not ts_r:
                    continue
                rows_by_mo[datetime.fromtimestamp(ts_r, tz=timezone.utc).month].append(r)
            except ValueError:
                continue

        mo_rows_html: list[str] = []
        for mo in range(1, 13):
            n = mon_y_counter.get(mo, 0)
            mo_name = months_full[mo - 1]
            mo_num = f"{mo:02d}"
            mo_shouts = shouts_by_yr_mo.get((yr, mo), [])

            if n == 0:
                # Empty month — try to borrow a photo from a nearby month
                borrowed, tag = _find_nearest_photo(yr, mo, photos_by_yr_mo, months_full)
                if borrowed:
                    photo_html = (
                        f'<div class="mo-photo borrowed" style="background-image:url(\'{_esc(borrowed["src"])}\')">'
                        f'<div class="mo-photo-tag">↺ {_esc(tag)}</div>'
                        f'</div>'
                    )
                else:
                    photo_html = f'<div class="mo-photo-empty">Quiet · {mo_name}</div>'
                mo_rows_html.append(
                    f'<div class="mo empty" id="mo-{mo_num}">'
                    f'{photo_html}'
                    f'<div class="mo-text-box">'
                    f'<div class="mo-num">{mo_num}</div>'
                    f'<div class="mo-name">{mo_name}</div>'
                    f'<div class="mo-count">No check-ins logged</div>'
                    f'<div class="mo-narrative">A month off the map — the calendar quietly empty.</div>'
                    f'</div></div>'
                )
                continue

            mo_photos = photos_by_yr_mo.get((yr, mo), [])
            if mo_photos:
                photo_html = (
                    f'<div class="mo-photo" style="background-image:url(\'{_esc(mo_photos[0]["src"])}\')"></div>'
                )
            else:
                # Borrow from neighbour, fall back to empty card
                borrowed, tag = _find_nearest_photo(yr, mo, photos_by_yr_mo, months_full)
                if borrowed:
                    photo_html = (
                        f'<div class="mo-photo borrowed" style="background-image:url(\'{_esc(borrowed["src"])}\')">'
                        f'<div class="mo-photo-tag">↺ {_esc(tag)}</div>'
                        f'</div>'
                    )
                else:
                    photo_html = f'<div class="mo-photo-empty">{mo_name}</div>'

            mo_narr = _compose_month_narrative(
                yr, mo, rows_by_mo[mo],
                mon_venue[mo], mon_city[mo], mon_cat[mo], mo_shouts,
            )

            # Mood badge
            mood = _mood_score(mo_shouts)
            mood_html = ""
            if mood >= 3:
                mood_html = '<span class="mo-mood warm">↑ bright stretch</span>'
            elif mood >= 1:
                mood_html = '<span class="mo-mood warm">↑ upbeat</span>'
            elif mood <= -2:
                mood_html = '<span class="mo-mood dim">↓ sharper edges</span>'

            # Pick the most memorable shout from this month
            shout_html = ""
            if mo_shouts:
                top_shout = max(mo_shouts, key=lambda s: len(s["text"]))
                if len(top_shout["text"]) >= 14:
                    meta_parts = [p for p in [top_shout["venue"], top_shout["city"]] if p]
                    meta_str = " · ".join(_esc(p) for p in meta_parts)
                    shout_html = (
                        f'<div class="mo-shout">'
                        f'{_esc(top_shout["text"])}'
                        + (f'<div class="mo-shout-meta">— {meta_str}</div>' if meta_str else '')
                        + '</div>'
                    )

            mo_rows_html.append(
                f'<div class="mo" id="mo-{mo_num}">'
                f'{photo_html}'
                f'<div class="mo-text-box">'
                f'<div class="mo-num">{mo_num}</div>'
                f'<div class="mo-name">{mo_name}</div>'
                f'<div class="mo-count">{n:,} check-ins</div>'
                f'<div class="mo-narrative">{mo_narr}</div>'
                f'{mood_html}'
                f'{shout_html}'
                f'</div></div>'
            )
        year_clock_html = _render_year_clock(yr, mon_y_counter, months_short)
        months_section = (
            f'<section class="section" id="sec-months" data-bg-index="2" data-sec-title="Month by Month">'
            f'<div class="section-h">Month by month</div>'
            f'<div class="section-title">Twelve panels of <em>{yr}</em></div>'
            f'<div class="section-intro">A pace check, month by month. The dial below sets the shape of the year at a glance — each wedge sized to that month\'s volume. Below, the photo on each row is from those weeks; the line beside it picks the city, the rhythm, and the landmark events that broke the routine.</div>'
            f'<div class="yearclock-wrap">{year_clock_html}<div class="yc-caption">{yr} · ring of months · tap a wedge to jump</div></div>'
            f'<div class="months">{"".join(mo_rows_html)}</div>'
            f'</section>'
        )

        # ── Top venues / cities / categories ──
        def _list_html(items: list, with_link: bool = False) -> str:
            out_lines: list[str] = []
            for i, (name, count) in enumerate(items[:8], 1):
                if isinstance(name, tuple):
                    vid, vname = name
                    link = (
                        f'<a href="https://foursquare.com/v/{_esc(vid)}" target="_blank" rel="noopener">{_esc(vname)}</a>'
                        if with_link and vid else _esc(vname)
                    )
                    out_lines.append(
                        f'<div class="item"><span class="rank">#{i}</span>'
                        f'<span class="name">{link}</span>'
                        f'<span class="cnt">{count}×</span></div>'
                    )
                else:
                    out_lines.append(
                        f'<div class="item"><span class="rank">#{i}</span>'
                        f'<span class="name">{_esc(name)}</span>'
                        f'<span class="cnt">{count}×</span></div>'
                    )
            return "".join(out_lines) or '<div style="color:var(--muted);font-size:.78rem;">—</div>'

        top_section = (
            f'<section class="section" id="sec-top" data-bg-index="3" data-sec-title="Anchors">'
            f'<div class="section-h">Anchors</div>'
            f'<div class="section-title">The places that <em>kept showing up</em></div>'
            f'<div class="section-intro">Some venues become rituals. Some cities pull you back without trying. Here\'s the gravity of {yr}.</div>'
            f'<div class="three-col">'
            f'<div><div class="list-col-h">Top venues</div><div class="item-list">{_list_html(ven_y.most_common(8), with_link=True)}</div></div>'
            f'<div><div class="list-col-h">Top cities</div><div class="item-list">{_list_html(cty_y.most_common(8))}</div></div>'
            f'<div><div class="list-col-h">Top categories</div><div class="item-list">{_list_html(cat_y.most_common(8))}</div></div>'
            f'</div></section>'
        )

        # ── New countries ──
        nc = ys.get("new_countries_list", [])
        if nc:
            nc_html = "".join(
                f'<div class="flag-item">{_flag(c)}<span class="nm">{_esc(c)}</span></div>'
                for c in nc
            )
            new_countries_section = (
                f'<section class="section" id="sec-newc" data-bg-index="4" data-sec-title="New Ground">'
                f'<div class="section-h">Firsts</div>'
                f'<div class="section-title">New ground in <em>{yr}</em></div>'
                f'<div class="section-intro">Borders crossed for the first time — the dots that turned gold.</div>'
                f'<div class="flag-grid">{nc_html}</div>'
                f'</section>'
            )
        else:
            new_countries_section = ""

        # ── Trips ──
        if trips_y:
            trip_cards = "".join(
                f'<a class="trip-card" href="trip-{t.get("id")}.html">'
                f'<div class="trip-name">{_esc(t.get("name", "Trip"))}</div>'
                f'<div class="trip-meta">{_esc(t.get("start_date", ""))} → {_esc(t.get("end_date", ""))} · {t.get("duration", 0)} days · {t.get("checkin_count", 0)} check-ins</div>'
                f'<div class="trip-countries">{" · ".join(_esc(c) for c in (t.get("countries") or [])[:5])}</div>'
                f'</a>'
                for t in trips_y
            )
            trips_section = (
                f'<section class="section" id="sec-trips" data-bg-index="5" data-sec-title="Journeys">'
                f'<div class="section-h">Journeys</div>'
                f'<div class="section-title">{len(trips_y)} trip{"s" if len(trips_y) != 1 else ""} <em>this year</em></div>'
                f'<div class="section-intro">Departures and returns — tap any card to enter the trip in full.</div>'
                f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;">{trip_cards}</div>'
                f'</section>'
            )
        else:
            trips_section = ""

        # ── Flights (year page) ──
        if flights_y:
            f_cards_parts: list[str] = []
            for f_idx, f in enumerate(flights_y):
                ia = _esc(f.get("airline_iata", ""))
                logo = (
                    f'<img class="fl-logo" id="ypfl_{yr}_{f_idx}" data-iata="{ia}" alt=""/>'
                    f'<div class="fl-logo-fb" style="display:none;">✈</div>'
                ) if f.get("airline_iata") else '<div class="fl-logo-fb">✈</div>'
                meta_bits = [f'<span>📅 {_esc(f.get("date", ""))}</span>']
                if f.get("airline"):
                    meta_bits.append(f'<span>{_esc(f.get("airline"))}</span>')
                if f.get("aircraft_code") or f.get("aircraft"):
                    meta_bits.append(f'<span>{_esc(f.get("aircraft_code", "") or f.get("aircraft", ""))}</span>')
                if f.get("dur_min"):
                    meta_bits.append(f'<span>{f.get("dur_min", 0) // 60}h {f.get("dur_min", 0) % 60}m</span>')
                f_cards_parts.append(
                    f'<div class="fl-card">{logo}'
                    f'<div class="fl-route">{_esc(f.get("from_iata", "—"))}<span class="arrow">→</span>{_esc(f.get("to_iata", "—"))}</div>'
                    + (f'<div class="fl-fno">{_esc(f.get("flight", ""))}</div>' if f.get("flight") else "")
                    + f'<div class="fl-meta">{"".join(meta_bits)}</div>'
                    + '</div>'
                )
            flights_section = (
                f'<section class="section" id="sec-flights" data-bg-index="6" data-sec-title="In the Air">'
                f'<div class="section-h">In the air</div>'
                f'<div class="section-title">{len(flights_y)} flight{"s" if len(flights_y) != 1 else ""} <em>across {yr}</em></div>'
                f'<div class="section-intro">Every leg cross-checked with the FlightRadar24 diary.</div>'
                f'<div style="display:flex;flex-direction:column;gap:8px;">{"".join(f_cards_parts)}</div>'
                f'</section>'
            )
        else:
            flights_section = ""

        # ── Photos full grid ──
        if photos_y:
            ph_cards = "".join(
                f'<div class="photo" onclick="openLB(\'{_esc(p["src"])}\')">'
                f'<img src="{_esc(p["src"])}" loading="lazy" alt="">'
                f'<div class="ph-meta">{_esc(p["venue"])}{" · " + _esc(p["city"]) if p["city"] else ""}</div>'
                f'</div>'
                for p in photos_y[:90]
            )
            photos_section = (
                f'<section class="section" id="sec-photos" data-bg-index="7" data-sec-title="Memory">'
                f'<div class="section-h">Memory</div>'
                f'<div class="section-title">{len(photos_y)} frame{"s" if len(photos_y) != 1 else ""} kept</div>'
                f'<div class="section-intro">The pictures you held onto. Tap to open any one full-screen.</div>'
                f'<div class="photo-grid">{ph_cards}</div>'
                + (f'<div style="text-align:center;margin-top:18px;font-size:.74rem;color:var(--muted);">Showing 90 of {len(photos_y):,}.</div>' if len(photos_y) > 90 else "")
                + '</section>'
            )
        else:
            photos_section = ""

        # ── Memorable shouts (longest 12, drop-quote style) ──
        memorable = sorted(shouts_y, key=lambda s: -len(s["text"]))[:12]
        if memorable:
            s_cards_parts: list[str] = []
            for s in memorable:
                meta_str = f'<strong>{_esc(s["venue"])}</strong>' if s["venue"] else ""
                if s["city"]:
                    meta_str += (" · " if meta_str else "") + _esc(s["city"])
                if s["country"]:
                    meta_str += (" · " if meta_str else "") + _esc(s["country"])
                s_cards_parts.append(
                    f'<div class="shout-card">'
                    f'<div class="shout-text">{_esc(s["text"])}</div>'
                    f'<div class="shout-meta">— {meta_str}</div>'
                    f'</div>'
                )
            shouts_section = (
                f'<section class="section" id="sec-shouts" data-bg-index="8" data-sec-title="Your Own Voice">'
                f'<div class="section-h">Words</div>'
                f'<div class="section-title"><em>{yr}</em> in your own voice</div>'
                f'<div class="section-intro">A handful of the year\'s longest shouts — fragments you wrote as you stood there.</div>'
                f'<div class="shouts">{"".join(s_cards_parts)}</div>'
                f'</section>'
            )
        else:
            shouts_section = ""

        # ── Companions ──
        top_comp = comp_y.most_common(12)
        if top_comp:
            companions_section = (
                f'<section class="section" id="sec-comp" data-bg-index="9" data-sec-title="With You">'
                f'<div class="section-h">With you</div>'
                f'<div class="section-title">In good company</div>'
                f'<div class="section-intro">The names that showed up most often beside yours through {yr}.</div>'
                f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px;">{_list_html(top_comp)}</div>'
                f'</section>'
            )
        else:
            companions_section = ""

        # ── Year prev/next nav ──
        idx = all_years_sorted.index(yr) if yr in all_years_sorted else -1
        prev_yr = all_years_sorted[idx - 1] if idx > 0 else None
        next_yr = all_years_sorted[idx + 1] if 0 <= idx < len(all_years_sorted) - 1 else None
        prev_html = f'<a href="year-{prev_yr}.html"><span>← Previous</span><span>{prev_yr}</span></a>' if prev_yr else '<span></span>'
        next_html = f'<a class="end" href="year-{next_yr}.html"><span>Next →</span><span>{next_yr}</span></a>' if next_yr else '<span></span>'
        year_nav = f'<div class="year-nav">{prev_html}{next_html}</div>'

        description = (
            f"{ys.get('total', 0):,} check-ins across "
            f"{ys.get('cities', 0)} cities and "
            f"{ys.get('countries', 0)} countries in {yr}."
        )
        html = PAGE_TEMPLATE.format(
            year=yr,
            title=f"{yr} — Year in Review",
            description=description,
            vivid=ys.get("vivid", description),
            hero_bg_html=hero_bg_html,
            scroll_bg_imgs=scroll_bg_imgs,
            kpi_section=kpi_section,
            months_section=months_section,
            top_section=top_section,
            new_countries_section=new_countries_section,
            trips_section=trips_section,
            flights_section=flights_section,
            photos_section=photos_section,
            shouts_section=shouts_section,
            companions_section=companions_section,
            year_nav=year_nav,
        )
        (out_dir / f"year-{yr}.html").write_text(html, encoding="utf-8")

    print(f"year-YYYY.html -> {out_dir}  ({len(year_summaries)} pages)")
