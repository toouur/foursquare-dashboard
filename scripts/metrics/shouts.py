# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Shout text-mining, sentiment, records + comment merge (metrics package)."""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .companions import collect_companions

log = logging.getLogger(__name__)

# ── Shout text mining ──────────────────────────────────────────────────────────

import re as _re  # noqa: E402 — section-local import for the shout-mining block

# Stopwords across the languages that appear in shouts (en, ru, be).  Kept
# minimal and additive — false-positives just leak a stopword into the
# top-N list, which is annoying but not broken.
_STOPWORDS: frozenset[str] = frozenset({
    # English
    "the","a","an","and","or","but","of","to","for","in","on","at","by","with",
    "is","was","are","be","been","being","am","i","my","me","mine","we","our",
    "you","your","he","she","it","its","they","them","their","this","that",
    "these","those","as","if","then","than","so","such","just","not","no",
    "yes","do","does","did","done","have","has","had","will","would","could",
    "should","can","may","might","get","got","make","made","go","went","gone",
    "from","up","down","out","off","over","under","again","still","more",
    "less","most","least","very","too","also","only","own","other","some",
    "any","all","each","every","new","old","good","bad","one","two","three",
    "first","last","time","day","now","here","there","what","who","when",
    "where","why","how","im","ill","ive","its","dont","didnt","cant","wont",
    # Russian
    "и","в","во","не","что","он","на","я","с","со","как","а","то","все",
    "она","так","его","но","да","ты","к","у","же","вы","за","бы","по","только",
    "ее","мне","было","вот","от","меня","еще","нет","о","из","ему","теперь",
    "когда","даже","ну","вдруг","ли","если","уже","или","ни","быть","был",
    "него","до","вас","нибудь","опять","уж","вам","ведь","там","потом","себя",
    "ничего","ей","может","они","тут","где","есть","надо","ней","для","мы",
    "тебя","их","чем","была","сам","чтоб","без","будто","чего","раз","тоже",
    "себе","под","будет","ж","тогда","кто","этот","того","потому","этого",
    "какой","совсем","ним","здесь","этом","один","почти","мой","тем","чтобы",
    "нее","сейчас","были","куда","зачем","всех","никогда","можно","при",
    "наконец","два","об","другой","хоть","после","над","больше","тот","через",
    "эти","нас","про","всего","них","какая","много","разве","три","эту",
    "моя","впрочем","хорошо","свою","этой","перед","иногда","лучше","чуть",
    "том","нельзя","такой","им","более","всегда","конечно","всю","между",
    # Belarusian (additions)
    "у","і","на","ў","да","не","з","ад","па","за","для","пра","над","пад",
    "цераз","праз","між","без","пры","к","к","і","ці","альбо","ёсць","няма",
    "быў","была","было","былі","можа","трэба","тут","там","гэта","гэты",
    "тая","той","той","той","ужо","яшчэ","нават","толькі","мы","вы","ён","яна",
    "яно","яны","свой","свая","сваё","свае",
})

# Token regex: keep unicode letters/digits, length ≥ 3.
_TOKEN_RE = _re.compile(r"[\w']{3,}", _re.UNICODE)

# Emoji extraction — covers the major unicode emoji blocks (not exhaustive
# but catches >99% of what people actually type).  Uses str scan rather than
# regex range because Python's re module groks Unicode codepoint ranges
# inconsistently across platforms.
_EMOJI_RANGES = (
    (0x1F300, 0x1F5FF),  # Misc symbols and pictographs
    (0x1F600, 0x1F64F),  # Emoticons
    (0x1F680, 0x1F6FF),  # Transport and map
    (0x1F700, 0x1F77F),
    (0x1F780, 0x1F7FF),
    (0x1F800, 0x1F8FF),
    (0x1F900, 0x1F9FF),  # Supplemental symbols and pictographs
    (0x1FA00, 0x1FA6F),
    (0x1FA70, 0x1FAFF),
    (0x2600,  0x26FF),   # Misc symbols
    (0x2700,  0x27BF),   # Dingbats
)

