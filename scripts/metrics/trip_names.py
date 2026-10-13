# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Self-healing for ``config/trip_names.json``.

A trip's name is keyed by its start timestamp (``_name_ts`` in detect_trips).
Any rule that moves the start — a new departure hub, a ride to the airport, a
platform that now counts as a station — moves the key, and the hand-written
name is silently replaced by an auto-name while the old key lingers as an
orphan nobody reads. This module decides, per orphan key:

  migrate    the trip it belonged to is still there, now starting a little
             earlier or later: the name moves to the new key;
  remove     the key is dead — its trip vanished, or the trip covering it is
             already named under its current key;
  suspicious the key looks like a shift that cannot be trusted (start moved
             by more than a day, two named trips merged into one, the old
             start is not a check-in of the trip any more). The key is kept
             untouched and reported, so a name is never thrown away or put on
             the wrong trip by a guess.

Pure function over (trip spans, names); detect_trips applies the result so the
same build renders the right names, and build.py writes the healed file.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# How far a trip start may move and still count as the same trip. Every rule
# that shifted a start so far moved it by minutes to a few hours (taxi before
# the airport, the platform before the train); a day is generous.
MAX_SHIFT_S = 24 * 3600


@dataclass
class TripSpan:
    key: int              # current _name_ts
    first: int            # ts of the first check-in
    last: int             # ts of the last check-in
    checkin_ts: frozenset[int]
    prev_last: int | None = None   # last ts of the previous trip


@dataclass
class HealResult:
    names: dict[str, str]
    migrated: list[tuple[str, str, str, int]] = field(default_factory=list)  # old, new, name, shift_s
    removed: list[tuple[str, str, str]] = field(default_factory=list)        # old, name, reason
    suspicious: list[tuple[str, str, str]] = field(default_factory=list)     # old, name, reason

    @property
    def changed(self) -> int:
        return len(self.migrated) + len(self.removed)


def _fmt_h(seconds: int) -> str:
    return f"{seconds / 3600:+.1f} h"


def heal_trip_names(spans: list[TripSpan], trip_names: dict[str, str]) -> HealResult:
    names = dict(trip_names)
    res = HealResult(names=names)
    current = {str(s.key) for s in spans}
    orphans = sorted(
        (k for k in trip_names if k.lstrip("-").isdigit() and k not in current),
        key=int,
    )
    claims: dict[int, list[str]] = {}   # span index -> orphan keys claiming it

    for k in orphans:
        ts = int(k)
        owners = [
            i for i, s in enumerate(spans)
            if s.first - MAX_SHIFT_S <= ts <= s.last
            and (s.prev_last is None or ts > s.prev_last)
        ]
        if not owners:
            res.removed.append((k, trip_names[k], "no trip at this time any more"))
            continue
        if len(owners) > 1:
            res.suspicious.append((k, trip_names[k], f"falls into {len(owners)} trips"))
            continue
        s = spans[owners[0]]
        if str(s.key) in trip_names:
            res.removed.append(
                (k, trip_names[k], f"trip is already named {trip_names[str(s.key)]!r}"))
            continue
        claims.setdefault(owners[0], []).append(k)

    for i, keys in claims.items():
        s = spans[i]

        def why_not(k: str) -> str | None:
            """None when moving key k onto this trip is a trustworthy shift."""
            ts, shift = int(k), s.key - int(k)
            if abs(shift) > MAX_SHIFT_S:
                return f"start moved by {_fmt_h(shift)}"
            # The shift must be explained: either the old start is still one of
            # the trip's check-ins (rows were added in front of it), or it sits
            # just before the new start (leading rows were dropped).
            if ts not in s.checkin_ts and not (s.first - MAX_SHIFT_S <= ts < s.first):
                return "old start is not a check-in of this trip"
            return None

        good = [k for k in keys if why_not(k) is None]
        if len(good) == 1:
            k = good[0]
            names[str(s.key)] = names.pop(k)
            res.migrated.append((k, str(s.key), names[str(s.key)], s.key - int(k)))
            for other in keys:
                if other != k:
                    res.removed.append((other, trip_names[other],
                                        f"superseded by {k} ({why_not(other)})"))
        elif len(good) > 1:
            for k in keys:
                res.suspicious.append(
                    (k, trip_names[k], f"{len(good)} named trips are now one trip (merge?)"))
        else:
            for k in keys:
                res.suspicious.append((k, trip_names[k], str(why_not(k))))

    for k, _, _ in res.removed:
        names.pop(k, None)
    return res


def describe(res: HealResult) -> list[str]:
    """Human-readable report lines (build log / CI summary)."""
    out = []
    for old, new, name, shift in res.migrated:
        out.append(f"MIGRATED   {old} -> {new} ({_fmt_h(shift)})  {name}")
    for old, name, why in res.removed:
        out.append(f"REMOVED    {old}  {name}  — {why}")
    for old, name, why in res.suspicious:
        out.append(f"SUSPICIOUS {old}  {name}  — {why}; key kept, check by hand")
    return out
