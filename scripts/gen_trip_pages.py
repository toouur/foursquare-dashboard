# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
gen_trip_pages.py — Generate one HTML page per trip with full check-in timeline + photos.

Each page is a self-contained local file:
  trip-{id}.html

Photos are referenced as file:/// absolute paths into the pix/ folder next to checkins.csv.
If no photos path is provided, the pages are still generated (photo sections are omitted).

Called by build.py when --photos path is supplied.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ICONS = ['✈️', '🚂', '🚌', '🚗', '⛺', '🛁', '⛴️', '🚲']

_CAT_ICON: dict[str, tuple[str, str]] = {
    'Coffee Shop': ('☕', '#6F4E37'), 'Café': ('☕', '#6F4E37'),
    'Restaurant': ('🍽️', '#C0392B'), 'Fast Food Restaurant': ('🍔', '#E67E22'),
    'Bar': ('🍺', '#F39C12'), 'Pub': ('🍻', '#D4A017'),
    'Hotel': ('🏨', '#2C3E50'), 'Hostel': ('🛏️', '#34495E'),
    'Home (private)': ('🏠', '#17202A'),
    'Metro Station': ('🚇', '#2980B9'), 'Rail Station': ('🚉', '#1A5276'),
    'Airport': ('✈️', '#154360'), 'Bus Station': ('🚌', '#1E8449'),
    'Fuel Station': ('⛽', '#E67E22'), 'Parking': ('🅿️', '#2980B9'),
    'Park': ('🌳', '#1E8449'), 'Museum': ('🏛️', '#2E86C1'),
    'Supermarket': ('🛒', '#219A52'), 'Grocery Store': ('🛒', '#27AE60'),
    'Neighborhood': ('🏘️', '#5D6D7E'),
}

def _cat_icon(cat: str) -> tuple[str, str]:
    return _CAT_ICON.get(cat, ('📍', '#4a5270'))


_TRANSPORT_ICONS = ['✈️', '🚂', '🚌', '🚗', '⛺', '🛁', '⛴️']

def _strip_icon(name: str) -> tuple[str, str]:
    for ic in _TRANSPORT_ICONS:
        if name.endswith(' ' + ic):
            return name[:-len(ic)-1], ic
    return name, ''


def build_page(trips: list[dict], photos_by_checkin: dict[str, list[str]],
               pix_dir: str, out_dir: str) -> None:
    """Generate trip-{id}.html for every trip that has check-ins."""
    os.makedirs(out_dir, exist_ok=True)
    pix_path = Path(pix_dir).resolve() if pix_dir else None
    generated = 0

    for trip in trips:
        tid = trip.get('id')
        if not tid:
            continue
        checkins = trip.get('checkins', [])
        if not checkins:
            continue

        # Figure out how many check-ins in this trip have photos
        photo_count = sum(
            len(photos_by_checkin.get(c.get('checkin_id', ''), []))
            for c in checkins
        )

        html = _render_trip_page(trip, checkins, photos_by_checkin, pix_path, photo_count)
        out_path = Path(out_dir) / f'trip-{tid}.html'
        out_path.write_text(html, encoding='utf-8')
        generated += 1

    print(f'trip pages -> {out_dir}  ({generated} pages)', file=sys.stderr)


