-- Migration 0011: source_reliability table for META-008
-- Auto-retraction: cross-sprint source reliability tracking with contradiction ratio.
-- Bounded: 256 tracked sources, LRU eviction.

CREATE TABLE IF NOT EXISTS source_reliability (
    source_id              TEXT PRIMARY KEY,
    total_claims          INTEGER NOT NULL DEFAULT 0,
    contradiction_count    INTEGER NOT NULL DEFAULT 0,
    ratio                  REAL NOT NULL DEFAULT 0.0,
    last_updated          DOUBLE NOT NULL,
    auto_retracted        BOOLEAN NOT NULL DEFAULT FALSE,
    auto_retracted_at     DOUBLE,
    sprint_id             TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_reliability_ratio
    ON source_reliability(ratio DESC);

CREATE INDEX IF NOT EXISTS idx_source_reliability_updated
    ON source_reliability(last_updated DESC);
