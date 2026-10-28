#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_country_pages.py — per-country detail pages (audit #10).

Emits one `country-<slug>.html` per country that appears in the check-in data,
and one `city-<slug>.html` per city with at least CITY_MIN_CHECKINS check-ins
(same layout; neighbourhoods instead of cities, home eras tinted).
Each page is a clean, self-contained magazine card (no side-nav, no external
JS) summarising everything that happened in that country:

  * hero — a photo from the country, flag, name, first/last visit; then KPIs
  * a tile-free dot map of every venue (inline SVG, top cities labelled)
  * a by-year activity bar strip over the whole 2007-now span
  * top cities / top venues / top categories
  * trips that touched the country (linking to trip-<id>.html)
  * a photo strip (when photos are available)
  * a few of the longest shouts written there

The slug is the ISO 3166-1 alpha-2 code when known (`country-by.html`,
`country-us.html`), falling back to an ASCII slug of the name. The index page's
country grid links to these with the SAME slug rule (see `countrySlug()` in
templates/index.html.tmpl), so the two must stay in lock-step.

Output is gitignored (`country-*.html`) and shipped by the CI cp glob.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from metrics.venues import _f, _Localiser, clean_shout

CTRY_CODE: dict[str, str] = {}

SITE_URL = "https://4sq.pages.dev"
CITY_MIN_CHECKINS = 20     # a city gets its own page from this many check-ins
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


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


