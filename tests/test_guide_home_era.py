# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_guide resolves home at the latest check-in's moment, not today's home."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import gen_guide

REPO = Path(__file__).resolve().parent.parent

SETTINGS = """\
trip_detection:
  home_city: Chișinău
  home_history:
    - city: Chișinău
      until: "2012-09-01"
    - city: Minsk
      until: "2026-07-29"
"""


def _row(ts, city, country, venue="Cafe"):
    return {"date": str(ts), "city": city, "country": country, "venue": venue,
            "venue_id": "v" * 24, "category": "Café", "lat": "53.9", "lng": "27.56",
            "checkin_id": f"c{ts}"}


def _session(tmp_path, rows):
    cfg = tmp_path / "config"
    shutil.copytree(REPO / "config", cfg)
    (cfg / "settings.yaml").write_text(SETTINGS, encoding="utf-8")
    out = tmp_path / "guide.html"
    gen_guide.build_page(csv_path=str(tmp_path / "none.csv"), config_dir=str(cfg),
                         out_path=str(out),
                         tmpl_path=str(REPO / "templates" / "guide.html.tmpl"), rows=rows)
    html = out.read_text(encoding="utf-8")
    m = re.search(r'"session":\s*(\{[^}]*\})', html)
    assert m, "guide payload has no session block"
    return json.loads(m.group(1))


def test_minsk_era_anchor_is_home_in_minsk(tmp_path):
    t = 1600000000  # 2020-09-13 — Minsk era
    rows = [_row(t - 86400 * i, "Minsk", "Belarus") for i in range(5)]
    s = _session(tmp_path, rows)
    assert s["home_city"] == "Minsk" and s["home_country"] == "Belarus"
    assert s["is_home"] is True and s["in_foreign_country"] is False


def test_current_era_anchor_uses_current_home(tmp_path):
    t = 1790000000  # 2026-09 — Chișinău era
    rows = [_row(t - 86400 * i, "Chișinău", "Moldova") for i in range(5)]
    s = _session(tmp_path, rows)
    assert s["home_city"] == "Chișinău" and s["is_home"] is True
