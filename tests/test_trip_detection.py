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


class TestRelocationTrip:
    """A move to a new home city: the trip's last check-in IS the homecoming.

    Mirrors the real 2026-07-29 Minsk→Chișinău move. Rows dated before the era
    boundary are "away" (home is still Minsk), so the arrival at the new flat
    lands *inside* the trip — and everything after the boundary is ordinary
    home-city life that must stay out of it.
    """

    # Era boundary: rows before it are Minsk-home, at/after it Chișinău-home.
    CUTOVER = T0 + 2 * DAY
    HOME_VID = "f" * 24                    # the new flat's venue_id

    def _rows(self):
        start = T0 + DAY
        rows = [home(T0)]                                    # Minsk, home era
        rows += [away(start + i * HOUR) for i in range(4)]   # Warsaw, en route
        # Arrival at the new flat, still before the cutover → part of the trip.
        rows += [away(self.CUTOVER - 2 * HOUR, city="Chisinau", country="Moldova",
                      category="Apartment or Condo", venue="Alecu Russo, 55",
                      venue_id=self.HOME_VID)]
        # Next day, new home city: ordinary life, not trip material.
        rows += [away(self.CUTOVER + 10 * HOUR, city="Chisinau", country="Moldova",
                      category="Neighborhood", venue="Ciocana",
                      venue_id="c" * 24)]
        return rows

    def _kw(self, **over):
        kw = {
            "home_city": "Chisinau",
            "min_checkins": 5,
            "home_history": [(self.CUTOVER, "Minsk")],
        }
        kw.update(over)
        return kw

    def test_trip_ends_at_home_venue(self):
        trips = detect_trips(self._rows(),
                             **self._kw(home_venue_ids={self.HOME_VID}))
        assert len(trips) == 1
        t = trips[0]
        assert t["checkins"][-1]["venue"] == "Alecu Russo, 55"
        assert t["checkin_count"] == 5      # 4 Warsaw + the homecoming, no more
        assert "Ciocana" not in [c["venue"] for c in t["checkins"]]

    def test_without_home_venue_ids_neighborhood_absorbs_next_day(self):
        # Same timeline, guard disarmed: the arrival Neighborhood fallback
        # reaches into the following day. This is the behaviour the
        # home-venue guard exists to suppress.
        trips = detect_trips(self._rows(), **self._kw())
        assert len(trips) == 1
        assert trips[0]["checkins"][-1]["venue"] == "Ciocana"

    def test_guard_only_fires_on_configured_home_venue(self):
        # An unrelated venue id in home_venue_ids must not arm the guard.
        trips = detect_trips(self._rows(),
                             **self._kw(home_venue_ids={"z" * 24}))
        assert trips[0]["checkins"][-1]["venue"] == "Ciocana"


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


class TestAirDepartureAndHomecoming:
    """Air travel from the current home — the asymmetry found on 2026-10-12.

    Foursquare almost never uses the bare "Airport" category (28 of ~1000
    airport check-ins; Chișinău's RMO has none — it is always "International
    Airport"). Minsk hid this: the airport there is reached by the airport bus
    or train, so all 37 Minsk flight trips opened at a "Bus Station" /
    "Rail Station" that the hub scan already covered. Flying out of Chișinău,
    the whole leg — taxi, RMO, security, gate, plane — was cut off and the trip
    began only on landing abroad.
    """

    HOME_VID = "c" * 24

    def _rows(self):
        # Depart: airport in the home city, then abroad for five days.
        rows = [home(T0)]
        rows.append(home(T0 + 10 * HOUR, category="International Airport",
                         venue="Home International Airport", venue_id="apt00000000000000000000a"))
        rows.append(home(T0 + 11 * HOUR, category="Airport Gate", venue="Gate 4",
                        venue_id="gate0000000000000000000a"))
        rows += [away(T0 + DAY + i * HOUR) for i in range(6)]
        return rows

    def test_departure_starts_at_the_home_airport(self):
        trips = detect_trips(self._rows(), min_checkins=5)
        assert len(trips) == 1
        first = trips[0]["checkins"][0]
        assert first["category"] == "International Airport", (
            "the trip must open at the home airport, not on landing abroad")

    def test_arrival_survives_a_transit_town_and_ends_at_the_home_venue(self):
        # Coming back: land at the home airport, then an airport bus geotagged
        # to a NEIGHBOURING town (the RMO bus comes back as "Ialoveni"), then
        # the home address. The transit row used to end the scan outright.
        rows = self._rows()
        back = T0 + DAY + 10 * HOUR
        rows.append(home(back, category="International Airport",
                         venue="Home International Airport", venue_id="apt00000000000000000000a"))
        rows.append({"date": str(back + 600), "city": "Ialoveni", "country": "Belarus",
                     "category": "Moving Target", "venue": "Airport bus",
                     "venue_id": "bus00000000000000000000a", "lat": "53.8",
                     "lng": "27.5", "checkin_id": ""})
        rows.append(home(back + 2 * HOUR, category="Apartment or Condo",
                         venue="Home address", venue_id=self.HOME_VID))
        trips = detect_trips(rows, min_checkins=5, home_venue_ids={self.HOME_VID})
        assert len(trips) == 1
        assert trips[0]["checkins"][-1]["venue_id"] == self.HOME_VID, (
            "the trip must reach the home address past the transit town")
