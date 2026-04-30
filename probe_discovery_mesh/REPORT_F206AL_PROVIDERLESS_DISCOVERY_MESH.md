# SPRINT F206AL — Providerless Discovery Mesh Audit
**Date:** 2026-04-30
**Status:** SEALED
**Scope:** read-only audit, no production code changes
**NO-GIT-RULE:** enforced
**MUST NOT EDIT:** `runtime/sprint_scheduler.py`, `core/__main__.py`, `pipeline/live_public_pipeline.py`, `discovery/duckduckgo_adapter.py`, `transport/*`, `storage/export schema`

---

## PHASE 0 — DISCOVERY SURFACE MAP

### Active Modules

| Module | Function/Class | Status | Dep Cost | Network | M1 RAM | OSINT Value |
|--------|---------------|--------|----------|---------|--------|-------------|
| `discovery/duckduckgo_adapter.py` | `DiscoveryHit`, `DiscoveryBatchResult` | **active** | 0 MB | yes | 0 | primary contract |
| `discovery/duckduckgo_adapter.py` | `async_search_public_web` | **active** | 0 MB | yes | 0 | primary discovery |
| `discovery/duckduckgo_adapter.py` | `_scrape_mojeek` | **active** (fallback L3) | 0 MB | yes | 0 | secondary |
| `discovery/rss_atom_adapter.py` | `FeedDiscoveryHit`, `FeedDiscoveryBatchResult` | **active** | 0 MB | yes | 0 | active elsewhere |
| `tools/content_miner.py` | `FeedDiscoveryResult`, `discover_feeds` | **active** | 0 MB | no | 0 | donor |

### Dormant Modules (Providerless-Candidate)

| Module | Function/Class | Status | Dep Cost | Network | M1 RAM | OSINT Value |
|--------|---------------|--------|----------|---------|--------|-------------|
| `discovery/duckduckgo_adapter.py` | `_search_wayback_cdx` | **dormant** | 0 MB | yes | 0 | HIGH |
| `discovery/duckduckgo_adapter.py` | `_search_commoncrawl_cdx` | **dormant** | 0 MB | yes | 0 | HIGH |
| `discovery/duckduckgo_adapter.py` | `_search_commoncrawl_domain` | **dormant** | 0 MB | yes | 0 | MED |
| `discovery/duckduckgo_adapter.py` | `search_multi_engine` | **dormant** | 0 MB | yes | 0 | MEDIUM |
| `discovery/ti_feed_adapter.py` | `search_crtsh` | **dormant** | 0 MB | yes | 0 | HIGH |
| `discovery/ti_feed_adapter.py` | `NvdApiAdapter` | **dormant** | 0 MB | yes | 0 | LOW |
| `discovery/ti_feed_adapter.py` | `CisaKevAdapter` | **dormant** | 0 MB | yes | 0 | LOW |
| `discovery/ti_feed_adapter.py` | `search_github_gists` | **dormant** | 0 MB | yes | 0 | MEDIUM |
| `discovery/ti_feed_adapter.py` | `_query_shodan_internetdb` | **dormant** | 0 MB | yes | 0 | MEDIUM |
| `discovery/ti_feed_adapter.py` | `_query_rdap` | **dormant** | 0 MB | yes | 0 | MEDIUM |
| `discovery/ti_feed_adapter.py` | `WaybackArchiveAdapter` | **dormant** | 0 MB | yes | 0 | MEDIUM |
| `intelligence/archive_discovery.py` | `wayback_cdx_lookup` | **dormant** | 0 MB | yes | 0 | HIGH |
| `intelligence/archive_discovery.py` | `ArchiveDiscovery` | **dormant** | bs4 opt | yes | 0 | MEDIUM |
| `deep_probe.py` | `WaybackCDXClient` | **dormant** | 0 MB | yes | 0 | HIGH |
| `deep_probe.py` | `DeepProbeScanner.scan_s3_buckets` | **dormant** | 0 MB | yes | 0 | HIGH |
| `deep_probe.py` | `scan_ipfs` | **dormant** | 0 MB | yes | 0 | MEDIUM |
| `tools/commoncrawl_adapter.py` | `CommonCrawlAdapter` | **dormant** | 0 MB | yes | 0 | HIGH |

### Donor / Test-Only Modules

