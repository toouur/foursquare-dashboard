# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Generate stats.html — Additional Statistics page."""
import json
import re
from pathlib import Path


def used_keys(template: str, data: dict) -> dict:
    """Only the keys the page reads (`S.key` / `S['key']`). The build data also
    carries the index map payload (unique_places, venues_heatmap: ~4.4 MB) that
    stats.html never touches; nothing in the template reads S as a whole."""
    keys = set(re.findall(r"\bS\.([A-Za-z_]\w*)", template))
    keys |= set(re.findall(r"\bS\[[\"']([A-Za-z_]\w*)", template))
    return {k: v for k, v in data.items() if k in keys}


def build_page(csv_path, config_dir, out_path, tmpl_path=None, stats_data=None, anomalies_html=""):
    TEMPLATE = Path(tmpl_path).read_text(encoding="utf-8")
    if stats_data is None:
        raise ValueError("gen_stats.py requires stats_data kwarg")
    html = TEMPLATE.replace(
        "{{STATS}}",
        json.dumps(used_keys(TEMPLATE, stats_data), ensure_ascii=False).replace("</", "<\\/"),
    ).replace("{{ANOMALIES}}", anomalies_html or "")
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"stats.html -> {out_path}  ({Path(out_path).stat().st_size // 1024}KB)")
