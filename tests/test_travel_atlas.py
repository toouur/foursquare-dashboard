# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_travel_atlas: the standalone travel-journal page.

The journal must show the dashboard's numbers, so these tests feed it the same
rows through the real metrics.process() and compare against its stats.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from conftest import make_row
import gen_travel_atlas as ga
from metrics import process

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "travel_atlas.json").read_text(encoding="utf-8"))
FLAGS = json.loads((ROOT / "config" / "country_flags.json").read_text(encoding="utf-8"))


def _ts(y, m, d, h=12):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp())


def _rows():
    return [
        make_row(_ts(2019, 6, 1), "Minsk", "Belarus", "Plaza", "Oktyabrskaya Square", "a" * 24,
                 "53.90", "27.56", "c1"),
        make_row(_ts(2019, 6, 2), "Minsk", "Belarus", "Home (private)", "My flat", "b" * 24,
                 "53.91", "27.50", "c2"),
        make_row(_ts(2019, 6, 3), "Vilnius", "Lithuania", "Church", "St. Anne's", "d" * 24,
                 "54.68", "25.29", "c3"),
        make_row(_ts(2019, 6, 4), "Vilnius", "Lithuania", "Church", "St. Anne's", "d" * 24,
                 "54.68", "25.29", "c4"),
        make_row(_ts(2019, 6, 5), "Vilnius", "Lithuania", "Road", "A road", "",
                 "54.70", "25.30", "c6"),
        make_row(_ts(2019, 6, 6), "Minsk", "Belarus", "Plaza", "Oktyabrskaya Square", "a" * 24,
                 "53.90", "27.56", "c5"),
    ]


PHOTOS = {"c1": ["29447180_aaa.jpg"], "c2": ["29447180_home.jpg"], "c3": ["backfill_x.jpg"]}
TIPS = [{"id": "t1", "ts": _ts(2019, 6, 4), "venue": "Gate of Dawn", "venue_id": "e" * 24,
         "city": "Vilnius", "country": "Lietuva", "category": "Gate", "photo": "29447180_tip.jpg"},
        {"id": "t2", "ts": _ts(2019, 6, 4), "venue": "No photo", "venue_id": "f" * 24}]


def _payload():
    rows = _rows()
    stats, trips = process(rows, {}, home_city="Minsk", min_trip_checkins=2)
    return ga.build_payload(rows, stats, trips, PHOTOS, TIPS, CFG, FLAGS,
                            ctry_norm={"Lietuva": "Lithuania"}), stats, trips


def test_headline_numbers_are_the_dashboards():
    p, stats, trips = _payload()
    m = p["meta"]
    assert m["checkins"] == stats["total"] == 6
    assert m["venues"] == stats["unique_places_count"] == len(p["places"])
    assert m["countries"] == len(stats["countries"]) and m["cities"] == len(stats["cities"])
    assert m["trips"] == len(trips)
    assert p["C"] == [c for c, _ in stats["countries"]]
    assert [(y["y"], y["n"]) for y in p["years"]] == [tuple(x) for x in stats["by_year"]]


def test_home_checkins_and_photos_are_included():
    p, _, _ = _payload()
    assert any(pl[0] == "My flat" for pl in p["places"])
    assert "home.jpg" in [ph[0] for ph in p["photos"]]
    assert p["countries"][0]["en"] == "Belarus" and p["countries"][0]["n"] == 3


def test_photo_total_matches_build_formula():
    """build.py: total_photos = all photos.json files + tip photos."""
    p, _, _ = _payload()
    assert p["meta"]["photos"] == sum(map(len, PHOTOS.values())) + 1
    assert p["meta"]["tipPhotos"] == 1
    tip = next(ph for ph in p["photos"] if ph[-1] == 1)
    assert tip[0] == "tip.jpg" and tip[1] == -1 and tip[3][0] == "Gate of Dawn"


def test_photo_keys_compact_and_reversible():
    files = [ph[0] for ph in _payload()[0]["photos"]]
    assert "aaa.jpg" in files                 # common prefix stripped
    assert "!backfill_x.jpg" in files         # other keys kept verbatim, flagged


def test_country_spread_uses_country_page_formulas():
    p, _, _ = _payload()
    lt = next(c for c in p["countries"] if c["en"] == "Lithuania")
    # venues keyed by venue_id or name; cities/days by UTC date — gen_country_pages
    assert (lt["n"], lt["v"], lt["c"], lt["d"]) == (3, 2, 1, 3)
    assert lt["venues"][0] == ["St. Anne's", 2]
    assert lt["iso2"] == "lt" and lt["num"] == "440"


def test_render_inlines_json_safely():
    p, _, _ = _payload()
    p["places"][0][0] = "evil </script><script>alert(1)</script>"
    html = ga.render(p, ga.TEMPLATE.read_text(encoding="utf-8"))
    assert "{{ATLAS_DATA}}" not in html
    blob = re.search(r'id="journalData" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert json.loads(blob)["places"][0][0].startswith("evil </script>")


def test_template_has_single_placeholder():
    assert ga.TEMPLATE.read_text(encoding="utf-8").count("{{ATLAS_DATA}}") == 1


def test_config_is_well_formed():
    assert all(re.fullmatch(r"\d{3}", v) for v in CFG["iso_numeric"].values())
    assert all(k in FLAGS for k in CFG["iso_numeric"])
    assert all(k in FLAGS for k in CFG["landmark_audit"])
    assert all(v["status"] in ("visited", "unknown") and v["landmark"] for v in CFG["landmark_audit"].values())
    assert all(re.fullmatch(r"[0-9a-f]{24}", c) for c in CFG["curated_checkins"])
