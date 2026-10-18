"""scripts/telegram_on_this_day.py — the daily "on this day" Telegram story.

Offline: weather, Claude and Telegram are exercised through fake sessions.
"""
import json
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


def test_home_category_keeps_its_real_name():
    r = minsk(ts(2018, 10, 18), venue="ул. Секретная, 5", category=otd.HOME_CATEGORY)
    assert otd.place_name(r) == "ул. Секретная, 5"
    assert otd.place_name(minsk(0, venue="")) == "somewhere"


def test_flat_while_travelling_shows_its_name():
    rows = [make_row(ts(2023, 10, 18, 8), city="Tbilisi", country="Georgia", venue="ул. Тайная, 1",
                     category=otd.HOME_CATEGORY, lat="41.7", lng="44.8")]
    (f,) = facts_for(rows)
    assert f["stops"][0]["name"] == "ул. Тайная, 1"
    assert "Тайная" in "".join(otd.build_messages([f], "s", TODAY))


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


def test_summary_lists_every_stop_with_its_own_companions():
    rows = [
        minsk(ts(2018, 10, 18, 9), venue="Cafe", with_name="Tata M"),
        minsk(ts(2018, 10, 18, 10), venue="Cafe"),
        minsk(ts(2018, 10, 18, 11), venue="Office", shout="[coords_approx] x"),
        minsk(ts(2018, 10, 18, 12), venue="Park", shout="lovely [geo_pinned]"),
    ]
    (f,) = facts_for(rows)
    assert [s["name"] for s in f["stops"]] == ["Cafe", "Cafe", "Office", "Park"]
    assert [s["time"] for s in f["stops"]] == ["12:00", "13:00", "14:00", "15:00"]  # Minsk UTC+3
    assert [s["companions"] for s in f["stops"]] == [["Tata M"], [], [], []]
    assert [s["shout"] for s in f["stops"]] == ["", "", "", "lovely"]
    assert f["companions"] == ["Tata M"]
    assert f["shouts"] == ["lovely"]
    assert f["checkins"] == 4 and not f["reconstructed"]


def test_companion_sits_on_its_own_checkin_line():
    rows = [minsk(ts(2018, 10, 18, 9), venue="Cafe"),
            minsk(ts(2018, 10, 18, 10), venue="Bar", with_name="Tata M")]
    (f,) = facts_for(rows)
    lines = otd.year_lines(f)
    assert lines[0].startswith("🏠 <b>2018</b>")
    assert lines[1] == "12:00 📍 Cafe"
    assert lines[2] == "13:00 📍 Bar — with Tata M"


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


def test_messages_split_under_telegram_limit_and_keep_every_checkin():
    rows = [minsk(ts(y, 10, 18, h), venue=f"Venue {y}-{h} " + "x" * 80)
            for y in range(1990, 2026) for h in range(8, 20)]
    msgs = otd.build_messages(facts_for(rows), "story " * 50, TODAY)
    assert len(msgs) > 1 and all(len(m) <= otd.TG_LIMIT for m in msgs)
    text = "\n".join(msgs)
    assert text.count("Venue ") == len(rows)               # nothing dropped
    assert text.index("<b>2025</b>") < text.index("<b>1990</b>")  # newest first
    assert msgs[0].startswith("📅 <b>On this day — 18 October</b>")


def test_oversized_year_flows_on_with_cont_head():
    rows = [minsk(ts(2018, 10, 18, 0, m), venue=f"V{m} " + "y" * 120) for m in range(0, 59)]
    msgs = otd.build_messages(facts_for(rows), "s", TODAY)
    assert len(msgs) >= 2 and all(len(m) <= otd.TG_LIMIT for m in msgs)
    assert "<b>2018</b>" in msgs[1] and "(cont.)" in msgs[1]


def test_message_escapes_html():
    (f,) = facts_for([minsk(ts(2018, 10, 18), venue="Bar <Tom & Jerry>", shout="1 < 2")])
    msg = "".join(otd.build_messages([f], "a < b", TODAY))
    assert "Bar &lt;Tom &amp; Jerry&gt;" in msg and "a &lt; b" in msg and "1 &lt; 2" in msg


def test_photo_albums_one_per_year_newest_first_capped():
    facts = [{"year": 2018, "main_city": "Minsk", "photos": ["a1", "a2"]},
             {"year": 2020, "main_city": "", "photos": []},
             {"year": 2023, "main_city": "Tbilisi", "photos": [f"c{i}" for i in range(25)]}]
    albums = otd.photo_albums(facts, "https://pix")
    assert [cap for cap, _ in albums] == ["2023 · Tbilisi", "2018 · Minsk"]
    assert len(albums[0][1]) == otd.ALBUM_MAX and albums[0][1][0] == "https://pix/c0"
    assert albums[1][1] == ["https://pix/a1", "https://pix/a2"]
    assert otd.photo_albums(facts, "") == []


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


