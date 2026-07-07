-- Audímetro schema (SQLite). Idempotente.

CREATE TABLE IF NOT EXISTS videos (
    platform     TEXT NOT NULL,           -- 'youtube' | 'instagram' | 'tiktok'
    video_id     TEXT NOT NULL,
    kind         TEXT NOT NULL,           -- 'video' | 'short' | 'reel'
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    published_at TEXT,
    first_seen   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen    TEXT NOT NULL,
    PRIMARY KEY (platform, video_id)
);

CREATE TABLE IF NOT EXISTS snapshots (
    platform    TEXT NOT NULL,
    video_id    TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    views       INTEGER NOT NULL,
    likes       INTEGER,
    comments    INTEGER,
    shares      INTEGER,
    saves       INTEGER,
    watch_min   INTEGER,             -- solo YouTube con Analytics
    avg_view_s  INTEGER,
    subs_gained INTEGER,
    PRIMARY KEY (platform, video_id, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_captured ON snapshots(captured_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS config_overrides (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
