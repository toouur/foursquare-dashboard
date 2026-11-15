"""metrics.rank_moves: arrows vs WINDOW days ago, competition ranks, NEW."""
from metrics.rank_moves import _comp_ranks, rank_moves

DAY = 86400
NOW = 2_000_000_000


def _r(city, ts, vid="v", cat="Café"):
    return {"city": city, "country": "X", "date": str(ts), "venue_id": vid, "venue": vid, "category": cat}


def test_comp_ranks_ties_share_a_place():
    assert _comp_ranks({"a": 5, "b": 5, "c": 1}) == {"a": 1, "b": 1, "c": 3}


def test_climb_fall_new_and_no_move():
    old = NOW - 60 * DAY
    rows = [_r("A", old)] * 3 + [_r("B", old)] * 2 + [_r("B", NOW)] * 2 + [_r("C", NOW)] + [_r("D", old)]
    m = rank_moves(rows, {"cities": ["A", "B", "C", "D"]}, {}, {}, lambda r: r["category"])
    # before: A3 B2 D1 -> A#1 B#2 D#3 ; now: B4 A3 C1 D1 -> B#1 A#2 C#3 D#3
    assert m["cities"] == {"B": 1, "C": "n"}          # A slipped one place: not shown
    assert m["window"] == 30


def test_explorer_moves_by_group():
    old = NOW - 60 * DAY
    rows = [_r("A", old, "v1")] * 2 + [_r("A", old, "v2")] + [_r("A", NOW, "v2")] * 2
    m = rank_moves(rows, {}, {"Coffee": ["Café"]}, {"Coffee": ["v2", "v1"]}, lambda r: r["category"])
    assert m["explorer"] == {"Coffee": {"v2": 1}}


def test_big_fall_is_shown():
    old = NOW - 60 * DAY
    rows = [_r("A", old)] * 2 + [_r(c, NOW) for c in "BCDEF" for _ in range(3)]
    m = rank_moves(rows, {"cities": ["A"]}, {}, {}, lambda r: r["category"])
    assert m["cities"] == {"A": -5}
