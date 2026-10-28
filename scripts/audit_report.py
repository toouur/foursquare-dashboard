#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""audit_report.py — monthly list of data to look at, never an auto-fix.

Three sections, each one only what a human has not reviewed yet:
  • spelling twins — two displayed cities that look like one place
    (check_city_count.find_spelling_twins; known pairs: city_twins_ok.json)
  • unknown categories — raw Foursquare categories categories.json lacks
    (they group as "Other" until added)
  • category suspects — a venue whose NAME says one kind of place while its
    category groups it as another ("Белоруснефть" filed as a car repair shop).
    Reviewed venues go in config/category_audit_ok.json and are not shown again;
    a real mistake is fixed with a venue_category_fixes.json pin.

Prints markdown; with --telegram also sends a short summary to the private
alerts chat (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID), only when there are findings.
--seed-ok writes every current suspect into category_audit_ok.json (after a
manual review of the whole list).

Usage:
    python scripts/audit_report.py --csv $DATA/checkins.csv --backfill $DATA/backfill.csv
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# name pattern -> the explorer group such a place belongs to
HINTS: list[tuple[str, str]] = [
    ("Coffee Shops", r"(?i)coffee|кофе|кофейн|кава\b|кавярн|espresso|эспрессо|roaster|обжар"),
    ("Bars, Pubs & Breweries", r"(?i)\bpub\b|\bпаб\b|пивн|brewery|пивовар|craft beer|taproom"),
    ("Bakeries & Pastry", r"(?i)bakery|пекар|булочн|кондитерск|patisserie|pâtisserie|boulangerie"),
    ("Fast Food & Street Food", r"(?i)\bkfc\b|mcdonald|макдональдс|burger king|шаурм|shawarma|döner|doner"),
    ("Cafeterias", r"(?i)столов|cafeteria|canteen"),
    ("Supermarkets", r"(?i)евроопт|гиппо|hippo|соседи|виталюр|пятёрочк|lidl|aldi|kaufland|billa|carrefour|"
                     r"linella|tesco|biedronka|maxima|rimi\b|auchan|ашан|супермаркет|supermarket|гастроном"),
    ("Pharmacies", r"(?i)аптек|pharmac|farmaci|apotheke|apteka"),
    ("Fuel Stations", r"(?i)\bазс\b|белоруснефть|лукойл|lukoil|petrom|rompetrol|\bokko\b|\bwog\b|neste|orlen|circle k|"
                      r"gas station|газпромнефть|татнефть|а-100"),
    ("Accommodation", r"(?i)hotel|hostel|гостиниц|отель|хостел|guest ?house|гостев|апарт|aparthotel|motel|мотель"),
    ("Churches & Mosques", r"(?i)\bchurch|церковь|храм\b|cathedral|костёл|костел|kościół|mosque|мечеть|синагог|synagogue"),
    ("Museums", r"(?i)museum|музей|muzeu|muzeum"),
    ("Hookah Lounges", r"(?i)кальянн|hookah|shisha"),
    ("Health", r"(?i)поликлиник|больниц|hospital|clinic|клиник|стоматолог|dental"),
]
# Places named AFTER something (a square by the opera, a stop at the clinic, a
# metro station by the market) — their name says nothing about what they are.
NAMED_AFTER = {"Plazas", "Streets & Roads", "Bridges", "Fountains", "Monuments & Sculptures",
               "Neighborhoods", "Public Transit", "Parks", "Gardens", "Historic Sites",
               "Seas, Rivers & Lakes", "Cities, Towns & Villages", "Parking", "Waterfront"}
# Explorer groups close enough that a hint for one is satisfied by the other.
SAME = {"Bakeries & Pastry": {"Bakery / Sweets"}, "Bars, Pubs & Breweries": {"Wine & Cocktail"}}


def _rows(csv_path: str, backfill: str | None) -> list[dict]:
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if backfill and Path(backfill).exists():
        with open(backfill, encoding="utf-8") as f:
            rows += list(csv.DictReader(f))
    return rows


def unknown_categories(rows: list[dict], cfg: dict) -> list[tuple[str, int]]:
    known = {n.lower() for g, ns in cfg["category_groups"].items() if not g.startswith("_") for n in ns}
    known &= {n.lower() for ns in cfg["explorer_groups"].values() for n in ns}
    c = Counter((r.get("category") or "").strip() for r in rows)
    return [(k, n) for k, n in c.most_common() if k and k.lower() not in known]


def category_suspects(rows: list[dict], cfg: dict, pins: dict, ok: set[str],
                      min_checkins: int = 2) -> list[dict]:
    explorer = {n.lower(): g for g, ns in cfg["explorer_groups"].items() for n in ns}
    n: Counter = Counter()
    last: dict[str, dict] = {}
    for r in rows:
        vid = (r.get("venue_id") or "").strip()
        if vid:
            n[vid] += 1
            last[vid] = r
    out = []
    for vid, k in n.items():
        if k < min_checkins or vid in ok or vid in pins:
            continue
        r = last[vid]
        group = explorer.get((r.get("category") or "").strip().lower())
        if group in NAMED_AFTER:
            continue
        for want, pat in HINTS:
            if re.search(pat, r.get("venue") or ""):
                if group != want and group not in SAME.get(want, ()):
                    out.append({"venue_id": vid, "venue": r.get("venue", ""), "city": r.get("city", ""),
                                "category": r.get("category", ""), "group": group, "looks_like": want,
                                "checkins": k})
                break
    return sorted(out, key=lambda s: -s["checkins"])


