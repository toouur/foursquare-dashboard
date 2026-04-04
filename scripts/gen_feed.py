# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

#!/usr/bin/env python3
"""Generate feed.html shell — data is loaded at runtime via /api/feed."""
from pathlib import Path


def build_page(csv_path, config_dir, out_path, tmpl_path=None, swarm_user_id=""):
    TEMPLATE = Path(tmpl_path).read_text(encoding="utf-8")
    html = TEMPLATE.replace('{{SWARM_USER_ID}}', swarm_user_id)
    Path(out_path).write_text(html, encoding='utf-8')
    print(f"feed.html -> {out_path}  ({Path(out_path).stat().st_size//1024}KB, data served via /api/feed)")
