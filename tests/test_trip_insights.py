# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""metrics.trip_insights — home eras, gateways, seasonality (read-only views)."""
from __future__ import annotations

from datetime import datetime, timezone

from metrics.trip_insights import (
    era_comparison,
    trip_gateways,
    trip_insights,
    trip_seasonality,
)


def _ts(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def _ck(venue: str, category: str, city: str = "Minsk", country: str = "Belarus",
        vid: str = "") -> dict:
    return {"venue": venue, "category": category, "city": city, "country": country,
            "venue_id": vid or venue}


def _trip(start: str, end: str, checkins: list[dict], countries: list[str]) -> dict:
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    return {
        "start_date": start, "end_date": end, "start_ts": _ts(start),
        "start_year": a.year, "duration": (b - a).days + 1,
        "countries": countries, "checkins": checkins,
    }


STATION = _ck("Minsk Railway Station", "Rail Station")
BUS = _ck("Central Bus Station", "Bus Station")
KIV_AIR = _ck("Chișinău Airport", "International Airport", "Chișinău", "Moldova")
TAXI = _ck("Taxi", "Taxi", "Chișinău", "Moldova")
CAFE = _ck("Cafe", "Café", "Vilnius", "Lithuania")


class TestGateways:
    def test_counts_opening_and_closing_hub(self):
        trips = [
            _trip("2020-01-01", "2020-01-03", [STATION, CAFE, STATION], ["Belarus", "Lithuania"]),
            _trip("2021-01-01", "2021-01-03", [BUS, CAFE, STATION], ["Belarus", "Lithuania"]),
        ]
        gw = {g["venue"]: g for g in trip_gateways(trips)}
        assert (gw["Minsk Railway Station"]["opened"], gw["Minsk Railway Station"]["closed"]) == (1, 2)
        assert (gw["Central Bus Station"]["opened"], gw["Central Bus Station"]["closed"]) == (1, 0)
        assert gw["Minsk Railway Station"]["first"] == 2020
        assert gw["Minsk Railway Station"]["last"] == 2021

    def test_ride_before_the_airport_is_not_the_gateway(self):
        # The taxi opens the TRIP (era rule) but the airport is where it leaves from.
        trips = [_trip("2026-09-25", "2026-10-02", [TAXI, KIV_AIR, CAFE, KIV_AIR, TAXI],
                       ["Moldova", "Turkey"])]
        gw = trip_gateways(trips)
        assert [g["venue"] for g in gw] == ["Chișinău Airport"]
        assert gw[0]["mode"] == "✈️" and gw[0]["opened"] == 1 and gw[0]["closed"] == 1

    def test_trip_without_hub_contributes_nothing(self):
        assert trip_gateways([_trip("2020-01-01", "2020-01-01", [CAFE], ["Lithuania"])]) == []


class TestSeasonality:
    def test_days_are_split_across_months(self):
        s = trip_seasonality([_trip("2020-01-30", "2020-02-02", [CAFE], ["Lithuania"])])
        assert s["years"] == [2020]
        assert s["days"][0][0] == 2 and s["days"][0][1] == 2
        assert s["starts"][0] == 1 and sum(s["starts"]) == 1
        assert s["month_days"][:2] == [2, 2]

    def test_years_without_trips_still_get_a_row(self):
        s = trip_seasonality([
            _trip("2018-07-01", "2018-07-01", [CAFE], ["Lithuania"]),
            _trip("2020-08-01", "2020-08-03", [CAFE], ["Lithuania"]),
        ])
        assert s["years"] == [2018, 2019, 2020]
        assert sum(s["days"][1]) == 0
        assert s["peak_month"] == 7  # August, 0-based

    def test_empty(self):
        assert trip_seasonality([]) == {}


class TestEraComparison:
    HISTORY = [(_ts("2012-09-01"), "Chișinău"), (_ts("2026-07-29"), "Minsk")]
    CC = {"Chișinău": "Moldova", "Minsk": "Belarus"}

    def _rows(self, *days: str, refurb: bool = False) -> list[dict]:
        return [{"date": str(_ts(d)), "source_app": "refurbished" if refurb else "Swarm"} for d in days]

    def test_trips_belong_to_the_era_they_start_in(self):
        rows = self._rows("2012-01-01", refurb=True) + self._rows("2013-01-01", "2026-10-01")
        trips = [
            # Relocation trip: leaves Chișinău, ends in Minsk after the boundary.
            _trip("2012-08-30", "2012-09-01", [STATION], ["Moldova", "Ukraine", "Belarus"]),
            _trip("2014-05-01", "2014-05-04", [STATION], ["Belarus", "Lithuania"]),
            _trip("2026-09-25", "2026-09-27", [KIV_AIR], ["Moldova", "Turkey"]),
        ]
        eras = era_comparison(trips, rows, "Chișinău", self.HISTORY, self.CC)
        assert [e["city"] for e in eras] == ["Chișinău", "Minsk", "Chișinău"]
        assert [e["trips"] for e in eras] == [1, 1, 1]
        assert eras[0]["to"] == "2012-08-31" and eras[1]["from"] == "2012-09-01"
        assert eras[1]["top_dest"] == ["Lithuania", 1]          # home country excluded
        assert eras[0]["reconstructed_pct"] == 100 and eras[1]["reconstructed_pct"] == 0

    def test_days_away_are_clipped_to_the_era(self):
        rows = self._rows("2012-08-01", "2012-09-30")
        trips = [_trip("2012-08-30", "2012-09-02", [STATION], ["Moldova", "Belarus"])]
        eras = era_comparison(trips, rows, "Minsk", [(_ts("2012-09-01"), "Chișinău")], self.CC)
        assert eras[0]["days_away"] == 2 and eras[1]["days_away"] == 2
        assert eras[0]["days"] == 31 and eras[1]["days"] == 30

    def test_short_era_has_no_yearly_rate(self):
        rows = self._rows("2026-08-01", "2026-09-01")
        eras = era_comparison([], rows, "Chișinău", [], self.CC)
        assert len(eras) == 1 and eras[0]["trips_per_year"] is None

    def test_era_outside_the_data_is_dropped(self):
        rows = self._rows("2020-01-01", "2020-12-31")
        history = [(_ts("2012-09-01"), "Chișinău"), (_ts("2099-01-01"), "Minsk")]
        eras = era_comparison([], rows, "Chișinău", history, self.CC)
        assert [e["city"] for e in eras] == ["Minsk"]


def test_trip_insights_empty_trips():
    assert trip_insights([], [], "Minsk", None, {}) == {
        "trip_eras": [], "trip_gateways": [], "trip_seasonality": {}}