def _render_trip_page(trip: dict, checkins: list[dict],
                      photos_by_checkin: dict[str, list[str]],
                      pix_path: Path | None,
                      photo_count: int) -> str:
    name_raw = trip.get('name', '')
    base_name, icon = _strip_icon(name_raw)
    tags = trip.get('tags', [])
    if 'bicycle' in tags:
        icon = '🚲'

    start_date = trip.get('start_date', '')
    end_date   = trip.get('end_date', '')
    duration   = trip.get('duration', 0)
    countries  = trip.get('countries', [])
    cities     = trip.get('cities', [])
    tid        = trip.get('id', '')
    checkin_count = trip.get('checkin_count', len(checkins))
    unique_places = trip.get('unique_places', 0)
    top_cats   = trip.get('top_cats', [])

    # Group by day
    by_day: dict[str, list[dict]] = {}
    for c in checkins:
        by_day.setdefault(c['date'], []).append(c)

    # Build timeline HTML
    timeline_html = ''
    for day in sorted(by_day.keys()):
        day_checkins = by_day[day]
        rows_html = ''
        for c in day_checkins:
            em, bg = _cat_icon(c.get('category', ''))
            cid = c.get('checkin_id', '')
            photos = photos_by_checkin.get(cid, []) if pix_path else []
            vid = c.get('venue_id', '')
            venue = c.get('venue', '')
            fsurl = f'https://app.foursquare.com/v/{venue.lower().replace(" ", "-")}/{vid}' if vid else ''
            sub = ' · '.join(filter(None, [c.get('category', ''), c.get('city', ''), c.get('country', '')]))

            photos_html = ''
            if photos:
                imgs = ''
                for fname in photos:
                    fpath = (pix_path / fname).as_uri() if pix_path else ''
                    imgs += f'<img class="ci-photo" src="{fpath}" loading="lazy" onclick="openLightbox(this.src)">'
                photos_html = f'<div class="ci-photos">{imgs}</div>'

            venue_link = f'<a href="{fsurl}" target="_blank" rel="noopener">{_esc(venue)}</a>' if fsurl else _esc(venue)
            rows_html += f'''<div class="ci-row">
  <div class="ci-time">{c.get("time","")}</div>
  <div class="ci-icon" style="background:{bg}22;border:1px solid {bg}55">{em}</div>
  <div class="ci-body">
    <div class="ci-venue">{venue_link}</div>
    <div class="ci-sub">{_esc(sub)}</div>
    {photos_html}
  </div>
</div>'''

        timeline_html += f'''<div class="day-block">
  <div class="day-header">{day}<span class="day-count">{len(day_checkins)} check-ins</span></div>
  {rows_html}
</div>'''

    # Category bar chart
    max_cat = top_cats[0][1] if top_cats else 1
    cats_html = ''.join(
        f'<div class="cat-row"><span class="cat-name">{_esc(cat)}</span>'
        f'<div class="cat-track"><div class="cat-fill" style="width:{cnt/max_cat*100:.1f}%"></div></div>'
        f'<span class="cat-cnt">{cnt}</span></div>'
        for cat, cnt in top_cats
    )

    country_flags = ''.join(f'<span class="cc">{c}</span>' for c in countries)
    city_chips    = ''.join(f'<span class="cc city">{c}</span>' for c in cities[:12])

    photo_note = f'<span class="kpi-note">📸 {photo_count:,} photos</span>' if photo_count else ''

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{_esc(base_name)} – Trip Journal</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{{--bg:#0b0d13;--card:#12151f;--card2:#181c28;--border:#222738;--gold:#e8b86d;--teal:#4ecdc4;--muted:#4a5270;--text:#cdd5f0;--text2:#7a85a8;}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;}}
a{{color:inherit;text-decoration:none;}}
a:hover{{color:var(--gold);}}

.topnav{{display:flex;align-items:center;gap:20px;padding:18px 48px;border-bottom:1px solid var(--border);background:var(--card);position:sticky;top:0;z-index:100;}}
.topnav-logo{{font-family:'Playfair Display',serif;font-size:1.1rem;font-weight:700;color:var(--gold);}}
.back-link{{font-family:'DM Mono',monospace;font-size:.62rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);transition:color .2s;}}
.back-link:hover{{color:var(--gold);}}

.hero{{padding:48px 48px 32px;border-bottom:1px solid var(--border);}}
.hero-title{{font-family:'Playfair Display',serif;font-size:clamp(1.8rem,4vw,3rem);font-weight:900;background:linear-gradient(130deg,#f5d48a 0%,#e8b86d 45%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:4px;}}
.hero-icon{{font-size:1.8rem;margin-left:10px;vertical-align:middle;}}
.hero-dates{{font-family:'DM Mono',monospace;font-size:.72rem;color:var(--muted);margin-bottom:20px;}}
.kpis{{display:flex;gap:28px;flex-wrap:wrap;margin-bottom:16px;}}
.kpi{{text-align:center;}}
.kpi-v{{font-family:'Playfair Display',serif;font-size:1.6rem;font-weight:700;color:var(--gold);}}
.kpi-l{{font-family:'DM Mono',monospace;font-size:.56rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);}}
.kpi-note{{font-family:'DM Mono',monospace;font-size:.65rem;color:var(--teal);align-self:center;}}
.chips{{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;}}
.cc{{padding:4px 12px;border-radius:6px;font-size:.75rem;background:rgba(232,184,109,.08);border:1px solid rgba(232,184,109,.18);color:var(--text2);}}
.cc.city{{background:rgba(78,205,196,.05);border-color:rgba(78,205,196,.15);color:var(--text2);}}

.layout{{display:grid;grid-template-columns:1fr 280px;gap:0;max-width:1400px;margin:0 auto;}}
@media(max-width:900px){{.layout{{grid-template-columns:1fr;}} .sidebar{{display:none;}}}}

.timeline{{padding:32px 48px;}}
.day-block{{margin-bottom:32px;}}
.day-header{{font-family:'DM Mono',monospace;font-size:.65rem;text-transform:uppercase;letter-spacing:.12em;color:var(--gold);padding-bottom:10px;border-bottom:1px solid var(--border);margin-bottom:12px;display:flex;align-items:center;gap:12px;}}
.day-count{{color:var(--muted);font-size:.58rem;}}

.ci-row{{display:flex;gap:10px;padding:8px 0;align-items:flex-start;}}
.ci-time{{font-family:'DM Mono',monospace;font-size:.60rem;color:var(--muted);width:40px;flex-shrink:0;padding-top:3px;}}
.ci-icon{{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;flex-shrink:0;}}
.ci-body{{flex:1;min-width:0;}}
.ci-venue{{font-size:.85rem;font-weight:600;margin-bottom:2px;}}
.ci-venue a{{color:var(--text);}} .ci-venue a:hover{{color:var(--gold);}}
.ci-sub{{font-size:.68rem;color:var(--muted);margin-bottom:6px;}}
.ci-photos{{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}}
.ci-photo{{width:120px;height:90px;object-fit:cover;border-radius:6px;cursor:pointer;transition:transform .15s,opacity .15s;border:1px solid var(--border);}}
.ci-photo:hover{{transform:scale(1.03);opacity:.9;}}

.sidebar{{padding:32px 24px;border-left:1px solid var(--border);position:sticky;top:57px;height:calc(100vh - 57px);overflow-y:auto;}}
.sidebar-section{{margin-bottom:28px;}}
.sidebar-title{{font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin-bottom:10px;}}
.cat-row{{display:flex;align-items:center;gap:8px;margin-bottom:5px;}}
.cat-name{{font-size:.72rem;color:var(--text2);width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.cat-track{{flex:1;height:4px;background:var(--border);border-radius:2px;}}
.cat-fill{{height:100%;background:var(--gold);border-radius:2px;}}
.cat-cnt{{font-family:'DM Mono',monospace;font-size:.58rem;color:var(--muted);width:24px;text-align:right;}}

/* ── Lightbox ── */
#lightbox{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:1000;align-items:center;justify-content:center;cursor:zoom-out;}}
#lightbox.open{{display:flex;}}
#lightbox img{{max-width:92vw;max-height:92vh;border-radius:8px;object-fit:contain;}}
</style>
</head>
<body>

<div class="topnav">
  <a class="back-link" href="trips.html#trip-{tid}">← Back to trips</a>
  <span class="topnav-logo">Trip Journal</span>
</div>

<div class="hero">
  <div class="hero-title">{_esc(base_name)}{('<span class="hero-icon">' + icon + '</span>') if icon else ''}</div>
  <div class="hero-dates">{start_date} – {end_date}</div>
  <div class="kpis">
    <div class="kpi"><div class="kpi-v">{duration}</div><div class="kpi-l">Days</div></div>
    <div class="kpi"><div class="kpi-v">{checkin_count:,}</div><div class="kpi-l">Check-ins</div></div>
    <div class="kpi"><div class="kpi-v">{unique_places:,}</div><div class="kpi-l">Places</div></div>
    <div class="kpi"><div class="kpi-v">{len(countries)}</div><div class="kpi-l">Countries</div></div>
    {photo_note}
  </div>
  <div class="chips">{country_flags}{city_chips}</div>
</div>

<div class="layout">
  <div class="timeline">
    {timeline_html}
  </div>
  <div class="sidebar">
    <div class="sidebar-section">
      <div class="sidebar-title">Categories</div>
      {cats_html}
    </div>
  </div>
</div>

<div id="lightbox" onclick="closeLightbox()"><img id="lightbox-img" src=""></div>

<script>
function openLightbox(src){{document.getElementById('lightbox-img').src=src;document.getElementById('lightbox').classList.add('open');}}
function closeLightbox(){{document.getElementById('lightbox').classList.remove('open');}}
document.addEventListener('keydown',e=>{{if(e.key==='Escape')closeLightbox();}});
</script>
</body>
</html>'''


def _esc(s: str) -> str:
    return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
