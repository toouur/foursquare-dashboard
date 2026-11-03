#!/usr/bin/env python3
"""Model ladder for the Telegram texts (on-this-day story, check-in echo, digest).

Borrowed from HookahSwarm's threads-auto.yml: the lightest model that does the
job writes first; when its call fails (overloaded, quota, CLI error) or its
text fails the checks below, the next model up rewrites it, and the reasons
for the rejection go into the retry prompt. Only when every rung fails do the
callers fall back to their templates, so the bots still never stay silent.

Ladders per task (light → heavy), overridable from the workflow:
  ANTHROPIC_MODELS_<TASK>  comma list, e.g. ANTHROPIC_MODELS_STORY=claude-opus-5-5
  ANTHROPIC_MODELS         comma list for every task
  ANTHROPIC_MODEL          one model put on the first rung, the defaults after it

Transport: the Messages API with ANTHROPIC_API_KEY, else the Claude Code CLI
(`claude -p --model …`) with CLAUDE_CODE_OAUTH_TOKEN (Pro/Max subscription).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

HAIKU, SONNET, OPUS = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"

# The daily story is long and posted once, so it starts on the medium model;
# the echo and the digest are short and frequent, so they start light.
LADDERS: dict[str, list[str]] = {
    "story": [SONNET, OPUS],
    "echo": [HAIKU, SONNET, OPUS],
    "digest": [HAIKU, SONNET, OPUS],
}

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿]")
_MARKDOWN = re.compile(r"(?m)^\s{0,3}(#{1,6}\s|[-*•]\s|\d{1,2}[.)]\s)|\*\*|__|`")
_REFUSAL = re.compile(r"(?i)\b(as an ai|i (?:can(?:no|')t|am unable to|don't have (?:access|enough))"
                      r"|i'm (?:sorry|unable)|here(?:'s| is) (?:a|the|your) (?:story|recap|message))")
_LEAK = re.compile(r"(?i)^\s*facts\s*:|\{\s*\"|\"events\"\s*:")


@dataclass
class Spec:
    """What a good text looks like: word range from the prompt, emoji cap."""
    words: tuple[int, int]
    max_emoji: int
    max_tokens: int = 600


def ladder(task: str) -> list[str]:
    for var in (f"ANTHROPIC_MODELS_{task.upper()}", "ANTHROPIC_MODELS"):
        raw = os.environ.get(var, "").strip()
        if raw:
            return [m.strip() for m in raw.split(",") if m.strip()]
    base = LADDERS.get(task, [SONNET, OPUS])
    first = os.environ.get("ANTHROPIC_MODEL", "").strip()
    return [first] + [m for m in base if m != first] if first else list(base)


def words_range(spec: str) -> tuple[int, int]:
    """"80-120" → (80, 120)."""
    lo, _, hi = spec.partition("-")
    return int(lo), int(hi or lo)


def check(text: str, spec: Spec) -> list[str]:
    """Reasons the text is not fit to post; [] means it passes. Loose on
    length (models drift ±40% and that reads fine), strict on form."""
    problems = []
    n = len(text.split())
    lo, hi = spec.words
    if n < lo * 0.6:
        problems.append(f"too short: {n} words, asked for {lo}-{hi}")
    if n > hi * 1.4:
        problems.append(f"too long: {n} words, asked for {lo}-{hi}")
    e = len(_EMOJI.findall(text))
    if e > spec.max_emoji:
        problems.append(f"{e} emoji, at most {spec.max_emoji} allowed")
    if _MARKDOWN.search(text):
        problems.append("markdown (headings, lists, bold or code) — plain text only")
    if _REFUSAL.search(text):
        problems.append("meta talk or a refusal instead of the text itself")
    if _LEAK.search(text):
        problems.append("the facts/JSON leaked into the text")
    return problems


def api_text(prompt: str, model: str, api_key: str, max_tokens: int = 600,
             session=requests) -> str | None:
    try:
        resp = session.post("https://api.anthropic.com/v1/messages", headers={
            "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json",
        }, json={"model": model, "max_tokens": max_tokens,
                 "messages": [{"role": "user", "content": prompt}]}, timeout=90)
        resp.raise_for_status()
        text = "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text")
        return text.strip() or None
    except (requests.RequestException, ValueError) as exc:
        log.warning("Claude API (%s) failed: %s", model, exc)
        return None


def cli_text(prompt: str, model: str = "", run=subprocess.run) -> str | None:
    """`claude -p` with the prompt over stdin (no argv length limit). Any failure → None."""
    exe = shutil.which("claude")
    if not exe:
        log.warning("claude CLI not found")
        return None
    cmd = [exe, "-p", "--output-format", "text"] + (["--model", model] if model else [])
    try:
        res = run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("claude CLI (%s) failed: %s", model, exc)
        return None
    if res.returncode != 0:
        log.warning("claude CLI (%s) exited %s: %s", model, res.returncode,
                    (res.stderr or res.stdout or "").strip()[:300])
        return None
    return (res.stdout or "").strip() or None


def _retry_prompt(prompt: str, draft: str, problems: list[str]) -> str:
    return (prompt + "\n\nA previous draft was rejected for: " + "; ".join(problems)
            + ". Write it again, fixing that.\n\nRejected draft:\n" + draft)


def write(task: str, prompt: str, spec: Spec, session=requests,
          run=subprocess.run) -> tuple[str | None, str]:
    """(text, source) — source is "claude-api:<model>" / "claude-cli:<model>",
    or (None, "template") when no transport is configured or every rung failed."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        transport = "claude-api"
    elif os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        transport = "claude-cli"
    else:
        log.info("no ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN — template")
        return None, "template"
    ask = prompt
    best: tuple[str, str] | None = None     # a draft whose only fault is length
    for model in ladder(task):
        text = (api_text(ask, model, api_key, spec.max_tokens, session) if api_key
                else cli_text(ask, model, run))
        if not text:
            continue                         # call failed → next model, same prompt
        problems = check(text, spec)
        if not problems:
            log.info("%s written by %s", task, model)
            return text, f"{transport}:{model}"
        log.warning("%s from %s rejected: %s", task, model, "; ".join(problems))
        if all(p.startswith("too ") for p in problems):
            best = (text, f"{transport}:{model}")
        ask = _retry_prompt(prompt, text, problems)
    if best:
        log.warning("%s: no rung passed; posting the draft that only missed the length", task)
        return best
    return None, "template"
