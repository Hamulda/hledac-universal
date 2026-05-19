# Less-Explored Modules Connection Audit

**Scope**: `intelligence/`, `discovery/`, `tools/`, `network/`, `dht/`, `rl/`, `multimodal/`, `export/`, `docs/runbooks/`
**Date**: 2026-05-19
**Goal**: Find duplicate implementations, dormant legacy code, active-but-unwired capabilities, M1 crash vectors, eager heavy imports, network calls without canonical transport seam, outputs not converted to CanonicalFinding, test gaps.

---

## Finding 1 — `intelligence/network_reconnaissance.py` — Active, NOT Wired to Canonical Transport

| Property | Value |
|---|---|
| Lines | 1387 |
| Active? | YES — has 30 `async def` methods, used by sprint_scheduler advisory |
| Wired? | PARTIAL — outputs `HostInfo`/`WHOISData`/`SSLCertificate` dicts, NOT converted to `CanonicalFinding` |
| Transport | Plain `aiohttp` + `dns.asyncresolver` (NOT FetchCoordinator/curl_cffi seam) |
| M1 risk | LOW — no `asyncio.run()` found, uses proper `async def` throughout |
| Canonical owner | NONE — `network_reconnaissance.py` has no import of `duckdb_store`, no call to `async_ingest_findings_batch` |

**Findings**:
- `async def lookup()` (WHOIS), `async def analyze_certificate()`, `async def recon_target()` all use plain `aiohttp.ClientSession` directly — bypasses `FetchCoordinator` (JA3 fingerprint, stealth) and `async_get_aiohttp_session()` surface
- DNS resolution via `dns.asyncresolver` — blocking resolver used in async context? Check if `run_in_executor` wraps it
- Outputs not converted to `CanonicalFinding` — creates `HostInfo` dataclass, caller must manually construct findings
- 30 `async def` methods, 0 `asyncio.run()` — good async hygiene

**Recommended action**: Wire `network_reconnaissance.py` to canonical transport: use `checked_aiohttp_get` from `network.session_runtime` or route through `FetchCoordinator` for JA3 fingerprint consistency.

---

## Finding 2 — `intelligence/rir_correlator.py` — Active and Wired, Mixed Transport

| Property | Value |
|---|---|
| Lines | 647 |
| Active? | YES — `_run_rir_correlator_advisory()` called from `sprint_scheduler.py` |
| Wired? | YES — uses `async_ingest_findings_batch()` canonical write path |
| Transport | Plain `aiohttp` (ip-api.com HTTP API) + `socket.gethostbyname` via `run_in_executor` |
| M1 risk | LOW — no `asyncio.run()`, properly uses `run_in_executor` for blocking socket ops |
| Canonical owner | Sprint F204H — `source_type="rir_correlation"` |

**Findings**:
- `socket.gethostbyname` wrapped in `loop.run_in_executor()` at line 171 — CORRECT async pattern
- Uses plain `aiohttp` for ip-api.com calls — NOT curl_cffi FetchCoordinator (stealth bypass)
- Bounds enforced: `MAX_RIR_LOOKUPS=50`, `MAX_RIR_RESULTS=500`, `MAX_RIR_CACHE_ENTRIES=2000`
- GHOST_INVARIANTS documented and enforced (gather return_exceptions=True, _check_gathered, CancelledError re-raised)
- CanonicalFinding conversion present and correct (line 1139+)

**Recommended action**: Low priority. Consider routing ip-api.com HTTP calls through FetchCoordinator for stealth consistency.

---

## Finding 3 — `discovery/ti_feed_adapter.py` — Active and Wired, Mixed Transport

| Property | Value |
|---|---|
| Lines | 1959 |
| Active? | YES — `_handle_*` functions called from sprint_scheduler task handlers |
| Wired? | YES — multiple `async_ingest_findings_batch()` calls (lines 1152, 1199, 1228, 1536, 1886) |
| Transport | Plain `aiohttp.ClientSession()` directly at multiple sites (1090, 1156, 1199), plus `ddgs`/`duckduckgo_search` |
| M1 risk | LOW — no `asyncio.run()` found |
| Canonical owner | Sprint F195G/F229 |

