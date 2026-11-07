"""gen_venues must count build.py's merged rows (incl. reconstructed rf* backfill)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import gen_venues  # noqa: E402

ROOT = Path(__file__).parent.parent


def test_venues_page_counts_given_rows(tmp_path):
    rows = [{"venue_id": "v1", "venue": "Суперпрод", "city": "Minsk", "country": "Belarus",
             "category": "Department Store", "date": str(1307437200 + i * 86400),
             "checkin_id": ("rf%02d" % i) if i < 2 else "c%02d" % i} for i in range(5)]
    out = tmp_path / "venues.html"
    gen_venues.build_page(csv_path=str(tmp_path / "missing.csv"), config_dir=str(ROOT / "config"),
                          out_path=str(out), tmpl_path=str(ROOT / "templates" / "venues.html.tmpl"),
                          rows=rows)
    top = json.loads(re.search(r"const TOP=(\[.*?\]);\n", out.read_text(encoding="utf-8")).group(1))
    assert top[0][0] == "Суперпрод" and top[0][4] == 5
