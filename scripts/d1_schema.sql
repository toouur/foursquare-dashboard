-- Cloudflare D1 schema for swarmdata
-- Run via import_to_d1.py (full recreate) or sync_to_d1.py (IF NOT EXISTS)

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
    overlaps_id      TEXT
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
    field       TEXT    NOT NULL,   -- 'venue' | 'city' | 'country' | 'lat' | 'lng' | 'category'
    old_value   TEXT,
    new_value   TEXT,
    detected_at INTEGER NOT NULL,   -- unix timestamp of snapshot diff
    PRIMARY KEY (venue_id, field, detected_at)
);

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