def _is_emoji_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)

def _extract_emojis(text: str) -> list[str]:
    return [ch for ch in text if _is_emoji_char(ch)]

# Light emoji sentiment lexicon — language-independent.  Conservative: only
# include emojis whose polarity is unambiguous.  Counted at character level.
_EMOJI_POS = frozenset("👍😀😁😂😃😄😅😆😊😍🥰😘🤩🥳🎉🎊🍻🥂🌟⭐❤💖💕💗💜💛💚💙🧡🍰🍕🍔🍣🍜🍱🍷🌅🌄🌞🏖🏝🏔🚀")
_EMOJI_NEG = frozenset("👎😞😢😭😱😡🤬💔😤😨😰😖😣😩😫😒🙄☹😟🤢🤮")

def _detect_lang(text: str) -> str:
    """Return 'cyr' if cyrillic dominates, 'lat' if latin, else 'mix'."""
    cyr = lat = 0
    for ch in text:
        if 'А' <= ch <= 'я' or ch in 'ЁёІіЎў':
            cyr += 1
        elif 'a' <= ch.lower() <= 'z':
            lat += 1
    if cyr == 0 and lat == 0:
        return "other"
    if cyr > lat * 2:
        return "cyr"
    if lat > cyr * 2:
        return "lat"
    return "mix"

def shout_analysis(rows: list[dict]) -> dict:
    """Mine the free-text 'shout' column for words, language, sentiment.

    Operates over the same filtered set used by shout_records() so the
    `total_shouts` count on the stats page matches the Shouts page count.
    """
    # Build a denylist of companion-name tokens (lower-cased single words).
    name_deny: set[str] = set()
    for r in rows:
        for col in ("with_name", "created_by_name", "overlaps_name"):
            raw = (r.get(col) or "").replace(" ,", ",")
            for part in raw.split(","):
                for tok in part.strip().split():
                    t = tok.strip().lower()
                    if len(t) >= 3 and t != "-":
                        name_deny.add(t)

    # Reuse the records pipeline so the filtered set is identical.
    records = shout_records(rows)
    shouts: list[tuple[int, str, str, str]] = []  # (year, country, city, text)
    for rec in records:
        try:
            yr = datetime.fromtimestamp(rec["ts"], tz=timezone.utc).year
        except (ValueError, OSError):
            continue
        shouts.append((yr, rec.get("country", ""), rec.get("city", ""), rec["text"]))

    if not shouts:
        return {}

    # Word frequency — global + per country (companion names denied)
    word_ctr_global: Counter = Counter()
    word_ctr_country: dict[str, Counter] = defaultdict(Counter)
    for _yr, co, _cy, text in shouts:
        toks = [t.lower() for t in _TOKEN_RE.findall(text)]
        toks = [t for t in toks
                if t not in _STOPWORDS
                and t not in name_deny
                and not t.isdigit()]
        word_ctr_global.update(toks)
        if co:
            word_ctr_country[co].update(toks)

    # Emoji frequency
    emoji_ctr: Counter = Counter()
    emoji_by_year: dict[int, Counter] = defaultdict(Counter)
    for yr, _co, _cy, text in shouts:
        for e in _extract_emojis(text):
            emoji_ctr[e] += 1
            emoji_by_year[yr][e] += 1

    # Emoji-based sentiment per year — share of pos vs neg emoji-bearing shouts
    sentiment_by_year: dict[int, list] = defaultdict(lambda: [0, 0, 0])  # [pos, neg, neutral]
    for yr, _co, _cy, text in shouts:
        pos = sum(1 for ch in text if ch in _EMOJI_POS)
        neg = sum(1 for ch in text if ch in _EMOJI_NEG)
        if pos > neg:
            sentiment_by_year[yr][0] += 1
        elif neg > pos:
            sentiment_by_year[yr][1] += 1
        else:
            sentiment_by_year[yr][2] += 1

    # Language distribution per year (cyr/lat/mix/other)
    lang_by_year: dict[int, Counter] = defaultdict(Counter)
    for yr, _co, _cy, text in shouts:
        lang_by_year[yr][_detect_lang(text)] += 1

    # Per-year shout count and avg length
    per_year: dict[int, list] = defaultdict(lambda: [0, 0])  # [count, total_chars]
    for yr, _co, _cy, text in shouts:
        per_year[yr][0] += 1
        per_year[yr][1] += len(text)

    # Top words per country — top 10 countries by shout volume, top 12 words each
    shouts_per_country: Counter = Counter(co for _yr, co, _cy, _t in shouts if co)
    top_cos = [co for co, _ in shouts_per_country.most_common(10)]
    words_by_country: list = []
    for co in top_cos:
        top12 = word_ctr_country[co].most_common(12)
        if top12:
            words_by_country.append([co, [[w, c] for w, c in top12]])

    return {
        "total_shouts":   len(shouts),
        "total_words":    sum(word_ctr_global.values()),
        "top_words":      [[w, c] for w, c in word_ctr_global.most_common(60)],
        "top_emojis":     [[e, c] for e, c in emoji_ctr.most_common(40)],
        "shouts_per_year": sorted([[str(yr), v[0], round(v[1] / v[0])] for yr, v in per_year.items()]),
        "sentiment_by_year": sorted([[str(yr), v[0], v[1], v[2]] for yr, v in sentiment_by_year.items()]),
        "lang_by_year":   sorted([[str(yr), ctr.get("lat", 0), ctr.get("cyr", 0), ctr.get("mix", 0), ctr.get("other", 0)] for yr, ctr in lang_by_year.items()]),
        "words_by_country": words_by_country,
    }