def test_claude_cli_story_and_fallback(monkeypatch):
    import subprocess
    from types import SimpleNamespace
    facts = facts_for([minsk(ts(2018, 10, 18))])
    seen = {}

    def ok(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return SimpleNamespace(returncode=0, stdout="  A calm Minsk day.\n", stderr="")

    monkeypatch.setattr(otd.shutil, "which", lambda name: "/usr/bin/claude")
    assert otd.claude_cli_story(facts, TODAY, "English", "sonnet", run=ok) == "A calm Minsk day."
    assert seen["cmd"][:2] == ["/usr/bin/claude", "-p"] and seen["cmd"][-2:] == ["--model", "sonnet"]
    assert "Minsk" in seen["input"]                        # prompt goes over stdin

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 180)
    assert otd.claude_cli_story(facts, TODAY, "English", run=boom) is None
    failed = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="auth")  # noqa: E731
    assert otd.claude_cli_story(facts, TODAY, "English", run=failed) is None
    monkeypatch.setattr(otd.shutil, "which", lambda name: None)
    assert otd.claude_cli_story(facts, TODAY, "English", run=ok) is None


def test_tg_send_photo_failure_is_not_fatal():
    s = FakeSession(FakeResp({}), FakeResp({}), FakeResp(status=400),
                    requests.ConnectionError("down"))
    albums = [("2023 · Tbilisi", ["u1", "u2"]), ("2018 · Minsk", ["u3"])]
    assert otd.tg_send("t", "c", ["hi", "more"], albums, session=s) is True
    assert s.calls[2][0].endswith("/sendMediaGroup")
    media = json.loads(s.calls[2][1]["data"]["media"])
    assert [m["media"] for m in media] == ["u1", "u2"] and media[0]["caption"] == "2023 · Tbilisi"
    assert s.calls[3][0].endswith("/sendPhoto")
    assert otd.tg_send("t", "c", ["hi"], [], session=FakeSession(FakeResp(status=401))) is False


# ── Story store ─────────────────────────────────────────────────────────────

def test_story_store_round_trip_keeps_every_date(tmp_path):
    p = tmp_path / "otd_stories.json"
    assert otd.load_stories(p) == {}
    stories = otd.load_stories(p)
    otd.save_story(p, stories, date(2026, 10, 18), "Minsk day", "claude-cli",
                   now=datetime(2026, 10, 18, 6, tzinfo=timezone.utc))
    otd.save_story(p, otd.load_stories(p), date(2025, 1, 2), "Old day", "claude-api")
    back = otd.load_stories(p)
    assert list(back) == ["2025-01-02", "2026-10-18"]          # sorted, nothing pruned
    assert back["2026-10-18"] == {"story": "Minsk day", "source": "claude-cli",
                                  "created": "2026-10-18T06:00:00Z"}
    assert otd.stored_story(back, date(2026, 10, 18)) == "Minsk day"
    assert otd.stored_story(back, date(2026, 10, 19)) is None


def test_story_store_tolerates_junk(tmp_path):
    p = tmp_path / "otd_stories.json"
    p.write_text("not json", encoding="utf-8")
    assert otd.load_stories(p) == {}
    assert otd.stored_story({"2026-10-18": {"story": "  "}}, TODAY) is None
    assert otd.stored_story({"2026-10-18": "bare"}, TODAY) is None


def _run_main(monkeypatch, tmp_path, *extra, sent=True):
    csv_path = tmp_path / "checkins.csv"
    csv_path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(otd, "load_rows", lambda path, cfg: [minsk(ts(2018, 10, 18))])
    monkeypatch.setattr(otd, "write_story", lambda facts, today, lang: ("Fresh story", "claude-cli"))
    posted = []
    monkeypatch.setattr(otd, "tg_send", lambda t, c, msgs, albums: posted.append(msgs) or sent)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(sys, "argv", ["otd", "--csv", str(csv_path), "--date", "2026-10-18",
                                      "--no-weather", "--stories", str(tmp_path / "s.json"), *extra])
    rc = otd.main()
    return rc, "".join(posted[0]) if posted else ""


def test_main_reuses_stored_story_and_regenerate_replaces_it(monkeypatch, tmp_path):
    rc, text = _run_main(monkeypatch, tmp_path)
    assert rc == 0 and "Fresh story" in text
    stories = otd.load_stories(tmp_path / "s.json")
    stories["2026-10-18"]["story"] = "My edited draft"           # a hand edit
    (tmp_path / "s.json").write_text(json.dumps(stories), encoding="utf-8")
    _, text = _run_main(monkeypatch, tmp_path)
    assert "My edited draft" in text and "Fresh story" not in text
    _, text = _run_main(monkeypatch, tmp_path, "--regenerate")
    assert "Fresh story" in text
    assert otd.stored_story(otd.load_stories(tmp_path / "s.json"), TODAY) == "Fresh story"


def test_main_does_not_store_template_or_failed_send(monkeypatch, tmp_path):
    _run_main(monkeypatch, tmp_path, sent=False)
    assert otd.load_stories(tmp_path / "s.json") == {}
    monkeypatch.setattr(otd, "write_story", lambda *a: ("Template", "template"))
    csv_path = tmp_path / "checkins.csv"
    csv_path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(otd, "load_rows", lambda path, cfg: [minsk(ts(2018, 10, 18))])
    monkeypatch.setattr(otd, "tg_send", lambda *a: True)
    monkeypatch.setattr(sys, "argv", ["otd", "--csv", str(csv_path), "--date", "2026-10-18",
                                      "--no-weather", "--stories", str(tmp_path / "s.json")])
    assert otd.main() == 0
    assert otd.load_stories(tmp_path / "s.json") == {}
