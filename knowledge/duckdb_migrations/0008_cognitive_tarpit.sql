-- 0008_cognitive_tarpit.sql
-- ISSUE [ADVERSARY]-002: LLM-Generated Honeypot Text Detection
--
-- Adds cognitive_tarpit_score column to domain_reputation table.
-- Stores the composite LLM-honeypot score (0.0-1.0) detected via:
--   - Byte-entropy variance (LLM < 0.15, human > 0.40)
--   - Burstiness deviation (LLM 0.3-0.5, human 0.8-1.5)
--   - POS trigram ratio (DT-JJ-NN / NN-VB-DT)
--   - SmolLM pseudo-perplexity (>0.45 → honeypot)
--
-- If tarpit_score is set to 1.0 by cognitive_tarpit AND html_tarpit_detector
-- simultaneously, the domain is a confirmed LLM honeypot (not just a tarpit).
-- This column persists across sprints for cross-sprint memory.
--
-- Backward compatible: existing rows get DEFAULT 0.0.

-- Add cognitive tarpit score column (0.0 = not detected, 1.0 = LLM honeypot)
ALTER TABLE domain_reputation
ADD COLUMN IF NOT EXISTS cognitive_tarpit_score REAL NOT NULL DEFAULT 0.0;

-- Index for identifying LLM honeypot domains at scale
CREATE INDEX IF NOT EXISTS idx_domain_reputation_cognitive_tarpit
    ON domain_reputation(cognitive_tarpit_score DESC)
    WHERE cognitive_tarpit_score > 0.0;

-- Record when cognitive tarpit was first detected (for forensics)
ALTER TABLE domain_reputation
ADD COLUMN IF NOT EXISTS cognitive_tarpit_first_seen TIMESTAMP;

-- Record the specific reasons that triggered the detection
-- (entropy_variance, burstiness_deviation, perplexity_score)
ALTER TABLE domain_reputation
ADD COLUMN IF NOT EXISTS cognitive_tarpit_reasons TEXT;