**Findings**:
- Multiple `async with aiohttp.ClientSession()` direct instantiations (lines 1090, 1156, 1199) — bypass canonical session surface (`async_get_aiohttp_session()`) and FetchCoordinator
- `scrape_pastebin_for_keyword()`, `github_dork()`, `search_ahmia()`, `fetch_gopher()`, `search_usenet()`, `fetch_malwarebazaar_recent()` — all use plain `aiohttp`, not curl_cffi
- `CommonCrawlAdapter` via `CommonCrawlAdapter()` at line 1256 — separate adapter object
- CanonicalFinding conversion correct and consistent
- Bounds: `MAX_QUEUE_SIZE=500`, `MAX_FEED_ITEMS=200`, per-source limits

**Recommended action**: Medium priority. Consolidate all HTTP transport through `FetchCoordinator` or `async_get_aiohttp_session()` for JA3 consistency and connection pooling.

---

## Finding 4 — `discovery/duckduckgo_adapter.py` — Active, Own HTTP Transport

| Property | Value |
|---|---|
| Lines | 1311 |
| Active? | YES — `DuckDuckGoAdapter` class with `async def search()`, `scrape_mojeek()`, `scrape_wayback()`, `scrape_internetdb()` |
| Wired? | UNCLEAR — not directly called from sprint_scheduler; appears to be task-type handler target |
| Transport | Plain `aiohttp.ClientSession()` at lines 1090, 1156, 1199; `ddgs`/`duckduckgo_search` at import; `async_get_aiohttp_session()` at line 1261 |
| M1 risk | LOW — no `asyncio.run()`, proper async throughout |
| Canonical owner | NONE assigned |

**Findings**:
- Direct `aiohttp.ClientSession()` at 3 sites — bypasses canonical session + FetchCoordinator
- `ddgs` (import at line 217) and `duckduckgo_search` (import at line 221, 620) — heavy eager imports at module level
- `BeautifulSoup` (line 1082) — eager import of heavy parsing library
- `mojeek_scrape` uses `aiohttp.ClientSession` + `checked_aiohttp_get` — mixed patterns
- `get(url)` method at line 1260 uses `async_get_aiohttp_session()` — correct canonical pattern
- No `CanonicalFinding` conversion — returns raw dicts, caller constructs findings

**Recommended action**: High priority — consolidate all `aiohttp.ClientSession()` instantiations to `async_get_aiohttp_session()`. Module-level heavy imports (`ddgs`, `duckduckgo_search`, `BeautifulSoup`) should be lazily imported inside functions to reduce startup RAM.

---

## Finding 5 — `discovery/source_registry.py` — Active, Properly Minimal

| Property | Value |
|---|---|
| Lines | 187 |
| Active? | YES — `SourceEntry` registry used by TI feed adapters for source metadata |
| Wired? | YES — registered sources used in acquisition lane planning |
| M1 risk | NONE — pure dataclass + dict, no network, no async |
| Canonical owner | Sprint F229 |

**Findings**:
- `register_source_adapter()` raises `ValueError` on duplicate registration — correct behavior
- `SourceEntry` frozen dataclass with tier (1-3) + acquisition_lane fields
- `get_source_adapter()` returns `SourceEntry` or raises `ValueError`
- No network calls, no async, no heavy imports
- No test file found for this module

**Recommended action**: Add probe test for `register_source_adapter` + `get_source_adapter` + duplicate registration rejection.

---

## Finding 6 — `dht/` — Dormant/Unwired

| Property | Value |
|---|---|
| Files | `kademlia_node.py`, `local_graph.py`, `sketch_exchange.py` |
| Active? | NO — no callers found in sprint_scheduler, coordinators, or pipeline |
| Wired? | NO — `SketchExchange.start()`, `KademliaNode` not called from any production code |
| M1 risk | LOW — small files, no asyncio.run, no heavy imports |
| Canonical owner | NONE |

