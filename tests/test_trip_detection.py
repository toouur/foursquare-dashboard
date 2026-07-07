# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for metrics.detect_trips — the trip-detection heuristics.

Synthetic timelines use noon-UTC timestamps so country-based localisation
(Europe/Minsk = UTC+3, Europe/Warsaw = UTC+1/+2) can never shift a check-in
across a date boundary and break duration assertions.
"""
from metrics import detect_trips

# 2023-06-01 12:00:00 UTC — a fixed, DST-stable anchor.
T0 = 1685620800
HOUR = 3600
DAY = 86400


def home(ts, category="Coffee Shop", venue="Cafe", venue_id="h" * 24):
    return {
        "date": str(ts), "city": "Minsk", "country": "Belarus",
        "category": category, "venue": venue, "venue_id": venue_id,
        "lat": "53.9", "lng": "27.56", "checkin_id": "",
    }


def away(ts, city="Warsaw", country="Poland", category="Plaza",
         venue="Rynek", venue_id="w" * 24):
    return {
        "date": str(ts), "city": city, "country": country,
        "category": category, "venue": venue, "venue_id": venue_id,
        "lat": "52.23", "lng": "21.01", "checkin_id": "",
    }


def simple_trip_rows(n=5, start=T0 + DAY):
    """n consecutive away check-ins 1 h apart, bracketed by home check-ins."""
    rows = [home(T0)]
    rows += [away(start + i * HOUR) for i in range(n)]
    rows += [home(start + n * HOUR + 2 * DAY)]
    return rows


class TestBasicDetection:
    def test_single_trip_detected(self):
        trips = detect_trips(simple_trip_rows(5), min_checkins=5)
        assert len(trips) == 1
        t = trips[0]
        assert t["checkin_count"] == 5
        assert t["countries"] == ["Poland"]
        assert t["name"] == "Warsaw, Poland"

    def test_below_min_checkins_dropped(self):
        # 3 away rows, threshold 5: even the min-1 pre-filter rejects this.
        trips = detect_trips(simple_trip_rows(3), min_checkins=5)
        assert trips == []

    def test_home_rows_split_into_two_trips(self):
        rows = [home(T0)]
        rows += [away(T0 + DAY + i * HOUR) for i in range(3)]
        rows += [home(T0 + DAY + 12 * HOUR)]          # non-transport home visit
        rows += [away(T0 + 3 * DAY + i * HOUR, city="Kraków") for i in range(3)]
        rows += [home(T0 + 5 * DAY)]
        trips = detect_trips(rows, min_checkins=2)
        assert len(trips) == 2
        assert trips[0]["cities"][0] == "Warsaw"
        assert trips[1]["cities"][0] == "Kraków"

    def test_rows_without_date_ignored(self):
        rows = simple_trip_rows(5)
        rows.append({"date": "", "city": "Warsaw", "country": "Poland",
                     "category": "", "venue": "", "venue_id": "",
                     "lat": "", "lng": "", "checkin_id": ""})
        trips = detect_trips(rows, min_checkins=5)
        assert len(trips) == 1
        assert trips[0]["checkin_count"] == 5

    def test_chronological_sort_not_input_order(self):
        rows = simple_trip_rows(5)
        rows.reverse()  # feed in reverse — detect_trips must sort by ts
        trips = detect_trips(rows, min_checkins=5)
        assert len(trips) == 1
        assert trips[0]["checkin_count"] == 5


class TestHubExtension:
    def test_departure_hub_included(self):
        start = T0 + DAY
        rows = [
            home(T0),
            home(start - 2 * HOUR, category="Rail Station",
                 venue="Minsk Railway Station", venue_id="r" * 24),
        ]
        rows += [away(start + i * HOUR) for i in range(4)]
        rows += [home(start + 2 * DAY)]
        trips = detect_trips(rows, min_checkins=5)
        # 4 away + 1 hub = 5 → survives; hub is the first check-in
        assert len(trips) == 1
        assert trips[0]["checkin_count"] == 5
        assert trips[0]["checkins"][0]["venue"] == "Minsk Railway Station"

    def test_departure_hub_beyond_24h_not_included(self):
        start = T0 + 2 * DAY
        rows = [
            home(start - 25 * HOUR, category="Rail Station",
                 venue="Minsk Railway Station", venue_id="r" * 24),
        ]
        rows += [away(start + i * HOUR) for i in range(4)]
        trips = detect_trips(rows, min_checkins=5)
        assert trips == []  # 4 away rows, no hub rescue → below threshold

    def test_arrival_hub_included(self):
        start = T0 + DAY
        end = start + 4 * HOUR
        rows = [home(T0)]
        rows += [away(start + i * HOUR) for i in range(4)]
        rows += [home(end + 2 * HOUR, category="Airport",
                      venue="Minsk National Airport", venue_id="a" * 24)]
        trips = detect_trips(rows, min_checkins=5)
        assert len(trips) == 1
        assert trips[0]["checkins"][-1]["venue"] == "Minsk National Airport"

    def test_plain_home_checkin_not_treated_as_hub(self):
        start = T0 + DAY
        rows = [home(start - 2 * HOUR, category="Coffee Shop")]
        rows += [away(start + i * HOUR) for i in range(4)]
        trips = detect_trips(rows, min_checkins=5)
        assert trips == []  # coffee shop is not a transport hub

    def test_blank_city_rows_between_hub_and_trip_included(self):
        # Departure: Rail Station (Minsk) → highway (blank city) → Warsaw…
        start = T0 + DAY
        rows = [
            home(start - 3 * HOUR, category="Rail Station",
                 venue="Minsk Railway Station", venue_id="r" * 24),
            away(start - 1 * HOUR, city="", country="", category="Road",
                 venue="M1 Highway", venue_id="m" * 24),
        ]
        rows += [away(start + i * HOUR) for i in range(4)]
        trips = detect_trips(rows, min_checkins=5)
        assert len(trips) == 1
        venues = [c["venue"] for c in trips[0]["checkins"]]
        assert venues[0] == "Minsk Railway Station"
        assert "M1 Highway" in venues


class TestNamingAndMetadata:
    def test_two_country_trip_name(self):
        start = T0 + DAY
        rows = [home(T0)]
        rows += [away(start + i * HOUR) for i in range(3)]
        rows += [away(start + (3 + i) * HOUR, city="Vilnius",
                      country="Lithuania") for i in range(3)]
        rows += [home(start + 3 * DAY)]
        trips = detect_trips(rows, min_checkins=5)
        assert len(trips) == 1
        assert trips[0]["name"] == "Poland & Lithuania"

    def test_custom_name_override_by_start_ts(self):
        rows = simple_trip_rows(5)
        start_ts = rows[1]["date"]  # first away row
        trips = detect_trips(rows, min_checkins=5,
                             trip_names={start_ts: "Honeymoon"})
        assert trips[0]["name"] == "Honeymoon"

    def test_duration_and_dates(self):
        start = T0 + DAY
        rows = [home(T0)]
        rows += [away(start), away(start + DAY), away(start + 2 * DAY),
                 away(start + 2 * DAY + HOUR), away(start + 2 * DAY + 2 * HOUR)]
        rows += [home(start + 4 * DAY)]
        trips = detect_trips(rows, min_checkins=5)
        t = trips[0]
        assert t["duration"] == 3  # inclusive day span
        assert t["start_date"] < t["end_date"]

    def test_unique_places_deduped_by_venue_id(self):
        start = T0 + DAY
        rows = [home(T0)]
        rows += [away(start + i * HOUR, venue_id="same-venue-id-000000000")
                 for i in range(5)]
        rows += [home(start + 2 * DAY)]
        trips = detect_trips(rows, min_checkins=5)
        assert trips[0]["checkin_count"] == 5
        assert trips[0]["unique_places"] == 1


class TestOverrides:
    def test_trip_end_override_splits_trip(self):
        start = T0 + DAY
        # 6 contiguous away rows; force a split after the 3rd
        rows = [home(T0)]
        rows += [away(start + i * HOUR) for i in range(6)]
        rows += [home(start + 3 * DAY)]
        cut_ts = start + 2 * HOUR  # end of row index 2
        trips = detect_trips(rows, min_checkins=2,
                             trip_end_overrides={start: cut_ts})
        assert len(trips) == 2
        assert trips[0]["checkin_count"] == 3
        assert trips[1]["checkin_count"] == 3
