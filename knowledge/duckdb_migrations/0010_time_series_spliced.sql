-- [META]-005: Time Series Splicer — unified millisecond-aligned timeline across protocols
-- Migration 0010: Creates time_series_spliced table

-- Sprint [META]-005: Unified millisecond-aligned timeline across all protocol sources.
-- Canonical timestamp format: int64 nanoseconds since Unix epoch.
-- Primary key: (entity_value, ioc_type, protocol, timestamp_ns) ensures event-level dedup.
-- Index: entity + timestamp for O(log n) timeline queries.

CREATE TABLE IF NOT EXISTS time_series_spliced (
    entity_value             VARCHAR NOT NULL,
    ioc_type                VARCHAR NOT NULL,
    protocol                VARCHAR NOT NULL,
    timestamp_ns            BIGINT NOT NULL,
    event_type              VARCHAR NOT NULL,
    source_evidence_url     VARCHAR NOT NULL,
    corroborating_sources   TEXT[],
    raw_timestamp           VARCHAR,
    sprint_id               VARCHAR DEFAULT '',
    inserted_at             DOUBLE DEFAULT CAST(UNIX_TIMESTAMP AS DOUBLE),
    PRIMARY KEY (entity_value, ioc_type, protocol, timestamp_ns)
);

-- Primary query index: entity timeline with time range filters
CREATE INDEX IF NOT EXISTS idx_timeline_entity
    ON time_series_spliced(entity_value, timestamp_ns DESC);

-- Protocol diversity queries (which protocols have data for an entity)
CREATE INDEX IF NOT EXISTS idx_timeline_proto
    ON time_series_spliced(protocol, timestamp_ns DESC);

-- Event type aggregation (group by entity + event_type)
CREATE INDEX IF NOT EXISTS idx_timeline_event_type
    ON time_series_spliced(entity_value, event_type, timestamp_ns DESC);

-- Corroboration queries (find events with multiple sources)
CREATE INDEX IF NOT EXISTS idx_timeline_corroboration
    ON time_series_spliced(entity_value, timestamp_ns DESC)
    WHERE array_length(corroborating_sources) > 0;

-- Protocol enum reference (for validation in application layer):
-- ct_log, git, telegram, blockchain, http, warc, passive_dns
--
-- Event type reference (for visualization):
-- certificate_valid_from, certificate_expires,
-- commit_authored, commit_committed,
-- message_posted,
-- tx_confirmed, address_first_seen,
-- resource_modified, resource_archived,
-- dns_first_seen, dns_last_seen,
-- domain_registered, domain_updated, domain_expired,
-- ip_first_seen, ip_last_seen