# Module-level so build.py / gen_shouts can reuse the same suffix regex.
_SHOUT_SUFFIX_RE = _re.compile(r"\s*[—\-–]\s*with\s+.+$", _re.IGNORECASE | _re.UNICODE)
# Matches shouts whose ENTIRE text is just "with X" companion attribution
# (with or without a leading dash). ~14K of the 18K shouts are this pattern.
_SHOUT_WITH_ONLY_RE = _re.compile(r"^\s*[—\-–]?\s*with\s+\S+", _re.IGNORECASE | _re.UNICODE)

def _build_companion_denylist(rows: list[dict]) -> set[str]:
    """Collect every companion name (full + first-name) seen in the rows.

    Used to drop shouts whose text is just a bare companion name like
    "Joanna" (~190 such rows) — Foursquare stores attribution without the
    "with" prefix sometimes, so the regex above misses them.
    """
    names: set[str] = set()
    for r in rows:
        for col in ("with_name", "created_by_name", "overlaps_name"):
            raw = (r.get(col) or "").replace(" ,", ",")
            for part in raw.split(","):
                n = part.strip()
                if not n or n == "-" or len(n) < 2:
                    continue
                names.add(n.lower())
                first = n.split()[0] if n.split() else ""
                if first:
                    names.add(first.lower())
    return names


