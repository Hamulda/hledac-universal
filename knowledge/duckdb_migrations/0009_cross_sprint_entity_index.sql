-- 0009_cross_sprint_entity_index.sql
-- [META-002]: Cross-sprint entity confirmation tracking
--
-- Problem: Each sprint is amnesiac — entity_observations tracks per-sprint
-- observations but no aggregated cross-sprint view survives sprint teardown.
-- After 3 sprints on the same threat actor, the 4th sprint re-verifies
-- the same domains/IPs/hashes from scratch (5-8 min waste per sprint).
--
-- Solution: cross_sprint_entity_index aggregates confirmed entities across
-- sprints into a single dedup index with confirmation counts and content hashes.
--
-- M1 8GB: Table grows ~1 row per unique entity per unique IOC type confirmed.
-- Realistic max ~50K rows = ~8 MB. Bounded via confirmation_count TTL:
-- entities with confirmation_count=1 and last_seen > 90 days are candidates
-- for eviction (handled by DeltaSyncEngine.sync() at winddown).

CREATE TABLE IF NOT EXISTS cross_sprint_entity_index (
    entity_value             TEXT    NOT NULL,
    ioc_type                 TEXT    NOT NULL,
    confirmation_count       INTEGER NOT NULL DEFAULT 1,
    -- DuckDB TEXT[] with array literal default (not quoted string)
    last_confirmed_sprint    TEXT[]  NOT NULL DEFAULT [],
    first_seen_sprint        TEXT    NOT NULL DEFAULT '',
    sha256_content_hash      TEXT,
    last_confirmed_ts        DOUBLE  NOT NULL DEFAULT 0.0,
    avg_confidence           REAL    NOT NULL DEFAULT 0.0,
    UNIQUE (entity_value, ioc_type)
);

-- Fast entity lookup by value (hash index, O(1))
CREATE INDEX IF NOT EXISTS idx_cross_sprint_entity_value
    ON cross_sprint_entity_index(entity_value);

-- Note: DuckDB does NOT support GIN indexes. We use a standard B-tree index
-- on last_confirmed_sprint; DuckDB's list functions (list_has, list_has_all)
-- work efficiently with this index for array containment queries.
CREATE INDEX IF NOT EXISTS idx_cross_sprint_confirmed_sprints
    ON cross_sprint_entity_index(last_confirmed_sprint);

-- confirmation_count DESC for high-value entity lookup
CREATE INDEX IF NOT EXISTS idx_cross_sprint_confirmations
    ON cross_sprint_entity_index(confirmation_count DESC);

-- last_confirmed_ts ASC for LRU eviction candidate selection
CREATE INDEX IF NOT EXISTS idx_cross_sprint_last_seen
    ON cross_sprint_entity_index(last_confirmed_ts ASC);
