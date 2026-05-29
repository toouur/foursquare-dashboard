#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_year_pages.py — Generate one year-YYYY.html "album" page per year.

Each page is the long-form companion to the Year-in-Review card on
stats.html / index.html. It shows the year as a scroll-driven story:

  • Hero with huge year + animated KPI counters + Ken-Burns photo cycle
  • Vivid one-paragraph narrative (from metrics.year_summaries[i].vivid)
  • Monthly check-in spark + per-month top venue/city
  • Top venues / cities / categories / countries grids
  • New-countries with flag icons
  • Trips list (clickable to trip-N.html)
  • Flights list (with route, airline, aircraft)
  • Photo grid (lazy-loaded)
  • Memorable shouts from that year
  • Top companions

Sections fade in as the user scrolls (IntersectionObserver). Counters
animate from 0 on first reveal (requestAnimationFrame).

Called from build.py with: rows, stats_data, photos_by_checkin, trips,
flights, pix_dir_uri.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

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
<link rel="preconnect" href="https://cdnjs.cloudflare.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700;900&family=DM+Sans:wght@400;500;700&family=DM+Mono&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/flag-icon-css/6.6.6/css/flag-icons.min.css">
<style>
:root{{--bg:#0a0c12;--card:#12141c;--card2:#181a24;--border:#222738;--text:#e0e2ec;--muted:#7a85a8;--gold:#e8b86d;--teal:#4ec9b0;--rose:#e8778a;}}
body.light{{--bg:#e9e4dc;--card:#f0ece3;--card2:#e5e0d7;--border:#aaa49a;--gold:#7a3e00;--teal:#155c58;--muted:#54576a;--text:#15172a;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
html{{scroll-behavior:smooth;}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.5;overflow-x:hidden;}}
a{{color:var(--teal);}}

.crumb{{position:fixed;top:14px;left:18px;z-index:50;font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);background:rgba(18,21,31,.85);backdrop-filter:blur(8px);border:1px solid var(--border);padding:6px 12px;border-radius:6px;text-decoration:none;}}
.crumb:hover{{color:var(--gold);}}

/* ── HERO ── */
.hero{{position:relative;height:100vh;min-height:580px;overflow:hidden;display:flex;align-items:center;justify-content:center;}}
.hero-bg{{position:absolute;inset:0;z-index:0;}}
.hero-bg .photo{{position:absolute;inset:0;background-size:cover;background-position:center;opacity:0;transition:opacity 1.6s ease;filter:brightness(.45) contrast(1.05);}}
.hero-bg .photo.active{{opacity:1;animation:kb 18s ease-in-out infinite;}}
@keyframes kb{{0%{{transform:scale(1.05);}}50%{{transform:scale(1.18);}}100%{{transform:scale(1.05);}}}}
.hero-overlay{{position:absolute;inset:0;background:radial-gradient(ellipse at center,rgba(10,12,18,0.10) 0%,rgba(10,12,18,0.85) 80%);z-index:1;}}
.hero-content{{position:relative;z-index:2;text-align:center;padding:24px;max-width:780px;}}
.hero-yr{{font-family:'Playfair Display',serif;font-size:clamp(7rem,18vw,18rem);font-weight:900;line-height:0.95;letter-spacing:-0.03em;background:linear-gradient(150deg,#f5d48a 0%,#e8b86d 40%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}}
.hero-vivid{{font-family:'Playfair Display',serif;font-size:clamp(1rem,2.2vw,1.4rem);font-weight:400;color:#e8e9ee;line-height:1.5;margin-top:18px;text-shadow:0 2px 16px rgba(0,0,0,0.5);max-width:720px;margin-left:auto;margin-right:auto;font-style:italic;}}
.hero-vivid strong{{color:var(--gold);font-style:normal;}}
.hero-scroll{{position:absolute;bottom:28px;left:50%;transform:translateX(-50%);z-index:3;font-family:'DM Mono',monospace;font-size:.55rem;color:var(--muted);letter-spacing:.18em;text-transform:uppercase;animation:bob 2s ease-in-out infinite;}}
@keyframes bob{{0%,100%{{transform:translateX(-50%) translateY(0);}}50%{{transform:translateX(-50%) translateY(6px);}}}}

/* ── Sections ── */
.section{{padding:80px 28px;max-width:1200px;margin:0 auto;opacity:0;transform:translateY(40px);transition:opacity .9s ease,transform .9s ease;}}
.section.in{{opacity:1;transform:translateY(0);}}
.section-h{{font-family:'DM Mono',monospace;font-size:.62rem;text-transform:uppercase;letter-spacing:.22em;color:var(--gold);margin-bottom:8px;}}
.section-title{{font-family:'Playfair Display',serif;font-size:clamp(1.6rem,3vw,2.6rem);font-weight:700;color:var(--text);margin-bottom:34px;line-height:1.15;}}
@media(max-width:600px){{.section{{padding:50px 18px;}}.section-title{{font-size:1.5rem;margin-bottom:24px;}}}}

/* ── KPI strip ── */
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:18px;margin-bottom:50px;}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:20px 18px;text-align:center;position:relative;overflow:hidden;}}
.kpi::after{{content:'';position:absolute;top:0;left:18px;right:18px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:.4;}}
.kpi-num{{font-family:'Playfair Display',serif;font-size:2.2rem;font-weight:700;color:var(--gold);line-height:1;}}
.kpi-lbl{{font-family:'DM Mono',monospace;font-size:.55rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);margin-top:6px;}}
.kpi-sub{{font-size:.7rem;color:var(--text);margin-top:4px;opacity:.85;}}

/* ── Lists / grids ── */
.list-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;}}
.item{{background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:14px 16px;display:flex;align-items:center;gap:10px;}}
.item .rank{{font-family:'DM Mono',monospace;font-size:.62rem;color:var(--muted);width:24px;text-align:right;flex-shrink:0;}}
.item .name{{flex:1;font-size:.85rem;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.item .name a{{color:inherit;text-decoration:none;}}
.item .name a:hover{{color:var(--gold);}}
.item .cnt{{font-family:'DM Mono',monospace;font-size:.7rem;color:var(--teal);flex-shrink:0;}}

/* ── Flag list ── */
.flag-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;}}
.flag-item{{background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:10px 14px;display:flex;align-items:center;gap:10px;}}
.flag-item .fi{{font-size:1.1em;border-radius:2px;}}
.flag-item .nm{{font-size:.82rem;font-weight:500;}}

/* ── Photos grid ── */
.photo-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;}}
.photo{{aspect-ratio:1/1;background:var(--card2);border-radius:8px;overflow:hidden;position:relative;cursor:zoom-in;}}
.photo img{{width:100%;height:100%;object-fit:cover;transition:transform .4s ease;}}
.photo:hover img{{transform:scale(1.08);}}
.photo .ph-meta{{position:absolute;left:0;right:0;bottom:0;padding:6px 8px 8px;background:linear-gradient(transparent,rgba(0,0,0,.85));font-size:.62rem;color:#fff;opacity:0;transition:opacity .2s;}}
.photo:hover .ph-meta{{opacity:1;}}

/* ── Trip / flight cards ── */
.trip-card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 20px;text-decoration:none;color:var(--text);transition:border-color .2s;display:block;}}
.trip-card:hover{{border-color:var(--gold);}}
.trip-name{{font-family:'Playfair Display',serif;font-size:1.1rem;font-weight:700;color:var(--gold);margin-bottom:6px;}}
.trip-meta{{font-family:'DM Mono',monospace;font-size:.62rem;color:var(--muted);letter-spacing:.04em;}}
.trip-countries{{margin-top:8px;font-size:.74rem;color:var(--text);opacity:.85;}}

