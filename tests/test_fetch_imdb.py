# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""fetch_imdb — new ratings from the logged-in ratings page (no network here)."""
from __future__ import annotations

import json

import fetch_imdb as fi
from culture_sources import load_imdb_any


def _page(items) -> str:
    data = {"props": {"pageProps": {"mainColumnData": {"advancedTitleSearch": {"edges": [
        {"node": {"title": it, "userRating": {"value": it.pop("_r"), "date": it.pop("_d", "")}}} for it in items]}}}}}
    return '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script></html>"


def _title(tt, name, r, d="2026-10-20T10:00:00Z", year=2024):
    return {"id": tt, "titleText": {"text": name}, "releaseYear": {"year": year}, "titleType": {"text": "Movie"},
            "titleGenres": {"genres": [{"genre": {"text": "Drama"}}]}, "ratingsSummary": {"aggregateRating": 7.1},
            "_r": r, "_d": d}


class _Resp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}


def _run(monkeypatch, tmp_path, resp, *args, cookie="c=1"):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
    if cookie:
        monkeypatch.setenv("IMDB_COOKIE", cookie)
    else:
        monkeypatch.delenv("IMDB_COOKIE", raising=False)
    out = tmp_path / "imdb_ratings.json"
    return fi.main(["--out", str(out), *args]), out


def test_items_found_by_content_and_parsed():
    html = _page([_title("tt15239678", "Dune: Part Two", 9), _title("tt1615147", "Margin Call", 8)])
    data = json.loads(fi.NEXT.search(html).group(1))
    items = fi.find_items(data)
    assert len(items) == 2
    row = fi.to_row(items[0][1])
    assert row == {"id": "tt15239678", "title": "Dune: Part Two", "original": "", "rating": 9, "date": "2026-10-20",
                   "type": "Movie", "year": 2024, "genres": ["Drama"], "imdb_rating": 7.1,
                   "url": "https://www.imdb.com/title/tt15239678/"}


def test_waf_challenge_and_missing_cookie_exit_2(monkeypatch, tmp_path, capsys):
    rc, _ = _run(monkeypatch, tmp_path, _Resp(202, "", {"x-amzn-waf-action": "challenge"}))
    assert rc == 2 and "CHANGED=false" in capsys.readouterr().out
    rc, _ = _run(monkeypatch, tmp_path, _Resp(200, "<html/>"), cookie="")
    assert rc == 2


def test_probe_reports_shape_and_writes_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    rc, out = _run(monkeypatch, tmp_path, _Resp(200, _page([_title("tt0000001", "Secret Title", 7)])), "--probe")
    text = capsys.readouterr().out
    assert rc == 0 and not out.exists()
    assert "::notice title=IMDb::probe: HTTP 200" in text and "1 rated items" in text
    assert "Secret Title" not in text                     # shape only, never the data


def test_merge_keeps_old_and_updates_changed(monkeypatch, tmp_path, capsys):
    page = _Resp(200, _page([_title("tt0000001", "A", 6), _title("tt0000002", "B", 8)]))
    rc, out = _run(monkeypatch, tmp_path, page)
    assert rc == 0 and "CHANGED=true" in capsys.readouterr().out
    page2 = _Resp(200, _page([_title("tt0000001", "A", 9), _title("tt0000003", "C", 5)]))
    rc, out = _run(monkeypatch, tmp_path, page2)
    r = {x["id"]: x for x in json.loads(out.read_text(encoding="utf-8"))["ratings"]}
    assert set(r) == {"tt0000001", "tt0000002", "tt0000003"} and r["tt0000001"]["rating"] == 9
    assert len(out.read_text(encoding="utf-8").splitlines()) == 5      # one rating per line


def test_build_merges_page_over_export(tmp_path):
    (tmp_path / "imdb_export.csv").write_text(
        "Const,Your Rating,Date Rated,Title,Original Title,URL,Title Type,IMDb Rating,Runtime (mins),Year,Genres,Num Votes,Release Date,Directors\n"
        'tt0000001,6,2020-01-01,"A","A",u,Movie,7,100,2001,"Drama",1,2001-01-01,X\n', encoding="utf-8")
    (tmp_path / "imdb_ratings.json").write_text(json.dumps({"ratings": [
        {"id": "tt0000001", "rating": 9, "date": "2026-10-20", "title": "A"},
        {"id": "tt0000009", "rating": 7, "date": "2026-10-21", "title": "New one", "year": 2026}]}), encoding="utf-8")
    films = {f["id"]: f for f in load_imdb_any(tmp_path)}
    assert films["tt0000001"]["rating"] == 9 and films["tt0000001"]["runtime"] == 100   # export fields kept
    assert films["tt0000009"]["title"] == "New one" and films["tt0000009"]["bulk"] is False
