# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""llm_ladder.py — model order per task, the text checks, and climbing on a failed call or a rejected text."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import llm_ladder as ll

GOOD = " ".join(["word"] * 50)
SPEC = ll.Spec(words=(30, 60), max_emoji=1)


@pytest.fixture
def cli(monkeypatch):
    """Claude Code CLI transport with a scripted reply per model."""
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_MODELS", "ANTHROPIC_MODELS_ECHO"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.setattr(ll.shutil, "which", lambda name: "/usr/bin/claude")
    calls: list[tuple[str, str]] = []

    def make(replies: dict[str, object]):
        def run(cmd, input, **kw):
            model = cmd[cmd.index("--model") + 1]
            calls.append((model, input))
            r = replies.get(model)
            if r is None:
                return SimpleNamespace(returncode=1, stdout="", stderr="overloaded")
            return SimpleNamespace(returncode=0, stdout=r, stderr="")
        return run
    return make, calls


def test_ladder_defaults_and_overrides(monkeypatch):
    for var in ("ANTHROPIC_MODEL", "ANTHROPIC_MODELS", "ANTHROPIC_MODELS_STORY"):
        monkeypatch.delenv(var, raising=False)
    assert ll.ladder("echo") == [ll.HAIKU, ll.SONNET, ll.OPUS]
    assert ll.ladder("story") == [ll.SONNET, ll.OPUS]
    monkeypatch.setenv("ANTHROPIC_MODEL", ll.OPUS)
    assert ll.ladder("story") == [ll.OPUS, ll.SONNET]
    monkeypatch.setenv("ANTHROPIC_MODELS", "a, b")
    assert ll.ladder("echo") == ["a", "b"]
    monkeypatch.setenv("ANTHROPIC_MODELS_STORY", "x")
    assert ll.ladder("story") == ["x"] and ll.ladder("echo") == ["a", "b"]


def test_check_flags_form_not_style():
    assert ll.check(GOOD, SPEC) == []
    assert ll.check("A dash — fine. 2015 was good. " + GOOD, SPEC) == []
    assert "too short" in ll.check("Hi there.", SPEC)[0]
    assert "too long" in ll.check(GOOD * 3, SPEC)[0]
    assert any("emoji" in p for p in ll.check(GOOD + " 🍺🎉", SPEC))
    assert any("markdown" in p for p in ll.check("**Bold** " + GOOD, SPEC))
    assert any("markdown" in p for p in ll.check("- one\n- two " + GOOD, SPEC))
    assert any("refusal" in p for p in ll.check("Here is the story: " + GOOD, SPEC))
    assert any("leaked" in p for p in ll.check('Facts: {"events": 1} ' + GOOD, SPEC))


def test_first_rung_wins_when_its_text_passes(cli):
    make, calls = cli
    text, src = ll.write("echo", "P", SPEC, run=make({ll.HAIKU: GOOD}))
    assert text == GOOD and src == f"claude-cli:{ll.HAIKU}" and len(calls) == 1


def test_failed_call_climbs_with_the_same_prompt(cli):
    make, calls = cli
    text, src = ll.write("echo", "P", SPEC, run=make({ll.SONNET: GOOD}))
    assert src == f"claude-cli:{ll.SONNET}"
    assert [m for m, _ in calls] == [ll.HAIKU, ll.SONNET] and calls[1][1] == "P"


def test_rejected_text_climbs_with_the_reasons(cli):
    make, calls = cli
    text, src = ll.write("echo", "P", SPEC, run=make({ll.HAIKU: "**x** " + GOOD, ll.SONNET: GOOD}))
    assert src == f"claude-cli:{ll.SONNET}"
    retry = calls[1][1]
    assert retry.startswith("P\n\nA previous draft was rejected for: markdown") and "**x**" in retry


def test_every_rung_failing_falls_back_to_template(cli):
    make, calls = cli
    assert ll.write("echo", "P", SPEC, run=make({})) == (None, "template")
    assert len(calls) == 3
    bad = "As an AI I can't. " + GOOD
    assert ll.write("echo", "P", SPEC, run=make({m: bad for m in ll.LADDERS["echo"]})) == (None, "template")


def test_length_only_miss_still_beats_the_template(cli):
    make, _ = cli
    short = "A short but fine text."
    text, src = ll.write("echo", "P", SPEC, run=make({m: short for m in ll.LADDERS["echo"]}))
    assert text == short and src == f"claude-cli:{ll.OPUS}"


def test_no_credentials_means_template(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert ll.write("story", "P", SPEC) == (None, "template")


def test_api_transport_uses_the_rung_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    for var in ("ANTHROPIC_MODEL", "ANTHROPIC_MODELS", "ANTHROPIC_MODELS_STORY"):
        monkeypatch.delenv(var, raising=False)
    seen = []

    class Resp:
        def __init__(self, ok):
            self.ok = ok

        def raise_for_status(self):
            if not self.ok:
                import requests
                raise requests.HTTPError("529")

        def json(self):
            return {"content": [{"type": "text", "text": GOOD}]}

    class Session:
        def post(self, url, headers, json, timeout):
            seen.append(json["model"])
            return Resp(json["model"] == ll.OPUS)

    text, src = ll.write("story", "P", SPEC, session=Session())
    assert seen == [ll.SONNET, ll.OPUS] and src == f"claude-api:{ll.OPUS}"