.flight-card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px 18px;display:flex;align-items:center;gap:18px;flex-wrap:wrap;}}
.flight-card .route{{font-family:'Playfair Display',serif;font-size:1.25rem;font-weight:700;color:var(--gold);}}
.flight-card .route .arrow{{margin:0 8px;color:var(--muted);font-size:1rem;font-weight:400;}}
.flight-card .fno{{font-family:'DM Mono',monospace;font-size:.75rem;color:var(--teal);background:rgba(78,201,176,.10);padding:2px 9px;border-radius:5px;}}
.flight-card .meta{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);letter-spacing:.04em;flex:1;}}
.flight-card .meta span{{margin-right:14px;}}

/* ── Shouts ── */
.shouts{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;}}
.shout-card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px;}}
.shout-text{{font-style:italic;font-size:.86rem;color:var(--text);line-height:1.5;}}
.shout-meta{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);margin-top:8px;}}

/* ── Lightbox ── */
.lb{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:9000;align-items:center;justify-content:center;cursor:zoom-out;padding:36px;}}
.lb.open{{display:flex;}}
.lb img{{max-width:100%;max-height:100%;border-radius:6px;}}

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

<!-- HERO -->
<section class="hero">
  <div class="hero-bg" id="heroBg">{hero_bg_html}</div>
  <div class="hero-overlay"></div>
  <div class="hero-content">
    <div class="hero-yr">{year}</div>
    <div class="hero-vivid">{vivid}</div>
  </div>
  <div class="hero-scroll">scroll ↓</div>