def shout_records(rows: list[dict]) -> list[dict]:
    """Return check-ins that carry a real text shout, sorted newest-first.

    Filters keep only substantive content:
      1. Strip trailing " — with X" companion suffix.
      2. Drop shouts whose entire content is just "with X" attribution.
      3. Drop bare companion-name shouts ("Joanna", "Максим") — same
         attribution data leaked in without the "with" prefix.
      4. Drop pure-punctuation shouts (".", "!", "?") which have no signal.
    """
    companion_names = _build_companion_denylist(rows)
    # Tokenise candidate text into words for the bare-name check.
    word_re = _re.compile(r"[^\W_]+", _re.UNICODE)

    out: list[dict] = []
    for r in rows:
        s = (r.get("shout") or "").strip()
        if not s:
            continue
        clean = _SHOUT_SUFFIX_RE.sub("", s).strip()
        if not clean:
            continue
        # Skip pure companion-attribution shouts ("with Joanna", "— with Tata")
        if _SHOUT_WITH_ONLY_RE.match(clean):
            continue
        # Skip if every alphanumeric token is itself a known companion name
        # (covers bare "Joanna", "Максим", "NIkita Tata" etc.)
        toks = word_re.findall(clean)
        if toks and all(t.lower() in companion_names for t in toks):
            continue
        # Skip pure-punctuation shouts (no word tokens at all)
        if not toks:
            continue
        try:
            ts = int(r["date"])
        except (ValueError, KeyError, TypeError):
            continue
        out.append({
            "ts":         ts,
            "text":       clean,
            "venue":      (r.get("venue") or "").strip(),
            "venue_id":   (r.get("venue_id") or "").strip(),
            "city":       (r.get("city") or "").strip(),
            "country":    (r.get("country") or "").strip(),
            "category":   (r.get("category") or "").strip(),
            "companions": collect_companions(r),
            "lat":        r.get("lat") or None,
            "lng":        r.get("lng") or None,
            "checkin_id": (r.get("checkin_id") or "").strip(),
        })
    out.sort(key=lambda x: -x["ts"])
    return out


def merge_comments_into_shouts(
    shouts: list[dict], rows: list[dict], comments_map: dict
) -> list[dict]:
    """Return a shouts-page list enriched with per-check-in comment threads.

    `comments_map` is the `comments` object from comments.json: {checkin_id:
    {venue, venue_id, ts, items:[{text, at, author, author_id}], …}}. Comments
    are the reply thread posted *under* a check-in — distinct from its `shout`.

    Behaviour:
      1. Every shout entry gets a `comments` list (its thread, or []).
      2. Check-ins that have comments but NO shout are added as new entries
         (text=""), so the archive surfaces every comment thread, not just the
         ones that happen to sit under a shout.
    Original `shouts` dicts are not mutated. Result is sorted newest-first.
    """
    comments_map = comments_map or {}

    def _thread(cid: str) -> list[dict]:
        return list((comments_map.get(cid) or {}).get("items") or [])

    out: list[dict] = []
    shout_ids: set[str] = set()
    for s in shouts:
        cid = (s.get("checkin_id") or "").strip()
        shout_ids.add(cid)
        entry = dict(s)
        entry["comments"] = _thread(cid)
        out.append(entry)

    # Comment-only check-ins (have a thread but no qualifying shout). Pull venue/
    # city/country/category/companions from the check-in row when we have it,
    # else fall back to the venue/ts stored alongside the comments.
    row_by_id = {(r.get("checkin_id") or "").strip(): r for r in rows}
    for cid, meta in comments_map.items():
        cid = (cid or "").strip()
        if not cid or cid in shout_ids:
            continue
        items = _thread(cid)
        if not items:
            continue
        r = row_by_id.get(cid)
        if r is not None:
            try:
                ts = int(r["date"])
            except (ValueError, KeyError, TypeError):
                ts = int(meta.get("ts") or 0)
            out.append({
                "ts":         ts,
                "text":       "",
                "venue":      (r.get("venue") or "").strip(),
                "venue_id":   (r.get("venue_id") or "").strip(),
                "city":       (r.get("city") or "").strip(),
                "country":    (r.get("country") or "").strip(),
                "category":   (r.get("category") or "").strip(),
                "companions": collect_companions(r),
                "checkin_id": cid,
                "comments":   items,
            })
        else:
            out.append({
                "ts":         int(meta.get("ts") or 0),
                "text":       "",
                "venue":      (meta.get("venue") or "").strip(),
                "venue_id":   (meta.get("venue_id") or "").strip(),
                "city":       "",
                "country":    "",
                "category":   "",
                "companions": [],
                "checkin_id": cid,
                "comments":   items,
            })

    out.sort(key=lambda x: -x["ts"])
    return out

