# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""visits_report — the weekly private digest of /api/hit page views."""
from __future__ import annotations

import re
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
        "languages": [{"lang": "ru-RU", "n": 8, "u": 3}],
        "devices": [{"dev": "phone", "n": 5, "u": 3}],
        "screens": [{"scr": "390x844@3", "n": 4, "u": 2}],
        "device_timezones": [{"dtz": "Europe/Chisinau", "tz": "Europe/Chisinau", "n": 4, "u": 2}],
        "models": [{"model": "Pixel 9", "n": 1, "u": 1}],
        "postals": [{"postal": "2001", "city": "Chișinău", "region": "Chișinău", "country": "MD", "n": 2, "u": 1}],
        "engagement": {"measured": 4, "avg_seconds": 71, "max_seconds": 300},
        "sessions": {"sessions": 4, "avg_pages": 2.25, "max_pages": 5, "avg_visible_seconds": 120},
        "newest": [{"vid": "a", "first": 1792800000, "n": 3, "ip": "1.2.3.4", "city": "Bălți",
                    "country": "MD", "org": "Orange", "dev": "phone", "lang": "ru-RU",
                    "dtz": "Europe/Chisinau", "scr": "390x844@3", "model": "Pixel 9", "postal": "2001"}],
    }


def test_render_report():
    text = vr.render(_collected())
    assert "12 views (+50% vs prev week)" in text and "5 visitors (+0% vs prev week)" in text
    assert "2 returning · 4 IPs" in text
    assert "• /index.html — 7 (4 u)" in text and "• Chișinău, MD — 9 (3 u)" in text
    assert "Moldtelecom &lt;SA&gt;" in text                      # HTML-escaped for parse_mode=HTML
    assert "1.2.3.4 · Bălți, MD · Orange · 3 views" in text
    assert "Browser languages" in text and "ru-RU — 8 (3 u)" in text
    assert "Device types" in text and "phone — 5 (3 u)" in text
    assert "390x844@3 — 4 (2 u)" in text and "Pixel 9 — 1 (1 u)" in text
    assert "2001 · Chișinău, Chișinău, MD — 2 (1 u)" in text
    assert "Visible page time: avg 1m 11s, longest 5m 0s" in text
    assert "Sessions (30-min inactivity rule): 4" in text and "2.25 pages/session" in text
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
    assert any("LAG(ts) OVER" in sql and "dur" in sql for sql, _ in seen)
    assert any("'unknown-' || id" in sql for sql, _ in seen)
    assert any("postal" in sql and "lang" in sql for sql, _ in seen)


