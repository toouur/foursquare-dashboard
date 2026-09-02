-- Cloudflare D1 schema for swarmdata
-- Applied by sync_to_d1.py via d1_client.apply_schema() (all statements IF NOT EXISTS).

-- ── Core check-ins ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS checkins (
    seq              INTEGER PRIMARY KEY,   -- auto-increment row order (preserves CSV duplicates)
    id               TEXT    NOT NULL,      -- checkin_id from CSV (not unique — duplicates allowed)
    date             INTEGER NOT NULL,      -- unix timestamp
    venue_id         TEXT,
    venue            TEXT,
    venue_url        TEXT,
    city             TEXT,
    state            TEXT,
    country          TEXT,
    neighborhood     TEXT,
    lat              REAL,
    lng              REAL,
    address          TEXT,
    category         TEXT,
    shout            TEXT,
    source_app       TEXT,
    source_url       TEXT,
    with_name        TEXT,
    with_id          TEXT,
    created_by_name  TEXT,
    created_by_id    TEXT,
    overlaps_name    TEXT,
    overlaps_id      TEXT,
    city_inferred    INTEGER DEFAULT 0   -- 1 = filled by blank-city resolver
);

-- ── Unique venues (aggregated from checkins) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS venues (
    id               TEXT    PRIMARY KEY,   -- venue_id
    name             TEXT,
    category         TEXT,
    lat              REAL,
    lng              REAL,
    city             TEXT,
    country          TEXT,
    checkin_count    INTEGER DEFAULT 0,
    first_checkin_at INTEGER,
    last_checkin_at  INTEGER
);

-- ── Tips ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tips (
    id             TEXT    PRIMARY KEY,
    ts             INTEGER,
    text           TEXT,
    venue          TEXT,
    venue_id       TEXT,
    city           TEXT,
    country        TEXT,
    lat            REAL,
    lng            REAL,
    category       TEXT,
    agree_count    INTEGER DEFAULT 0,
    disagree_count INTEGER DEFAULT 0,
    closed         INTEGER DEFAULT 0,
    view_count     INTEGER DEFAULT 0
);

-- ── Venue ratings (like / okay / dislike) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS ratings (
    venue_id   TEXT PRIMARY KEY,
    venue_name TEXT,
    venue_url  TEXT,
    rating     TEXT,               -- 'like' | 'okay' | 'dislike'
    created_at INTEGER DEFAULT 0
);

-- ── Lists ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lists (
    id         TEXT PRIMARY KEY,
    name       TEXT,
    url        TEXT,
    cover      TEXT,
    updated_at INTEGER DEFAULT 0
);

-- ── List venue membership ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS list_venues (
    list_id                TEXT    NOT NULL,
    venue_id               TEXT    NOT NULL,
    created_at             INTEGER DEFAULT 0,
    venue_name             TEXT,
    venue_url              TEXT,
    category               TEXT,
    category_id            TEXT,
    category_short_name    TEXT,
    category_icon_prefix   TEXT,
    category_icon_suffix   TEXT,
    lat                    REAL,
    lng                    REAL,
    address                TEXT,
    city                   TEXT,
    state                  TEXT,
    cc                     TEXT,
    country                TEXT,
    formatted_address      TEXT,
    visited                INTEGER DEFAULT 0,
    last_visit_ts          INTEGER DEFAULT 0,
    PRIMARY KEY (list_id, venue_id)
);

-- ── Trips (computed from checkins via metrics.py) ─────────────────────────────
CREATE TABLE IF NOT EXISTS trips (
    id            INTEGER PRIMARY KEY,   -- sequential, 1-based (same as trips.html order)
    name          TEXT,
    start_date    TEXT,                  -- ISO date "YYYY-MM-DD"
    end_date      TEXT,                  -- ISO date "YYYY-MM-DD"
    start_ts      INTEGER,               -- unix timestamp of first check-in
    start_year    INTEGER,
    duration      INTEGER,               -- days
    checkin_count INTEGER DEFAULT 0,
    unique_places INTEGER DEFAULT 0,
    countries     TEXT,                  -- JSON array e.g. ["Italy","France"]
    cities        TEXT,                  -- JSON array e.g. ["Rome","Paris"]
    tags          TEXT,                  -- JSON array e.g. ["bicycle"]
    top_cats      TEXT                   -- JSON array of [category, count] pairs
);

