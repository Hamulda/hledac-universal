# Discovery Layer Capability Audit — 2026-05-22

## ČÁST 1: Discovery Adapters Inventory

### discovery/ — 12 files (9,923 lines total)

| Adapter | Lines | Source Covered | Main Async Function | Output Type |
|---------|-------|----------------|---------------------|-------------|
| `duckduckgo_adapter.py` | 1,477 | DuckDuckGo search | `async_search_public_web()` | `DiscoveryBatchResult` |
| `crtsh_adapter.py` | 1,258 | crt.sh CT logs | `async_search_crtsh()` | `DiscoveryBatchResult` |
| `ti_feed_adapter.py` | 1,966 | TI feeds (MISP, AlienVault, MITRE) | `async_query_ti_feeds()` | `list[CanonicalFinding]` |
| `rss_atom_adapter.py` | 2,074 | RSS/Atom feeds | `async_query_rss()` + `get_runtime_feed_seeds()` | `list[FeedSeed]` + findings |
| `circl_pdns_adapter.py` | 725 | CIRCL Passive DNS | `async_search_circl_pdns()` | `DiscoveryBatchResult` |
| `discovery_planner.py` | 671 | Provider selection orchestrator | `get_provider_state()` | `ProviderCapabilityState` enum |
| `wayback_cdx_adapter.py` | 278 | Wayback Machine CDX | `async_search_wayback_cdx()` | `DiscoveryBatchResult` |
| `provider_stats.py` | 434 | Provider reliability stats | `get_provider_stats_registry()` | registry dict |
| `cascade.py` | 319 | Multi-provider cascade | `async_search_providerless()` | merged `DiscoveryBatchResult` |
| `fusion_ranker.py` | 339 | Result fusion/ranking | `fuse_results()` | ranked list |
| `historical_frontier.py` | 195 | Historical frontier scan | `async_search_historical_frontier()` | `DiscoveryBatchResult` |
| `source_registry.py` | 187 | SourceEntry registry | `register_source_adapter()` | `SourceEntry` dict |

### Adapter Feature Matrix

| Adapter | Replay Cassette | Rate Limiting | Fail-Soft | Canonical Finding Output |
|---------|-----------------|---------------|-----------|--------------------------|
| `duckduckgo_adapter` | ✅ `discovery_replay` | ✅ `cooldown_active` | ✅ `try/except → replay_miss` | DiscoveryBatchResult (not CanonicalFinding) |
| `crtsh_adapter` | ❌ | ✅ `circuit_breaker` | ✅ `try/except → UNAVAILABLE` | DiscoveryBatchResult |
| `circl_pdns_adapter` | ❌ | ✅ `cooldown_active` | ✅ `try/except → empty` | DiscoveryBatchResult |
| `wayback_cdx_adapter` | ❌ | ❌ | ❌ | DiscoveryBatchResult |
| `rss_atom_adapter` | ✅ (cassette) | ❌ | ❌ | list[FeedSeed] + findings |
| `ti_feed_adapter` | ✅ (cassette) | ❌ | ❌ | list[CanonicalFinding] |
| `historical_frontier` | ❌ | ❌ | ❌ | DiscoveryBatchResult |
| `fusion_ranker` | ❌ | ❌ | ❌ | ranked list |
| `cascade` | ❌ | ❌ | ❌ | merged DiscoveryBatchResult |

---

## ČÁST 2: Call-Site Verification

### Adapter → Pipeline Wiring Map