def country_slug(country: str) -> str:
    """ISO alpha-2 (lower) when known, else an ASCII slug of the name.

    MUST mirror `countrySlug()` in templates/index.html.tmpl — the index country
    grid links here with the identical rule.
    """
    code = CTRY_CODE.get(country, "")
    if code:
        return code.lower()
    out = []
    for ch in country.lower():
        out.append(ch if ("a" <= ch <= "z" or "0" <= ch <= "9") else "-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "x"


def _flag(country: str) -> str:
    code = CTRY_CODE.get(country, "")
    return f'<span class="fi fi-{code.lower()}"></span>' if code else ""


def _esc(s) -> str:
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _fmt_date(ts: int) -> str:
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{site}/{page}">
<meta property="og:type" content="article">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;700;900&family=DM+Sans:wght@400;500;600;700&family=DM+Mono&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/flag-icon-css/6.6.6/css/flag-icons.min.css">
<style>
:root{{--bg:#0a0c12;--card:#12141c;--card2:#181a24;--border:#222738;--text:#e0e2ec;--muted:#7a85a8;--gold:#e8b86d;--teal:#4ec9b0;--rose:#e8778a;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.55;overflow-x:hidden;}}
a{{color:var(--teal);}}
.crumb{{position:fixed;top:14px;left:18px;z-index:200;font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);background:rgba(18,21,31,.85);backdrop-filter:blur(8px);border:1px solid var(--border);padding:6px 12px;border-radius:6px;text-decoration:none;}}
.crumb:hover{{color:var(--gold);}}
.wrap{{max-width:960px;margin:0 auto;padding:88px 22px 120px;}}
.hero-flag{{font-size:0;line-height:0;margin-bottom:16px;}}
.hero-flag .fi{{width:96px;height:72px;border-radius:8px;box-shadow:0 8px 26px rgba(0,0,0,.5);}}
.hero-name{{font-family:'Playfair Display',serif;font-size:clamp(2.4rem,6vw,4.2rem);font-weight:900;line-height:1.08;letter-spacing:-.02em;padding-bottom:.08em;background:linear-gradient(150deg,#f5d48a 0%,#e8b86d 40%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}}
.hero-sub{{font-family:'DM Mono',monospace;font-size:.66rem;text-transform:uppercase;letter-spacing:.16em;color:var(--muted);margin-top:12px;}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:34px 0 12px;}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px 16px;text-align:center;}}
.kpi-num{{font-family:'Playfair Display',serif;font-size:2rem;font-weight:700;color:var(--gold);line-height:1;}}
.kpi-lbl{{font-family:'DM Mono',monospace;font-size:.54rem;text-transform:uppercase;letter-spacing:.13em;color:var(--muted);margin-top:7px;}}
section{{margin-top:52px;}}
.sec-h{{font-family:'DM Mono',monospace;font-size:.6rem;text-transform:uppercase;letter-spacing:.2em;color:var(--gold);margin-bottom:16px;}}
.years{{display:flex;align-items:flex-end;gap:6px;height:148px;padding:8px 0;overflow-x:auto;overflow-y:hidden;}}
.yr-bar{{display:flex;flex-direction:column;align-items:center;gap:6px;flex:0 0 auto;min-width:34px;}}
.yr-bar .bar{{width:22px;background:linear-gradient(180deg,#f5d48a,#b97c30);border-radius:4px 4px 0 0;}}
.yr-bar .yr-lbl{{font-family:'DM Mono',monospace;font-size:.52rem;color:var(--muted);}}
.yr-bar .yr-n{{font-family:'DM Mono',monospace;font-size:.5rem;color:var(--teal);}}
.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:26px;}}
.rank-list{{display:flex;flex-direction:column;gap:4px;}}
.rank-row{{display:grid;grid-template-columns:20px 1fr auto;gap:8px;align-items:center;padding:4px 6px;border-radius:6px;}}
.rank-row:hover{{background:var(--card2);}}
.rank-i{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);}}
.rank-name{{font-size:.85rem;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.rank-name a{{color:inherit;text-decoration:none;transition:color .15s;}}
.rank-row:hover .rank-name a{{color:var(--gold);}}
.rank-n{{font-family:'DM Mono',monospace;font-size:.72rem;color:var(--teal);}}
.trips{{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:10px;}}
.trips .trip-card.more{{display:none;}}
.trips.all .trip-card.more{{display:block;}}
.more-btn{{display:block;margin:14px auto 0;background:var(--card2);border:1px solid var(--border);border-radius:999px;padding:8px 18px;font-family:'DM Mono',monospace;font-size:.6rem;letter-spacing:.1em;color:var(--text);cursor:pointer;}}
.more-btn:hover{{border-color:var(--gold);color:var(--gold);}}
.trip-card{{display:block;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 16px;text-decoration:none;color:var(--text);transition:border-color .2s,transform .2s;}}
.trip-card:hover{{border-color:var(--gold);transform:translateY(-2px);}}
.trip-name{{font-family:'Playfair Display',serif;font-size:1.1rem;font-weight:700;color:var(--gold);margin-bottom:4px;}}
.trip-meta{{font-family:'DM Mono',monospace;font-size:.58rem;color:var(--muted);letter-spacing:.04em;}}
.photos{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;}}
.photos img{{width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:8px;background:var(--card2);}}
.shouts{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;}}
.shout{{position:relative;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 16px 12px 22px;font-family:'Playfair Display',serif;font-style:italic;font-size:.98rem;color:#e8e9ee;line-height:1.5;}}
.shout::before{{content:'';position:absolute;left:10px;top:16px;bottom:16px;width:3px;border-radius:2px;background:linear-gradient(180deg,#f6d38b,#ff6fae);}}
.log-btn{{width:100%;display:flex;justify-content:space-between;align-items:center;gap:12px;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 18px;color:var(--text);font-family:'DM Sans',sans-serif;font-size:.92rem;cursor:pointer;text-align:left;}}
.log-btn:hover{{border-color:var(--gold);}}
.log-btn b{{font-family:'DM Mono',monospace;font-size:.6rem;letter-spacing:.12em;color:var(--gold);font-weight:400;white-space:nowrap;}}
.log{{display:none;margin-top:10px;}}
.log.open{{display:block;}}
.log input{{width:100%;background:var(--card);border:1px solid var(--border);border-radius:999px;padding:10px 16px;color:var(--text);font-size:.85rem;outline:none;margin-bottom:6px;}}
.log input:focus{{border-color:var(--gold);}}
.log-m{{font-family:'DM Mono',monospace;font-size:.6rem;letter-spacing:.14em;text-transform:uppercase;color:var(--gold);padding:16px 2px 6px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;}}
.log-m span{{color:var(--muted);}}
.log-r{{display:grid;grid-template-columns:104px minmax(0,1fr) auto;gap:10px;align-items:baseline;padding:7px 2px;border-bottom:1px solid rgba(255,255,255,.04);}}
.log-r .d{{font-family:'DM Mono',monospace;font-size:.62rem;color:var(--muted);white-space:nowrap;}}
.log-r .d b{{font-weight:400;margin-left:6px;color:#9aa3c0;}}
.log-r .v{{font-size:.86rem;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.log-r .v a{{color:var(--text);text-decoration:none;}}
.log-r .v a:hover{{color:var(--gold);}}
.log-r .v i{{font-style:normal;color:var(--muted);font-size:.76rem;}}
.log-r .c{{font-family:'DM Mono',monospace;font-size:.58rem;color:var(--teal);white-space:nowrap;max-width:140px;overflow:hidden;text-overflow:ellipsis;}}
.log-r.rf .v{{opacity:.75;}}
.log-n{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);padding:6px 2px;}}
@media(max-width:600px){{.log-r{{grid-template-columns:54px minmax(0,1fr);align-items:center;}}.log-r .c{{display:none;}}.log-r .d b{{display:block;margin-left:0;}}}}
.shout-meta{{font-family:'DM Mono',monospace;font-size:.55rem;color:var(--muted);margin-top:8px;font-style:normal;letter-spacing:.04em;}}
@media(max-width:600px){{.wrap{{padding:78px 16px 90px;}}}}
/* 2026 refresh: photo hero, sunset gradient, dot map, cards */
.hero{{position:relative;min-height:440px;display:flex;align-items:flex-end;overflow:hidden;}}
.hero-bg{{position:absolute;inset:0;background:var(--card2) center/cover no-repeat;filter:brightness(.42) saturate(1.05);transform:scale(1.04);}}
.hero-bg.none{{background:radial-gradient(ellipse at 30% 20%,rgba(255,154,107,.18),transparent 60%),radial-gradient(ellipse at 80% 80%,rgba(255,111,174,.12),transparent 60%);filter:none;}}
.hero::after{{content:'';position:absolute;inset:0;background:linear-gradient(180deg,rgba(10,12,18,.2) 0%,rgba(10,12,18,.35) 45%,var(--bg) 100%);}}
.hero-in{{position:relative;z-index:1;width:100%;max-width:960px;margin:0 auto;padding:110px 22px 34px;}}
.hero-name{{display:inline-block;background:linear-gradient(150deg,#f6d38b 0%,#ff9a6b 55%,#ff6fae 100%);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}}
.hero-sub{{color:#c4cbe0;}}
.wrap{{padding-top:10px;}}
.kpis{{grid-template-columns:repeat(4,1fr);margin-top:6px;}}
.kpi{{border-radius:18px;text-align:left;background:rgba(18,21,31,.85);}}
.kpi-num{{font-size:2.1rem;background:linear-gradient(135deg,#f6d38b,#ff9a6b 55%,#ff6fae);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}}
.sec-h{{color:#ff9a6b;}}
.cmap{{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:10px;overflow:hidden;}}
.cmap svg{{display:block;width:100%;height:auto;}}
.cmap circle{{fill:#ff9a6b;fill-opacity:.55;}}
.cmap circle.hot{{fill:#f6d38b;fill-opacity:.85;}}
.cmap text{{font-family:'DM Mono',monospace;font-size:11px;fill:#e0e2ec;paint-order:stroke;stroke:#12141c;stroke-width:3px;letter-spacing:.04em;}}
.years{{gap:4px;height:130px;overflow:visible;}}
.yr-bar{{flex:1 1 0;min-width:0;}}
.yr-bar .bar{{width:100%;border-radius:6px 6px 2px 2px;background:linear-gradient(180deg,#ff9a6b,#f6d38b);}}
.yr-bar.zero .bar{{background:var(--border);}}
.yr-bar.zero .yr-n{{visibility:hidden;}}
.yr-bar .yr-n{{color:#f6b98b;}}
.cols>div{{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:16px 14px 12px;}}
.rank-n{{color:#f6b98b;}}
.trip-card{{padding:0;overflow:hidden;border-radius:18px;}}
.trip-card:hover{{border-color:#ff9a6b;}}
.trip-cover{{aspect-ratio:16/9;background:var(--card2) center/cover no-repeat;}}
.trip-cover.none{{aspect-ratio:auto;height:6px;background:linear-gradient(90deg,#f6d38b,#ff9a6b 55%,#ff6fae);opacity:.7;}}
.trip-body{{padding:12px 16px 14px;}}
.trip-name{{color:#f6b98b;}}
.photos{{grid-template-columns:repeat(4,1fr);}}
.photos img{{border-radius:12px;}}
.photos img:first-child{{grid-column:span 2;grid-row:span 2;height:100%;aspect-ratio:auto;contain:size;}}
.log-btn:hover,.more-btn:hover{{border-color:#ff9a6b;}}
.hero-sub a{{color:#f6d38b;text-decoration:none;}}
.hero-sub a:hover{{color:#ff9a6b;}}
.home-tag{{font-weight:400;color:#0b0d13;background:#4ec9b0;border-radius:999px;padding:2px 9px;white-space:nowrap;}}
.yr-bar.home .bar{{background:linear-gradient(180deg,#7fe0cb,#3aa892);}}
.legend{{float:right;color:var(--muted);letter-spacing:.12em;}}
.legend i{{display:inline-block;width:9px;height:9px;border-radius:2px;background:#4ec9b0;margin-right:6px;vertical-align:-1px;}}
@media(max-width:700px){{.cmap text{{font-size:26px;stroke-width:5px;}}.cmap circle{{transform-box:fill-box;}}.kpis{{grid-template-columns:1fr 1fr;}}.photos{{grid-template-columns:repeat(3,1fr);}}.hero{{min-height:340px;}}.yr-bar .yr-lbl{{font-size:.42rem;writing-mode:vertical-rl;transform:rotate(180deg);}}.yr-bar .yr-n{{display:none;}}}}
@media(max-width:600px){{.wrap{{padding:6px 16px 90px;}}.hero-in{{padding:96px 16px 26px;}}}}
</style>
</head>
<body>
<a class="crumb" href="{crumb_href}">{crumb_label}</a>
<header class="hero"><div class="hero-bg{hero_cls}"{hero_style}></div><div class="hero-in">
<div class="hero-flag">{flag_big}</div>
<h1 class="hero-name">{name}</h1>
<div class="hero-sub">{sub}</div>
</div></header>
<div class="wrap">
<div class="kpis">{kpis}</div>
{cmap}
{years}
{cities}
{venues}
{cats}
{trips}
{photos}
{shouts}
{log}
</div>
<script>
function moreTrips(b){{b.previousElementSibling.classList.add('all');b.remove();}}
// The full log loads its own file (<kind>_ck_<slug>.json) on the first open.
const LOG_URL={log_url}, MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],
      WD=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'], STEP=300;
let ROWS=null, shown=0, list=[];
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
function toggleLog(){{
  const l=document.getElementById('log'); l.classList.toggle('open');
  document.getElementById('logLbl').textContent=l.classList.contains('open')?'HIDE ▴':'OPEN ▾';
  if(ROWS===null){{ROWS=[];fetch(LOG_URL).then(r=>r.json()).then(j=>{{ROWS=j.r;filterLog('');}}).catch(()=>{{document.getElementById('logBody').textContent='Could not load the log.';}});}}
}}
function filterLog(q){{
  q=(q||'').toLowerCase();
  list=q?ROWS.filter(r=>(r[1]+' '+r[2]+' '+r[3]).toLowerCase().includes(q)):ROWS;
  shown=0; document.getElementById('logBody').innerHTML=''; moreLog();
}}
function moreLog(){{
  const body=document.getElementById('logBody'), btn=document.getElementById('logMore'); if(btn) btn.remove();
  let h='', prev=shown?list[shown-1][0].slice(0,7):'';
  const end=Math.min(list.length, shown+STEP);
  for(let k=shown;k<end;k++){{
    const [t,v,c,cat,id,rf]=list[k], m=t.slice(0,7);
    if(m!==prev){{const n=list.filter(r=>r[0].startsWith(m)).length;h+=`<div class="log-m">${{MON[+m.slice(5)-1]}} ${{m.slice(0,4)}}<span>${{n}}</span></div>`;prev=m;}}
    const d=new Date(t.slice(0,10)+'T12:00:00Z');
    h+=`<div class="log-r${{rf?' rf':''}}"><span class="d">${{WD[d.getUTCDay()]}} ${{+t.slice(8,10)}}<b>${{t.slice(11)}}</b></span>`+
       `<span class="v">${{id?`<a href="/venue.html#${{id}}">${{esc(v)}}</a>`:esc(v)}} <i>${{esc(c)}}</i></span><span class="c">${{esc(cat)}}</span></div>`;
  }}
  shown=end; body.insertAdjacentHTML('beforeend',h);
  document.getElementById('logN').textContent=list.length.toLocaleString()+' shown';
  if(shown<list.length) body.insertAdjacentHTML('beforeend',`<button class="more-btn" id="logMore" onclick="moreLog()">${{Math.min(STEP,list.length-shown)}} MORE ▾</button>`);
}}
</script>
</body>
</html>"""


def _kpi(num: str, lbl: str) -> str:
    return f'<div class="kpi"><div class="kpi-num">{num}</div><div class="kpi-lbl">{_esc(lbl)}</div></div>'


def _trip_cover(t: dict, pix_base: str) -> str:
    """Card cover, the same pick as trips.html: a photo from the middle of the trip."""
    shots = [c["photos"][0] for c in t.get("checkins") or [] if c.get("photos")]
    if not shots or not pix_base:
        return '<div class="trip-cover none"></div>'
    return (f'<div class="trip-cover" style="background-image:url(\'{pix_base}/t/'
            f'{_esc(shots[len(shots) // 2])}\')"></div>')


def _dot_map(crows: list[dict], w: int = 920, h: int = 380, label_key: str = "city") -> str:
    """A tile-free map: one dot per venue (area ~ visits), the top cities (or
    neighbourhoods, label_key) labelled.

    Plain equirectangular with a cos(lat) squeeze, fitted to the box; the 1st-99th
    percentile window keeps one far-flung exclave from shrinking the rest to a dot.
    """
    pts: Counter = Counter()
    city_pts: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in crows:
        la, lo = _f(r.get("lat")), _f(r.get("lng"))
        if la is None or lo is None or (la == 0 and lo == 0):
            continue
        pts[(round(la, 4), round(lo, 4))] += 1
        cy = (r.get(label_key) or "").strip() if label_key else ""
        if cy:
            city_pts[cy].append((la, lo))
    if len(pts) < 2:
        return ""
    lats = sorted(p[0] for p in pts)
    lngs = sorted(p[1] for p in pts)
    cut = len(lats) // 100

    def window(v: list[float]) -> tuple[float, float]:
        return v[cut], v[len(v) - 1 - cut]
    la0, la1 = window(lats)
    lo0, lo1 = window(lngs)
    import math
    k = math.cos(math.radians((la0 + la1) / 2))
    span_x = max((lo1 - lo0) * k, 0.02)
    span_y = max(la1 - la0, 0.02)
    pad = 26
    s = min((w - 2 * pad) / span_x, (h - 2 * pad) / span_y)
    ox = (w - span_x * s) / 2
    oy = (h - span_y * s) / 2

    def xy(la: float, lo: float) -> tuple[float, float]:
        return ox + (lo - lo0) * k * s, oy + (la1 - la) * s
    top = max(pts.values())
    dots = []
    for (la, lo), n in sorted(pts.items(), key=lambda kv: kv[1]):
        x, y = xy(la, lo)
        if not (-10 <= x <= w + 10 and -10 <= y <= h + 10):
            continue
        rad = 1.6 + 7 * math.sqrt(n / top)
        hot = ' class="hot"' if n * 4 >= top else ""   # no backslash in an f-string: CI may run 3.11
        dots.append(f'<circle{hot} cx="{x:.1f}" cy="{y:.1f}" r="{rad:.1f}"/>')
    labels = []
    placed: list[tuple[float, float]] = []
    for cy, cp in sorted(city_pts.items(), key=lambda kv: -len(kv[1]))[:6]:
        x, y = xy(sum(p[0] for p in cp) / len(cp), sum(p[1] for p in cp) / len(cp))
        if any(abs(x - px) < 130 and abs(y - py) < 16 for px, py in placed):
            continue          # would print on top of a busier label
        placed.append((x, y))
        if 0 <= x <= w and 0 <= y <= h:
            anchor = "end" if x > w - 120 else "start"
            labels.append(f'<text x="{x + (-10 if anchor == "end" else 10):.1f}" y="{y + 4:.1f}" '
                          f'text-anchor="{anchor}">{_esc(cy)}</text>')
    return ('<section><div class="sec-h">Where it happened</div><div class="cmap">'
            f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Map of check-ins">'
            + "".join(dots) + "".join(labels) + "</svg></div></section>")


def _rank_list(pairs: list[tuple[str, int]], hrefs: list[str] | None = None) -> str:
    rows = []
    for i, (name, n) in enumerate(pairs, 1):
        href = hrefs[i - 1] if hrefs else ""
        label = (f'<a href="{_esc(href)}">{_esc(name)}</a>'
                 if href else _esc(name))
        rows.append(
            f'<div class="rank-row"><span class="rank-i">{i}</span>'
            f'<span class="rank-name" title="{_esc(name)}">{label}</span>'
            f'<span class="rank-n">{n:,}</span></div>')
    return '<div class="rank-list">' + "".join(rows) + "</div>"


def _ts(r: dict) -> int:
    try:
        return int(r.get("date", 0) or 0)
    except (ValueError, TypeError):
        return 0


def _home_at(ts: int, home_city: str, home_history: list[tuple[int, str]]) -> str:
    """Same rule as metrics.trips._home_at: an entry's city is home strictly before its until."""
    for until_ts, city in home_history:
        if ts < until_ts:
            return city
    return home_city


def _home_spans(city: str, home_city: str, home_history: list[tuple[int, str]],
                now_year: int) -> list[tuple[int, int]]:
    """(first_year, last_year) of every era the city was home, oldest first; 0 = since
    the start of the data. Years are clamped to now (the open era ends far ahead)."""
    eras, start = [], 0
    for until_ts, c in home_history:
        eras.append((start, until_ts, c))
        start = until_ts
    eras.append((start, 0, home_city))
    spans: list[tuple[int, int]] = []
    for t0, t1, c in eras:
        if c != city:
            continue
        y0 = datetime.fromtimestamp(t0, tz=timezone.utc).year if t0 else 0
        y1 = min(now_year, datetime.fromtimestamp(t1 - 1, tz=timezone.utc).year) if t1 else now_year
        if y0 > now_year:
            continue
        if spans and y0 <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], y1))
        else:
            spans.append((y0, y1))
    return spans


_CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяіўєї",
                ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
                 "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "-", "y", "-", "e", "yu", "ya", "i",
                 "u", "ye", "yi"], strict=True))


_LATIN = {"ł": "l", "ø": "o", "ß": "ss", "æ": "ae", "œ": "oe", "đ": "d", "ð": "d", "þ": "th", "ı": "i", "ħ": "h"}


def city_slug(city: str) -> str:
    """ASCII slug of a city name (accents folded, Cyrillic transliterated)."""
    import unicodedata
    out = []
    for ch in unicodedata.normalize("NFKD", city.lower()):
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            out.append(ch)
        elif ch in _CYR:
            out.append(_CYR[ch].strip("-"))
        elif ch in _LATIN:
            out.append(_LATIN[ch])
        elif not unicodedata.combining(ch):
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def city_slugs(cities: list[tuple[str, str]]) -> dict[str, str]:
    """{city: slug} for (city, country) pairs, busiest first; a clash gets the country code."""
    taken: set[str] = set()
    out: dict[str, str] = {}
    for city, country in cities:
        base = city_slug(city) or "city"
        slug = base
        if slug in taken:
            slug = f"{base}-{(CTRY_CODE.get(country) or country_slug(country)).lower()}"
        n = 2
        while slug in taken:
            slug, n = f"{base}-{n}", n + 1
        taken.add(slug)
        out[city] = slug
    return out


def _render(
    *,
    kind: str,
    name: str,
    slug: str,
    crows: list[dict],
    country: str,
    out_dir: Path,
    year_span: list[int],
    trips_here: list[dict],
    photos_by_checkin: dict,
    pix_base: str,
    local: _Localiser,
    city_links: dict[str, str],
    home_spans: list[tuple[int, int]] | None = None,
    home_years: set[int] | None = None,
) -> None:
    """One country-<slug>.html or city-<slug>.html page plus its log JSON."""
    is_city = kind == "city"
    tss = sorted(t for t in (_ts(r) for r in crows) if t)

    venues: Counter = Counter()
    venue_names: dict[str, str] = {}
    venue_links: dict[str, str] = {}
    places: Counter = Counter()          # cities on a country page, neighbourhoods on a city page
    cats: Counter = Counter()
    by_year: Counter = Counter()
    days = set()
    for r in crows:
        vid = (r.get("venue_id") or "").strip()
        vname = (r.get("venue") or "").strip()
        if vname:
            key = vid or vname
            venues[key] += 1
            venue_names.setdefault(key, vname)
            if vid:
                venue_links[key] = f"/venue.html#{vid}"   # gen_venue_page dossier
        pl = (r.get("neighborhood" if is_city else "city") or "").strip()
        if pl:
            places[pl] += 1
        cat = (r.get("category") or "").strip()
        if cat:
            cats[cat] += 1
        ts = _ts(r)
        if ts:
            d = datetime.fromtimestamp(ts, tz=timezone.utc)
            by_year[d.year] += 1
            days.add(d.date())

    sub = []
    if is_city and country:
        sub.append(f'<a href="/country-{country_slug(country)}.html">{_esc(country)}</a>')
    if tss:
        sub.append(_esc(f"first visit {_fmt_date(tss[0])} · last {_fmt_date(tss[-1])}"))
    for y0, y1 in home_spans or []:
        sub.append(f'<b class="home-tag">home {y0}–{y1}</b>' if y1 != y0 else f'<b class="home-tag">home {y0}</b>')

    kpis = (
        _kpi(f"{len(crows):,}", "check-ins")
        + _kpi(f"{len(venues):,}", "venues")
        + (_kpi(f"{len(by_year):,}", "years") if is_city else _kpi(f"{len(places):,}", "cities"))
        + _kpi(f"{len(days):,}", "days")
    )

    # Year bars (home years tinted on a city page)
    years_html = ""
    if by_year:
        ymax = max(by_year.values())
        bars = []
        for yr in year_span or sorted(by_year):
            n = by_year.get(yr, 0)
            h = max(3, round(84 * n / ymax))
            cls = ("" if n else " zero") + (" home" if home_years and yr in home_years and n else "")
            bars.append(
                f'<div class="yr-bar{cls}"><span class="yr-n">{n}</span>'
                f'<div class="bar" style="height:{h}px"></div>'
                f'<span class="yr-lbl">{yr}</span></div>')
        legend = ('<span class="legend"><i></i>lived here</span>'
                  if home_years and any(by_year.get(y) for y in home_years) else "")
        years_html = (f'<section><div class="sec-h">By year{legend}</div>'
                      '<div class="years">' + "".join(bars) + "</div></section>")

    # Cities (or neighbourhoods) / venues / categories columns
    top_places = places.most_common(10)
    # Foursquare fills neighbourhoods for some cities only (Chișinău yes, Minsk barely)
    hoods_ok = not is_city or sum(n for _, n in top_places) * 4 >= len(crows)
    top_venue_keys = [k for k, _ in venues.most_common(10)]
    top_venues = [(venue_names[k], venues[k]) for k in top_venue_keys]
    cols = []
    if top_places and hoods_ok and (not is_city or len(places) > 1):
        hrefs = None if is_city else [f"/city-{city_links[c]}.html" if c in city_links else ""
                                      for c, _ in top_places]
        cols.append(f'<div><div class="sec-h">{"Neighbourhoods" if is_city else "Top cities"}</div>'
                    + _rank_list(top_places, hrefs) + "</div>")
    if top_venues:
        cols.append('<div><div class="sec-h">Top venues</div>'
                    + _rank_list(top_venues, [venue_links.get(k, "") for k in top_venue_keys])
                    + "</div>")
    if cats:
        cols.append('<div><div class="sec-h">Categories</div>'
                    + _rank_list(cats.most_common(10)) + "</div>")
    cols_html = ('<section><div class="cols">' + "".join(cols)
                 + "</div></section>") if cols else ""

    cmap_html = _dot_map(crows, label_key="city" if not is_city else "neighborhood" if hoods_ok else "")

    # Trips
    trips_html = ""
    if trips_here:
        ctrips = sorted(trips_here, key=lambda t: t.get("start_date", ""), reverse=True)
        cards = []
        for k, t in enumerate(ctrips):
            cards.append(
                f'<a class="trip-card{" more" if k >= 6 else ""}" href="/trips.html#trip-{_esc(t.get("id"))}">'
                + _trip_cover(t, pix_base) +
                f'<div class="trip-body"><div class="trip-name">{_esc(t.get("name", "Trip"))}</div>'
                f'<div class="trip-meta">{_esc(t.get("start_date", ""))} → '
                f'{_esc(t.get("end_date", ""))} · {t.get("duration", 0)} day{"" if t.get("duration", 0) == 1 else "s"} · '
                f'{t.get("checkin_count", 0)} check-ins</div></div></a>')
        more = (f'<button class="more-btn" onclick="moreTrips(this)">ALL {len(ctrips)} TRIPS ▾</button>'
                if len(ctrips) > 6 else "")
        trips_html = (f'<section><div class="sec-h">Trips here · {len(ctrips)}</div>'
                      '<div class="trips">' + "".join(cards) + "</div>" + more + "</section>")

    # Photos (up to 12); the hero borrows one from the middle of the page's photos
    photos_html = ""
    hero_style, hero_cls = "", " none"
    if pix_base:
        shots = [fn for r in sorted(crows, key=_ts)
                 for fn in (photos_by_checkin.get((r.get("checkin_id") or "").strip()) or [])[:1]]
        if shots:
            hero_style = f' style="background-image:url(\'{pix_base}/t/{_esc(shots[len(shots) // 2])}\')"'
            hero_cls = ""
        imgs = []
        for r in sorted(crows, key=_ts, reverse=True):
            cid = (r.get("checkin_id") or "").strip()
            for fn in (photos_by_checkin.get(cid) or []):
                imgs.append(f'<img loading="lazy" src="{pix_base}/t/{_esc(fn)}" '
                            f'alt="{_esc(r.get("venue", ""))}">')
                if len(imgs) >= 12:
                    break
            if len(imgs) >= 12:
                break
        if imgs:
            photos_html = ('<section><div class="sec-h">Photos</div>'
                           '<div class="photos">' + "".join(imgs) + "</div></section>")

    # Shouts: one per year for the six most recent years that have one, the
    # one nearest a readable length (the 3 longest were pasted walls of text)
    shouts_html = ""
    best: dict[int, tuple[int, dict, str]] = {}
    for r in crows:
        s = clean_shout(r.get("shout") or "", [])
        if not 20 <= len(s) <= 260 or s.lower().startswith(("with ", "- with")):
            continue
        try:
            yr = datetime.fromtimestamp(_ts(r), tz=timezone.utc).year
        except (ValueError, OSError):
            continue
        score = abs(len(s) - 110)
        if yr not in best or score < best[yr][0]:
            best[yr] = (score, r, s)
    if best:
        blocks = []
        for yr in sorted(best, reverse=True)[:6]:
            _, r, s = best[yr]
            loc = ", ".join(p for p in ((r.get("venue") or "").strip(),
                                        (r.get("city") or "").strip()) if p)
            blocks.append(f'<div class="shout">“{_esc(s)}”'
                          f'<div class="shout-meta">{yr} · {_esc(loc)}</div></div>')
        shouts_html = ('<section><div class="sec-h">In your own voice</div><div class="shouts">'
                       + "".join(blocks) + "</div></section>")

    # The full log: rows in their own file, loaded on the first open
    log_rows = []
    for r in crows:
        try:
            dt = datetime.fromtimestamp(_ts(r), tz=timezone.utc)
        except (ValueError, OSError):
            continue
        ld = local(dt, _f(r.get("lat")), _f(r.get("lng")), (r.get("country") or "").strip())
        log_rows.append([ld.strftime("%Y-%m-%d %H:%M"), (r.get("venue") or "").strip(),
                         (r.get("neighborhood" if is_city else "city") or "").strip() if hoods_ok else "",
                         (r.get("category") or "").strip(),
                         (r.get("venue_id") or "").strip(),
                         1 if (r.get("source_app") or "") == "refurbished" else 0])
    log_rows.sort(key=lambda x: x[0], reverse=True)
    log_file = f"{kind}_ck_{slug}.json"
    (out_dir / log_file).write_text(json.dumps({"r": log_rows}, ensure_ascii=False, separators=(",", ":")),
                                    encoding="utf-8")
    log_html = (f'<section><button class="log-btn" onclick="toggleLog()"><span>All {len(crows):,} check-ins '
                f'in {_esc(name)}, searchable</span><b id="logLbl">OPEN ▾</b></button>'
                '<div class="log" id="log"><input type="search" placeholder="Search venues, '
                f'{"neighbourhoods" if is_city else "cities"}, categories…" '
                'oninput="filterLog(this.value)"><div class="log-n" id="logN"></div><div id="logBody"></div></div></section>')

    title = f"{name} — Check-in Journal"
    if is_city:
        desc = f"{len(crows):,} check-ins at {len(venues):,} venues in {name}" + (f", {country}." if country else ".")
        crumb = (f"/country-{country_slug(country)}.html", f"← {country}") if country else ("/index.html", "← home")
    else:
        desc = (f"{len(crows):,} check-ins across {len(places):,} cities and "
                f"{len(venues):,} venues in {name}.")
        crumb = ("/index.html#s-countries", "← all countries")
    html = PAGE_TEMPLATE.format(
        title=_esc(title), description=_esc(desc), site=SITE_URL, page=f"{kind}-{slug}.html",
        crumb_href=crumb[0], crumb_label=_esc(crumb[1]),
        flag_big=_flag(country), name=_esc(name), sub=" · ".join(sub),
        kpis=kpis, years=years_html, cmap=cmap_html, hero_style=hero_style, hero_cls=hero_cls,
        cities=cols_html, venues="", cats="",
        trips=trips_html, photos=photos_html, shouts=shouts_html, log=log_html,
        log_url=json.dumps(f"/{log_file}"))
    (out_dir / f"{kind}-{slug}.html").write_text(html, encoding="utf-8")


def build_page(
    csv_path: str = "",
    config_dir: str = "",
    out_path: str = "",
    tmpl_path: str = "",
    rows: list | None = None,
    stats_data: dict | None = None,
    photos_by_checkin: dict | None = None,
    pix_url: str = "",
    trips: list | None = None,
    min_checkins: int = 1,
    city_min_checkins: int = CITY_MIN_CHECKINS,
    home_city: str = "",
    home_history: list[tuple[int, str]] | None = None,
    **_extra,
) -> int:
    """Write country-<slug>.html for every country and city-<slug>.html for every
    city with at least `city_min_checkins` check-ins. Returns the page count."""
    out_dir = Path(out_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    _load_flags(Path(config_dir))

    rows = rows or []
    trips = trips or []
    photos_by_checkin = photos_by_checkin or {}
    home_history = sorted(home_history or [])

    by_country: dict[str, list[dict]] = defaultdict(list)
    by_city: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        c = (r.get("country") or "").strip()
        if c:
            by_country[c].append(r)
        cy = (r.get("city") or "").strip()
        if cy:
            by_city[cy].append(r)

    trips_by_country: dict[str, list[dict]] = defaultdict(list)
    trips_by_city: dict[str, list[dict]] = defaultdict(list)
    for t in trips:
        for c in (t.get("countries") or []):
            trips_by_country[c].append(t)
        for cy in (t.get("cities") or []):
            # a trip "to" the city it started from (home at the time) is not a visit
            if _home_at(int(t.get("start_ts") or 0), home_city, home_history) != cy:
                trips_by_city[cy].append(t)

    pix_base = pix_url.rstrip("/") if pix_url else ""
    all_years = {y for y in (datetime.fromtimestamp(t, tz=timezone.utc).year
                             for t in (_ts(r) for r in rows) if t)}
    year_span = list(range(min(all_years), max(all_years) + 1)) if all_years else []
    local = _Localiser()
    for stale in [*out_dir.glob("country_ck_*.json"), *out_dir.glob("city_ck_*.json"),
                  *out_dir.glob("city-*.html")]:
        stale.unlink()

    # The city's own country: where most of its check-ins are
    city_country = {cy: Counter((r.get("country") or "").strip() for r in crs).most_common(1)[0][0]
                    for cy, crs in by_city.items()}
    big = sorted((cy for cy, crs in by_city.items() if len(crs) >= city_min_checkins),
                 key=lambda cy: -len(by_city[cy]))
    slugs = city_slugs([(cy, city_country[cy]) for cy in big])

    common: dict[str, Any] = {"out_dir": out_dir, "year_span": year_span, "photos_by_checkin": photos_by_checkin,
                              "pix_base": pix_base, "local": local, "city_links": slugs}
    n_pages = 0
    for country, crows in by_country.items():
        if len(crows) < min_checkins:
            continue
        _render(kind="country", name=country, slug=country_slug(country), crows=crows, country=country,
                trips_here=trips_by_country.get(country, []), **common)
        n_pages += 1

    now_year = year_span[-1] if year_span else datetime.now(timezone.utc).year
    for cy in big:
        crows = by_city[cy]
        first_year = min((datetime.fromtimestamp(_ts(r), tz=timezone.utc).year for r in crows if _ts(r)),
                         default=now_year)
        spans = [(y0 or first_year, y1) for y0, y1 in _home_spans(cy, home_city, home_history, now_year)]
        home_years = {datetime.fromtimestamp(_ts(r), tz=timezone.utc).year for r in crows
                      if _ts(r) and _home_at(_ts(r), home_city, home_history) == cy}
        _render(kind="city", name=cy, slug=slugs[cy], crows=crows, country=city_country[cy],
                trips_here=trips_by_city.get(cy, []), home_spans=spans, home_years=home_years, **common)
        n_pages += 1

    if stats_data is not None:
        stats_data["city_pages"] = slugs     # {city: slug} for the pages that link here
    return n_pages