</section>

<!-- KPI ROW -->
<section class="section" id="sec-kpi">
  <div class="section-h">By the numbers</div>
  <div class="section-title">A year in numbers</div>
  <div class="kpi-grid" id="kpiGrid">{kpi_html}</div>
</section>

<!-- TOP VENUES / CITIES / CATEGORIES -->
<section class="section" id="sec-top">
  <div class="section-h">Most visited</div>
  <div class="section-title">Where you spent {year}</div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px;">
    <div>
      <div style="font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--gold);margin-bottom:10px;">Top venues</div>
      <div class="list-grid" style="grid-template-columns:1fr;">{top_venues_html}</div>
    </div>
    <div>
      <div style="font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--gold);margin-bottom:10px;">Top cities</div>
      <div class="list-grid" style="grid-template-columns:1fr;">{top_cities_html}</div>
    </div>
    <div>
      <div style="font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--gold);margin-bottom:10px;">Top categories</div>
      <div class="list-grid" style="grid-template-columns:1fr;">{top_cats_html}</div>
    </div>
  </div>
</section>

<!-- NEW COUNTRIES -->
{new_countries_section}

<!-- TRIPS -->
{trips_section}

<!-- FLIGHTS -->
{flights_section}

<!-- PHOTOS -->
{photos_section}

<!-- SHOUTS -->
{shouts_section}

<!-- COMPANIONS -->
{companions_section}

<!-- Lightbox -->
<div class="lb" id="lb" onclick="this.classList.remove('open')"><img id="lbImg" src="" alt=""></div>

<nav class="side-nav" aria-label="Page shortcuts">
  <a href="index.html"><span class="sn-icon">🏠</span><span class="sn-lbl">HOME</span></a>
  <a href="stats.html"><span class="sn-icon">📊</span><span class="sn-lbl">STATS</span></a>
  <a href="trips.html"><span class="sn-icon">✈</span><span class="sn-lbl">TRIPS</span></a>
  <a href="feed.html"><span class="sn-icon">📰</span><span class="sn-lbl">FEED</span></a>
</nav>

<script>
// ── Scroll fade-in ──
const obs=new IntersectionObserver(es=>es.forEach(e=>e.isIntersecting&&e.target.classList.add('in')),{{threshold:0.08}});
document.querySelectorAll('.section').forEach(s=>obs.observe(s));

// ── KPI counters animate from 0 ──
function animateCount(el,target){{
  const start=performance.now(),dur=1200;
  function tick(t){{
    const p=Math.min(1,(t-start)/dur);
    const eased=1-Math.pow(1-p,3);
    el.textContent=Math.round(target*eased).toLocaleString();
    if(p<1)requestAnimationFrame(tick);
  }}
  requestAnimationFrame(tick);
}}
const kpiObs=new IntersectionObserver(es=>es.forEach(e=>{{
  if(!e.isIntersecting)return;
  e.target.querySelectorAll('[data-count]').forEach(el=>{{
    const v=+el.dataset.count;
    if(!isNaN(v))animateCount(el,v);
    el.removeAttribute('data-count');
  }});
  kpiObs.unobserve(e.target);
}}),{{threshold:0.3}});
document.querySelectorAll('#kpiGrid,.kpi-grid').forEach(g=>kpiObs.observe(g));

// ── Hero photo cycle ──
(function(){{
  const bg=document.getElementById('heroBg');
  if(!bg)return;
  const photos=bg.querySelectorAll('.photo');
  if(photos.length<2)return;
  let i=0;photos[0].classList.add('active');
  setInterval(()=>{{photos[i].classList.remove('active');i=(i+1)%photos.length;photos[i].classList.add('active');}},5200);
}})();

// ── Lightbox ──
window.openLB=src=>{{document.getElementById('lbImg').src=src;document.getElementById('lb').classList.add('open');}};

