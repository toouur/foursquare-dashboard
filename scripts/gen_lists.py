# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_lists.py — Generate lists.html from lists.json."""
from pathlib import Path


def build_page(
    csv_path: str,
    config_dir: str,
    out_path: str,
    tmpl_path: str,
    lists_data_json: str,
) -> None:
    tmpl = Path(tmpl_path).read_text(encoding="utf-8")
    html = tmpl.replace("LISTS_DATA_PLACEHOLDER", lists_data_json)
    Path(out_path).write_text(html, encoding="utf-8")
    size = Path(out_path).stat().st_size // 1024
    print(f"lists.html -> {out_path}  ({size}KB)")