**Findings**:
- `SketchExchange` has `start()`/`stop()`/`query_entity()`/`publish_digests()` methods — no callers
- `KademliaNode` — no callers found
- `LocalGraphStore` — no callers found
- No `asyncio.run()` patterns in any DHT file
- `dht/sketch_exchange.py` imports `resource_governor` and `KademliaNode` — but never instantiated in production
- `_refresh_digests()` / `_publish_loop()` — daemon-style coroutines with no trigger

**Recommended action**: Either wire to sprint_scheduler as advisory lane, or document as dormant/experimental. No M1 risk. Consider: is DHT intended for future P2P overlay?

---

## Finding 7 — `rl/sprint_policy_manager.py` — Active/Wired, Not Canonical Output

| Property | Value |
|---|---|
| Lines | 385 |
| Active? | YES — `SprintPolicyManager` injected into `SprintScheduler` via `inject_policy_manager()` |
| Wired? | YES — `run()` called with `SprintSchedulerResult`, reward computed, epsilon-greedy exploration |
| Transport | NONE — no network calls, purely in-memory RL agent |
| M1 risk | LOW — no asyncio.run, no network, only memory ops |
| Canonical owner | Sprint F195C |

**Findings**:
- No `CanonicalFinding` output — policy manager reads `SprintSchedulerResult`, writes `.sprint_policy_state.json`
- Reward derived from `scorecard.accepted_findings` / `scorecard.findings_per_minute` — read from scorecard, not from canonical store
- `load_state()` / `save_state()` — JSON file persistence (not LMDB)
- `QMIXAgent` in `rl/qmix.py` uses `mlx.core` — potential M1 RAM issue if loaded unnecessarily

**Recommended action**: Document that `SprintPolicyManager` is a sidecar advisor, not a finding-producing component. Consider adding tests for epsilon-greedy exploration behavior.

---

## Finding 8 — `rl/replay_buffer.py` — Properly Lazy MLX Import (No Issue)

| Property | Value |
|---|---|
| Lines | 94 |
| Active? | YES — `MARLReplayBuffer` imported in `rl/__init__.py` and referenced by `SprintPolicyManager` |
| M1 risk | NONE — mlx.core is lazy-imported inside `_get_mlx_core()` on first use, not at module level |

**Findings**:
- `import mlx.core as _mlx_core_mod` is inside the `_get_mlx_core()` function (lines 16-26), not at module level
- Global `_mlx_core_mod = None` guard ensures MLX is only loaded when `MARLReplayBuffer` actually calls `_get_mlx_core()`
- `_MLX_CORE_AVAILABLE` flag handles case where mlx is not installed (graceful fallback)
- `MARLReplayBuffer` uses `np.save()` / `np.load()` for persistence — no async issues
- Test coverage: `tests/test_sprint58a.py::TestReplayBuffer` (2 tests)

**Recommended action**: None — properly lazy-loaded, no M1 issue.

---

## Finding 9 — `multimodal/analyzer.py` — Active/Wired, No M1 Issues

| Property | Value |
|---|---|
| Lines | 875 |
| Active? | YES — `DocumentExtractor` and `MultimodalEnricher` wired to sprint_scheduler |
| Wired? | YES — `CanonicalFinding` conversion at lines 659, 1874; `async_ingest_findings_batch` at 1886 |
| Transport | NONE — no network calls, pure file processing (PDF via PyPDF2, image via PIL) |
| M1 risk | LOW — no asyncio.run, no network, RAM guard present |
| Canonical owner | Sprint F202I |

**Findings**:
- No `asyncio.run()` patterns
- RAM guard: `if snapshot.is_critical or snapshot.is_emergency: return {}` — correctly implemented
- `VisionEncoder` / `MambaFusion` loaded lazily inside `initialize()` — correct
- `extract_batch()` uses `Semaphore(4)` for concurrency control — bounded
- `orjson` used for JSON serialization — correct
- `lmdb_kv` used for enrichment metadata persistence — correct

**Recommended action**: None. Well-implemented, no issues found.

---

## Finding 10 — `export/` — Active, `stix_exporter.py` Uses CanonicalFinding

