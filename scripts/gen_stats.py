# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Generate stats.html — Additional Statistics page."""
import json
from pathlib import Path


def build_page(csv_path, config_dir, out_path, tmpl_path=None, stats_data=None):
    TEMPLATE = Path(tmpl_path).read_text(encoding="utf-8")
    if stats_data is None:
        raise ValueError("gen_stats.py requires stats_data kwarg")
    html = TEMPLATE.replace(
        "{{STATS}}",
        json.dumps(stats_data, ensure_ascii=False).replace("</", "<\\/"),
    )
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"stats.html -> {out_path}  ({Path(out_path).stat().st_size // 1024}KB)")