def spelling_twins(csv_path: str, backfill: str | None, config_dir: str) -> list[tuple]:
    import check_city_count as ccc
    counts, centroids = ccc.compute_city_counts(csv_path, config_dir, backfill)
    return [(s, b, r, d, counts[s], counts[b])
            for s, b, r, d in ccc.find_spelling_twins(counts, centroids, ccc.load_twin_allowlist(config_dir))]


def render(twins: list[tuple], unknown: list[tuple[str, int]], suspects: list[dict]) -> str:
    lines = ["## Monthly data audit", ""]
    if not (twins or unknown or suspects):
        return "\n".join(lines + ["Nothing new to review."])
    if twins:
        lines += [f"### Spelling twins ({len(twins)})", ""]
        lines += [f"- {s} ({ns}) ~ {b} ({nb}) — similarity {r:.2f}, {d:.1f} km" for s, b, r, d, ns, nb in twins]
        lines += ["", "Fix: `city_merge.yaml` onto the canonical name, or `city_twins_ok.json`.", ""]
    if unknown:
        lines += [f"### Categories missing from categories.json ({len(unknown)})", ""]
        lines += [f"- {c} × {k}" for c, k in unknown]
        lines += [""]
    if suspects:
        lines += [f"### Category suspects ({len(suspects)})", ""]
        lines += [f"- {s['venue']} ({s['city']}, {s['checkins']}) — {s['category']} → {s['group']}; "
                  f"looks like {s['looks_like']} · `{s['venue_id']}`" for s in suspects]
        lines += ["", "Fix: pin in `venue_category_fixes.json`, or add the id to `category_audit_ok.json`."]
    return "\n".join(lines)


def telegram_text(twins: list[tuple], unknown: list[tuple[str, int]], suspects: list[dict], run_url: str) -> str:
    e = html.escape
    parts = ["🧹 <b>Monthly data audit</b>"]
    if twins:
        parts.append(f"\n<b>Spelling twins: {len(twins)}</b>")
        parts += [f"• {e(s)} ~ {e(b)} ({d:.0f} km)" for s, b, _, d, _, _ in twins[:10]]
    if unknown:
        parts.append(f"\n<b>New categories: {len(unknown)}</b>")
        parts += [f"• {e(c)} × {k}" for c, k in unknown[:10]]
    if suspects:
        parts.append(f"\n<b>Category suspects: {len(suspects)}</b>")
        parts += [f"• {e(s['venue'])} ({e(s['city'])}) — {e(s['category'])}, looks like {e(s['looks_like'])}"
                  for s in suspects[:15]]
    if run_url:
        parts.append(f"\n<a href=\"{e(run_url)}\">Full list</a>")
    return "\n".join(parts)[:4000]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--backfill")
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--telegram", action="store_true", help="Send the summary to the private alerts chat")
    ap.add_argument("--seed-ok", action="store_true",
                    help="Mark every current category suspect as reviewed (category_audit_ok.json)")
    args = ap.parse_args()

    cdir = Path(args.config_dir)
    cfg = json.loads((cdir / "categories.json").read_text(encoding="utf-8"))
    pins = {k for k in json.loads((cdir / "venue_category_fixes.json").read_text(encoding="utf-8"))
            if not k.startswith("_")}
    ok_path = cdir / "category_audit_ok.json"
    ok_doc = json.loads(ok_path.read_text(encoding="utf-8")) if ok_path.exists() else {}
    ok = set(ok_doc.get("venue_ids", []))

    rows = _rows(args.csv, args.backfill)
    suspects = category_suspects(rows, cfg, {p: 1 for p in pins}, ok)
    if args.seed_ok:
        ok_doc.setdefault("_comment", "Venues audit_report.py flagged as category suspects that a human "
                                      "reviewed and left as Foursquare has them. Not shown again.")
        ok_doc["venue_ids"] = sorted(ok | {s["venue_id"] for s in suspects})
        ok_path.write_text(json.dumps(ok_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Marked {len(suspects)} suspect(s) as reviewed in {ok_path}")
        return 0

    twins = spelling_twins(args.csv, args.backfill, args.config_dir)
    unknown = unknown_categories(rows, cfg)
    print(render(twins, unknown, suspects))

    if args.telegram and (twins or unknown or suspects):
        import requests
        token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — not sent", file=sys.stderr)
            return 1
        run_url = ""
        if os.environ.get("GITHUB_RUN_ID"):
            run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                       f"{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30, data={
            "chat_id": chat, "text": telegram_text(twins, unknown, suspects, run_url),
            "parse_mode": "HTML", "disable_web_page_preview": "true"})
        if r.status_code != 200:
            print(f"Telegram sendMessage failed: {r.status_code} {r.text[:300]}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
