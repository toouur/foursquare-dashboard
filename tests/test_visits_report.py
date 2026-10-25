# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""visits_report — the weekly private digest of /api/hit page views."""
from __future__ import annotations

import json
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
    # the late model beacon must not re-hash the row: one browser = one fingerprint
    fp_fn = js[js.index("async function deviceFingerprint"):js.index("export async function")]
    assert "body.m" not in fp_fn and "fp = COALESCE" not in js
    assert "', utm'" in js and "/utm/i.test" in js                    # tags stored; a missing column still counts the view
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
    for marker in ("Now on site", "Top venues", "Site searches", "Outbound clicks", "Scroll depth", "Web Vitals",
                   "people (ids merged)", "browser ids", "new</span> / <span", "Network · VPN / relay / timezone check", "Countries without VPN / relay views", "device-names.json",
                   "iphoneGuess", "Sources · where sessions start", "Off-site referrers", "UTM tags", "Sessions · page sequence",
                   "owner-toggle", "with-owner", "Clear all visits", "Browser languages", "Device types", "Screen sizes", "Device time zones",
                   "Android models", "Approx. postal areas", "engagement-grid", "average pages / session", "IP addresses · visits per IP", "Device fingerprints", "visible "):
        assert marker in page, marker

def test_report_creates_the_visits_table_itself():
    ddl = vr.visits_ddl()
    assert ddl[0].startswith("CREATE TABLE IF NOT EXISTS visits") and "ip" in ddl[0]
    assert ddl[1] == "CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts)"
    alters = [s for s in ddl[2:] if s.startswith("ALTER TABLE")]
    added = {s.split()[-2] for s in alters}                      # the analytics columns migrated in
    assert added == {"lang", "dtz", "scr", "dev", "model", "postal", "dur", "view_id", "fp", "utm",
                     "scroll", "lcp", "cls", "inp", "ttfb"}
    assert all(s.startswith("ALTER TABLE visits ADD COLUMN") for s in alters)
    rest = [s for s in ddl[2:] if not s.startswith("ALTER TABLE")]   # the in-page events table
    assert rest[0].startswith("CREATE TABLE IF NOT EXISTS visit_events") and "kind" in rest[0]
    assert rest[1] == "CREATE INDEX IF NOT EXISTS idx_visit_events_ts ON visit_events(ts)"


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
             "scr": "390x844@3", "model": "Pixel 9", "postal": "2001", "dur": 42,
             "utm": "source=tg&medium=post"},
            {"who": "b", "first": 1792803600, "n": 1, "city": "Minsk", "country": "BY", "org": "A1",
             "ref": None, "paths": "/venue.html", "back": False}]


def test_render_ping():
    text = vr.render_ping(_ping_rows(), 1792790000)
    assert text.startswith("<b>👀 2 visitors · 4 views</b> since ")
    assert "Bălți, MD · Orange &lt;SA&gt; · ↺ returning" in text and "/index.html, /trips.html · from https://t.me/" in text
    assert "Minsk, BY · A1\n" in text and "returning" not in text.split("Minsk")[1]
    assert "utm source=tg&amp;medium=post · phone · model Pixel 9 · lang ru-RU · device TZ Europe/Chisinau" in text
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


def test_hit_beacon_keeps_venue_hash_on_pretty_urls():
    """Cloudflare Pages serves venue.html as /venue — the #id must survive both forms."""
    import shutil
    import subprocess

    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    build = (Path(__file__).resolve().parents[1] / "scripts" / "build.py").read_text(encoding="utf-8")
    line = next(ln for ln in build.splitlines() if "var p=location.pathname" in ln).strip()
    expr = line[len("var p="):line.index(";")]
    js = ("const cases=[['/venue','#abc'],['/venue.html','#abc'],['/venues','#x'],['/','#y']];"
          "console.log(JSON.stringify(cases.map(([pathname,hash])=>{const location={pathname,hash};return " + expr + ";})));")
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == ["/venue#abc", "/venue.html#abc", "/venues", "/"]


