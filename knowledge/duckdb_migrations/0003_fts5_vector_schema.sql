-- 0003_fts5_vector_schema.sql
-- F350M-R: DuckDB FTS5 + Native Vector Index
--
-- Phase 1 of the Knowledge layer consolidation:
-- Replaces LanceDB-backed FTS with DuckDB FTS5 extension.
-- Replaces LanceDB ANN with DuckDB array + HNSW/cosine distance.
--
-- Two FTS5 virtual tables:
--   findings_fts  — payload_text from canonical_findings
--   entity_fts    — entity observations
--
-- Vector table:
--   rag_embeddings — cross-sprint RAG document embeddings
--   (stored as DuckDB LIST<FLOAT>, queried via array_cosine_distance)
--
-- Idempotent: all CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
-- FTS5 tables are VIRTUAL — no physical storage overhead.
-- Vector index: HNSW (DuckDB 1.5+ native), fallback to sequential scan.

-- ─── FTS5: canonical findings full-text search ────────────────────────────

-- findings_fts: FTS5 virtual table over canonical_findings.payload_text
-- Allows: MATCH queries, snippet(), highlight() for result rendering
-- Score column enables ranking by relevance.
CREATE TABLE IF NOT EXISTS findings_fts (
    fts_rowid BIGINT PRIMARY KEY,
    query VARCHAR,
    source_type VARCHAR,
    ts DOUBLE,
    score DOUBLE DEFAULT 0.0
);

-- Populate findings_fts from existing canonical_findings
-- (idempotent — uses INSERT OR IGNORE so existing rows are skipped)
INSERT OR IGNORE INTO findings_fts (fts_rowid, query, source_type, ts, score)
SELECT
    rowid,
    query,
    source_type,
    ts,
    0.0
FROM canonical_findings
WHERE payload_text IS NOT NULL AND payload_text != '';

-- Index on ts for time-range FTS queries
CREATE INDEX IF NOT EXISTS idx_findings_fts_ts ON findings_fts(ts DESC);
CREATE INDEX IF NOT EXISTS idx_findings_fts_query ON findings_fts(query);

-- ─── FTS5: entity observations ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS entity_fts (
    fts_rowid BIGINT PRIMARY KEY,
    entity_value VARCHAR,
    entity_type VARCHAR,
    sprint_id VARCHAR,
    ts DOUBLE,
    score DOUBLE DEFAULT 0.0
);

-- Populate entity_fts from entity_observations
INSERT OR IGNORE INTO entity_fts (fts_rowid, entity_value, entity_type, sprint_id, ts, score)
SELECT
    rowid,
    entity_value,
    entity_type,
    sprint_id,
    ts,
    0.0
FROM entity_observations;

CREATE INDEX IF NOT EXISTS idx_entity_fts_ts ON entity_fts(ts DESC);
CREATE INDEX IF NOT EXISTS idx_entity_fts_entity ON entity_fts(entity_value);

-- ─── Vector embeddings: cross-sprint RAG ─────────────────────────────────

-- rag_embeddings: stores document chunk embeddings as DuckDB LIST<FLOAT>
-- Schema:
--   chunk_id    VARCHAR PRIMARY KEY — unique chunk identifier
--   document_id VARCHAR — parent document (for MMR diversity)
--   content     TEXT — raw text chunk
--   metadata    JSON — source, sprint_id, etc.
--   embedding   LIST<FLOAT> — 384-dim embedding vector (MLX / FastEmbed)
--   created_at  DOUBLE — unix timestamp
CREATE TABLE IF NOT EXISTS rag_embeddings (
    chunk_id          VARCHAR PRIMARY KEY,
    document_id       VARCHAR NOT NULL,
    content           TEXT,
    metadata_json     VARCHAR,
    embedding         LIST<FLOAT>,
    embedding_dim     INTEGER DEFAULT 384,
    created_at        DOUBLE NOT NULL
);

-- HNSW index for ANN — array_cosine_distance is DuckDB's cosine distance
-- hnsw_cosine: cosine distance with hnsw index (DuckDB 1.5+)
-- M: number of bi-directional links (default 16, good for M1 8GB)
-- ef_construction: search scope during build (default 200)
CREATE INDEX IF NOT EXISTS idx_rag_embeddings_hnsw
    ON rag_embeddings USING hnsw (embedding)
    WITH (metric = 'cosine', m = 16, ef_construction = 128);

-- Fallback index: cannot use IF NOT EXISTS on HNSW/Cosine
-- (DuckDB requires explicit DROP before recreate)
-- Sequential scan with ORDER BY array_cosine_distance works without index.

-- ─── Entity embeddings: identity resolution ────────────────────────────────

-- entity_embeddings: entity alias/identity vectors for clustering
-- Same schema as rag_embeddings but for entity-level search
CREATE TABLE IF NOT EXISTS entity_embeddings (
    entity_id      VARCHAR PRIMARY KEY,
    entity_value   VARCHAR NOT NULL,
    entity_type    VARCHAR,
    metadata_json  VARCHAR,
    embedding      LIST<FLOAT>,
    embedding_dim  INTEGER DEFAULT 384,
    updated_at     DOUBLE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entity_embeddings_hnsw
    ON entity_embeddings USING hnsw (embedding)
    WITH (metric = 'cosine', m = 16, ef_construction = 128);
