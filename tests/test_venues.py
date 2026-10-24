# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""metrics.venues + gen_venue_page — per-venue dossiers and the shard key both sides compute."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

import gen_venue_page
from conftest import make_row
from metrics.venues import MAX_VISITS, VISIT_FIELDS, compute_venues, venue_shard, write_venue_shards


def _ts(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def _row(ts, vid, venue="Cafe", category="Café", country="Belarus", **kw):
    kw.setdefault("source_app", "Swarm")
    kw.setdefault("checkin_id", f"c{ts}")
    return make_row(ts, city="Minsk", country=country, category=category, venue=venue,
                    venue_id=vid, lat="53.9", lng="27.56", **kw)


IDS = ["4f7ae16ae4b00fc6db0ec224", "50fbc23ae4b0afd6fe382f57", "4b0588f1f964a52076d522e3", "x", ""]


def test_shard_key_shape():
    for vid in IDS:
        k = venue_shard(vid)
        assert len(k) == 2 and int(k, 16) < 256


def _node():
    exe = shutil.which("node")
    if exe:
        return exe
    fb = Path("C:/Program Files/nodejs/node.exe")
    return str(fb) if fb.exists() else None


def test_js_shard_matches_python(tmp_path):
    node = _node()
    if not node:
        pytest.skip("node not installed — cannot run the JS side")
    src = gen_venue_page.PAGE_TEMPLATE.format(site="", ver_json='""', pix_json='""', top_json="[]")
    start = src.index("function venueShard")
    end = src.index("\n}\n", start) + 2
    harness = tmp_path / "h.js"
    harness.write_text(src[start:end] + f"\nprocess.stdout.write(JSON.stringify({json.dumps(IDS)}.map(venueShard)));\n",
                       encoding="utf-8")
    proc = subprocess.run([node, str(harness)], capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    assert json.loads(proc.stdout) == [venue_shard(v) for v in IDS]


def test_dossier():
    rows = [_row(_ts("2015-06-01T09:00:00"), "v1", venue="Old name", with_name="Vika"),
            _row(_ts("2019-06-01T17:30:00"), "v1", venue="New name", shout="again!", with_name="Vika"),
            _row(_ts("2019-07-01T17:30:00"), "v1", venue="New name", checkin_id="cX"),
            _row(_ts("2016-01-01T12:00:00"), "", venue="No id")]
    v = compute_venues(rows, {"cX": ["p1.jpg", "p2.jpg"]})
    assert list(v) == ["v1"]                      # a row without venue_id has no page
    r = v["v1"]
    assert r["name"] == "New name" and r["n"] == 3
    assert r["first"] == "2015-06-01" and r["last"] == "2019-07-01"
    assert r["years"] == {"2015": 1, "2019": 2}
    assert r["hours"][20] == 2 and r["hours"][12] == 1   # Europe/Minsk is UTC+3
    assert r["companions"] == [("Vika", 2)]
    assert r["photos"] == 2
    ix = {k: i for i, k in enumerate(VISIT_FIELDS)}
    newest = r["v"][0]
    assert newest[ix["local"]] == "2019-07-01 20:30" and newest[ix["photo"]] == "p1.jpg"
    assert r["v"][1][ix["shout"]] == "again!" and r["v"][1][ix["with"]] == ["Vika"]


def test_refurbished_flag_and_visit_cap():
    t0 = _ts("2020-01-01T10:00:00")
    rows = [_row(t0 + i * 86400, "v") for i in range(MAX_VISITS + 5)]
    rows.append(_row(t0 - 10 * 365 * 86400, "v", source_app="refurbished"))
    r = compute_venues(rows)["v"]
    assert r["n"] == MAX_VISITS + 6 and len(r["v"]) == MAX_VISITS
    assert r["first"].startswith("2010")          # the reconstructed visit still counts
    assert all(x[-1] == 0 for x in r["v"])        # …but is older than the shown window


def test_shards_written_and_stale_removed(tmp_path):
    (tmp_path / "venue_data_zz.json").write_text("{}")
    v = compute_venues([_row(_ts("2020-01-01T10:00:00"), vid) for vid in IDS[:3]])
    ver = write_venue_shards(v, str(tmp_path))
    files = sorted(p.name for p in tmp_path.glob("venue_data_*.json"))
    assert "venue_data_zz.json" not in files
    for vid in IDS[:3]:
        body = json.loads((tmp_path / f"venue_data_{venue_shard(vid)}.json").read_text(encoding="utf-8"))
        assert body["f"] == VISIT_FIELDS and vid in body["v"]
    assert ver == write_venue_shards(v, str(tmp_path))   # content-addressed version


def test_page_includes_home_and_lists_top(tmp_path):
    rows = [_row(_ts("2020-01-01T10:00:00") + i, "home1", venue="Str. Alecu Russo, 55", category="Home (private)")
            for i in range(5)]
    rows += [_row(_ts("2020-02-01T10:00:00"), "bar1", venue="Bar </script>")]
    assert gen_venue_page.build_page(out_path=str(tmp_path), rows=rows, pix_url="https://x/pix/") == 2
    page = (tmp_path / "venue.html").read_text(encoding="utf-8")
    assert page.index('"id": "home1"') < page.index('"id": "bar1"')   # most visited first and "Bar <\\/script>" in page
    assert 'const PIX = "https://x/pix";' in page
    assert "{{" not in page.replace("{{CARTO_KEY}}", "")
    shards = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("venue_data_*.json"))
    assert "Str. Alecu Russo, 55" in shards
