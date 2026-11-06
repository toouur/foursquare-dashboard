# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
sweep_overlaps.py — find friends who were at a check-in but are missing from it.

Swarm lists every check-in you share with a friend (history search by friend),
each with its `overlaps`: the friends whose own check-in sits on yours, and who
created that check-in. Our CSV only learns overlaps when a row is enriched, so a
friend who checked in later, or a whole era fetched before overlaps were read,
leaves gaps. This walks each friend's list and:

  * ADDS to config/companion_fixes.json (overlaps_name/overlaps_id) every overlap
    our row lacks that is the friend's own check-in or one another friend created
    — the same rule the 2026-11 manual sweep used;
  * REPORTS, without changing anything, overlaps on a check-in YOU created where the
    friend is no longer in your `with` list: you probably untagged them (the
    2026-09-06 Ghennadi case), so a human decides;
  * never re-adds anyone an entry already removes (remove_ids).

    python scripts/sweep_overlaps.py --csv $DATA/checkins.csv --config-dir config \\
        [--friends 1413067054,50740862] [--dry-run] [--telegram] [--probe]

Cost: one API call per 100 shared check-ins (~120 calls for the whole history).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import transform  # noqa: E402

log = logging.getLogger(__name__)

API = "https://api.foursquare.com/v2"
API_V = "20231201"
PAGE = 100
SLEEP = 0.4
ME = "self"


