# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""culture_sources + metrics.culture — films, wines and sound on the check-in timeline."""
from __future__ import annotations

from datetime import datetime, timezone

from conftest import make_row
from culture_sources import BULK_DAY, load_imdb, load_vivino
from metrics.culture import compute_culture

IMDB_HEAD = ("Const,Your Rating,Date Rated,Title,Original Title,URL,Title Type,IMDb Rating,"
             "Runtime (mins),Year,Genres,Num Votes,Release Date,Directors\n")
VIVINO_HEAD = ("Winery,Wine name,Vintage,Region,Country,Regional wine style,Average rating,Scan date,"
               "Scan/Review Location,Your rating,Your review,Personal Note,Wine type,Drinking Window,"
               "Link to wine,Label image\n")


def _ts(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def _row(when, venue, category, **kw):
    kw.setdefault("source_app", "Swarm")
    kw.setdefault("country", "Belarus")
    return make_row(_ts(when), city="Minsk", category=category, venue=venue,
                    venue_id=f"v-{venue}", lat="53.9", lng="27.56", checkin_id=f"c{_ts(when)}", **kw)


def _imdb(tmp_path, lines):
    p = tmp_path / "imdb.csv"
    p.write_text("﻿" + IMDB_HEAD + "".join(lines), encoding="utf-8")
    return load_imdb(p)


def _film(tt, rating, date, title, year):
    return (f'{tt},{rating},{date},"{title}","{title}",https://www.imdb.com/title/{tt},Movie,7.1,107,'
            f'{year},"Drama, Thriller",1000,"{year}-01-01","Someone"\n')


def test_load_imdb_flags_catch_up_days(tmp_path):
    lines = [_film(f"tt{i}", 7, "2011-11-08", f"Old {i}", 1999) for i in range(BULK_DAY)]
    lines.append(_film("tt99", 9, "2024-03-18", "Dune, Part Two", 2024))
    films = _imdb(tmp_path, lines)
    assert len(films) == BULK_DAY + 1
    assert all(f["bulk"] for f in films if f["date"] == "2011-11-08")
    dune = films[-1]
    assert dune == {**dune, "title": "Dune, Part Two", "rating": 9, "year": 2024, "bulk": False,
                    "genres": ["Drama", "Thriller"], "runtime": 107, "imdb_rating": 7.1}


def _wine(name, rating, scan, link="https://www.vivino.com/wines/1", vintage="2020"):
    return (f'Winery,{name},{vintage},Region,Moldova,,4.1,{scan},,{rating},,,'
            f'Red Wine,,{link},https://images.vivino.com/x.jpg\n')


def test_load_vivino_keeps_only_rated_and_dedups(tmp_path):
    p = tmp_path / "w.csv"
    p.write_text(VIVINO_HEAD + _wine("A", "3.8", "2024-05-01 18:00:00")
                 + _wine("A", "3.8", "2024-05-01 18:00:00")            # exact duplicate row
                 + _wine("Scanned only", "", "2024-05-02 18:00:00")    # never rated
                 + _wine("No date", "4.0", "", link="https://www.vivino.com/wines/2"), encoding="utf-8")
    wines = load_vivino(p)
    assert [w["wine"] for w in wines] == ["A", "No date"]
    assert wines[0]["ts"] == _ts("2024-05-01T18:00:00") and wines[0]["rating"] == 3.8
    assert wines[1]["ts"] is None


def test_missing_files_mean_no_culture(tmp_path):
    assert load_imdb(tmp_path / "nope.csv") == [] and load_vivino(tmp_path / "nope.csv") == []


def test_films_cinema_road_home_backlog(tmp_path):
    rows = [_row("2024-03-17T17:00:00", "Silver Screen", "Movie Theater"),
            _row("2024-06-01T10:00:00", "Kraków Główny", "Train Station", country="Poland")]
    trips = [{"id": 3, "name": "Kraków", "start_date": "2024-06-01", "end_date": "2024-06-05",
              "checkins": [{"ts": _ts("2024-06-01T10:00:00")}, {"ts": _ts("2024-06-05T18:00:00")}]}]
    lines = [_film("tt1", 9, "2024-03-18", "Dune Part Two", 2024),      # the day after the cinema
             _film("tt2", 6, "2024-03-18", "Highlander", 1986),         # same day, but an old film
             _film("tt3", 7, "2024-06-03", "On the train", 2010),
             _film("tt4", 8, "2024-08-01", "At home", 2001)]
    lines += [_film(f"tb{i}", 7, "2011-11-08", f"Seen long ago {i}", 1995) for i in range(BULK_DAY)]
    c = compute_culture(rows, trips, films=_imdb(tmp_path, lines))
    where = {f["title"]: f["where"] for f in c["films"]}
    assert where["Dune Part Two"] == "cinema" and where["Highlander"] == "home"
    assert where["On the train"] == "road" and where["At home"] == "home"
    assert where["Seen long ago 0"] == "backlog"
    dune = next(f for f in c["films"] if f["title"] == "Dune Part Two")
    assert dune["venue"] == "Silver Screen"
    assert c["trips"]["3"]["films"][0]["title"] == "On the train"
    y = c["years"]["2024"]
    assert y["films"] == 4 and y["where"] == {"cinema": 1, "home": 2, "road": 1}
    assert "2011" not in c["years"]                                   # catch-up days are not a year's viewing


def test_wine_is_placed_where_you_were(tmp_path):
    rows = [_row("2022-03-16T12:45:00", "Office canteen", "Corporate Cafeteria"),
            _row("2022-03-16T13:03:00", "Три лося", "Convenience Store"),
            _row("2022-05-01T19:00:00", "Wine Bar X", "Wine Bar"),
            _row("2022-05-01T19:20:00", "Bus stop", "Bus Stop")]
    p = tmp_path / "w.csv"
    p.write_text(VIVINO_HEAD + _wine("Shop bottle", "3.5", "2022-03-16 13:05:00")
                 + _wine("Bar glass", "4.0", "2022-05-01 19:30:00", link="https://www.vivino.com/wines/2")
                 + _wine("Nowhere", "3.0", "2022-07-01 03:00:00", link="https://www.vivino.com/wines/3"),
                 encoding="utf-8")
    c = compute_culture(rows, [], wines=load_vivino(p))
    w = {x["wine"]: x for x in c["wines"]}
    assert w["Shop bottle"]["venue"] == "Три лося"          # not the canteen 20 min earlier
    assert w["Bar glass"]["venue"] == "Wine Bar X"          # the bar beats the closer bus stop
    assert w["Nowhere"].get("venue") is None and w["Nowhere"]["date"] == "2022-07-01"
    assert w["Bar glass"]["lat"] == 53.9
    assert c["years"]["2022"]["wines"] == 3 and c["years"]["2022"]["wine_countries"] == [("Moldova", 3)]


def test_trip_sound_spans_first_to_last_checkin():
    rows = [_row("2024-06-01T10:00:00", "Station", "Train Station")]
    trips = [{"id": 1, "name": "T", "start_date": "2024-06-01", "end_date": "2024-06-02",
              "checkins": [{"ts": _ts("2024-06-01T10:00:00")}, {"ts": _ts("2024-06-02T20:00:00")}]}]
    sc = [(_ts("2024-06-01T09:00:00"), "Before", "x", ""),              # at home, before leaving
          (_ts("2024-06-01T11:00:00"), "Кино", "Группа крови", "Группа крови"),
          (_ts("2024-06-01T12:00:00"), "Кино", "Группа крови", "Группа крови"),
          (_ts("2024-06-02T15:00:00"), "Air", "Sexy Boy", "Moon Safari")]
    snd = compute_culture(rows, trips, scrobbles=sc)["trips"]["1"]["sound"]
    assert snd["n"] == 3
    assert snd["artists"][0] == ("Кино", 2) and snd["tracks"][0] == ["Кино", "Группа крови", 2]


def test_culture_page_and_profiles(tmp_path):
    import json

    import gen_culture
    culture = {"films": [{"title": "Dune", "rating": 9, "date": "2024-03-18", "where": "cinema"}],
               "wines": [], "trips": {"3": {"sound": None, "wines": [], "films": []}}, "years": {}}
    n = gen_culture.build_page(out_path=str(tmp_path), culture=culture,
                               music_by_year={"2024": {"scrobbles": 5}},
                               profiles={"imdb_profile": "https://www.imdb.com/user/x/ratings/", "vivino_profile": ""})
    assert n == 1
    html = (tmp_path / "culture.html").read_text(encoding="utf-8")
    assert "{{" not in html.replace("{{CARTO_KEY}}", "")
    assert 'href="https://www.imdb.com/user/x/ratings/"' in html and "Vivino ↗" not in html
    data = json.loads((tmp_path / "culture_data.json").read_text(encoding="utf-8"))
    assert data["music"] == {"2024": {"scrobbles": 5}} and data["trips"]["3"]["films"] == []
