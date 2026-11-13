"""City inference: generic merge names stay local, unreviewed cities via real check-ins."""
import csv

from transform import apply_transforms, build_blank_city_resolver, drop_foreign_city_on_country_rows


def _review(tmp_path, points):
    p = tmp_path / "city_merge_normalized_review.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["decision_source", "resolved_city", "low_volume_checkins_detail",
                                           "country", "lat", "lng"])
        w.writeheader()
        for city, country, lat, lng in points:
            w.writerow({"decision_source": "BLANK_CITY_INFERRED", "resolved_city": city,
                        "low_volume_checkins_detail": "", "country": country, "lat": lat, "lng": lng})
    return build_blank_city_resolver(p)


def _row(city, country, lat, lng, ts="1"):
    return {"date": ts, "city": city, "country": country, "lat": str(lat), "lng": str(lng),
            "venue_id": "", "category": "Café"}


def test_downtown_of_another_city_is_not_cairo(tmp_path):
    resolver = _review(tmp_path, [("Alexandria", "Egypt", 31.20, 29.92)])
    rows = [_row("Cairo", "Egypt", 30.05, 31.24, str(i)) for i in range(3)]
    rows += [_row("Downtown", "Egypt", 30.046, 31.238, "10"),     # downtown Cairo
             _row("Downtown", "Egypt", 31.196, 29.898, "11")]     # downtown Alexandria
    apply_transforms(rows, {"city_merge": {"Downtown": "Cairo"}}, blank_city_resolver=resolver)
    assert rows[3]["city"] == "Cairo"
    assert rows[4]["city"] == "Alexandria" and rows[4]["city_inferred"] == "1"


def test_blank_row_takes_unreviewed_city_of_a_nearby_checkin(tmp_path):
    # the review only knows Charleroi (~90 km away); real check-ins name Liège
    resolver = _review(tmp_path, [("Charleroi", "Belgium", 50.41, 4.44)])
    rows = [_row("Liège", "Belgium", 50.633, 5.575, str(i)) for i in range(3)]
    rows.append(_row("", "Belgium", 50.6354, 5.5806, "10"))
    apply_transforms(rows, {}, blank_city_resolver=resolver)
    assert rows[3]["city"] == "Liège"


def test_reviewed_points_still_decide_near_them(tmp_path):
    # a suburb Foursquare labels with the big city's name keeps its reviewed name
    resolver = _review(tmp_path, [("Zarechye", "Belarus", 53.955, 27.385)])
    rows = [_row("Minsk", "Belarus", 53.956, 27.383, str(i)) for i in range(3)]
    rows.append(_row("", "Belarus", 53.958, 27.386, "10"))
    apply_transforms(rows, {}, blank_city_resolver=resolver)
    assert rows[3]["city"] == "Zarechye"


def test_country_row_keeps_a_border_twin_city():
    rows = [_row("Sculeni", "Moldova", 47.33, 27.62, "1"), _row("Sculeni", "Moldova", 47.33, 27.62, "2"),
            _row("Sculeni", "Romania", 47.317, 27.61, "3"),
            dict(_row("Sculeni", "Romania", 45.8, 24.9, "4"), category="Country"),
            dict(_row("Minsk", "Russia", 54.5, 31.0, "5"), category="Country")]
    rows.insert(0, _row("Minsk", "Belarus", 53.9, 27.56, "0"))
    assert drop_foreign_city_on_country_rows(rows) == 1
    assert rows[4]["city"] == "Sculeni" and rows[5]["city"] == ""
