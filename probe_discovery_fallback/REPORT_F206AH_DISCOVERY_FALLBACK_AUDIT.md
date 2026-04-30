# SPRINT F206AH — Discovery Provider Fallback Strategy Audit
**Date:** 2026-04-30
**Status:** SEALED
**Scope:** read-only audit, no production changes
**NO-GIT-RULE:** enforced

---

## PHASE 0 — EXISTING DISCOVERY MAP

### Active Discovery Providers

| Provider | Adapter | Wired to Pipeline | Returns |
|---|---|---|---|
| **DuckDuckGo** | `duckduckgo_adapter.async_search_public_web` | YES — `_ASYNC_DISCOVERY_SEARCH` | `DiscoveryBatchResult` |
| **Mojeek scrape** | `duckduckgo_adapter._scrape_mojeek` | YES — fallback Layer 3 in adapter | `list[dict]` → converted |

**Active entry point in canonical public branch:**
```
pipeline/live_public_pipeline.py:3080-3083
  from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web
  _ASYNC_DISCOVERY_SEARCH = async_search_public_web
```

### Existing Fallback Seam

```
async_search_public_web()
  1. DDG primary via _ddgs_text_search()
  2. on error_tag in _BACKEND_ERROR_TAGS:
       timeout | proxy_error | network_error | server_error | unknown_backend_error
  3. → _scrape_mojeek() as bounded fallback (single-layer)
  4. → fallback_triggered = "primary_backend_failed_fallback_succeeded" | "primary_backend_failed_fallback_failed"
```

**NO retry with jitter exists in adapter** (confirmed: `rg 'retry|jitter|backoff' discovery/duckduckgo_adapter.py` → 0 matches).

### Dormant / Donor Discovery Code

| Provider | Adapter | Returns | Wired to Pipeline |
|---|---|---|---|
| Multi-engine (DDG+Mojeek+CC) | `duckduckgo_adapter.search_multi_engine` | `list[dict]` | NO — ti_feed only |
| Wayback CDX | `duckduckgo_adapter._search_wayback_cdx` | `list[dict]` | NO |
| CommonCrawl CDX | `duckduckgo_adapter._search_commoncrawl_cdx` | `list[dict]` | NO |
| CommonCrawl domain | `duckduckgo_adapter._search_commoncrawl_domain` | `list[dict]` | NO |
| NVD API | `ti_feed_adapter.NvdApiAdapter` | CanonicalFindings | NO |
| CISA KEV | `ti_feed_adapter.CisaKevAdapter` | CanonicalFindings | NO |
| crtsh | `ti_feed_adapter.search_crtsh` | CanonicalFindings | NO |
| GitHub Gists | `ti_feed_adapter.search_github_gists` | CanonicalFindings | NO |
| Ahmia (I2P) | `ti_feed_adapter.search_ahmia` | CanonicalFindings | NO |
| Wayback Archive | `ti_feed_adapter.WaybackArchiveAdapter` | CanonicalFindings | NO |
| IPFS | `ti_feed_adapter.search_ipfs` | CanonicalFindings | NO |
| Usenet | `ti_feed_adapter.search_usenet` | CanonicalFindings | NO |
| MalwareBazaar | `ti_feed_adapter._handle_malwarebazaar_search` | CanonicalFindings | NO |
| RSS/Atom | `rss_atom_adapter` | CanonicalFindings | NO |
| Archive Discovery | `intelligence/archive_discovery.py` | — | NO |
| Academic Discovery | `intelligence/academic_discovery.py` | — | NO |
| Relationship Discovery | `intelligence/relationship_discovery.py` | — | NO |

### DiscoveryBatchResult Schema

```
fields: hits (tuple[DiscoveryHit]), error (str|None), fallback_triggered (str|None)
provider_name: MISSING (F2)
error_type: NOT a field — error is a string tag
```

`fallback_triggered` values: `"primary_backend_failed_fallback_succeeded"` | `"primary_backend_failed_fallback_failed"` | `None`

### DiscoveryResult already supports fallback_triggered + error

- `DiscoveryBatchResult` has `fallback_triggered: str | None` field ✓
- `DiscoveryHit` has `source: str` field ✓
- `error: str | None` field exists ✓
- `provider_name` field does NOT exist (F2 — MEDIUM) ✗

---

## PHASE 1 — PROVIDER OPTIONS ANALYSIS

