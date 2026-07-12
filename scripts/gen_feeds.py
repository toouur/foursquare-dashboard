# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Static syndication feeds (audit #10).

Emits two files into the site root from the metrics `recent` list (the 30 newest
check-ins, same payload the index recent-strip renders):

  * feed.xml  — RSS 2.0
  * feed.json — JSON Feed 1.1 (https://jsonfeed.org/version/1.1)

Both are built once at deploy time — no runtime query. Each entry links to the
Swarm check-in permalink (falling back to the Foursquare venue page), which keeps
the feed useful in a reader without exposing anything the public site doesn't.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

SITE_URL = "https://4sq.pages.dev"
FEED_TITLE = "Check-in Journal"
FEED_DESC = "Latest Foursquare/Swarm check-ins."
MAX_ITEMS = 30


def _item_url(it: dict[str, Any], swarm_user_id: str) -> str:
    cid = (it.get("checkin_id") or "").strip()
    if cid and swarm_user_id:
        return f"https://www.swarmapp.com/user/{swarm_user_id}/checkin/{cid}"
    vid = (it.get("venue_id") or "").strip()
    if vid:
        return f"https://foursquare.com/v/{vid}"
    return SITE_URL + "/"


def _title(it: dict[str, Any]) -> str:
    venue = (it.get("venue") or "").strip() or "Check-in"
    place = ", ".join(p for p in ((it.get("city") or "").strip(),
                                  (it.get("country") or "").strip()) if p)
    return f"{venue} — {place}" if place else venue


def _summary(it: dict[str, Any]) -> str:
    bits = []
    cat = (it.get("category") or "").strip()
    if cat:
        bits.append(cat)
    comps = it.get("companions") or []
    if comps:
        bits.append("with " + ", ".join(comps))
    when = (it.get("datetime") or "").strip()
    if when:
        bits.append(when)
    return " · ".join(bits)


def build_feeds(recent: list[dict[str, Any]], out_dir: str,
                swarm_user_id: str = "") -> None:
    """Write feed.xml + feed.json for the newest check-ins into out_dir."""
    items = [it for it in (recent or []) if it.get("ts")][:MAX_ITEMS]
    out = Path(out_dir)
    now = datetime.now(timezone.utc)

    # ── RSS 2.0 ───────────────────────────────────────────────────────────────
    rss_items = []
    for it in items:
        pub = format_datetime(datetime.fromtimestamp(int(it["ts"]), tz=timezone.utc))
        link = _item_url(it, swarm_user_id)
        desc = _summary(it)
        rss_items.append(
            "<item>"
            f"<title>{_xml_escape(_title(it))}</title>"
            f"<link>{_xml_escape(link)}</link>"
            f"<guid isPermaLink=\"false\">{_xml_escape((it.get('checkin_id') or link))}</guid>"
            f"<pubDate>{pub}</pubDate>"
            + (f"<description>{_xml_escape(desc)}</description>" if desc else "")
            + "</item>"
        )
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
        f"<title>{_xml_escape(FEED_TITLE)}</title>"
        f"<link>{SITE_URL}/</link>"
        f"<description>{_xml_escape(FEED_DESC)}</description>"
        "<language>en</language>"
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>"
        f'<atom:link href="{SITE_URL}/feed.xml" rel="self" type="application/rss+xml"/>'
        + "".join(rss_items)
        + "</channel></rss>"
    )
    (out / "feed.xml").write_text(rss, encoding="utf-8")

    # ── JSON Feed 1.1 ─────────────────────────────────────────────────────────
    jf_items = []
    for it in items:
        dt = datetime.fromtimestamp(int(it["ts"]), tz=timezone.utc)
        link = _item_url(it, swarm_user_id)
        jf_items.append({
            "id": (it.get("checkin_id") or link),
            "url": link,
            "title": _title(it),
            "summary": _summary(it),
            "date_published": dt.isoformat(),
        })
    jf = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": FEED_TITLE,
        "home_page_url": SITE_URL + "/",
        "feed_url": SITE_URL + "/feed.json",
        "description": FEED_DESC,
        "language": "en",
        "items": jf_items,
    }
    (out / "feed.json").write_text(
        json.dumps(jf, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
