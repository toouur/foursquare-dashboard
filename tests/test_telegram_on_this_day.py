"""scripts/telegram_on_this_day.py — the daily "on this day" Telegram story.

Offline: weather, Claude and Telegram are exercised through fake sessions.
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import telegram_on_this_day as otd  # noqa: E402
from conftest import make_row  # noqa: E402

TODAY = date(2026, 10, 18)
# Minsk until 29.07.2026, Chișinău before 01.09.2012 and again after.
HOME_CITY = "Chișinău"
HISTORY = [(1346457600, "Chișinău"), (1785283200, "Minsk")]


def ts(y, mo, d, h=12, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


def minsk(t, venue="Cafe", **kw):
    return make_row(t, city="Minsk", country="Belarus", venue=venue,
                    lat="53.9", lng="27.56", **kw)


def facts_for(rows):
    by_year = otd.rows_on_this_day(rows, TODAY)
    return [otd.summarize_year(y, items, TODAY, HOME_CITY, HISTORY)
            for y, items in by_year.items()]


class FakeResp:
    def __init__(self, payload=None, status=200, exc=None):
        self.payload, self.status_code, self.exc = payload, status, exc
        self.ok = status < 400
        self.text = ""

    def raise_for_status(self):
        if self.exc:
            raise self.exc
        if not self.ok:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, url, kw):
        self.calls.append((url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def get(self, url, **kw):
        return self._next(url, kw)

    def post(self, url, **kw):
        return self._next(url, kw)


# ── Day matching ────────────────────────────────────────────────────────────

def test_matches_local_date_and_skips_current_year():
    rows = [
        minsk(ts(2018, 10, 18)),
        minsk(ts(2026, 10, 18)),                 # this year — not "a year ago"
        minsk(ts(2019, 10, 17, 12)),             # wrong day
        # 22:30 UTC on the 17th is 01:30 on the 18th in Minsk (UTC+3).
        minsk(ts(2020, 10, 17, 22, 30)),
    ]
    assert list(otd.rows_on_this_day(rows, TODAY)) == [2018, 2020]


def test_home_category_reads_home():
    r = minsk(ts(2018, 10, 18), venue="ул. Секретная, 5", category=otd.HOME_CATEGORY)
    assert otd.place_name(r) == "home"
    assert otd.place_name(minsk(0, venue="")) == "somewhere"


def test_at_home_is_era_aware():
    rows = [
        minsk(ts(2018, 10, 18)),
        make_row(ts(2009, 10, 18), city="Chișinău", country="Moldova",
                 venue="Liceu", lat="47.0", lng="28.85"),
        make_row(ts(2023, 10, 18), city="Tbilisi", country="Georgia",
                 venue="Metro", lat="41.7", lng="44.8"),
    ]
    by = {f["year"]: f for f in facts_for(rows)}
    assert by[2018]["at_home"] and by[2018]["home_city"] == "Minsk"
    assert by[2009]["at_home"] and by[2009]["home_city"] == "Chișinău"
    assert not by[2023]["at_home"]


def test_summary_dedupes_places_and_collects_companions():
    rows = [
        minsk(ts(2018, 10, 18, 9), venue="Cafe", with_name="Tata M"),
        minsk(ts(2018, 10, 18, 10), venue="Cafe"),
        minsk(ts(2018, 10, 18, 11), venue="Office", shout="[coords_approx] x"),
        minsk(ts(2018, 10, 18, 12), venue="Park", shout="lovely"),
    ]
    (f,) = facts_for(rows)
    assert [p["name"] for p in f["places"]] == ["Cafe", "Office", "Park"]
    assert f["companions"] == ["Tata M"]
    assert f["shouts"] == ["lovely"]
    assert f["checkins"] == 4 and not f["reconstructed"]


# ── Message ─────────────────────────────────────────────────────────────────

def test_template_story_groups_quiet_home_years():
    rows = [minsk(ts(y, 10, 18)) for y in (2017, 2019)] + [
        make_row(ts(2023, 10, 18), city="Tbilisi", country="Georgia",
                 venue="Metro", lat="41.7", lng="44.8"),
    ]
    story = otd.template_story(facts_for(rows))
    lines = story.splitlines()
    assert lines[0].startswith("3 years ago you were in Tbilisi, Georgia")
    assert lines[-1] == "Quiet home days in Minsk: 2019, 2017."


def test_message_stays_under_telegram_limit():
    rows = [minsk(ts(y, 10, 18, h), venue=f"Venue {y}-{h} " + "x" * 80)
            for y in range(1990, 2026) for h in range(8, 20)]
    facts = facts_for(rows)
    msg = otd.build_message(facts, "story " * 50, TODAY)
    assert len(msg) <= otd.TG_LIMIT
    assert "<b>2025</b>" in msg            # newest years survive, oldest are dropped
    assert "<b>1990</b>" not in msg


def test_message_escapes_html():
    (f,) = facts_for([minsk(ts(2018, 10, 18), venue="Bar <Tom & Jerry>")])
    msg = otd.build_message([f], "a < b", TODAY)
    assert "Bar &lt;Tom &amp; Jerry&gt;" in msg and "a &lt; b" in msg


def test_pick_photos_round_robin_newest_first():
    facts = [{"photos": ["a1", "a2", "a3"]}, {"photos": []}, {"photos": ["c1", "c2"]}]
    assert otd.pick_photos(facts, limit=4) == ["c1", "a1", "c2", "a2"]
    assert otd.pick_photos([{"photos": []}]) == []


# ── External calls fail soft ────────────────────────────────────────────────

def test_weather_parses_and_fails_soft():
    ok = FakeSession(FakeResp({"daily": {"temperature_2m_max": [9.4], "temperature_2m_min": [5.6],
                                         "precipitation_sum": [3.2], "weather_code": [61]}}))
    w = otd.fetch_weather(53.9, 27.56, date(2025, 10, 18), session=ok)
    assert w is not None and w["tmax"] == 9 and w["tmin"] == 6
    assert otd.weather_text(w).startswith(w["icon"])
    assert otd.fetch_weather(53.9, 27.56, date(2025, 10, 18),
                             session=FakeSession(requests.ConnectionError("down"))) is None
    assert otd.fetch_weather(None, None, date(2025, 10, 18)) is None


def test_claude_story_and_fallback():
    facts = facts_for([minsk(ts(2018, 10, 18))])
    ok = FakeSession(FakeResp({"content": [{"type": "text", "text": "  A calm Minsk day.  "}]}))
    assert otd.claude_story(facts, TODAY, "English", "k", session=ok) == "A calm Minsk day."
    assert ok.calls[0][1]["headers"]["x-api-key"] == "k"
    bad = FakeSession(FakeResp(status=529))
    assert otd.claude_story(facts, TODAY, "English", "k", session=bad) is None


def test_tg_send_photo_failure_is_not_fatal():
    s = FakeSession(FakeResp({}), FakeResp(status=400))
    assert otd.tg_send("t", "c", "hi", ["u1", "u2"], session=s) is True
    assert s.calls[1][0].endswith("/sendMediaGroup")
    assert otd.tg_send("t", "c", "hi", [], session=FakeSession(FakeResp(status=401))) is False