def _node_or_skip():
    import shutil

    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    return node


def _run_beacon(node, search, ua_data=""):
    """Run the generated hit snippet under stubbed browser globals; return (beacon, owner flag, URL after)."""
    import subprocess
    build = (Path(__file__).resolve().parents[1] / "scripts" / "build.py").read_text(encoding="utf-8")
    html = re.search(r'_hit_js = r"""(.*?)"""', build, re.S).group(1)
    snippet = html[len("<script>"):html.rfind("</script>")]
    stub = (
        "const store={}; globalThis.localStorage={getItem:k=>k in store?store[k]:null,setItem:(k,v)=>{store[k]=String(v)}};"
        "let url=null, sent=[];"
        "globalThis.location={pathname:'/trips',search:" + json.dumps(search) + ",hash:'#t1'};"
        "globalThis.history={state:null,replaceState:(s,t,u)=>{url=u}};"
        "Object.defineProperty(globalThis,'navigator',{value:{userAgent:'UA',language:'ru',sendBeacon:(u,b)=>sent.push(JSON.parse(b))" + ua_data + "}});"
        "globalThis.screen={width:1,height:2}; globalThis.window=globalThis;"
        "globalThis.document={referrer:'',visibilityState:'visible',addEventListener(){}};"
        "globalThis.addEventListener=()=>{};"
    )
    tail = ";setTimeout(()=>console.log(JSON.stringify([sent[0], store.fsq_owner||null, url, sent.length])),50)"
    out = subprocess.run([node, "-e", stub + snippet + tail], capture_output=True, text=True, check=True).stdout
    return json.loads(out)[:3] if not ua_data else json.loads(out)


def test_hit_beacon_sends_utm_tags():
    node = _node_or_skip()
    beacon, owner, url = _run_beacon(node, "?utm_source=telegram&utm_medium=post&utm_campaign=oct&x=1")
    assert beacon["u"] == "source=telegram&medium=post&campaign=oct" and beacon["o"] == 0
    assert owner is None and url is None                          # nothing to strip, flag untouched
    beacon, _, _ = _run_beacon(node, "")
    assert beacon["u"] == ""


def test_hit_beacon_owner_secret_mark():
    node = _node_or_skip()
    beacon, owner, url = _run_beacon(node, "?fsq_owner=1&utm_source=x")
    assert owner == "1" and beacon["o"] == 1                      # this very view is already the owner's
    assert url == "/trips?utm_source=x#t1"                        # the mark leaves the address bar
    beacon, owner, url = _run_beacon(node, "?fsq_owner=0")
    assert owner == "0" and beacon["o"] == 0 and url == "/trips#t1"