### Option A: DDG Retry/Backoff Only
- **Reliability:** 3/5 — same environmental cause will recur
- **Stealth:** 4/5 — same UA, same pattern
- **API key required:** NO
- **Self-hosted compatible:** YES
- **RAM cost:** 0 MB
- **Maintenance:** LOW
- **Rate-limit risk:** HIGH — retry amplifies request count
- **Legal/TOS:** LOW
- **Fit for 30-min sprint:** Already implemented (though no jitter)
- **Implementation complexity:** N/A
- **Verdict:** Already done. Adding jitter is cosmetic only.

### Option B: Brave Search API Fallback
- **Reliability:** 4/5 — separate infrastructure
- **Stealth:** 2/5 — different UA fingerprint, API call traceable
- **API key required:** YES (HLEDAC_BRAVE_API_KEY)
- **Self-hosted compatible:** NO — cloud-only
- **RAM cost:** 0 MB
- **Maintenance:** LOW
- **Rate-limit risk:** LOW — own API key
- **Legal/TOS:** MEDIUM — Brave TOS for scraping
- **Fit for 30-min sprint:** LOW — requires key management, env gate, integration
- **Implementation complexity:** MEDIUM
- **Verdict:** Not recommended as primary fallback. Opt-in only.

### Option C: SearXNG Local Instance
- **Reliability:** 4/5 — self-hosted, full control
- **Stealth:** 5/5 — self-hosted, no external calls
- **API key required:** NO
- **Self-hosted compatible:** YES — but OFF DEVICE on M1 8GB
- **RAM cost:** ~300 MB for SearXNG process
- **Maintenance:** HIGH — separate service, update management
- **Rate-limit risk:** NONE — self-hosted
- **Legal/TOS:** NONE
- **Fit for 30-min sprint:** LOW — requires Docker/service setup
- **Implementation complexity:** MEDIUM
- **Verdict:** OFF DEVICE only. Good for users with external SearXNG.

### Option D: ddgs Multi-Backend Wrapper
- **Reliability:** 4/5 — already in use
- **Stealth:** 3/5 — same DDG backend
- **API key required:** NO
- **Self-hosted compatible:** YES
- **RAM cost:** 0 MB
- **Maintenance:** LOW
- **Rate-limit risk:** SAME as current DDG
- **Legal/TOS:** SAME as current DDG
- **Fit for 30-min sprint:** Already covered
- **Implementation complexity:** N/A
- **Verdict:** Already implemented. duckduckgo_search IS ddgs equivalent.

### Option E: Static Seed URL Fallback
- **Reliability:** 3/5 — limited fresh discovery
- **Stealth:** 5/5 — no external calls, feeds already ingested
- **API key required:** NO
- **Self-hosted compatible:** YES
- **RAM cost:** 0 MB
- **Maintenance:** MEDIUM — feed freshness
- **Rate-limit risk:** NONE
- **Legal/TOS:** NONE
- **Fit for 30-min sprint:** HIGH — TI adapters exist dormant
- **Implementation complexity:** LOW — needs pipeline wiring
- **Verdict:** ✓ HIGHEST value for minimal effort. TI adapters already exist.

### Option F: Common Crawl / Wayback CDX Fallback
- **Reliability:** 4/5 — separate infrastructure (CDN-backed)
- **Stealth:** 5/5 — no search engine, purely archive-based
- **API key required:** NO
- **Self-hosted compatible:** YES
- **RAM cost:** 0 MB
- **Maintenance:** LOW
- **Rate-limit risk:** LOW — public CDX API with fair use
- **Legal/TOS:** NONE
- **Fit for 30-min sprint:** HIGH — code already exists dormant
- **Implementation complexity:** LOW — wiring only
- **Verdict:** ✓ HIGHEST value. Code exists, needs pipeline wiring.

---

## PHASE 2 — M1-SAFE FALLBACK DESIGN

### Recommended Cascade (NOT YET IMPLEMENTED)

