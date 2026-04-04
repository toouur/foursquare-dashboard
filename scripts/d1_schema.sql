-- Cloudflare D1 schema for swarmdata
-- Run via import_to_d1.py or sync_to_d1.py — idempotent (IF NOT EXISTS everywhere)

-- ── Core check-ins ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS checkins (
    id           TEXT    PRIMARY KEY,   -- checkin_id from CSV
    date         INTEGER NOT NULL,      -- unix timestamp
    venue_id     TEXT,
    venue        TEXT,
    city         TEXT,
    state        TEXT,
    country      TEXT,
    neighborhood TEXT,
    lat          REAL,
    lng          REAL,
    address      TEXT,
    category     TEXT,
    shout        TEXT,
    with_name    TEXT,
    with_id      TEXT
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

-- ── Venue ratings (like / neutral / dislike) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS ratings (
    venue_id   TEXT PRIMARY KEY,
    venue_name TEXT,
    rating     TEXT,               -- 'like' | 'neutral' | 'dislike'
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
    list_id       TEXT    NOT NULL,
    venue_id      TEXT    NOT NULL,
    venue_name    TEXT,
    category      TEXT,
    lat           REAL,
    lng           REAL,
    city          TEXT,
    country       TEXT,
    visited       INTEGER DEFAULT 0,
    last_visit_ts INTEGER DEFAULT 0,
    PRIMARY KEY (list_id, venue_id)
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_checkins_venue_id ON checkins(venue_id);
CREATE INDEX IF NOT EXISTS idx_checkins_date     ON checkins(date);
CREATE INDEX IF NOT EXISTS idx_checkins_city     ON checkins(city);
CREATE INDEX IF NOT EXISTS idx_tips_venue_id     ON tips(venue_id);
CREATE INDEX IF NOT EXISTS idx_list_venues_list  ON list_venues(list_id);
CREATE INDEX IF NOT EXISTS idx_ratings_rating    ON ratings(rating);
