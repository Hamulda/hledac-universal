-- 0006_proxy_routes.sql
-- UNIFIED-009: Persistent cross-sprint proxy route graph
--
-- Eliminates per-sprint relearning of:
--   - Which proxy+transport pairs work for each domain
--   - Per-route latency profiles (EWMA median, p95, p99)
--   - Route success/failure counts for weighted routing decisions
--   - Bandwidth estimates for large-body fetching
--
-- Architecture: This is a weighted adjacency-list graph where:
--   Node = domain (the target)
--   Edge = (domain, proxy, transport) triple with performance metrics
--   Weight = composite score of success_rate × latency_decay × recency_bonus
--
-- M1 8GB: Table grows ~3-5 rows per unique domain (one per tried proxy+transport).
-- Realistic max ~15K rows = ~3 MB. Bounded LRU eviction via
-- HLEDAC_PROXY_ROUTES_MAX_ROWS (default 10000) in RouteGraphService.

CREATE TABLE IF NOT EXISTS proxy_routes (
    -- Primary key: auto-increment BIGINT for DuckDB WAL efficiency
    id                  BIGINT PRIMARY KEY DEFAULT nextval('seq_proxy_routes_id'),
    -- Target domain (normalized: lowercased, no port, no www.)
    domain              TEXT NOT NULL,
    -- Proxy identifier (empty string = direct/no proxy)
    proxy               TEXT NOT NULL DEFAULT '',
    -- Transport identifier: 'curl_cffi' | 'httpx' | 'nw_connection' | 'nw_quic'
    transport           TEXT NOT NULL DEFAULT '',
    -- Latency metrics (milliseconds) — EWMA-smoothed across sprints
    ewma_latency_ms     REAL NOT NULL DEFAULT 0.0,
    p50_latency_ms      REAL NOT NULL DEFAULT 0.0,
    p95_latency_ms      REAL NOT NULL DEFAULT 0.0,
    p99_latency_ms      REAL NOT NULL DEFAULT 0.0,
    -- Success/failure counters (cumulative, cross-sprint)
    success_count       INTEGER NOT NULL DEFAULT 0,
    fail_count          INTEGER NOT NULL DEFAULT 0,
    -- Timestamps for recency scoring
    last_success        TIMESTAMP,
    last_failure        TIMESTAMP,
    first_seen          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Bandwidth estimate (bytes/sec) for large-body routing decisions
    bw_bytes_per_sec    REAL NOT NULL DEFAULT 0.0,
    -- Maximum body size fetched through this route (bytes)
    max_body_bytes      INTEGER NOT NULL DEFAULT 0,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Unique constraint: one row per (domain, proxy, transport) triple
    UNIQUE(domain, proxy, transport)
);

-- Sequence for auto-increment id
CREATE SEQUENCE IF NOT EXISTS seq_proxy_routes_id START 1;

-- Primary lookup: find all routes for a domain, ordered by success rate
CREATE INDEX IF NOT EXISTS idx_proxy_routes_domain_success
    ON proxy_routes(domain, success_count DESC, ewma_latency_ms ASC);

-- Latency index: find fastest routes across all domains (admin queries)
CREATE INDEX IF NOT EXISTS idx_proxy_routes_latency
    ON proxy_routes(ewma_latency_ms ASC);

-- Last seen index: LRU eviction candidate selection
CREATE INDEX IF NOT EXISTS idx_proxy_routes_last_success
    ON proxy_routes(last_success ASC);

-- Transport index: per-transport performance aggregation
CREATE INDEX IF NOT EXISTS idx_proxy_routes_transport
    ON proxy_routes(transport, ewma_latency_ms ASC);

-- Composite: proxy affinity lookup for a specific domain+transport
CREATE INDEX IF NOT EXISTS idx_proxy_routes_domain_transport
    ON proxy_routes(domain, transport, success_count DESC);