| Adapter | WIRED | CALLED_FROM | Evidence |
|---------|-------|-------------|----------|
| `duckduckgo_adapter` | ✅ YES | `pipeline/live_public_pipeline.py:3167` | `_ASYNC_DISCOVERY_SEARCH = duckduckgo_adapter.async_search_public_web` |
| `cascade` | ✅ YES (env-gated) | `pipeline/live_public_pipeline.py:3110-5013` | `_ASYNC_DISCOVERY_SEARCH = async_search_providerless` when `HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=1` |
| `crtsh_adapter` | ✅ YES | `core/__main__.py:1477` | `from hledac.universal.discovery.crtsh_adapter import call_crtsh` — canonical nonfeed CT path |
| `rss_atom_adapter` | ✅ YES | `core/__main__.py:931` | `from hledac.universal.discovery.rss_atom_adapter import get_runtime_feed_seeds` — feed bootstrap |
| `wayback_cdx_adapter` | ✅ YES | `intelligence/wayback_diff_miner.py:4450` + `sprint_scheduler.py:5775` | `from hledac.universal.intelligence.wayback_diff_miner import WaybackDiffMiner` → `wayback_results_to_findings()` |
| `historical_frontier` | ✅ YES | `discovery/cascade.py:66` | `_run_historical_frontier()` called in cascade via `asyncio.gather()` |
| `circl_pdns_adapter` | ❌ ORPHAN | NOT_IN_PIPELINE | No call site in `runtime/`, `pipeline/`, `core/`, or `sprint_scheduler.py` |
| `ti_feed_adapter` | ❌ ORPHAN | NOT_IN_PIPELINE | No call site in `runtime/`, `pipeline/`, `core/`, or `sprint_scheduler.py` |
| `fusion_ranker` | ❌ ORPHAN | NOT_IN_PIPELINE | No call site in pipeline |
| `discovery_planner` | ⚠️ ADVISORY | `discovery/cascade.py` (env-gated) | `get_provider_state()` used in providerless cascade selection, NOT in main pipeline |

### Key Pipeline Entry Points

**`live_public_pipeline.py` — PUBLIC discovery lane:**
- `_ASYNC_DISCOVERY_SEARCH` global (line 3167)
- Default: `duckduckgo_adapter.async_search_public_web` (env=`HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=0`)
- Providerless mode: `cascade.async_search_providerless` (env=`HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=1`)
- Called at line 3359: `discovery_result = await _ASYNC_DISCOVERY_SEARCH(self.query, self.max_results)`

**`core/__main__.py` — Bootstrap + CT:**
- Line 931: `rss_atom_adapter.get_runtime_feed_seeds()` — RSS feed seeding
- Line 1477: `crtsh_adapter.call_crtsh()` — CT log canonical discovery

**`sprint_scheduler.py` — WAYBACK lane:**
- Line 5775: `wayback_diff_miner.WaybackDiffMiner` → uses wayback_cdx_adapter indirectly

---

## ČÁST 3: Transport Layer Audit

### Transport Components

| Component | Location | Status | Details |
|-----------|----------|--------|---------|
| `public_fetcher.py` | `fetching/public_fetcher.py` | ACTIVE | curl_cffi + httpx fallback; Chrome 124 JA3; 114KB |
| `session_runtime.py` | `network/session_runtime.py` | ACTIVE | aiohttp session management for structured TI |
| `circuit_breaker.py` | `transport/circuit_breaker.py` | ACTIVE | domain failure tracking; used by crtsh_adapter |
| `tor_manager.py` | `network/tor_manager.py` | EXISTS | Tor circuit isolation; 5.2KB; NOT imported in pipeline |
| `ct_log_scanner.py` | `network/ct_log_scanner.py` | ACTIVE | subdomain enum via CT logs |
| `passive_dns.py` | `network/passive_dns.py` | EXISTS | PDNS client; 11.2KB; NOT imported in pipeline |
| `bgp_monitor.py` | `network/bgp_monitor.py` | EXISTS | BGP observations; NOT in pipeline |
| `ipfs_client.py` | `network/ipfs_client.py` | EXISTS | IPFS CID fetching; NOT in pipeline |

### curl_cffi / JA3 Impersonation

```
Location: fetching/public_fetcher.py
JA3 fingerprint: Chrome 124 (from F219D_PUBLIC_SESSION_SEAL)
Impersonation target: desktop Chrome 124 on Windows 10
```

