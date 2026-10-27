#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_country_pages.py — per-country detail pages (audit #10).

Emits one `country-<slug>.html` per country that appears in the check-in data.
Each page is a clean, self-contained magazine card (no side-nav, no external
JS) summarising everything that happened in that country:

  * hero — flag, name, headline KPIs (check-ins, venues, cities, first/last)
  * a by-year activity bar strip
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

from metrics.venues import _f, _Localiser, clean_shout

CTRY_CODE: dict[str, str] = {}

SITE_URL = "https://4sq.pages.dev"
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
<link rel="canonical" href="{site}/country-{slug}.html">
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
</style>
</head>
<body>
<a class="crumb" href="/index.html#s-countries">← all countries</a>
<div class="wrap">
<div class="hero-flag">{flag_big}</div>
<h1 class="hero-name">{name}</h1>
<div class="hero-sub">{first_last}</div>
<div class="kpis">{kpis}</div>
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
// The full log loads its own file (country_ck_<slug>.json) on the first open.
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
    **_extra,
) -> int:
    """Write country-<slug>.html for every country. Returns the page count."""
    out_dir = Path(out_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    _load_flags(Path(config_dir))

    rows = rows or []
    trips = trips or []
    photos_by_checkin = photos_by_checkin or {}

    # Group rows by country
    by_country: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        c = (r.get("country") or "").strip()
        if c:
            by_country[c].append(r)

    # Trips touching each country
    trips_by_country: dict[str, list[dict]] = defaultdict(list)
    for t in trips:
        for c in (t.get("countries") or []):
            trips_by_country[c].append(t)

    pix_base = pix_url.rstrip("/") if pix_url else ""
    local = _Localiser()
    for stale in out_dir.glob("country_ck_*.json"):
        stale.unlink()
    n_pages = 0
    for country, crows in by_country.items():
        if len(crows) < min_checkins:
            continue
        slug = country_slug(country)

        # Timestamps
        tss = []
        for r in crows:
            try:
                ts = int(r.get("date", 0) or 0)
            except (ValueError, TypeError):
                ts = 0
            if ts:
                tss.append(ts)
        tss.sort()

        venues = Counter()
        venue_names: dict[str, str] = {}
        venue_links: dict[str, str] = {}
        cities = Counter()
        cats = Counter()
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
            cy = (r.get("city") or "").strip()
            if cy:
                cities[cy] += 1
            cat = (r.get("category") or "").strip()
            if cat:
                cats[cat] += 1
            try:
                ts = int(r.get("date", 0) or 0)
            except (ValueError, TypeError):
                ts = 0
            if ts:
                d = datetime.fromtimestamp(ts, tz=timezone.utc)
                by_year[d.year] += 1
                days.add(d.date())

        first_last = ""
        if tss:
            first_last = f"first visit {_fmt_date(tss[0])} · last {_fmt_date(tss[-1])}"

        kpis = (
            _kpi(f"{len(crows):,}", "check-ins")
            + _kpi(f"{len(venues):,}", "venues")
            + _kpi(f"{len(cities):,}", "cities")
            + _kpi(f"{len(days):,}", "days")
        )

        # Year bars
        years_html = ""
        if by_year:
            ymax = max(by_year.values())
            bars = []
            for yr in sorted(by_year):
                n = by_year[yr]
                h = max(4, round(84 * n / ymax))
                bars.append(
                    f'<div class="yr-bar"><span class="yr-n">{n}</span>'
                    f'<div class="bar" style="height:{h}px"></div>'
                    f'<span class="yr-lbl">{yr}</span></div>')
            years_html = ('<section><div class="sec-h">By year</div>'
                          '<div class="years">' + "".join(bars) + "</div></section>")

        # Cities / venues / categories columns
        top_cities = [(n, c) for n, c in cities.most_common(10)]
        top_venue_keys = [k for k, _ in venues.most_common(10)]
        top_venues = [(venue_names[k], venues[k]) for k in top_venue_keys]
        top_cats = [(n, c) for n, c in cats.most_common(10)]
        cols = []
        if top_cities:
            cols.append('<div><div class="sec-h">Top cities</div>'
                        + _rank_list(top_cities) + "</div>")
        if top_venues:
            cols.append('<div><div class="sec-h">Top venues</div>'
                        + _rank_list(top_venues, [venue_links.get(k, "") for k in top_venue_keys])
                        + "</div>")
        if top_cats:
            cols.append('<div><div class="sec-h">Categories</div>'
                        + _rank_list(top_cats) + "</div>")
        cities_html = ('<section><div class="cols">' + "".join(cols)
                       + "</div></section>") if cols else ""

        # Trips
        trips_html = ""
        ctrips = trips_by_country.get(country, [])
        if ctrips:
            ctrips = sorted(ctrips, key=lambda t: t.get("start_date", ""), reverse=True)
            cards = []
            for k, t in enumerate(ctrips):
                cards.append(
                    f'<a class="trip-card{" more" if k >= 6 else ""}" href="/trips.html#trip-{_esc(t.get("id"))}">'
                    f'<div class="trip-name">{_esc(t.get("name", "Trip"))}</div>'
                    f'<div class="trip-meta">{_esc(t.get("start_date", ""))} → '
                    f'{_esc(t.get("end_date", ""))} · {t.get("duration", 0)} day{"" if t.get("duration", 0) == 1 else "s"} · '
                    f'{t.get("checkin_count", 0)} check-ins</div></a>')
            more = (f'<button class="more-btn" onclick="moreTrips(this)">ALL {len(ctrips)} TRIPS ▾</button>'
                    if len(ctrips) > 6 else "")
            trips_html = (f'<section><div class="sec-h">Trips here · {len(ctrips)}</div>'
                          '<div class="trips">' + "".join(cards) + "</div>" + more + "</section>")

        # Photos (up to 12)
        photos_html = ""
        if pix_base:
            imgs = []
            for r in sorted(crows, key=lambda r: int(r.get("date", 0) or 0), reverse=True):
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
                yr = datetime.fromtimestamp(int(r.get("date", 0) or 0), tz=timezone.utc).year
            except (ValueError, TypeError, OSError):
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
                dt = datetime.fromtimestamp(int(r.get("date", 0) or 0), tz=timezone.utc)
            except (ValueError, TypeError, OSError):
                continue
            ld = local(dt, _f(r.get("lat")), _f(r.get("lng")), country)
            log_rows.append([ld.strftime("%Y-%m-%d %H:%M"), (r.get("venue") or "").strip(),
                             (r.get("city") or "").strip(), (r.get("category") or "").strip(),
                             (r.get("venue_id") or "").strip(),
                             1 if (r.get("source_app") or "") == "refurbished" else 0])
        log_rows.sort(key=lambda x: x[0], reverse=True)
        log_file = f"country_ck_{slug}.json"
        (out_dir / log_file).write_text(json.dumps({"r": log_rows}, ensure_ascii=False, separators=(",", ":")),
                                        encoding="utf-8")
        log_html = (f'<section><button class="log-btn" onclick="toggleLog()"><span>All {len(crows):,} check-ins '
                    f'in {_esc(country)}, searchable</span><b id="logLbl">OPEN ▾</b></button>'
                    '<div class="log" id="log"><input type="search" placeholder="Search venues, cities, categories…" '
                    'oninput="filterLog(this.value)"><div class="log-n" id="logN"></div><div id="logBody"></div></div></section>')

        title = f"{country} — Check-in Journal"
        desc = (f"{len(crows):,} check-ins across {len(cities):,} cities and "
                f"{len(venues):,} venues in {country}.")
        html = PAGE_TEMPLATE.format(
            title=_esc(title), description=_esc(desc), site=SITE_URL, slug=slug,
            flag_big=_flag(country), name=_esc(country), first_last=_esc(first_last),
            kpis=kpis, years=years_html, cities=cities_html, venues="", cats="",
            trips=trips_html, photos=photos_html, shouts=shouts_html, log=log_html,
            log_url=json.dumps(f"/{log_file}"))
        (out_dir / f"country-{slug}.html").write_text(html, encoding="utf-8")
        n_pages += 1

    return n_pages
