"""
build.py  –  CLI entry point. Reads checkins.csv → index.html + trips.html
Run:  python build.py [--input checkins.csv] [--config-dir config]
             [--home-city Minsk] [--min-checkins 5] [--output-dir .]
"""
import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from transform import load_mappings, apply_transforms
from metrics import process

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# Directory that contains this script — used as base for default paths
_SCRIPT_DIR = Path(__file__).resolve().parent


def load_settings(config_dir: Path) -> dict:
    path = config_dir / "settings.yaml"
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def save_category_list(rows: list[dict], out_path: str) -> None:
    from collections import Counter
    cats = Counter(r.get("category", "") for r in rows if r.get("category", "").strip())
    lines = ["FULL CATEGORY LIST", "=" * 60,
             f"Total unique categories: {len(cats)}", ""]
    for cat, n in cats.most_common():
        lines.append(f"  {n:6,}  {cat}")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    log.info("Category list → %s  (%d categories)", out_path, len(cats))


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Foursquare Check-in Dashboard</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/flag-icon-css/6.6.6/css/flag-icons.min.css">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
<style>
:root{--bg:#0b0d13;--card:#12151f;--card2:#181c28;--border:#222738;--gold:#e8b86d;--teal:#4ecdc4;--muted:#4a5270;--text:#cdd5f0;--text2:#7a85a8;}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;}

/* ── HEADER ── */
header{padding:52px 56px 36px;background:linear-gradient(160deg,#0f1220 0%,#0b0d13 70%);border-bottom:1px solid var(--border);display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:28px;}
header h1{font-family:'Playfair Display',serif;font-size:clamp(2.2rem,5vw,5rem);font-weight:900;line-height:1;letter-spacing:-0.02em;background:linear-gradient(130deg,#f5d48a 0%,#e8b86d 45%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
header .sub{margin-top:8px;font-family:'DM Mono',monospace;font-size:0.72rem;letter-spacing:0.18em;text-transform:uppercase;color:var(--muted);}
.updated{font-family:'DM Mono',monospace;font-size:0.62rem;color:var(--muted);margin-top:4px;}
.kpis{display:flex;gap:28px;flex-wrap:wrap;align-items:flex-end;}
.kpi .num{font-family:'Playfair Display',serif;font-size:2.2rem;font-weight:700;color:var(--gold);line-height:1;}
.kpi .lbl{font-family:'DM Mono',monospace;font-size:0.60rem;text-transform:uppercase;letter-spacing:0.14em;color:var(--muted);margin-top:5px;}

/* ── GRID ── */
.grid{padding:36px 56px 72px;display:grid;grid-template-columns:1fr 1fr;gap:20px;max-width:1500px;}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:26px 30px;position:relative;overflow:hidden;}
.card::after{content:'';position:absolute;top:0;left:24px;right:24px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:0.35;}
.card.full{grid-column:1/-1;}
.card-title{font-family:'DM Mono',monospace;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.2em;color:var(--gold);margin-bottom:18px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.card-title em{opacity:0.5;font-style:normal;}

/* ── TABS ── */
.tabs{display:flex;gap:6px;flex-wrap:wrap;}
.tab{padding:5px 13px;border-radius:6px;font-family:'DM Mono',monospace;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.1em;cursor:pointer;border:1px solid var(--border);background:var(--card2);color:var(--text2);transition:all 0.2s;}
.tab.active{background:var(--gold);color:#0b0d13;border-color:var(--gold);}
.pane{display:none;}.pane.active{display:block;}

/* ── SEARCH ── */
.search-box{width:100%;background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:8px 14px;color:var(--text);font-family:'DM Sans',sans-serif;font-size:0.85rem;margin-bottom:14px;outline:none;transition:border-color 0.2s;}
.search-box:focus{border-color:var(--gold);}

/* ── CATEGORY PILLS ── */
.cat-pills{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:18px;}
.cat-pill{padding:5px 14px;border-radius:20px;font-family:'DM Mono',monospace;font-size:0.60rem;text-transform:uppercase;letter-spacing:0.08em;cursor:pointer;border:1px solid var(--border);background:var(--card2);color:var(--text2);transition:all 0.2s;white-space:nowrap;}
.cat-pill.active{background:var(--teal);color:#0b0d13;border-color:var(--teal);}
.cat-pill:hover:not(.active){border-color:var(--teal);color:var(--teal);}

/* ── BAR LISTS ── */
.bar-list{display:flex;flex-direction:column;gap:7px;max-height:520px;overflow-y:auto;padding-right:4px;}
.bar-list::-webkit-scrollbar{width:3px;}
.bar-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
.bar-row{display:grid;grid-template-columns:28px 1fr 110px 58px;align-items:center;gap:8px;}
.bar-row .rank{font-family:'DM Mono',monospace;font-size:0.58rem;color:var(--muted);text-align:right;}
.bar-row .name{font-size:0.80rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text);}
.bar-row .name .city-tag{font-size:0.64rem;color:var(--muted);margin-left:5px;font-style:italic;}
.bar-row .track{height:5px;background:var(--card2);border-radius:3px;overflow:hidden;}
.bar-row .fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--gold),var(--teal));}
.bar-row .cnt{font-family:'DM Mono',monospace;font-size:0.66rem;color:var(--muted);text-align:right;}
.bar-row.hidden{display:none;}

/* ── COUNTRIES GRID ── */
.country-table{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;}
.country-item{background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:9px 12px;display:flex;align-items:center;gap:8px;}
.ci-rank{font-family:'DM Mono',monospace;font-size:0.58rem;color:var(--muted);width:22px;flex-shrink:0;}
.ci-name{font-size:0.82rem;color:var(--text);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.ci-count{font-family:'DM Mono',monospace;font-size:0.72rem;color:var(--teal);flex-shrink:0;}

/* ── MAP ── */
.map-wrap{position:relative;}
#map{height:600px;border-radius:8px;}
.map-status{position:absolute;bottom:14px;left:50%;transform:translateX(-50%);background:rgba(11,13,19,0.9);border:1px solid var(--border);border-radius:8px;padding:7px 16px;font-family:'DM Mono',monospace;font-size:0.68rem;color:var(--gold);pointer-events:none;transition:opacity 0.5s;white-space:nowrap;z-index:999;}

/* ── GITHUB CALENDAR HEATMAP ── */
.heatmap-outer{overflow-x:auto;padding-bottom:8px;}
.heatmap-outer::-webkit-scrollbar{height:3px;}
.heatmap-outer::-webkit-scrollbar-thumb{background:var(--border);}
.heatmap-year{display:flex;gap:14px;align-items:flex-start;margin-bottom:10px;}
.heatmap-label{font-family:'DM Mono',monospace;font-size:0.60rem;color:var(--muted);width:34px;flex-shrink:0;padding-top:4px;text-align:right;}
.heatmap-grid{display:flex;gap:3px;}
.heatmap-week{display:flex;flex-direction:column;gap:3px;}
.heatmap-cell{width:11px;height:11px;border-radius:2px;background:var(--card2);}
.heatmap-cell[data-v="0"]{background:#151820;}
.heatmap-cell[data-v="1"]{background:#1a3a1a;}
.heatmap-cell[data-v="2"]{background:#1e5c1e;}
.heatmap-cell[data-v="3"]{background:#c97a20;}
.heatmap-cell[data-v="4"]{background:#e8b86d;}
.heatmap-cell[data-v="5"]{background:#f5d48a;}
.heatmap-tooltip{position:fixed;pointer-events:none;background:rgba(11,13,19,0.96);border:1px solid var(--border);border-radius:6px;padding:5px 10px;font-family:'DM Mono',monospace;font-size:0.64rem;color:var(--text);z-index:9999;display:none;}
.heatmap-month-labels{display:flex;gap:3px;margin-left:48px;margin-bottom:4px;}
.hm-month{font-family:'DM Mono',monospace;font-size:0.55rem;color:var(--muted);text-transform:uppercase;}

/* ── TRAVEL TIMELINE (Gantt) ── */
.tl-wrap{position:relative;}
.tl-month-ruler{display:grid;grid-template-columns:40px repeat(12,1fr);margin-bottom:3px;}
.tl-month-tick{font-family:'DM Mono',monospace;font-size:0.52rem;color:var(--muted);text-align:center;letter-spacing:0;}
.tl-row{display:grid;grid-template-columns:40px 1fr 56px;align-items:center;gap:8px;margin-bottom:4px;}
.tl-year-label{font-family:'DM Mono',monospace;font-size:0.60rem;color:var(--muted);text-align:right;}
.tl-track{height:30px;background:var(--card2);border-radius:5px;position:relative;overflow:hidden;}
.tl-track-grid{position:absolute;inset:0;display:grid;grid-template-columns:repeat(12,1fr);pointer-events:none;}
.tl-track-grid span{border-left:1px solid rgba(255,255,255,0.04);}
.tl-track-grid span:first-child{border-left:none;}
.tl-bar{position:absolute;top:3px;height:24px;border-radius:4px;display:flex;align-items:center;overflow:hidden;text-decoration:none;transition:filter 0.15s;z-index:1;}
.tl-bar:hover{filter:brightness(1.25);z-index:10;}
.tl-bar-text{font-family:'DM Sans',sans-serif;font-size:0.60rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 6px;color:rgba(10,12,20,0.9);}
.tl-bar-dot{width:6px;height:6px;border-radius:50%;margin:0 4px;flex-shrink:0;}
.tl-total{font-family:'DM Mono',monospace;font-size:0.60rem;color:var(--muted);text-align:right;}
.tl-tooltip{position:fixed;background:rgba(11,13,19,0.97);border:1px solid var(--border);border-radius:8px;padding:9px 13px;font-family:'DM Mono',monospace;font-size:0.63rem;color:var(--text);z-index:9999;pointer-events:none;display:none;max-width:260px;line-height:1.6;}
.tl-tooltip-name{color:var(--gold);font-size:0.68rem;margin-bottom:2px;}

/* ── COMPANIONS ── */
.companion-bar{display:grid;grid-template-columns:160px 1fr 52px;align-items:center;gap:8px;margin-bottom:6px;}
.companion-name{font-size:0.80rem;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.companion-track{height:5px;background:var(--card2);border-radius:3px;overflow:hidden;}
.companion-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--teal),#45b7d1);}
.companion-cnt{font-family:'DM Mono',monospace;font-size:0.66rem;color:var(--muted);text-align:right;}

/* ── DISCOVERY RATE ── */
/* (canvas handled by Chart.js) */

/* ── VENUE LOYALTY ── */
.loyalty-list{display:flex;flex-direction:column;gap:5px;max-height:560px;overflow-y:auto;padding-right:4px;}
.loyalty-list::-webkit-scrollbar{width:3px;}
.loyalty-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
.loyalty-row{display:grid;grid-template-columns:28px 1fr auto 38px;align-items:center;gap:12px;padding:9px 13px;background:var(--card2);border:1px solid var(--border);border-radius:8px;transition:border-color 0.15s;}
.loyalty-row:hover{border-color:rgba(232,184,109,0.4);}
.lr-rank{font-family:'DM Mono',monospace;font-size:0.56rem;color:var(--muted);text-align:right;}
.lr-info{min-width:0;}
.lr-venue{font-size:0.80rem;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.lr-city{font-size:0.62rem;color:var(--muted);}
.lr-years{display:flex;gap:3px;flex-wrap:wrap;justify-content:flex-end;}
.lr-yr{font-family:'DM Mono',monospace;font-size:0.52rem;padding:2px 5px;border-radius:3px;background:rgba(232,184,109,0.1);color:var(--gold);border:1px solid rgba(232,184,109,0.2);white-space:nowrap;}
.lr-count{font-family:'DM Mono',monospace;font-size:0.68rem;color:var(--teal);text-align:right;}

/* ── TRIPS LINK ── */
.trips-link-card{display:flex;align-items:center;justify-content:space-between;padding:20px 26px;background:linear-gradient(135deg,#12151f 0%,#1a1f30 100%);border:1px solid var(--border);border-radius:14px;cursor:pointer;text-decoration:none;transition:border-color 0.2s;}
.trips-link-card:hover{border-color:var(--gold);}
.tlc-left{display:flex;flex-direction:column;gap:4px;}
.tlc-num{font-family:'Playfair Display',serif;font-size:2.4rem;font-weight:700;color:var(--gold);line-height:1;}
.tlc-label{font-family:'DM Mono',monospace;font-size:0.60rem;text-transform:uppercase;letter-spacing:0.18em;color:var(--muted);}
.tlc-arrow{font-size:1.6rem;color:var(--gold);opacity:0.7;}

@media (max-width:900px){
  .companion-bar{grid-template-columns:120px 1fr 44px;}
  .heatmap-cell{width:9px;height:9px;}
  .tl-month-tick{font-size:0;}
}
@media (max-width:520px){
  .heatmap-cell{width:7px;height:7px;}
  .companion-bar{grid-template-columns:100px 1fr 40px;}
  .loyalty-row{grid-template-columns:20px 1fr auto 30px;gap:6px;}
}


.recent-section{padding:0 56px 28px;max-width:1500px;}
.recent-header{display:flex;align-items:baseline;gap:16px;margin-bottom:14px;}
.recent-title{font-family:'DM Mono',monospace;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.2em;color:var(--gold);}
.recent-sub{font-family:'DM Mono',monospace;font-size:0.58rem;color:var(--muted);}
.recent-scroll{display:flex;gap:14px;overflow-x:auto;padding-bottom:10px;scroll-snap-type:x mandatory;}
.recent-scroll::-webkit-scrollbar{height:3px;}
.recent-scroll::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
.recent-card{flex:0 0 220px;scroll-snap-align:start;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 18px 14px;display:flex;flex-direction:column;gap:6px;position:relative;overflow:hidden;transition:border-color 0.2s;}
.recent-card::after{content:'';position:absolute;top:0;left:20px;right:20px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:0.3;}
.recent-card:hover{border-color:var(--gold);}
.rc-venue-row{display:flex;align-items:flex-start;gap:6px;justify-content:space-between;}
.rc-cat-icon{width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;flex-shrink:0;margin-top:1px;}
.rc-venue{font-size:0.88rem;font-weight:600;color:var(--text);line-height:1.25;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;flex:1;}
.rc-cat{font-family:'DM Mono',monospace;font-size:0.57rem;text-transform:uppercase;letter-spacing:0.1em;color:var(--teal);margin-top:2px;}
.rc-location{font-size:0.75rem;color:var(--text2);margin-top:4px;}
.rc-date{font-family:'DM Mono',monospace;font-size:0.60rem;color:var(--muted);margin-top:auto;padding-top:8px;}
.rc-weather{display:flex;align-items:center;gap:6px;margin-top:4px;}
.rc-weather-icon{font-size:1.3rem;line-height:1;}
.rc-weather-temp{font-family:'DM Mono',monospace;font-size:0.72rem;color:var(--gold);}
.rc-weather-desc{font-size:0.65rem;color:var(--muted);}
.rc-weather-loading{font-family:'DM Mono',monospace;font-size:0.60rem;color:var(--muted);animation:pulse 1.5s infinite;}

/* ── RECENT CARD: MINI MAP ── */
.rc-map{margin:-18px -18px 12px;height:86px;position:relative;overflow:hidden;border-radius:12px 12px 0 0;background:var(--card2);}
.rc-map img{position:absolute;image-rendering:auto;opacity:.82;}
.rc-map-fade{position:absolute;inset:0;background:linear-gradient(to bottom,transparent 35%,var(--card) 100%);}
.rc-map-pin{position:absolute;top:50%;left:50%;transform:translate(-50%,-60%);font-size:1.25rem;line-height:1;filter:drop-shadow(0 1px 3px rgba(0,0,0,.8));pointer-events:none;}
.rc-streak{display:inline-flex;align-items:center;gap:3px;background:rgba(232,184,109,0.18);border:1px solid rgba(232,184,109,0.35);color:var(--gold);border-radius:4px;padding:1px 6px;font-family:'DM Mono',monospace;font-size:0.54rem;letter-spacing:.05em;width:fit-content;}



@keyframes pulse{0%,100%{opacity:0.4;}50%{opacity:1;}}
.recent-loading{font-family:'DM Mono',monospace;font-size:0.70rem;color:var(--muted);padding:20px 0;}
@media (max-width: 900px) {
  .recent-section{padding:0 14px 20px;}
  .recent-card{flex:0 0 185px;}
}
@media (max-width: 520px) {
  .recent-card{flex:0 0 160px;padding:14px 14px 10px;}
  .rc-venue{font-size:0.80rem;}
}


@media (max-width: 900px) {
  header{padding:28px 20px 24px;flex-direction:column;align-items:flex-start;gap:20px;}
  .kpis{gap:16px;}
  .kpi .num{font-size:1.8rem;}
  .kpi .lbl{font-size:0.58rem;}
  .grid{padding:14px 14px 48px;grid-template-columns:1fr;gap:14px;}
  .card{padding:18px 18px;}
  .card.full{grid-column:1;}
  .country-table{grid-template-columns:repeat(2,1fr);}
  #map{height:420px;}
  .bar-row{grid-template-columns:22px 1fr 80px 44px;gap:6px;}
  .bar-row .name{font-size:0.75rem;}
  .cat-pill{font-size:0.57rem;padding:4px 10px;}
}
@media (max-width: 520px) {
  header h1{font-size:2rem;}
  header .sub{font-size:0.62rem;letter-spacing:0.1em;}
  .kpis{gap:12px;}
  .kpi .num{font-size:1.5rem;}
  .country-table{grid-template-columns:1fr 1fr;}
  #map{height:320px;}
  .bar-row{grid-template-columns:18px 1fr 60px 36px;gap:5px;}
  .bar-row .name .city-tag{display:none;}
  .grid{padding:10px 10px 36px;}
  .card{padding:14px 14px;}
  .card-title{font-size:0.58rem;}
  .tabs .tab{font-size:0.56rem;padding:4px 9px;}
  .cat-pill{font-size:0.54rem;padding:3px 8px;}
}
</style>
</head>
<body>
<header>
  <div>
    <h1>Check-in Journal</h1>
    <p class="sub">Foursquare &nbsp;&middot;&nbsp; {{DATE_MIN}} &ndash; {{DATE_MAX}}</p>
    <p class="updated">Updated {{UPDATED}}</p>
  </div>
  <div class="kpis">
    <div class="kpi"><div class="num">{{TOTAL}}</div><div class="lbl">Check-ins</div></div>
    <div class="kpi"><div class="num">{{COUNTRIES}}</div><div class="lbl">Countries</div></div>
    <div class="kpi"><div class="num">{{CITIES}}</div><div class="lbl">Cities</div></div>
    <div class="kpi"><div class="num">{{PLACES}}</div><div class="lbl">Unique Places</div></div>
    <div class="kpi"><div class="num">{{TRIPS}}</div><div class="lbl">Trips</div></div>
  </div>
</header>

<div class="recent-section">
  <div class="recent-header">
    <span class="recent-title">Recent Check-ins</span>
  </div>
  <div class="recent-scroll" id="recentScroll">
    <div class="recent-loading">Loading…</div>
  </div>
</div>

<div class="grid">
  <div class="card"><div class="card-title">Check-ins by Year</div><canvas id="yearChart" height="210"></canvas></div>
  <div class="card"><div class="card-title">Monthly Trend</div><canvas id="monthChart" height="210"></canvas></div>
  <div class="card"><div class="card-title">Hour of Day</div><canvas id="hourChart" height="200"></canvas></div>
  <div class="card"><div class="card-title">Day of Week</div><canvas id="dowChart" height="200"></canvas></div>

  <div class="card full">
    <div class="card-title">Activity Calendar <em>&middot; GitHub-style heatmap by day</em></div>
    <div id="heatmapTip" class="heatmap-tooltip"></div>
    <div class="heatmap-outer" id="heatmapCont"></div>
  </div>

  <div class="card full">
    <div class="card-title">Travel Timeline <em>&middot; trips away from Minsk &middot; click a bar for details</em></div>
    <div id="timelineCont"></div>
  </div>

  <a class="trips-link-card" href="trips.html">
    <div class="tlc-left">
      <div class="tlc-num">{{TRIPS}}</div>
      <div class="tlc-label">Trips documented &nbsp;&middot;&nbsp; view full trip journal →</div>
    </div>
    <div class="tlc-arrow">✈</div>
  </a>

  <div class="card full">
    <div class="card-title">
      All {{COUNTRIES}} Countries <em>&middot;</em>
      <div class="tabs">
        <div class="tab active" onclick="switchCountryTab('checkins',this)">By Check-ins</div>
        <div class="tab" onclick="switchCountryTab('places',this)">By Unique Places</div>
      </div>
    </div>
    <div class="pane active" id="pane-checkins"><div class="country-table" id="countriesCheckins"></div></div>
    <div class="pane" id="pane-places"><div class="country-table" id="countriesPlaces"></div></div>
  </div>

  <div class="card">
    <div class="card-title">All {{CITIES}} Cities <em>&middot; scroll &amp; search</em></div>
    <input class="search-box" type="text" placeholder="Search cities..." oninput="filterList('citiesList',this.value)">
    <div class="bar-list" id="citiesList"></div>
  </div>
  <div class="card">
    <div class="card-title">Top 500 Venues <em>&middot;&nbsp;unique by venue&nbsp;&middot;&nbsp; scroll &amp; search</em></div>
    <input class="search-box" type="text" placeholder="Search venues..." oninput="filterList('venuesList',this.value)">
    <div class="bar-list" id="venuesList"></div>
  </div>

  <div class="card">
    <div class="card-title">Top Companions <em>&middot; check-ins with others</em></div>
    <div id="companionsList"></div>
  </div>

  <div class="card">
    <div class="card-title">Discovery Rate <em>&middot; new vs revisited venues per month</em></div>
    <canvas id="discoveryChart" height="200"></canvas>
  </div>

  <div class="card full">
    <div class="card-title">Venue Loyalty <em>&middot; places visited in 3+ different years</em></div>
    <div class="loyalty-list" id="loyaltyGrid"></div>
  </div>

  <div class="card full">
    <div class="card-title">Place Categories <em>&middot; by group</em></div>
    <canvas id="catChart" height="85"></canvas>
  </div>

  <div class="card full">
    <div class="card-title">Category Explorer <em>&middot; top 50 unique venues per category (by check-ins)</em></div>
    <div class="cat-pills" id="catPills"></div>
    <div class="bar-list" id="explorerList"></div>
  </div>

  <div class="card full">
    <div class="card-title">Map <em>&middot;</em>
      <div class="tabs">
        <div class="tab active" id="tabHeat" onclick="switchMap('heat')">Heatmap &middot; {{TOTAL}} check-ins</div>
        <div class="tab" id="tabDots" onclick="switchMap('dots')">Dots &middot; {{PLACES}} unique places</div>
        <div class="tab" id="tabCountries" onclick="switchMap('countries')">Countries &middot; flags</div>
      </div>
    </div>
    <div class="map-wrap">
      <div id="map" style="display:block"></div>
      <div id="countriesMap" style="display:none;height:600px;border-radius:8px;"></div>
      <div class="map-status" id="mapStatus">Loading heatmap...</div>
    </div>
  </div>
</div>

<script>
const S={{STATS}};
// ── XSS-safe string escape ─────────────────────────────────────────────────
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}

// ── Country flag emoji ──────────────────────────────────────────────────────
// ── Country → ISO 3166-1 alpha-2 ──────────────────────────────────────────
const ISO2={
  'Afghanistan':'af','Albania':'al','Algeria':'dz','Angola':'ao','Argentina':'ar',
  'Armenia':'am','Australia':'au','Austria':'at','Azerbaijan':'az',
  'Belarus':'by','Belgium':'be','Bolivia':'bo','Bosnia and Herzegovina':'ba',
  'Brazil':'br','Bulgaria':'bg','Cambodia':'kh','Canada':'ca','Chile':'cl',
  'China':'cn','Colombia':'co','Croatia':'hr','Cyprus':'cy','Czech Republic':'cz',
  'Denmark':'dk','Ecuador':'ec','Egypt':'eg','Estonia':'ee','Finland':'fi',
  'France':'fr','Georgia':'ge','Germany':'de','Greece':'gr','Hungary':'hu',
  'Hong Kong':'hk','India':'in','Indonesia':'id','Iran':'ir','Iraq':'iq',
  'Ireland':'ie','Israel':'il','Italy':'it','Japan':'jp','Kazakhstan':'kz',
  'Kosovo':'xk','Kyrgyzstan':'kg','Latvia':'lv','Lithuania':'lt','Luxembourg':'lu',
  'Macao':'mo','Malaysia':'my','Malta':'mt','Mexico':'mx','Moldova':'md',
  'Montenegro':'me','Morocco':'ma','Myanmar':'mm','Netherlands':'nl',
  'New Zealand':'nz','North Macedonia':'mk','Norway':'no','Oman':'om',
  'Pakistan':'pk','Peru':'pe','Philippines':'ph','Poland':'pl','Portugal':'pt',
  'Qatar':'qa','Romania':'ro','Russia':'ru','Serbia':'rs','Singapore':'sg',
  'Slovakia':'sk','Slovenia':'si','South Korea':'kr','Spain':'es','Sweden':'se',
  'Switzerland':'ch','Taiwan':'tw','Thailand':'th','Tunisia':'tn',
  'Turkey':'tr','Türkiye':'tr','Ukraine':'ua','United Arab Emirates':'ae',
  'United Kingdom':'gb','United States':'us','Uruguay':'uy','Uzbekistan':'uz',
  'Venezuela':'ve','Vietnam':'vn','Holy See (Vatican City State)':'va','	Holy See (Vatican City State)':'va','Vatican City':'va',
  'North Korea':'kp','Cuba':'cu','Iceland':'is','Sri Lanka':'lk',
  'Liechtenstein':'li',
};
// Returns an HTML <span> using flag-icons CSS library (renders on all platforms)
function flagHtml(country,size){
  const c=(country||'').trim();
  const code=ISO2[c];
  if(!code) return '';
  const sz=size||'1em';
  return `<span class="fi fi-${code}" style="font-size:${sz};border-radius:2px;vertical-align:middle;flex-shrink:0"></span>`;
}
// Legacy compat — used in tooltip text (plain text context)
function flag(country){ return flagHtml(country); }
Chart.defaults.color='#7a85a8';Chart.defaults.borderColor='#1e2335';
Chart.defaults.font.family="'DM Mono',monospace";Chart.defaults.font.size=11;
const PAL=['#e63946','#f4831f','#e8b86d','#f5d48a','#a8d8a8','#4ecdc4','#45b7d1','#96ceb4','#ff6b9d','#c44dff','#4d79ff','#ff4d4d','#ffaa00','#00c9a7'];

// ── Charts ────────────────────────────────────────────────────────
new Chart(document.getElementById('yearChart'),{type:'bar',data:{labels:S.by_year.map(x=>x[0]),datasets:[{data:S.by_year.map(x=>x[1]),backgroundColor:S.by_year.map((_,i)=>PAL[i%PAL.length]),borderRadius:5,borderWidth:0}]},options:{responsive:true,plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>' '+ctx.parsed.y.toLocaleString()+' check-ins'}}},scales:{x:{grid:{display:false}},y:{grid:{color:'#1a1e2e'}}}}});
new Chart(document.getElementById('monthChart'),{type:'line',data:{labels:S.by_month.map(x=>x[0]),datasets:[{data:S.by_month.map(x=>x[1]),borderColor:'#4ecdc4',backgroundColor:'rgba(78,205,196,0.07)',borderWidth:2,pointRadius:0,fill:true,tension:0.4}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{maxTicksLimit:12,maxRotation:0}},y:{grid:{color:'#1a1e2e'}}}}});
new Chart(document.getElementById('hourChart'),{type:'bar',data:{labels:S.by_hour.map(x=>x[0]+':00'),datasets:[{data:S.by_hour.map(x=>x[1]),backgroundColor:S.by_hour.map(x=>{const m=Math.max(...S.by_hour.map(y=>y[1]));return`rgba(78,205,196,${(0.2+0.8*(x[1]/m)).toFixed(2)})`;}),borderRadius:4,borderWidth:0}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{grid:{color:'#1a1e2e'}}}}});
const DOW=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
new Chart(document.getElementById('dowChart'),{type:'bar',data:{labels:S.by_dow.map(x=>DOW[x[0]]),datasets:[{data:S.by_dow.map(x=>x[1]),backgroundColor:S.by_dow.map(x=>x[0]>=4?'rgba(78,205,196,0.75)':'rgba(232,184,109,0.55)'),borderRadius:4,borderWidth:0}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{grid:{color:'#1a1e2e'}}}}});
const CC=['#e8b86d','#4ecdc4','#e63946','#45b7d1','#a8d8a8','#c44dff','#f4831f','#96ceb4'];
new Chart(document.getElementById('catChart'),{type:'bar',data:{labels:S.cat_groups.map(x=>x[0]),datasets:[{data:S.cat_groups.map(x=>x[1]),backgroundColor:S.cat_groups.map((_,i)=>CC[i%CC.length]),borderRadius:5,borderWidth:0}]},options:{indexAxis:'y',responsive:true,plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>' '+ctx.parsed.x.toLocaleString()+' check-ins'}}},scales:{x:{grid:{color:'#1a1e2e'}},y:{grid:{display:false}}}}});

// ── GitHub Heatmap ─────────────────────────────────────────────────────────
(function(){
  const data=S.heatmap, tip=document.getElementById('heatmapTip');
  const cont=document.getElementById('heatmapCont');
  const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function level(v){if(!v||v===0)return 0;if(v<=2)return 1;if(v<=5)return 2;if(v<=10)return 3;if(v<=20)return 4;return 5;}
  Object.keys(data).sort().forEach(year=>{
    const d=data[year];
    const row=document.createElement('div'); row.className='heatmap-year';
    const lbl=document.createElement('div'); lbl.className='heatmap-label'; lbl.textContent=year; row.appendChild(lbl);
    const grid=document.createElement('div'); grid.className='heatmap-grid';
    let cur=new Date(parseInt(year),0,1);
    const end=new Date(parseInt(year),11,31);
    // pad to Monday start
    let dow=cur.getDay(); dow=(dow===0?6:dow-1);
    let week=document.createElement('div'); week.className='heatmap-week';
    for(let p=0;p<dow;p++){const blank=document.createElement('div');blank.className='heatmap-cell';blank.setAttribute('data-v','0');week.appendChild(blank);}
    while(cur<=end){
      if(cur.getDay()===1&&week.children.length>0){grid.appendChild(week);week=document.createElement('div');week.className='heatmap-week';}
      const ds=cur.getFullYear()+'-'+String(cur.getMonth()+1).padStart(2,'0')+'-'+String(cur.getDate()).padStart(2,'0');
      const v=d[ds]||0; const cell=document.createElement('div'); cell.className='heatmap-cell';
      cell.setAttribute('data-v',level(v));
      cell.addEventListener('mouseenter',e=>{if(v>0){tip.textContent=ds+': '+v+' check-in'+(v===1?'':'s');tip.style.display='block';}});
      cell.addEventListener('mousemove',e=>{tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY-28)+'px';});
      cell.addEventListener('mouseleave',()=>{tip.style.display='none';});
      week.appendChild(cell);
      cur.setDate(cur.getDate()+1);
    }
    if(week.children.length>0) grid.appendChild(week);
    row.appendChild(grid); cont.appendChild(row);
  });
})();

// ── Travel Timeline (Gantt) ─────────────────────────────────────────────────
(function(){
  const trips=S.timeline;
  const cont=document.getElementById('timelineCont');
  if(!trips||!trips.length){cont.innerHTML='<p style="color:var(--muted);font-size:.8rem">No trips found.</p>';return;}

  // Floating tooltip
  const tip=document.createElement('div'); tip.className='tl-tooltip'; document.body.appendChild(tip);
  function showTip(e,t){
    tip.innerHTML=`<div class="tl-tooltip-name">${esc(t.name)}</div>`+
      `<div>${esc(t.start)} – ${esc(t.end)} &nbsp;·&nbsp; ${t.days}d</div>`+
      `<div>${t.count.toLocaleString()} check-ins &nbsp;·&nbsp; ${t.countries.length} countr${t.countries.length===1?'y':'ies'}</div>`+
      `<div style="color:var(--muted);font-size:.58rem;margin-top:3px">${t.countries.slice(0,4).map(esc).join(', ')}</div>`;
    tip.style.display='block';moveTip(e);
  }
  function moveTip(e){tip.style.left=(e.clientX+14)+'px';tip.style.top=(e.clientY-56)+'px';}
  function hideTip(){tip.style.display='none';}

  // Month ruler
  const ruler=document.createElement('div'); ruler.className='tl-month-ruler';
  const spc=document.createElement('div'); ruler.appendChild(spc);
  'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' ').forEach(m=>{
    const el=document.createElement('div'); el.className='tl-month-tick'; el.textContent=m; ruler.appendChild(el);
  });
  cont.appendChild(ruler);

  // Group by year
  const byYear={};
  trips.forEach(t=>{if(!byYear[t.year])byYear[t.year]=[];byYear[t.year].push(t);});

  const COLORS=['#e8b86d','#4ecdc4','#e63946','#45b7d1','#a8d8a8','#c44dff','#f4831f','#96ceb4','#ff6b9d','#4d79ff','#00c9a7','#ffaa00'];

  Object.keys(byYear).sort().forEach(yr=>{
    const row=document.createElement('div'); row.className='tl-row';

    const lbl=document.createElement('div'); lbl.className='tl-year-label'; lbl.textContent=yr; row.appendChild(lbl);

    const track=document.createElement('div'); track.className='tl-track';
    // Month gridlines
    const grid=document.createElement('div'); grid.className='tl-track-grid';
    for(let m=0;m<12;m++){const s=document.createElement('span');grid.appendChild(s);}
    track.appendChild(grid);

    const yrS=new Date(parseInt(yr),0,1).getTime();
    const yrE=new Date(parseInt(yr),11,31,23,59,59).getTime();
    const yrSpan=yrE-yrS;

    byYear[yr].forEach((t,i)=>{
      const sMs=new Date(t.start+'T00:00:00Z').getTime();
      const eMs=new Date(t.end+'T23:59:59Z').getTime();
      const leftPct=Math.max(0,((sMs-yrS)/yrSpan)*100);
      const widthPct=Math.max(0.1,((eMs-sMs)/yrSpan)*100);
      const color=COLORS[i%COLORS.length];

      const bar=document.createElement('a');
      bar.className='tl-bar';
      bar.href='trips.html#trip-'+t.id;
      bar.style.left=leftPct.toFixed(3)+'%';
      bar.style.width=widthPct.toFixed(3)+'%';
      bar.style.minWidth='8px';
      bar.style.background=color;

      bar.addEventListener('mouseenter',e=>showTip(e,t));
      bar.addEventListener('mousemove',moveTip);
      bar.addEventListener('mouseleave',hideTip);

      // After paint: decide whether to show text or just a dot
      requestAnimationFrame(()=>{
        const pw=bar.offsetWidth;
        if(pw>=52){
          const txt=document.createElement('span'); txt.className='tl-bar-text';
          txt.textContent=t.name; bar.appendChild(txt);
        } else if(pw>=14){
          const dot=document.createElement('span'); dot.className='tl-bar-dot';
          dot.style.background='rgba(10,12,20,0.5)'; bar.appendChild(dot);
        }
      });

      track.appendChild(bar);
    });

    row.appendChild(track);

    const total=document.createElement('div'); total.className='tl-total';
    total.textContent=byYear[yr].reduce((s,t)=>s+t.count,0).toLocaleString();
    row.appendChild(total);

    cont.appendChild(row);
  });
})();

// ── Companions ──────────────────────────────────────────────────────────────
(function(){
  const data=S.companions, max=data[0]?data[0][1]:1;
  document.getElementById('companionsList').innerHTML=data.map(([n,c])=>
    `<div class="companion-bar">
      <span class="companion-name" title="${esc(n)}">${esc(n)}</span>
      <div class="companion-track"><div class="companion-fill" style="width:${(c/max*100).toFixed(1)}%"></div></div>
      <span class="companion-cnt">${c.toLocaleString()}</span>
    </div>`
  ).join('');
})();

// ── Discovery Rate ─────────────────────────────────────────────────────────
new Chart(document.getElementById('discoveryChart'),{type:'bar',
  data:{labels:S.discovery_rate.map(x=>x[0]),
    datasets:[
      {label:'New venues',data:S.discovery_rate.map(x=>x[1]),backgroundColor:'rgba(78,205,196,0.75)',borderWidth:0,borderRadius:2},
      {label:'Revisits',  data:S.discovery_rate.map(x=>x[2]),backgroundColor:'rgba(232,184,109,0.45)',borderWidth:0,borderRadius:2},
    ]},
  options:{responsive:true,plugins:{legend:{display:true,labels:{color:'#7a85a8',font:{family:"'DM Mono',monospace",size:10}}},
    tooltip:{callbacks:{label:ctx=>' '+ctx.dataset.label+': '+ctx.parsed.y.toLocaleString()}}},
    scales:{x:{stacked:true,grid:{display:false},ticks:{maxTicksLimit:24,maxRotation:0,font:{size:10}}},
      y:{stacked:true,grid:{color:'#1a1e2e'}}}}});

// ── Venue Loyalty ──────────────────────────────────────────────────────────
(function(){
  const data=S.venue_loyalty;
  document.getElementById('loyaltyGrid').innerHTML=data.map(([name,city,years,total],i)=>
    `<div class="loyalty-row">
      <span class="lr-rank">#${i+1}</span>
      <div class="lr-info">
        <div class="lr-venue" title="${esc(name)}">${esc(name)}</div>
        <div class="lr-city">${esc(city||'')}</div>
      </div>
      <div class="lr-years">${years.map(y=>`<span class="lr-yr">${y}</span>`).join('')}</div>
      <span class="lr-count">${total}</span>
    </div>`
  ).join('');
})();


const explorerData=S.explorer, explorerCats=S.explorer_cats;
let activeCat=explorerCats[0];
const pillsEl=document.getElementById('catPills');
explorerCats.forEach(cat=>{
  const p=document.createElement('div');
  p.className='cat-pill'+(cat===activeCat?' active':'');
  p.textContent=cat;
  p.onclick=()=>{
    document.querySelectorAll('.cat-pill').forEach(x=>x.classList.remove('active'));
    p.classList.add('active'); activeCat=cat; renderExplorer(cat);
  };
  pillsEl.appendChild(p);
});
function renderExplorer(cat){
  const data=explorerData[cat]||[];
  const max=data.length?data[0][2]:1;
  document.getElementById('explorerList').innerHTML=data.map(([name,city,count],i)=>
    `<div class="bar-row">
      <span class="rank">#${i+1}</span>
      <span class="name" title="${esc(name)} · ${esc(city)}">${esc(name)}<span class="city-tag">${esc(city||'')} </span></span>
      <div class="track"><div class="fill" style="width:${(count/max*100).toFixed(1)}%"></div></div>
      <span class="cnt">${count.toLocaleString()}</span>
    </div>`
  ).join('')||'<div style="color:var(--muted);padding:8px;font-size:0.85rem">No data</div>';
}
renderExplorer(activeCat);

// ── Countries ─────────────────────────────────────────────────────
function makeCountryGrid(data,id){
  const n=data.length, cols=3, rows=Math.ceil(n/cols), vis=[];
  for(let r=0;r<rows;r++) for(let c=0;c<cols;c++){const i=c*rows+r;if(i<n)vis.push([data[i],i+1]);}
  document.getElementById(id).innerHTML=vis.map(([[name,count],rank])=>{
    const f=flagHtml(name,'1.1em');
    return `<div class="country-item"><span class="ci-rank">#${rank}</span>`+
      `<span class="ci-name" title="${esc(name)}">${f} ${esc(name)}</span>`+
      `<span class="ci-count">${count.toLocaleString()}</span></div>`;
  }).join('');
}
makeCountryGrid(S.countries,'countriesCheckins');
makeCountryGrid(S.countries_by_venues,'countriesPlaces');
function switchCountryTab(name,el){
  document.querySelectorAll('.pane').forEach(p=>p.classList.remove('active'));
  document.getElementById('pane-'+name).classList.add('active');
  el.closest('.tabs').querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  el.classList.add('active');
}

// ── Bar Lists ─────────────────────────────────────────────────────
function barList(id,data){
  const max=data[0][1];
  document.getElementById(id).innerHTML=data.map(([n,c,country],i)=>{
    const f=country?flagHtml(country):'';
    return `<div class="bar-row" data-name="${esc(n).toLowerCase()}${country?' '+country.toLowerCase():''}">
      <span class="rank">#${i+1}</span>
      <span class="name" title="${esc(n)}">${f} ${esc(n)}</span>
      <div class="track"><div class="fill" style="width:${(c/max*100).toFixed(1)}%"></div></div>
      <span class="cnt">${c.toLocaleString()}</span>
    </div>`;
  }).join('');
}
barList('citiesList',S.cities);
// Venues: data is [name, count, city]
(function(){
  const data=S.venues, max=data[0][1];
  document.getElementById('venuesList').innerHTML=data.map(([n,c,city],i)=>
    `<div class="bar-row" data-name="${n.toLowerCase().replace(/"/g,'')}${city?' '+city.toLowerCase():''}">
      <span class="rank">#${i+1}</span>
      <span class="name" title="${esc(n)}${city?' · '+esc(city):''}">  ${esc(n)}<span class="city-tag">${esc(city||'')} </span></span>
      <div class="track"><div class="fill" style="width:${(c/max*100).toFixed(1)}%"></div></div>
      <span class="cnt">${c.toLocaleString()}</span>
    </div>`
  ).join('');
})();
function filterList(id,q){
  document.getElementById(id).querySelectorAll('.bar-row').forEach(r=>
    r.classList.toggle('hidden',q.length>0&&!r.dataset.name.includes(q.toLowerCase()))
  );
}

// ── Recent Check-ins + Weather ─────────────────────────────────
(function(){
  const recent=S.recent;
  const WMO={
    0:['☀️','Clear'],1:['🌤️','Mainly clear'],2:['⛅','Partly cloudy'],3:['☁️','Overcast'],
    45:['🌫️','Fog'],48:['🌫️','Icy fog'],51:['🌦️','Light drizzle'],53:['🌦️','Drizzle'],55:['🌧️','Heavy drizzle'],
    61:['🌧️','Light rain'],63:['🌧️','Rain'],65:['🌧️','Heavy rain'],
    71:['🌨️','Light snow'],73:['❄️','Snow'],75:['❄️','Heavy snow'],77:['🌨️','Snow grains'],
    80:['🌦️','Rain showers'],81:['🌧️','Rain showers'],82:['⛈️','Violent showers'],
    85:['🌨️','Snow showers'],86:['❄️','Heavy snow showers'],
    95:['⛈️','Thunderstorm'],96:['⛈️','Thunderstorm+hail'],99:['⛈️','Thunderstorm+hail'],
  };
  const scrollEl=document.getElementById('recentScroll');

  // ── Streak: count each venue_id in the recent 30 ──
  const venueCounts={};
  recent.forEach(r=>{if(r.venue_id)venueCounts[r.venue_id]=(venueCounts[r.venue_id]||0)+1;});

  // ── OSM tile mini-map helper ──
  function tileBlock(lat,lng){
    if(!lat||!lng) return '';
    const z=14, n=1<<z;
    const xf=(lng+180)/360*n;
    const latr=lat*Math.PI/180;
    const yf=(1-Math.log(Math.tan(latr)+1/Math.cos(latr))/Math.PI)/2*n;
    const xt=Math.floor(xf), yt=Math.floor(yf);
    const fx=(xf-xt)*256, fy=(yf-yt)*256;
    // Tile is 256×256; we want the venue at the card's horizontal centre (110px) and vertical centre (43px)
    const iLeft=Math.round(110-fx), iTop=Math.round(43-fy);
    return `<div class="rc-map">` +
      `<img src="https://tile.openstreetmap.org/${z}/${xt}/${yt}.png" ` +
           `width="256" height="256" style="left:${iLeft}px;top:${iTop}px;" loading="lazy" crossorigin="anonymous">` +
      `<div class="rc-map-fade"></div>` +
      `<div class="rc-map-pin">📍</div>` +
      `</div>`;
  }

  // ── Foursquare app URL ──
  function fsUrl(r){
    if(!r.venue_id) return null;
    return `https://app.foursquare.com/v/${encodeURIComponent(r.venue.toLowerCase().replace(/\s+/g,'-'))}/${r.venue_id}`;
  }

  // ── Category icons: emoji + vivid colour, 100% self-contained, no CDN ──
  // Each entry: [emoji, background-colour]
  const CAT_ICON={
    // ── Food & Drink ──────────────────────────────────────────────────
    'Coffee Shop':              ['☕','#6F4E37'],
    'Café':                     ['☕','#6F4E37'],
    'Bakery':                   ['🥐','#C8860A'],
    'Restaurant':               ['🍽️','#C0392B'],
    'Fast Food Restaurant':     ['🍔','#E67E22'],
    'Pizzeria':                 ['🍕','#E74C3C'],
    'Burger Joint':             ['🍔','#D35400'],
    'Italian Restaurant':       ['🍝','#C0392B'],
    'Kebab Restaurant':         ['🌯','#E67E22'],
    'Doner Restaurant':         ['🌯','#D35400'],
    'Shawarma Restaurant':      ['🌯','#E67E22'],
    'Caucasian Restaurant':     ['🍖','#922B21'],
    'Food Court':               ['🍱','#E67E22'],
    'Cafeteria':                ['🥣','#D4A017'],
    'Corporate Cafeteria':      ['🥣','#D4A017'],
    'College Cafeteria':        ['🥣','#D4A017'],
    'Farmers Market':           ['🥦','#27AE60'],
    'Market':                   ['🛒','#27AE60'],
    'Grocery Store':            ['🛒','#27AE60'],
    'Supermarket':              ['🛒','#219A52'],
    'Convenience Store':        ['🏪','#2ECC71'],
    'Gourmet Store':            ['🧀','#F39C12'],
    'Candy Store':              ['🍬','#F06292'],
    // ── Nightlife ─────────────────────────────────────────────────────
    'Bar':                      ['🍺','#F39C12'],
    'Beer Bar':                 ['🍺','#E67E22'],
    'Pub':                      ['🍻','#D4A017'],
    'Hookah Bar':               ['💨','#8E44AD'],
    'Cocktail Bar':             ['🍸','#E74C3C'],
    'Wine Bar':                 ['🍷','#922B21'],
    'Wine Store':               ['🍷','#7B241C'],
    'Liquor Store':             ['🥃','#784212'],
    'Lounge':                   ['🛋️','#5D6D7E'],
    'Gastropub':                ['🍖','#CA6F1E'],
    'Brewery':                  ['🍻','#BA4A00'],
    'Nightclub':                ['🎵','#6C3483'],
    'Karaoke Bar':              ['🎤','#7D3C98'],
    // ── Hotels & Accommodation ────────────────────────────────────────
    'Hotel':                    ['🏨','#2C3E50'],
    'Hostel':                   ['🛏️','#34495E'],
    'Bed and Breakfast':        ['🛏️','#4A235A'],
    'Inn':                      ['🏠','#2E4053'],
    'Apartment or Condo':       ['🏠','#1A5276'],
    'Home (private)':           ['🏠','#17202A'],
    // ── Transport ─────────────────────────────────────────────────────
    'Metro Station':            ['🚇','#2980B9'],
    'Rail Station':             ['🚉','#1A5276'],
    'Train':                    ['🚂','#E74C3C'],
    'Tram Station':             ['🚃','#2471A3'],
    'Light Rail Station':       ['🚈','#2980B9'],
    'Platform':                 ['🚉','#1F618D'],
    'Bus Line':                 ['🚌','#27AE60'],
    'Bus Station':              ['🚌','#1E8449'],
    'Bus Stop':                 ['🚏','#239B56'],
    'Airport Terminal':         ['✈️','#2471A3'],
    'International Airport':    ['✈️','#154360'],
    'Airport Gate':             ['✈️','#1A5276'],
    'Airport Service':          ['✈️','#21618C'],
    'Plane':                    ['✈️','#2980B9'],
    'Travel Lounge':            ['🛋️','#21618C'],
    'Boat or Ferry':            ['⛴️','#117A65'],
    'Border Crossing':          ['🛂','#717D7E'],
    'Taxi':                     ['🚕','#F4D03F'],
    'Parking':                  ['🅿️','#2980B9'],
    'Fuel Station':             ['⛽','#E67E22'],
    'Rest Area':                ['🛑','#7F8C8D'],
    'Cable Car':                ['🚡','#8E44AD'],
    // ── Outdoors ──────────────────────────────────────────────────────
    'Park':                     ['🌳','#1E8449'],
    'Plaza':                    ['🏛️','#2E86C1'],
    'Pedestrian Plaza':         ['🚶','#2E86C1'],
    'Beach':                    ['🏖️','#F39C12'],
    'Mountain':                 ['⛰️','#7F8C8D'],
    'Hiking Trail':             ['🥾','#6E2F1A'],
    'Garden':                   ['🌸','#1E8449'],
    'Scenic Lookout':           ['🔭','#1A5276'],
    'Waterfront':               ['🌊','#2471A3'],
    'Harbor or Marina':         ['⚓','#154360'],
    'River':                    ['🌊','#2980B9'],
    'Lake':                     ['🏞️','#2471A3'],
    'Island':                   ['🏝️','#27AE60'],
    'Bike Trail':               ['🚴','#E67E22'],
    'Other Great Outdoors':     ['🌲','#186A3B'],
    'Cemetery':                 ['🪦','#5D6D7E'],
    'Ski Area':                 ['⛷️','#85C1E9'],
    'Sculpture Garden':         ['🗿','#7F8C8D'],
    'Bridge':                   ['🌉','#626567'],
    'Fountain':                 ['⛲','#5DADE2'],
    // ── Arts & Culture ────────────────────────────────────────────────
    'Museum':                   ['🏛️','#7D3C98'],
    'Art Museum':               ['🎨','#884EA0'],
    'History Museum':           ['📜','#6E2F1A'],
    'Art Gallery':              ['🖼️','#7D3C98'],
    'Monument':                 ['🗽','#626567'],
    'Historic and Protected Site':['🏰','#784212'],
    'Castle':                   ['🏰','#6E2F1A'],
    'Palace':                   ['🏯','#784212'],
    'Outdoor Sculpture':        ['🗿','#717D7E'],
    'Public Art':               ['🎨','#7D3C98'],
    'Street Art':               ['🎨','#8E44AD'],
    'Event Space':              ['🎪','#C0392B'],
    'Dance Studio':             ['💃','#E74C3C'],
    'Theater':                  ['🎭','#922B21'],
    // ── Religion ──────────────────────────────────────────────────────
    'Church':                   ['⛪','#7D3C98'],
    'Mosque':                   ['🕌','#117A65'],
    'Temple':                   ['🛕','#B9770E'],
    'Monastery':                ['🙏','#6E2F1A'],
    'Synagogue':                ['✡️','#1A5276'],
    // ── Shops ─────────────────────────────────────────────────────────
    'Shopping Mall':            ['🛍️','#E91E63'],
    'Department Store':         ['🏬','#C0392B'],
    'Clothing Store':           ['👔','#8E44AD'],
    'Shoe Store':               ['👟','#D35400'],
    'Electronics Store':        ['💻','#2471A3'],
    'Mobile Phone Store':       ['📱','#2E86C1'],
    'Bookstore':                ['📚','#1A5276'],
    'Furniture and Home Store': ['🛋️','#784212'],
    'Cosmetics Store':          ['💄','#F06292'],
    'Bicycle Store':            ['🚲','#27AE60'],
    'Big Box Store':            ['📦','#7F8C8D'],
    'Flea Market':              ['🏷️','#D35400'],
    'Hardware Store':           ['🔧','#7F8C8D'],
    // ── Health & Fitness ──────────────────────────────────────────────
    'Gym':                      ['💪','#E74C3C'],
    'Bath House':               ['♨️','#1ABC9C'],
    'Spa':                      ['💆','#1ABC9C'],
    'Medical Center':           ['🏥','#E74C3C'],
    'Pharmacy':                 ['💊','#27AE60'],
    // ── Civic ─────────────────────────────────────────────────────────
    'Bank':                     ['🏦','#1A5276'],
    'Office':                   ['🏢','#2C3E50'],
    'Post Office':              ['📮','#E74C3C'],
    'Police Station':           ['👮','#2471A3'],
    'City Hall':                ['🏛️','#1A5276'],
    'Government Building':      ['🏛️','#154360'],
    'University':               ['🎓','#154360'],
    'Tech Startup':             ['💡','#F39C12'],
    'Business Center':          ['🏢','#2C3E50'],
    'Non-Profit Organization':  ['🤝','#1E8449'],
    // ── Roads & Generic ───────────────────────────────────────────────
    'Road':                     ['🛣️','#626567'],
    'Tunnel':                   ['🚧','#E67E22'],
    'Neighborhood':             ['🏘️','#5D6D7E'],
    'Intersection':             ['🔀','#717D7E'],
    'Housing Development':      ['🏘️','#1A5276'],
    'Bridge':                   ['🌉','#5D6D7E'],
    // ── Shops (extended) ──────────────────────────────────────────────
    'Miscellaneous Store':      ['🏪','#E67E22'],
    'Food and Beverage Retail': ['🛒','#27AE60'],
    'Sporting Goods Retail':    ['⚽','#27AE60'],
    'Health Food Store':        ['🥗','#1E8449'],
    'Butcher':                  ['🥩','#C0392B'],
    'Eyecare Store':            ['👓','#2471A3'],
    'Tailor':                   ['🧵','#7D3C98'],
    'Bike Rental':              ['🚲','#27AE60'],
    'Currency Exchange':        ['💱','#F39C12'],
    // ── Food extended ─────────────────────────────────────────────────
    'Diner':                    ['🍳','#E67E22'],
    'Bistro':                   ['🍽️','#C0392B'],
    'Breakfast Spot':           ['🥞','#F39C12'],
    'Bagel Shop':               ['🥯','#C8860A'],
    'Dessert Shop':             ['🍰','#F06292'],
    'Ice Cream Parlor':         ['🍦','#F06292'],
    'Sandwich Spot':            ['🥪','#E67E22'],
    'Noodle Restaurant':        ['🍜','#E74C3C'],
    'Turkish Restaurant':       ['🥙','#C0392B'],
    'Indian Restaurant':        ['🍛','#E67E22'],
    'Romanian Restaurant':      ['🍲','#C0392B'],
    'Ukrainian Restaurant':     ['🥟','#D4A017'],
    'Eastern European Restaurant':['🍲','#C0392B'],
    'Modern European Restaurant':['🍽️','#922B21'],
    'Middle Eastern Restaurant':['🧆','#D35400'],
    'Beer Garden':              ['🍻','#D4A017'],
    // ── Culture & Entertainment ────────────────────────────────────────
    'Concert Hall':             ['🎼','#922B21'],
    'Movie Theater':            ['🎬','#C0392B'],
    'Cultural Center':          ['🎭','#7D3C98'],
    'Exhibit':                  ['🖼️','#7D3C98'],
    'Science Museum':           ['🔬','#1A5276'],
    'Amusement Park':           ['🎡','#E74C3C'],
    'Memorial Site':            ['🕯️','#626567'],
    'Library':                  ['📚','#1A5276'],
    // ── Outdoors extended ─────────────────────────────────────────────
    'National Park':            ['🌲','#186A3B'],
    'Nature Preserve':          ['🌿','#1E8449'],
    'Forest':                   ['🌲','#186A3B'],
    'Botanical Garden':         ['🌺','#1E8449'],
    'Waterfall':                ['💦','#2980B9'],
    'Canal':                    ['🚤','#2471A3'],
    'Bay':                      ['🌊','#2980B9'],
    'Reservoir':                ['💧','#2471A3'],
    'Playground':               ['🛝','#E74C3C'],
    'Campground':               ['⛺','#186A3B'],
    'Ski Area':                 ['⛷️','#85C1E9'],
    'Well':                     ['🪣','#7F8C8D'],
    'Tree':                     ['🌳','#1E8449'],
    'Lighthouse':               ['🔦','#F39C12'],
    'Port':                     ['🚢','#154360'],
    'Pier':                     ['🎣','#2471A3'],
    // ── Religion extended ─────────────────────────────────────────────
    'Buddhist Temple':          ['☸️','#D4A017'],
    'Hindu Temple':             ['🛕','#E67E22'],
    'Shrine':                   ['⛩️','#C0392B'],
    // ── Education ─────────────────────────────────────────────────────
    'Education':                ['🎓','#1A5276'],
    'College Arts Building':    ['🎨','#7D3C98'],
    'College Technology Building':['💻','#2471A3'],
    'College Academic Building':['📖','#1A5276'],
    'College Residence Hall':   ['🏠','#2C3E50'],
    'College Library':          ['📚','#154360'],
    'College Gym':              ['💪','#C0392B'],
    'College Cafeteria':        ['🥣','#D4A017'],
    // ── Transport extended ────────────────────────────────────────────
    'Airport Lounge':           ['🛋️','#21618C'],
    'Airport':                  ['✈️','#154360'],
    'Airport Ticket Counter':   ['🎫','#2471A3'],
    'Baggage Claim':            ['🧳','#2C3E50'],
    'Travel and Transportation':['🧳','#2C3E50'],
    'Transportation Service':   ['🚐','#27AE60'],
    'Shipping, Freight, and Material Transportation Service':['📦','#7F8C8D'],
    'Moving Target':            ['📍','#E74C3C'],
    'Toll Plaza':               ['🛣️','#626567'],
    // ── Civic extended ────────────────────────────────────────────────
    'Embassy or Consulate':     ['🏛️','#1A5276'],
    'Hair Salon':               ['✂️','#F06292'],
    'Swimming Pool':            ['🏊','#2980B9'],
    'Pool Hall':                ['🎱','#2C3E50'],
    'Factory':                  ['🏭','#7F8C8D'],
    'Structure':                ['🏗️','#626567'],
    'Arts and Entertainment':   ['🎭','#7D3C98'],
    'Sports and Recreation':    ['⚽','#27AE60'],
    'Gym and Studio':           ['💪','#E74C3C'],
    // ── Geography labels ──────────────────────────────────────────────
    'City':                     ['🏙️','#2E86C1'],
    'Town':                     ['🏘️','#5D6D7E'],
    'Village':                  ['🏡','#5D6D7E'],
    'Country':                  ['🌍','#2E86C1'],
    'County':                   ['📍','#626567'],
    'State':                    ['📍','#626567'],
  };
  function catIcon(category){
    const entry=CAT_ICON[category];
    if(!entry) return '';
    const [emoji,bg]=entry;
    return `<div class="rc-cat-icon" style="background:${bg}22;border:1px solid ${bg}66" title="${category}">${emoji}</div>`;
  }


  scrollEl.innerHTML=recent.map((r,i)=>{
    const url=fsUrl(r);
    const tag=url?'a':'div', href=url?` href="${url}" target="_blank" rel="noopener"`:'';
    const streak=r.venue_id&&venueCounts[r.venue_id]>1
      ? `<div class="rc-streak">🔁 ${venueCounts[r.venue_id]}× in recent</div>` : '';
    const f=r.country?flagHtml(r.country,'1em'):'';
    const loc=[r.city,r.country].filter(Boolean);
    const locStr=loc.join(', ');
    return `<${tag}${href} class="recent-card" id="rc_${i}" style="${url?'text-decoration:none;cursor:pointer;':''}">
      ${tileBlock(r.lat,r.lng)}
      <div class="rc-venue-row"><div class="rc-venue">${esc(r.venue)||'Unknown venue'}</div>${catIcon(r.category)}</div>
      ${streak}
      <div class="rc-cat">${esc(r.category||'')}</div>
      <div class="rc-location">${f} ${esc(locStr)}</div>
      <div class="rc-weather" id="rcw_${i}"><span class="rc-weather-loading">fetching weather…</span></div>
      <div class="rc-date">${r.datetime}</div>
    </${tag}>`;
  }).join('');

  // ── Weather fetch (throttled) ──
  async function fetchWeather(r,i){
    if(!r.lat||!r.lng){document.getElementById('rcw_'+i).innerHTML='';return;}
    try{
      const url=`https://archive-api.open-meteo.com/v1/archive?latitude=${r.lat}&longitude=${r.lng}`+
        `&start_date=${r.date}&end_date=${r.date}&hourly=temperature_2m,weather_code&timezone=${encodeURIComponent(r.tz_name||'UTC')}`;
      const res=await fetch(url); const d=await res.json();
      const hour=parseInt(r.time.split(':')[0]);
      const temp=d.hourly?.temperature_2m?.[hour], code=d.hourly?.weather_code?.[hour];
      const [icon,desc]=WMO[code]||['🌡️',''];
      const el=document.getElementById('rcw_'+i);
      if(el) el.innerHTML=`<span class="rc-weather-icon">${icon}</span><span class="rc-weather-temp">${temp!=null?Math.round(temp)+'°C':'—'}</span><span class="rc-weather-desc">${desc}</span>`;
    }catch(e){const el=document.getElementById('rcw_'+i);if(el)el.innerHTML='';}
  }
  recent.forEach((r,i)=>setTimeout(()=>fetchWeather(r,i),i*120));
})();

// ── Main Map (heatmap / dots) ─────────────────────────────────────
const map=L.map('map',{preferCanvas:true}).setView([30,15],2);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{
  attribution:'&copy; OpenStreetMap &copy; CARTO',subdomains:'abcd',maxZoom:19}).addTo(map);
const status=document.getElementById('mapStatus');
let heatLayer=null,dotLayer=null,currentMode='heat';

// Heatmap: pre-computed p75-capped weights from Python (one point per ~330m cell).
// radius=14, blur=10 keeps individual cities distinct at world zoom.
// minOpacity=0.3 ensures even single-visit places show up on the map.
heatLayer=L.heatLayer(S.venues_heatmap,{
  radius:14, blur:10, maxZoom:18, max:1.0, minOpacity:0.3,
  gradient:{'0.0':'#0a1628','0.2':'#0d3b6e','0.4':'#1a6b9a','0.65':'#e8b86d','0.85':'#ff7700','1.0':'#ff2200'}
}).addTo(map);
status.textContent='Heatmap · '+S.venues_heatmap.length.toLocaleString()+' unique locations';
setTimeout(()=>status.style.opacity='0',2800);

function buildDots(){
  if(dotLayer)return; status.style.opacity='1';
  const pts=S.unique_places; let i=0; dotLayer=L.layerGroup();
  function chunk(){
    const end=Math.min(i+3000,pts.length);
    for(;i<end;i++) L.circleMarker([pts[i][0],pts[i][1]],{radius:3,color:'#e8b86d',fillColor:'#e8b86d',fillOpacity:0.65,weight:0})
      .bindTooltip(esc(pts[i][2]||''),{direction:'top',opacity:0.9}).addTo(dotLayer);
    status.textContent='Plotting '+i.toLocaleString()+' / '+pts.length.toLocaleString()+'...';
    if(i<pts.length)requestAnimationFrame(chunk);
    else{if(currentMode==='dots')dotLayer.addTo(map);status.style.opacity='0';}
  }
  requestAnimationFrame(chunk);
}

// ── Countries flag map ─────────────────────────────────────────────────────
let countriesMapInst=null;
function buildCountriesMap(){
  if(countriesMapInst) return;
  countriesMapInst=L.map('countriesMap',{preferCanvas:false,zoomControl:true}).setView([25,20],2);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png',{
    attribution:'&copy; OpenStreetMap &copy; CARTO',subdomains:'abcd',maxZoom:10}).addTo(countriesMapInst);

  const centroids=S.country_centroids;
  const counts=Object.values(centroids).map(v=>v[2]);
  const maxC=Math.max(...counts);

  // Sort by count ascending so high-traffic countries render on top
  const sorted=Object.entries(centroids).sort((a,b)=>a[1][2]-b[1][2]);

  sorted.forEach(([country,[lat,lng,count]])=>{
    const code=ISO2[country];

    // Scale: min 18px, max 42px based on sqrt(count/max)
    const size=Math.round(18+Math.sqrt(count/maxC)*24);
    const half=Math.round(size/2);

    // Flag width = size * 4/3 (flag-icons are 4:3 ratio)
    const fw=Math.round(size*1.33);

    // Build inner HTML: flag + count badge
    const flagEl=code
      ? `<span class="fi fi-${code}" style="width:${fw}px;height:${size}px;display:block;border-radius:3px;box-shadow:0 1px 4px rgba(0,0,0,.7)"></span>`
      : `<div style="width:${fw}px;height:${size}px;background:#2a2f45;border-radius:3px;display:flex;align-items:center;justify-content:center;font-size:${Math.round(size*.55)}px;box-shadow:0 1px 4px rgba(0,0,0,.7)">🌐</div>`;

    const badge=count>=100
      ? `<div style="position:absolute;top:-7px;right:-7px;background:#e8b86d;color:#0b0d13;font-family:'DM Mono',monospace;font-size:${Math.max(8,Math.round(size*.22))}px;font-weight:700;padding:1px 4px;border-radius:10px;white-space:nowrap;line-height:1.4">${count>=10000?(count/1000).toFixed(0)+'k':count>=1000?(count/1000).toFixed(1)+'k':count}</div>`
      : '';

    const icon=L.divIcon({
      className:'',
      html:`<div style="position:relative;cursor:pointer;transition:transform .15s" onmouseenter="this.style.transform='scale(1.25)'" onmouseleave="this.style.transform=''">${flagEl}${badge}</div>`,
      iconSize:[fw,size],
      iconAnchor:[half,half],
      popupAnchor:[0,-half],
    });

    L.marker([lat,lng],{icon})
      .bindTooltip(`<b>${esc(country)}</b> &nbsp;${count.toLocaleString()} check-ins`,{
        direction:'top',opacity:0.97,offset:[0,-half]})
      .addTo(countriesMapInst);
  });
}

