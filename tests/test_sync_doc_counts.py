"""Every count rule in scripts/sync_doc_counts.py must still match its doc.

A reworded sentence in README.md / CLAUDE.md / solution.html / qa/ would silently
drop out of the monthly sync (doc-counts.yml) and start drifting again; this
catches it on the push that rewords it instead of a month later.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sync_doc_counts as sdc  # noqa: E402

KEYS = ("total", "offline", "live_api", "live_e2e", "e2e_smoke", "a11y", "files")


def test_every_rule_matches_its_doc():
    counts = {k: 1 for k in KEYS}
    _changes, missing = sdc.apply(counts, write=False)
    assert missing == []


def test_rules_only_use_known_keys():
    assert {key for _f, _p, key in sdc.RULES} <= set(KEYS)


def test_every_readme_stats_rule_matches():
    import sync_readme_stats as srs
    vals = srs.values({"checkins": 71845, "places": 1, "countries": 1, "km": 615332,
                       "photos": 23456, "years": 1, "year_pages": 1, "country_pages": 1})
    assert vals["checkins_k"] == "71,000" and vals["km"] == "615,332" and vals["earths"] == 15
    _changes, missing = sdc.apply(vals, write=False, rules=srs.RULES)
    assert missing == []
