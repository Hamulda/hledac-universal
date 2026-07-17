-- 0001_init_canonical_schema.sql
-- P0-8: Initial schema — all 14 tables and 22 indexes from _SCHEMA_SQL
-- This migration creates the full canonical schema on a fresh DB.
-- For legacy DBs (pre-migration), version 1 is bootstrapped as already applied
-- so this file is skipped. New installs get the full schema.

-- Sprint F350M: Schema version tracking table (created first so subsequent
-- migrations can record themselves).
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  DOUBLE,
    description TEXT
);

CREATE TABLE IF NOT EXISTS canonical_findings (
    id              VARCHAR PRIMARY KEY,
    query           VARCHAR,
    source_type     VARCHAR,
    confidence      DOUBLE,
    ts              DOUBLE,
    provenance_json TEXT,
    payload_text    TEXT,
    UNIQUE (id),
    UNIQUE (query, source_type)
);
-- Sprint STORAGE-FIX-1: time-range + per-query lookups
CREATE INDEX IF NOT EXISTS idx_canonical_findings_ts ON canonical_findings(ts DESC);
CREATE INDEX IF NOT EXISTS idx_canonical_findings_query ON canonical_findings(query);

CREATE TABLE IF NOT EXISTS shadow_runs (
    run_id      VARCHAR PRIMARY KEY,
    started_at  TIMESTAMP,
    ended_at    TIMESTAMP,
    total_fds   INTEGER,
    rss_mb      INTEGER
);

CREATE TABLE IF NOT EXISTS sprint_delta (
    sprint_id TEXT PRIMARY KEY,
    ts DOUBLE NOT NULL,
    query TEXT,
    duration_s REAL DEFAULT 0,
    new_findings INT DEFAULT 0,
    dedup_hits INT DEFAULT 0,
    ioc_nodes INT DEFAULT 0,
    ioc_new_this_sprint INT DEFAULT 0,
    uma_peak_gib REAL DEFAULT 0,
    synthesis_success BOOL DEFAULT false,
    findings_per_minute REAL DEFAULT 0,
    top_source_type TEXT,
    synthesis_confidence REAL DEFAULT 0
);
-- Index for ORDER BY ts DESC queries (scoreboard, recent sprints)
CREATE INDEX IF NOT EXISTS idx_sprint_delta_ts ON sprint_delta(ts DESC);

