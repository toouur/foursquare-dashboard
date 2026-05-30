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
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

CTRY_CODE: dict[str, str] = {}


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

/* ── Scrolling background photo (fixed, fades in per section) ── */
.scroll-bg{{position:fixed;inset:0;z-index:0;pointer-events:none;}}
.scroll-bg .sbg-img{{position:absolute;inset:0;background-size:cover;background-position:center;opacity:0;transition:opacity 1.2s ease;filter:brightness(.30) contrast(1.05) saturate(.9);}}
.scroll-bg .sbg-img.active{{opacity:1;}}
.scroll-bg::after{{content:'';position:absolute;inset:0;background:linear-gradient(180deg,rgba(10,12,18,0.55) 0%,rgba(10,12,18,0.92) 100%);}}

/* ── Hero ── */
.hero{{position:relative;height:100vh;min-height:580px;overflow:hidden;display:flex;align-items:center;justify-content:center;z-index:5;}}
.hero-bg{{position:absolute;inset:0;z-index:0;}}
.hero-bg .ph{{position:absolute;inset:0;background-size:cover;background-position:center;opacity:0;transition:opacity 1.6s ease;filter:brightness(.45) contrast(1.05);}}
.hero-bg .ph.active{{opacity:1;animation:kb 18s ease-in-out infinite;}}
@keyframes kb{{0%{{transform:scale(1.05);}}50%{{transform:scale(1.18);}}100%{{transform:scale(1.05);}}}}
.hero-overlay{{position:absolute;inset:0;background:radial-gradient(ellipse at center,rgba(10,12,18,0.10) 0%,rgba(10,12,18,0.85) 80%);z-index:1;}}
.hero-content{{position:relative;z-index:2;text-align:center;padding:24px;max-width:820px;}}
.hero-yr{{font-family:'Playfair Display',serif;font-size:clamp(7rem,18vw,18rem);font-weight:900;line-height:0.92;letter-spacing:-0.035em;background:linear-gradient(150deg,#f5d48a 0%,#e8b86d 40%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}}
.hero-vivid{{font-family:'Playfair Display',serif;font-size:clamp(1rem,2.2vw,1.45rem);font-weight:400;color:#f0f1f6;line-height:1.55;margin-top:24px;text-shadow:0 2px 18px rgba(0,0,0,0.55);max-width:740px;margin-left:auto;margin-right:auto;font-style:italic;}}
.hero-vivid strong{{color:var(--gold);font-style:normal;font-weight:500;}}
.hero-scroll{{position:absolute;bottom:28px;left:50%;transform:translateX(-50%);z-index:3;font-family:'DM Mono',monospace;font-size:.55rem;color:var(--muted);letter-spacing:.18em;text-transform:uppercase;animation:bob 2s ease-in-out infinite;}}
@keyframes bob{{0%,100%{{transform:translateX(-50%) translateY(0);}}50%{{transform:translateX(-50%) translateY(6px);}}}}

/* ── Sections ── */
.section{{padding:110px 28px;max-width:1200px;margin:0 auto;opacity:0;transform:translateY(40px);transition:opacity 1s ease,transform 1s ease;position:relative;z-index:5;}}
.section.in{{opacity:1;transform:translateY(0);}}
.section-h{{font-family:'DM Mono',monospace;font-size:.6rem;text-transform:uppercase;letter-spacing:.22em;color:var(--gold);margin-bottom:6px;}}
.section-title{{font-family:'Playfair Display',serif;font-size:clamp(1.7rem,3.2vw,2.8rem);font-weight:700;color:var(--text);margin-bottom:34px;line-height:1.15;letter-spacing:-0.01em;}}
.section-title em{{font-style:normal;color:var(--gold);font-weight:500;}}
.section-intro{{font-size:.95rem;color:#cdd5f0;max-width:680px;margin-bottom:32px;line-height:1.7;}}
.section-intro strong{{color:var(--gold);font-weight:500;}}
@media(max-width:600px){{.section{{padding:60px 18px;}}.section-title{{font-size:1.6rem;margin-bottom:24px;}}}}

/* ── KPI tiles ── */
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:14px;}}
.kpi{{background:rgba(18,21,31,0.78);backdrop-filter:blur(6px);border:1px solid var(--border);border-radius:14px;padding:20px 18px;text-align:center;position:relative;overflow:hidden;}}
.kpi::after{{content:'';position:absolute;top:0;left:18px;right:18px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:.4;}}
.kpi-num{{font-family:'Playfair Display',serif;font-size:2.3rem;font-weight:700;color:var(--gold);line-height:1;}}
.kpi-lbl{{font-family:'DM Mono',monospace;font-size:.55rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);margin-top:7px;}}
.kpi-sub{{font-size:.66rem;color:var(--text);margin-top:5px;opacity:.7;}}

/* ── Month timeline ── */
.months{{display:flex;flex-direction:column;gap:0;border-left:2px solid rgba(232,184,109,.25);padding-left:24px;margin-left:8px;position:relative;}}
.mo{{position:relative;padding:18px 0 24px;}}
.mo::before{{content:'';position:absolute;left:-32px;top:24px;width:14px;height:14px;border-radius:50%;background:var(--card);border:2px solid var(--gold);}}
.mo.empty::before{{border-color:var(--muted);opacity:.5;}}
.mo-name{{font-family:'Playfair Display',serif;font-size:1.4rem;font-weight:700;color:var(--gold);line-height:1.1;}}
.mo-count{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);letter-spacing:.06em;margin-left:8px;font-weight:400;text-transform:uppercase;}}
.mo-body{{display:grid;grid-template-columns:160px 1fr;gap:18px;margin-top:10px;align-items:start;}}
.mo-thumb{{width:160px;height:120px;border-radius:10px;background-size:cover;background-position:center;background-color:rgba(255,255,255,.04);background-blend-mode:luminosity;}}
.mo-thumb-empty{{background:linear-gradient(135deg,rgba(232,184,109,.06),rgba(78,201,176,.04));display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:.6rem;text-transform:uppercase;letter-spacing:.14em;font-family:'DM Mono',monospace;}}
.mo-text{{font-size:.88rem;line-height:1.65;color:var(--text);}}
.mo-text strong{{color:var(--gold);font-weight:500;}}
.mo-shout{{margin-top:10px;padding:10px 14px;border-left:2px solid var(--teal);background:rgba(78,201,176,.05);font-style:italic;font-size:.78rem;color:#cdd5f0;border-radius:0 6px 6px 0;}}
.mo-shout-meta{{font-family:'DM Mono',monospace;font-size:.55rem;color:var(--muted);margin-top:5px;font-style:normal;}}
.mo.empty .mo-name{{color:var(--muted);font-weight:400;}}
.mo.empty .mo-text{{color:var(--muted);font-style:italic;}}
@media(max-width:600px){{.mo-body{{grid-template-columns:1fr;}}.mo-thumb{{width:100%;height:160px;}}}}

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

/* ── Shouts ── */
.shouts{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;}}
.shout-card{{background:rgba(18,21,31,0.78);backdrop-filter:blur(6px);border:1px solid var(--border);border-radius:12px;padding:16px 18px;}}
.shout-text{{font-family:'Playfair Display',serif;font-style:italic;font-size:.94rem;color:var(--text);line-height:1.55;font-weight:400;}}
.shout-meta{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);margin-top:10px;letter-spacing:.04em;}}

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

<a class="crumb" href="index.html">← Dashboard</a>

<!-- Scrolling background photo layer (fixed) -->
<div class="scroll-bg" id="scrollBg">{scroll_bg_imgs}</div>

<!-- HERO -->
<section class="hero" data-bg-index="0">
  <div class="hero-bg" id="heroBg">{hero_bg_html}</div>
  <div class="hero-overlay"></div>
  <div class="hero-content">
    <div class="hero-yr">{year}</div>
    <div class="hero-vivid">{vivid}</div>
  </div>
  <div class="hero-scroll">scroll ↓</div>
</section>

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
document.querySelectorAll('.section').forEach(s => sObs.observe(s));

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
            f'<section class="section" id="sec-kpi" data-bg-index="1">'
            f'<div class="section-h">By the numbers</div>'
            f'<div class="section-title">A year in numbers</div>'
            f'<div class="section-intro">The shape of {yr}, distilled into eight counts.</div>'
            f'<div class="kpi-grid">{kpi_html}</div>'
            f'</section>'
        )

        # ── Month-by-month timeline ──
        mo_rows_html: list[str] = []
        for mo in range(1, 13):
            n = mon_y_counter.get(mo, 0)
            mo_name = months_full[mo - 1]
            if n == 0:
                mo_rows_html.append(
                    f'<div class="mo empty">'
                    f'<div class="mo-name">{mo_name}<span class="mo-count">— no check-ins</span></div>'
                    f'<div class="mo-body">'
                    f'<div class="mo-thumb mo-thumb-empty">Quiet</div>'
                    f'<div class="mo-text">A month off the map.</div>'
                    f'</div></div>'
                )
                continue
            top_v = mon_venue[mo].most_common(1)[0] if mon_venue[mo] else ("", 0)
            top_c = mon_city[mo].most_common(1)[0] if mon_city[mo] else ("", 0)
            top_cat = mon_cat[mo].most_common(1)[0] if mon_cat[mo] else ("", 0)
            # Pick a thumbnail from this month's photos
            mo_photos = photos_by_yr_mo.get((yr, mo), [])
            thumb_html = ""
            if mo_photos:
                thumb_html = f'<div class="mo-thumb" style="background-image:url(\'{_esc(mo_photos[0]["src"])}\')"></div>'
            else:
                thumb_html = f'<div class="mo-thumb mo-thumb-empty">{mo_name}</div>'
            # Compose month narrative
            bits: list[str] = []
            if top_c[0]:
                bits.append(f"<strong>{_esc(top_c[0])}</strong> held the most days")
            if top_v[0] and top_v[1] >= 4:
                bits.append(f"<strong>{_esc(top_v[0])}</strong> ({top_v[1]}× visits)")
            if top_cat[0]:
                bits.append(f"mostly <strong>{_esc(top_cat[0])}</strong>")
            mo_narr = ". ".join(bits[:3])
            if mo_narr:
                mo_narr = mo_narr[0].upper() + mo_narr[1:] + "."

            # Memorable shout (longest from this month, or null)
            mo_shouts = shouts_by_yr_mo.get((yr, mo), [])
            shout_html = ""
            if mo_shouts:
                top_shout = max(mo_shouts, key=lambda s: len(s["text"]))
                if len(top_shout["text"]) >= 12:
                    shout_html = (
                        f'<div class="mo-shout">"{_esc(top_shout["text"])}"'
                        f'<div class="mo-shout-meta">— at {_esc(top_shout["venue"] or "—")}, {_esc(top_shout["city"] or "—")}</div>'
                        f'</div>'
                    )
            mo_rows_html.append(
                f'<div class="mo">'
                f'<div class="mo-name">{mo_name}<span class="mo-count">{n:,} check-ins</span></div>'
                f'<div class="mo-body">'
                f'{thumb_html}'
                f'<div class="mo-text">{mo_narr or "A month in the rotation."}'
                f'{shout_html}'
                f'</div></div></div>'
            )
        months_section = (
            f'<section class="section" id="sec-months" data-bg-index="2">'
            f'<div class="section-h">Month by month</div>'
            f'<div class="section-title">The shape of <em>{yr}</em></div>'
            f'<div class="section-intro">Twelve panels, twelve textures. Each row picks the busiest city, the venue that kept showing up, and a sample of your words from those weeks.</div>'
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
            f'<section class="section" id="sec-top" data-bg-index="3">'
            f'<div class="section-h">Anchors</div>'
            f'<div class="section-title">Where <em>{yr}</em> kept landing</div>'
            f'<div class="section-intro">The places, venues and types of stops that defined the year.</div>'
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
                f'<section class="section" id="sec-newc" data-bg-index="4">'
                f'<div class="section-h">Firsts</div>'
                f'<div class="section-title">New on the map in <em>{yr}</em></div>'
                f'<div class="section-intro">First-time countries this year — the dots that turned gold.</div>'
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
                f'<section class="section" id="sec-trips" data-bg-index="5">'
                f'<div class="section-h">Journeys</div>'
                f'<div class="section-title">{len(trips_y)} trip{"s" if len(trips_y) != 1 else ""} <em>this year</em></div>'
                f'<div class="section-intro">Tap a card to open the full trip page.</div>'
                f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;">{trip_cards}</div>'
                f'</section>'
            )
        else:
            trips_section = ""

        # ── Flights (year page) ──
        if flights_y:
            f_cards_parts: list[str] = []
            for f in flights_y:
                logo = (
                    f'<img class="fl-logo" src="https://content.airhex.com/content/logos/airlines_{_esc(f.get("airline_iata", ""))}_70_70_s.png" alt="" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';"/>'
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
                f'<section class="section" id="sec-flights" data-bg-index="6">'
                f'<div class="section-h">In the air</div>'
                f'<div class="section-title">{len(flights_y)} flight{"s" if len(flights_y) != 1 else ""} <em>across the year</em></div>'
                f'<div class="section-intro">Each leg verified against the FlightRadar24 diary.</div>'
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
                f'<section class="section" id="sec-photos" data-bg-index="7">'
                f'<div class="section-h">Memory</div>'
                f'<div class="section-title">{len(photos_y)} photo{"s" if len(photos_y) != 1 else ""} from <em>{yr}</em></div>'
                f'<div class="section-intro">Tap any image to open it full-screen.</div>'
                f'<div class="photo-grid">{ph_cards}</div>'
                + (f'<div style="text-align:center;margin-top:18px;font-size:.74rem;color:var(--muted);">Showing 90 of {len(photos_y)}.</div>' if len(photos_y) > 90 else "")
                + '</section>'
            )
        else:
            photos_section = ""

        # ── Memorable shouts (longest 12) ──
        memorable = sorted(shouts_y, key=lambda s: -len(s["text"]))[:12]
        if memorable:
            s_cards = "".join(
                f'<div class="shout-card">'
                f'<div class="shout-text">"{_esc(s["text"])}"</div>'
                f'<div class="shout-meta">— {_esc(s["venue"])}{" · " + _esc(s["city"]) if s["city"] else ""}{" · " + _esc(s["country"]) if s["country"] else ""}</div>'
                f'</div>'
                for s in memorable
            )
            shouts_section = (
                f'<section class="section" id="sec-shouts" data-bg-index="8">'
                f'<div class="section-h">Words</div>'
                f'<div class="section-title"><em>{yr}</em> in your own voice</div>'
                f'<div class="section-intro">A sample of the year\'s longest shouts.</div>'
                f'<div class="shouts">{s_cards}</div>'
                f'</section>'
            )
        else:
            shouts_section = ""

        # ── Companions ──
        top_comp = comp_y.most_common(12)
        if top_comp:
            companions_section = (
                f'<section class="section" id="sec-comp" data-bg-index="9">'
                f'<div class="section-h">With you</div>'
                f'<div class="section-title">Most-frequent companions in <em>{yr}</em></div>'
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