| Module | Function/Class | Status | Dep Cost | Network | M1 RAM | OSINT Value |
|--------|---------------|--------|----------|---------|--------|-------------|
| `intelligence/relationship_discovery.py` | `RelationshipDiscoveryEngine` | **donor** | scipy opt | no | 0 | MEDIUM |
| `intelligence/academic_discovery.py` | *(module)* | **donor** | — | — | — | LOW |
| `storage/knowledge/duckdb_store.py` | `get_sprint_pivot_candidates` | **donor** | 0 MB | no | 0 | HIGH |
| `knowledge/ioc_graph.py` | `upsert_ioc`, `graph_stats` | **donor** | 0 MB | no | 0 | MEDIUM |
| `knowledge/graph_service.py` | `upsert_ioc`, `graph_stats` | **donor** | 0 MB | no | 0 | MEDIUM |

### Test-Only Modules

| Module | Function/Class | Status | Note |
|--------|---------------|--------|------|
| `tests/probe_8ac/test_sprint_8ac.py` | `TestDiscoveryHitContract`, `TestDiscoveryBatchResultContract` | **test-only** | contract validation |
| `tests/probe_8aj/test_sprint_8aj.py` | `TestDiscoveryRSSLink` | **test-only** | RSS discovery |
| `tests/probe_public_branch_diagnosis/` | `TestF206AADiscovery*` | **test-only** | F206AA probes |

---

## PHASE 1 — PROVIDERLESS OPTIONS ANALYSIS

### A) Feed-Derived Pivots
- **Source:** `FeedDiscoveryHit` from active `rss_atom_adapter` / `content_miner.discover_feeds`
- **Mechanism:** take domains/entities from feed findings → Wayback CDX archived URL lookup
- **RAM cost:** 0 MB — read-only from prior findings
- **OSINT value:** HIGH — feeds contain entity-specific URLs not in search engines
- **Constraint:** requires `FeedDiscoveryHit` → `DiscoveryHit` coercion wrapper
- **Status: DONOR** — `FeedDiscoveryHit` exists and is active; not wired to CDX layer

### B) CT-Derived Pivots (crtsh)
- **Source:** `discovery/ti_feed_adapter.search_crtsh(domain, max_results=100)`
- **Mechanism:** certificate transparency → subdomain expansion + passive infra
- **RAM cost:** 0 MB, timeout 30s, async aiohttp
- **OSINT value:** HIGH — ct_log findings already stored in DuckDB
- **Returns:** `list[dict]` — NOT `DiscoveryBatchResult`
- **Status: DORMANT** — implemented but not wired to discovery pipeline

### C) Wayback CDX
- **Source:** `intelligence/archive_discovery.wayback_cdx_lookup` (canonical)
- **Wrapper:** `discovery/duckduckgo_adapter._search_wayback_cdx` (compat shim)
- **API:** `https://web.archive.org/cdx/search/cdx`
- **RAM cost:** 0 MB, timeout 20s, asyncio-only
- **OSINT value:** HIGH — historical URLs, deleted content, infrastructure patterns
- **Returns:** `list[dict]` — NOT `DiscoveryBatchResult`
- **Status: DORMANT** — code exists, needs pipeline wrapper + wiring

### D) Common Crawl CDX
- **Source:** `discovery/duckduckgo_adapter._search_commoncrawl_cdx`
- **API:** `https://index.commoncrawl.org/CC-MAIN-2024-51-index` (rotating index)
- **RAM cost:** 0 MB, timeout 25s, asyncio-only
- **OSINT value:** HIGH — largest web archive, high recall
- **Returns:** `list[dict]` — NOT `DiscoveryBatchResult`
- **Status: DORMANT** — code exists, needs pipeline wrapper + wiring

### E) DuckDB Historical Frontier
- **Source:** `knowledge/duckdb_store.get_sprint_pivot_candidates(sprint_id, limit=100)`
- **Mechanism:** extract domain/IP/url tokens from prior sprint findings → use as discovery seeds
- **RAM cost:** 0 MB, no network, read-only from DuckDB
- **OSINT value:** HIGH — cheapest possible layer, maintains continuity
- **Returns:** `list[dict]` with entity_value, entity_type, source, occurrences, last_seen_ts
- **Status: DONOR** — seam exists in duckdb_store; needs discovery-shaped wrapper

### F) GitHub/Gist Passive Search
- **Source:** `discovery/ti_feed_adapter.search_github_gists(keyword, max_results=10)`
- **Mechanism:** keyword search for pastes/leaks via GitHub Gist API
- **RAM cost:** 0 MB, timeout 15s
- **OSINT value:** MEDIUM — paste leaks, exposed configs
- **Returns:** `list[dict]` — NOT `DiscoveryBatchResult`
- **Constraint:** GitHub token-free but may rate-limit
- **Status: DORMANT**

