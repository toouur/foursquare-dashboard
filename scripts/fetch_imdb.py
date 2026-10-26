# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
fetch_imdb.py — new IMDb ratings from the logged-in ratings page → imdb_ratings.json.

IMDb has no public API and its pages sit behind AWS WAF, so this reads the user's
own ratings list with the browser session cookie (secret IMDB_COOKIE, the whole
`Cookie:` header of a logged-in imdb.com request). The list is sorted newest
rating first, so the first page is enough to catch what is new since the last
run; the history before that comes from the one-off `imdb_export.csv`, and
`culture_sources.load_imdb_any` merges both (the page wins for a title in both,
so a changed rating is picked up too).

The page is a Next.js app: the data is the JSON in <script id="__NEXT_DATA__">.
Its shape is not documented, so items are found by what they contain — a `tt…`
title id plus a 1..10 user rating — wherever they sit. `--probe` prints only the
SHAPE of what it found (key paths and counts, no titles or ratings) as GitHub
annotations, which can be read without access to the run logs.

Exit 0 ok · 2 cookie rejected / WAF challenge (needs a fresh cookie) · 1 other.
Prints CHANGED=true|false.

    IMDB_COOKIE='...' python scripts/fetch_imdb.py --out $DATA/imdb_ratings.json [--probe]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

log = logging.getLogger("fetch_imdb")

USER = "p.yx6kaorazm2bqhp4nwbhqrqsni"
URL = "https://www.imdb.com/user/{user}/ratings/?sort=date_added%2Cdesc"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
TT = re.compile(r"^tt\d{6,10}$")
GQL = "https://api.graphql.imdb.com/"
GQL_QUERY = "query($ids: [ID!]!) { titles(ids: $ids) { id userRating { value date } } }"
NEXT = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def _note(level: str, msg: str) -> None:
    (log.error if level == "error" else log.info)("%s", msg)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level} title=IMDb::{msg.replace(chr(10), ' ')}")


def _first(d: dict, *names: str):
    for n in names:
        v = d.get(n)
        if v not in (None, "", [], {}):
            return v
    return None


def _text(v) -> str:
    if isinstance(v, dict):
        return str(_first(v, "text", "plainText", "displayableProperty", "value") or "")
    return "" if v is None else str(v)


def _rating(d: dict):
    """A 1..10 user rating, wherever IMDb put it on this node."""
    for k in ("userRating", "otherUserRating", "authorRating", "rating", "yourRating", "value"):
        v = d.get(k)
        if isinstance(v, dict):
            v = _first(v, "value", "rating")
        if isinstance(v, (int, float)) and 1 <= v <= 10 and k != "value":
            return int(v)
    return None


