# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
gen_photos.py — Generate photos.html: full gallery of all 21k+ photos.

Called by build.py when --photos path is supplied.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_page(
    photos_by_checkin: dict[str, list[str]],
    csv_path: str,
    pix_dir_uri: str,
    out_path: str,
    tips: list | None = None,
) -> None:
    # Load checkin metadata from CSV
    with open(csv_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    # Build checkin metadata map: checkin_id → {venue, city, country, date, ts}
    ci_meta: dict[str, dict] = {}
    for r in rows:
        cid = r.get("checkin_id", "").strip()
        if not cid:
            continue
        ts = int(r.get("date", 0) or 0)
        date_str = ""
        if ts:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %Y")
        ci_meta[cid] = {
            "venue":   r.get("venue", ""),
            "city":    r.get("city", ""),
            "country": r.get("country", ""),
            "date":    date_str,
            "ts":      ts,
        }

    # Build flat photo list sorted by timestamp desc
    all_photos: list[dict] = []
    for cid, filenames in photos_by_checkin.items():
        meta = ci_meta.get(cid, {})
        ts = meta.get("ts", 0)
        for fname in filenames:
            all_photos.append({
                "src":     pix_dir_uri + "/" + fname,
                "venue":   meta.get("venue", ""),
                "city":    meta.get("city", ""),
                "country": meta.get("country", ""),
                "date":    meta.get("date", ""),
                "ts":      ts,
            })
    all_photos.sort(key=lambda p: -p["ts"])
    total = len(all_photos)
    photos_json = json.dumps(all_photos, ensure_ascii=False).replace("</", "<\\/")

    # Build tip photos list
    tip_photos: list[dict] = []
    for t in (tips or []):
        if not t.get("photo"):
            continue
        ts = t.get("ts", 0)
        date_str = ""
        if ts:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %Y")
        tip_photos.append({
            "src":   pix_dir_uri.rstrip("/") + "/" + t["photo"],
            "venue": t.get("venue", ""),
            "city":  t.get("city", ""),
            "date":  date_str,
            "text":  t.get("text", ""),
            "ts":    ts,
        })
    tip_photos.sort(key=lambda p: -p["ts"])
    tip_photos_json = json.dumps(tip_photos, ensure_ascii=False).replace("</", "<\\/")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Photos Gallery</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{{--bg:#0b0d13;--card:#12151f;--card2:#181c28;--border:#222738;--gold:#e8b86d;--teal:#4ecdc4;--muted:#4a5270;--text:#cdd5f0;--text2:#7a85a8;}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;}}
a{{color:inherit;text-decoration:none;}}

.topnav{{display:flex;align-items:center;gap:20px;padding:18px 48px;border-bottom:1px solid var(--border);background:var(--card);position:sticky;top:0;z-index:100;}}
.topnav-logo{{font-family:'Playfair Display',serif;font-size:1.1rem;font-weight:700;color:var(--gold);}}
.topnav a{{font-family:'DM Mono',monospace;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.14em;color:var(--muted);transition:color .2s;}}
.topnav a:hover{{color:var(--gold);}}

.page-hero{{padding:40px 48px 28px;border-bottom:1px solid var(--border);}}
.page-hero h1{{font-family:'Playfair Display',serif;font-size:clamp(1.8rem,4vw,3rem);font-weight:900;background:linear-gradient(130deg,#f5d48a 0%,#e8b86d 45%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:4px;}}
.page-hero-sub{{font-family:'DM Mono',monospace;font-size:.65rem;color:var(--muted);}}

.gallery-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:4px;padding:16px 24px 72px;}}
.ph-item{{position:relative;overflow:hidden;border-radius:4px;cursor:pointer;aspect-ratio:4/3;background:var(--card2);}}
.ph-item img{{width:100%;height:100%;object-fit:cover;display:block;transition:transform .2s,opacity .2s;}}
.ph-item:hover img{{transform:scale(1.05);opacity:.85;}}
.ph-tooltip{{position:absolute;bottom:0;left:0;right:0;padding:24px 8px 7px;background:linear-gradient(transparent,rgba(0,0,0,.75));font-size:.64rem;color:#fff;opacity:0;transition:opacity .2s;pointer-events:none;}}
.ph-tooltip .pv{{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.ph-tooltip .pd{{font-family:'DM Mono',monospace;font-size:.56rem;color:rgba(255,255,255,.6);margin-top:1px;}}
.ph-item:hover .ph-tooltip{{opacity:1;}}

#gallery{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.94);z-index:9999;flex-direction:column;align-items:center;justify-content:center;}}
#gallery.open{{display:flex;}}
#gallery-img{{max-width:90vw;max-height:82vh;object-fit:contain;border-radius:6px;}}
.gal-nav{{position:absolute;top:50%;transform:translateY(-50%);font-size:2.2rem;color:#fff;cursor:pointer;padding:18px 22px;opacity:.5;transition:opacity .2s;background:none;border:none;user-select:none;}}
.gal-nav:hover{{opacity:1;}}
#gal-prev{{left:0;}}#gal-next{{right:0;}}
.gal-close{{position:absolute;top:18px;right:24px;font-size:1.5rem;color:#fff;cursor:pointer;opacity:.5;background:none;border:none;}}.gal-close:hover{{opacity:1;}}
.gal-counter{{position:absolute;bottom:18px;font-family:'DM Mono',monospace;font-size:.70rem;color:rgba(255,255,255,.55);}}
.gal-caption{{position:absolute;bottom:40px;font-family:'DM Sans',sans-serif;font-size:.76rem;color:rgba(255,255,255,.65);max-width:80vw;text-align:center;pointer-events:none;}}

.load-more{{display:block;margin:0 auto 48px;padding:11px 32px;background:var(--card);border:1px solid var(--border);border-radius:8px;font-family:'DM Mono',monospace;font-size:.64rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);cursor:pointer;transition:all .2s;}}
.load-more:hover{{border-color:var(--gold);color:var(--gold);}}
.sort-pills{{display:flex;gap:8px;margin-top:14px;}}
.sort-pill{{padding:5px 13px;border-radius:6px;font-family:'DM Mono',monospace;font-size:.60rem;text-transform:uppercase;letter-spacing:.1em;cursor:pointer;border:1px solid var(--border);background:var(--card2);color:var(--text2);transition:all .2s;}}
.sort-pill.active{{background:var(--gold);color:#0b0d13;border-color:var(--gold);}}
.sort-pill:hover:not(.active){{border-color:var(--gold);color:var(--gold);}}
</style>
</head>
<body>

<nav class="topnav">
  <a href="index.html" class="topnav-logo">Check-in Journal</a>
  <a href="index.html">← Dashboard</a>
  <a href="photos.html">Photos</a>
</nav>

<div class="page-hero">
  <h1>Photos</h1>
  <div class="page-hero-sub">{total:,} photos</div>
  <div class="sort-pills">
    <div class="sort-pill active" id="sNewest" onclick="setSort('newest')">Newest first</div>
    <div class="sort-pill" id="sOldest" onclick="setSort('oldest')">Oldest first</div>
  </div>
</div>

<div class="gallery-grid" id="galleryGrid"></div>
<button class="load-more" id="loadMore" onclick="loadMore()">Load more</button>

<div id="tipPhotosSection" style="display:none;padding:0 24px 24px;">
  <div style="font-family:'Playfair Display',serif;font-size:1.4rem;font-weight:700;color:var(--gold);padding:32px 0 12px;border-top:1px solid var(--border);">Tip Photos</div>
  <div class="gallery-grid" id="tipGalleryGrid" style="padding:0;"></div>
</div>

<div id="gallery" onclick="if(event.target===this)closeGallery()">
  <img id="gallery-img" src="" alt="">
  <button class="gal-nav" id="gal-prev" onclick="event.stopPropagation();galPrev()">&#8592;</button>
  <button class="gal-nav" id="gal-next" onclick="event.stopPropagation();galNext()">&#8594;</button>
  <button class="gal-close" onclick="closeGallery()">&#10005;</button>
  <div class="gal-caption" id="gal-caption"></div>
  <div class="gal-counter" id="gal-counter"></div>
</div>

<script>
const PHOTOS_NEWEST = {photos_json};
const PHOTOS_OLDEST = [...PHOTOS_NEWEST].reverse();
const TIP_PHOTOS = {tip_photos_json};
const PAGE = 300;
let sorted = PHOTOS_NEWEST, loaded = 0, galleryIdx = 0;
let tipGalIdx = 0, galMode = 'photos'; // 'photos' or 'tips'

function esc(s){{return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}}

function setSort(order){{
  sorted = order==='oldest' ? PHOTOS_OLDEST : PHOTOS_NEWEST;
  document.getElementById('sNewest').classList.toggle('active', order==='newest');
  document.getElementById('sOldest').classList.toggle('active', order==='oldest');
  document.getElementById('galleryGrid').innerHTML='';
  loaded=0;
  loadMore();
}}

function renderBatch(start, end){{
  const grid = document.getElementById('galleryGrid');
  const frag = document.createDocumentFragment();
  for(let i=start;i<end&&i<sorted.length;i++){{
    const p=sorted[i];
    const div=document.createElement('div');
    div.className='ph-item';
    div.innerHTML=`<img src="${{p.src}}" loading="lazy" alt="${{esc(p.venue)}}"><div class="ph-tooltip"><div class="pv">${{esc(p.venue)}}</div><div class="pd">${{esc(p.city||p.country||'')}}${{p.city&&p.date?' · ':''}}${{esc(p.date||'')}}</div></div>`;
    div.onclick=(()=>{{const idx=i;return()=>openGallery(idx);}})();
    frag.appendChild(div);
  }}
  grid.appendChild(frag);
}}

function loadMore(){{
  const end=Math.min(loaded+PAGE, sorted.length);
  renderBatch(loaded, end);
  loaded=end;
  document.getElementById('loadMore').style.display=loaded>=sorted.length?'none':'block';
}}
loadMore();

function openGallery(idx){{galMode='photos';galleryIdx=idx;showGalItem();document.getElementById('gallery').classList.add('open');}}
function showGalItem(){{
  if(galMode==='tips'){{const p=TIP_PHOTOS[tipGalIdx];document.getElementById('gallery-img').src=p.src;document.getElementById('gal-counter').textContent=(tipGalIdx+1)+' / '+TIP_PHOTOS.length;document.getElementById('gal-caption').textContent=p.venue+(p.date?' · '+p.date:'');return;}}
  const p=sorted[galleryIdx];document.getElementById('gallery-img').src=p.src;document.getElementById('gal-counter').textContent=(galleryIdx+1)+' / '+sorted.length;document.getElementById('gal-caption').textContent=p.venue+(p.date?' · '+p.date:'');
}}
function closeGallery(){{document.getElementById('gallery').classList.remove('open');document.getElementById('gallery-img').src='';}}
function galPrev(){{if(galMode==='tips'){{tipGalIdx=(tipGalIdx-1+TIP_PHOTOS.length)%TIP_PHOTOS.length;}}else{{galleryIdx=(galleryIdx-1+sorted.length)%sorted.length;}}showGalItem();}}
function galNext(){{if(galMode==='tips'){{tipGalIdx=(tipGalIdx+1)%TIP_PHOTOS.length;}}else{{galleryIdx=(galleryIdx+1)%sorted.length;}}showGalItem();}}
document.addEventListener('keydown',e=>{{const g=document.getElementById('gallery');if(!g.classList.contains('open'))return;if(e.key==='ArrowLeft')galPrev();else if(e.key==='ArrowRight')galNext();else if(e.key==='Escape')closeGallery();}});

// ── Tip photos section ──
if(TIP_PHOTOS.length){{
  document.getElementById('tipPhotosSection').style.display='';
  const grid=document.getElementById('tipGalleryGrid');
  const frag=document.createDocumentFragment();
  TIP_PHOTOS.forEach((p,i)=>{{
    const div=document.createElement('div');
    div.className='ph-item';
    div.innerHTML=`<img src="${{p.src}}" loading="lazy" alt="${{esc(p.venue)}}"><div class="ph-tooltip"><div class="pv">${{esc(p.venue)}}</div><div class="pd">${{esc(p.text||'').slice(0,60)}}${{p.text&&p.text.length>60?'…':''}}</div></div>`;
    div.onclick=(()=>{{const idx=i;return()=>openTipGallery(idx);}})();
    frag.appendChild(div);
  }});
  grid.appendChild(frag);
}}

function openTipGallery(idx){{galMode='tips';tipGalIdx=idx;showGalItem();document.getElementById('gallery').classList.add('open');}}
</script>
</body>
</html>"""

    Path(out_path).write_text(html, encoding="utf-8")
    print(f"photos.html → {out_path}  ({total:,} photos, {Path(out_path).stat().st_size//1024:,} KB)", file=sys.stderr)
