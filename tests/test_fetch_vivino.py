# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""fetch_vivino — public profile activity feed → vivino_ratings.json (no network here)."""
from __future__ import annotations

import json

import fetch_vivino as fv
from culture_sources import load_vivino_any, load_vivino_json


def _act(aid, rating, scanned="2024-05-01T18:00:00Z", rated="2024-05-08T20:00:00Z", year=2020, typ=1, country="md"):
    return {"id": aid, "verb": "reviewed", "object_type": "user-vintage", "object": {
        "id": 1000 + aid, "scanned_at": scanned, "image": {"location": "//images.vivino.com/labels/x.jpg"},
        "review": {"rating": rating, "note": "  tasty ", "created_at": rated},
        "vintage": {"id": 5000 + aid, "year": year, "statistics": {"ratings_average": 3.9},
                    "wine": {"name": "Shiraz", "type_id": typ, "winery": {"name": "Cricova"},
                             "region": {"name": "Moldova", "country": country}}}}}


def test_to_rating_reads_both_times_and_names_the_country():
    r = fv.to_rating(_act(1, 4.1), {"md": "Moldova"})
    assert r == {"id": "1001", "wine": "Shiraz", "winery": "Cricova", "vintage": "2020", "region": "Moldova",
                 "country": "Moldova", "type": "Red Wine", "rating": 4.1, "avg": 3.9, "review": "tasty",
                 "scanned_at": "2024-05-01T18:00:00Z", "rated_at": "2024-05-08T20:00:00Z",
                 "url": "https://www.vivino.com/wines/5001", "label": "https://images.vivino.com/labels/x.jpg"}
    assert fv.to_rating({"object_type": "user", "object": {}}, {}) is None
    assert fv.to_rating(_act(2, None), {}) is None
    assert fv.to_rating(_act(3, 3.0, year=0, typ=99, country="zz"), {})["vintage"] == ""


def test_walk_pages_with_start_from(monkeypatch):
    monkeypatch.setattr(fv.time, "sleep", lambda s: None)
    pages = {None: [_act(9, 4), _act(8, 3)], 8: [_act(7, 2)], 7: []}
    seen = []

    def get(url):
        start = int(url.split("start_from=")[1]) if "start_from=" in url else None
        seen.append(start)
        assert "limit=50" in url
        return pages[start]

    assert [a["id"] for a in fv.walk("1", get=get)] == [9, 8, 7]
    assert seen == [None, 8, 7]


def _run(monkeypatch, tmp_path, acts, old=None):
    monkeypatch.setattr(fv, "walk", lambda user: acts)
    out = tmp_path / "v.json"
    if old is not None:
        out.write_text(json.dumps({"ratings": old}), encoding="utf-8")
    return fv.main(["--out", str(out)]), out


def test_main_writes_one_rating_per_line_and_reports_change(monkeypatch, tmp_path, capsys):
    rc, out = _run(monkeypatch, tmp_path, [_act(2, 4.0), _act(1, 3.5, rated="2024-01-01T00:00:00Z")])
    assert rc == 0 and "CHANGED=true" in capsys.readouterr().out
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4 and lines[1].startswith('{"avg"')          # header, 2 ratings, footer
    assert [r["id"] for r in json.loads(out.read_text(encoding="utf-8"))["ratings"]] == ["1001", "1002"]
    rc, _ = fv.main(["--out", str(out)]), None
    assert "CHANGED=false" in capsys.readouterr().out


def test_a_shrunken_walk_writes_nothing(monkeypatch, tmp_path, capsys):
    old = [{"id": str(i)} for i in range(20)]
    rc, out = _run(monkeypatch, tmp_path, [_act(1, 4.0)], old=old)
    assert rc == 1 and "CHANGED=false" in capsys.readouterr().out
    assert len(json.loads(out.read_text(encoding="utf-8"))["ratings"]) == 20


def test_build_prefers_the_api_mirror(tmp_path, monkeypatch):
    monkeypatch.setattr(fv, "walk", lambda user: [_act(1, 4.0, scanned=None)])
    fv.main(["--out", str(tmp_path / "vivino_ratings.json")])
    (tmp_path / "full_wine_list.csv").write_text("Winery,Wine name,Your rating\nX,From CSV,3\n", encoding="utf-8")
    w = load_vivino_any(tmp_path)
    assert [x["wine"] for x in w] == ["Shiraz"]
    assert w[0]["ts"] == w[0]["rated_ts"]              # no scan time → the rating time places it
    assert load_vivino_json(tmp_path / "missing.json") == []
