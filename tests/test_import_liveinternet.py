# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for import_liveinternet.py — the LiveInternet diary crawler →
location-predicted ``backfill_liveinternet.yaml`` review-file adapter.

Covers:
  - Russian genitive-month date parsing (both header-span class variants)
  - CONBL container parsing (id, title, body, music, rubrics, tags, date)
  - location prediction (explicit gazetteer → high, home-context cue → medium,
    bare post → home base + low)
  - the ``--before`` year window (2012+ posts excluded as Foursquare-era)
  - dedup by post id across pages
  - ``write_yaml`` header + overwrite-refusal + ``--append`` dedup by ``li_id``
"""
import re

import yaml

import import_liveinternet as il


# ── a minimal but realistic CONBL container ─────────────────────────────────
def _container(pid, date_span, *, title="Post title", body="prose", music="",
               rubrics="Будничное", tag="утро",
               span_class="GL_TXTSM GL_MAR5B"):
    """Build one diary post container close enough to the real HTML that the
    module regexes bite. ``date_span`` is the raw RU date text."""
    music_html = (f"<nobr>В колонках играет - <em><a href='#'>{music}</a>"
                  f"</em></nobr><br>" if music else "")
    return f'''<div class="CONBL blo-fastcom_base" id="post{pid}" style="x">
  <h1 class="ZAG"><a href="/users/toouur/post{pid}/" class="TTL"><b>{title}</b></a></h1>
  <span class="{span_class}">
     {date_span}
     <a class="GL_LNXMAR4" href="#">+ в цитатник</a>
  </span>
  <div class="GL_MAR10T GL_MAR10B MESS">
    {music_html}{body}<br/>
    <div class="B"></div>
  </div>
  <table><tr class="GL_TXTSM"><td>
    <b>Рубрики:&nbsp;</b></td><td><a href="/rubric/1/">{rubrics}</a><br/>
    </td></tr></table>
    <b>Метки:</b> <span class="DI_TAG_MNU">
    <a rel="tag" href="/tags/x/">{tag}</a></span>
</div>'''


# ── date parsing ────────────────────────────────────────────────────────────
def test_parse_ru_date_genitive_months():
    assert il.parse_ru_date("Суббота, 25 Октября 2008 г. 23:09") == "2008-10-25"
    assert il.parse_ru_date("Понедельник, 1 Января 2009 г. 00:05") == "2009-01-01"
    assert il.parse_ru_date("Среда, 9 Мая 2012 г. 10:00") == "2012-05-09"
    assert il.parse_ru_date("7 Июля 2010 г.") == "2010-07-07"


def test_parse_ru_date_rejects_garbage():
    assert il.parse_ru_date("no date here") is None
    assert il.parse_ru_date("") is None
    assert il.parse_ru_date("25 Fakemonth 2008 г.") is None


def test_date_parsed_from_photo_span_variant():
    """Photo/audio posts carry the date in a GL_MAR10B span, not GL_MAR5B."""
    html = _container("111", "Пятница, 3 Апреля 2009 г. 18:00",
                      span_class="GL_TXTSM GL_MAR10B")
    posts = il.parse_containers(html)
    assert posts[0]["date"] == "2009-04-03"


# ── container parsing ───────────────────────────────────────────────────────
def test_parse_containers_extracts_fields():
    html = _container("88214552", "Суббота, 25 Октября 2008 г. 23:09",
                      title="А суббота блин", body="хороший день",
                      music="Alternosfera - Drumuri de noroi", tag="утро")
    posts = il.parse_containers(html)
    assert len(posts) == 1
    p = posts[0]
    assert p["id"] == "88214552"
    assert p["date"] == "2008-10-25"
    assert p["title"] == "А суббота блин"
    assert "хороший день" in p["body"]
    assert "Alternosfera" in p["music"]
    assert "хороший день" in p["body"] and "В колонках" not in p["body"]
    assert "Будничное" in p["rubrics"]
    assert "утро" in p["tags"]
    assert p["url"] == "https://www.liveinternet.ru/users/{user}/post88214552/"


def test_parse_containers_multiple_posts():
    html = (_container("1", "1 Января 2009 г.") +
            _container("2", "2 Января 2009 г."))
    posts = il.parse_containers(html)
    assert [p["id"] for p in posts] == ["1", "2"]


# ── location prediction ─────────────────────────────────────────────────────
def _gaz():
    return il.compile_gazetteer(None)


def _home_ctx():
    return [re.compile(p, re.IGNORECASE) for p in il._HOME_CONTEXT_CUES]


def test_infer_explicit_place_is_high():
    post = {"title": "Поехали в Минск", "body": "", "tags": [], "music": ""}
    city, country, conf, cue = il.infer_location(
        post, _gaz(), "Chișinău", "Moldova", _home_ctx())
    assert (city, country, conf) == ("Minsk", "Belarus", "high")
    assert "минск" in cue.lower()


def test_infer_home_context_cue_is_medium():
    post = {"title": "", "body": "", "tags": [],
            "music": "Alternosfera - Drumuri de noroi"}
    city, country, conf, cue = il.infer_location(
        post, _gaz(), "Chișinău", "Moldova", _home_ctx())
    assert (city, country, conf) == ("Chișinău", "Moldova", "medium")
    assert "alternosfera" in cue.lower()


def test_infer_bare_post_is_home_low():
    post = {"title": "просто мысли", "body": "ничего особенного",
            "tags": [], "music": ""}
    city, country, conf, cue = il.infer_location(
        post, _gaz(), "Chișinău", "Moldova", _home_ctx())
    assert (city, country, conf) == ("Chișinău", "Moldova", "low")
    assert "home base" in cue.lower()


def test_gazetteer_extra_rows_take_precedence():
    extra = [(r"мойгород", "Custom City", "Neverland")]
    gaz = il.compile_gazetteer(extra)
    post = {"title": "мойгород forever", "body": "", "tags": [], "music": ""}
    city, country, conf, _cue = il.infer_location(
        post, gaz, "Chișinău", "Moldova", _home_ctx())
    assert (city, country, conf) == ("Custom City", "Neverland", "high")


# ── build_entries ───────────────────────────────────────────────────────────
def test_build_entries_before_window_excludes_2012():
    pages = [_container("1", "5 Июня 2011 г."),
             _container("2", "1 Января 2012 г.")]
    entries, total, skipped = il.build_entries(
        pages, "toouur", _gaz(), "Chișinău", "Moldova",
        before_year=2012, excerpt_len=240)
    assert total == 2 and skipped == 0
    assert [e["date"] for e in entries] == ["2011-06-05"]


def test_build_entries_all_years_when_no_window():
    pages = [_container("1", "5 Июня 2011 г."),
             _container("2", "1 Января 2013 г.")]
    entries, _total, _skipped = il.build_entries(
        pages, "toouur", _gaz(), "Chișinău", "Moldova",
        before_year=None, excerpt_len=240)
    assert len(entries) == 2


def test_build_entries_dedups_by_post_id_across_pages():
    dup = _container("42", "5 Июня 2011 г.")
    entries, total, _skipped = il.build_entries(
        [dup, dup], "toouur", _gaz(), "Chișinău", "Moldova",
        before_year=2012, excerpt_len=240)
    assert total == 2 and len(entries) == 1
    assert entries[0]["li_id"] == "42"


def test_build_entries_skips_undated_posts():
    dated = _container("1", "5 Июня 2011 г.")
    undated = _container("2", "no parseable date at all")
    entries, total, skipped = il.build_entries(
        [dated + undated], "toouur", _gaz(), "Chișinău", "Moldova",
        before_year=2012, excerpt_len=240)
    assert total == 2 and skipped == 1 and len(entries) == 1


def test_build_entries_fills_url_user_placeholder():
    entries, _total, _skipped = il.build_entries(
        [_container("7", "5 Июня 2011 г.")], "toouur", _gaz(),
        "Chișinău", "Moldova", before_year=2012, excerpt_len=240)
    assert entries[0]["url"] == "https://www.liveinternet.ru/users/toouur/post7/"


def test_build_entries_excerpt_truncates_with_ellipsis():
    long_body = "a" * 500
    html = _container("9", "5 Июня 2011 г.", body=long_body)
    entries, _t, _s = il.build_entries(
        [html], "toouur", _gaz(), "Chișinău", "Moldova",
        before_year=2012, excerpt_len=100)
    assert entries[0]["body"].endswith("…")
    assert len(entries[0]["body"]) == 101  # 100 chars + ellipsis


# ── write / append ──────────────────────────────────────────────────────────
def test_write_yaml_header_and_content(tmp_path):
    out = tmp_path / "review.yaml"
    e = {"date": "2011-06-05", "city": "Minsk", "country": "Belarus",
         "confidence": "high", "cue": "matched 'minsk'", "venue": "",
         "note": "trip", "source": "liveinternet", "li_id": "1",
         "url": "https://x/post1/"}
    n = il.write_yaml([e], out, append=False)
    assert n == 1
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Reconstructed pre-2012 travel")
    data = yaml.safe_load(text)
    assert data[0]["li_id"] == "1" and data[0]["city"] == "Minsk"


def test_write_yaml_append_dedups_by_li_id(tmp_path):
    out = tmp_path / "review.yaml"
    e1 = {"date": "2011-06-05", "city": "Minsk", "country": "Belarus",
          "confidence": "high", "cue": "c", "source": "liveinternet", "li_id": "1"}
    e2 = {"date": "2011-06-06", "city": "Brest", "country": "Belarus",
          "confidence": "low", "cue": "c", "source": "liveinternet", "li_id": "2"}
    assert il.write_yaml([e1], out, append=False) == 1
    # re-run adds only the new post, not the already-present one
    assert il.write_yaml([e1, e2], out, append=True) == 1
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert [d["li_id"] for d in data] == ["1", "2"]


def test_main_refuses_overwrite_without_append(tmp_path):
    out = tmp_path / "exists.yaml"
    out.write_text("- {li_id: 1}\n", encoding="utf-8")
    rc = il.main(["--from-cache", str(tmp_path), "--out", str(out)])
    assert rc == 1  # refused (and did not clobber)
    assert out.read_text(encoding="utf-8") == "- {li_id: 1}\n"
