# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_shouts.py — Generate shouts.html (all shout-bearing check-ins)."""
import json
from datetime import datetime, timezone
from pathlib import Path


def build_page(
    csv_path: str,
    config_dir: str,
    out_path: str,
    tmpl_path: str,
    shouts: list | None = None,
    country_count: int = 0,
) -> None:
    tmpl = Path(tmpl_path).read_text(encoding="utf-8")
    shouts = shouts or []

    # Format display date once at build time so the client doesn't have to
    # construct a Date() per record.
    out: list[dict] = []
    for s in shouts:
        ts = s["ts"]
        try:
            d = datetime.fromtimestamp(ts, tz=timezone.utc)
            date_str = d.strftime("%d %b %Y")
        except (ValueError, OSError):
            date_str = ""
        out.append({
            "ts":        ts,
            "date":      date_str,
            "text":      s["text"],
            "venue":     s.get("venue", ""),
            "venue_id":  s.get("venue_id", ""),
            "city":      s.get("city", ""),
            "country":   s.get("country", ""),
            "category":  s.get("category", ""),
            "with_name": s.get("with_name", ""),
        })

    data_json = json.dumps(out, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = (
        tmpl
        .replace("SHOUTS_DATA_PLACEHOLDER", data_json)
        .replace("{{COUNTRIES}}", str(country_count))
        .replace("{{SHOUTS_TOTAL}}", f"{len(out):,}")
    )
    Path(out_path).write_text(html, encoding="utf-8")
    size = Path(out_path).stat().st_size // 1024
    print(f"shouts.html -> {out_path}  ({size}KB, {len(out):,} shouts)")
