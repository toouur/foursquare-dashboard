# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_ratings.py — Generate ratings.html from venueRatings.json."""
import json
from pathlib import Path


def build_page(
    csv_path: str,
    config_dir: str,
    out_path: str,
    tmpl_path: str,
    likes: list,
    neutral: list,
    dislikes: list,
) -> None:
    tmpl = Path(tmpl_path).read_text(encoding="utf-8")

    if not likes and not neutral and not dislikes:
        html = tmpl.replace("RATINGS_DATA_PLACEHOLDER", "[]").replace("RATINGS_COUNTS_PLACEHOLDER", "{}")
        Path(out_path).write_text(html, encoding="utf-8")
        print(f"ratings.html -> {out_path}  (no ratings data)")
        return

    all_ratings = likes + neutral + dislikes
    all_json   = json.dumps(all_ratings, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    counts = {"likes": len(likes), "neutral": len(neutral), "dislikes": len(dislikes)}
    counts_json = json.dumps(counts)

    html = tmpl.replace("RATINGS_DATA_PLACEHOLDER", all_json).replace("RATINGS_COUNTS_PLACEHOLDER", counts_json)
    Path(out_path).write_text(html, encoding="utf-8")
    total = len(all_ratings)
    size  = Path(out_path).stat().st_size // 1024
    print(f"ratings.html -> {out_path}  ({size}KB, {total:,} rated venues)")
