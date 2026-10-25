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


def _ping_rows():
    return [{"who": "a", "first": 1792800000, "n": 3, "city": "Bălți", "country": "MD", "org": "Orange <SA>",
             "ref": "https://t.me/", "paths": "/index.html,/trips.html", "back": True},
            {"who": "b", "first": 1792803600, "n": 1, "city": "Minsk", "country": "BY", "org": "A1",
             "ref": None, "paths": "/venue.html", "back": False}]


def test_render_ping():
    text = vr.render_ping(_ping_rows(), 1792790000)
    assert text.startswith("<b>👀 2 visitors · 4 views</b> since ")
    assert "Bălți, MD · Orange &lt;SA&gt; · ↺ returning" in text and "/index.html, /trips.html · from https://t.me/" in text
    assert "Minsk, BY · A1\n" in text and "returning" not in text.split("Minsk")[1]


def test_ping_window_from_the_watermark(monkeypatch):
    now = 2_000_000
    monkeypatch.setattr(vr, "_q", lambda sql, params: [])
    assert vr.ping_since(now) == now - vr.PING_DEFAULT                         # first ping ever
    monkeypatch.setattr(vr, "_q", lambda sql, params: [{"updated_at": now - 5000}])
    assert vr.ping_since(now) == now - 5000                                    # continues where the last one stopped
    monkeypatch.setattr(vr, "_q", lambda sql, params: [{"updated_at": 10}])
    assert vr.ping_since(now) == now - vr.PING_MAX                             # long pause: capped


def test_ping_marks_returning_visitors_in_one_query(monkeypatch):
    seen = []

    def fake(sql, params):
        seen.append((sql, params))
        if "GROUP BY who" in sql:
            return [{"who": "a", "first": 5, "n": 1}, {"who": "b", "first": 6, "n": 2}]
        return [{"who": "b"}]

    monkeypatch.setattr(vr, "_q", fake)
    rows = vr.collect_ping(100, 200)
    assert [r["back"] for r in rows] == [False, True]
    assert len(seen) == 2 and all("owner = 0" in s for s, _ in seen)
    assert seen[0][1] == [100, 200] and seen[1][1] == [100, "a", "b"]


def test_ping_without_visitors_sends_nothing_and_moves_the_watermark(monkeypatch):
    calls = []
    monkeypatch.setattr(vr.d1, "query", lambda sql, params=None: calls.append(sql) or [])
    monkeypatch.setattr(vr.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    assert vr.main(["--ping"]) == 0
    assert calls[-1].startswith("INSERT OR REPLACE INTO sync_state")
    assert not any(s.startswith("DELETE") for s in calls)                      # retention stays weekly


def test_ping_watermark_moves_only_after_a_send(monkeypatch):
    calls = []
    monkeypatch.setattr(vr.d1, "query", lambda sql, params=None: calls.append(sql) or [])
    monkeypatch.setattr(vr, "collect_ping", lambda since, now: _ping_rows())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    class R:
        ok, status_code, text = False, 500, "boom"
    monkeypatch.setattr(vr.requests, "post", lambda *a, **k: R())
    assert vr.main(["--ping"]) == 1
    assert not any("sync_state (key" in s for s in calls)
    R.ok = True
    assert vr.main(["--ping"]) == 0
    assert calls[-1].startswith("INSERT OR REPLACE INTO sync_state")


def test_ping_marks_a_local_copy_referrer():
    rows = [{**_ping_rows()[1], "ref": "http://127.0.0.1:8766/"}]
    assert "⌂ from a local copy (http://127.0.0.1:8766/)" in vr.render_ping(rows, 1792790000)
    assert vr.LOCAL_REF.match("http://192.168.1.5:8000/x") and vr.LOCAL_REF.match("file:///C:/site/index.html")
    assert not vr.LOCAL_REF.match("https://t.me/") and not vr.LOCAL_REF.match("https://10times.com/")