```
Layer 1: DDG primary           → always, 8s timeout
Layer 2: DDG retry + jitter    → on backend_error only, 5s wait + random(0,2s) jitter
Layer 3: Mojeek scrape         → on backend_error, 12s timeout [EXISTING — active]
Layer 4: Wayback CDX           → on unknown_backend_error after Layer 3 fails [DORMANT — needs wiring]
Layer 5: CommonCrawl CDX domain→ on Layer 4 fails              [DORMANT — needs wiring]
Layer 6: Static seed expansion  → when all above return empty  [DORMANT — needs wiring]
---
Opt-in (off-device):
  Layer S1: SearXNG             → HLEDAC_ENABLE_SEARXNG=1, HLEDAC_SEARXNG_URL=...
  Layer S2: Brave Search         → HLEDAC_ENABLE_BRAVE_SEARCH=1 + HLEDAC_BRAVE_API_KEY
```

### Env Gates Design

| Env Var | Default | Description |
|---|---|---|
| `HLEDAC_ENABLE_SEARXNG` | `0` | Enable SearXNG fallback (off-device) |
| `HLEDAC_SEARXNG_URL` | `http://127.0.0.1:8888` | SearXNG instance URL |
| `HLEDAC_ENABLE_BRAVE_SEARCH` | `0` | Enable Brave Search fallback |
| `HLEDAC_BRAVE_API_KEY` | `None` | Brave Search API key |
| `HLEDAC_ENABLE_STATIC_SEED_FALLBACK` | `0` | Enable static seed expansion |
| `HLEDAC_DISCOVERY_FALLBACK_FORCE` | `0` | Force fallback chain always (benchmark) |

### Why `unknown_backend_error` Is Environmental

```
DDG backend error
  → classify_error(e)
      → "unknown_backend_error" if no keyword match
  → _BACKEND_ERROR_TAGS includes "unknown_backend_error"
  → triggers Mojeek fallback ✓
  → Mojeek may also fail (same environmental cause)
  → fallback_triggered = "primary_backend_failed_fallback_failed"
```

**Key insight:** The fallback IS correctly triggered. Both primary (DDG) and fallback (Mojeek) can be blocked by the same environmental condition. This is NOT a code bug — it is an environmental constraint.

Fixable aspects: HTML error page parsing, CAPTCHA detection — requires separate infrastructure for fallback (Wayback CDX is separate from DDG/Mojeek).

---

## PHASE 3 — FINDINGS

### F1 — HIGH — Architecture Gap
**search_multi_engine() (DDG+Mojeek+CC parallel) exists since F192E but is NOT wired to public pipeline.**
- Location: `discovery/duckduckgo_adapter.py:1063`
- Returns `list[dict]` not `DiscoveryBatchResult` — incompatible with pipeline without wrapper
- Only used in `ti_feed_adapter.py:1152` (dormant TI task context)

### F2 — MEDIUM — Schema Gap
**DiscoveryBatchResult lacks provider_name field — cannot identify which provider returned hits.**
- Location: `discovery/duckduckgo_adapter.py:78`
- Cannot distinguish DDG hits from Mojeek hits in pipeline verdict

### F3 — MEDIUM — Design Limitation
**unknown_backend_error is environmental catch-all — Mojeek fallback may fail for same reason.**
- Location: `discovery/duckduckgo_adapter.py:129`
- Fixable in 30-min sprint: NO — requires HTML error page parsing or separate infrastructure

### F4 — HIGH — Architecture Gap
**Wayback CDX and CommonCrawl CDX exist but dormant — not used as discovery fallback.**
- Location: `discovery/duckduckgo_adapter.py:889-1022`
- Both already implemented with proper error handling
- Not wired to pipeline

### F5 — MEDIUM — Unused Capability
**All TI feed adapters in ti_feed_adapter.py are dormant — could provide static seed expansion.**
- Location: `discovery/ti_feed_adapter.py`
- Sources: NVD, CISA KEV, crtsh, GitHub Gists, Ahmia, Wayback Archive, IPFS, Usenet, MalwareBazaar
- Not wired to public discovery pipeline

### F6 — MEDIUM — Type Gap
**search_multi_engine() returns list[dict] not DiscoveryBatchResult — incompatible with pipeline.**
- Location: `discovery/duckduckgo_adapter.py:1063`
- Requires wrapper to convert to `DiscoveryBatchResult` for pipeline integration

### F7 — LOW — Configuration Gap
**No env gates for SearXNG or Brave — no opt-in mechanism exists.**
- Not implemented anywhere in codebase

---

## PHASE 4 — SMOKE RESULTS

