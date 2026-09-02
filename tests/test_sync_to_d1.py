# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Offline tests for scripts/sync_to_d1.py (no network, no secrets).

The real sync talks to Cloudflare D1 over HTTP via the `d1_client` module. Here
we swap that module out for an in-memory **sqlite3** backend (`FakeD1`) — sqlite
speaks the same `?`-placeholder dialect and the same `INSERT OR REPLACE` /
`INSERT OR IGNORE` grammar the sync emits, so `main()` runs verbatim against a
real (tiny, local) database. That lets us assert on actual table state after a
sync instead of mocking call arguments.

The high-value coverage is the reconciliation logic behind BUG-013
(`qa/bug-reports/BUG-013-d1-watermark-sync-drift.md`):
  * the MAX(date) watermark fast path inserts strictly-newer rows;
  * a backdated row (ts <= watermark, new checkin_id) is still inserted via the
    count-mismatch checkin_id set-difference fallback — the defect that silently
    dropped backdated check-ins forever;
  * venues reconcile in BOTH directions — a CSV venue missing from D1 is
    inserted, an orphan venue absent from the CSV is deleted with an audit row;
  * the --force-* flags DELETE and fully reinsert.

Plus straight unit tests for the parse_* loaders (row shape + venue aggregation).

Runtime: well under a second. Unmarked, so it runs in `pytest -m "not live"`.
"""
import csv
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import d1_client
import sync_to_d1

SCHEMA = Path(__file__).resolve().parents[1] / "scripts" / "d1_schema.sql"


# ── in-memory fake for the d1_client module ─────────────────────────────────

class FakeD1:
    """sqlite3-backed stand-in exposing the subset of d1_client that
    sync_to_d1 calls. One shared :memory: connection per instance."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def configure(self, token):  # noqa: ARG002 — signature parity only
        pass

    def apply_schema(self, path):
        sql = Path(path).read_text(encoding="utf-8")
        # Mirror d1_client.apply_schema: strip -- comment lines BEFORE splitting
        # on ';' (a trailing comment would otherwise leak into the next fragment),
        # and swallow duplicate-column errors from the idempotent ALTER migrations.
        clean = "\n".join(ln for ln in sql.splitlines()
                          if not ln.strip().startswith("--"))
        for stmt in clean.split(";"):
            s = stmt.strip()
            if not s:
                continue
            try:
                self.conn.execute(s)
            except sqlite3.OperationalError as e:
                if s.upper().startswith("ALTER TABLE") and \
                        "duplicate column" in str(e).lower():
                    continue
                raise
        self.conn.commit()

    def query(self, sql, params=None, silent=False):  # noqa: ARG002
        cur = self.conn.execute(sql, params or [])
        if cur.description is None:      # non-SELECT (INSERT/UPDATE/DELETE/DDL)
            self.conn.commit()
            return []
        return [dict(r) for r in cur.fetchall()]

    def batch_upsert(self, sql, rows, chunk=0, label=""):  # noqa: ARG002
        if not rows:
            return 0
        self.conn.executemany(sql, rows)
        self.conn.commit()
        return len(rows)

    def raw_upsert(self, base_sql, rows, label=""):  # noqa: ARG002
        if not rows:
            return 0
        ncols = len(rows[0])
        ph = "(" + ",".join("?" * ncols) + ")"
        values = ",".join([ph] * len(rows))
        flat = [v for row in rows for v in row]
        self.conn.execute(f"{base_sql} {values}", flat)
        self.conn.commit()
        return len(rows)

    def _raw_with_retry(self, sql, retries=5):  # noqa: ARG002
        # sync emits ';'-joined literalized statements for this path.
        self.conn.executescript(sql)
        self.conn.commit()
        return []

    # -- test-side conveniences (not part of the d1_client interface) --------
    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]

    def ids(self, table, col="id"):
        return {r[col] for r in self.conn.execute(f"SELECT {col} FROM {table}").fetchall()}


def _install_fake(monkeypatch):
    fake = FakeD1()
    for name in ("configure", "apply_schema", "query",
                 "batch_upsert", "raw_upsert", "_raw_with_retry"):
        monkeypatch.setattr(d1_client, name, getattr(fake, name))
    # _sql_val stays the real (pure) implementation.
    return fake


# ── CSV / tips fixtures ─────────────────────────────────────────────────────

CSV_COLS = [
    "checkin_id", "date", "venue_id", "venue", "venue_url", "city", "state",
    "country", "neighborhood", "lat", "lng", "address", "category", "shout",
    "source_app", "source_url", "with_name", "with_id", "created_by_name",
    "created_by_id", "overlaps_name", "overlaps_id", "city_inferred",
]


def _crow(cid, ts, vid, venue="Venue", city="Minsk", country="Belarus",
          lat="53.9", lng="27.5", category="Café"):
    r = {c: "" for c in CSV_COLS}
    r.update(checkin_id=cid, date=str(ts), venue_id=vid, venue=venue, city=city,
             country=country, lat=lat, lng=lng, category=category,
             city_inferred="0")
    return r


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLS)
        w.writeheader()
        w.writerows(rows)


