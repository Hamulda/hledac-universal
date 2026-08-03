-- 0005_domain_reputation.sql
-- UNIFIED-007 + UNIFIED-008: Persistent cross-sprint domain reputation store
--
-- Eliminates per-sprint relearning of:
--   - Tarpit/honeypot domains (tarpit_score)
--   - Proxy affinity (which proxies worked/failed for each domain)
--   - Anti-bot protection type (Cloudflare/Akamai/DataDome)
--   - Cumulative success rate for pre-fetch routing decisions
--
-- M1 8GB: Table grows ~1 row per unique domain fetched. Realistic max
-- ~10K rows = ~2 MB. Bounded LRU eviction via HLEDAC_DOMAIN_REPUTATION_MAX_ROWS
-- (default 5000) in DomainReputationService.

CREATE TABLE IF NOT EXISTS domain_reputation (
    domain              TEXT PRIMARY KEY,
    tarpit_score        REAL NOT NULL DEFAULT 0.0,
    -- JSON array of successful proxy strings, e.g. ["socks5h://127.0.0.1:9050", "residential:us"]
    successful_proxies  TEXT NOT NULL DEFAULT '[]',
    -- JSON array of failed proxy strings
    failed_proxies      TEXT NOT NULL DEFAULT '[]',
    -- Anti-bot type detected: cloudflare | akamai | datadome | imperva | none
    anti_bot_type       TEXT NOT NULL DEFAULT 'none',
    -- Challenge type: js | captcha | turnstile | none
    challenge_type      TEXT NOT NULL DEFAULT 'none',
    success_rate        REAL NOT NULL DEFAULT 1.0,
    total_attempts      INTEGER NOT NULL DEFAULT 0,
    successful_attempts INTEGER NOT NULL DEFAULT 0,
    last_seen           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Primary lookup: fetch-by-domain (O(1) with hash index)
-- tarpit_score index: bulk-scan for "worst offenders" (admin/maintenance queries)
CREATE INDEX IF NOT EXISTS idx_domain_reputation_tarpit
    ON domain_reputation(tarpit_score DESC);

-- success_rate index: identify reliable domains for priority fetching
CREATE INDEX IF NOT EXISTS idx_domain_reputation_success_rate
    ON domain_reputation(success_rate ASC);

-- last_seen index: LRU eviction candidate selection
CREATE INDEX IF NOT EXISTS idx_domain_reputation_last_seen
    ON domain_reputation(last_seen ASC);