-- ── Venue change history (from sync_venue_changes.py diffs) ─────────────────
CREATE TABLE IF NOT EXISTS venue_changes (
    venue_id    TEXT    NOT NULL,
    field       TEXT    NOT NULL,   -- 'venue' | 'city' | 'country' | 'lat' | 'lng' | 'category' | 'venue_id'
    old_value   TEXT,
    new_value   TEXT,
    detected_at INTEGER NOT NULL,   -- unix timestamp of snapshot diff
    venue_name  TEXT,               -- human-readable venue name at time of change
    action      TEXT,               -- 'renamed' | 'relocated' | 'recategorized' | 'merged' | 'updated'
    PRIMARY KEY (venue_id, field, detected_at)
);

-- Migrations: add new columns to existing venue_changes tables
-- (safe to run repeatedly -- apply_schema suppresses duplicate-column errors)
ALTER TABLE venue_changes ADD COLUMN venue_name TEXT;
ALTER TABLE venue_changes ADD COLUMN action TEXT;

-- Migrations: add new columns to existing checkins tables
ALTER TABLE checkins ADD COLUMN city_inferred INTEGER DEFAULT 0;

-- ── Sync state (content-hash gate for trips/lists/tips/ratings) ─────────────
-- One row per synced table; sync_to_d1.py skips the D1 write when the sha256
-- of the parsed rows matches the stored hash (avoids hourly re-sync of
-- unchanged trips/lists driven by the check-ins fetch flag).
CREATE TABLE IF NOT EXISTS sync_state (
    key        TEXT    PRIMARY KEY,   -- 'tips' | 'ratings' | 'trips' | 'lists'
    hash       TEXT,                  -- sha256 hex of parsed rows last written
    updated_at INTEGER                -- unix ts of last write
);

-- ── Full-text search (FTS5) ──────────────────────────────────────────────────
-- External-content FTS5 indexes: they store only the inverted index and read
-- display columns from the base table via rowid (venues/tips/trips are ordinary
-- rowid tables even though their PK is TEXT/INTEGER). Populated by sync_to_d1.py
-- with `INSERT INTO <t>_fts(<t>_fts) VALUES('rebuild')` after the base table is
-- written (rebuild-on-change — no triggers, because apply_schema splits on ';'
-- and would shred a BEGIN..END trigger body). search.js queries these via MATCH
-- + bm25(). Each is a single statement with no internal ';' so it survives the
-- split-on-';' parser in d1_client.apply_schema().
-- Companion name -> check-in count, precomputed by sync_to_d1.build_companion_rows().
-- /api/search used to answer a companion query with three unindexed LIKE scans of the
-- 70k-row checkins table (~210k rows READ per keystroke-debounced request, on an
-- endpoint that is Cache-Control: no-store). This table is a few hundred rows, so the
-- same lookup scans a few hundred. No secondary index on name_lc on purpose: a
-- leading-wildcard LIKE cannot use one, and every index costs rows WRITTEN on sync.
CREATE TABLE IF NOT EXISTS companions (
    name    TEXT PRIMARY KEY,
    name_lc TEXT    NOT NULL,
    cnt     INTEGER NOT NULL DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS venues_fts USING fts5(name, category, city, country, content='venues', content_rowid='rowid');
CREATE VIRTUAL TABLE IF NOT EXISTS tips_fts USING fts5(venue, text, city, country, content='tips', content_rowid='rowid');
CREATE VIRTUAL TABLE IF NOT EXISTS trips_fts USING fts5(name, countries, cities, content='trips', content_rowid='rowid');

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_checkins_id       ON checkins(id);
CREATE INDEX IF NOT EXISTS idx_checkins_venue_id ON checkins(venue_id);
CREATE INDEX IF NOT EXISTS idx_checkins_date     ON checkins(date);
CREATE INDEX IF NOT EXISTS idx_checkins_city     ON checkins(city);
CREATE INDEX IF NOT EXISTS idx_tips_venue_id     ON tips(venue_id);
CREATE INDEX IF NOT EXISTS idx_list_venues_list  ON list_venues(list_id);
CREATE INDEX IF NOT EXISTS idx_ratings_rating    ON ratings(rating);
CREATE INDEX IF NOT EXISTS idx_trips_start_ts    ON trips(start_ts);
CREATE INDEX IF NOT EXISTS idx_trips_name        ON trips(name);