def _get(token: str, path: str, params: dict, getter=requests.get) -> dict:
    for attempt in range(5):
        r = getter(f"{API}/{path}", params={"oauth_token": token, "v": API_V, **params}, timeout=30)
        if r.status_code == 200:
            return r.json().get("response") or {}
        if r.status_code in (500, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        raise RuntimeError(f"{path}: HTTP {r.status_code} {r.text[:200]}")
    raise RuntimeError(f"{path}: server errors after retries")


def my_id(token: str, getter=requests.get) -> str:
    return str((_get(token, "users/self", {}, getter).get("user") or {}).get("id") or "")


def friends(token: str, getter=requests.get) -> dict[str, str]:
    """{user id: display name} of current friends (only they show in overlaps)."""
    out: dict[str, str] = {}
    offset = 0
    while True:
        resp = _get(token, "users/self/friends", {"limit": 500, "offset": offset}, getter)
        items = (resp.get("friends") or {}).get("items") or []
        for u in items:
            out[str(u["id"])] = (f"{u.get('firstName', '')} {u.get('lastName', '')}").strip()
        if len(items) < 500:
            return out
        offset += 500


def shared_checkins(token: str, friend_id: str, getter=requests.get) -> list[dict]:
    """Every check-in of mine Swarm lists with this friend, with its overlaps."""
    out: list[dict] = []
    offset = 0
    while True:
        resp = _get(token, "users/self/historysearch",
                    {"friendId": friend_id, "limit": PAGE, "offset": offset}, getter)
        items = (resp.get("checkins") or {}).get("items") or []
        out.extend(items)
        if len(items) < PAGE:
            return out
        offset += PAGE
        time.sleep(SLEEP)


def _ids(row: dict, *fields: str) -> set[str]:
    return {i.strip() for f in fields for i in (row.get(f) or "").split(",")
            if i.strip() and i.strip() != "-"}


def classify(items: list[dict], rows_by_id: dict, fixes: dict, me: str):
    """(to_add {checkin id: [user ids]}, untagged [(checkin id, user id)], missing [ids])."""
    add: dict[str, list[str]] = {}
    untagged: list[tuple[str, str]] = []
    missing: list[str] = []
    for ci in items:
        cid = str(ci.get("id") or "")
        row = rows_by_id.get(cid)
        if row is None:
            missing.append(cid)
            continue
        removed = _ids({"r": (fixes.get(cid) or {}).get(transform.COMPANION_REMOVE_FIELD, "")}, "r")
        have = _ids(row, "with_id", "created_by_id", "overlaps_id")
        creator = (row.get("created_by_id") or "").strip()
        for ov in (ci.get("overlaps") or {}).get("items") or []:
            uid = str((ov.get("user") or {}).get("id") or "")
            cb = str((ov.get("createdBy") or {}).get("id") or "")
            if not uid or uid == me or uid in have or uid in removed:
                continue
            if cb == me and creator in ("", me) and uid not in _ids(row, "with_id"):
                untagged.append((cid, uid))
                continue
            if uid not in add.setdefault(cid, []):
                add[cid].append(uid)
    return {k: v for k, v in add.items() if v}, untagged, missing


def write_fixes(path: Path, add: dict, rows_by_id: dict, names: dict) -> int:
    """Append overlaps_* entries, keeping every existing entry byte-for-byte."""
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    new_keys = []
    today = datetime.now(timezone.utc).strftime("%Y-%m")
    for cid, users in sorted(add.items(), key=lambda kv: int(rows_by_id[kv[0]].get("date") or 0)):
        if cid in data:            # existing entry: leave it to a human
            continue
        r = rows_by_id[cid]
        ids = [i for i in (r.get("overlaps_id") or "").replace(" ", "").split(",") if i and i != "-"]
        nm = [n.strip() for n in (r.get("overlaps_name") or "").split(",") if n.strip()]
        if len(nm) != len(ids):
            continue
        pairs = list(zip(nm, ids)) + [(names.get(u, u), u) for u in users if u not in ids]
        day = datetime.fromtimestamp(int(r["date"]), timezone.utc).strftime("%Y-%m-%d")
        data[cid] = {"overlaps_name": ", ".join(n for n, _ in pairs),
                     "overlaps_id": ", ".join(i for _, i in pairs),
                     "_note": f"{r.get('venue', '')}, {day}: Swarm overlaps add "
                              f"{', '.join(names.get(u, u) for u in users)} (sweep_overlaps {today})"}
        new_keys.append(cid)
    if not new_keys:
        return 0
    body = text.rstrip()
    assert body.endswith("}")
    out = body[:-1].rstrip() + ",\n" + ",\n".join(
        "  " + json.dumps(k, ensure_ascii=False) + ": " + json.dumps(data[k], ensure_ascii=False)
        for k in new_keys) + "\n}\n"
    assert json.loads(out) == data
    path.write_text(out, encoding="utf-8")
    return len(new_keys)


def report(added: dict, untagged: list, missing: list, rows_by_id: dict, names: dict) -> str:
    def line(cid: str) -> str:
        r = rows_by_id.get(cid) or {}
        d = datetime.fromtimestamp(int(r.get("date") or 0), timezone.utc).strftime("%Y-%m-%d")
        return f"{d} {r.get('venue', '?')}"
    out = [f"Overlap sweep: {sum(len(v) for v in added.values())} companion(s) added "
           f"on {len(added)} check-in(s)."]
    for cid, users in sorted(added.items(), key=lambda kv: line(kv[0]))[:40]:
        out.append(f"  + {line(cid)} — {', '.join(names.get(u, u) for u in users)}")
    if untagged:
        out.append(f"Tagged by you, no longer in `with` ({len(untagged)}) — add to "
                   "companion_fixes (overlaps) or remove_ids:")
        for cid, uid in untagged:
            out.append(f"  ? {line(cid)} — {names.get(uid, uid)} ({cid})")
    if missing:
        out.append(f"Swarm check-ins not in the CSV: {', '.join(missing[:20])}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--csv", required=True)
    ap.add_argument("--config-dir", default=str(Path(__file__).parent.parent / "config"))
    ap.add_argument("--token", default="")
    ap.add_argument("--friends", default="", help="comma-separated user ids (default: all friends)")
    ap.add_argument("--dry-run", action="store_true", help="report only, do not edit companion_fixes.json")
    ap.add_argument("--telegram", action="store_true", help="send the report to TELEGRAM_CHAT_ID")
    ap.add_argument("--probe", action="store_true", help="check the history-search endpoint and exit")
    args = ap.parse_args(argv)
    token = args.token or os.environ.get("FOURSQUARE_TOKEN", "")
    if not token:
        log.error("no token (--token or FOURSQUARE_TOKEN)")
        return 1
    me = my_id(token)
    names = friends(token)
    log.info("me=%s, %d friends", me, len(names))
    if args.probe:
        fid = (args.friends.split(",")[0].strip() if args.friends else next(iter(names), ""))
        resp = _get(token, "users/self/historysearch", {"friendId": fid, "limit": 5})
        items = (resp.get("checkins") or {}).get("items") or []
        hit = sum(1 for c in items if any(str((o.get("user") or {}).get("id")) == fid
                                         for o in (c.get("overlaps") or {}).get("items") or []))
        print(f"PROBE friend={fid} items={len(items)} with_friend_in_overlaps={hit} "
              f"count={(resp.get('checkins') or {}).get('count')}")
        return 0 if items and hit == len(items) else 2

    rows = list(csv.DictReader(open(args.csv, encoding="utf-8")))
    mappings = transform.load_mappings(args.config_dir)
    transform.apply_transforms(rows, mappings)
    rows_by_id = {r["checkin_id"]: r for r in rows if r.get("checkin_id")}
    fixes = mappings.get("companion_fixes", {})

    targets = [f.strip() for f in args.friends.split(",") if f.strip()] or list(names)
    added: dict[str, list[str]] = {}
    untagged: list = []
    missing: list = []
    for fid in targets:
        items = shared_checkins(token, fid)
        a, u, m = classify(items, rows_by_id, fixes, me)
        log.info("%s: %d shared, %d to add, %d untagged-later", names.get(fid, fid), len(items),
                 sum(len(v) for v in a.values()), len(u))
        for cid, users in a.items():
            for x in users:
                if x not in added.setdefault(cid, []):
                    added[cid].append(x)
        untagged.extend(p for p in u if p not in untagged)
        missing.extend(c for c in m if c not in missing)

    text = report(added, untagged, missing, rows_by_id, names)
    print(text)
    if not args.dry_run:
        n = write_fixes(Path(args.config_dir) / "companion_fixes.json", added, rows_by_id, names)
        print(f"WROTE={n}")
    if args.telegram and (added or untagged):
        tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        if tok and chat:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=30,
                          data={"chat_id": chat, "text": text[:3900],
                                "disable_web_page_preview": "true"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
