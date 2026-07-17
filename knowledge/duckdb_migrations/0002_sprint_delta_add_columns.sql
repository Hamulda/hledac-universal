-- 0002_sprint_delta_add_columns.sql
-- P0-8: ALTER TABLE ADD COLUMN for sprint_delta columns added in F192F and later sprints.
--
-- Idempotent via Python-level duplicate-column handling in _apply_migration().
-- DuckDB does not support IF NOT EXISTS for ALTER ADD COLUMN, so the Python
-- caller catches "duplicate column" / "already exists" errors and treats them
-- as success.
--
-- Migration order matters — add new column first, then handle legacy column:
--   1. Add findings_per_minute (new canonical name, matches sprint_scorecard)
--   2. Add top_source_type
--   3. Add synthesis_confidence
--
-- Legacy findings_per_min column is retained but not written to
-- (inserts use findings_per_minute).

ALTER TABLE sprint_delta ADD COLUMN findings_per_minute REAL DEFAULT 0;
ALTER TABLE sprint_delta ADD COLUMN top_source_type TEXT;
ALTER TABLE sprint_delta ADD COLUMN synthesis_confidence REAL DEFAULT 0;
