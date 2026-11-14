"""refresh_city_review.py: new cities join the blank-city review table,
suburbs next to a reviewed city do not."""
import csv

import refresh_city_review as rcr
import transform as t

HEADER = ["raw_city", "country", "checkins_count", "all_raw_count", "lat", "lng", "first_checkin_utc",
          "last_checkin_utc", "low_volume_checkins_count", "low_volume_checkin_names",
          "low_volume_checkins_detail", "normalized_name", "decision_source", "suggested_target",
          "resolved_city", "distance_to_target_km", "timeline_overlap", "review", "comment"]


def _review(tmp_path):
    p = tmp_path / t.REVIEW_CSV_NAME
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for city, la, ln in (("Rotterdam", 51.92, 4.48), ("Minsk", 53.90, 27.56)):
            w.writerow({"raw_city": "(BLANK_CITY) x", "country": "Netherlands" if city == "Rotterdam" else "Belarus",
                        "lat": la, "lng": ln, "decision_source": "BLANK_CITY_INFERRED", "resolved_city": city})
    return p


def _row(city, country, la, ln):
    return {"city": city, "country": country, "lat": str(la), "lng": str(ln), "category": "Café"}


def test_far_city_added_suburb_left(tmp_path):
    p = _review(tmp_path)
    rows = [_row("Amsterdam", "Netherlands", 52.37, 4.89)] * 3 + [_row("Valeryanovo", "Belarus", 53.93, 27.60)] * 3 \
        + [_row("Lonely", "Netherlands", 53.0, 6.0)]          # one check-in: not enough
    miss = {m.city: m for m in rcr.missing_cities(rows, p)}
    assert set(miss) == {"Amsterdam", "Valeryanovo"}
    assert miss["Amsterdam"].addable and not miss["Valeryanovo"].addable

    rcr.append_rows(p, [m for m in miss.values() if m.addable], "2026-11-14")
    _, centroids, reviewed = t.review_points(p)
    assert "Amsterdam" in reviewed["Netherlands"] and "Valeryanovo" not in reviewed["Belarus"]
    assert rcr.missing_cities(rows, p)[0].city == "Valeryanovo"   # idempotent: Amsterdam is known now
    res = t.build_blank_city_resolver(p)
    assert res({"date": "1", "lat": "52.36", "lng": "4.90", "country": "Netherlands"}) == "Amsterdam"
