# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the shouts pipeline: shout_records filtering,
merge_comments_into_shouts enrichment, language/emoji helpers.
"""
from conftest import make_row
from metrics import (
    _detect_lang,
    _extract_emojis,
    merge_comments_into_shouts,
    shout_records,
)


def shout_row(ts, text, **kw):
    kw.setdefault("city", "Minsk")
    kw.setdefault("country", "Belarus")
    return make_row(ts, shout=text, **kw)


class TestShoutRecords:
    def test_plain_shout_kept(self):
        recs = shout_records([shout_row(100, "Great coffee here")])
        assert len(recs) == 1
        assert recs[0]["text"] == "Great coffee here"

    def test_with_suffix_stripped(self):
        recs = shout_records([shout_row(100, "Great coffee — with Darya")])
        assert recs[0]["text"] == "Great coffee"

    def test_with_only_shout_dropped(self):
        assert shout_records([shout_row(100, "with Darya")]) == []
        assert shout_records([shout_row(101, "— with Darya")]) == []

    def test_bare_companion_name_dropped(self):
        # "Joanna" is a known companion (from with_name of another row) →
        # a shout that is ONLY that name is attribution leakage, not content.
        rows = [
            shout_row(100, "Joanna"),
            make_row(50, with_name="Joanna Smith"),
        ]
        assert shout_records(rows) == []

    def test_same_word_kept_when_not_a_companion(self):
        recs = shout_records([shout_row(100, "Joanna")])  # nobody named Joanna
        assert len(recs) == 1

    def test_pure_punctuation_dropped(self):
        assert shout_records([shout_row(100, "!!!")]) == []
        assert shout_records([shout_row(101, "...")]) == []

    def test_empty_and_blank_dropped(self):
        rows = [shout_row(100, ""), shout_row(101, "   ")]
        assert shout_records(rows) == []

    def test_sorted_newest_first(self):
        rows = [shout_row(100, "older"), shout_row(200, "newer")]
        recs = shout_records(rows)
        assert [r["text"] for r in recs] == ["newer", "older"]

    def test_companions_attached(self):
        recs = shout_records([shout_row(100, "Nice place", with_name="Darya")])
        assert recs[0]["companions"] == ["Darya"]

    def test_bad_ts_dropped(self):
        recs = shout_records([shout_row("not-a-ts", "Nice place")])
        assert recs == []


class TestMergeCommentsIntoShouts:
    def test_thread_attached_to_shout(self):
        shouts = [{"ts": 100, "text": "hi", "checkin_id": "c1"}]
        cm = {"c1": {"items": [{"text": "reply", "author": "Max"}]}}
        out = merge_comments_into_shouts(shouts, [], cm)
        assert out[0]["comments"] == [{"text": "reply", "author": "Max"}]

    def test_shout_without_thread_gets_empty_list(self):
        shouts = [{"ts": 100, "text": "hi", "checkin_id": "c1"}]
        out = merge_comments_into_shouts(shouts, [], {})
        assert out[0]["comments"] == []

    def test_originals_not_mutated(self):
        shouts = [{"ts": 100, "text": "hi", "checkin_id": "c1"}]
        merge_comments_into_shouts(shouts, [], {"c1": {"items": [{"text": "r"}]}})
        assert "comments" not in shouts[0]

    def test_comment_only_checkin_added_from_row(self):
        row = make_row(200, city="Minsk", country="Belarus",
                       venue="Cafe", checkin_id="c2", with_name="Darya")
        cm = {"c2": {"items": [{"text": "reply"}]}}
        out = merge_comments_into_shouts([], [row], cm)
        assert len(out) == 1
        e = out[0]
        assert e["text"] == "" and e["venue"] == "Cafe"
        assert e["companions"] == ["Darya"]

    def test_comment_only_checkin_falls_back_to_meta(self):
        cm = {"c3": {"ts": 300, "venue": "Ghost Venue",
                     "items": [{"text": "reply"}]}}
        out = merge_comments_into_shouts([], [], cm)
        assert out[0]["venue"] == "Ghost Venue"
        assert out[0]["ts"] == 300

    def test_empty_thread_not_added(self):
        out = merge_comments_into_shouts([], [], {"c4": {"items": []}})
        assert out == []

    def test_result_sorted_newest_first(self):
        shouts = [{"ts": 100, "text": "old", "checkin_id": "a"}]
        cm = {"b": {"ts": 500, "venue": "V", "items": [{"text": "r"}]}}
        out = merge_comments_into_shouts(shouts, [], cm)
        assert [e["ts"] for e in out] == [500, 100]


class TestLangAndEmoji:
    def test_cyrillic(self):
        assert _detect_lang("Отличное место") == "cyr"

    def test_latin(self):
        assert _detect_lang("great place") == "lat"

    def test_mixed(self):
        assert _detect_lang("кафе cafe") == "mix"

    def test_other(self):
        assert _detect_lang("123 !!!") == "other"

    def test_belarusian_letters_count_as_cyrillic(self):
        assert _detect_lang("Ўў Іі Ёё") == "cyr"

    def test_extract_emojis(self):
        assert _extract_emojis("nice ☕ place 🎉") == ["☕", "🎉"]

    def test_no_emojis(self):
        assert _extract_emojis("plain text") == []