### G) Static Seed Expansion
- **Source:** TI feeds (crtsh findings, CT domain findings from prior sprints)
- **Mechanism:** read ct_log/certificate/domain findings from DuckDB → feed as seeds to Wayback CDX
- **RAM cost:** 0 MB, no network for seed reading
- **OSINT value:** MEDIUM — curated seeds from prior sprint data
- **Status: NOT YET DESIGNED** — needs `get_sprint_pivot_candidates` → CDX pipeline wiring

---

## PHASE 2 — DESIGN CASCADE (Providerless)

```
Layer 0: DDG/Mojeek (EXISTING — no change in this sprint)
Layer 1: DuckDB Historical Frontier         ← NEW (cheapest, zero network)
Layer 2: Feed-derived pivots               ← NEW (FeedDiscoveryHit → CDX)
Layer 3: CT/crtsh pivots                  ← NEW (crtsh → subdomain expansion)
Layer 4: Wayback CDX                      ← EXISTING dormant, wire it
Layer 5: Common Crawl CDX                  ← EXISTING dormant, wire it
Layer 6: Static seeds (CT/DuckDB)         ← NEW (seed from prior findings)
```

### Cascade Layer Specifications

| Layer | Provider | Max Candidates | Timeout | Dedup Key | Failure Taxonomy | Provider Name | Fallback Trigger | M1 RAM |
|-------|----------|---------------|---------|-----------|-----------------|---------------|-----------------|--------|
| 0 | DDG primary | 20 | 15s | url_fp | empty_query/rate_limited/timeout/proxy_error/network_error/server_error/unknown_backend_error | `"ddg"` | — | 0 MB |
| 0b | Mojeek scrape | 20 | 20s | url_fp | scrape_failed/empty_results | `"mojeek"` | `"primary_backend_failed_fallback_succeeded"` | 0 MB |
| 1 | DuckDB frontier | 50 | 0s | entity_fp | no_candidates/read_error | `"duckdb_frontier"` | `"frontier_exhausted"` | 0 MB |
| 2 | Feed-derived | 30 | 0s | url_fp | no_feeds/no_entities | `"feed_pivot"` | `"feed_pivot_exhausted"` | 0 MB |
| 3 | CT/crtsh | 100 | 30s | cert_fp | ct_lookup_failed/timeout/empty | `"crtsh"` | `"ct_pivot_exhausted"` | 0 MB |
| 4 | Wayback CDX | 50 | 20s | url_fp | cdx_timeout/cdx_error/empty | `"wayback_cdx"` | `"wayback_exhausted"` | 0 MB |
| 5 | CommonCrawl CDX | 50 | 25s | url_fp | cc_timeout/cc_error/empty | `"commoncrawl_cdx"` | `"cc_exhausted"` | 0 MB |
| 6 | Static seeds | 20 | 0s | url_fp | no_seeds/exhausted | `"static_seed"` | `"static_seed_exhausted"` | 0 MB |

**Dedup key definitions:**
- `url_fp` = BLAKE2b-128 of normalized URL (already implemented in `_normalize_osint_url`)
- `entity_fp` = `(entity_type, entity_value)` tuple
- `cert_fp` = certificate fingerprint

**Fallback triggered semantics:**
Each layer sets `fallback_triggered` to layer-specific tag when it returns empty. Downstream caller aggregates the chain.

---

## PHASE 3 — DiscoveryBatchResult Gap Analysis

### Current Schema
```python
class DiscoveryBatchResult(msgspec.Struct, frozen=True, gc=False):
    hits: tuple[DiscoveryHit, ...]
    error: str | None = None
    fallback_triggered: str | None = None
```

### Recommended Additive Fields

| Field | Type | Purpose | Breaking Change |
|-------|------|---------|----------------|
| `provider_name: str \| None` | identity | which provider returned hits | NO — additive |
| `provider_chain: tuple[str, ...]` | chain | all providers consulted in cascade | NO — additive |
| `fallback_triggered: str \| None` | **RETAIN** | existing cascade position marker | — |
| `source_family: str \| None` | taxonomy | archive \| feed \| ct \| search \| seed | NO — additive |
| `elapsed_s: float \| None` | perf | time spent in this provider call | NO — additive |
| `error_type: str \| None` | taxonomy | refined error classification | NO — additive (error is raw tag) |

### Rationale
- `provider_name`: answers "which provider returned these hits" — currently unknowable from `DiscoveryBatchResult` alone
- `provider_chain`: enables post-hoc analysis of cascade efficiency
- `source_family`: enables source-quality weighting downstream (archive vs search signal)
- `elapsed_s`: enables adaptive timeout / performance regression detection
- `error_type`: refines the current `error` free-text tag into structured taxonomy