def test_visits_api_builds_sessions_and_sources(tmp_path):
    import subprocess
    node = _node_or_skip()
    src = (Path(__file__).resolve().parents[1] / "functions" / "api" / "visits.js").read_text(encoding="utf-8")
    mod = tmp_path / "visits.mjs"
    mod.write_text(src + "\nexport { buildSessions };\n", encoding="utf-8")
    rows = [  # newest first, as the query returns them
        {"id": 6, "ts": 9000, "path": "/", "ref": None, "vid": "b", "dur": None},
        {"id": 5, "ts": 1300, "path": "/venue#x", "ref": "https://4sq.pages.dev/trips", "vid": "a", "dur": 30, "model": "ELI-NX9"},
        {"id": 4, "ts": 1200, "path": "/trips", "ref": "https://t.me/", "vid": "a", "dur": 20},
        {"id": 3, "ts": 1100, "path": "/", "ref": None, "vid": "c", "utm": "source=tg&medium=post", "dur": None},
        {"id": 2, "ts": 1000, "path": "/", "ref": "https://4sq.pages.dev/", "vid": "b", "dur": 5},
        {"id": 1, "ts": -1000, "path": "/", "ref": None, "vid": "a", "dur": 4},
    ]
    js = ("import { buildSessions } from " + json.dumps(mod.as_uri()) + ";"
          "console.log(JSON.stringify(buildSessions(" + json.dumps(rows) + ", '4sq.pages.dev')));")
    out = json.loads(subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, check=True).stdout)
    by = {(s["who"], s["start"]): s for s in out["list"]}
    a2 = by[("a", 1200)]                                          # 2200 s gap after ts -1000 → a new session
    assert [p["path"] for p in a2["pages"]] == ["/trips", "/venue#x"]
    assert a2["seconds"] == 130 and a2["visible_seconds"] == 50 and a2["model"] == "ELI-NX9"
    assert a2["source"] == {"kind": "referrer", "label": "t.me", "ref": "https://t.me/"}
    assert by[("c", 1100)]["source"]["label"] == "tg / post"
    assert by[("b", 1000)]["source"]["kind"] == "internal" and by[("b", 9000)]["source"]["kind"] == "direct"
    assert [s["start"] for s in out["list"]] == sorted((s["start"] for s in out["list"]), reverse=True)
    tally = {(s["kind"], s["source"]): s["sessions"] for s in out["sources"]}
    assert tally == {("direct", "direct"): 2, ("referrer", "t.me"): 1, ("utm", "tg / post"): 1, ("internal", "internal"): 1}


