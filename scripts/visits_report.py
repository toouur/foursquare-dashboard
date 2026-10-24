# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
visits_report.py — weekly private Telegram digest of site visits (D1 `visits`).

1. Retention: deletes rows older than 90 days (IPs are personal data; /api/hit
   also does this for 1 in 200 hits as a backstop).
2. Report: the last 7 days vs the 7 before — views, unique visitors and IPs,
   top pages, countries, cities, referrers, networks, and the newest visitors
   with IP / city / network. The owner's own views (owner = 1) are excluded.
3. Sends it to TELEGRAM_CHAT_ID — the private alerts chat, never the public
   on-this-day channel.

Needs CF_D1_TOKEN (+ CF_ACCOUNT_ID / CF_D1_DATABASE_ID, defaults in d1_client),
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. --dry-run prints instead of sending and
deletes nothing. Exit 0 ok, 1 on a D1 or Telegram failure.

    python scripts/visits_report.py --dry-run
"""
from __future__ import annotations

import argparse
import html
import logging
import os
import re
import sys
import time
from pathlib import Path

import requests

import d1_client as d1

log = logging.getLogger("visits_report")
RETENTION_DAYS = 90
WEEK = 7 * 86400


def _q(sql: str, params: list) -> list[dict]:
    return d1.query(sql, params)


def collect(now: int) -> dict:
    a, b = now - WEEK, now - 2 * WEEK
    w = "WHERE ts >= ? AND owner = 0"
    tot = _q(f"SELECT COUNT(*) AS views, COUNT(DISTINCT COALESCE(vid, ip)) AS visitors, COUNT(DISTINCT ip) AS ips FROM visits {w}", [a])
    prev = _q("SELECT COUNT(*) AS views, COUNT(DISTINCT COALESCE(vid, ip)) AS visitors FROM visits "
              "WHERE ts >= ? AND ts < ? AND owner = 0", [b, a])
    top = lambda col, n=8: _q(f"SELECT {col}, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits {w} "  # noqa: E731
                              f"GROUP BY {col} ORDER BY n DESC LIMIT {n}", [a])
    returning = _q(f"SELECT COUNT(DISTINCT COALESCE(vid, ip)) AS n FROM visits {w} AND COALESCE(vid, ip) IN "
                   "(SELECT COALESCE(vid, ip) FROM visits WHERE ts < ? AND owner = 0)", [a, a])
    newest = _q(f"SELECT COALESCE(vid, ip) AS who, MIN(ts) AS first, COUNT(*) AS n, MAX(ip) AS ip, MAX(city) AS city, "
                f"MAX(country) AS country, MAX(org) AS org FROM visits {w} GROUP BY who ORDER BY first DESC LIMIT 10", [a])
    return {
        "totals": tot[0] if tot else {}, "prev": prev[0] if prev else {},
        "returning": (returning[0] if returning else {}).get("n", 0),
        "paths": top("path"), "countries": top("country"),
        "cities": _q(f"SELECT city, country, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits {w} "
                     "GROUP BY city, country ORDER BY n DESC LIMIT 8", [a]),
        "refs": _q(f"SELECT ref, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits {w} AND ref IS NOT NULL "
                   "AND ref != '' GROUP BY ref ORDER BY n DESC LIMIT 6", [a]),
        "orgs": top("org", 6), "newest": newest,
    }


def _delta(cur: int, prev: int) -> str:
    if not prev:
        return ""
    d = round(100 * (cur - prev) / prev)
    return f" ({'+' if d >= 0 else ''}{d}% vs prev week)"


def render(c: dict, site: str = "https://4sq.pages.dev") -> str:
    def e(s, n=70):
        s = str(s if s is not None else "—")
        return html.escape(s if len(s) <= n else s[:n - 1] + "…", quote=False)
    T, P = c["totals"], c["prev"]
    views, visitors = int(T.get("views") or 0), int(T.get("visitors") or 0)
    lines = ["<b>📈 Site visits · last 7 days</b>",
             f"{views:,} views{_delta(views, int(P.get('views') or 0))}",
             f"{visitors:,} visitors{_delta(visitors, int(P.get('visitors') or 0))} · "
             f"{int(c.get('returning') or 0):,} returning · {int(T.get('ips') or 0):,} IPs"]
    if not views:
        lines.append("No visits recorded this week.")
        return "\n".join(lines)

    def block(title, rows, label):
        if rows:
            lines.append("")
            lines.append(f"<b>{title}</b>")
            lines.extend(f"• {label(r)} — {r['n']}" + (f" ({r['u']} u)" if "u" in r else "") for r in rows)
    block("Pages", c["paths"], lambda r: e(r["path"]))
    block("Countries", c["countries"], lambda r: e(r["country"]))
    block("Cities", c["cities"], lambda r: f"{e(r['city'])}, {e(r['country'])}")
    block("Referrers", c["refs"], lambda r: e(r["ref"]))
    block("Networks", c["orgs"], lambda r: e(r["org"]))
    if c["newest"]:
        lines += ["", "<b>Newest visitors</b>"]
        lines += [f"• {time.strftime('%d.%m %H:%M', time.gmtime(int(r['first'])))} UTC · {e(r['ip'])} · "
                  f"{e(r['city'])}, {e(r['country'])} · {e(r['org'])} · {r['n']} views" for r in c["newest"]]
    lines += ["", f'<a href="{site}/visits.html">Full stats</a> · owner views excluded · kept {RETENTION_DAYS} days']
    return "\n".join(lines)


def _fit(text: str, limit: int = 3900) -> str:
    """Telegram caps a message at 4096 chars and rejects HTML cut inside a tag:
    cut at the last line break instead."""
    if len(text) <= limit:
        return text
    return text[:text.rfind("\n", 0, limit)] + "\n…"


def visits_ddl(schema_path: Path = Path(__file__).with_name("d1_schema.sql")) -> list[str]:
    """The `visits` statements of d1_schema.sql, split the way apply_schema() splits."""
    sql = "\n".join(ln for ln in schema_path.read_text(encoding="utf-8").splitlines()
                    if not ln.strip().startswith("--"))
    return [st.strip() for st in sql.split(";")
            if st.strip() and re.search(r"\b(TABLE|ON)\s+(IF NOT EXISTS\s+)?visits\b", st)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dry-run", action="store_true", help="print the report, send and delete nothing")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    d1.configure(os.environ.get("CF_D1_TOKEN", ""))
    now = int(time.time())
    try:
        # The table is normally created by update-dashboard's --schema-only, but
        # that step only runs on a deploy, and a deploy only follows new data —
        # so the first report could meet a database without it ("no such table").
        for stmt in visits_ddl():
            d1.query(stmt)
        if not args.dry_run:
            d1.query("DELETE FROM visits WHERE ts < ?", [now - RETENTION_DAYS * 86400])
        text = render(collect(now))
    except Exception as exc:
        log.error("D1 failed: %s", exc)
        return 1
    if args.dry_run:
        print(text)
        return 0
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        return 1
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30, data={
        "chat_id": chat, "text": _fit(text), "parse_mode": "HTML", "disable_web_page_preview": "true"})
    if not r.ok:
        log.error("sendMessage failed: %s %s", r.status_code, r.text[:300])
        return 1
    log.info("Visits report sent (%d chars)", len(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
