"""Rank moves: where each item of the index top lists stood WINDOW_DAYS ago.

The index lists (venues, cities, countries, category explorer) show a small
▲3 / ▼2 / NEW next to an item whose place changed since then. "Then" is the same
rows cut at (newest check-in − WINDOW_DAYS), so a rebuild without new check-ins
shows the same arrows.

Ranks are competition ranks (1 + how many items have a strictly larger count),
on both sides: two items tied at 152 do not "swap" places between builds.
Only the moves of items present in the shown list are emitted. A climb is
always shown; a fall only from MIN_DROP places: when one place climbs, every
place below it slips one rank without anything happening to it, and showing
those turned 444 of the 500 top venues red.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable

WINDOW_DAYS = 30
MIN_DROP = 5


def _comp_ranks(counts: dict[str, int]) -> dict[str, int]:
    vals = sorted(counts.values(), reverse=True)
    first: dict[int, int] = {}
    for i, v in enumerate(vals):
        first.setdefault(v, i + 1)
    return {k: first[v] for k, v in counts.items()}


def _moves(now: dict[str, int], before: dict[str, int], shown: Iterable[str]) -> dict[str, int | str]:
    rn, rb = _comp_ranks(now), _comp_ranks({k: v for k, v in before.items() if v > 0})
    out: dict[str, int | str] = {}
    for k in shown:
        if k not in rn:
            continue
        if k not in rb:
            out[k] = "n"
        elif rb[k] > rn[k] or rn[k] - rb[k] >= MIN_DROP:
            out[k] = rb[k] - rn[k]          # positive = climbed
    return out


def rank_moves(rows: list[dict], shown: dict[str, list[str]], explorer_groups: dict[str, list[str]],
               explorer_shown: dict[str, list[str]], group_category: Callable[[dict], str],
               window_days: int = WINDOW_DAYS) -> dict:
    """{"window": days, "venues": {vid: move}, "cities": {..}, "cities_p": {..},
    "countries": {..}, "countries_p": {..}, "explorer": {group: {vid: move}}}.
    move = +n climbed n places, -n fell, "n" = new in the list since then.
    `shown` keys: venues (venue ids), cities, countries — the items each list displays."""
    ts_all = [int(r["date"]) for r in rows if str(r.get("date", "")).strip().lstrip("-").isdigit()]
    if not ts_all:
        return {}
    cutoff = max(ts_all) - window_days * 86400

    def old(r: dict) -> bool:
        try:
            return int(r["date"]) < cutoff
        except (KeyError, TypeError, ValueError):
            return False

    cnt: dict[str, list[Counter]] = {k: [Counter(), Counter()] for k in ("venues", "cities", "countries")}
    vids: dict[str, list[defaultdict]] = {k: [defaultdict(set), defaultdict(set)] for k in ("cities", "countries")}
    raw_to_group: dict[str, list[str]] = defaultdict(list)
    for g, raws in explorer_groups.items():
        for rc in raws:
            raw_to_group[rc].append(g)
    exp: dict[str, list[Counter]] = defaultdict(lambda: [Counter(), Counter()])
    for r in rows:
        sides = (0, 1) if old(r) else (0,)
        vid = (r.get("venue_id") or "").strip()
        venue_key = vid or (r.get("venue") or "").strip()
        cy, co = (r.get("city") or "").strip(), (r.get("country") or "").strip()
        groups = raw_to_group.get(group_category(r), []) if vid and (r.get("venue") or "").strip() else []
        for s in sides:
            if vid and (r.get("venue") or "").strip():
                cnt["venues"][s][vid] += 1
            if cy:
                cnt["cities"][s][cy] += 1
                if venue_key:
                    vids["cities"][s][cy].add(venue_key)
            if co:
                cnt["countries"][s][co] += 1
                if venue_key:
                    vids["countries"][s][co].add(venue_key)
            for g in groups:
                exp[g][s][vid] += 1

    out: dict = {"window": window_days}
    for k in ("venues", "cities", "countries"):
        out[k] = _moves(cnt[k][0], cnt[k][1], shown.get(k, []))
    for k in ("cities", "countries"):
        now = {x: len(v) for x, v in vids[k][0].items()}
        before = {x: len(v) for x, v in vids[k][1].items()}
        out[k + "_p"] = _moves(now, before, shown.get(k + "_p", []))
    out["explorer"] = {g: m for g, ids in explorer_shown.items()
                       if (m := _moves(exp[g][0], exp[g][1], ids))}
    return out