# A 5-row baseline across 3 venues: vA×2, vB×1, vC×2. max_date = 5000.
BASE_ROWS = [
    _crow("c1", 1000, "vA"),
    _crow("c2", 2000, "vA"),
    _crow("c3", 3000, "vB"),
    _crow("c4", 4000, "vC"),
    _crow("c5", 5000, "vC"),
]


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Installed fake D1 + a tmp dir + a tips.json (default path never parses it
    unless --tips-changed/--force-tips, but the arg is required)."""
    fake = _install_fake(monkeypatch)
    tips = tmp_path / "tips.json"
    tips.write_text("[]", encoding="utf-8")
    return SimpleNamespace(fake=fake, monkeypatch=monkeypatch,
                           tmp_path=tmp_path, tips=tips)


def _run(env, csv_path, *extra):
    argv = ["sync_to_d1.py", "--csv", str(csv_path), "--tips", str(env.tips),
            "--config-dir", "", "--token", "test", *extra]
    env.monkeypatch.setattr(sys, "argv", argv)
    sync_to_d1.main()


def _sync_base(env):
    csv_path = env.tmp_path / "checkins.csv"
    _write_csv(csv_path, BASE_ROWS)
    _run(env, csv_path)
    return csv_path


# ── end-to-end sync behaviour ───────────────────────────────────────────────

def test_initial_sync_inserts_all_checkins_and_aggregates_venues(env):
    _sync_base(env)
    assert env.fake.count("checkins") == 5
    assert env.fake.ids("venues") == {"vA", "vB", "vC"}
    # vC aggregation: 2 check-ins, first 4000, last 5000.
    vc = env.fake.conn.execute(
        "SELECT checkin_count, first_checkin_at, last_checkin_at "
        "FROM venues WHERE id='vC'").fetchone()
    assert vc["checkin_count"] == 2
    assert vc["first_checkin_at"] == 4000
    assert vc["last_checkin_at"] == 5000


def test_watermark_fast_path_inserts_strictly_newer_row(env):
    csv_path = _sync_base(env)
    _write_csv(csv_path, BASE_ROWS + [_crow("c6", 6000, "vB")])
    _run(env, csv_path)
    assert env.fake.count("checkins") == 6
    assert "c6" in env.fake.ids("checkins")


def test_backdated_row_inserted_via_set_difference(env):
    """BUG-013 #1: a check-in with ts <= the D1 watermark but a new checkin_id
    must still be inserted — the watermark alone would drop it forever."""
    csv_path = _sync_base(env)
    # ts 1500 <= max_date 5000, so the fast path finds it 'not newer'.
    _write_csv(csv_path, BASE_ROWS + [_crow("c0", 1500, "vB")])
    _run(env, csv_path)
    assert env.fake.count("checkins") == 6
    assert "c0" in env.fake.ids("checkins")


def test_orphan_venue_pruned_with_audit_row(env):
    """BUG-013 #2: a venue present in D1 but absent from the CSV aggregation is
    deleted, and an audit row lands in venue_changes."""
    csv_path = _sync_base(env)
    env.fake.conn.execute(
        "INSERT INTO venues (id, name) VALUES ('orphanX', 'Ghost Venue')")
    env.fake.conn.commit()
    assert "orphanX" in env.fake.ids("venues")

    # A second sync in the same UTC day is deliberately NON-census: _sync_base()
    # already cached daily_counts in sync_state, and the full venues reconcile
    # (a whole-table scan, ~20k rows read on real data) is contracted to run once
    # per day, not on every sync. So the orphan survives this run untouched.
    _run(env, csv_path)
    assert "orphanX" in env.fake.ids("venues")

    # Dropping the cached census row is what the next UTC day does; the reconcile
    # then runs and prunes the orphan.
    env.fake.conn.execute("DELETE FROM sync_state WHERE key='daily_counts'")
    env.fake.conn.commit()

    _run(env, csv_path)   # same CSV → orphanX is not in venue_meta
    assert env.fake.ids("venues") == {"vA", "vB", "vC"}
    audit = env.fake.conn.execute(
        "SELECT field, action FROM venue_changes WHERE venue_id='orphanX'"
    ).fetchone()
    assert audit is not None
    assert audit["field"] == "venue" and audit["action"] == "deleted"


def test_missing_venue_reinserted(env):
    """A venue whose check-ins are in the CSV but whose venues row is missing
    from D1 (e.g. surfaced via a backdated sync) is inserted on reconcile."""
    csv_path = _sync_base(env)
    env.fake.conn.execute("DELETE FROM venues WHERE id='vB'")
    env.fake.conn.commit()
    assert "vB" not in env.fake.ids("venues")

    # Same contract as the orphan prune: the missing-from-D1 arm needs the FULL
    # venue id set, so it only runs on the daily census. A second sync in the
    # same UTC day reuses the cached counts and probes only the venues its own
    # new check-ins touched — vB is not among them, so it stays missing.
    _run(env, csv_path)
    assert "vB" not in env.fake.ids("venues")

    # Next UTC day (cached census dropped) → full reconcile → vB comes back.
    env.fake.conn.execute("DELETE FROM sync_state WHERE key='daily_counts'")
    env.fake.conn.commit()

    _run(env, csv_path)
    assert "vB" in env.fake.ids("venues")
    assert env.fake.conn.execute(
        "SELECT checkin_count FROM venues WHERE id='vB'").fetchone()[0] == 1


def test_force_checkins_wipes_and_reinserts(env):
    csv_path = _sync_base(env)
    # Pollute D1 with a phantom row the CSV doesn't contain.
    env.fake.conn.execute(
        "INSERT INTO checkins (id, date, venue_id) VALUES ('zzz', 9999, 'vZ')")
    env.fake.conn.commit()
    assert env.fake.count("checkins") == 6

    _run(env, csv_path, "--force-checkins")
    assert env.fake.count("checkins") == 5
    assert "zzz" not in env.fake.ids("checkins")
    assert env.fake.ids("venues") == {"vA", "vB", "vC"}


def test_force_tips_reinserts_and_records_sync_hash(env):
    csv_path = _sync_base(env)
    tips = [
        {"id": "t1", "ts": 1000, "text": "Great coffee.",
         "venue": "Café", "venue_id": "vA", "city": "Minsk", "country": "Belarus"},
        {"id": "t2", "ts": 2000, "text": "Nice park.",
         "venue": "Park", "venue_id": "vB", "city": "Minsk", "country": "Belarus"},
    ]
    env.tips.write_text(json.dumps(tips, ensure_ascii=False), encoding="utf-8")

    _run(env, csv_path, "--force-tips")
    assert env.fake.count("tips") == 2
    # Content-hash gate recorded so the next unchanged run can skip.
    hashed = env.fake.conn.execute(
        "SELECT hash FROM sync_state WHERE key='tips'").fetchone()
    assert hashed is not None and hashed["hash"]


# ── parse_* loader unit tests (no D1) ───────────────────────────────────────

def test_parse_checkins_row_shape_and_aggregation(tmp_path):
    csv_path = tmp_path / "c.csv"
    _write_csv(csv_path, BASE_ROWS)
    rows, venue_meta = sync_to_d1.parse_checkins(str(csv_path))
    assert len(rows) == 5
    assert all(len(r) == 23 for r in rows)           # column contract
    assert rows[0][0] == "c1" and rows[0][1] == 1000  # cid, ts
    assert rows[0][2] == "vA"                          # venue_id
    assert set(venue_meta) == {"vA", "vB", "vC"}
    assert venue_meta["vA"]["count"] == 2
    assert venue_meta["vA"]["first_ts"] == 1000
    assert venue_meta["vA"]["last_ts"] == 2000


def test_parse_checkins_skips_rows_without_checkin_id(tmp_path):
    csv_path = tmp_path / "c.csv"
    rows_in = [_crow("c1", 1000, "vA"), _crow("", 2000, "vB")]
    _write_csv(csv_path, rows_in)
    rows, venue_meta = sync_to_d1.parse_checkins(str(csv_path))
    assert len(rows) == 1
    assert "vB" not in venue_meta


def test_parse_tips_shape(tmp_path):
    p = tmp_path / "tips.json"
    p.write_text(json.dumps([
        {"id": "t1", "ts": 1000, "text": "hi", "venue": "V", "venue_id": "vA",
         "city": "Minsk", "country": "Belarus", "agree_count": 3, "closed": True},
    ]), encoding="utf-8")
    rows = sync_to_d1.parse_tips(str(p))
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "t1" and row[1] == 1000
    assert row[10] == 3        # agree_count
    assert row[12] == 1        # closed → 1


def test_parse_ratings_maps_labels(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({
        "venueLikes": [{"id": "vA", "name": "A", "url": "u", "createdAt": 111}],
        "venueDislikes": [{"id": "vB", "name": "B", "url": "u2", "createdAt": 222}],
    }), encoding="utf-8")
    rows = sync_to_d1.parse_ratings(str(p))
    by_vid = {r[0]: r for r in rows}
    assert by_vid["vA"][3] == "like"
    assert by_vid["vB"][3] == "dislike"


def test_parse_trips_shape(tmp_path):
    p = tmp_path / "trips.json"
    p.write_text(json.dumps([
        {"id": 1, "name": "Warsaw", "start_date": "2019-07-10",
         "end_date": "2019-07-15", "start_ts": 100, "start_year": 2019,
         "duration": 5, "checkin_count": 6, "unique_places": 6,
         "countries": ["Poland"], "cities": ["Warsaw"], "tags": ["train"],
         "top_cats": [["Park", 2]]},
    ]), encoding="utf-8")
    rows = sync_to_d1.parse_trips(str(p))
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == 1 and row[1] == "Warsaw"
    assert json.loads(row[9]) == ["Poland"]    # countries serialized as JSON
    assert json.loads(row[12]) == [["Park", 2]]  # top_cats