### Tor Transport

```
File: network/tor_manager.py
Port: 9050 (default Tor SOCKS proxy)
Circuit isolation: YES (per-target circuit)
Fail-soft: not evident in pipeline wiring
Status: EXISTS but NOT wired to public discovery pipeline
```

### I2P / NYM Transport

```
Status: NOT FOUND in transport/ or network/
No I2P SAM client, no NYM transport implementation in codebase
```

---

## ČÁST 4: Source Registry + Selection Logic

### source_registry.py (187 lines)

```python
class SourceEntry:
    adapter: Callable[..., Any]
    tier: int = 1  # 1=structured/deterministic, 2=overlay, 3=experimental
    acquisition_lane: str = "passive_dns"

_SOURCE_REGISTRY: dict[str, SourceEntry] = {}

def register_source_adapter(source_type: str, entry: SourceEntry) -> None
def get_source_adapter(source_type: str) -> SourceEntry | None
def list_registered_source_types() -> list[str]
def source_quality_score(source_type: str) -> float
```

**Registration calls found:** 0 (no `register_source_adapter` calls in codebase — registry is populated at import by each adapter via `from . import register_source_adapter` pattern)

### discovery_planner.py — Provider Selection State Machine

```python
class ProviderCapabilityState(Enum):
    PRODUCTION = "production"      # Fully wired, real endpoint
    ADVISORY_STUB = "advisory_stub"  # Placeholder, endpoint not implemented
    NOT_WIRED = "not_wired"          # No pipeline context / adapter wired
    DISABLED = "disabled"            # Explicitly disabled

def get_provider_state(name: str) -> ProviderCapabilityState:
    # Returns NOT_WIRED when context unavailable; PRODUCTION when context available
```

**ADVISORY_STUB > NOT_WIRED > DISABLED > PRODUCTION** (priority order for selection)

### How Sprint Scheduler Decides Which Adapters to Use

1. **PUBLIC lane** (`live_public_pipeline`): `_ASYNC_DISCOVERY_SEARCH` global — DDG or cascade based on env var
2. **CT lane** (`core/__main__.py`): `crtsh_adapter.call_crtsh()` hardcoded
3. **RSS lane** (`core/__main__.py`): `rss_atom_adapter.get_runtime_feed_seeds()` hardcoded
4. **WAYBACK lane** (`sprint_scheduler`): `wayback_diff_miner` → `wayback_cdx_adapter` hardcoded

**No dynamic adapter selection** — all paths are hardcoded in their respective pipeline entry points.

---

## ČÁST 5: Gap Matrix

