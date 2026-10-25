# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
visits_report.py — weekly private Telegram digest of site visits (D1 `visits`).

1. Retention: deletes rows older than 90 days (IPs are personal data; /api/hit
   also does this for 1 in 200 hits as a backstop).
2. Report: the last 7 days vs the 7 before — views, unique visitors/IPs, pages,
   geography/referrers/networks, browser language, device type/screen, device vs
   IP timezone, Android model where exposed, approximate postal area, visible page
   time, 30-minute session depth, and newest visitors. Owner views are excluded.
3. Sends it to TELEGRAM_CHAT_ID — the private alerts chat, never the public
   on-this-day channel.

--ping is the short in-between note (every 3 h from the workflow): only the
visitors since the previous ping, and nothing is sent when there were none. The
watermark lives in D1 `sync_state` (key 'visits_ping'), so a late or skipped cron
run neither drops nor repeats visits; it is moved only after a successful send.

Needs CF_D1_TOKEN (+ CF_ACCOUNT_ID / CF_D1_DATABASE_ID, defaults in d1_client),
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. --dry-run prints instead of sending and
deletes nothing. Exit 0 ok, 1 on a D1 or Telegram failure.

    python scripts/visits_report.py --dry-run
    python scripts/visits_report.py --ping --dry-run
"""
from __future__ import annotations

import argparse
import html
import ipaddress
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

import d1_client as d1

log = logging.getLogger("visits_report")
RETENTION_DAYS = 90
# a referrer on the visitor's own machine or LAN: they clicked through from a local copy of the site
def is_local_referrer(value: object) -> bool:
    """Whether a referrer points to a local file, loopback, or private LAN host."""
    if not isinstance(value, str) or not value:
        return False
    if value.lower().startswith("file:"):
        return True
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified)
WEEK = 7 * 86400


def _q(sql: str, params: list) -> list[dict]:
    return d1.query(sql, params)


def collect(now: int) -> dict:
    a, b = now - WEEK, now - 2 * WEEK
    w = "WHERE ts >= ? AND owner = 0"
    tot = _q(f"SELECT COUNT(*) AS views, COUNT(DISTINCT COALESCE(vid, ip)) AS visitors, COUNT(DISTINCT ip) AS ips FROM visits {w}", [a])
    prev = _q("SELECT COUNT(*) AS views, COUNT(DISTINCT COALESCE(vid, ip)) AS visitors FROM visits "
              "WHERE ts >= ? AND ts < ? AND owner = 0", [b, a])

    def top(col: str, n: int = 8, nonempty: bool = False) -> list[dict]:
        extra = f" AND {col} IS NOT NULL AND {col} != ''" if nonempty else ""
        return _q(f"SELECT {col}, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits {w}{extra} "
                  f"GROUP BY {col} ORDER BY n DESC LIMIT {n}", [a])

    returning = _q(f"SELECT COUNT(DISTINCT COALESCE(vid, ip)) AS n FROM visits {w} AND COALESCE(vid, ip) IN "
                   "(SELECT COALESCE(vid, ip) FROM visits WHERE ts < ? AND owner = 0)", [a, a])
    newest = _q(
        f"SELECT COALESCE(vid, ip) AS who, MIN(ts) AS first, COUNT(*) AS n, MAX(ip) AS ip, "
        f"MAX(city) AS city, MAX(region) AS region, MAX(country) AS country, MAX(org) AS org, "
        f"MAX(lang) AS lang, MAX(dtz) AS dtz, MAX(tz) AS tz, MAX(scr) AS scr, MAX(dev) AS dev, "
        f"MAX(model) AS model, MAX(postal) AS postal, "
        f"SUM(CASE WHEN dur IS NOT NULL THEN dur ELSE 0 END) AS visible_seconds "
        f"FROM visits {w} GROUP BY who ORDER BY first DESC LIMIT 10", [a])
    session_sql = (
        "WITH base AS (SELECT id, ts, COALESCE(vid, ip, 'unknown-' || id) AS who, dur FROM visits " + w + "), "
        "lagged AS (SELECT *, LAG(ts) OVER (PARTITION BY who ORDER BY ts, id) AS prev_ts FROM base), "
        "marked AS (SELECT *, CASE WHEN prev_ts IS NULL OR ts - prev_ts > 1800 THEN 1 ELSE 0 END AS starts FROM lagged), "
        "numbered AS (SELECT *, SUM(starts) OVER (PARTITION BY who ORDER BY ts, id "
        "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS session_no FROM marked), "
        "grouped AS (SELECT who, session_no, COUNT(*) AS pages, "
        "SUM(CASE WHEN dur IS NULL THEN 0 ELSE dur END) AS visible_seconds, COUNT(dur) AS measured_pages "
        "FROM numbered GROUP BY who, session_no) "
        "SELECT COUNT(*) AS sessions, ROUND(AVG(pages), 2) AS avg_pages, MAX(pages) AS max_pages, "
        "ROUND(AVG(CASE WHEN measured_pages > 0 THEN visible_seconds END), 0) AS avg_visible_seconds FROM grouped"
    )
    sessions = _q(session_sql, [a])
    engagement = _q(f"SELECT COUNT(dur) AS measured, ROUND(AVG(dur), 0) AS avg_seconds, "
                    f"MAX(dur) AS max_seconds FROM visits {w} AND dur IS NOT NULL", [a])
    return {
        "totals": tot[0] if tot else {}, "prev": prev[0] if prev else {},
        "returning": (returning[0] if returning else {}).get("n", 0),
        "paths": top("path"), "countries": top("country"),
        "cities": _q(f"SELECT city, country, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits {w} "
                     "GROUP BY city, country ORDER BY n DESC LIMIT 8", [a]),
        "refs": _q(f"SELECT ref, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits {w} AND ref IS NOT NULL "
                   "AND ref != '' GROUP BY ref ORDER BY n DESC LIMIT 6", [a]),
        "orgs": top("org", 6),
        "languages": top("lang", 6, True),
        "devices": top("dev", 5, True),
        "screens": top("scr", 6, True),
        "device_timezones": _q(f"SELECT dtz, tz, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u "
                               f"FROM visits {w} AND dtz IS NOT NULL AND dtz != '' "
                               "GROUP BY dtz, tz ORDER BY n DESC LIMIT 8", [a]),
        "models": top("model", 6, True),
        "postals": _q(f"SELECT postal, city, region, country, COUNT(*) AS n, "
                      f"COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits {w} "
                      "AND postal IS NOT NULL AND postal != '' "
                      "GROUP BY postal, city, region, country ORDER BY n DESC LIMIT 6", [a]),
        "engagement": engagement[0] if engagement else {},
        "sessions": sessions[0] if sessions else {},
        "newest": newest,
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

    def duration(value) -> str:
        if value is None:
            return "—"
        try:
            sec = max(0, int(round(float(value))))
        except (TypeError, ValueError):
            return "—"
        if sec < 60:
            return f"{sec}s"
        mins, rest = divmod(sec, 60)
        if mins < 60:
            return f"{mins}m {rest}s"
        hours, mins = divmod(mins, 60)
        return f"{hours}h {mins}m"

    T, P = c.get("totals") or {}, c.get("prev") or {}
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

    block("Pages", c.get("paths", []), lambda r: e(r.get("path")))
    block("Countries", c.get("countries", []), lambda r: e(r.get("country")))
    block("Cities", c.get("cities", []), lambda r: f"{e(r.get('city'))}, {e(r.get('country'))}")
    block("Referrers", c.get("refs", []), lambda r: e(r.get("ref")))
    block("Networks", c.get("orgs", []), lambda r: e(r.get("org")))
    block("Browser languages", c.get("languages", []), lambda r: e(r.get("lang")))
    block("Device types", c.get("devices", []), lambda r: e(r.get("dev")))
    block("Screen sizes (CSS px @ DPR)", c.get("screens", []), lambda r: e(r.get("scr")))
    block("Device time zones · device / IP", c.get("device_timezones", []),
          lambda r: f"{e(r.get('dtz'))}" + (f" / {e(r.get('tz'))}" if r.get("tz") else ""))
    block("Android models (when reported)", c.get("models", []), lambda r: e(r.get("model")))
    block("Approx. postal areas", c.get("postals", []),
          lambda r: f"{e(r.get('postal'))} · {e(r.get('city'))}, {e(r.get('region'))}, {e(r.get('country'))}")

    engagement = c.get("engagement") or {}
    sessions = c.get("sessions") or {}
    measured = int(engagement.get("measured") or 0)
    if measured or sessions.get("sessions"):
        lines += ["", "<b>Engagement</b>"]
        if measured:
            lines.append(f"Visible page time: avg {duration(engagement.get('avg_seconds'))}, longest "
                         f"{duration(engagement.get('max_seconds'))} · measured on {measured} views")
        if sessions.get("sessions"):
            lines.append(f"Sessions (30-min inactivity rule): {int(sessions.get('sessions') or 0):,} · "
                         f"{e(sessions.get('avg_pages'))} pages/session on average · "
                         f"{e(sessions.get('max_pages'))} max pages · avg measured session time "
                         f"{duration(sessions.get('avg_visible_seconds'))}")

    if c.get("newest"):
        lines += ["", "<b>Newest visitors</b>"]
        for r in c["newest"]:
            details = []
            for key, label in (("dev", ""), ("model", "model "), ("lang", "lang "),
                               ("dtz", "device TZ "), ("scr", "screen "), ("postal", "ZIP ")):
                if r.get(key):
                    details.append(label + str(r[key]))
            line = (f"• {time.strftime('%d.%m %H:%M', time.gmtime(int(r['first'])))} UTC · "
                    f"{e(r.get('ip'))} · {e(r.get('city'))}, {e(r.get('country'))} · "
                    f"{e(r.get('org'))} · {r['n']} views")
            if details:
                line += " · " + e(" · ".join(details), 100)
            lines.append(line)

    lines += ["", f'<a href="{site}/visits.html">Full stats</a> · owner views excluded · kept {RETENTION_DAYS} days']
    return "\n".join(lines)


PING_KEY = "visits_ping"
PING_DEFAULT = 3 * 3600      # window of the very first ping
PING_MAX = 24 * 3600         # never look back further than this, even after a long pause


def ping_since(now: int) -> int:
    rows = _q("SELECT updated_at FROM sync_state WHERE key = ?", [PING_KEY])
    last = int((rows[0] if rows else {}).get("updated_at") or 0)
    return max(last, now - PING_MAX) if last else now - PING_DEFAULT


def collect_ping(since: int, now: int) -> list[dict]:
    """One row per visitor seen in (since, now], including browser/device metadata."""
    rows = _q("SELECT COALESCE(vid, ip) AS who, MIN(ts) AS first, COUNT(*) AS n, "
              "MAX(city) AS city, MAX(country) AS country, MAX(org) AS org, MAX(ref) AS ref, "
              "GROUP_CONCAT(DISTINCT path) AS paths, MAX(lang) AS lang, MAX(dtz) AS dtz, "
              "MAX(scr) AS scr, MAX(dev) AS dev, MAX(model) AS model, MAX(postal) AS postal, "
              "MAX(dur) AS dur FROM visits WHERE ts > ? AND ts <= ? AND owner = 0 "
              "GROUP BY who ORDER BY first", [since, now])
    if rows:
        whos = [r["who"] for r in rows][:90]
        back = {r["who"] for r in _q("SELECT DISTINCT COALESCE(vid, ip) AS who FROM visits WHERE ts <= ? AND owner = 0 "
                                     f"AND COALESCE(vid, ip) IN ({','.join('?' * len(whos))})", [since, *whos])}
        for r in rows:
            r["back"] = r["who"] in back
    return rows


def render_ping(rows: list[dict], since: int, site: str = "https://4sq.pages.dev") -> str:
    def e(s, n=60):
        s = str(s if s is not None else "—")
        return html.escape(s if len(s) <= n else s[:n - 1] + "…", quote=False)

    def duration(value):
        if value is None:
            return "—"
        try:
            sec = max(0, int(round(float(value))))
        except (TypeError, ValueError):
            return "—"
        if sec < 60:
            return f"{sec}s"
        mins, rest = divmod(sec, 60)
        return f"{mins}m {rest}s" if mins < 60 else f"{mins // 60}h {mins % 60}m"

    views = sum(int(r["n"]) for r in rows)
    lines = [f"<b>👀 {len(rows)} visitor{'s' if len(rows) != 1 else ''} · {views} view{'s' if views != 1 else ''}</b> "
             f"since {time.strftime('%d.%m %H:%M', time.gmtime(since))} UTC"]
    for r in rows[:12]:
        paths = ", ".join(e(p, 40) for p in str(r.get("paths") or "").split(",")[:4])
        line = (f"• {time.strftime('%H:%M', time.gmtime(int(r['first'])))} · {e(r.get('city'))}, {e(r.get('country'))} · "
                f"{e(r.get('org'), 40)}{' · ↺ returning' if r.get('back') else ''}\n   {paths}"
                + ((" · ⌂ from a local copy (" + e(r["ref"], 40) + ")" if is_local_referrer(r["ref"]) else f" · from {e(r['ref'], 40)}") if r.get("ref") else ""))
        details = []
        if r.get("dev"):
            details.append(str(r["dev"]))
        if r.get("model"):
            details.append("model " + str(r["model"]))
        if r.get("lang"):
            details.append("lang " + str(r["lang"]))
        if r.get("dtz"):
            details.append("device TZ " + str(r["dtz"]))
        if r.get("scr"):
            details.append("screen " + str(r["scr"]))
        if r.get("postal"):
            details.append("ZIP " + str(r["postal"]))
        if r.get("dur") is not None:
            details.append("visible " + duration(r["dur"]))
        if details:
            line += "\n   " + e(" · ".join(details), 180)
        lines.append(line)
    if len(rows) > 12:
        lines.append(f"… and {len(rows) - 12} more")
    lines.append(f'<a href="{site}/visits.html">Full stats</a>')
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


def run_ddl(stmt: str) -> None:
    """One visits DDL statement; an ALTER TABLE that adds a column the table already
    has is the normal case after the first run (as in d1_client.apply_schema)."""
    is_alter = stmt.upper().lstrip().startswith("ALTER TABLE")
    try:
        d1.query(stmt, silent=is_alter)
    except Exception as err:
        text = str(err).lower()
        resp = getattr(err, "response", None)
        if resp is not None:
            try:
                text += str(resp.json()).lower()
            except Exception:
                pass
        if not (is_alter and ("duplicate column" in text or "already exists" in text)):
            raise


def _fail(msg: str) -> int:
    """Log the failure and, in GitHub Actions, raise it as an ::error annotation —
    annotations are readable through the public API without the run logs."""
    log.error("%s", msg)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::error title=Visits report::{msg.replace(chr(10), ' ')}")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dry-run", action="store_true", help="print the report, send and delete nothing")
    ap.add_argument("--ping", action="store_true", help="short note of the visitors since the previous ping")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    d1.configure(os.environ.get("CF_D1_TOKEN", ""))
    now = int(time.time())
    since = 0
    try:
        # The table is normally created by update-dashboard's --schema-only, but
        # that step only runs on a deploy, and a deploy only follows new data —
        # so the first report could meet a database without it ("no such table").
        for stmt in visits_ddl():
            run_ddl(stmt)
        if args.ping:
            since = ping_since(now)
            rows = collect_ping(since, now)
            if not rows:
                log.info("No visitors since %s UTC — nothing to send", time.strftime("%d.%m %H:%M", time.gmtime(since)))
                if not args.dry_run:
                    d1.query("INSERT OR REPLACE INTO sync_state (key, hash, updated_at) VALUES (?,?,?)", [PING_KEY, "", now])
                return 0
            text = render_ping(rows, since)
        else:
            if not args.dry_run:
                d1.query("DELETE FROM visits WHERE ts < ?", [now - RETENTION_DAYS * 86400])
            text = render(collect(now))
    except Exception as exc:
        return _fail(f"D1 failed: {exc}")
    if args.dry_run:
        print(text)
        return 0
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        missing = [k for k, v in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat)) if not v]
        return _fail(f"secret(s) not set: {', '.join(missing)} — the report goes to the PRIVATE alerts chat only")
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30, data={
        "chat_id": chat, "text": _fit(text), "parse_mode": "HTML", "disable_web_page_preview": "true"})
    if not r.ok:
        return _fail(f"Telegram sendMessage failed: {r.status_code} {r.text[:300]}")
    if args.ping:
        try:
            d1.query("INSERT OR REPLACE INTO sync_state (key, hash, updated_at) VALUES (?,?,?)", [PING_KEY, "", now])
        except Exception as exc:
            return _fail(f"ping sent, but the watermark was not saved (the next ping repeats it): {exc}")
    log.info("Visits %s sent (%d chars)", "ping" if args.ping else "report", len(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