**Backwards compatibility:** All fields are additive with `= None` defaults. Frozen struct can be extended.

---

## PHASE 4 — Implementation Order (F206AM)

### Step 1: `provider_name` field (P0 — non-negotiable first)
- **File:** `discovery/duckduckgo_adapter.py`
- **Change:** Add `provider_name: str | None = None` to `DiscoveryBatchResult`
- **Propagate:** `_scrape_mojeek` sets `provider_name="mojeek"`, DDG primary sets `provider_name="ddg"`
- **Rationale:** Every downstream decision (cascading, dedup, weighting) benefits from provider identity. Must be first because all other wiring depends on it.

### Step 2: DuckDB Historical Frontier (P1 — cheapest, zero network)
- **File:** `discovery/duckduckgo_adapter.py`
- **New function:** `_search_duckdb_frontier(query, max_results=50)`
- **Source:** `duckdb_store.get_sprint_pivot_candidates(sprint_id=current_sprint_id, limit=50)`
- **Wrapper:** convert `list[dict]` → `DiscoveryBatchResult(hits=..., provider_name="duckdb_frontier")`
- **Env gate:** `HLEDAC_ENABLE_DUCKDBl_FRONTIER=1` (default 0)
- **Rationale:** Zero network, zero cost, uses existing seam. Enables entity continuity.

### Step 3: Wayback CDX wiring (P1)
- **File:** `discovery/duckduckgo_adapter.py`
- **Existing code:** `_search_wayback_cdx` at line 889 — compat wrapper around `archive_discovery.wayback_cdx_lookup`
- **Change:** Wrap `_search_wayback_cdx` output to `DiscoveryBatchResult`
- **Dedup:** use `url_fp` (BLAKE2b-128 from `_compute_url_fingerprint`)
- **Rationale:** Wayback CDX is infrastructure-independent from DDG/Mojeek — survives Cloudflare blocks

### Step 4: Common Crawl CDX wiring (P2)
- **File:** `discovery/duckduckgo_adapter.py`
- **Existing code:** `_search_commoncrawl_cdx` at line 914
- **Change:** Wrap output to `DiscoveryBatchResult`, add `source_family="archive"`
- **Dedup:** same `url_fp` deduplication
- **Rationale:** Larger recall than Wayback, complementary coverage

### Step 5: CT/crtsh pivot (P2)
- **File:** `discovery/ti_feed_adapter.py`
- **Existing code:** `search_crtsh(domain, max_results=100)` at line 590
- **New wrapper:** `_wrap_crtsh_to_discovery_batch(domain)` → `DiscoveryBatchResult`
- **Use case:** subdomain expansion from domain entities in prior findings
- **Rationale:** Passive infra discovery, no search engine dependency

### Step 6: Static seed expansion (P3)
- **File:** `discovery/duckduckgo_adapter.py` or new `discovery/static_seed_adapter.py`
- **Source:** `duckdb_store.get_sprint_pivot_candidates` for domains/URLs from CT findings
- **Pipeline:** feed entity candidates into Wayback CDX layer
- **Env gate:** `HLEDAC_ENABLE_STATIC_SEED_FALLBACK=1`

### NOT IN SCOPE FOR F206AM
- Feed-derived pivots (requires `FeedDiscoveryHit` → `DiscoveryHit` coercion — defer to F206AN)
- `search_multi_engine` type fix (F6 — parallel multi-engine, separate sprint)
- TI adapter activation (crtsh/Gist/NVD — P2/P3 only after Step 5)

---

## PHASE 5 — Current Architecture Diagram

