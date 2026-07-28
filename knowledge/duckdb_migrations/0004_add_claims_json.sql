-- 0004_add_claims_json.sql
-- F350M-R: Add claims_json column for Rust claims_extraction wiring
-- Rust batch_extract_claims_python extracts sentence-level claims with
-- polarity/confidence metadata per finding.
-- Claims stored as JSON array: [{"text": "...", "polarity": "...", "confidence": 0.xx, "source": "...", "evidence_type": "..."}]

ALTER TABLE canonical_findings ADD COLUMN IF NOT EXISTS claims_json TEXT;