def test_visits_clear_needs_key_and_confirm_word(tmp_path):
    """POST /api/visits wipes the table only with STATS_KEY and the literal confirm word."""
    import subprocess
    node = _node_or_skip()
    src = (Path(__file__).resolve().parents[1] / "functions" / "api" / "visits.js").read_text(encoding="utf-8")
    mod = tmp_path / "visits.mjs"
    mod.write_text(src, encoding="utf-8")
    js = ("import { onRequestPost } from " + json.dumps(mod.as_uri()) + ";"
          "const seen=[]; const env={STATS_KEY:'k', DB:{prepare:s=>({bind:(...a)=>({run:async()=>{seen.push([s,a.length]);return {meta:{changes:7}}}})})}};"
          "const req=(key,body)=>new Request('https://x/api/visits',{method:'POST',headers:key?{'X-Stats-Key':key}:{},body:JSON.stringify(body)});"
          "const out=[];"
          "for (const [k,b] of [[null,{action:'clear',confirm:'clear'}],['bad',{action:'clear',confirm:'clear'}],['k',{action:'clear'}],['k',{action:'clear',confirm:'clear'}]]) {"
          "  const r=await onRequestPost({request:req(k,b),env}); out.push([r.status, await r.json()]); }"
          "console.log(JSON.stringify({out, seen}));")
    res = json.loads(subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert [s for s, _ in res["out"]] == [401, 401, 400, 200]
    assert res["out"][3][1]["deleted"] == 7
    assert res["seen"] == [["DELETE FROM visits WHERE ts <= ?", 1],    # only the confirmed call touched D1
                           ["DELETE FROM visit_events WHERE ts <= ?", 1]]


def test_hit_beacon_writes_the_model_with_the_view():
    """The view waits for client hints, so the row has the model at once — no second beacon."""
    node = _node_or_skip()
    hints = ",userAgentData:{mobile:true,getHighEntropyValues:async()=>({model:'ELI-NX9'})}"
    beacon, _, _, n = _run_beacon(node, "", hints)
    assert beacon["m"] == "ELI-NX9" and "t" not in beacon and n == 1
    refused = ",userAgentData:{mobile:true,getHighEntropyValues:()=>Promise.reject(new Error('no'))}"
    beacon, _, _, n = _run_beacon(node, "", refused)
    assert beacon["m"] == "" and n == 1


def test_model_codes_get_marketing_names():
    assert vr.model_name("ELI-NX9") == "HONOR 200 (ELI-NX9)"
    assert vr.model_name("SM-S928B") == "Samsung Galaxy S24 Ultra (SM-S928B)"
    assert vr.model_name("Pixel 8") == "Pixel 8"                  # already readable: not in the table
    assert vr.model_name("XYZ-UNKNOWN") == "XYZ-UNKNOWN" and vr.model_name(None) == ""


def test_device_names_table_from_play_csv(tmp_path):
    import update_device_names as udn
    text = ('Retail Branding,Marketing Name,Device,Model\n'
            '"Honor","HONOR 200","HNELIX","ELI-NX9"\n'
            '"Samsung","Galaxy S24 Ultra","e3q","SM-S928B"\n'
            '"Google","Pixel 8","shiba","Pixel 8"\n'
            '"","","FJL21","FJL21"\n')
    assert udn.build_map(text) == {"ELI-NX9": "HONOR 200", "SM-S928B": "Samsung Galaxy S24 Ultra"}
    assert udn.decode(text.encode("utf-16")) == text
    small = tmp_path / "small.csv"
    small.write_text(text, encoding="utf-8")
    out = tmp_path / "names.json"
    assert udn.main(["--csv", str(small), "--out", str(out)]) == 1 and not out.exists()   # too few rows: refused


def test_visits_api_network_flags(tmp_path):
    import subprocess
    node = _node_or_skip()
    src = (Path(__file__).resolve().parents[1] / "functions" / "api" / "visits.js").read_text(encoding="utf-8")
    mod = tmp_path / "visits.mjs"
    mod.write_text(src + "\nexport { netFlags, netSummary };\n", encoding="utf-8")
    oct25 = 1792940000            # Chisinau already back on UTC+2, Minsk stays UTC+3
    rows = [
        {"id": 1, "ts": oct25, "vid": "a", "country": "MD", "asn": 31252, "org": "SC STARNET SRL", "tz": "Europe/Chisinau", "dtz": "Europe/Minsk"},
        {"id": 2, "ts": oct25, "vid": "b", "country": "NL", "asn": 9009, "org": "M247 Europe SRL", "tz": "Europe/Amsterdam", "dtz": "Europe/Chisinau"},
        {"id": 3, "ts": oct25, "vid": "c", "country": "MD", "asn": 13335, "org": "Cloudflare, Inc.", "tz": "Europe/Chisinau", "dtz": "Europe/Chisinau"},
        {"id": 4, "ts": oct25, "vid": "d", "country": "MD", "asn": 25454, "org": "Orange Moldova Network", "tz": "Europe/Chisinau", "dtz": "Europe/Chisinau"},
        {"id": 5, "ts": oct25, "vid": "e", "country": "DE", "asn": 1, "org": "Some Hosting GmbH", "tz": "Europe/Berlin", "dtz": None},
    ]
    js = ("import { netFlags, netSummary } from " + json.dumps(mod.as_uri()) + ";"
          "const rows=" + json.dumps(rows) + ";"
          "console.log(JSON.stringify({flags: rows.map(netFlags), sum: netSummary(rows)}));")
    out = json.loads(subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out["flags"] == [["tz"], ["vpn", "tz"], ["relay"], [], ["vpn"]]
    ctry = {c["country"]: c for c in out["sum"]["countriesClean"]}
    assert ctry["MD"]["n"] == 2 and ctry["MD"]["hidden"] == 1 and ctry["NL"]["n"] == 0 and ctry["NL"]["hidden"] == 1
    flags = {f["flag"]: f["views"] for f in out["sum"]["networkFlags"]}
    assert flags == {"tz": 2, "vpn": 2, "relay": 1, "clean": 1}
    old = [{"id": 9, "ts": oct25, "vid": "z", "country": "MD", "asn": 25454, "org": "Orange", "tz": "Europe/Chisinau", "dtz": None}]
    js2 = ("import { netSummary } from " + json.dumps(mod.as_uri()) + ";console.log(JSON.stringify(netSummary(" + json.dumps(old) + ")));")
    out2 = json.loads(subprocess.run([node, "--input-type=module", "-e", js2], capture_output=True, text=True, check=True).stdout)
    assert [f["flag"] for f in out2["networkFlags"]] == ["no-tz"]                # unchecked is not "clean"


def test_hit_beacon_events_scroll_and_vitals():
    """search queries after a 2 s pause, outbound clicks, and scroll/vitals on the leave beacon."""
    import subprocess
    node = _node_or_skip()
    build = (Path(__file__).resolve().parents[1] / "scripts" / "build.py").read_text(encoding="utf-8")
    html = re.search(r'_hit_js = r"""(.*?)"""', build, re.S).group(1)
    snippet = html[len("<script>"):html.rfind("</script>")]
    stub = (
        "const store={}; globalThis.localStorage={getItem:k=>k in store?store[k]:null,setItem:(k,v)=>{store[k]=String(v)}};"
        "const sent=[], docL={}, winL={};"
        "globalThis.location={pathname:'/',search:'',hash:'',host:'4sq.pages.dev'};"
        "globalThis.history={replaceState(){}};"
        "Object.defineProperty(globalThis,'navigator',{value:{userAgent:'UA',language:'ru',sendBeacon:(u,b)=>sent.push(JSON.parse(b))}});"
        "globalThis.screen={width:1,height:2}; globalThis.window=globalThis; globalThis.innerHeight=500; globalThis.scrollY=0;"
        "globalThis.document={referrer:'',visibilityState:'visible',documentElement:{scrollHeight:2000,clientHeight:500,scrollTop:0},body:{scrollHeight:2000},"
        "  addEventListener:(t,f)=>{(docL[t]=docL[t]||[]).push(f)}};"
        "globalThis.addEventListener=(t,f)=>{(winL[t]=winL[t]||[]).push(f)};"
    )
    drive = (
        ";const input={id:'gsInput',value:'Kyiv',matches:s=>s.includes('#gsInput')};"
        "docL.input.forEach(f=>f({target:input}));"
        "const link={href:'https://t.me/x',closest:()=>link}; docL.click.forEach(f=>f({target:{closest:()=>link}}));"
        "const own={href:'https://4sq.pages.dev/trips'}; docL.click.forEach(f=>f({target:{closest:()=>own}}));"
        "globalThis.scrollY=1000; winL.scroll.forEach(f=>f());"
        "setTimeout(()=>{winL.pagehide.forEach(f=>f()); console.log(JSON.stringify(sent));},2100);"
    )
    sent = json.loads(subprocess.run([node, "-e", stub + snippet + drive], capture_output=True, text=True, check=True, timeout=30).stdout)
    evs = [s for s in sent if s.get("t") == "ev"]
    assert [(e["e"], e["x"]) for e in evs] == [("out", "https://t.me/x"), ("search", "gsInput:Kyiv")]   # own-host link skipped
    leave = [s for s in sent if s.get("t") == "leave"][-1]
    assert leave["sc"] == 75 and leave["dur"] >= 0


def test_visits_api_people_vitals_and_now(tmp_path):
    import subprocess
    node = _node_or_skip()
    src = (Path(__file__).resolve().parents[1] / "functions" / "api" / "visits.js").read_text(encoding="utf-8")
    mod = tmp_path / "visits.mjs"
    mod.write_text(src + "\nexport { personMap, vitalsTable, nowOnSite, buildSessions };\n", encoding="utf-8")
    rows = [  # newest first
        {"id": 3, "ts": 300, "path": "/", "vid": "new", "fp": "F", "ip": "1.1.1.1"},
        {"id": 2, "ts": 200, "path": "/", "vid": "other", "fp": "F", "ip": "2.2.2.2"},     # same fp, other IP: not merged
        {"id": 1, "ts": 100, "path": "/", "vid": "old", "fp": "F", "ip": "1.1.1.1"},
    ]
    vit = [{"path": "/", "lcp": v, "inp": None, "cls": 50, "ttfb": 100} for v in (1000, 2000, 3000, 4000)] + \
          [{"path": "/trips", "lcp": 900, "inp": 300, "cls": None, "ttfb": None}]
    js = ("import { personMap, vitalsTable, nowOnSite, buildSessions } from " + json.dumps(mod.as_uri()) + ";"
          "const rows=" + json.dumps(rows) + ";"
          "console.log(JSON.stringify({p:Object.fromEntries(personMap(rows)), v:vitalsTable(" + json.dumps(vit) + "),"
          " n:nowOnSite(rows), s:buildSessions(rows,'x')}));")
    out = json.loads(subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out["p"] == {"new": "old", "other": "other", "old": "old"}
    assert out["s"]["people"] == 2 and out["s"]["merged"] == 1
    allp = out["v"][0]
    assert allp["path"] == "(all pages)" and allp["views"] == 5 and allp["lcp"] == 3000 and allp["inp"] == 300
    home = next(r for r in out["v"] if r["path"] == "/")
    assert home["lcp"] == 3000 and home["lcp_n"] == 4 and home["inp"] is None and home["cls"] == 50
    assert [n["who"] for n in out["n"]] == ["new", "other", "old"]


def test_hit_alerts_and_events_are_guarded():
    js = (Path(__file__).resolve().parents[1] / "functions" / "api" / "hit.js").read_text(encoding="utf-8")
    assert "env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID" in js and "!body.o" in js     # owner never alerts
    assert "waitUntil(job)" in js and "EVENT_KINDS.includes(body.e)" in js and "MAX_EVENTS" in js
    assert "DELETE FROM visit_events WHERE ts < ?" in js


def test_hit_sends_one_alert_for_a_new_country(tmp_path):
    import subprocess
    node = _node_or_skip()
    src = (Path(__file__).resolve().parents[1] / "functions" / "api" / "hit.js").read_text(encoding="utf-8")
    mod = tmp_path / "hit.mjs"
    mod.write_text(src, encoding="utf-8")
    js = ("import { onRequestPost } from " + json.dumps(mod.as_uri()) + ";"
          "const sql=[], tg=[]; globalThis.fetch=async (u,o)=>{tg.push(JSON.parse(o.body).text);return {ok:true}};"
          "const db={prepare:s=>({bind:(...a)=>({first:async()=>{sql.push(s);return s.includes('COUNT')?{n:0}:(s.includes('country = ?')?null:{x:1})},"
          "  run:async()=>{sql.push(s);return {}}})})};"
          "const jobs=[]; const call=(o,secret)=>onRequestPost({env:{DB:db,...(secret?{TELEGRAM_BOT_TOKEN:'t',TELEGRAM_CHAT_ID:'1'}:{})},waitUntil:j=>jobs.push(j),"
          "  request:Object.assign(new Request('https://4sq.pages.dev/api/hit',{method:'POST',headers:{Origin:'https://4sq.pages.dev','User-Agent':'Mozilla','CF-Connecting-IP':'9.9.9.9'},"
          "  body:JSON.stringify({p:'/',v:'abcdefgh12',k:'view12345a',o})}),{cf:{country:'JP',city:'Tokyo',asOrganization:'NTT'}})});"
          "await call(0,true); await Promise.all(jobs); const a=tg.length;"
          "await call(1,true); await Promise.all(jobs); const b=tg.length;"            # owner: no alert
          "await call(0,false); await Promise.all(jobs);"                            # no secrets: no alert, no lookups
          "console.log(JSON.stringify({a,b,c:tg.length,text:tg[0],lookups:sql.filter(s=>s.includes('LIMIT 1')).length}));")
    out = json.loads(subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out["a"] == 1 and out["b"] == 1 and out["c"] == 1
    assert "new country JP" in out["text"] and "Tokyo, JP · NTT" in out["text"]
    assert out["lookups"] == 1                                                    # only the first, alert-enabled view looked
