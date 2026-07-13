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

from year_covers import load_month_cover_overrides, load_month_notes, select_year_covers

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


# ── Narrative helpers ───────────────────────────────────────────────────
_MONTHS_FULL = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]
_SMALL_NUMS = ["zero", "one", "two", "three", "four", "five", "six",
               "seven", "eight", "nine", "ten", "eleven", "twelve"]


def _nword(n: int) -> str:
    """Counts up to twelve as words ('seven'), larger ones as digits."""
    return _SMALL_NUMS[n] if 0 <= n < len(_SMALL_NUMS) else f"{n:,}"


def _dayord(d: int) -> str:
    """1 → '1st', 22 → '22nd', 13 → '13th'."""
    if 11 <= d % 100 <= 13:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
    return f"{d}{suf}"


def _oxford(items: list[str]) -> str:
    """'A' / 'A and B' / 'A, B and C'."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _cap_html(body: str) -> str:
    """Capitalize the first plain letter, skipping any leading HTML tags."""
    i = 0
    while i < len(body) and body[i] == "<":
        close = body.find(">", i)
        if close < 0:
            break
        i = close + 1
    if i < len(body):
        body = body[:i] + body[i].upper() + body[i + 1:]
    return body


def _strong(s: str) -> str:
    return f"<strong>{_esc(s)}</strong>"


def _compose_month_narrative(
    yr: int, mo: int,
    rows_m: list[dict], mon_venue_m: Counter, mon_city_m: Counter,
    mon_cat_m: Counter, shouts_m: list[dict],
    *,
    comp_m: Counter | None = None,
    days_active: int = 0,
    new_venues_m: list[tuple[str, str]] | None = None,
    trips_starting: list[dict] | None = None,
    trips_ending: list[dict] | None = None,
    photos_count_m: int = 0,
    new_countries_m: list[str] | None = None,
    country_ordinals: dict[str, int] | None = None,
) -> str:
    """Write the month's story in real sentences instead of stat fragments.

    Shape (at most five short sentences, fully deterministic):
      1. Lead — the geographic shape of the month (home / split / on the road).
      2. Rhythm or anchor — the dominant routine, OR the venue that kept
         recurring (never both; whichever wins rotates month to month).
      3. Landmarks — up to two named one-off events, with the venue's OWN
         visit count (the old text misleadingly showed the bucket's total).
      4. Journey — trips departing or winding down, with door-to-door length;
         first-time countries carry their all-time ordinal ("country № 47").
      5. Texture — at most two of: top companion, new-places volume, photo
         volume, busiest single day, weekend tilt, thin-log note, mood.

    Phrase pools rotate on (yr + mo) so adjacent months never reuse the same
    template; counts up to twelve are spelled out and "(N×)" tallies are gone.
    """
    rot = yr + mo
    mon_name = _MONTHS_FULL[mo - 1]
    total_m = len(rows_m)

    # ── Raw material ─────────────────────────────────────────────────
    bucket_n: Counter = Counter()
    bucket_venue: dict[str, Counter] = defaultdict(Counter)
    venue_city: dict[str, Counter] = defaultdict(Counter)
    day_counter: Counter = Counter()
    weekend_n = 0
    for r in rows_m:
        b = _bucket_for(r.get("category") or "")
        v = (r.get("venue") or "").strip()
        c = (r.get("city") or "").strip()
        if v and c:
            venue_city[v][c] += 1
        if b:
            bucket_n[b] += 1
            if v:
                bucket_venue[b][v] += 1
        try:
            ts = int(r.get("date") or 0)
        except (ValueError, TypeError):
            ts = 0
        if ts:
            d = datetime.fromtimestamp(ts, tz=timezone.utc)
            day_counter[d.day] += 1
            if d.weekday() >= 5:
                weekend_n += 1

    routines = sorted(
        [(b, n) for b, n in bucket_n.items()
         if ACTIVITY_BUCKETS[b]["kind"] == "routine" and n > 0],
        key=lambda x: -x[1],
    )
    landmarks = sorted(
        [(b, n) for b, n in bucket_n.items()
         if ACTIVITY_BUCKETS[b]["kind"] == "landmark" and n > 0],
        key=lambda x: -x[1],
    )

    city_total = sum(mon_city_m.values()) or 1
    top_c = mon_city_m.most_common(1)[0] if mon_city_m else ("", 0)
    second_c = mon_city_m.most_common(2)[1] if len(mon_city_m) >= 2 else ("", 0)
    sig_cities = [c for c, nn in mon_city_m.most_common() if nn / city_total >= 0.12]
    multi_city = bool(top_c[0]) and (top_c[1] / city_total) < 0.55
    lead_city = top_c[0] if (top_c[0] and not multi_city) else ""

    def _landmark_frag(b: str, with_count: bool) -> str:
        """'walks in <strong>Park</strong> · City — five visits over the month'"""
        ph = ACTIVITY_BUCKETS[b]["phrases"]
        phrase = ph[(rot + len(b)) % len(ph)]
        top_v = bucket_venue[b].most_common(1)
        if not top_v or not top_v[0][0]:
            return ""
        venue, vcnt = top_v[0]
        v_city = venue_city[venue].most_common(1)[0][0] if venue_city.get(venue) else ""
        label = _strong(venue)
        # Annotate the venue's city on multi-city months (or when it sits
        # outside the lead city) so cross-city months read honestly.
        if v_city and (multi_city or (lead_city and v_city != lead_city)):
            label += ('<span style="color:var(--muted);font-style:normal;'
                      f'font-size:.85em;"> · {_esc(v_city)}</span>')
        frag = f"{phrase} {label}"
        if with_count and vcnt >= 4:
            frag += f" — {_nword(vcnt)} visits over the month"
        return frag

    # ── Sparse months read short and honest ──────────────────────────
    if total_m < 8:
        where = f", most of it around {_strong(top_c[0])}" if top_c[0] else ""
        plural = "s" if total_m != 1 else ""
        leads = [
            f"A quiet {mon_name} — {_nword(total_m)} check-in{plural} in the log{where}.",
            f"The log runs thin here: {_nword(total_m)} entr{'ies' if total_m != 1 else 'y'}{where}.",
            f"{mon_name} barely registered — {_nword(total_m)} stop{plural}{where}.",
        ]
        out = [leads[rot % len(leads)]]
        if landmarks:
            frag = _landmark_frag(landmarks[0][0], with_count=False)
            if frag:
                out.append(f"Still, time for {frag}.")
        return " ".join(out)

    # ── 1. Lead — the geographic shape of the month ──────────────────
    kind = "flat"
    if len(sig_cities) >= 3 or (multi_city and len(sig_cities) >= 2 and new_countries_m):
        kind = "roam"
    elif multi_city and second_c[0]:
        kind = "split"
    elif top_c[0]:
        kind = "home"

    if kind == "roam":
        # Name the significant cities in the order they were first visited.
        sig_set = set(sig_cities[:4])
        order: list[str] = []
        for r in sorted(rows_m, key=lambda r: int(r.get("date") or 0)):
            c = (r.get("city") or "").strip()
            if c in sig_set and c not in order:
                order.append(c)
        cl = [_strong(c) for c in (order or sig_cities[:4])]
        k = len(sig_cities)
        if len(cl) >= 3 and rot % 3 == 0:
            lead = (f"{mon_name} belonged to the road — {cl[0]} first, then {cl[1]}, "
                    f"then {cl[2]}" + (", and onward" if k > 3 else "") + ".")
        elif len(cl) >= 2 and rot % 3 == 1:
            lead = (f"A month in motion — {_nword(k)} cities, running from {cl[0]} "
                    f"through to {cl[-1]}.")
        else:
            named = cl[:3]
            listed = (", ".join(named) + " and more" if k > len(named)
                      else _oxford(named))
            lead = (f"The map hardly sat still: {listed} — "
                    f"{_nword(k)} cities in a single month.")
    elif kind == "split":
        a, b2 = _strong(top_c[0]), _strong(second_c[0])
        pool = [
            f"The month ran on two rails — {a} on one, {b2} on the other.",
            f"{a} and {b2} split {mon_name} between them.",
            f"Two home bases this time: {a} and {b2}.",
            f"{a} took the larger share of the month, {b2} much of the rest.",
        ]
        lead = pool[rot % len(pool)]
    elif kind == "home":
        ch = _strong(top_c[0])
        pool = [
            f"{ch} held the month together.",
            f"A {ch} month, wall to wall.",
            f"{ch} set the pace from the first day to the last.",
            f"{mon_name} ran on {ch} time.",
        ]
        lead = pool[rot % len(pool)]
    else:
        lead = f"{mon_name}, logged on the move."

    # ── 2. Rhythm or anchor (one, not both) ──────────────────────────
    rhythm_s = ""
    top_v = mon_venue_m.most_common(1)[0] if mon_venue_m else ("", 0)
    anchor = top_v if (top_v[0] and top_v[1] >= 10) else None
    routine = ""
    for b, _n in routines:
        if b == "travel":
            continue  # the journey sentence already covers being on the road
        if kind == "roam" and b in ("shopping", "work"):
            continue  # "grocery loops" reads absurd against a four-city month
        routine = b
        break
    use_anchor = anchor is not None and (not routine or rot % 2 == 0)
    if use_anchor and anchor:
        vs, vn = _strong(anchor[0]), anchor[1]
        pool = [
            f"{vs} was practically a second address — {vn} visits.",
            f"The log keeps circling back to {vs}: {_nword(vn)} check-ins.",
            f"{vs}, again and again — {_nword(vn)} times this month.",
        ]
        rhythm_s = pool[rot % len(pool)]
    elif routine:
        ph = ACTIVITY_BUCKETS[routine]["phrases"]
        p = ph[rot % len(ph)]
        pool = [
            f"The rhythm underneath: {p}.",
            _cap_html(f"{p}, mostly."),
            f"Day to day, it was {p}.",
        ]
        rhythm_s = pool[rot % len(pool)]

    # ── 3. Landmarks — named one-off events ──────────────────────────
    lm_s = ""
    lm_frags = []
    for i, (b, _n) in enumerate(landmarks[:2]):
        frag = _landmark_frag(b, with_count=(i == 0))
        if frag:
            lm_frags.append(frag)
    if len(lm_frags) == 2:
        pool = [
            f"There was time for {lm_frags[0]}, and for {lm_frags[1]}.",
            f"The standouts: {lm_frags[0]}; also {lm_frags[1]}.",
            _cap_html(f"{lm_frags[0]} — plus {lm_frags[1]}."),
        ]
        lm_s = pool[rot % len(pool)]
    elif lm_frags:
        pool = [
            f"There was time for {lm_frags[0]}.",
            f"The standout: {lm_frags[0]}.",
            _cap_html(f"{lm_frags[0]}."),
        ]
        lm_s = pool[rot % len(pool)]

    # ── 4. Journey — trips and first-time countries ──────────────────
    def _trip_phase(t: dict) -> tuple[str, str]:
        try:
            day = int((t.get("start_date") or "").split("-")[2])
        except (ValueError, IndexError):
            return "Mid-month", "mid-month"
        if day <= 10:
            return "Early in the month", "early in the month"
        if day <= 20:
            return "Mid-month", "mid-month"
        return "Late in the month", "late in the month"

    def _trip_days(t: dict) -> int:
        try:
            sd = datetime.strptime(t.get("start_date") or "", "%Y-%m-%d")
            ed = datetime.strptime(t.get("end_date") or "", "%Y-%m-%d")
            return (ed - sd).days + 1
        except ValueError:
            return 0

    journey_s = ""
    if trips_starting:
        if len(trips_starting) == 1:
            t0 = trips_starting[0]
            name0 = _strong(t0.get("name") or "trip")
            cap, low = _trip_phase(t0)
            dur = _trip_days(t0)
            dtxt = f" — {_nword(dur)} days door to door" if dur >= 3 else ""
            pool = [
                f"{cap}, the {name0} trip pulled everything onto the road{dtxt}.",
                f"The {name0} trip departed {low}{dtxt}.",
                f"{cap}, a packed bag again: {name0}{dtxt}.",
            ]
            journey_s = pool[rot % len(pool)]
        else:
            names = [_strong(t.get("name") or "trip") for t in trips_starting[:2]]
            more = ", and more besides" if len(trips_starting) > 2 else ""
            pool = [
                f"{_nword(len(trips_starting))} separate departures — "
                f"{names[0]}, then {names[1]}{more}.",
                f"The suitcase never quite got put away: {names[0]}, "
                f"then {names[1]}{more}.",
            ]
            journey_s = _cap_html(pool[rot % len(pool)])
    elif trips_ending:
        name0 = _strong(trips_ending[0].get("name") or "trip")
        pool = [
            f"The month opened on the road, closing out {name0}.",
            f"{name0} wound down in the month's first days.",
        ]
        journey_s = pool[rot % len(pool)]

    ctry_s = ""
    ncs = new_countries_m or []
    if len(ncs) == 1:
        o = (country_ordinals or {}).get(ncs[0])
        otxt = f" — country № {o}" if o else ""
        pool = [
            f"{_strong(ncs[0])} went on the map for the first time{otxt}.",
            f"A first-ever stamp: {_strong(ncs[0])}{otxt}.",
        ]
        ctry_s = pool[rot % len(pool)]
    elif ncs:
        cs = _oxford([_strong(c) for c in ncs[:3]])
        if len(ncs) > 3:
            ctry_s = (f"{_nword(len(ncs)).capitalize()} new flags in a single "
                      f"month — {cs} among them.")
        else:
            word = "both" if len(ncs) == 2 else "all"
            ctry_s = (f"{cs} {word} joined the map for the first time — "
                      f"{_nword(len(ncs))} new flags in one month.")

    # ── 5. Texture — at most two quick human details ─────────────────
    tex: list[str] = []
    top_comp = comp_m.most_common(1)[0] if comp_m else ("", 0)
    if top_comp[0] and top_comp[1] >= 4:
        cn = _strong(top_comp[0])
        if top_comp[1] >= 20 or top_comp[1] / total_m >= 0.25:
            pool = [
                f"{cn} was there for most of it",
                f"more often than not, {cn} was in the frame",
                f"most days had {cn} in them",
            ]
        else:
            pool = [
                f"{cn} drifts in and out of the entries",
                f"{cn} shows up more than anyone else",
                f"the name beside mine most often: {cn}",
            ]
        tex.append(pool[rot % len(pool)])
    new_v_n = len(new_venues_m or [])
    if new_v_n >= 150:
        pool = [
            f"{new_v_n:,} of the month's stops were brand-new ground",
            f"{new_v_n:,} places entered the log for the first time",
            f"the map gained {new_v_n:,} places it had never seen",
        ]
        tex.append(pool[rot % len(pool)])
    if len(tex) < 2 and photos_count_m >= 300:
        pool = [
            f"the camera barely rested — {photos_count_m:,} photos made the cut",
            f"{photos_count_m:,} photos survived the month",
        ]
        tex.append(pool[rot % len(pool)])
    busiest = day_counter.most_common(1)[0] if day_counter else (0, 0)
    avg_day = total_m / max(days_active, 1)
    if len(tex) < 2 and total_m >= 40 and busiest[1] >= max(15, int(2.5 * avg_day)):
        tex.append(f"the {_dayord(busiest[0])} alone stacked up {busiest[1]} stops")
    if len(tex) < 2 and total_m >= 40 and weekend_n / total_m >= 0.5:
        tex.append("weekends did the heavy lifting")
    if len(tex) < 2 and 0 < days_active <= 8:
        tex.append(f"only {_nword(days_active)} days made it into the log")
    if len(tex) < 2:
        mp = _mood_phrase(_mood_score(shouts_m), yr, mo)
        if mp:
            tex.append(mp)
    tex_s = _cap_html("; ".join(tex[:2]) + ".") if tex else ""

    # ── Assemble (max five sentences; the rhythm line gives way first) ─
    out = [lead]
    for s in (rhythm_s, lm_s, journey_s, ctry_s, tex_s):
        if s:
            out.append(s)
    if len(out) > 5 and rhythm_s in out:
        out.remove(rhythm_s)
    return " ".join(out[:5])


def _pick_month_photo(yr: int, mo: int,
                      photos_by_yr_mo: dict[tuple[int, int], list[dict]],
                      used: set[str],
                      months_full: list[str]) -> tuple[dict | None, str]:
    """Pick a photo for month `mo`, avoiding ones already shown in this
    year's timeline.  Falls back to the month's own first photo if all
    are taken, then walks outward through neighbours.  Returns (photo,
    label) — label is '' for own-month, 'from {month}' for borrowed."""
    own = photos_by_yr_mo.get((yr, mo), [])
    # 1) Prefer an unused own-month photo
    for p in own:
        if p["src"] not in used:
            used.add(p["src"])
            return p, ""
    # 2) Borrow an unused photo from the nearest neighbour
    for delta in range(1, 12):
        for d in (-delta, delta):
            cm = mo + d
            if cm < 1 or cm > 12:
                continue
            for p in photos_by_yr_mo.get((yr, cm), []):
                if p["src"] not in used:
                    used.add(p["src"])
                    return p, f"from {months_full[cm - 1]}"
    # 3) All photos used — fall through to first own/neighbour without
    #    marking used (better duplicate than empty)
    if own:
        return own[0], ""
    for delta in range(1, 12):
        for d in (-delta, delta):
            cm = mo + d
            if 1 <= cm <= 12 and photos_by_yr_mo.get((yr, cm)):
                return photos_by_yr_mo[(yr, cm)][0], f"from {months_full[cm - 1]}"
    return None, ""


# Kept for back-compat; thin wrapper around _pick_month_photo without
# de-duplication.  Year-page rendering uses _pick_month_photo directly.
def _find_nearest_photo(yr: int, mo: int,
                        photos_by_yr_mo: dict[tuple[int, int], list[dict]],
                        months_full: list[str]) -> tuple[dict | None, str]:
    return _pick_month_photo(yr, mo, photos_by_yr_mo, set(), months_full)


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<script>document.documentElement.classList.add('js');</script>
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="https://4sq.pages.dev/year-{year}.html">
<meta property="og:type" content="article">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
{og_image}
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
.hero{{position:relative;height:100vh;height:100svh;min-height:580px;overflow:hidden;display:flex;align-items:center;justify-content:center;z-index:5;}}
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
.section{{padding:120px 28px;max-width:1200px;margin:0 auto;transition:opacity 1.1s cubic-bezier(.16,1,.3,1),transform 1.1s cubic-bezier(.16,1,.3,1);position:relative;z-index:5;}}
/* Reveal-on-scroll hidden state only when JS is running (html.js set inline in <head>) —
   without it, no-JS/reader/crawler loads would render every section invisible. */
html.js .section:not(.in){{opacity:0;transform:translateY(50px);}}
.section.in{{opacity:1;transform:translateY(0);}}
.section-h{{font-family:'DM Mono',monospace;font-size:.6rem;text-transform:uppercase;letter-spacing:.22em;color:var(--gold);margin-bottom:8px;}}
.section-title{{font-family:'Playfair Display',serif;font-size:clamp(1.9rem,3.6vw,3.2rem);font-weight:700;color:var(--text);margin-bottom:34px;line-height:1.1;letter-spacing:-0.015em;}}
.section-title em{{font-style:normal;color:var(--gold);font-weight:500;}}
.section-intro{{font-family:'Playfair Display',serif;font-size:1.08rem;color:#cdd5f0;max-width:720px;margin-bottom:36px;line-height:1.75;font-style:italic;font-weight:400;}}
.section-intro strong{{color:var(--gold);font-weight:500;font-style:normal;}}
@media(max-width:600px){{.section{{padding:70px 18px;}}.section-title{{font-size:1.65rem;margin-bottom:24px;}}.sticky-strip{{padding:10px 16px;}}.hero-content{{padding:24px 18px;}}.hero-yr{{font-size:clamp(4.4rem,24vw,7rem);}}}}

/* ── KPI tiles ── */
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:14px;}}
.kpi{{background:rgba(18,21,31,0.78);backdrop-filter:blur(6px);border:1px solid var(--border);border-radius:14px;padding:20px 18px;text-align:center;position:relative;overflow:hidden;}}
.kpi::after{{content:'';position:absolute;top:0;left:18px;right:18px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:.4;}}
.kpi-num{{font-family:'Playfair Display',serif;font-size:2.3rem;font-weight:700;color:var(--gold);line-height:1;}}
.kpi-lbl{{font-family:'DM Mono',monospace;font-size:.55rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);margin-top:7px;}}
.kpi-sub{{font-size:.66rem;color:var(--text);margin-top:5px;opacity:.7;}}

/* ── The year in sound (Last.fm) ── */
.music-hero{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:34px;}}
.music-artist{{font-family:'Playfair Display',serif;font-size:1.55rem;font-weight:700;color:var(--teal);line-height:1.12;overflow-wrap:anywhere;}}
.music-months{{display:flex;align-items:flex-end;gap:5px;height:120px;margin-top:8px;}}
.music-months .mm-col{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:7px;height:100%;}}
.music-months .mm-fill{{width:100%;max-width:26px;background:linear-gradient(180deg,var(--teal),rgba(78,201,176,.22));border-radius:4px 4px 0 0;min-height:2px;}}
.music-months .mm-lbl{{font-family:'DM Mono',monospace;font-size:.5rem;color:var(--muted);letter-spacing:.05em;text-transform:uppercase;}}

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
.mo{{display:grid;grid-template-columns:1fr 1fr;gap:30px;align-items:center;padding:14px 0;transition:opacity 1s cubic-bezier(.16,1,.3,1),transform 1s cubic-bezier(.16,1,.3,1);}}
html.js .mo:not(.in){{opacity:0;transform:translateY(40px);}}
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
{music_section}

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
document.querySelectorAll('.section, .mo').forEach(s => {{
  sObs.observe(s);
  // Anchored / restored-scroll loads: elements already above the viewport never
  // intersect, so reveal them immediately instead of leaving them invisible.
  if (s.getBoundingClientRect().bottom < 0) s.classList.add('in');
}});

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
    music_by_year: dict | None = None,
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

    # First-seen month per venue across ENTIRE history — used to detect
    # "new venues" within a given month for narrative flavour.
    venue_first_seen_yr_mo: dict[str, tuple[int, int]] = {}
    country_first_seen_yr_mo: dict[str, tuple[int, int]] = {}
    for r in sorted(rows, key=lambda r: int(r.get("date") or 0)):
        try:
            ts_r = int(r.get("date") or 0)
        except ValueError:
            continue
        if not ts_r:
            continue
        d_r = datetime.fromtimestamp(ts_r, tz=timezone.utc)
        ym = (d_r.year, d_r.month)
        vid = (r.get("venue_id") or r.get("venue") or "").strip()
        if vid and vid not in venue_first_seen_yr_mo:
            venue_first_seen_yr_mo[vid] = ym
        country = (r.get("country") or "").strip()
        if country and country not in country_first_seen_yr_mo:
            country_first_seen_yr_mo[country] = ym
    # Rows were iterated in ts order, so insertion order IS discovery order —
    # position N means "the Nth country ever visited".
    country_ordinal = {c: i + 1 for i, c in enumerate(country_first_seen_yr_mo)}

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

    # Stable per-year cover photo (shared with the years index; deterministic
    # signature score + config/year_covers.json override). Used as the lead
    # frame of the hero Ken-Burns cycle and the og:image.
    covers = select_year_covers(rows, photos_by_checkin, config_dir, pix_url=pix_url)

    # Per-month pins from the same config file: "YYYY-MM" → filename or
    # checkin_id (resolved here against the photo index), "YYYY-MM-note" →
    # hand-written text that replaces the auto narrative.
    month_notes = load_month_notes(config_dir)
    month_cover_ov: dict[tuple[int, int], dict] = {}
    raw_month_ov = load_month_cover_overrides(config_dir)
    if raw_month_ov:
        fname_entry: dict[str, dict] = {}
        cid_entry: dict[str, dict] = {}
        for entries in photos_by_yr_mo.values():
            for e in entries:
                fname_entry.setdefault(e["src"].rsplit("/", 1)[-1], e)
                cid_entry.setdefault(e["checkin_id"], e)
        for ym, val in raw_month_ov.items():
            ent = fname_entry.get(val) or cid_entry.get(val)
            if ent:
                month_cover_ov[ym] = ent
            else:
                print(f"  ! year_covers.json: month cover {ym[0]}-{ym[1]:02d} "
                      f"value {val!r} matches no photo filename or checkin_id — ignored")

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
        mon_comp: dict[int, Counter] = defaultdict(Counter)
        mon_days: dict[int, set] = defaultdict(set)
        mon_new_venues: dict[int, list[tuple[str, str]]] = defaultdict(list)
        mon_new_countries: dict[int, list[str]] = defaultdict(list)
        try:
            from metrics import collect_companions as _cc
        except ImportError:
            def _cc(row: dict) -> list[str]:
                return []
        for r in rows_y:
            vid = (r.get("venue_id") or "").strip()
            vn = (r.get("venue") or "").strip()
            vkey = vid or vn
            if vid and vn:
                ven_y[(vid, vn)] += 1
            cy = (r.get("city") or "").strip()
            ct = (r.get("category") or "").strip()
            country = (r.get("country") or "").strip()
            if cy: cty_y[cy] += 1
            if ct: cat_y[ct] += 1
            comps = _cc(r)
            for n in comps:
                comp_y[n] += 1
            try:
                ts_y = int(r.get("date", 0) or 0)
                if ts_y:
                    d_y = datetime.fromtimestamp(ts_y, tz=timezone.utc)
                    mo_y = d_y.month
                    mon_y_counter[mo_y] += 1
                    mon_days[mo_y].add(d_y.date())
                    if vn: mon_venue[mo_y][vn] += 1
                    if cy: mon_city[mo_y][cy] += 1
                    if ct: mon_cat[mo_y][ct] += 1
                    for n in comps:
                        mon_comp[mo_y][n] += 1
                    # New venue / country first-seen?
                    if vkey and venue_first_seen_yr_mo.get(vkey) == (yr, mo_y):
                        # Only record once per venue per month
                        if not any(v[0] == vkey for v in mon_new_venues[mo_y]):
                            mon_new_venues[mo_y].append((vkey, vn))
                    if country and country_first_seen_yr_mo.get(country) == (yr, mo_y):
                        if country not in mon_new_countries[mo_y]:
                            mon_new_countries[mo_y].append(country)
            except ValueError:
                pass

        # Trip start/end month indexes
        trip_starts_by_mo: dict[int, list[dict]] = defaultdict(list)
        trip_ends_by_mo:   dict[int, list[dict]] = defaultdict(list)
        for t in trips_y:
            try:
                sdate = t.get("start_date") or ""
                if sdate:
                    sm = int(sdate.split("-")[1])
                    trip_starts_by_mo[sm].append(t)
            except (ValueError, IndexError):
                continue
        # Ends scan ALL trips, not just this year's starts — a trip can
        # straddle the year boundary — and must match the end YEAR too.
        for t in trips:
            try:
                edate = t.get("end_date") or ""
                if edate:
                    ey, em = int(edate.split("-")[0]), int(edate.split("-")[1])
                    if ey == yr:
                        trip_ends_by_mo[em].append(t)
            except (ValueError, IndexError):
                continue

        # HERO Ken-Burns photos: the STABLE cover leads, then up to 4 more
        # recent shots. Leading with the cover keeps the first frame (and the
        # og:image) fixed build-to-build instead of chasing the newest photo.
        cover = covers.get(yr)
        cover_src = cover["src"] if cover else ""
        hero_photos = list(photos_y[:5])
        if cover_src:
            hero_photos = [p for p in hero_photos if p["src"] != cover_src]
            hero_photos.insert(0, {"src": cover_src})
            hero_photos = hero_photos[:5]
        hero_bg_html = "".join(
            f'<div class="ph" style="background-image:url(\'{_esc(p["src"])}\')"></div>'
            for p in hero_photos
        )

        # Scrolling background photos (one per non-hero section, picked spaced
        # out from the year's photos so each section has a different feel)
        section_count = 11  # kpi, months, top, newc, trips, flights, photos, shouts, comp, music
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

        # Track which photo URLs have been used for this year's timeline
        # so no two months show the same image (incl. borrowed photos).
        # Pinned covers are claimed up front so the auto-picker in an earlier
        # month can't consume a photo the config reserves for a later one.
        used_photos_this_year: set[str] = set()
        for mo_ov in range(1, 13):
            ent = month_cover_ov.get((yr, mo_ov))
            if ent:
                used_photos_this_year.add(ent["src"])

        mo_rows_html: list[str] = []
        for mo in range(1, 13):
            n = mon_y_counter.get(mo, 0)
            mo_name = months_full[mo - 1]
            mo_num = f"{mo:02d}"
            mo_shouts = shouts_by_yr_mo.get((yr, mo), [])

            if n == 0:
                # Empty month — never borrow a photo from a neighbouring
                # month (a borrowed picture reads as if something happened).
                # Future months get a "to be updated" card; past empty
                # months an honest quiet card.
                now_utc = datetime.now(timezone.utc)
                is_future = (yr, mo) > (now_utc.year, now_utc.month)
                if is_future:
                    photo_html = '<div class="mo-photo-empty">To be updated</div>'
                    count_txt = "Not yet"
                    narr_txt = "This month hasn't happened yet — the page will fill in as check-ins land."
                else:
                    photo_html = f'<div class="mo-photo-empty">Quiet · {mo_name}</div>'
                    count_txt = "No check-ins logged"
                    narr_txt = "A month off the map — the calendar quietly empty."
                # Explicit config pins still win on empty months.
                ent = month_cover_ov.get((yr, mo))
                if ent:
                    photo_html = (
                        f'<div class="mo-photo" style="background-image:url(\'{_esc(ent["src"])}\')"></div>'
                    )
                note = month_notes.get((yr, mo))
                if note:
                    narr_txt = _esc(note)
                mo_rows_html.append(
                    f'<div class="mo empty" id="mo-{mo_num}">'
                    f'{photo_html}'
                    f'<div class="mo-text-box">'
                    f'<div class="mo-num">{mo_num}</div>'
                    f'<div class="mo-name">{mo_name}</div>'
                    f'<div class="mo-count">{count_txt}</div>'
                    f'<div class="mo-narrative">{narr_txt}</div>'
                    f'</div></div>'
                )
                continue

            pinned = month_cover_ov.get((yr, mo))
            if pinned:
                picked, tag = pinned, ""
            else:
                picked, tag = _pick_month_photo(
                    yr, mo, photos_by_yr_mo, used_photos_this_year, months_full,
                )
            if picked and not tag:
                photo_html = (
                    f'<div class="mo-photo" style="background-image:url(\'{_esc(picked["src"])}\')"></div>'
                )
            elif picked:
                photo_html = (
                    f'<div class="mo-photo borrowed" style="background-image:url(\'{_esc(picked["src"])}\')">'
                    f'<div class="mo-photo-tag">↺ {_esc(tag)}</div>'
                    f'</div>'
                )
            else:
                photo_html = f'<div class="mo-photo-empty">{mo_name}</div>'

            note = month_notes.get((yr, mo))
            if note:
                # Hand-written note from year_covers.json replaces the
                # auto-generated narrative wholesale.
                mo_narr = _esc(note)
            else:
                mo_narr = _compose_month_narrative(
                    yr, mo, rows_by_mo[mo],
                    mon_venue[mo], mon_city[mo], mon_cat[mo], mo_shouts,
                    comp_m=mon_comp[mo],
                    days_active=len(mon_days[mo]),
                    new_venues_m=mon_new_venues[mo],
                    trips_starting=trip_starts_by_mo.get(mo, []),
                    trips_ending=trip_ends_by_mo.get(mo, []),
                    photos_count_m=len(photos_by_yr_mo.get((yr, mo), [])),
                    new_countries_m=mon_new_countries[mo],
                    country_ordinals=country_ordinal,
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

        # ── The year in sound (Last.fm scrobbles) ──
        mys = (music_by_year or {}).get(str(yr)) or {}
        if mys.get("scrobbles"):
            m_total = int(mys.get("scrobbles", 0))
            top_a = mys.get("top_artist") or {}
            a_name = (top_a.get("name") or "").strip()
            a_plays = int(top_a.get("plays", 0) or 0)
            tiles = (
                f'<div class="kpi"><div class="kpi-num" data-count="{m_total}">0</div>'
                f'<div class="kpi-lbl">Scrobbles</div>'
                f'<div class="kpi-sub">tracks played</div></div>'
            )
            if a_name:
                tiles += (
                    f'<div class="kpi"><div class="kpi-num music-artist">{_esc(a_name)}</div>'
                    f'<div class="kpi-lbl">Most-played artist</div>'
                    f'<div class="kpi-sub">{a_plays:,} play{"s" if a_plays != 1 else ""}</div></div>'
                )
            months = mys.get("months") or [0] * 12
            m_max = max(months) if months else 0
            m_labels = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]
            if m_max > 0:
                bars = "".join(
                    f'<div class="mm-col"><div class="mm-fill" '
                    f'style="height:{max(2, round(c / m_max * 100))}%"></div>'
                    f'<div class="mm-lbl">{m_labels[i]}</div></div>'
                    for i, c in enumerate(months)
                )
                months_html = f'<div class="music-months">{bars}</div>'
            else:
                months_html = ""
            lead = (
                f'{a_name} led the rotation. ' if a_name else ''
            )
            music_section = (
                f'<section class="section" id="sec-music" data-bg-index="10" data-sec-title="The Year in Sound">'
                f'<div class="section-h">The year in sound</div>'
                f'<div class="section-title">{m_total:,} scrobble{"s" if m_total != 1 else ""} <em>across {yr}</em></div>'
                f'<div class="section-intro">The private radio behind the check-ins. {lead}Every track logged to Last.fm.</div>'
                f'<div class="music-hero">{tiles}</div>'
                f'{months_html}'
                f'</section>'
            )
        else:
            music_section = ""

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
        # og:image only when the cover resolves to an absolute URL (i.e. a real
        # build with --pix-url); a bare local filename wouldn't resolve for crawlers.
        og_image = (
            f'<meta property="og:image" content="{_esc(cover_src)}">'
            if cover_src.startswith("http") else ""
        )
        html = PAGE_TEMPLATE.format(
            year=yr,
            title=f"{yr} — Year in Review",
            description=description,
            og_image=og_image,
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
            music_section=music_section,
            year_nav=year_nav,
        )
        (out_dir / f"year-{yr}.html").write_text(html, encoding="utf-8")

    print(f"year-YYYY.html -> {out_dir}  ({len(year_summaries)} pages)")
