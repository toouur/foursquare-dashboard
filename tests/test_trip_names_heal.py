# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""metrics.trip_names — orphaned trip_names keys are migrated, removed or flagged."""
from __future__ import annotations

from metrics.trip_names import MAX_SHIFT_S, TripSpan, heal_trip_names
from metrics.trips import detect_trips

H = 3600
DAY = 86400


def span(first: int, last: int, extra: tuple[int, ...] = (), prev_last: int | None = None) -> TripSpan:
    return TripSpan(key=first, first=first, last=last,
                    checkin_ts=frozenset({first, last, *extra}), prev_last=prev_last)


class TestHeal:
    def test_start_moved_earlier_migrates_the_name(self):
        # A platform at 05:00 now opens a trip that used to start at 08:30.
        s = span(1000 * H, 1013 * H, extra=(1003 * H + 1800,))
        res = heal_trip_names([s], {str(1003 * H + 1800): "Bender 🚂"})
        assert res.names == {str(1000 * H): "Bender 🚂"}
        assert [m[:2] for m in res.migrated] == [(str(1003 * H + 1800), str(1000 * H))]

    def test_start_moved_later_migrates_the_name(self):
        # The old departure row was dropped: the old start sits just before the trip.
        s = span(1000 * H, 1010 * H)
        res = heal_trip_names([s], {str(998 * H): "Trip"})
        assert res.names == {str(1000 * H): "Trip"}

    def test_stale_key_inside_a_named_trip_is_removed(self):
        s = span(1000 * H, 1100 * H, extra=(1050 * H,))
        res = heal_trip_names([s], {str(1000 * H): "Current", str(1050 * H): "Old"})
        assert res.names == {str(1000 * H): "Current"}
        assert res.removed[0][0] == str(1050 * H)

    def test_key_of_a_vanished_trip_is_removed(self):
        res = heal_trip_names([span(1000 * H, 1010 * H)], {str(5000 * H): "Gone"})
        assert res.names == {} and res.removed[0][2] == "no trip at this time any more"

    def test_only_the_explained_key_wins_the_others_are_superseded(self):
        # 28.08.2010: the real old start (a check-in) and a stale key that is not.
        s = span(1000 * H, 1010 * H, extra=(1003 * H,))
        res = heal_trip_names([s], {str(1003 * H): "Bender", str(1007 * H): "Tiraspol"})
        assert res.names == {str(1000 * H): "Bender"}
        assert res.removed[0][0] == str(1007 * H) and "superseded" in res.removed[0][2]

    def test_merged_named_trips_are_suspicious_and_kept(self):
        s = span(1000 * H, 1100 * H, extra=(1002 * H, 1004 * H))
        names = {str(1002 * H): "A", str(1004 * H): "B"}
        res = heal_trip_names([s], names)
        assert res.names == names and len(res.suspicious) == 2 and not res.changed

    def test_shift_over_a_day_is_suspicious_and_kept(self):
        s = span(1000 * H, 1000 * H + 3 * DAY, extra=(1000 * H + MAX_SHIFT_S + H,))
        names = {str(1000 * H + MAX_SHIFT_S + H): "Long"}
        res = heal_trip_names([s], names)
        assert res.names == names and "moved by" in res.suspicious[0][2]

    def test_key_in_the_previous_trips_tail_is_not_taken(self):
        a = span(1000 * H, 1010 * H)
        b = span(1020 * H, 1030 * H, prev_last=1010 * H)
        res = heal_trip_names([a, b], {str(1000 * H): "A", str(1005 * H): "stale A"})
        assert res.names == {str(1000 * H): "A"}

    def test_nothing_to_do(self):
        res = heal_trip_names([span(1000 * H, 1010 * H)], {str(1000 * H): "A"})
        assert res.names == {str(1000 * H): "A"} and not res.changed and not res.suspicious


def _row(ts, city="Minsk", category="Coffee Shop", vid="h" * 24):
    return {"date": str(ts), "city": city, "country": "Belarus" if city == "Minsk" else "Poland",
            "category": category, "venue": category, "venue_id": vid,
            "lat": "53.9", "lng": "27.56", "checkin_id": ""}


def test_detect_trips_renders_the_healed_name():
    """End to end: a departure hub added in front of a named trip keeps its name."""
    t0 = 1685620800
    rows = [_row(t0)]
    rows.append(_row(t0 + 2 * H, category="Rail Station", vid="s" * 24))
    rows += [_row(t0 + 4 * H + i * H, city="Warsaw", vid="w" * 24) for i in range(5)]
    rows.append(_row(t0 + 2 * DAY))
    old_key = str(t0 + 4 * H)            # the start before the station counted
    report: dict = {}
    trips = detect_trips(rows, min_checkins=5, trip_names={old_key: "Warsaw weekend"},
                         name_report=report)
    assert trips[0]["name"] == "Warsaw weekend"
    assert str(trips[0]["_name_ts"]) in report["heal"].names
    assert old_key not in report["heal"].names


def test_mass_removal_aborts_the_heal():
    """A heal that would drop dozens of keys is looking at the wrong data."""
    from metrics.trip_names import MAX_REMOVALS
    names = {str(5000 * H + i): f"T{i}" for i in range(MAX_REMOVALS + 1)}
    res = heal_trip_names([span(1000 * H, 1010 * H)], names)
    assert res.names == names and not res.changed
    assert len(res.suspicious) == MAX_REMOVALS + 1


def test_integration_build_never_writes_the_real_config():
    import pathlib
    src = (pathlib.Path(__file__).parent / "test_build_integration.py").read_text(encoding="utf-8")
    assert '"--config-dir", str(CONFIG_DIR)' not in src