def find_items(node, path: str = "$", out: list | None = None) -> list[tuple[str, dict]]:
    """Every dict that holds a tt-id and a user rating (directly or one level down)."""
    out = [] if out is None else out
    if isinstance(node, dict):
        tid = node.get("id") or node.get("const") or node.get("titleId")
        rating = _rating(node)
        cand = node.get("title") or node.get("node")
        inner = cand if isinstance(cand, dict) else None
        if inner and not (isinstance(tid, str) and TT.match(tid)):
            tid = inner.get("id")
        if isinstance(tid, str) and TT.match(tid) and rating is not None:
            out.append((path, node))
            return out
        for k, v in node.items():
            find_items(v, f"{path}.{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node[:2000]):
            find_items(v, f"{path}[{i}]" if i < 1 else f"{path}[*]", out)
    return out


def to_row(node: dict) -> dict:
    cand = node.get("title") if isinstance(node.get("title"), dict) else node.get("node")
    t: dict = cand if isinstance(cand, dict) else node
    tid = node.get("id") if isinstance(node.get("id"), str) and TT.match(node["id"]) else t.get("id")
    date = _first(node, "dateRated", "date", "ratedAt", "createdDate") or _first(node.get("userRating") or {}, "date", "dateRated") or ""
    year = _first(t, "releaseYear", "year")
    if isinstance(year, dict):
        year = year.get("year")
    genres = _first(t, "titleGenres", "genres") or []
    if isinstance(genres, dict):
        genres = genres.get("genres") or []
    return {
        "id": tid,
        "title": _text(_first(t, "titleText", "title", "originalTitleText")),
        "original": _text(_first(t, "originalTitleText")),
        "rating": _rating(node) or _rating(t),
        "date": str(date)[:10],
        "type": _text(_first(t, "titleType")),
        "year": year if isinstance(year, int) else None,
        "genres": [_text(g.get("genre") if isinstance(g, dict) and "genre" in g else g) for g in genres][:6],
        "imdb_rating": _first(_first(t, "ratingsSummary") or {}, "aggregateRating"),
        "url": f"https://www.imdb.com/title/{tid}/",
    }


def shape(node: dict) -> str:
    """Key names two levels deep — the structure, never the values."""
    keys = []
    for k, v in node.items():
        keys.append(k + ("{" + ",".join(list(v)[:8]) + "}" if isinstance(v, dict) else ""))
    return ", ".join(keys[:25])


def deep_shape(node, depth: int = 6) -> str:
    """Nested key names (a list shows its length and first item) — never the values."""
    if depth <= 0:
        return "…"
    if isinstance(node, dict):
        return "{" + ",".join(k + (":" + deep_shape(v, depth - 1) if isinstance(v, (dict, list)) and v else "")
                              for k, v in list(node.items())[:15]) + "}"
    if isinstance(node, list):
        return f"[{len(node)}]" + (deep_shape(node[0], depth - 1) if node else "")
    return ""


def add_user_ratings(requests, titles: list[dict], headers: dict) -> str:
    """The ratings page server-renders the titles but not the viewer's rating: the browser
    asks the GraphQL API for it afterwards. Do the same and attach `userRating` to each
    title dict in place. Returns '' or a short error (schema messages only, no values)."""
    by_id = {t["id"]: t for t in titles if isinstance(t.get("id"), str) and TT.match(t["id"])}
    ids = list(by_id)
    for i in range(0, len(ids), 100):
        try:
            r = requests.post(GQL, timeout=30, headers={**headers, "Content-Type": "application/json",
                              "Origin": "https://www.imdb.com", "Referer": "https://www.imdb.com/",
                              "x-imdb-client-name": "imdb-web-next"},
                              json={"query": GQL_QUERY, "variables": {"ids": ids[i:i + 100]}})
        except Exception as e:
            return f"graphql request failed: {e}"
        if r.status_code != 200:
            return f"graphql HTTP {r.status_code}"
        body = r.json()
        if body.get("errors"):
            return "graphql errors: " + "; ".join(str(e.get("message", ""))[:200] for e in body["errors"][:3])
        for t in (body.get("data") or {}).get("titles") or []:
            if t and t.get("id") in by_id and t.get("userRating"):
                by_id[t["id"]]["userRating"] = t["userRating"]
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="New IMDb ratings via the logged-in ratings page.")
    ap.add_argument("--user", default=USER)
    ap.add_argument("--out", required=True, help="imdb_ratings.json")
    ap.add_argument("--probe", action="store_true", help="report the page shape only; write nothing")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    cookie = os.environ.get("IMDB_COOKIE", "").strip()
    if not cookie:
        _note("error", "IMDB_COOKIE secret is not set")
        print("CHANGED=false")
        return 2
    import requests  # noqa: PLC0415
    try:
        r = requests.get(URL.format(user=args.user), timeout=30, headers={
            "User-Agent": UA, "Cookie": cookie, "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9"})
    except Exception as e:
        _note("error", f"request failed: {e}")
        print("CHANGED=false")
        return 1
    waf = r.headers.get("x-amzn-waf-action") or ("challenge" if r.status_code == 202 and not r.text.strip() else "")
    if waf or r.status_code in (401, 403):
        _note("error", f"blocked: HTTP {r.status_code}, WAF action '{waf or '-'}' — the cookie was refused or "
                       "the request was challenged; copy a fresh Cookie header into IMDB_COOKIE")
        print("CHANGED=false")
        return 2
    m = NEXT.search(r.text)
    if r.status_code != 200 or not m:
        signed_in = "Sign In" not in r.text[:200000] or "nav__userMenu" in r.text
        _note("error", f"no page data: HTTP {r.status_code}, {len(r.text)} bytes, __NEXT_DATA__ missing, "
                       f"looks signed {'in' if signed_in else 'out'}")
        print("CHANGED=false")
        return 1
    data = json.loads(m.group(1))
    items = find_items(data)
    gql_err = ""
    if not items:
        pp0 = data.get("props", {}).get("pageProps", {}) or {}
        edges0 = ((pp0.get("mainColumnData") or {}).get("advancedTitleSearch") or {}).get("edges") or []
        titles = [(e.get("node") or {}).get("title") for e in edges0]
        titles = [t for t in titles if isinstance(t, dict)]
        if titles:
            gql_err = add_user_ratings(requests, titles, {"User-Agent": UA, "Cookie": cookie,
                                                          "Accept-Language": "en-US,en;q=0.9"})
            items = find_items(data)
            _note("error" if gql_err else "notice",
                  f"graphql: {len(items)} of {len(titles)} page titles got a user rating" + (f" — {gql_err}" if gql_err else ""))
    if args.probe:
        paths = sorted({p for p, _ in items})
        _note("notice", f"probe: HTTP 200, page data {len(m.group(1)) // 1024} KB, {len(items)} rated items "
                        f"at {paths[:3]}")
        if items:
            _note("notice", f"probe: item keys = {shape(items[0][1])}")
            row = to_row(items[0][1])
            _note("notice", "probe: parsed fields present = " + ", ".join(k for k, v in row.items() if v not in (None, "", [])))
        else:
            pp = data.get("props", {}).get("pageProps", {}) or data
            _note("notice", f"probe: top-level keys = {shape(pp)}")
            _note("notice", f"probe: {len(set(re.findall(r'tt[0-9]{6,10}', m.group(1))))} distinct tt-ids in the page data")
            for k in ("mainColumnData", "urqlState", "fetchState"):
                if pp.get(k):
                    _note("notice", f"probe: {k} = {deep_shape(pp[k])[:3000]}")
            edges = ((pp.get("mainColumnData") or {}).get("advancedTitleSearch") or {}).get("edges") or []
            if edges:
                node = edges[0].get("node") or {}
                t = node.get("title") or {}
                _note("notice", f"probe: edge keys = {list(edges[0])}, node keys = {list(node)}, title keys = {list(t)}")
                _note("notice", "probe: rating-like title fields = " + ", ".join(
                    f"{k}:{deep_shape(v, 2) if isinstance(v, (dict, list)) else type(v).__name__}"
                    for k, v in t.items() if "rat" in k.lower()))
        print("CHANGED=false")
        return 0
    rows = [to_row(n) for _, n in items]
    rows = [x for x in rows if x["id"] and x["rating"]]
    if not rows:
        _note("error", "the page parsed but no rated titles were found — run with --probe")
        print("CHANGED=false")
        return 1
    out = Path(args.out)
    old = {}
    if out.exists():
        try:
            previous = json.loads(out.read_text(encoding="utf-8"))
            previous_rows = previous.get("ratings")
            if not isinstance(previous_rows, list):
                raise ValueError("ratings is not a list")
            old = {x["id"]: x for x in previous_rows if isinstance(x, dict) and isinstance(x.get("id"), str)}
            if previous_rows and not old:
                raise ValueError("existing ratings file has no valid title IDs")
        except Exception as e:
            _note("error", f"existing ratings file is invalid; refusing to replace it ({type(e).__name__})")
            print("CHANGED=false")
            return 1
    # Guard against a changed IMDb page/schema producing a tiny, plausible-looking
    # but incomplete scrape. Never replace or commit data from a suspicious sample.
    # On an established dataset require a meaningful sample; on first bootstrap,
    # accept any non-empty parse and rely on the existing export as the historical base.
    minimum = min(5, len(old)) if old else 1
    if len(rows) < minimum:
        _note("error", f"only {len(rows)} rated titles parsed; expected at least {minimum}. "
                       "Possible IMDb page/schema change — keeping previous file unchanged")
        print("CHANGED=false")
        return 1
    merged = dict(old)
    for x in rows:
        if not x["date"] and x["id"] in old:
            x["date"] = old[x["id"]].get("date", "")
        merged[x["id"]] = {**old.get(x["id"], {}), **{k: v for k, v in x.items() if v not in (None, "", [])}}
    changed = merged != old
    if changed:
        body = ",\n".join(json.dumps(v, ensure_ascii=False, sort_keys=True)
                          for v in sorted(merged.values(), key=lambda v: (v.get("date", ""), v["id"])))
        out.write_text('{"ratings":[\n' + body + "\n]}\n", encoding="utf-8", newline="\n")
    _note("notice", f"{len(rows)} ratings on the first page, {len(merged) - len(old)} new, file now {len(merged)}")
    print(f"CHANGED={'true' if changed else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