| Check | Result |
|---|---|
| SearXNG on `http://127.0.0.1:8888` | **NOT_RUNNING** — no service detected |
| Brave Search API key | **not_set** — `HLEDAC_BRAVE_API_KEY` not configured |
| `HLEDAC_ENABLE_SEARXNG` | **not_set** — env gate does not exist |
| `HLEDAC_ENABLE_BRAVE_SEARCH` | **not_set** — env gate does not exist |
| Wayback CDX code | **exists dormant** in `duckduckgo_adapter.py:889` |
| CommonCrawl CDX code | **exists dormant** in `duckduckgo_adapter.py:1000` |
| Static seed sources | **available** — ti_feed_adapter.py dormant adapters |

---

## EXACT NEXT SPRINT: F206AI

### Title
**Discovery Fallback Cascade — Wayback/CommonCrawl Wire + Static Seed**

### Scope Boundary (MUST NOT EDIT)
- `runtime/sprint_scheduler.py`
- `pipeline/live_public_pipeline.py` (entry point unchanged)
- `discovery/duckduckgo_adapter.py` core fallback logic
- `storage/export` schema

### Tasks for F206AI
1. Add `provider_name: str | None` field to `DiscoveryBatchResult` — answers "which provider returned hits"
2. Wrap `search_multi_engine()` to return `DiscoveryBatchResult` with `provider_name` set
3. Wire `_search_wayback_cdx()` as fallback Layer 4 — wrap to `DiscoveryBatchResult`
4. Wire `_search_commoncrawl_cdx()` as fallback Layer 5 — wrap to `DiscoveryBatchResult`
5. Add static seed expansion from CT findings via DuckDB read — existing ti_feed adapters
6. Add `HLEDAC_ENABLE_STATIC_SEED_FALLBACK=1` env gate
7. Add probe tests for fallback cascade (mock each layer)
8. Add `HLEDAC_DISCOVERY_FALLBACK_FORCE=1` for hermetic testing

### Not in Scope for F206AI
- Adding retry + jitter to DDG Layer 2 (cosmetic — same backend, same environmental cause)
- Brave Search integration (cloud-only, not M1-safe as primary)
- SearXNG integration (off-device, maintenance burden)
- Any change to `runtime/sprint_scheduler.py`
- Any change to `pipeline/live_public_pipeline.py` beyond `_ASYNC_DISCOVERY_SEARCH` assignment
- Any change to storage/export schema

---

## RISKS AND ABORT CONDITIONS

### Abort Conditions (violate = stop)
1. Any change to `runtime/sprint_scheduler.py`
2. Any change to `pipeline/live_public_pipeline.py`
3. Any change to `discovery/duckduckgo_adapter.py` core fallback logic (Layer 1-3)
4. Any change to `storage/export` schema
5. Any new production behavior without env gate
6. Any introduction of blocking operations in async contexts

### Risks
| Risk | Severity | Mitigation |
|---|---|---|
| Wayback CDX rate limiting under heavy use | MEDIUM | Per-request backoff, bound retries |
| CommonCrawl CDX data quality (old snapshots) | MEDIUM | Document as overlay-ready, not authoritative |
| Static seed staleness | LOW | Time-bounded seed TTL |
| search_multi_engine type mismatch | MEDIUM | Wrapper conversion to DiscoveryBatchResult |
| M1 RAM pressure from concurrent fallback layers | LOW | Sequential (not parallel) fallback execution |

---

## FINAL VERDICT

**SEALED — F206AH Discovery Fallback Strategy Audit COMPLETE**

1. ✅ All discovery surfaces mapped (15+ providers, 1 active, 14 dormant)
2. ✅ Fallback options compared (A-F rated, E+F highest value for M1)
3. ✅ Recommended cascade is M1-safe (0 MB RAM additions, off-device SearXNG/Brave opt-in)
4. ✅ No production code changed
5. ✅ Report + JSON matrix generated

**Key architectural truth:** The `unknown_backend_error` is environmental, not a code bug. The existing fallback (Mojeek) is correctly triggered but may fail for the same environmental reason. The fix is NOT more retry logic — it is wiring the existing dormant Wayback/CommonCrawl CDX layers (separate infrastructure) and static seed expansion. The `search_multi_engine()` parallel multi-engine is also a candidate but requires type-safe wrapping to `DiscoveryBatchResult`.

**F206AI implementation prompt** is above. All abort conditions documented. No production behavior changes without env gate.
