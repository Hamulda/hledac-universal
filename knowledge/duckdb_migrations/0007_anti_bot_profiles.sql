-- 0007_anti_bot_profiles.sql
-- UNIFIED-010: Persistent cross-sprint anti-bot fingerprint database
--
-- Eliminates per-sprint relearning of:
--   - Which WAF/CDN protects each domain (Cloudflare/Akamai/DataDome/Imperva)
--   - What challenge types are in use (JS challenge, CAPTCHA, Turnstile, 403)
--   - Required headers for bypass (Accept-Encoding, Accept-Language, Referer)
--   - Cookie requirements (cf_clearance, ak_bmsc, datadome, etc.)
--   - Whether JS rendering is needed (Playwright/Camoufox)
--   - Whether residential proxy is needed (datacenter blocked)
--   - Stealth level required (none | standard | aggressive | JS_render)
--
-- Architecture: One row per domain, updated incrementally as anti-bot
-- detection fires during normal fetch operations. The AntiBotProfileService
-- merges new observations into existing profiles via confidence-weighted
-- exponential moving average.
--
-- M1 8GB: One row per domain that has been profiled. Realistic max
-- ~5K rows = ~2 MB. Bounded via HLEDAC_ANTI_BOT_PROFILES_MAX_ROWS (default 5000).

CREATE TABLE IF NOT EXISTS anti_bot_profiles (
    -- Target domain (normalized, lowercased, no port/www)
    domain                  TEXT PRIMARY KEY,
    -- WAF/CDN type: cloudflare | akamai | datadome | imperva | fastly | cloudfront | none
    waf_type                TEXT NOT NULL DEFAULT 'none',
    -- JSON array of challenge types observed: ["js","captcha","turnstile","403","429"]
    challenge_types         TEXT NOT NULL DEFAULT '[]',
    -- Current recommended bypass strategy
    -- 'none' | 'curl_cffi' | 'residential_proxy' | 'js_render' | 'stealth_headers'
    bypass_strategy         TEXT NOT NULL DEFAULT 'none',
    -- JSON array of required HTTP headers for bypass
    -- e.g. ["Accept","Accept-Encoding","Accept-Language","Referer","Sec-Fetch-*"]
    required_headers        TEXT NOT NULL DEFAULT '[]',
    -- JSON array of required cookie names
    -- e.g. ["cf_clearance","__cf_bm","ak_bmsc","datadome"]
    required_cookies        TEXT NOT NULL DEFAULT '[]',
    -- Whether JS rendering (Playwright/Camoufox) is needed
    js_rendering_needed     BOOLEAN NOT NULL DEFAULT FALSE,
    -- Whether a residential proxy is required (datacenter IPs blocked)
    residential_proxy_needed BOOLEAN NOT NULL DEFAULT FALSE,
    -- Stealth level: none | standard | aggressive | js_render
    stealth_level           TEXT NOT NULL DEFAULT 'none',
    -- Whether TLS fingerprint (JA3) randomization is recommended
    ja3_randomize           BOOLEAN NOT NULL DEFAULT FALSE,
    -- Detected bot-blocking patterns in HTML body (comma-separated keywords)
    -- e.g. "attention_required,checking_browser,enable_javascript"
    block_patterns          TEXT NOT NULL DEFAULT '',
    -- Confidence in the profile accuracy (0.0-1.0)
    -- Updated via EMA as more observations come in
    confidence              REAL NOT NULL DEFAULT 0.0,
    -- Number of observations that built this profile
    observation_count       INTEGER NOT NULL DEFAULT 0,
    -- Timestamp of last anti-bot challenge detection
    last_challenge_seen     TIMESTAMP,
    -- Timestamp of last successful bypass
    last_bypass_success     TIMESTAMP,
    -- Profile creation/update timestamps
    first_seen              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Primary lookup: find profile by domain
-- (implicit index via PRIMARY KEY on domain)

-- WAF type index: bulk-scan for "all Cloudflare domains"
CREATE INDEX IF NOT EXISTS idx_anti_bot_profiles_waf
    ON anti_bot_profiles(waf_type, confidence DESC);

-- Stealth level index: find domains requiring aggressive stealth
CREATE INDEX IF NOT EXISTS idx_anti_bot_profiles_stealth
    ON anti_bot_profiles(stealth_level);

-- JS rendering index: find domains that always need browser rendering
CREATE INDEX IF NOT EXISTS idx_anti_bot_profiles_js
    ON anti_bot_profiles(js_rendering_needed, last_challenge_seen DESC);

-- Residential proxy index: find domains where datacenter IPs are blocked
CREATE INDEX IF NOT EXISTS idx_anti_bot_profiles_residential
    ON anti_bot_profiles(residential_proxy_needed);

-- Confidence index: find low-confidence profiles that need more exploration
CREATE INDEX IF NOT EXISTS idx_anti_bot_profiles_confidence
    ON anti_bot_profiles(confidence ASC, observation_count ASC);

-- Last seen index: LRU eviction candidate selection
CREATE INDEX IF NOT EXISTS idx_anti_bot_profiles_last_seen
    ON anti_bot_profiles(updated_at ASC);
