# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Companion-name extraction from the three Foursquare signals (metrics package)."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

def collect_companions(row: dict) -> list[str]:
    """De-duplicated list of companion names from all three Foursquare signals.

    - `with_name`: explicitly tagged ("with Joanna")
    - `created_by_name`: someone else checked you in
    - `overlaps_name`: a friend independently checked in to the same venue
      around the same time

    Names are compared case-insensitively to dedupe; the first-seen original
    casing wins.  The Foursquare overlaps column uses "-" as a sentinel for
    "none" — those are excluded.
    """
    seen: set[str] = set()
    out: list[str] = []

    def _push(raw: str, allow_dash_sentinel: bool = False) -> None:
        for part in (raw or "").replace(" ,", ",").split(","):
            n = part.strip()
            if not n:
                continue
            if not allow_dash_sentinel and n == "-":
                continue
            key = n.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(n)

    _push(row.get("with_name") or "")
    cb = (row.get("created_by_name") or "").strip()
    if cb and cb.lower() not in seen:
        seen.add(cb.lower())
        out.append(cb)
    _push(row.get("overlaps_name") or "")
    return out

