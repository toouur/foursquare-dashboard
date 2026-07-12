# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""metrics — aggregation and trip-detection logic.

Formerly a single 2.8k-line module; split into a package (trips / companions /
shouts / stats) for maintainability. This __init__ re-exports the public API so
existing callers keep working unchanged: ``import metrics`` /
``from metrics import process`` / ``from metrics import collect_companions`` …

Depends on: transform.py
"""
from __future__ import annotations

from .companions import collect_companions
from .shouts import (
    _build_companion_denylist,
    _detect_lang,
    _extract_emojis,
    merge_comments_into_shouts,
    shout_analysis,
    shout_records,
)
from .stats import cross_dim_analysis, process
from .trips import (
    _COUNTRY_TZ,
    _BICYCLE_PASSTHROUGH_CATS,
    _TRANSPORT_CATEGORIES,
    _is_home_transport,
    _localise,
    _parse_ts,
    _tz_at,
    detect_trips,
)

__all__ = [
    "process",
    "cross_dim_analysis",
    "detect_trips",
    "collect_companions",
    "shout_analysis",
    "shout_records",
    "merge_comments_into_shouts",
    "_build_companion_denylist",
    "_detect_lang",
    "_extract_emojis",
    "_localise",
    "_parse_ts",
    "_tz_at",
    "_is_home_transport",
    "_COUNTRY_TZ",
    "_TRANSPORT_CATEGORIES",
    "_BICYCLE_PASSTHROUGH_CATS",
]