function switchMap(mode){
  currentMode=mode;
  ['Heat','Dots','Countries'].forEach(m=>
    document.getElementById('tab'+m).classList.toggle('active',mode===m.toLowerCase()));
  if(mode==='heat'){
    document.getElementById('map').style.display='block';
    document.getElementById('countriesMap').style.display='none';
    if(dotLayer)map.removeLayer(dotLayer);
    heatLayer.addTo(map);
    status.textContent='Heatmap · '+S.venues_heatmap.length.toLocaleString()+' unique locations';
    status.style.opacity='1';setTimeout(()=>status.style.opacity='0',2500);
  } else if(mode==='dots'){
    document.getElementById('map').style.display='block';
    document.getElementById('countriesMap').style.display='none';
    map.removeLayer(heatLayer);
    if(dotLayer)dotLayer.addTo(map);else buildDots();
  } else {
    document.getElementById('map').style.display='none';
    document.getElementById('countriesMap').style.display='block';
    setTimeout(()=>{buildCountriesMap();countriesMapInst.invalidateSize();},50);
  }
}

</script>
</body>
</html>"""



TRIPS_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Trip Journal</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/flag-icon-css/6.6.6/css/flag-icons.min.css">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
<style>
:root{--bg:#0b0d13;--card:#12151f;--card2:#181c28;--border:#222738;--gold:#e8b86d;--teal:#4ecdc4;--muted:#4a5270;--text:#cdd5f0;--text2:#7a85a8;}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;}
a{color:inherit;}

/* ── TOP NAV ── */
.topnav{display:flex;align-items:center;gap:20px;padding:18px 56px;border-bottom:1px solid var(--border);background:var(--card);}
.topnav-logo{font-family:'Playfair Display',serif;font-size:1.1rem;font-weight:700;color:var(--gold);text-decoration:none;}
.topnav a{font-family:'DM Mono',monospace;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.14em;color:var(--muted);text-decoration:none;transition:color .2s;}
.topnav a:hover,.topnav a.active{color:var(--gold);}

/* ── VIEWS ── */
#listView,#detailView{min-height:calc(100vh - 57px);}
#detailView{display:none;}

/* ── LIST HEADER ── */
.list-header{padding:40px 56px 28px;display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:16px;}
.list-header h1{font-family:'Playfair Display',serif;font-size:clamp(1.8rem,4vw,3rem);font-weight:900;background:linear-gradient(130deg,#f5d48a 0%,#e8b86d 45%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.list-meta{font-family:'DM Mono',monospace;font-size:0.62rem;color:var(--muted);letter-spacing:.12em;}
.list-filters{padding:0 56px 20px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;}
.filter-search{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:8px 14px;color:var(--text);font-family:'DM Sans',sans-serif;font-size:.85rem;outline:none;min-width:220px;transition:border-color .2s;}
.filter-search:focus{border-color:var(--gold);}
.filter-pill{padding:5px 13px;border-radius:6px;font-family:'DM Mono',monospace;font-size:.60rem;text-transform:uppercase;letter-spacing:.1em;cursor:pointer;border:1px solid var(--border);background:var(--card2);color:var(--text2);transition:all .2s;}
.filter-pill.active{background:var(--gold);color:#0b0d13;border-color:var(--gold);}
.filter-pill:hover:not(.active){border-color:var(--gold);color:var(--gold);}

/* ── TRIPS GRID ── */
.trips-grid{padding:0 56px 72px;display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;}
.trip-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:22px 22px 18px;cursor:pointer;transition:border-color .2s,transform .15s;position:relative;overflow:hidden;text-decoration:none;display:block;}
.trip-card::after{content:'';position:absolute;top:0;left:20px;right:20px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:.3;}
.trip-card:hover{border-color:var(--gold);transform:translateY(-2px);}
.trip-card.hidden{display:none;}
.tc-num{font-family:'DM Mono',monospace;font-size:.56rem;color:var(--muted);margin-bottom:6px;}
.tc-name{font-size:1rem;font-weight:600;color:var(--text);line-height:1.3;margin-bottom:8px;}
.tc-dates{font-family:'DM Mono',monospace;font-size:.62rem;color:var(--teal);margin-bottom:10px;}
.tc-countries{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:12px;}
.tc-country{font-family:'DM Mono',monospace;font-size:.56rem;padding:3px 8px;border-radius:4px;background:rgba(78,205,196,.1);color:var(--teal);border:1px solid rgba(78,205,196,.2);}
.tc-stats{display:flex;gap:16px;}
.tc-stat{display:flex;flex-direction:column;gap:2px;}
.tc-stat-v{font-family:'DM Mono',monospace;font-size:.80rem;color:var(--gold);}
.tc-stat-l{font-size:.60rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;}

/* ── DETAIL VIEW ── */
.detail-back{display:flex;align-items:center;gap:8px;padding:18px 56px 0;font-family:'DM Mono',monospace;font-size:.62rem;color:var(--muted);cursor:pointer;text-transform:uppercase;letter-spacing:.12em;width:max-content;transition:color .2s;}
.detail-back:hover{color:var(--gold);}
.detail-back::before{content:'←';}
.detail-hero{padding:28px 56px 24px;display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:20px;border-bottom:1px solid var(--border);}
.detail-hero h2{font-family:'Playfair Display',serif;font-size:clamp(1.6rem,4vw,2.6rem);font-weight:900;background:linear-gradient(130deg,#f5d48a,#e8b86d,#b97c30);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;line-height:1.1;}
.detail-dates{font-family:'DM Mono',monospace;font-size:.65rem;color:var(--muted);margin-top:6px;}
.detail-kpis{display:flex;gap:24px;flex-wrap:wrap;}
.detail-kpi .num{font-family:'Playfair Display',serif;font-size:1.8rem;font-weight:700;color:var(--gold);line-height:1;}
.detail-kpi .lbl{font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);margin-top:4px;}
.detail-body{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:24px 56px 60px;max-width:1400px;}
.detail-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:22px 24px;position:relative;overflow:hidden;}
.detail-card::after{content:'';position:absolute;top:0;left:20px;right:20px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:.3;}
.detail-card.full{grid-column:1/-1;}
.detail-card-title{font-family:'DM Mono',monospace;font-size:.60rem;text-transform:uppercase;letter-spacing:.18em;color:var(--gold);margin-bottom:16px;}
#detailMap{height:380px;border-radius:8px;}
.detail-countries{display:flex;flex-wrap:wrap;gap:6px;}
.detail-country{background:var(--card2);border:1px solid var(--border);border-radius:6px;padding:6px 12px;font-size:.78rem;}
.detail-timeline{max-height:440px;overflow-y:auto;padding-right:4px;}
.detail-timeline::-webkit-scrollbar{width:3px;}
.detail-timeline::-webkit-scrollbar-thumb{background:var(--border);}
.tl-day{margin-bottom:18px;}
.tl-day-header{font-family:'DM Mono',monospace;font-size:.60rem;text-transform:uppercase;letter-spacing:.14em;color:var(--gold);margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid var(--border);}
.tl-checkin{display:flex;gap:10px;align-items:flex-start;padding:5px 0;}
.tl-checkin-time{font-family:'DM Mono',monospace;font-size:.60rem;color:var(--muted);flex-shrink:0;width:40px;}
.tl-checkin-info{flex:1;min-width:0;}
.tl-checkin-venue{font-size:.80rem;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.tl-checkin-venue a{color:inherit;text-decoration:none;}
.tl-checkin-venue a:hover{color:var(--gold);}
.tl-checkin-sub{font-size:.65rem;color:var(--muted);}
.cat-bar-row{display:flex;align-items:center;gap:8px;margin-bottom:6px;}
.cat-bar-name{font-size:.75rem;color:var(--text);flex:1;}
.cat-bar-track{width:100px;height:5px;background:var(--card2);border-radius:3px;overflow:hidden;}
.cat-bar-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--gold),var(--teal));}
.cat-bar-cnt{font-family:'DM Mono',monospace;font-size:.62rem;color:var(--muted);width:36px;text-align:right;}

@media(max-width:900px){
  .topnav,.list-header,.list-filters,.trips-grid,.detail-back,.detail-hero,.detail-body{padding-left:18px;padding-right:18px;}
  .trips-grid{grid-template-columns:1fr 1fr;}
  .detail-body{grid-template-columns:1fr;}
}
@media(max-width:520px){
  .trips-grid{grid-template-columns:1fr;}
  .detail-kpis{gap:14px;}
  .detail-kpi .num{font-size:1.4rem;}
}
</style>
</head>
<body>

<nav class="topnav">
  <a href="index.html" class="topnav-logo">Check-in Journal</a>
  <a href="index.html">← Dashboard</a>
  <a href="trips.html" class="active">Trips</a>
</nav>

<!-- ── LIST VIEW ── -->
<div id="listView">
  <div class="list-header">
    <div>
      <h1>Trip Journal</h1>
      <div class="list-meta">{{TOTAL_TRIPS}} trips &nbsp;·&nbsp; updated {{UPDATED}}</div>
    </div>
  </div>
  <div class="list-filters">
    <input class="filter-search" type="text" placeholder="Search trips…" id="tripSearch" oninput="filterTrips()">
    <div id="yearPills" style="display:flex;gap:6px;flex-wrap:wrap;"></div>
  </div>
  <div class="trips-grid" id="tripsGrid"></div>
</div>

<!-- ── DETAIL VIEW ── -->
<div id="detailView">
  <div class="detail-back" onclick="showList()">All trips</div>
  <div class="detail-hero">
    <div>
      <h2 id="detailName"></h2>
      <div class="detail-dates" id="detailDates"></div>
    </div>
    <div class="detail-kpis" id="detailKpis"></div>
  </div>
  <div class="detail-body">
    <div class="detail-card full">
      <div class="detail-card-title">Map</div>
      <div id="detailMap"></div>
    </div>
    <div class="detail-card">
      <div class="detail-card-title">Countries & Cities</div>
      <div id="detailCountries" class="detail-countries"></div>
    </div>
    <div class="detail-card">
      <div class="detail-card-title">Top Categories</div>
      <div id="detailCats"></div>
    </div>
    <div class="detail-card full">
      <div class="detail-card-title">Check-in Timeline</div>
      <div class="detail-timeline" id="detailTimeline"></div>
    </div>
  </div>
</div>

<script>
const TRIPS = {{TRIPS_JSON}};
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
let activeMap = null;

// ── Build the grid ──────────────────────────────────────────────────────────
function renderGrid(trips){
  const grid = document.getElementById('tripsGrid');
  grid.innerHTML = trips.map(t => `
    <a class="trip-card" id="card-trip-${t.id}" href="#trip-${t.id}" onclick="showTrip(${t.id});return false;">
      <div class="tc-num">Trip #${t.id}</div>
      <div class="tc-name">${esc(t.name)}</div>
      <div class="tc-dates">${t.start_date} &nbsp;–&nbsp; ${t.end_date}</div>
      <div class="tc-countries">${t.countries.slice(0,5).map(c=>`<span class="tc-country">${c}</span>`).join('')}</div>
      <div class="tc-stats">
        <div class="tc-stat"><div class="tc-stat-v">${t.duration}</div><div class="tc-stat-l">Days</div></div>
        <div class="tc-stat"><div class="tc-stat-v">${t.checkin_count.toLocaleString()}</div><div class="tc-stat-l">Check-ins</div></div>
        <div class="tc-stat"><div class="tc-stat-v">${t.unique_places.toLocaleString()}</div><div class="tc-stat-l">Places</div></div>
        <div class="tc-stat"><div class="tc-stat-v">${t.countries.length}</div><div class="tc-stat-l">Countries</div></div>
      </div>
    </a>`).join('');
}
renderGrid(TRIPS);

// ── Year filter pills ───────────────────────────────────────────────────────
const years = [...new Set(TRIPS.map(t => t.start_year))].sort();
const pillsEl = document.getElementById('yearPills');
let activeYear = null;
['All',...years].forEach(y => {
  const p = document.createElement('div');
  p.className = 'filter-pill' + (y==='All'?' active':'');
  p.textContent = y;
  p.onclick = () => {
    document.querySelectorAll('.filter-pill').forEach(x=>x.classList.remove('active'));
    p.classList.add('active');
    activeYear = y==='All' ? null : y;
    filterTrips();
  };
  pillsEl.appendChild(p);
});

function filterTrips(){
  const q = document.getElementById('tripSearch').value.toLowerCase();
  TRIPS.forEach(t => {
    const card = document.getElementById('card-trip-'+t.id);
    const matchYear = !activeYear || t.start_year === activeYear;
    const matchQ = !q || t.name.toLowerCase().includes(q)
      || t.countries.some(c=>c.toLowerCase().includes(q))
      || t.cities.some(c=>c.toLowerCase().includes(q))
      || t.start_date.includes(q);
    card.classList.toggle('hidden', !(matchYear && matchQ));
  });
}

// ── Show detail ─────────────────────────────────────────────────────────────
function showTrip(id){
  const t = TRIPS.find(x=>x.id===id);
  if(!t) return;
  history.pushState({trip:id},'','#trip-'+id);
  document.getElementById('listView').style.display = 'none';
  document.getElementById('detailView').style.display = 'block';
  document.getElementById('detailName').textContent = t.name;
  document.getElementById('detailDates').textContent = t.start_date + ' – ' + t.end_date;
  document.getElementById('detailKpis').innerHTML =
    `<div class="detail-kpi"><div class="num">${t.duration}</div><div class="lbl">Days</div></div>
     <div class="detail-kpi"><div class="num">${t.checkin_count.toLocaleString()}</div><div class="lbl">Check-ins</div></div>
     <div class="detail-kpi"><div class="num">${t.unique_places.toLocaleString()}</div><div class="lbl">Unique Places</div></div>
     <div class="detail-kpi"><div class="num">${t.countries.length}</div><div class="lbl">Countries</div></div>`;
  // Countries + cities
  document.getElementById('detailCountries').innerHTML =
    t.countries.map(c=>`<span class="detail-country">🌍 ${c}</span>`).join('')
    + '<br style="margin:8px 0">'
    + t.cities.slice(0,12).map(c=>`<span class="detail-country" style="background:rgba(232,184,109,.05);border-color:rgba(232,184,109,.15);color:var(--text2);">📍 ${c}</span>`).join('');
  // Categories
  const maxCat = t.top_cats[0]?t.top_cats[0][1]:1;
  document.getElementById('detailCats').innerHTML = t.top_cats.map(([cat,cnt])=>
    `<div class="cat-bar-row">
      <span class="cat-bar-name">${cat}</span>
      <div class="cat-bar-track"><div class="cat-bar-fill" style="width:${(cnt/maxCat*100).toFixed(1)}%"></div></div>
      <span class="cat-bar-cnt">${cnt}</span>
    </div>`).join('');
  // Timeline grouped by day
  const byDay = {};
  t.checkins.forEach(c=>{
    if(!byDay[c.date]) byDay[c.date] = [];
    byDay[c.date].push(c);
  });
  document.getElementById('detailTimeline').innerHTML = Object.keys(byDay).sort().map(day=>
    `<div class="tl-day">
      <div class="tl-day-header">${day} &nbsp;·&nbsp; ${byDay[day].length} check-ins</div>
      ${byDay[day].map(c=>{
        const fsUrl = c.venue_id ? `https://app.foursquare.com/v/${encodeURIComponent(c.venue.toLowerCase().replace(/\s+/g,'-'))}/${c.venue_id}` : null;
        return `<div class="tl-checkin">
          <div class="tl-checkin-time">${c.time}</div>
          <div class="tl-checkin-info">
            <div class="tl-checkin-venue">${fsUrl?`<a href="${fsUrl}" target="_blank" rel="noopener">${c.venue}</a>`:c.venue}</div>
            <div class="tl-checkin-sub">${[c.category,c.city,c.country].filter(Boolean).join(' · ')}</div>
          </div>
        </div>`;
      }).join('')}
    </div>`).join('');
  // Map
  if(activeMap){ activeMap.remove(); activeMap=null; }
  setTimeout(()=>{
    activeMap = L.map('detailMap',{preferCanvas:true});
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      {attribution:'© OpenStreetMap © CARTO',subdomains:'abcd',maxZoom:19}).addTo(activeMap);
    const coords = t.coords.filter(c=>c[0]&&c[1]);
    if(coords.length){
      const heat = L.heatLayer(coords,{radius:14,blur:16,maxZoom:18,
        gradient:{'0.0':'#000033','0.25':'#0a3d6b','0.5':'#e8b86d','0.75':'#ff7700','1.0':'#ff1100'}}).addTo(activeMap);
      const lats=coords.map(c=>c[0]), lngs=coords.map(c=>c[1]);
      activeMap.fitBounds([[Math.min(...lats),Math.min(...lngs)],[Math.max(...lats),Math.max(...lngs)]],{padding:[20,20]});
    }
  },50);
  window.scrollTo(0,0);
}

function showList(){
  document.getElementById('detailView').style.display = 'none';
  document.getElementById('listView').style.display = 'block';
  if(activeMap){ activeMap.remove(); activeMap=null; }
  history.pushState({},'','trips.html');
  window.scrollTo(0,0);
}

// Handle direct #trip-N links & back button
function handleHash(){
  const m = location.hash.match(/^#trip-(\d+)$/);
  if(m){ showTrip(parseInt(m[1])); }
  else { showList(); }
}
window.addEventListener('popstate', handleHash);
handleHash();
</script>
</body>
</html>"""