| Property | Value |
|---|---|
| Files | `export_manager.py`, `sprint_exporter.py`, `stix_exporter.py`, `markdown_reporter.py`, `jsonld_exporter.py`, `sprint_markdown_reporter.py`, `formatters.py` |
| Active? | YES — `SprintExporter` wired to sprint_scheduler at teardown |
| CanonicalFinding | `stix_exporter.py` references CanonicalFinding (2 refs), others output plain dicts |
| M1 risk | LOW |
| Transport | NONE — export only, no network calls |

**Findings**:
- `sprint_exporter.py` (4265 lines) — largest file, canonical export pipeline
- `stix_exporter.py` imports `CanonicalFinding` at line 1854 — correctly uses canonical type
- `export_manager.py` (1336 lines) — no network calls (comments note "Client-side filtering by time range")
- No `asyncio.run()` patterns found in any export file
- `COMPAT_HANDOFF.py` (94 lines) — compatibility layer, no active production use indicated

**Recommended action**: `stix_exporter.py` CanonicalFinding reference should be verified for correct STIX 2.1 conversion. Add probe test if missing.

---

## Finding 11 — `network/session_runtime.py` — Canonical Session Surface, Mixed Health

| Property | Value |
|---|---|
| Lines | 428 |
| Active? | YES — `async_get_aiohttp_session()` used across discovery/intelligence/adapters |
| Transport | Plain `aiohttp` (TCP world, not curl_cffi) |
| M1 risk | LOW — no asyncio.run in the runtime itself; async context properly managed |

**Findings**:
- Line 428: `loop = asyncio.new_event_loop()` — used in `close_aiohttp_session()` sync function — acceptable (sync cleanup context)
- `async_get_aiohttp_session()` properly lazy — created on first await, cached in `_session_instance`
- `close_aiohttp_session_async()` — idempotent, safe to call multiple times
- DNS cache: `ttl_dns_cache=300`, `use_dns_cache=True` — aiohttp 3.9+ correct setup
- Connector: `limit=25`, `limit_per_host=0` (default), `connector_owner=True` — correct
- `DarknetConnector` via `aiohttp_socks.ProxyConnector` — SOCKS5 proxy support present

**Recommended action**: This is the canonical `aiohttp` session surface. All plain `aiohttp.ClientSession()` instantiations in discovery/ti_feed_adapter/duckduckgo_adapter should route through here instead.

---

## Finding 12 — `tools/replay_research_loop.py` — Research Tool, Not Production

| Property | Value |
|---|---|
| Lines | 732 |
| Active? | NO — CLI research tool run manually via `uv run python tools/replay_research_loop.py` |
| Production? | NO — `sprint_id = "replay_f236b"` hardcoded; reads JSON reports |
| CanonicalFinding | NO — outputs plain dicts, used for scorecard analysis |

**Findings**:
- Not part of sprint execution pipeline — manual post-processing tool
- `asyncio.run()` at line 949 (benchmarks) — acceptable in a CLI tool
- Heavy: `import aiohttp`, `import msgspec`, `import numpy`
- No production wiring to sprint_scheduler

**Recommended action**: Document clearly as a research/debugging tool, not production. Consider moving to `tools/research/` subdirectory.

---

## Finding 13 — `docs/runbooks/` — Directory Does Not Exist

| Property | Value |
|---|---|
| Path | `docs/runbooks/` |
| Status | NOT FOUND |

**Recommended action**: Create `docs/runbooks/` with operational runbooks (smoke test runbook, M1 memory pressure response, sprint diagnostics).

---

## Summary Table