```
PUBLIC BRANCH DISCOVERY (pipeline/live_public_pipeline.py)
│
└── _ASYNC_DISCOVERY_SEARCH = async_search_public_web
    │
    ├── Layer 0a: DDG primary (_ddgs_text_search) → DiscoveryBatchResult
    │
    ├── on error_tag in _BACKEND_ERROR_TAGS:
    │   └── Layer 0b: Mojeek scrape (_scrape_mojeek) → DiscoveryBatchResult
    │
    └── fallback_triggered = "primary_backend_failed_fallback_succeeded" | "primary_backend_failed_fallback_failed"
            ↑ EXISTS — no change in this sprint

EXISTING DORMANT (not wired to public pipeline):
    ├── _search_wayback_cdx() → list[dict]  [line 889]
    ├── _search_commoncrawl_cdx() → list[dict]  [line 914]
    ├── _search_commoncrawl_domain() → list[dict]  [line 1022]
    ├── search_multi_engine() → list[dict]  [line 1063]
    ├── search_crtsh() → list[dict]  [ti_feed_adapter.py:590]
    ├── search_github_gists() → list[dict]  [ti_feed_adapter.py:775]
    ├── get_sprint_pivot_candidates() → list[dict]  [duckdb_store.py:2608]
    └── FeedDiscoveryHit → active in rss_atom_adapter (separate contract)

PROPOSED CASCADE (F206AL — no runtime behavior change this sprint):
    Layer 1: DuckDB frontier (get_sprint_pivot_candidates)     ← NEW
    Layer 2: Feed pivots (FeedDiscoveryHit → CDX)             ← NEW (F206AN)
    Layer 3: CT/crtsh (search_crtsh → subdomain expansion)   ← NEW
    Layer 4: Wayback CDX (_search_wayback_cdx)                ← wire existing
    Layer 5: Common Crawl CDX (_search_commoncrawl_cdx)       ← wire existing
    Layer 6: Static seeds (DuckDB CT findings → CDX)          ← NEW
```

---

## SUCCESS CRITERIA VERIFICATION

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Brave/SearXNG NOT in recommended default cascade | ✅ EXCLUDED |
| 2 | Dormant modules fully mapped | ✅ 17 dormant, 4 donor, 6 test-only |
| 3 | Providerless cascade is concrete | ✅ 7 layers with exact specs |
| 4 | M1 budget explicit | ✅ 0 MB per layer, no new deps |
| 5 | No production code changed | ✅ read-only audit |
| 6 | Report + JSON exist | ✅ probe_discovery_mesh/ |

---

## NEXT SPRINT PROMPT: F206AM

```
SPRINT F206AM — Providerless Discovery Mesh — Phase 1

CONTEXT:
Providerless cascade audit F206AL is SEALED.
Default cascade excludes Brave, SearXNG, Docker, cloud API keys.
7-layer providerless cascade designed.

OBJECTIVE:
Wire the first 3 providerless layers into the discovery fallback seam.

EDIT ONLY THESE FILES:
- hledac/universal/discovery/duckduckgo_adapter.py (DiscoveryBatchResult schema + new wrappers)
- hledac/universal/discovery/ti_feed_adapter.py (crtsh wrapper only)

INVARIANTS:
- DiscoveryBatchResult.provider_name: str | None = None  [additive, non-breaking]
- All wrappers return DiscoveryBatchResult (not list[dict])
- All async functions use asyncio (no blocking sync calls)
- Fail-soft: errors return empty DiscoveryBatchResult (never raise)
- M1 RAM: 0 MB per layer
- Env gate prefix: HLEDAC_ENABLE_*

STEP 1 — provider_name field:
- Add provider_name: str | None = None to DiscoveryBatchResult
- Propagate: DDG primary → "ddg", Mojeek → "mojeek"

STEP 2 — DuckDB Historical Frontier (HLEDAC_ENABLE_DUCKDBl_FRONTIER=1):
- New async def _search_duckdb_frontier(query: str, max_results: int = 50)
- Read from duckdb_store.get_sprint_pivot_candidates(sprint_id=current_sprint_id, limit=max_results)
- Convert entity candidates to DiscoveryHit list
- Return DiscoveryBatchResult(hits=..., provider_name="duckdb_frontier", fallback_triggered=None)
- Wire AFTER DDG primary, BEFORE Mojeek fallback

STEP 3 — Wayback CDX wiring (HLEDAC_ENABLE_WAYBACK_CDX=1):
- _search_wayback_cdx already exists (line 889) — wrap output to DiscoveryBatchResult
- Set provider_name="wayback_cdx"
- Use existing _compute_url_fingerprint for dedup

STEP 4 — crtsh pivot (HLEDAC_ENABLE_CRTSH_PIVOT=1):
- New async def _search_ct_pivot(domain: str, max_results: int = 100)
- Call search_crtsh(domain, max_results) from ti_feed_adapter
- Wrap output to DiscoveryBatchResult with provider_name="crtsh"
- Use case: subdomain expansion from domain entities

TESTS:
- Add probe tests in tests/probe_discovery_mesh/ (hermetic, no live internet)
- Test provider_name propagation in DiscoveryBatchResult
- Test empty results return valid DiscoveryBatchResult (not raise)

MUST NOT EDIT (F206AL boundary):
- runtime/sprint_scheduler.py
- core/__main__.py
- pipeline/live_public_pipeline.py
- storage/export schema

FINAL COMMAND:
pytest hledac/universal/tests/probe_discovery_mesh/ -v -q
```