CREATE TABLE IF NOT EXISTS source_hit_log (
    sprint_id TEXT,
    ts DOUBLE,
    source_type TEXT,
    findings_count INT,
    ioc_count INT,
    hit_rate REAL
);
-- Sprint F-B: indexes for per-sprint + time-range source_hit_log lookups
CREATE INDEX IF NOT EXISTS idx_source_hit_log_sprint_ts ON source_hit_log(sprint_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_source_hit_log_ts ON source_hit_log(ts DESC);

CREATE TABLE IF NOT EXISTS sprint_scorecard (
    sprint_id TEXT PRIMARY KEY,
    ts DOUBLE NOT NULL,
    findings_per_minute REAL,
    ioc_density REAL,
    semantic_novelty REAL,
    source_yield_json TEXT,
    phase_timings_json TEXT,
    outlines_used BOOL,
    accepted_findings INT,
    ioc_nodes INT
);
CREATE INDEX IF NOT EXISTS idx_sprint_scorecard_ts ON sprint_scorecard(ts DESC);

CREATE TABLE IF NOT EXISTS research_episodes (
    episode_id   TEXT PRIMARY KEY,
    sprint_id    TEXT NOT NULL,
    query        TEXT NOT NULL,
    summary      TEXT,
    top_findings JSON,
    ioc_clusters JSON,
    source_yield JSON,
    synthesis_engine TEXT,
    duration_s   REAL,
    ts           DOUBLE NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodes_ts ON research_episodes(ts DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_sprint ON research_episodes(sprint_id);

CREATE TABLE IF NOT EXISTS target_profiles (
    target_id TEXT PRIMARY KEY,
    first_seen DOUBLE,
    last_seen DOUBLE,
    cumulative_finding_count INTEGER,
    entity_summary_json TEXT
);
-- Sprint F-B: target_profiles queried by last_seen DESC for recent targets
CREATE INDEX IF NOT EXISTS idx_target_profiles_last_seen ON target_profiles(last_seen DESC);

CREATE TABLE IF NOT EXISTS hypothesis_feedback (
    id TEXT PRIMARY KEY,
    target_id TEXT,
    pivot_type TEXT,
    ioc_type TEXT,
    produced_count INTEGER,
    accepted_count INTEGER,
    signal_value DOUBLE,
    ts DOUBLE
);
-- Sprint F-B: hypothesis_feedback target_id is the primary filter
CREATE INDEX IF NOT EXISTS idx_hypothesis_feedback_target_ts ON hypothesis_feedback(target_id, ts DESC);

CREATE TABLE IF NOT EXISTS hypothesis_tracking (
    hypothesis_id TEXT PRIMARY KEY,
    sprint_id TEXT,
    hypothesis_text TEXT,
    status TEXT,
    confidence REAL,
    falsification_result TEXT,
    disproved_by_sprint_id TEXT,
    ts DOUBLE
);
-- Sprint F-B: hypothesis_tracking queried by sprint_id and status
CREATE INDEX IF NOT EXISTS idx_hypothesis_tracking_sprint ON hypothesis_tracking(sprint_id);
CREATE INDEX IF NOT EXISTS idx_hypothesis_tracking_status_ts ON hypothesis_tracking(status, ts DESC);

CREATE TABLE IF NOT EXISTS target_memory (
    target_id TEXT PRIMARY KEY,
    first_seen_ts DOUBLE NOT NULL,
    last_seen_ts DOUBLE NOT NULL,
    sprint_count INTEGER NOT NULL,
    cumulative_finding_count INTEGER NOT NULL,
    entity_facets_json TEXT NOT NULL,
    exposure_facets_json TEXT NOT NULL,
    pivot_facets_json TEXT NOT NULL,
    confidence_drift_json TEXT NOT NULL,
    updated_by_sprint_id TEXT NOT NULL
);
-- Sprint F-B: target_memory last_seen_ts is the primary sort key
CREATE INDEX IF NOT EXISTS idx_target_memory_last_seen ON target_memory(last_seen_ts DESC);

-- Sprint F224A: DHT metadata table for torrent content discovery
CREATE TABLE IF NOT EXISTS dht_metadata (
    infohash TEXT PRIMARY KEY,
    name TEXT,
    files_json TEXT,
    size_bytes BIGINT,
    first_seen DOUBLE,
    last_seen DOUBLE,
    peer_count INT,
    sources_json TEXT
);
-- Sprint F-B: dht_metadata queried by last_seen DESC and peer_count
CREATE INDEX IF NOT EXISTS idx_dht_metadata_last_seen ON dht_metadata(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_dht_metadata_peer_count ON dht_metadata(peer_count DESC);

-- Sprint F350M: Cross-sprint research session memory
CREATE TABLE IF NOT EXISTS research_sessions (
    session_id TEXT PRIMARY KEY,
    sprint_id TEXT NOT NULL,
    query TEXT NOT NULL,
    ts DOUBLE NOT NULL,
    findings_count INTEGER,
    accepted_count INTEGER,
    gaps_json TEXT,
    entities_json TEXT,
    source_patterns_json TEXT,
    unexplored_angles_json TEXT,
    temporal_anomalies_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_sessions_sprint ON research_sessions(sprint_id);
CREATE INDEX IF NOT EXISTS idx_research_sessions_ts ON research_sessions(ts DESC);

-- Sprint F350M: Entity observations for temporal tracking
CREATE TABLE IF NOT EXISTS entity_observations (
    observation_id TEXT PRIMARY KEY,
    entity_value TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    sprint_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    confidence REAL,
    ts DOUBLE NOT NULL,
    finding_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entity_observations_entity ON entity_observations(entity_value);
CREATE INDEX IF NOT EXISTS idx_entity_observations_sprint ON entity_observations(sprint_id);

-- Sprint F330: IOC co-occurrence matrix for speculative edge mining
CREATE TABLE IF NOT EXISTS ioc_cooccurrence (
    ioc_a TEXT NOT NULL,
    ioc_b TEXT NOT NULL,
    ioc_type_a TEXT NOT NULL,
    ioc_type_b TEXT NOT NULL,
    support INTEGER NOT NULL,
    confidence REAL NOT NULL,
    score REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ioc_cooccurrence_score ON ioc_cooccurrence(score DESC);
CREATE INDEX IF NOT EXISTS idx_ioc_cooccurrence_ioc_a ON ioc_cooccurrence(ioc_a);
CREATE INDEX IF NOT EXISTS idx_ioc_cooccurrence_ioc_b ON ioc_cooccurrence(ioc_b);