// ── Theme ──
(function(){{document.body.classList.toggle('light',localStorage.getItem('fsq-theme')==='light');}})();
</script>
</body>
</html>
"""

# Country → ISO 3166 alpha-2.  Filled lazily on first build_page() call from
# config/country_flags.json so the module can be imported without the config
# being present (tests, isolated imports).
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
    if not code:
        return ""
    return f'<span class="fi fi-{code.lower()}"></span>'


def _esc(s) -> str:
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


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
    """Generate one year-YYYY.html per year present in the data.

    `out_path` here is treated as the OUTPUT DIRECTORY (build.py passes
    args.output_dir). Each year writes its own file inside.
    """
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

    # Index check-ins by year for venue/city/category breakdowns
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

    # Index photos by year via the check-in date lookup
    cid_year: dict[str, int] = {}
    for r in rows:
        cid = (r.get("checkin_id") or "").strip()
        try:
            ts = int(r.get("date", 0) or 0)
        except ValueError:
            continue
        if cid and ts:
            cid_year[cid] = datetime.fromtimestamp(ts, tz=timezone.utc).year
    photos_by_year: dict[int, list[dict]] = defaultdict(list)
    for cid, fnames in photos_by_checkin.items():
        yr = cid_year.get(cid)
        if not yr or not fnames:
            continue
        for fn in fnames:
            # Look up the original row for venue + city
            for r in by_year_rows.get(yr, []):
                if r.get("checkin_id") == cid:
                    photos_by_year[yr].append({
                        "src":      f"{pix_url.rstrip('/')}/{fn}" if pix_url else fn,
                        "venue":    r.get("venue", ""),
                        "city":     r.get("city", ""),
                        "ts":       int(r.get("date", 0) or 0),
                        "checkin_id": cid,
                    })
                    break

    # Shouts grouped by year (top 12 longest per year)
    shout_records_iter = stats_data.get("shout_records") or []
    # The stats payload doesn't carry the full shout list; rebuild from rows
    shouts_by_year: dict[int, list[dict]] = defaultdict(list)
    import re as _re
    suffix_re = _re.compile(r"\s*[—\-–]\s*with\s+.+$", _re.IGNORECASE | _re.UNICODE)
    with_only_re = _re.compile(r"^\s*[—\-–]?\s*with\s+\S+", _re.IGNORECASE | _re.UNICODE)
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
        yr = datetime.fromtimestamp(ts, tz=timezone.utc).year
        shouts_by_year[yr].append({
            "text":    clean,
            "venue":   r.get("venue", ""),
            "city":    r.get("city", ""),
            "country": r.get("country", ""),
            "ts":      ts,
        })

    # Trips by year
    trips_by_year: dict[int, list[dict]] = defaultdict(list)
    for t in trips:
        yr = t.get("start_year")
        if yr:
            trips_by_year[yr].append(t)

    # Flights by year — re-derive route info using the canonical flights list
    flights_by_year: dict[int, list[dict]] = defaultdict(list)
    if flights_data:
        for f in flights_data:
            try:
                yr = int((f.get("date") or "")[:4])
            except (ValueError, IndexError):
                continue
            flights_by_year[yr].append(f)

    # ── Render one page per year ─────────────────────────────────────────
    for ys in year_summaries:
        yr = ys["year"]
        rows_y = by_year_rows.get(yr, [])
        photos_y = sorted(photos_by_year.get(yr, []), key=lambda p: -p["ts"])
        shouts_y = shouts_by_year.get(yr, [])
        trips_y  = sorted(trips_by_year.get(yr, []), key=lambda t: t.get("start_ts", 0))
        flights_y = sorted(flights_by_year.get(yr, []), key=lambda f: f.get("date", ""))

        # Build counters
        ven_y: Counter = Counter()
        ven_id: dict[str, str] = {}
        cty_y: Counter = Counter()
        cat_y: Counter = Counter()
        comp_y: Counter = Counter()
        try:
            from metrics import collect_companions as _cc
        except ImportError:
            _cc = lambda r: []
        for r in rows_y:
            vid = (r.get("venue_id") or "").strip()
            vn  = (r.get("venue") or "").strip()
            if vid and vn:
                ven_y[(vid, vn)] += 1
            if r.get("city"): cty_y[r["city"]] += 1
            if r.get("category"): cat_y[r["category"]] += 1
            for n in _cc(r): comp_y[n] += 1

        # HERO Ken-Burns photos (top 5)
        hero_photos = photos_y[:5]
        hero_bg_html = "".join(
            f'<div class="photo" style="background-image:url(\'{_esc(p["src"])}\')"></div>'
            for p in hero_photos
        )

        # KPI grid
        kpi_html = ""
        kpis = [
            (ys["total"], "Check-ins", ""),
            (ys["cities"], "Cities", ""),
            (ys["countries"], "Countries", ""),
            (ys["new_countries"], "New countries", "discovered for the first time"),
            (ys.get("distance_km", 0), "km travelled", "between consecutive check-ins"),
            (ys.get("trip_count", 0), "Trips", ""),
            (len(photos_y), "Photos", ""),
            (len(flights_y), "Flights", ""),
        ]
        for n, lbl, sub in kpis:
            if n is None:
                continue
            kpi_html += (
                f'<div class="kpi"><div class="kpi-num" data-count="{n}">0</div>'
                f'<div class="kpi-lbl">{_esc(lbl)}</div>'
                + (f'<div class="kpi-sub">{_esc(sub)}</div>' if sub else '')
                + '</div>'
            )

        # Top venues / cities / categories
        def _list_html(items: list[tuple], with_link=False) -> str:
            html_lines = []
            for i, (name, count) in enumerate(items[:8], 1):
                # name may be tuple (vid, vname) for venues
                if isinstance(name, tuple):
                    vid, vname = name
                    link = (f'<a href="https://foursquare.com/v/{_esc(vid)}" target="_blank" rel="noopener">{_esc(vname)}</a>'
                            if with_link and vid else _esc(vname))
                    html_lines.append(
                        f'<div class="item"><span class="rank">#{i}</span>'
                        f'<span class="name">{link}</span>'
                        f'<span class="cnt">{count}×</span></div>'
                    )
                else:
                    html_lines.append(
                        f'<div class="item"><span class="rank">#{i}</span>'
                        f'<span class="name">{_esc(name)}</span>'
                        f'<span class="cnt">{count}×</span></div>'
                    )
            return "".join(html_lines) or '<div style="color:var(--muted);font-size:.78rem;">—</div>'

        top_venues_html = _list_html(ven_y.most_common(8), with_link=True)
        top_cities_html = _list_html(cty_y.most_common(8))
        top_cats_html   = _list_html(cat_y.most_common(8))

        # New countries (flags)
        nc = ys.get("new_countries_list", [])
        if nc:
            nc_html = "".join(
                f'<div class="flag-item">{_flag(c)}<span class="nm">{_esc(c)}</span></div>'
                for c in nc
            )
            new_countries_section = (
                '<section class="section" id="sec-newc">'
                '<div class="section-h">Firsts</div>'
                f'<div class="section-title">New on the map in {yr}</div>'
                f'<div class="flag-grid">{nc_html}</div>'
                '</section>'
            )
        else:
            new_countries_section = ""

        # Trips
        if trips_y:
            trip_cards = "".join(
                f'<a class="trip-card" href="trip-{t.get("id")}.html">'
                f'<div class="trip-name">{_esc(t.get("name", "Trip"))}</div>'
                f'<div class="trip-meta">{_esc(t.get("start_date", ""))} → {_esc(t.get("end_date", ""))} · {t.get("duration", 0)} days · {t.get("checkin_count", 0)} check-ins</div>'
                f'<div class="trip-countries">{" · ".join(_esc(c) for c in (t.get("countries") or [])[:5])}</div>'
                '</a>'
                for t in trips_y
            )
            trips_section = (
                '<section class="section" id="sec-trips">'
                '<div class="section-h">Journeys</div>'
                f'<div class="section-title">{len(trips_y)} trip{"s" if len(trips_y) != 1 else ""} in {yr}</div>'
                f'<div class="list-grid">{trip_cards}</div>'
                '</section>'
            )
        else:
            trips_section = ""

        # Flights
        if flights_y:
            f_cards = "".join(
                f'<div class="flight-card">'
                f'<div class="route">{_esc(f.get("from_iata", "—"))}<span class="arrow">→</span>{_esc(f.get("to_iata", "—"))}</div>'
                + (f'<div class="fno">{_esc(f.get("flight", ""))}</div>' if f.get("flight") else '')
                + f'<div class="meta">'
                  f'<span>📅 {_esc(f.get("date", ""))}</span>'
                  + (f'<span>{_esc(f.get("airline", ""))}</span>' if f.get("airline") else '')
                  + (f'<span>{_esc(f.get("aircraft_code", "") or f.get("aircraft", ""))}</span>' if (f.get("aircraft_code") or f.get("aircraft")) else '')
                  + (f'<span>{f.get("dur_min", 0)//60}h {f.get("dur_min", 0)%60}m</span>' if f.get("dur_min") else '')
                  + f'</div>'
                + '</div>'
                for f in flights_y
            )
            flights_section = (
                '<section class="section" id="sec-flights">'
                '<div class="section-h">In the air</div>'
                f'<div class="section-title">{len(flights_y)} flight{"s" if len(flights_y) != 1 else ""} in {yr}</div>'
                f'<div style="display:flex;flex-direction:column;gap:8px;">{f_cards}</div>'
                '</section>'
            )
        else:
            flights_section = ""

        # Photos
        if photos_y:
            ph_cards = "".join(
                f'<div class="photo" onclick="openLB(\'{_esc(p["src"])}\')">'
                f'<img src="{_esc(p["src"])}" loading="lazy" alt="">'
                f'<div class="ph-meta">{_esc(p["venue"])}{" · " + _esc(p["city"]) if p["city"] else ""}</div>'
                '</div>'
                for p in photos_y[:60]
            )
            photos_section = (
                '<section class="section" id="sec-photos">'
                '<div class="section-h">Memory</div>'
                f'<div class="section-title">{len(photos_y)} photo{"s" if len(photos_y) != 1 else ""} from {yr}</div>'
                f'<div class="photo-grid">{ph_cards}</div>'
                + (f'<div style="text-align:center;margin-top:18px;font-size:.74rem;color:var(--muted);">Showing 60 of {len(photos_y)}.</div>' if len(photos_y) > 60 else '')
                + '</section>'
            )
        else:
            photos_section = ""

        # Memorable shouts — longest 12 (rough proxy for "memorable")
        memorable = sorted(shouts_y, key=lambda s: -len(s["text"]))[:12]
        if memorable:
            s_cards = "".join(
                f'<div class="shout-card">'
                f'<div class="shout-text">"{_esc(s["text"])}"</div>'
                f'<div class="shout-meta">{_esc(s["venue"])}{" · " + _esc(s["city"]) if s["city"] else ""}{" · " + _esc(s["country"]) if s["country"] else ""}</div>'
                '</div>'
                for s in memorable
            )
            shouts_section = (
                '<section class="section" id="sec-shouts">'
                '<div class="section-h">Words</div>'
                f'<div class="section-title">{yr} in your own voice</div>'
                f'<div class="shouts">{s_cards}</div>'
                '</section>'
            )
        else:
            shouts_section = ""

        # Companions
        top_companions = comp_y.most_common(12)
        if top_companions:
            c_cards = _list_html(top_companions)
            companions_section = (
                '<section class="section" id="sec-comp">'
                '<div class="section-h">With you</div>'
                f'<div class="section-title">Most-frequent companions in {yr}</div>'
                f'<div class="list-grid">{c_cards}</div>'
                '</section>'
            )
        else:
            companions_section = ""

        # Description (slugged, plain text)
        description = (
            f"{ys.get('total', 0):,} check-ins, "
            f"{ys.get('cities', 0)} cities, "
            f"{ys.get('countries', 0)} countries — your year {yr}."
        )

        # Render the template
        html = PAGE_TEMPLATE.format(
            year=yr,
            title=f"{yr} — Year in Review",
            description=description,
            vivid=ys.get("vivid", description),
            hero_bg_html=hero_bg_html,
            kpi_html=kpi_html,
            top_venues_html=top_venues_html,
            top_cities_html=top_cities_html,
            top_cats_html=top_cats_html,
            new_countries_section=new_countries_section,
            trips_section=trips_section,
            flights_section=flights_section,
            photos_section=photos_section,
            shouts_section=shouts_section,
            companions_section=companions_section,
        )
        # The hero photo CSS variables are inlined as plain strings, no
        # additional placeholders survive in PAGE_TEMPLATE.
        (out_dir / f"year-{yr}.html").write_text(html, encoding="utf-8")

    print(f"year-YYYY.html -> {out_dir}  ({len(year_summaries)} pages)")
