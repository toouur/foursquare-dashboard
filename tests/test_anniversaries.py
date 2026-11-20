from collections import Counter
from datetime import date, datetime, timezone

from gen_country_pages import _once_and_not_yet, _region_progress
from metrics.anniversaries import anniversaries


def _utc(r):
    return datetime.fromtimestamp(int(r["date"]), tz=timezone.utc)


def _row(y, m, d, city, country, vid="v", cat="Café"):
    return {"date": str(int(datetime(y, m, d, 12, tzinfo=timezone.utc).timestamp())),
            "city": city, "country": country, "venue_id": vid, "venue": vid, "category": cat}


def test_firsts_within_the_next_week():
    rows = [_row(2014, 11, 22, "Minsk", "Belarus")] * 25 + [
        _row(2016, 11, 20, "Vilnius", "Lithuania"),        # today, 10 years; Vilnius too small
        _row(2020, 12, 30, "Riga", "Latvia"),              # outside the window
    ]
    a = anniversaries(rows, _utc, date(2026, 11, 20))
    assert [(x["kind"], x["name"], x["years"], x["in_days"]) for x in a] == [
        ("country", "Lithuania", 10, 0),
        ("first", "", 12, 2), ("country", "Belarus", 12, 2), ("city", "Minsk", 12, 2),
    ]


def test_window_crosses_new_year():
    a = anniversaries([_row(2019, 1, 2, "Riga", "Latvia")], _utc, date(2026, 12, 29))
    assert a[0]["years"] == 8 and a[0]["in_days"] == 4


def test_empty():
    assert anniversaries([], _utc, date(2026, 1, 1)) == []


def test_been_once_keeps_places_worth_a_return_newest_first():
    cfg = {"group": {"Café": "Food & Drink", "Bus Stop": "Transport"},
           "explorer": {"Café": "Coffee Shops", "Bus Stop": "Public Transit", "Museum": "Museums"}}
    rows = [_row(2020, 1, 1, "X", "Y", "a"), _row(2021, 1, 1, "X", "Y", "a"),   # twice
            _row(2022, 1, 1, "X", "Y", "b"), _row(2023, 1, 1, "X", "Y", "c"),
            _row(2024, 1, 1, "X", "Y", "s", cat="Bus Stop")]
    h = _once_and_not_yet(rows, cfg, Counter({"Museums": 30, "Public Transit": 99}))
    assert "Been once · 2 places" in h and h.index("#c") < h.index("#b") and "#s" not in h
    assert "Not yet here" not in h                       # under 50 check-ins in the city
    h = _once_and_not_yet(rows * 10, cfg, Counter({"Museums": 30, "Public Transit": 99}))
    assert "Museums" in h and "Public Transit" not in h


def test_region_progress_lists_missing_dashed():
    iso = {"names": {"BY-BR": "Brest Region", "BY-MI": "Minsk Region", "BY-HM": "Minsk (city)"},
           "parents": {}}
    row = {"regions": [["Minsk (city)", 900, "BY-HM", 2014], ["Brest Region", 12, "BY-BR", 2016]], "of": 3}
    h = _region_progress("Belarus", row, iso)
    assert "Regions · 2 of 3" in h and 'chip miss">Minsk Region' in h
