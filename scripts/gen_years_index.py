#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_years_index.py — Generate years.html, the index of year cards
(one click → year-YYYY.html). Replaces the "Year in Review" card that
used to live on stats.html.

Hero photo is picked from the latest year that has photos. Cards are
sorted newest-first with the vivid narrative front and centre.
"""

from collections import Counter
from pathlib import Path

from year_covers import select_year_covers


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    word = singular if n == 1 else (plural or singular + "s")
    return f"{n:,} {word}"


def life_narrative(year_summaries: list[dict]) -> str:
    """Compose the cross-year "summary of my life" lede for the /years hero.

    Warm-memoir voice: first-person, reflective, understated. Every clause is
    a pure function of the aggregated year summaries, so it is deterministic
    across builds and grows on its own as new years are appended. Clauses that
    lack data drop out silently, so early/sparse datasets still read cleanly.
    Gold <strong> spans (matching the year cards) carry the few real numbers.
    """
    ys = [y for y in year_summaries if y.get("total")]
    if not ys:
        return ""
    ys.sort(key=lambda y: y["year"])
    first_year, last_year = ys[0]["year"], ys[-1]["year"]
    n_years = len(ys)
    grand_total = sum(y.get("total", 0) for y in ys)
    # new_* count first-time places per year, so their sum is the lifetime distinct total.
    n_countries = sum(y.get("new_countries", 0) for y in ys)
    n_cities = sum(y.get("new_cities", 0) for y in ys)
    total_km = sum(y.get("distance_km", 0) for y in ys)

    # The place whose name headlines the most years — "where I kept coming back".
    city_years: Counter = Counter()
    city_weight: dict[str, int] = {}
    for y in ys:
        c = (y.get("top_city") or "").strip()
        if c:
            city_years[c] += 1
            city_weight[c] = city_weight.get(c, 0) + y.get("top_city_n", 0)
    recurring_city, recurring_n = "", 0
    if city_years:
        recurring_city, recurring_n = max(
            city_years.items(), key=lambda kv: (kv[1], city_weight.get(kv[0], 0), kv[0])
        )

    # A companion who threads through several years.
    comp_years: Counter = Counter()
    for y in ys:
        who = (y.get("top_companion") or "").strip()
        if who:
            comp_years[who] += 1
    companion, companion_n = "", 0
    if comp_years:
        companion, companion_n = max(comp_years.items(), key=lambda kv: (kv[1], kv[0]))

    # Farthest single reach across all years.
    far_city, far_km = "", 0
    for y in ys:
        if y.get("farthest_km", 0) > far_km:
            far_km, far_city = y["farthest_km"], (y.get("farthest_city") or "").strip()

    def g(text: str) -> str:
        return f'<strong>{text}</strong>'

    parts: list[str] = []
    # S1 — span + scale.
    if n_years == 1:
        parts.append(
            f"It's just {g(str(first_year))} so far — {g(_plural(grand_total, 'check-in'))} "
            f"and the start of a habit I never quite meant to keep.")
    else:
        parts.append(
            f"{g(f'{first_year}–{last_year}')} — {n_years} years of checking in, "
            f"{g(f'{grand_total:,}')} times over, a habit I never quite meant to keep "
            f"and never managed to break.")
    # S2 — reach.
    reach = []
    if n_countries:
        reach.append(g(_plural(n_countries, "country", "countries")))
    if n_cities:
        reach.append(g(_plural(n_cities, "city", "cities")))
    if reach:
        tail = (f", and something like {g(f'{total_km:,} km')} of getting there"
                if total_km >= 1000 else "")
        parts.append("They add up to " + " and ".join(reach) + tail + ".")
    # S3 — the recurring anchor.
    if recurring_city and (recurring_n >= 2 or n_years == 1):
        parts.append(
            f"{g(recurring_city)} is the name that keeps recurring — the place I kept "
            f"coming back to between everywhere else.")
    # S4 — the people.
    if companion and companion_n >= 2:
        parts.append(
            f"{g(companion)} turns up across a lot of these years; the best of them usually did.")
    # S5 — farthest reach.
    if far_city and far_km >= 1000:
        parts.append(
            f"The farthest I ever got was {g(far_city)}, about {far_km:,} km from home.")
    # S6 — the invitation.
    parts.append("Each card below is one year of it. Start wherever you like.")
    return " ".join(parts)


def build_page(csv_path, config_dir, out_path, tmpl_path=None,
               stats_data=None, photos_by_checkin=None, rows=None,
               pix_url="", **_extra) -> None:
    stats_data = stats_data or {}
    photos_by_checkin = photos_by_checkin or {}
    rows = rows or []
    year_summaries = stats_data.get("year_summaries", [])

    if not year_summaries:
        Path(out_path).write_text(
            "<!doctype html><meta charset=utf-8><title>Years</title>"
            "<style>body{background:#0a0c12;color:#7a85a8;font-family:sans-serif;padding:48px;text-align:center;}</style>"
            "<h1>No year data</h1>",
            encoding="utf-8",
        )
        return

    # One STABLE cover photo per year (deterministic signature score, config
    # override via config/year_covers.json) — shared with the per-year album
    # pages so the same image fronts both. See scripts/year_covers.py.
    covers = select_year_covers(rows, photos_by_checkin, config_dir, pix_url=pix_url)
    photo_by_year_flat: dict[int, str] = {yr: c["src"] for yr, c in covers.items()}

    # Hero: most recent year's photo
    sorted_ys = sorted(year_summaries, key=lambda y: -y["year"])
    hero_photo = ""
    for ys in sorted_ys:
        if ys["year"] in photo_by_year_flat:
            hero_photo = photo_by_year_flat[ys["year"]]
            break

    # Card HTML
    card_html_parts: list[str] = []
    for ys in sorted_ys:
        yr = ys["year"]
        photo = photo_by_year_flat.get(yr, "")
        thumb_html = (
            f'<div class="yc-thumb" style="background-image:url(\'{photo}\')"></div>'
            if photo else '<div class="yc-thumb yc-thumb-empty"></div>'
        )
        stat_row = (
            f"<span><strong>{ys['total']:,}</strong> check-ins</span>"
            f"<span><strong>{ys['cities']}</strong> cities</span>"
            f"<span><strong>{ys['countries']}</strong> countries</span>"
            + (f"<span class='new'>+{ys['new_countries']} new</span>" if ys.get('new_countries') else '')
        )
        card_html_parts.append(
            f'<a class="yc" href="year-{yr}.html">'
            f'{thumb_html}'
            f'<div class="yc-body">'
            f'<div class="yc-year">{yr}</div>'
            f'<div class="yc-stats">{stat_row}</div>'
            f'<div class="yc-vivid">{ys.get("vivid", "")}</div>'
            f'<div class="yc-cta">Open <span class="arrow">→</span></div>'
            f'</div></a>'
        )

    # Cross-year "summary of my life" lede — grows on its own as years accrue.
    life_html = life_narrative(year_summaries)

    html = HTML.format(
        hero_photo=hero_photo,
        total_years=len(year_summaries),
        life_html=life_html,
        cards_html="".join(card_html_parts),
    )
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"years.html -> {out_path}  ({len(year_summaries)} years, {len(html)//1024} KB)")


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Years — Check-in Journal</title>
<meta name="description" content="A year-by-year album of every place you've been, every flight you've taken, every photo you've kept.">
<link rel="canonical" href="https://4sq.pages.dev/years.html">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=DM+Sans:wght@400;500;600&family=DM+Mono&display=swap" rel="stylesheet">
<style>
:root{{--bg:#0a0c12;--card:#12141c;--card2:#181a24;--border:#222738;--text:#e0e2ec;--muted:#7a85a8;--gold:#e8b86d;--teal:#4ec9b0;}}
body.light{{--bg:#e9e4dc;--card:#f0ece3;--card2:#e5e0d7;--border:#aaa49a;--gold:#7a3e00;--teal:#155c58;--muted:#54576a;--text:#15172a;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
html{{scroll-behavior:smooth;}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.55;}}
a{{color:var(--teal);}}

.crumb{{position:fixed;top:14px;left:18px;z-index:200;font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);background:rgba(18,21,31,.85);backdrop-filter:blur(8px);border:1px solid var(--border);padding:6px 12px;border-radius:6px;text-decoration:none;}}
.crumb:hover{{color:var(--gold);}}

/* Hero */
.hero{{position:relative;height:60vh;min-height:340px;display:flex;align-items:center;justify-content:center;overflow:hidden;border-bottom:1px solid var(--border);}}
.hero-bg{{position:absolute;inset:0;background-size:cover;background-position:center;filter:brightness(.35) contrast(1.05);animation:kb 20s ease-in-out infinite;}}
@keyframes kb{{0%{{transform:scale(1.04);}}50%{{transform:scale(1.16);}}100%{{transform:scale(1.04);}}}}
.hero-overlay{{position:absolute;inset:0;background:radial-gradient(ellipse at center,rgba(10,12,18,.15) 0%,rgba(10,12,18,.92) 80%);}}
.hero-content{{position:relative;text-align:center;padding:24px;max-width:740px;}}
.hero-eyebrow{{font-family:'DM Mono',monospace;font-size:.6rem;text-transform:uppercase;letter-spacing:.22em;color:var(--gold);margin-bottom:14px;}}
.hero-title{{font-family:'Playfair Display',serif;font-size:clamp(2.6rem,6vw,5rem);font-weight:900;letter-spacing:-0.03em;line-height:0.95;background:linear-gradient(140deg,#f5d48a,#e8b86d 50%,#b97c30);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}}
.hero-sub{{font-family:'Playfair Display',serif;font-style:italic;font-size:1.05rem;color:#cdd5f0;margin-top:18px;line-height:1.6;}}

/* Cards */
.grid{{max-width:1280px;margin:0 auto;padding:50px 28px 80px;display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:22px;}}
.yc{{background:var(--card);border:1px solid var(--border);border-radius:16px;overflow:hidden;text-decoration:none;color:var(--text);display:flex;flex-direction:column;transition:transform .25s ease,border-color .25s ease,box-shadow .25s ease;opacity:0;transform:translateY(30px);}}
.yc.in{{opacity:1;transform:translateY(0);transition-duration:.7s;}}
.yc:hover{{transform:translateY(-4px);border-color:rgba(232,184,109,.55);box-shadow:0 14px 40px rgba(0,0,0,.32);}}
.yc-thumb{{aspect-ratio:16/9;background-size:cover;background-position:center;background-color:rgba(232,184,109,.06);position:relative;}}
.yc-thumb::after{{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 50%,rgba(18,20,28,.6) 100%);}}
.yc-thumb-empty{{background:linear-gradient(135deg,rgba(232,184,109,.07),rgba(78,201,176,.05));}}
.yc-body{{padding:20px 22px 22px;display:flex;flex-direction:column;gap:8px;flex:1;}}
.yc-year{{font-family:'Playfair Display',serif;font-size:3rem;font-weight:900;line-height:1;background:linear-gradient(140deg,#f5d48a,#e8b86d 55%,#b97c30);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;letter-spacing:-.02em;}}
.yc-stats{{display:flex;flex-wrap:wrap;gap:10px;font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);letter-spacing:.06em;}}
.yc-stats strong{{color:var(--text);font-weight:600;}}
.yc-stats .new{{color:var(--gold);}}
.yc-vivid{{font-size:.86rem;line-height:1.65;color:var(--text);flex:1;}}
.yc-vivid strong{{color:var(--gold);font-weight:500;}}
.yc-cta{{font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.16em;color:var(--teal);margin-top:auto;padding-top:8px;display:flex;align-items:center;gap:6px;}}
.yc:hover .yc-cta .arrow{{transform:translateX(4px);}}
.yc-cta .arrow{{transition:transform .2s;}}

.side-nav{{position:fixed;right:0;top:50%;transform:translateY(-50%);z-index:4000;display:flex;flex-direction:column;background:rgba(18,21,31,0.92);border:1px solid var(--border);border-right:none;border-radius:10px 0 0 10px;backdrop-filter:blur(10px);overflow:hidden;}}
.side-nav a{{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;width:52px;padding:6px 4px;color:var(--muted);text-decoration:none;}}
.side-nav a.active{{color:var(--gold);}}
.side-nav a:hover{{color:var(--text);background:rgba(255,255,255,.06);}}
.side-nav .sn-icon{{font-size:.82rem;}}
.side-nav .sn-lbl{{font-family:'DM Mono',monospace;font-size:.42rem;text-transform:uppercase;letter-spacing:.05em;}}
@media(max-width:900px){{.side-nav{{display:none;}}}}
@media(max-width:600px){{.hero{{height:46vh;}}.grid{{padding:30px 14px 50px;grid-template-columns:1fr;gap:14px;}}.yc-year{{font-size:2.4rem;}}}}

/* Life lede — the cross-year opening paragraph */
.life{{max-width:760px;margin:0 auto;padding:52px 28px 6px;text-align:center;}}
.life-lede{{font-family:'Playfair Display',serif;font-size:clamp(1.05rem,2.1vw,1.4rem);line-height:1.75;color:var(--text);letter-spacing:-.005em;}}
.life-lede strong{{color:var(--gold);font-weight:700;}}
.life-lede::first-letter{{font-size:2.4em;font-weight:900;float:left;line-height:.8;padding:6px 10px 0 0;color:var(--gold);font-family:'Playfair Display',serif;}}
@media(max-width:600px){{.life{{padding:34px 18px 2px;}}.life-lede::first-letter{{font-size:2em;}}}}
</style>
</head>
<body>
<a class="crumb" href="index.html">← Dashboard</a>

<section class="hero">
  <div class="hero-bg" style="background-image:url('{hero_photo}')"></div>
  <div class="hero-overlay"></div>
  <div class="hero-content">
    <div class="hero-eyebrow">Year in review · {total_years} chapters</div>
    <h1 class="hero-title">All your years, gathered.</h1>
    <p class="hero-sub">Pick a year. Open the album. Let the photos do most of the talking.</p>
  </div>
</section>

<section class="life"><p class="life-lede">{life_html}</p></section>

<div class="grid" id="grid">
{cards_html}
</div>

<nav class="side-nav" aria-label="Page shortcuts">
  <a href="index.html"><span class="sn-icon">🏠</span><span class="sn-lbl">HOME</span></a>
  <a href="feed.html"><span class="sn-icon">📰</span><span class="sn-lbl">FEED</span></a>
  <a href="flights.html"><span class="sn-icon">✈</span><span class="sn-lbl">FLIGHTS</span></a>
  <a href="trips.html"><span class="sn-icon">🧳</span><span class="sn-lbl">TRIPS</span></a>
  <a href="photos.html"><span class="sn-icon">📷</span><span class="sn-lbl">PHOTOS</span></a>
  <a href="stats.html"><span class="sn-icon">📊</span><span class="sn-lbl">STATS</span></a>
  <a href="years.html" class="active"><span class="sn-icon">🗓️</span><span class="sn-lbl">YEARS</span></a>
</nav>

<script>
// Stagger reveal
const cards = document.querySelectorAll('.yc');
const obs = new IntersectionObserver(es => es.forEach((e, i) => {{
  if (!e.isIntersecting) return;
  setTimeout(() => e.target.classList.add('in'), i * 60);
  obs.unobserve(e.target);
}}), {{ threshold: 0.08 }});
cards.forEach(c => obs.observe(c));
(function(){{document.body.classList.toggle('light', localStorage.getItem('fsq-theme') === 'light');}})();
</script>
</body>
</html>
"""
