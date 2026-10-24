# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""visits_report — the weekly private digest of /api/hit page views."""
from __future__ import annotations

from pathlib import Path

import visits_report as vr


def _collected(views=12):
    return {
        "totals": {"views": views, "visitors": 5, "ips": 4}, "prev": {"views": 8, "visitors": 5},
        "returning": 2,
        "paths": [{"path": "/index.html", "n": 7, "u": 4}],
        "countries": [{"country": "MD", "n": 9, "u": 3}],
        "cities": [{"city": "Chișinău", "country": "MD", "n": 9, "u": 3}],
        "refs": [{"ref": "https://t.me/", "n": 3, "u": 2}],
        "orgs": [{"org": "Moldtelecom <SA>", "n": 6, "u": 2}],
        "newest": [{"vid": "a", "first": 1792800000, "n": 3, "ip": "1.2.3.4", "city": "Bălți",
                    "country": "MD", "org": "Orange"}],
    }


def test_render_report():
    text = vr.render(_collected())
    assert "12 views (+50% vs prev week)" in text and "5 visitors (+0% vs prev week)" in text
    assert "2 returning · 4 IPs" in text
    assert "• /index.html — 7 (4 u)" in text and "• Chișinău, MD — 9 (3 u)" in text
    assert "Moldtelecom &lt;SA&gt;" in text                      # HTML-escaped for parse_mode=HTML
    assert "1.2.3.4 · Bălți, MD · Orange · 3 views" in text
    assert 'href="https://4sq.pages.dev/visits.html"' in text


def test_render_empty_week():
    assert vr.render({**_collected(0), "prev": {}}).endswith("No visits recorded this week.")


def test_collect_excludes_owner_and_windows(monkeypatch):
    seen = []

    def fake(sql, params):
        seen.append((sql, params))
        return [{"views": 1, "visitors": 1, "ips": 1, "n": 1}]

    monkeypatch.setattr(vr, "_q", fake)
    vr.collect(1_000_000)
    assert all("owner = 0" in sql for sql, _ in seen)
    assert all(p[0] in (1_000_000 - vr.WEEK, 1_000_000 - 2 * vr.WEEK) for _, p in seen)


def test_dry_run_deletes_nothing(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(vr.d1, "query", lambda sql, params=None: calls.append(sql) or [])
    monkeypatch.setattr(vr, "collect", lambda now: _collected())
    assert vr.main(["--dry-run"]) == 0
    assert not any(s.startswith("DELETE") for s in calls)
    assert "Site visits" in capsys.readouterr().out


def test_hit_endpoint_guards():
    js = (Path(__file__).resolve().parents[1] / "functions" / "api" / "hit.js").read_text(encoding="utf-8")
    assert "CF-Connecting-IP" in js and "90 * 86400" in js
    assert "BOT_RE.test(ua)" in js and "new URL(origin).host" in js   # bots and foreign origins dropped
    assert "MAX_PER_IP" in js and "VID_RE" in js and "startsWith('//')" in js
    vjs = (Path(__file__).resolve().parents[1] / "functions" / "api" / "visits.js").read_text(encoding="utf-8")
    assert "env.STATS_KEY" in vjs and "401" in vjs                    # closed by default


def test_long_report_is_cut_at_a_line_break():
    text = "\n".join(f'<b>row {i}</b> <a href="https://x/{i}">{"y" * 80}</a>' for i in range(100))
    cut = vr._fit(text)
    assert len(cut) <= 3902 and cut.endswith("\n…")
    assert cut.count("<a ") == cut.count("</a>") and cut.count("<b>") == cut.count("</b>")


def test_labels_are_clipped():
    c = _collected()
    c["refs"] = [{"ref": "https://example.com/" + "z" * 300, "n": 1, "u": 1}]
    line = next(ln for ln in vr.render(c).splitlines() if "example.com" in ln)
    assert len(line) < 120 and "…" in line


def test_pages_functions_parse(tmp_path):
    """A Function with a syntax error is not deployed at all — /api/* would 404."""
    import shutil
    import subprocess

    import pytest
    node = shutil.which("node") or ("C:/Program Files/nodejs/node.exe" if Path("C:/Program Files/nodejs/node.exe").exists() else None)
    if not node:
        pytest.skip("node not installed — cannot syntax-check functions/")
    for js in sorted((Path(__file__).resolve().parents[1] / "functions").rglob("*.js")):
        mjs = tmp_path / (js.stem + ".mjs")             # ES-module syntax (export) needs .mjs
        mjs.write_text(js.read_text(encoding="utf-8"), encoding="utf-8")
        proc = subprocess.run([node, "--check", str(mjs)], capture_output=True, timeout=30)
        assert proc.returncode == 0, f"{js.name}: {proc.stderr.decode(errors='replace')}"


def test_report_creates_the_visits_table_itself():
    ddl = vr.visits_ddl()
    assert len(ddl) == 2
    assert ddl[0].startswith("CREATE TABLE IF NOT EXISTS visits") and "ip" in ddl[0]
    assert ddl[1] == "CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts)"


def test_main_runs_the_ddl_before_reading(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(vr.d1, "query", lambda sql, params=None: calls.append(sql) or [])
    assert vr.main(["--dry-run"]) == 0
    assert calls[0].startswith("CREATE TABLE IF NOT EXISTS visits")
    assert "No visits recorded this week." in capsys.readouterr().out


def test_failures_surface_as_annotations(monkeypatch, capsys):
    monkeypatch.setattr(vr.d1, "query", lambda sql, params=None: [])
    monkeypatch.setattr(vr, "collect", lambda now: _collected())
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert vr.main([]) == 1
    assert "::error title=Visits report::secret(s) not set: TELEGRAM_CHAT_ID" in capsys.readouterr().out
