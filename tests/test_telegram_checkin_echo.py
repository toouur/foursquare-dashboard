# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""telegram_checkin_echo — which check-ins are news, and the watermark that never replays."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import telegram_checkin_echo as echo
from conftest import make_row

DAY = 86400
YEAR = 365 * DAY


def _ts(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def _row(ts, vid, venue="Cafe", city="Chișinău", country="Moldova", category="Café", cid="", **kw):
    kw.setdefault("source_app", "Swarm")
    return make_row(ts, city=city, country=country, category=category, venue=venue, venue_id=vid,
                    lat="47.02", lng="28.83", checkin_id=cid or f"c{ts}", **kw)


def _town(t0, years):
    """Ordinary life in the same city in between, so only the VENUE is a comeback."""
    return [_row(t0 + y * YEAR - 30 * DAY, f"life{y}") for y in range(1, years + 1)]


def _kinds(rows):
    h = echo.History(rows)
    return echo.events(echo.facts_for(h, len(rows) - 1))


def test_new_city_and_new_country_are_instant():
    rows = [_row(_ts("2026-01-01"), "a"), _row(_ts("2026-02-01"), "b", city="Iași", country="Romania")]
    kinds = _kinds(rows)
    assert kinds[:2] == ["new_country", "new_city"] and echo.is_instant(kinds)


def test_comeback_after_five_years_is_instant_after_one_is_digest():
    t0 = _ts("2015-06-01")
    assert "comeback_long" in _kinds([_row(t0, "a"), *_town(t0, 6), _row(t0 + 6 * YEAR, "a")])
    kinds = _kinds([_row(t0, "a"), *_town(t0, 2), _row(t0 + 2 * YEAR, "a")])
    assert "comeback" in kinds and not echo.is_instant(kinds)


def test_city_back_after_a_year_away():
    t0 = _ts("2020-01-01")
    rows = [_row(t0, "a", city="Bukhara", country="Uzbekistan"),
            _row(t0 + DAY, "z", city="Minsk", country="Belarus"),
            _row(t0 + 4 * YEAR, "b", city="Bukhara", country="Uzbekistan")]
    assert _kinds(rows)[0] == "city_back"


def test_milestones():
    t0 = _ts("2024-01-01")
    rows = [_row(t0 + i * DAY, "a") for i in range(50)]
    assert "milestone_big" in _kinds(rows)          # 50th visit
    assert "milestone" in _kinds(rows[:10])          # 10th visit, digest only


def test_a_street_never_pings_on_its_own():
    t0 = _ts("2012-01-01")
    kinds = _kinds([_row(t0, "s", category="Road"), *_town(t0, 7),
                    _row(t0 + 7 * YEAR, "s", category="Road")])
    assert kinds == ["comeback"] and not echo.is_instant(kinds)


def test_home_and_reconstructed_rows_raise_nothing():
    t0 = _ts("2026-01-01")
    h = echo.History([_row(t0, "h", category="Home (private)"),
                      _row(t0 + 1, "x", source_app="refurbished")])
    assert not h.is_real(0) and not h.is_real(1)


def test_companions_counted_at_this_venue():
    t0 = _ts("2025-01-01")
    rows = [_row(t0, "a", with_name="Darya"), _row(t0 + DAY, "a", with_name="Darya")]
    f = echo.facts_for(echo.History(rows), 1)
    assert f["with_here_before"] == {"Darya": 1} and f["visit_no"] == 2


def test_instant_skips_old_and_caps_but_moves_the_watermark():
    now = _ts("2026-10-19T12:00:00")
    rows = [_row(now - 3 * DAY, "o", city="Old"),                       # too old: digest's job
            *[_row(now - 3600 * (9 - i), f"v{i}", city=f"City{i}") for i in range(5)]]
    todo, mark = echo.plan_instant(echo.History(rows), {"last_ts": 0}, now)
    assert len(todo) == echo.MAX_INSTANT
    assert mark == max(int(r["date"]) for r in rows)


def test_first_run_only_records_the_watermark(tmp_path, monkeypatch):
    csv_path = tmp_path / "checkins.csv"
    csv_path.write_text("date\n", encoding="utf-8")
    state = tmp_path / "state.json"
    rows = [_row(_ts("2026-10-18"), "a")]
    monkeypatch.setattr(echo, "load_rows", lambda *a: rows)
    monkeypatch.setattr("sys.argv", ["x", "--mode", "instant", "--csv", str(csv_path),
                                     "--state", str(state)])
    assert echo.main() == 0
    assert json.loads(state.read_text())["last_ts"] == int(rows[0]["date"])


def test_digest_ranks_big_news_first_and_summarises_the_tail():
    t0 = _ts("2026-10-11T08:00:00")
    hist = [_row(_ts("2013-07-03"), "col", venue="Columna"), _row(_ts("2026-10-10"), "x")]
    day = [_row(t0 + i * 60, f"n{i}", venue=f"Street {i}", category="Road") for i in range(20)]
    day.append(_row(t0 + 3600, "col", venue="Columna"))
    h = echo.History(hist + day)
    todo, totals = echo.plan_digest(h, date(2026, 10, 11))
    msg = echo.digest_message(date(2026, 10, 11), [(f, k, False) for _, f, k in todo], totals,
                              {}, use_llm=False)
    lines = msg.splitlines()
    assert "Columna" in lines[2] and "13 years" in lines[2]
    assert lines[-1] == "+ 6 new places" and totals["checkins"] == 21


def test_llm_falls_back_to_template(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    rows = [_row(_ts("2026-01-01"), "a"), _row(_ts("2026-02-01"), "b", venue="Bar <1>", city="Iași")]
    h = echo.History(rows)
    f = echo.facts_for(h, 1)
    msg = echo.instant_message(f, echo.events(f), {})
    assert msg.startswith("📍 <b>Bar &lt;1&gt;</b> · Iași\nFirst time ever in Iași")