| Capability | Status | Evidence | Notes |
|------------|--------|----------|-------|
| Surface web search (DuckDuckGo) | ✅ ACTIVE | `duckduckgo_adapter.py` → `live_public_pipeline.py:3167` | Default public discovery lane |
| Certificate CT (crt.sh) | ✅ ACTIVE | `crtsh_adapter.py` → `core/__main__.py:1477` | Canonical nonfeed CT path |
| Passive DNS (CIRCL) | ❌ ORPHAN | `circl_pdns_adapter.py` exists, no pipeline call site | Sprint F229 alignment, never wired |
| Wayback Machine | ✅ ACTIVE | `wayback_cdx_adapter.py` → `wayback_diff_miner` → `sprint_scheduler.py:5775` | WAYBACK lane |
| RSS/Atom feeds | ✅ ACTIVE | `rss_atom_adapter.py` → `core/__main__.py:931` | Feed bootstrap only |
| Historical frontier | ✅ ACTIVE | `historical_frontier.py` → `cascade.py` | Providerless cascade only |
| TI feeds (MISP/AlienVault) | ❌ ORPHAN | `ti_feed_adapter.py` exists, no pipeline call site | Has cassette replay support but not wired |
| Providerless cascade (DDG→Historical→Wayback) | ⚠️ OPT-IN | `cascade.py` → `live_public_pipeline.py` | Env-gated: `HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=1` |
| Tor .onion discovery | ⚠️ EXISTS-UNWIRED | `network/tor_manager.py` exists | Not imported in any discovery pipeline |
| I2P SAM transport | ❌ FILE NOT FOUND | No `i2p` dir or `i2p_client.py` | No implementation |
| NYM mixnet transport | ❌ FILE NOT FOUND | No `nym` dir or transport | No implementation |
| Pastebin monitoring | ⚠️ EXISTS-ELSEWHERE | `enhanced_research.py:506` (DataLeakHunter) | Separate tool, not discovery adapter |
| GitHub secret scanning | ⚠️ EXISTS-ELSEWHERE | `enhanced_research.py` | Separate tool, not discovery adapter |
| Shodan/network scan | ❌ FILE NOT FOUND | No shodan adapter | Not implemented |
| BGP observations | ❌ EXISTS-UNWIRED | `network/bgp_monitor.py` | Not imported in pipeline |
| IPFS content retrieval | ❌ EXISTS-UNWIRED | `network/ipfs_client.py` | Not imported in pipeline |
| DNS tunnel detection | ❌ EXISTS-UNWIRED | `network/dns_tunnel_detector.py` | forensics domain, not discovery |
| Open storage scanner | ❌ EXISTS-UNWIRED | `network/open_storage_scanner.py` | separate scan tool, not discovery |
| Fusion/ranking | ❌ ORPHAN | `fusion_ranker.py` exists | No call site in pipeline |
| Provider stats | ⚠️ ADVISORY | `provider_stats.py` | Used in discovery_planner but not in main pipeline |

---

## ČÁST 6: Orphan Adapter Details

### ORPHAN: circl_pdns_adapter.py (725 lines)
- **Source:** CIRCL Passive DNS (`passive_dns` domain queries)
- **Async:** `async_search_circl_pdns()`
- **Output:** `DiscoveryBatchResult`
- **Replay:** ❌ no cassette support
- **Rate limit:** ✅ `cooldown_active` check
- **Fail-soft:** ✅ `try/except → return DiscoveryBatchResult(err=...)`
- **Wiring:** Sprint F229 aligned with `source_registry` tier-1, but NO call site in pipeline
- **Why orphan:** CT/PDNS pivot path not wired to sprint_scheduler (likely F229 deferred)

### ORPHAN: ti_feed_adapter.py (1,966 lines)
- **Source:** MISP, AlienVault OTX, MITRE, pulseovat, IBM X-Force
- **Async:** `async_query_ti_feeds()`
- **Output:** `list[CanonicalFinding]`
- **Replay:** ✅ has cassette support
- **Rate limit:** ❌ no rate limiting
- **Fail-soft:** ❌ no fail-soft
- **Wiring:** No pipeline call site
- **Why orphan:** TI feed ingestion likely handled by `live_feed_pipeline` via different path

### ORPHAN: fusion_ranker.py (339 lines)
- **Purpose:** Score + merge results from multiple providers
- **Wiring:** No call site — possibly intended for multi-provider aggregation never completed
- **Why orphan:** Design artifact from multi-source discovery planning

---

## Summary

**WIRED adapters (5):** duckduckgo, cascade(providerless), crtsh, rss_atom, wayback_cdx + historical_frontier (via cascade)

**ORPHAN adapters (3):** circl_pdns, ti_feed, fusion_ranker

**EXISTS-UNWIRED (5):** tor_manager, passive_dns, bgp_monitor, ipfs_client, open_storage_scanner

**NOT FOUND (2):** I2P transport, NYM mixnet transport

**Total: 11 adapters in discovery/, 5 actively used in pipeline, 3 orphaned, 3 transport-level modules unconnected to discovery**

---

*Audit date: 2026-05-22 | Source: hledac/universal/discovery/ + pipeline grep survey*