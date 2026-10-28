# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the city pages in gen_country_pages.py — slugs, home eras,
which cities get a page and which trips count as a visit."""
import json
from datetime import datetime, timezone

import gen_country_pages as gcp


def _ts(y, m=6, d=1):
    return int(datetime(y, m, d, 12, tzinfo=timezone.utc).timestamp())


def test_city_slug_folds_accents_and_cyrillic():
    assert gcp.city_slug("Chișinău") == "chisinau"
    assert gcp.city_slug("Białystok") == "bialystok"
    assert gcp.city_slug("Saint Petersburg") == "saint-petersburg"
    assert gcp.city_slug("Минск") == "minsk"
    assert gcp.city_slug("Biel/Bienne") == "biel-bienne"


def test_city_slugs_disambiguate_clashes():
    slugs = gcp.city_slugs([("Paris", "France"), ("París", "Spain"), ("Paris ", "France")])
    assert slugs["Paris"] == "paris"
    assert slugs["París"] != "paris" and slugs["París"].startswith("paris-")
    assert len(set(slugs.values())) == 3


def test_home_spans_clamp_the_open_era_and_skip_other_cities():
    hist = [(_ts(2012, 9, 1), "Chișinău"), (_ts(2026, 7, 29), "Minsk"), (_ts(2099, 1, 1), "Chișinău")]
    assert gcp._home_spans("Chișinău", "Chișinău", hist, 2026) == [(0, 2012), (2026, 2026)]
    assert gcp._home_spans("Minsk", "Chișinău", hist, 2026) == [(2012, 2026)]
    assert gcp._home_spans("Vilnius", "Chișinău", hist, 2026) == []


def _row(city, ts, cid, country="Belarus", **kw):
    r = {"city": city, "country": country, "date": str(ts), "venue": f"V {cid}",
         "venue_id": f"v{cid}", "checkin_id": f"c{cid}", "lat": "53.9", "lng": "27.5",
         "category": "Café", "shout": "", "neighborhood": ""}
    r.update(kw)
    return r


def test_city_pages_threshold_home_tags_and_trip_filter(tmp_path):
    (tmp_path / "country_flags.json").write_text(json.dumps({"Belarus": "BY"}), encoding="utf-8")
    rows = [_row("Minsk", _ts(2015, 1, 1 + i % 27), i) for i in range(25)]
    rows += [_row("Brest", _ts(2010, 3, 1 + i), 100 + i) for i in range(20)]
    rows += [_row("Hrodna", _ts(2016), 200 + i) for i in range(3)]
    trips = [
        # a trip while Minsk was home that only passes through Minsk: not a visit to Minsk
        {"id": 1, "name": "Brest run", "start_ts": _ts(2015, 2), "countries": ["Belarus"],
         "cities": ["Brest", "Minsk"], "checkins": []},
        # before the move: Minsk was a destination
        {"id": 2, "name": "Minsk visit", "start_ts": _ts(2010, 3), "countries": ["Belarus"],
         "cities": ["Minsk"], "checkins": []},
    ]
    stats: dict = {}
    n = gcp.build_page(config_dir=str(tmp_path), out_path=str(tmp_path / "out"), rows=rows,
                       trips=trips, stats_data=stats, home_city="Minsk",
                       home_history=[(_ts(2012, 9, 1), "Chișinău")])
    out = tmp_path / "out"
    assert stats["city_pages"] == {"Minsk": "minsk", "Brest": "brest"}   # Hrodna: under 20
    assert n == 1 + 2
    minsk = (out / "city-minsk.html").read_text(encoding="utf-8")
    assert "home 2012–2016" in minsk   # open era runs to the last year of data
    assert 'class="yr-bar home"' in minsk
    assert "Minsk visit" in minsk and "Brest run" not in minsk
    assert 'href="/country-by.html"' in minsk
    assert "Brest run" in (out / "city-brest.html").read_text(encoding="utf-8")
    # the country page links its top cities to their pages
    assert 'href="/city-brest.html"' in (out / "country-by.html").read_text(encoding="utf-8")
    assert (out / "city_ck_minsk.json").exists()


def test_outline_file_companions_and_all_cities(tmp_path):
    (tmp_path / "country_flags.json").write_text(json.dumps({"Belarus": "BY"}), encoding="utf-8")
    rows = [_row("Minsk", _ts(2015, 1, 1 + i % 27), i, with_name="Joanna K." if i < 5 else "",
                 lat=f"53.9{i:02d}") for i in range(25)]
    rows += [_row(f"Village {k}", _ts(2016), 100 + k) for k in range(11)]
    poly = {"type": "Polygon", "coordinates": [[[27.4, 53.8], [27.7, 53.8], [27.7, 54.0], [27.4, 53.8]]]}
    gcp.build_page(config_dir=str(tmp_path), out_path=str(tmp_path / "out"), rows=rows, trips=[],
                   stats_data={}, boundaries={gcp.bnd_key("city", "Minsk", "Belarus"): {"g": poly, "t": 1}})
    out = tmp_path / "out"
    minsk = (out / "city-minsk.html").read_text(encoding="utf-8")
    assert json.loads((out / "bnd_city_minsk.json").read_text(encoding="utf-8")) == poly
    assert '"g":"bnd_city_minsk.json"' in minsk
    assert "Most often with" in minsk and "Joanna K." in minsk
    by = (out / "country-by.html").read_text(encoding="utf-8")
    assert "All 12 cities" in by
    assert '<a class="chip" href="/city-minsk.html">' in by and '<span class="chip">Village 3<b>1</b></span>' in by
    assert not (out / "bnd_country_by.json").exists()      # no cached outline: the page looks it up live
    assert ("country", "Belarus", "Belarus") in gcp.page_places(rows)
    assert ("city", "Minsk", "Belarus") in gcp.page_places(rows)