def test_dry_run_deletes_nothing(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(vr.d1, "query", lambda sql, params=None, **kw: calls.append(sql) or [])
    monkeypatch.setattr(vr, "collect", lambda now: _collected())
    assert vr.main(["--dry-run"]) == 0
    assert not any(s.startswith("DELETE") for s in calls)
    assert "Site visits" in capsys.readouterr().out


def test_hit_endpoint_guards():
    js = (Path(__file__).resolve().parents[1] / "functions" / "api" / "hit.js").read_text(encoding="utf-8")
    assert "CF-Connecting-IP" in js and "90 * 86400" in js
    assert "BOT_RE.test(ua)" in js and "new URL(origin).host" in js   # bots and foreign origins dropped
    assert "MAX_PER_IP" in js and "VID_RE" in js and "VIEW_ID_RE" in js and "startsWith('//')" in js
    assert "body.t === 'meta'" in js and "view_id" in js and "body.t === 'leave'" in js
    vjs = (Path(__file__).resolve().parents[1] / "functions" / "api" / "visits.js").read_text(encoding="utf-8")
    assert "env.STATS_KEY" in vjs and "401" in vjs                    # closed by default
    assert "deviceTimezones" in vjs and "languages" in vjs and "postals" in vjs
    assert "ipStats, fingerprints" in vjs
    assert "PARTITION BY ip ORDER BY ts, id" in vjs and "AS visits" in vjs
    assert "COUNT(DISTINCT fp)" in vjs and "GROUP BY fp" in vjs
    assert "LAG(ts) OVER" in vjs and "avg_pages" in vjs and "dur" in vjs
    assert "'unknown-' || id" in vjs


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
    """A syntax error in a Pages Function or inline private-page script breaks it at deploy."""
    import shutil
    import subprocess

    import pytest
    node = shutil.which("node") or ("C:/Program Files/nodejs/node.exe" if Path("C:/Program Files/nodejs/node.exe").exists() else None)
    if not node:
        pytest.skip("node not installed — cannot syntax-check JavaScript")

    repo = Path(__file__).resolve().parents[1]
    scripts = []
    for js in sorted((repo / "functions").rglob("*.js")):
        scripts.append((js.name, js.read_text(encoding="utf-8")))

    build = (repo / "scripts" / "build.py").read_text(encoding="utf-8")
    hit = re.search(r'_hit_js = r"""(.*?)"""', build, re.S)
    assert hit, "build.py must define the generated page-view beacon"
    hit_html = hit.group(1)
    hit_js = hit_html[len("<script>"):hit_html.rfind("</script>")]
    scripts.append(("generated-hit-beacon.js", hit_js))

    page_src = (repo / "scripts" / "gen_visits.py").read_text(encoding="utf-8")
    page = re.search(r'PAGE = r"""(.*?)"""', page_src, re.S)
    assert page, "gen_visits.py must contain the private visits page"
    inline_scripts = re.findall(r"<script>(.*?)</script>", page.group(1), re.S)
    assert inline_scripts, "visits.html needs an inline controller"
    scripts.extend((f"visits-page-{i}.js", js) for i, js in enumerate(inline_scripts))

    for name, code in scripts:
        mjs = tmp_path / (name.replace("/", "_").replace(".", "_") + ".mjs")
        mjs.write_text(code, encoding="utf-8")
        proc = subprocess.run([node, "--check", str(mjs)], capture_output=True, timeout=30)
        assert proc.returncode == 0, f"{name}: {proc.stderr.decode(errors='replace')}"


def test_visits_page_renders_extended_analytics():
    page = (Path(__file__).resolve().parents[1] / "scripts" / "gen_visits.py").read_text(encoding="utf-8")
    for marker in ("Browser languages", "Device types", "Screen sizes", "Device time zones",
                   "Android models", "Approx. postal areas", "engagement-grid", "average pages / session", "IP addresses · visits per IP", "Device fingerprints", "visible "):
        assert marker in page, marker

def test_report_creates_the_visits_table_itself():
    ddl = vr.visits_ddl()
    assert ddl[0].startswith("CREATE TABLE IF NOT EXISTS visits") and "ip" in ddl[0]
    assert ddl[1] == "CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts)"
    added = {s.split()[-2] for s in ddl[2:]}                     # the analytics columns migrated in
    assert added == {"lang", "dtz", "scr", "dev", "model", "postal", "dur", "view_id", "fp"}
    assert all(s.startswith("ALTER TABLE visits ADD COLUMN") for s in ddl[2:])


def test_a_column_that_already_exists_is_not_an_error(monkeypatch):
    def fake(sql, params=None, **kw):
        if sql.startswith("ALTER"):
            raise RuntimeError("D1 error: duplicate column name: lang")
        return []
    monkeypatch.setattr(vr.d1, "query", fake)
    vr.run_ddl("ALTER TABLE visits ADD COLUMN lang TEXT")       # swallowed
    monkeypatch.setattr(vr.d1, "query", lambda sql, params=None, **kw: (_ for _ in ()).throw(RuntimeError("no such table")))
    import pytest
    with pytest.raises(RuntimeError):
        vr.run_ddl("CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts)")


def test_main_runs_the_ddl_before_reading(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(vr.d1, "query", lambda sql, params=None, **kw: calls.append(sql) or [])
    assert vr.main(["--dry-run"]) == 0
    assert calls[0].startswith("CREATE TABLE IF NOT EXISTS visits")
    assert "No visits recorded this week." in capsys.readouterr().out


def test_failures_surface_as_annotations(monkeypatch, capsys):
    monkeypatch.setattr(vr.d1, "query", lambda sql, params=None, **kw: [])
    monkeypatch.setattr(vr, "collect", lambda now: _collected())
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert vr.main([]) == 1
    assert "::error title=Visits report::secret(s) not set: TELEGRAM_CHAT_ID" in capsys.readouterr().out


def _ping_rows():
    return [{"who": "a", "first": 1792800000, "n": 3, "city": "Bălți", "country": "MD", "org": "Orange <SA>",
             "ref": "https://t.me/", "paths": "/index.html,/trips.html", "back": True,
             "dev": "phone", "lang": "ru-RU", "dtz": "Europe/Chisinau",
             "scr": "390x844@3", "model": "Pixel 9", "postal": "2001", "dur": 42},
            {"who": "b", "first": 1792803600, "n": 1, "city": "Minsk", "country": "BY", "org": "A1",
             "ref": None, "paths": "/venue.html", "back": False}]


def test_render_ping():
    text = vr.render_ping(_ping_rows(), 1792790000)
    assert text.startswith("<b>👀 2 visitors · 4 views</b> since ")
    assert "Bălți, MD · Orange &lt;SA&gt; · ↺ returning" in text and "/index.html, /trips.html · from https://t.me/" in text
    assert "Minsk, BY · A1\n" in text and "returning" not in text.split("Minsk")[1]
    assert "phone · model Pixel 9 · lang ru-RU · device TZ Europe/Chisinau" in text
    assert "screen 390x844@3 · ZIP 2001 · visible 42s" in text


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
    monkeypatch.setattr(vr.d1, "query", lambda sql, params=None, **kw: calls.append(sql) or [])
    monkeypatch.setattr(vr.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    assert vr.main(["--ping"]) == 0
    assert calls[-1].startswith("INSERT OR REPLACE INTO sync_state")
    assert not any(s.startswith("DELETE") for s in calls)                      # retention stays weekly


def test_ping_watermark_moves_only_after_a_send(monkeypatch):
    calls = []
    monkeypatch.setattr(vr.d1, "query", lambda sql, params=None, **kw: calls.append(sql) or [])
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
    assert vr.is_local_referrer("http://192.168.1.5:8000/x")
    assert vr.is_local_referrer("http://localhost:8000?debug=1")
    assert vr.is_local_referrer("http://[::1]:8000/")
    assert vr.is_local_referrer("http://[fd12::2]/")
    assert vr.is_local_referrer("http://printer.local/")
    assert vr.is_local_referrer("file:///C:/site/index.html")
    assert not vr.is_local_referrer("https://t.me/")
    assert not vr.is_local_referrer("https://10times.com/")
    assert not vr.is_local_referrer("http://127.999.0.1/")
    assert not vr.is_local_referrer(None)