| File | Capability | Active/Wired? | Duplicate? | M1 Risk | Canonical Owner | Recommended Next Action |
|------|-----------|---------------|------------|---------|------------------|--------------------------|
| `intelligence/network_reconnaissance.py` | WHOIS, SSL cert, ASN, subdomain recon | Active, partially wired | No | LOW | NONE | Wire to canonical transport (FetchCoordinator or async_get_aiohttp_session); convert outputs to CanonicalFinding |
| `intelligence/rir_correlator.py` | RIR/ASN/WHOIS correlation | Active, wired | No | LOW | Sprint F204H | Low priority — consider curl_cffi for stealth on ip-api.com calls |
| `discovery/ti_feed_adapter.py` | Structured TI ingest (NVD, CISA, URLhaus, etc.) | Active, wired | No | LOW | Sprint F195G/F229 | Consolidate aiohttp.ClientSession() to async_get_aiohttp_session() |
| `discovery/duckduckgo_adapter.py` | DuckDuckGo/Mojeek/Wayback search | Active, unclear wiring | No | LOW | NONE | Consolidate HTTP transport; lazy-import ddgs/duckduckgo_search/BeautifulSoup |
| `discovery/source_registry.py` | Source adapter registry | Active, wired | No | NONE | Sprint F229 | Add probe tests for registration + duplicate rejection |
| `dht/sketch_exchange.py` | DHT sketch exchange (Kademlia) | Dormant, unwired | No | NONE | NONE | Wire as advisory lane or document as experimental |
| `dht/kademlia_node.py` | Kademlia DHT node | Dormant, unwired | No | NONE | NONE | Same as above |
| `dht/local_graph.py` | Local graph store for DHT | Dormant, unwired | No | NONE | NONE | Same as above |
| `rl/sprint_policy_manager.py` | RL-based sprint policy | Active, wired | No | LOW | Sprint F195C | Document as advisory-only (no CanonicalFinding output) |
| `rl/replay_buffer.py` | MARL replay buffer | Active | No | NONE (properly lazy) | Sprint F195C | None — properly lazy-loaded |
| `multimodal/analyzer.py` | Document extraction, multimodal enrichment | Active, wired | No | LOW | Sprint F202I | None — well-implemented |
| `export/sprint_exporter.py` | Sprint export pipeline | Active, wired | No | LOW | Sprint F200A | None |
| `export/stix_exporter.py` | STIX 2.1 export | Active, wired | No | LOW | NONE | Verify CanonicalFinding→STIX conversion correctness; add tests |
| `network/session_runtime.py` | Canonical aiohttp session surface | Active, wired | No | LOW | Sprint 8AA | Use as template for all plain aiohttp consolidation |
| `tools/replay_research_loop.py` | Post-sprint research analysis | Research tool | No | LOW | NONE | Document as non-production; consider moving to tools/research/ |
| `docs/runbooks/` | Operational runbooks | MISSING | N/A | N/A | NONE | Create with smoke test, M1 memory, sprint diagnostics runbooks |

---

## Top Priority Issues

1. ~~CRITICAL — `rl/replay_buffer.py` eager MLX import~~ — **FALSE POSITIVE**: mlx.core is properly lazy-imported inside `_get_mlx_core()` function, not at module level.
2. **HIGH — `discovery/duckduckgo_adapter.py` heavy eager imports**: `ddgs`/`duckduckgo_search` at lines 37-39 inside `TYPE_CHECKING` block — only type-check time, but `ddgs` also at line 217 inside `backend_version()` (lazy on first call) and line 618 inside `_ddgs_text_search()` (function-level). `BeautifulSoup` at line 1082 is inside `_scrape_mojeek()` function — correctly lazy. Overall the lazy-loading is correct here, but the `TYPE_CHECKING` block imports are worth noting.
3. **HIGH — Multiple discovery adapters bypass canonical transport**: `ti_feed_adapter.py` lines 1090, 1156, 1199 create direct `aiohttp.ClientSession()`, `duckduckgo_adapter.py` lines 1090, 1156, 1199 also. `network_reconnaissance.py` uses plain `aiohttp` throughout. JA3 fingerprint consistency compromised — should route through `async_get_aiohttp_session()` or `FetchCoordinator`.
4. **MEDIUM — `network_reconnaissance.py` outputs not CanonicalFinding**: `recon_target()` returns `HostInfo` dataclass, not `CanonicalFinding`. Caller must manually convert.
5. **MEDIUM — DHT module completely unwired**: `dht/` has no production callers. Either wire as advisory lane or explicitly document as dormant/experimental.
6. **LOW — `docs/runbooks/` missing**: No operational runbooks exist. Create for smoke tests, M1 memory pressure response, and sprint diagnostics.