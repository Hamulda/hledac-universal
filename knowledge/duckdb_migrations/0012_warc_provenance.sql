-- ISSUE F5-FIX: WARC Provenance Columns for Court-Admissible Evidence Replay
-- ========================================================================
-- This migration adds WARC provenance columns to canonical_findings table
-- enabling byte-level WARC seeks without full file scanning.
--
-- New columns:
--   - warc_record_id: URN-UUID from WARC-Record-ID header
--   - warc_path: Absolute path to .warc.gz file
--   - compressed_offset: Compressed (seekable) byte offset
--   - compressed_size: Compressed record block size
--   - warc_url: Archived URL from WARC-Target-URI
--
-- Index added:
--   - idx_canonical_findings_warc: for WARC replay queries by record_id

-- Add WARC columns to canonical_findings (idempotent - errors ignored)
ALTER TABLE canonical_findings ADD COLUMN warc_record_id VARCHAR;
ALTER TABLE canonical_findings ADD COLUMN warc_path VARCHAR;
ALTER TABLE canonical_findings ADD COLUMN compressed_offset BIGINT DEFAULT 0;
ALTER TABLE canonical_findings ADD COLUMN compressed_size BIGINT DEFAULT 0;
ALTER TABLE canonical_findings ADD COLUMN warc_url VARCHAR;

-- Add index for WARC replay queries (idempotent)
CREATE INDEX IF NOT EXISTS idx_canonical_findings_warc ON canonical_findings(warc_record_id);