def build(data, trips, out_dir='.'):
    import os
    # ── index.html ──────────────────────────────────────────────────────────
    html = TEMPLATE
    html = html.replace('{{DATE_MIN}}',  data['date_min'])
    html = html.replace('{{DATE_MAX}}',  data['date_max'])
    html = html.replace('{{TOTAL}}',     f"{data['total']:,}")
    html = html.replace('{{COUNTRIES}}', str(len(data['countries'])))
    html = html.replace('{{CITIES}}',    f"{len(data['cities']):,}")
    html = html.replace('{{PLACES}}',    f"{data['unique_places_count']:,}")
    html = html.replace('{{UPDATED}}',   datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    html = html.replace('{{TRIPS}}',     str(data['trips_count']))
    html = html.replace('{{STATS}}',     json.dumps(data, ensure_ascii=False))
    idx_path = os.path.join(out_dir, 'index.html')
    with open(idx_path, 'w', encoding='utf-8') as f: f.write(html)
    print(f"Built → {idx_path}  ({len(html)//1024:,} KB)")

    # ── trips.html ──────────────────────────────────────────────────────────
    trips_html = TRIPS_TEMPLATE
    trips_html = trips_html.replace('{{TRIPS_JSON}}', json.dumps(trips, ensure_ascii=False))
    trips_html = trips_html.replace('{{TOTAL_TRIPS}}', str(len(trips)))
    trips_html = trips_html.replace('{{UPDATED}}', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    trips_path = os.path.join(out_dir, 'trips.html')
    with open(trips_path, 'w', encoding='utf-8') as f: f.write(trips_html)
    print(f"Built → {trips_path}  ({len(trips_html)//1024:,} KB)")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Foursquare check-in dashboard")
    parser.add_argument("--input",       default=str(_SCRIPT_DIR / "checkins.csv"),
                        help="Input CSV file (default: checkins.csv next to build.py)")
    parser.add_argument("--config-dir",  default=str(_SCRIPT_DIR / "config"),
                        help="Directory with config JSON/YAML files (default: config/ next to build.py)")
    parser.add_argument("--output-dir",  default=str(_SCRIPT_DIR),
                        help="Output directory for HTML files (default: same dir as build.py)")
    parser.add_argument("--home-city",   default=None,
                        help="Override home city (default: from settings.yaml, fallback Minsk)")
    parser.add_argument("--min-checkins",type=int, default=None,
                        help="Override min check-ins for a trip")
    parser.add_argument("--cat-list",    action="store_true",
                        help="Also write category_list.txt")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        log.error("Input file not found: %s", args.input)
        sys.exit(1)

    config_dir = Path(args.config_dir)
    settings   = load_settings(config_dir)
    trip_cfg   = settings.get("trip_detection", {})

    home_city     = args.home_city     or trip_cfg.get("home_city",    "Minsk")
    min_checkins  = args.min_checkins  or trip_cfg.get("min_checkins", 5)

    log.info("Loading mappings from %s …", config_dir)
    mappings = load_mappings(config_dir)

    log.info("Reading %s …", args.input)
    with open(args.input, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    log.info("  %d rows loaded", len(rows))

    rows = apply_transforms(rows, mappings)

    log.info("Computing metrics (home=%s, min_checkins=%d) …", home_city, min_checkins)
    data, trips = process(rows, mappings, home_city=home_city, min_trip_checkins=min_checkins)

    os.makedirs(args.output_dir, exist_ok=True)
    build(data, trips, out_dir=args.output_dir)

    if args.cat_list:
        save_category_list(rows, os.path.join(args.output_dir, "category_list.txt"))

    log.info("Done!")

