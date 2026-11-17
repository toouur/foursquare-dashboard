from datetime import datetime, timezone

from metrics.pulse import pulse


def _row(ts, vid, city="Minsk", country="Belarus", cat="Café", venue=None):
    return {"date": str(ts), "venue_id": vid, "venue": venue or vid, "city": city,
            "country": country, "category": cat}


def _utc(r):
    return datetime.fromtimestamp(int(r["date"]), tz=timezone.utc)


def ts(y, m, d):
    return int(datetime(y, m, d, 12, tzinfo=timezone.utc).timestamp())


def test_month_to_date_compares_the_same_days_a_year_earlier():
    rows = [
        _row(ts(2025, 11, 3), "a"), _row(ts(2025, 11, 20), "b"),   # 20th: after today's day
        _row(ts(2026, 11, 2), "a"), _row(ts(2026, 11, 5), "c", city="Vilnius", country="Lithuania"),
    ]
    p = pulse(rows, _utc)
    m = p["month"]
    assert m["label"] == "November 1–5"
    assert m["this"] == {"year": 2026, "checkins": 2, "venues": 2, "new_venues": 1, "cities": 2, "countries": 2}
    assert m["prev"]["checkins"] == 1 and m["prev"]["new_venues"] == 1


def test_firsts_only_count_first_ever_within_the_window():
    rows = [
        _row(ts(2020, 1, 1), "old", cat="Bar"),
        _row(ts(2026, 11, 1), "old", cat="Bar"),                    # revisit: not new
        _row(ts(2026, 11, 2), "new1", city="Riga", country="Latvia", cat="Museum"),
        _row(ts(2026, 11, 3), "new2", cat="Bar"),
    ]
    f = pulse(rows, _utc)["firsts"]
    assert f["venues"] == 2
    assert [v[2] for v in f["venue_sample"]] == ["new2", "new1"]   # newest first
    assert f["cities"] == [["Riga", "Latvia"]]
    assert f["countries"] == ["Latvia"]
    assert f["categories"] == ["Museum"]


def test_empty_rows():
    assert pulse([], _utc) == {}
