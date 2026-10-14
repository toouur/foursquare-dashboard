# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_travel_atlas: the standalone travel-journal page (payload + render)."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from conftest import make_row
import gen_travel_atlas as ga

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "travel_atlas.json").read_text(encoding="utf-8"))
FLAGS = json.loads((ROOT / "config" / "country_flags.json").read_text(encoding="utf-8"))


def _ts(y, m, d, h=12):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp())


def _rows():
    rows = [
        make_row(_ts(2019, 6, 1), "Minsk", "Belarus", "Plaza", "Oktyabrskaya Square", "a" * 24,
                 "53.90", "27.56", "c1"),
        make_row(_ts(2019, 6, 2), "Minsk", "Belarus", "Home (private)", "My flat", "b" * 24,
                 "53.91", "27.50", "c2"),
        make_row(_ts(2019, 6, 3), "Vilnius", "Lithuania", "Church", "St. Anne's", "d" * 24,
                 "54.68", "25.29", "c3"),
        make_row(_ts(2019, 6, 4), "Vilnius", "Lithuania", "Church", "St. Anne's", "d" * 24,
                 "54.68", "25.29", "c4"),
        make_row(_ts(2019, 6, 5), "Minsk", "Belarus", "Plaza", "Oktyabrskaya Square", "a" * 24,
                 "53.90", "27.56", "c5"),
    ]
    return ga._prepare(rows, {})


PHOTOS = {"c1": ["29447180_aaa.jpg"], "c2": ["29447180_home.jpg"], "c3": ["backfill_x.jpg"]}
TRIPS = [{"id": 1, "name": "Vilnius", "start_date": "2019-06-03", "end_date": "2019-06-04",
          "start_ts": _ts(2019, 6, 3, 0), "duration": 2, "checkin_count": 2, "unique_places": 1,
          "countries": ["Lithuania"], "cities": ["Vilnius"]}]


def _payload():
    return ga.build_payload(_rows(), PHOTOS, TRIPS, CFG, FLAGS, n_backfill=0)


def test_counts_and_countries():
    p = _payload()
    assert p["meta"]["checkins"] == 5
    assert p["meta"]["countries"] == 2
    assert p["C"] == ["Belarus", "Lithuania"]          # ordered by check-in count
    by = p["countries"][0]
    assert by["iso2"] == "by" and by["num"] == "112"
    assert by["n"] == 3 and by["audit"]["landmark"]


def test_private_home_never_surfaces():
    p = _payload()
    assert all(pl[0] != "My flat" for pl in p["places"])
    assert all("home" not in ph[0] for ph in p["photos"])
    assert p["meta"]["photos"] == 2


def test_photo_keys_compact_and_reversible():
    files = [ph[0] for ph in _payload()["photos"]]
    assert "aaa.jpg" in files                 # common prefix stripped
    assert "!backfill_x.jpg" in files         # other keys kept verbatim, flagged


def test_trip_gets_its_own_photos_and_route_is_recorded():
    p = _payload()
    t = p["trips"][0]
    assert t["phn"] == 1 and len(t["ph"]) == 1
    assert p["places"][p["photos"][t["ph"][0]][1]][0] == "St. Anne's"
    assert {(r[7], r[8]) for r in p["routes"]} == {("Minsk", "Vilnius"), ("Vilnius", "Minsk")}


def test_years_track_first_visits():
    (y,) = _payload()["years"]
    assert y["y"] == "2019" and y["new"] == ["Belarus", "Lithuania"] and y["tr"] == 1


def test_render_inlines_json_safely():
    p = _payload()
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
