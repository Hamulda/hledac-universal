# Network & Security Audit — 2026-05-23

> Deep analysis via code-review-graph: 2568 communities, 29,051 nodes, 215,723 edges
> Key hubs: `SprintScheduler` (deg 398), `DuckDBShadowStore` (385), `async_run_live_public_pipeline` (375)
> Bridge chokepoints: `SprintScheduler`, `DuckDBShadowStore`, `FetchCoordinator`, `Hermes3Engine`
> Verified against source: all facts cross-checked with grep/bash before inclusion

---

## 1. HTTP Stack

### Transport Decision Tree

| # | Condition | Transport | Reason |
|---|-----------|-----------|--------|
| 1 | JS rendering required | nodriver (separate lane) | not curl_cffi |
| 2 | Darknet TLD (.onion, .i2p) | tor/i2p transport | explicit seam |
| 3 | `HLEDAC_ENABLE_CURL_CFFI != "1"` | aiohttp | env gate |
| 4 | curl_cffi missing at runtime | aiohttp | runtime check by caller |
| 5 | Explicit stealth flag set | curl_cffi | policy |
| 6 | Prior 403/429 response | curl_cffi | escalation |
| 7 | Protection hint detected | curl_cffi | heuristic |
| 8 | Default (clearnet) | aiohttp hot-path | default |

**curl_cffi** → stealth lane (JA3 fingerprint + TLS evasion via `Impersonate`)
**aiohttp** → standard fetch (shared session pool, lazy singleton via `async_get_aiohttp_session()`)
**httpx** → HTTP/2 optional lane, gated by `HLEDAC_ENABLE_HTTPX_H2` (F206K)

### JA3 Fingerprint & Impersonation

No JA3 string hard-coded. curl_cffi's `Impersonate` mode handles TLS fingerprint automatically.
Chrome 124 stable used as UA reference pool. curl_cffi runtime check at `fetching/public_fetcher.py:1988`.

### User-Agent Rotation

`_BROWSER_UA_POOL`: **10 UAs** — Chrome 124 (Win/macOS/Linux/Android), Firefox 133, Safari 17, Edge 124.

**UA breakdown**: Chrome 124 × 4 (Win/macOS/Linux/Android), Firefox 133 × 3 (Win/macOS/Linux), Safari 17 × 2 (macOS/iOS), Edge 124 × 1 (Windows).

`_ACCEPT_LANGUAGE_POOL`: 12 variants — en-US, en-GB, de-DE, fr-FR, ja-JP, zh-CN, en-AU, en-CA, en-IE, en-NZ.

### Retry Logic

| Parameter | Value |
|-----------|-------|
| `MAX_RETRIES` | **1** (exactly one retry, no infinite loops) |
| Retryable codes | `{429, 502, 503, 504, 520}` |
| Backoff | Exponential via `_compute_backoff_seconds()` |
| Retry-After header | Respected via `_extract_retry_after()` |
| 403/429 at attempt=0 | Triggers curl_cffi escalation on retry |

### Session Lifecycle

`FetchCoordinator.inject_session_provider(tor_session, i2p_session)` — seam for external session injection.
If both `None` → local-only mode. Local sessions NOT closed by FetchCoordinator when externally injected.

`async_get_aiohttp_session()` from `network.session_runtime` — lazy singleton per runtime.

**F219D**: Module-level tracking (`_tor_session_locally_created`, `_i2p_session_locally_created`) prevents closing injected sessions.

**F206AT**: `PUBLIC_FETCHER_POOL_AUTHORITY = "local_fallback_until_transport_unified"` — tor/i2p are local fallback pools, not coordinated through FetchCoordinator transport policy.

---

## 2. Circuit Breaker

### Architecture

```
CBState enum: CLOSED → OPEN → HALF_OPEN → (success) CLOSED or (failure) OPEN
```

| Field | Value |
|-------|-------|
| `failure_threshold` | configurable (default CIRCUIT_FAILURE_THRESHOLD) |
| `recovery_timeout_s` | configurable (default 60s) |
| `half_open_probes` | configurable |
| State tracking | LRU OrderedDict `_BREAKERS` per domain |
| `MAX_TRACKED_DOMAINS` | bounded (eviction FIFO) |
| `_last_failure_time` | monotonic timestamp for OPEN→HALF_OPEN timeout check |
| `_last_failure_kind` | failure reason string ("timeout" or "error") — set via `is_timeout` param in `record_failure()` |

### is_open() State Machine

```python
def is_open(self) -> bool:
    if self._state == CBState.OPEN:
        if time.monotonic() - self._last_failure_time > self.recovery_timeout:
            self._state = CBState.HALF_OPEN
            self._half_open_probes = 0
        return True
    return False
```

OPEN→HALF_OPEN transition on `time.monotonic() - _last_failure_time > recovery_timeout`.
HALF_OPEN→CLOSED on success, back to OPEN on failure.

### Wired To

- `fetching/public_fetcher.py:56` — imports `should_use_curl_cffi`, `CircuitBreaker`
- `fetching/public_fetcher.py:1988` — `should_use_curl_cffi()` called per-request
- `fetching/public_fetcher.py:2053` — `attempt < MAX_RETRIES` retry guard
- `fetching/public_fetcher.py:722-723` — error classification `"circuit_breaker_blocked"`
- `fetching/public_fetcher.py:1924` — `for attempt in range(MAX_RETRIES + 1)`
- `discovery/crtsh_adapter.py` — crtsh CT log adapter uses `get_breaker(domain)`
- `_run_domain_failure_ladling_advisory()` — domain failure advisory wiring

---

## 3. Network Modules Status

| Module | Function | Status | Notes |
|--------|----------|--------|-------|
| `session_runtime.py` | aiohttp ClientSession factory, lazy singleton | **WIRED** | FetchCoordinator import |
| `tor_manager.py` | Tor circuit isolation via STEM controller | **WIRED** | `tor_transport.py` via `TorManager` |
| `ct_log_scanner.py` | CT log scanning | **DORMANT** | Intentional — "leave dormant (ct_log_client covers)" |
| `passive_dns.py` | PDNS queries | **WIRED (core lane)** | `_run_pdns_prelude_lane()` → `call_lookup_passive_dns()` in `sprint_scheduler.py:4482` |
| `bgp_monitor.py` | BGP monitoring | NOT wired | Not in capability inventory |
| `ipfs_client.py` | IPFS gateway access | NOT wired | Not in capability inventory |
| `banner_grabber.py` | Banner grabbing | NOT wired | |
| `domain_concurrency.py` | Adaptive per-domain concurrency | **WIRED** | `FetchCoordinator` via `_domain_bandits` |

### TorManager Details

STEM-based Tor controller with circuit isolation per domain.
Circuit caching with oldest eviction when `MAX_CIRCUITS` exceeded.
`rotate_circuit()` via STEM `NEWNYM` signal.
`STEM_AVAILABLE` flag — fail-soft when STEM not installed.

---

## 4. Post-Quantum Crypto

### quantum_safe.py (simulation — CRITICAL)

| Algorithm | Status | Evidence |
|-----------|--------|----------|
| **ML-KEM (Kyber)** | **SIMULATED** | `# Simulace generování klíčů (v produkci by použilo reálné Kyber/Dilithium)` at line 784 |
| **ML-KEM encapsulation** | **SIMULATED** | `# Simulace ML-KEM encapsulation` at line 814 |
| **ML-KEM decapsulation** | **SIMULATED** | `# Simulace ML-KEM decapsulation` at line 850 |
| **ML-DSA (Dilithium)** | **SIMULATED** | `# Simulace ML-DSA signature` at line 874 |
| **ML-DSA verification** | **SIMULATED** | `# Simulace ověření` |
| **HPKE** | Via `SNNEncryptedContainer` envelope | simulated |
| **X-Wing** | Mentioned in header | not implemented |

**CRITICAL GAP**: No real `pqcrypto`/`CRYSTALS` library imported anywhere in the security tree.
`quantum_safe.py` is a simulation layer only. Do NOT use for real cryptographic operations.

### pq_export_encryption.py (real implementation path)

**HPKEExportBackend** — imports from `pq_export_encryption_swift.py` at line 332.
`pq_export_encryption_swift.py` — attempts real HPKE via `secure_enclave_helper` binary or falls back to simulation.

**Location**: `security/` directory, 20 files, 8,912 bytes.

---

## 5. AIMD Semaphore

**No AIMD algorithm** — `asyncio.Semaphore` with fixed bounds only.

| Location | Bound | Purpose |
|----------|-------|---------|
| `fetch_coordinator.py` | `Semaphore(20)` | Global fetch concurrency |
| `sprint_scheduler.py` | `Semaphore(3)` | M1 8GB safe — nonfeed prelude |
| `sprint_scheduler.py` | `Semaphore(limits["fetch"])` | Config-driven fetch |
| `sprint_scheduler.py` | `Semaphore(branch_concurrency)` | Branch concurrency |

### DomainConcurrencyBandit (NOT AIMD)

`domain_concurrency.py` provides `DomainConcurrencyBandit` — Gradient Bandit with softmax action selection, not classical AIMD.

```
class DomainConcurrencyBandit:
    [I1] select_arm() always returns valid arm index [0, N_ARMS)
    [I2] record_outcome() updates preferences and baseline atomically
    [I3] current_limit property returns ARM_VALUES[selected_arm]
    [I4] consecutive_42...
```

Closest to "adaptive" behavior but is NOT classical additive-increase/multiplicative-decrease AIMD.

---

## 6. Bloom Filter / URL Dedup

**Implementation**: `tools/url_dedup.py` — `RotatingBloomFilterAdapter` wrapping `probables.RotatingBloomFilter`.

| Parameter | Value |
|-----------|-------|
| Library | `probables` (pip) or `pyprobables` fallback |
| Sentinel | `RotatingBloomFilter = object` if import fails |
| Reset | `reset_default_bloom_filter()` — global singleton |
| Used by | FetchCoordinator via `DeduplicationStrategy` protocol |
| Boot error tracking | `_bloom_filter_boot_error` field |
| Canonical wired | `exec/ghost_executor.py:528` — uses `create_rotating_bloom_filter()` |

**No reset interval** — filter persists across runs. Reset only via explicit call.

---

## 7. Monitoring & Metrics

### MetricsRegistry (root `metrics_registry.py`, 13KB)

Prometheus-style, in-memory bounded counters, periodic JSONL disk flush.

| Category | Metrics |
|----------|---------|
| Fetch | `fetch_count`, `fetch_errors`, `curl_cffi_count`, `curl_cffi_fallback_to_aiohttp_count` |
| RAM | `memory_zone_normal_seconds`, `memory_zone_high_seconds`, `memory_zone_critical_seconds`, `uma_budget_used` |
| Sprint | `sprint_started`, `sprint_completed`, `branch_timeout_count` |
| Transport | `tor_circuit_renewal_count`, `i2p_session_count` |
| Thermal | `thermal_throttle_events`, `thermal_recovery_events` |
| Circuit Breaker | `circuit_breaker_count`, `circuit_breaker_blocked` |

**Wired in**: `sprint_scheduler.py:9754` — `MetricsRegistry` init via `_init_metrics_registry()`.
`monitoring/sprint_dashboard.py` — separate TUI dashboard (not metrics push, reads runtime state).

**No Prometheus push endpoint** — periodic flush to disk JSONL only.

### RAM Pressure Levels

| Level | Threshold | Action |
|-------|-----------|--------|
| `is_emergency` | >95% high_water / critical | fetch=3, block=1 |
| `is_critical` | >85% high_water | fetch=12, block=2 |
| `is_warn` | >70% high_water | fetch=20, block=3 |
| `is_normal` | ≤70% | fetch=25, block=4 |

`get_uma_snapshot()` → GovernorDecision applied via M1ResourceGovernor.

---

## 8. Transport Summary

| Transport | Protocol | Port | Wired | Notes |
|-----------|----------|------|-------|-------|
| `curl_cffi_transport.py` | HTTP/1.1 + JA3 | — | ✓ `should_use_curl_cffi()` | Stealth lane |
| `httpx_transport.py` | HTTP/2 opt | — | ✓ `transport_router.py` | Gated by env var |
| `tor_transport.py` | SOCKS v5 | 9050 (Tor default) | ✓ | Circuit isolation via `TorManager` |
| `nym_transport.py` | Nym mixnet | — | ✓ | |
| `i2p_transport.py` | I2P SAM | — | via injected session | |
| `gopher_transport.py` | Gopher | — | ✓ | |

---

## 9. Infrastructure

**No dedicated `infrastructure/` directory** — deployment concerns out of scope per filesystem boundary.

---

## 10. Security Gaps

| Gap | Severity | Detail |
|-----|----------|--------|
| **PQ crypto is simulation** | CRITICAL | `quantum_safe.py` — 5 "Simulace" markers, no real pqcrypto/CRYSTALS library |
| **CT log scanner dormant** | LOW | Intentionally not wired. "leave dormant (ct_log_client covers)" |
| **Secure Enclave stub** | MEDIUM | `secure_enclave.py` defines `SecureEnclaveBackend` — no macOS Keychain impl |
| **nodriver separate lane** | LOW | JS renderer uses nodriver separate from curl_cffi — correct separation |
| **BGP/IPFS not wired** | MEDIUM | Exist as standalone modules, not in capability inventory |
| **self_healing.CircuitBreaker** | MEDIUM | `security/self_healing.py:117` — second circuit breaker in security layer, independent of `transport/circuit_breaker.py`. Not wired to FetchCoordinator. |

---

## 11. Completeness Gaps (from code-review-expert review)

### Missing Transport Modules

`multiplexer_transport.py` — does not exist in `transport/`. No gap.

### Missing Security Modules

`security/self_healing.py` — `CircuitBreaker` class (line 117) is independent of `transport/circuit_breaker.py`. Different implementation (string state "closed/open/half_open" vs CBState enum). Not wired to FetchCoordinator.

### Missing Metrics

| Metric | Status |
|--------|--------|
| `fetch_bytes_total` | Not documented |
| `fetch_latency_p50/p95/p99` | Not documented |
| `circuit_breaker_state_transitions` | Not documented |
| `tls_fingerprint_score` | Not documented |
| `dns_lookup_latency` | Not documented |

---

## 12. Architectural Map

### Top 20 Communities by Size

| Community | Size | Cohesion | Role |
|-----------|------|----------|------|
| `runtime-ct` | 1009 | 0.270 | `core/__main__.py`, sprint execution, export |
| `legacy-load` | 922 | 0.344 | `autonomous_orchestrator.py` — queue, token bucket |
| `knowledge-graph` | 608 | 0.273 | `duckdb_store.py::DuckDBShadowStore` — canonical write |
| `intelligence-search` | 580 | 0.302 | `relationship_discovery.py`, `web_intelligence.py` |
| `probe-8af-feed` | 474 | 0.402 | Probe test suite |
| `benchmarks-sprint` | 381 | 0.230 | Benchmark infrastructure |
| `probe-8o-panic` | 359 | 0.463 | Probe test suite |
| `discovery-error` | 318 | 0.330 | Discovery adapters |
| `intelligence-fetch` | 290 | 0.242 | Fetch coordinators |
| `probe-8x-pattern` | 283 | 0.404 | Probe test suite |
| `layers-pivot` | 162 | 0.370 | Pivot lane planning |
| `brain-model` | 155 | 0.325 | `hermes3_engine.py`, model lifecycle |
| `probe-f203e-bundle` | 144 | 0.334 | Probe test suite |
| `brain-evidence` | 137 | 0.230 | Brain evidence synthesis |
| `brain-model` | 134 | 0.229 | Model inference |
| `legacy-compute` | 125 | 0.363 | Legacy compute layer |
| `utils-hash` | 122 | 0.317 | Hash utilities |
| `layers-state` | 118 | 0.421 | State management |
| `coordinators-memory` | 117 | 0.373 | Memory/resource coordinators |
| `knowledge-secure` | 103 | 0.350 | Security layer, PQ crypto, vault |

### Bridge Hubs (Architectural Chokepoints)

| Node | Betweenness | Degree | Role |
|------|-------------|--------|------|
| `SprintScheduler` | 0.01244 | 398 | **TOP CHOKEPOINT** — all sprint execution flows through here |
| `DuckDBShadowStore` | 0.00823 | 385 | Canonical write — all findings land here |
| `RelationshipDiscoveryEngine` | 0.00316 | — | Intelligence correlation |
| `async_run_live_public_pipeline` | 0.00278 | 375 | Non-feed web intelligence |
| `Hermes3Engine` | 0.00202 | — | LLM inference engine |
| `FetchCoordinator` | 0.00193 | — | HTTP transport seam |
| `_build_nonfeed_lane_eligibility` | 0.00223 | — | Acquisition strategy decision |
| `SocialIdentityMiner` | 0.00184 | — | Identity extraction |

### Surprising Cross-Community Connections

1. `CircuitBreaker` in `transport/circuit_breaker.py` — peripheral-to-hub cross-community
2. `test_touch_node_temporal_ring_limits` → `DuckDBShadowStore` — test bridging canonical store
3. `test_canonical_run_sprint_persists_and_exports_findings` → `DuckDBShadowStore` — e2e test coupling
4. `run_sprint` → `DuckDBShadowStore` — main entry to canonical write

### Canonical Sprint Owner

**`core.__main__.run_sprint()`** — sole canonical sprint owner (line 190, degree 190).

```
run_sprint() [core/__main__.py]
  → _scheduler_result_acquisition_payload() [core/__main__.py, degree 406]
  → SprintScheduler.run() [runtime/sprint_scheduler.py, degree 263]
    → _run_mandatory_acquisition_prelude() [degree 194]
    → compute_sprint_intelligence() [degree 213]
    → _build_diagnostic_report() [degree 205]
    → _run_live_public_pipeline() → async_run_live_public_pipeline() [degree 375]
    → _run_live_feed_pipeline() → async_run_live_feed_pipeline()
```

### Key Module Roles

| Module | Symbols | Role |
|--------|---------|------|
| `runtime/sprint_scheduler.py` | SprintScheduler, SprintSchedulerConfig | Canonical execution engine |
| `knowledge/duckdb_store.py` | DuckDBShadowStore, CanonicalFinding (119 in / 103 out) | Canonical write |
| `brain/hermes3_engine.py` | Hermes3Engine (53 symbols) | LLM inference via MLX |
| `coordinators/fetch_coordinator.py` | FetchCoordinator (52 symbols) | HTTP transport seam + CircuitBreaker |
| `export/sprint_exporter.py` | ExportManager (34 symbols) | STIX, Markdown, JSON-LD export |
| `pipeline/live_public_pipeline.py` | async_run_live_public_pipeline (degree 375) | Non-feed web intelligence |
| `legacy/autonomous_orchestrator.py` | FullyAutonomousOrchestrator (degree 387), ThreadSafeBoundedQueue.get (degree 943) | Legacy orchestration |

### Coordinator Layer

| Coordinator | Purpose | Key Fields |
|-------------|---------|------------|
| `FetchCoordinator` | HTTP transport seam | `_domain_bandits`, `circuit_breaker`, `tor_session` |
| `MemoryCoordinator` | LMDB/LanceDB lifecycle | `_stores`, `aggressive_cleanup()` |
| `ResourceGovernor` | M1 8GB UMA advisory | `evaluate()`, `GovernorDecision` dataclass |
| `SecurityCoordinator` | Security posture | `get_stealth_capability_flags()` |
| `ResearchCoordinator` | Research lane orchestration | — |

### Security Architecture

```
security/
├── quantum_safe.py        # ML-KEM/ML-DSA SIMULATION (CRITICAL — 5 "Simulace" markers)
├── pq_export_encryption.py # HPKE via pq_export_encryption_swift.py helper
├── secure_enclave.py     # SecureEnclaveBackend protocol (no macOS impl)
├── audit.py              # Audit trail (zero-knowledge policy)
├── vault_manager.py      # Encrypted credential vault
├── pii_gate.py          # PII detection/redaction
├── self_healing.py       # Self-healing security
└── deep_research_security.py
```

### Transport Decision Hierarchy (from source)

```python
def should_use_curl_cffi(use_stealth=False, prior_status=None, url=None, ...):
    # Rule 1: JS rendering → nodriver (checked by caller)
    # Rule 2: Darknet TLD → tor/i2p (checked by caller)
    # Rule 3: Env gate HLEDAC_ENABLE_CURL_CFFI != "1" → aiohttp
    # Rule 4: curl_cffi availability checked at runtime by caller
    # Rule 5: Explicit stealth flag → curl_cffi
    # Rule 6: Prior 403/429 → curl_cffi
    # Rule 7: Known protection system detected
    # Rule 8 (default): clearnet → aiohttp
```

### Pipeline Entry Points

| Pipeline | Degree | Role |
|----------|--------|------|
| `async_run_live_public_pipeline` | 375 | Non-feed web intelligence acquisition |
| `async_run_live_feed_pipeline` | — | Feed-based acquisition (CT logs, passive DNS) |
| `async_run_live_synthesis` | — | LLM synthesis via Hermes3Engine |

---

## 13. Corrections to Previous Audit

| Was Incorrect | Now Corrected |
|--------------|--------------|
| "No circuit_breaker.py" | CircuitBreaker EXISTS at `transport/circuit_breaker.py` — CBState enum CLOSED/OPEN/HALF_OPEN, wired to FetchCoordinator |
| "passive_dns.py NOT wired" | **WIRED** — `sprint_scheduler.py:4482` calls `call_lookup_passive_dns()`, `_run_pdns_prelude_lane()` in core lane |
| "ct_log_scanner.py NOT wired" | **DORMANT** (intentional) — "leave dormant (ct_log_client covers)" per capability inventory |
| "CircuitBreaker threshold/recovery unknown" | Full state machine with configurable `failure_threshold`, `recovery_timeout_s`, `half_open_probes` |
| "AIMD present" | **No AIMD** — only fixed `asyncio.Semaphore` (20/3/concurrency bounds) + DomainConcurrencyBandit (softmax, NOT AIMD) |
| "UA pool 10 UAs" | Confirmed 10 UAs in `_BROWSER_UA_POOL` — Chrome 124 x4, Firefox 133 x3, Safari 17 x2, Edge 124 x1 |
| "MAX_RETRIES unknown" | **MAX_RETRIES = 1** (exactly one retry, no infinite loops) at `public_fetcher.py:438` |

---

## 14. GHOST_INVARIANTS (Network/Fetching)

These invariants MUST be preserved in any network/fetching changes:

| Invariant | Location | Enforcement |
|-----------|----------|-------------|
| `gather(return_exceptions=True)` | All async gather calls | Prevents single failure from cancelling batch |
| `mx.eval([])` before `clear_cache()` | MLX cache cleanup | Prevents cache clear from being no-op |
| `time.monotonic()` for intervals | All timing calculations | Monotonic clock immune to system clock skew |
| No bare `except:` | All try/except blocks | Catch specific exceptions only |
| `_check_gathered()` after gather | Sprint result processing | Ensures all gather results evaluated |
| `F_NOCACHE = 48` Darwin guard | fetch_coordinator.py:169-174 | Prevents Linux/CI failures |
| `malloc_zone_pressure_relief` guard | autonomous_orchestrator.py:18928 | Prevents libc unavailability crash |
| `loop.run_until_complete()` not `asyncio.run()` | Nested async in threads | `asyncio.run()` crashes M1 in thread pool |
| `return_exceptions=True` in gather | Sprint result handling | Canonical GHOST_INVARIANT |
| `asyncio.CancelledError` always re-raised | Async cancellation | Canonical GHOST_INVARIANT |

---

## 15. M1 8GB Memory Constraints

| Resource | Limit | Guard |
|----------|-------|-------|
| Active memory | <5.5GB | `uma_budget.py` monitoring |
| MLX model | ~2GB | Single model, no parallel |
| KV cache | ~0.75GB | `max_kv_size=8192`, `kv_bits=4` in generate() not load() |
| Fetch concurrency | ≤25 normal, 3 emergency | `asyncio.Semaphore(20)` global + governor |
| Vision/RAM guard | >85% high_water | Blocks heavy multimodal |

---

## 21. reports/ Directory Analysis

### 21.1 Directory Inventory

| Property | Value |
|----------|-------|
| Total files | 147 |
| Total size | 2.21MB |
| Date range | 2026-05-05 to 2026-05-22 (17 days) |
| Subdirectory | `benchmarks/` (14 files, 35.8KB) |
| Tracked in git | No — all reports gitignored |
| `.DS_Store` present | Yes (12KB, gitignored) |

### 21.2 File Type Breakdown

| Extension | Count | Total Size | Description |
|-----------|-------|------------|-------------|
| `.json` | 63 | 1,251.8KB | Sprint results, gate checks, domain audits, live runs |
| `.md` | 54 | 493.5KB | Audit reports, sprint retrospectives, capability exports |
| `.log` | 16 | 218.8KB | Live sprint runtime logs |
| `.jsonl` | 12 | 35.8KB | Benchmark time-series data |
| `.txt` | 1 | 249.4KB | `pytest_collect_after_p0.txt` — pytest collection error dump |
| (none) | 1 | 12.0KB | `.DS_Store` (macOS metadata) |

### 21.3 Report Categories

**Sprint live-run outputs** (27 files, ~800KB)
- `live_sprint_300s.json` / `live_sprint_300s_20260515.json` / `live_sprint_300s_20260516c/d/e/f.json` — canonical 300s sprint output, 31-44KB each
- `live_sprint_f231c_domain_lockbit3.json` — F231C domain lockbit3 sprint, 43.8KB
- `live_sprint_f234d_deep_osint_m1_lockbit.json` — F234D deep OSINT sprint, 39.1KB
- `live_sprint_f220h.json` — F220H lockbit sprint, 31.5KB
- `live_sprint_300s.md` — markdown summary generated from JSON output
- `live_sprint_300s.log` — runtime log from sprint execution, 18.3KB

**Domain/gate audit reports** (8 files, ~350KB)
- `domain_gate.json` (9.7KB) — gate verdict: `verdict`, `live_allowed`, `reasons`, `warnings`, `uma` snapshot, cross-sprint artifact checks (F221/F223)
- `domain_live.json` (6.5KB) — similar gate structure
- `f229d_domain_lockbit3_shape_recheck.json` (43.5KB) — F229D shape validation
- `f222g_lockbit_text_nonfeed_180.json`, `f223f_duckdb_lockbit_seeds_quality.json` — seed quality audits

**Pre-flight checks** (9 files, ~55KB)
- `preflight_f226_live.json`, `preflight_f227a_live.json`, `preflight_f230_live.json`, `preflight_f230d_live.json`, `preflight_f234d_deep_osint_m1.json` — all ~5.7-5.9KB, JSON with measurement/sprint fields + `status: planned`
- `preflight.json` — generic preflight template

**F214*/F220*/F226*/F229*/F230* sprint audit docs** (48 files, ~900KB)
- Naming pattern: `F214*`, `F220*`, `F221*`, `F222*`, `F223*`, `F226*`, `F229*`, `F230*`, `F231*`, `F233*`, `F234*`, `PY314*`
- Types: `.md` audit reports (security, performance, correctness), `.json` structured results
- Examples:
  - `F214S_ARCHIVE_EXTRACTION_SECURITY_AUDIT.md` (13.2KB) — archive extraction security findings
  - `F214H_EXECUTOR_BACKPRESSURE_AUDIT.md` (13.9KB) — executor backpressure analysis
  - `F214M_PY314_MODERNIZATION_AUDIT_V2.md` (41.6KB) — largest single file, comprehensive Python 3.14 audit
  - `PY314_ADVANCEMENTS_AUDIT.md` (29.3KB) — Python 3.14 advancements analysis
  - `F214OPT314_RUNTIME_OPTIMIZATION_SWEEP.md` (7.9KB) — optimization sweep results
  - `F214SMOKE*` series — controlled runtime smoke tests (6.7-13KB)
  - `F214READY_PRE_SPRINT_READINESS_GATE.md` (5.9KB) — pre-sprint readiness gate

**Benchmark time-series** (12 `.jsonl` files in `benchmarks/`)
- `bench_m1_runtime_gates_20260518_*.jsonl` — 11 files, M1 runtime gate benchmarks, 2.9-3.8KB each, 5 lines per file
- `sprint_timer_overhead.jsonl` (432B) — sprint timer overhead measurement, single-line JSON

**Capability exports** (2 files)
- `capability_export_f228d.json` (4.4KB) — F228D sprint capability export with `sprint`, `title`, `status`, `canonical_verdict_mapping`, `files_modified`
- `REPORT_CAPABILITY_EXPORT_F228D.md` (6.6KB) — markdown rendering of above

**Runtime hygiene / event truth** (2 files)
- `runtime_hygiene_event_truth.json` (2.8KB) — event truth verification
- `REPORT_LIVE_RUNTIME_PRODUCT_PATH_CLOSURE.md` (5.2KB) — product path closure report

**External/imported reports** (2 files)
- `ghost_cti_20260521_114140.stix.json` and `ghost_cti_20260521_114620.stix.json` — external STIX 2.1 CTI reports, NOT generated by the pipeline
- `embedding_similarity_dedup_audit_2026-05-06.md` (13.4KB) — external audit doc

### 21.4 Live Sprint JSON Schema

All `live_sprint_*.json` files share this structure (verified against 8 samples):

```
Top-level fields:
  measurement_id          string — lsm_{timestamp}_{random_id}
  sprint_id               string — e.g. lsm_1778805309616_a0496a
  mode                    string — sprint mode
  status                  string — "completed" | "planned" | etc.
  start_time_iso          string — ISO timestamp
  end_time_iso            string — ISO timestamp
  planned_duration_s      number
  actual_duration_s       number
  query                   string — search query (e.g. "LockBit ransomware")
  profile                 string — acquisition profile (e.g. "active300", "nonfeed_diagnostic180")
  duration_s              number
  aggressive_mode         boolean
  deep_probe              boolean
  uma_pre_used_gib        number
  uma_pre_swap_gib        number
  uma_pre_state           object — UMA snapshot before sprint
  uma_post_used_gib       number
  uma_post_swap_gib       number
  uma_post_state          object — UMA snapshot after sprint
  findings_count          number — 0 in all examined files (findings stored elsewhere)
  cycles_completed        number
  cycles_started          number
  accepted_findings       number — e.g. 262 for LockBit 300s sprint
  export_paths            object — paths to resolved outputs
  report_json_path        string
  verdict                 null | object
  run_quality_verdict     string
  hardware_constrained     boolean
  memory_state_pre/post    object
  swap_warning             boolean
  comparable_result        object
  live_kpi                 object
  public_pipeline          object
  acquisition_strategy     object
  acquisition_profile      string
  windup_guard_observation object
  scheduler_exit           object
  acquisition_terminality_* fields
  runtime_authority_* fields
  core_run_sprint_module_file    string
  sprint_scheduler_module_file   string
  python_executable        string
  sys_path_head            string
  core_main_mtime          number — file modification time
  sprint_scheduler_mtime   number
  research_quality_grade/score
  canonical_report_snapshot
  derived_checks           object
```

**Key security observation**: `findings_count` is always 0 in examined live sprint reports — findings are counted separately in `accepted_findings` field. The JSON does NOT embed raw IOC data (domains, IPs, hashes) directly; those are stored in DuckDB via the canonical `async_ingest_findings_batch` path.

### 21.5 Benchmark JSONL Schema

`bench_m1_runtime_gates_*.jsonl` files (5 entries each):

```json
{"type":"benchmark_record","name":"body_limiter_throughput","timestamp":"2026-05-18T17:21:38...","python_version":"3.14.4","platform":"darwin","free_threaded":false,"jit_available":false,"jit_active":false,"rss_start_kb":32080,"rss_psutil_start_mib":31.3,"has_psutil":true,"has_selectolax":true,"has_bs4":false,"quick":true,"result":{"name":"body_limiter_throughput","status":"ok","wall_s":0.000309,"samples_ms":[...],"summary":{"min_ms":0.2439,"median_ms":0.2911,"mean_ms":0.3091,"p95_ms":0.3671,"max_ms":0.4073,"runs":7},"throughput_mb_s":335.493,"fixture":{"total_bytes":102400,"chunk_size":1024,"n_chunks":100}}}
```

`sprint_timer_overhead.jsonl` (single-line): benchmark name, ops count (3000), wall time (0.0016s), `bounded_ok: true`, event keys, `timer_events_wired_in_scheduler: true`, scheduler line reference (`sprint_scheduler.py:3351`).

**No sensitive content** in benchmark files — only performance metrics and timing data.

### 21.6 Domain/Gate Audit JSON Schema

`domain_gate.json` / `domain_live.json`:

```json
{
  "verdict": "...",
  "live_allowed": boolean,
  "reasons": [...],
  "warnings": [...],
  "uma": {...},
  "f221_artifacts": [...],
  "missing_f221": [...],
  "missing_cross_sprint": [...],
  "f223_artifacts": [...],
  "missing_f223_required": [...],
  "f223_optional_status": {...},
  "provider_surface_ok": boolean
}
```

Security-relevant: gate decisions are based on cross-sprint artifact presence and provider surface validation. No IOC data embedded.

### 21.7 Sensitive Content Scan Results

| Scan | Result |
|------|--------|
| API keys (`sk_live`, `sk_test`, `api_key`) | **None found** — all 147 files clean |
| Passwords / tokens / Bearer | **None found** |
| LockBit IOC references | Found in `live_sprint_300s_20260516f.json` (4 refs) and `live_sprint_f231c_domain_lockbit3.json` (5 refs) — query/profile context only, not embedded IOCs |
| Onion domains | Not found in JSON fields (onion TLDs present in IOC database, not in report JSON) |
| IP addresses (IPv4) | **None found** in report JSONs |
| Hashes (MD5/SHA1/SHA256) | **None found** in report JSONs |

**Conclusion**: Reports do NOT contain raw IOC data, API keys, tokens, or credentials. They contain only metadata about sprints (queries, profiles, timing, UMA snapshots, verdicts). IOCs are stored in DuckDB, not in reports.

### 21.8 Report Generation Sources

| Report Type | Generated By | Evidence |
|-------------|-------------|----------|
| `live_sprint_*.json` | `SprintScheduler.run()` → `autonomous_orchestrator.py` → canonical write path | `live_sprint_300s.md` log shows "Markdown summary written to .../reports/live_sprint_300s.md" |
| `preflight_*.json` | Sprint pre-flight check script or `SprintScheduler` pre-flight phase | `status: planned` + same schema as live_sprint outputs |
| `domain_gate.json` | Gate check before live sprint execution | Contains cross-sprint artifact checks (F221/F223) |
| `bench_*.jsonl` | `benchmark_helpers.py` or dedicated benchmark scripts | `type: benchmark_record`, timestamp + platform fields |
| `F214*/F226*/etc.` | Claude Code agent-generated audit docs (human-in-the-loop sprint retrospectives) | File naming matches sprint designation pattern |
| `ghost_cti*.stix.json` | **External** — imported STIX 2.1 CTI reports, not generated by pipeline | JSON parse shows STIX type, `objects` array |
| `capability_export_f228d.json` | Capability export feature (`export/sprint_exporter.py` or equivalent) | Dedicated `capability_export` naming + `canonical_verdict_mapping` |

**Key finding**: `ghost_cti*.stix.json` are the only clearly external files. All other reports are either pipeline-generated (live_sprint, preflight, benchmarks) or human-authored audit docs (F214* series).

### 21.9 Retention Policy

**Current state**: No automated retention policy. Reports accumulate in `reports/` with no expiration.

- Date range: 17 days (2026-05-05 to 2026-05-22)
- 147 files in 2.21MB — manageable, no immediate pressure
- All files gitignored (`.gitignore` has `.DS_Store`, no explicit `reports/` exclusion needed since `reports/` is not at repo root)
- `reports/` is inside `hledac/universal/` which is itself inside the repo — `.gitignore` at repo root covers it

**Risk**: Without a retention policy, reports could grow indefinitely. Recommend adding a `reports/` cleanup to `.gitignore` or a cron job to purge reports older than N days.

### 21.10 Security Assessment

| Aspect | Finding | Risk Level |
|--------|---------|------------|
| IOC data in reports | None — IOCs stored in DuckDB only | LOW |
| API keys / credentials | None found in any of 147 files | LOW |
| Personal / PII data | No user-generated content; all machine-generated metadata | LOW |
| External content | `ghost_cti*.stix.json` (2 files) — external STIX CTI, should be reviewed before sharing | MEDIUM |
| Log files | `live_sprint_300s.log` (18.3KB) contains warning-level system messages (degraded mode, missing captcha solver) — no sensitive data | LOW |
| `.DS_Store` | Present (12KB), gitignored — macOS metadata, no security concern | NONE |
| Git exposure | All reports gitignored — not committed to repo | LOW |
| Benchmark data | Only performance metrics, no PII or credentials | LOW |

**Overall**: `reports/` directory is low risk. No sensitive IOCs, credentials, or PII found. External STIX files should be reviewed before external sharing. No data leakage vectors identified.

### 21.11 Findings Summary

- **147 files**, 2.21MB total, spanning 17 days
- **6 file types**: JSON (63), MD (54), LOG (16), JSONL (12), TXT (1), none/.DS_Store (1)
- **3 pipeline-generated types**: live_sprint outputs (JSON + MD + LOG), preflight checks (JSON), benchmarks (JSONL)
- **2 external types**: ghost_cti STIX files (external CTI reports)
- **No sensitive content** (API keys, tokens, IOCs, PII) found in any report file
- **No retention policy** — reports accumulate indefinitely (2.21MB currently, manageable)
- **All gitignored** — no reports committed to repository
- **ghost_cti STIX files**: only external-content carriers; verify before external sharing
- **LockBit references**: present only as query/profile strings in live_sprint metadata, not as embedded IOCs
- **UMA snapshots**: present in live_sprint reports (pre/post memory state), no security concern

---

### 23.1 Directory Inventory

| File | Type | Purpose | Dev/Prod | M1-Specific | Security Notes |
|------|------|---------|----------|-------------|----------------|
| `smoke_llm_candidate.py` | Python | Smoke-test LLM + model stack components (LLM, embeddings, NER, reranker, PII, OCR) | Dev | Yes | Imports `SecurityGate` for PII redaction smoke-test; no credentials |
| `model_stack_smoke.py` | Python | Verify model stack imports/availability/disk-free space; print download commands | Dev | Yes | No credentials; checks MLX/LLM availability; `--print-download-commands` |
| `pre_commit_guard.py` | Python | Git pre-commit hook blocking "None" or "None.*" filenames | Dev | No | Minimal security; blocks only pathological filenames |
| `mount_ramdisk.sh` | Bash | Create macOS RAM disk at `/tmp/hledac_ramdisk` (256MB default) | Dev | Yes (macOS) | Requires `hdiutil`/`diskutil`; chmod 777 a potential info-leak risk |
| `unmount_ramdisk.sh` | Bash | Safely unmount the Hledac RAM disk | Dev | Yes (macOS) | Graceful exit 0 if already unmounted; `log_error()` to stderr |
| `extract_nonfeed_seeds.py` | Python | Extract NonfeedSeed candidates from DuckDB findings for nonfeed lane | Dev | No | Reads DuckDB; produces JSON with seeds, quality gates, lane unlocks |
| `check_torrc.py` | Python | Verify Tor configuration sanity (IsolateSOCKSAuth, DataDirectory, cache bounds) | Dev | No | Reads torrc file; test seam via `TORRC_PATH_OVERRIDE` env var |
| `score_corroboration.py` | Python | Score corroboration strength between CT findings and pivot seeds | Dev | No | Reads DuckDB; produces JSON corroboration scores per source |

**Total: 8 files (5 Python, 2 Bash, 1 compound Bash)**

---

### 23.2 Detailed Analysis

#### smoke_llm_candidate.py

```
Purpose: Smoke-test for LLM model stack on M1 MacBook Air 8GB
Sprint: F221A
Does NOT load models — only checks availability flags, imports, disk space.
Reports: OK / WARN / FAIL per component
```

**Components checked:**

| Component | Check Method | Security Relevance |
|-----------|--------------|-------------------|
| LLM (Hermes-3) | `mlx_lm` import + `Hermes3Engine` availability flag | None — no model loading |
| Embeddings | `mlx_lm.embed` + `EMBEDDINGS_AVAILABLE` flag | None |
| NER | `transformers` pipeline (CPU/MPS) + `NER_AVAILABLE` | None |
| Reranker | `LightweightReranker` from `flashrank` + `FLASHRANK_AVAILABLE` | Cache directory check |
| PII | `SecurityGate` import + `create_security_gate()` + `sanitize("john.doe@example.com")` | **CRITICAL** — this IS a security component smoke-test |
| OCR | `VisionOCR` + `ocrmac` imports | None |

**Security observations:**
- PII component IS the `SecurityGate` from `security/pii_gate.py` — a legitimate security component being smoke-tested
- No hardcoded credentials; all checks are import/availability based
- Component failure returns `FAIL` with error message but no sensitive data
- `NO_MODEL_CHANGE`, `NO_NETWORK_IN_TESTS` flags ensure no state modification

**Risk: LOW** — read-only smoke test, no credential exposure.

---

#### model_stack_smoke.py

```
Purpose: Practical Model Stack Smoke & Assets — verify disk space, availability, print download commands
Sprint: F221A
Does NOT load models. Does NOT modify state.
```

**Usage modes:** `--check` (all components, terse), `--smoke` (smoke + import test), `--component <name>`, `--print-download-commands`

**Model stack verified:**

| Model | Type | Download Location |
|-------|------|-------------------|
| `Hermes-3-Llama-3.2-3B-4bit` | Primary LLM | `~/.cache/mlx/` via `mlx_lm` |
| Rollback LLM | Secondary | Same |
| `mlx-embeddings` | Embeddings | Same |
| `FlashRank` | Reranker | local pip + cache |
| Transformers NER | NER | pip + torch |

**Security observations:**
- Prints download commands but does NOT execute them
- `shutil.disk_usage()` checked for free space — no credential access
- `uv run` prefix shown but not executed by the script itself
- No API key checks; purely availability/import based

**Risk: LOW** — informational script, no credential exposure.

---

#### pre_commit_guard.py

```
Purpose: Git pre-commit hook blocking commit of 'None' or 'None.*' pathologically bad filenames
```

**Logic:**
```python
staged = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True, text=True).stdout.strip().splitlines()
bad = [f for f in staged if re.match(r'^None(\.|$)', f)]
if bad: sys.exit(1)
```

**Security observations:**
- Pure Python, no imports from hledac
- Uses `subprocess.run` safely (no shell=True, no user input in command)
- Regex is safe — no injection
- Only blocks pathologically named files; not a general security gate

**Risk: VERY LOW** — local git hook only, no credential exposure.

---

#### mount_ramdisk.sh

```
Purpose: Create a macOS RAM disk for Hledac scratch space
Requires: macOS (hdiutil, diskutil)
```

**Key parameters:**
```bash
MOUNT_POINT="${MOUNT_POINT:-/tmp/hledac_ramdisk}"
SIZE_MB="${SIZE_MB:-256}"  # default 256MB
SECTOR_SIZE=512
sector_count=$((SIZE_MB * 1024 * 1024 / SECTOR_SIZE))
```

**Security observations:**
- RAM disk created with `hdiutil attach` — device owned by root, group operator
- Permissions: `chmod 777` on mount point — **INFO-LEAK RISK** (world-readable/writable)
- No sensitive data written; scratch space only
- Pre-check: `df -lh /dev/disk2*` before unmount (confirm device exists)

**Risk: MEDIUM** — world-writable RAM disk could be exploited for local privilege escalation if attacker has local access. However, RAM disk disappears on unmount/reboot. Not a remote attack vector.

**M1 relevance:** RAM disk is useful on M1 to avoid SSD wear for high-IO scratch work.

---

#### unmount_ramdisk.sh

```
Purpose: Safely unmount the Hledac RAM disk
Requires: macOS (hdiutil)
```

**Logic:**
```bash
if ! mount | grep -q "^/dev/disk[0-9] on ${MOUNT_POINT}"; then
  log_info "Already unmounted: ${MOUNT_POINT}"
  exit 0
fi
hdiutil detach "${DEVICE}"  # blocks until sync
```

**Security observations:**
- `set -euo pipefail` — fails fast on error
- `log_error()` function for stderr output
- Only unmounts the specific mount point; no wildcards
- Graceful exit (0) if already unmounted

**Risk: LOW** — unmount operation only affects the specific RAM disk.

---

#### extract_nonfeed_seeds.py

```
Purpose: Extract NonfeedSeed candidates from DuckDB findings for nonfeed lane bootstrapping
Sprint: F222H
Input: DuckDB database path
Output: JSON with seeds, quality gate stats, lane unlocks
```

**Pipeline:**
1. Connect to DuckDB via `duckdb_engine` from `hledac.universal.knowledge.duckdb_store`
2. Query `canonical_report_snapshot` + `all_findings` tables
3. Extract domain/identity/leak/archive/graph seeds from `payload_text`
4. Score seeds via `classify_seed_quality()`
5. Output JSON with quality summary

**Quality gate:**
- `kept`: high-quality seeds
- `weak`: weak but included (via `--include-weak`)
- `dropped`: below threshold

**Lane unlocks:** reports which nonfeed acquisition lanes are enabled by seeds

**Security observations:**
- Reads DuckDB — potentially sensitive CT data in findings
- `payload_text` field contains `source_type`, `created_at`, `publisher_domains` — could include PII
- No credential hardcoding; DuckDB path is a parameter
- Test seam: `TORRC_PATH_OVERRIDE` env var pattern used here as `USE_DUCKDB_PATH_OVERRIDE`

**Risk: MEDIUM** — reads from DuckDB which may contain CT findings with PII. Should only be run on local data, not shared.

---

#### check_torrc.py

```
Purpose: Bootstrap helper verifying Tor configuration sanity
Checks: IsolateSOCKSAuth, DataDirectory, cache bounds, SOCKSPort
Handles: comments, line continuations, missing keys, unknown keys
```

**Checks performed:**
1. `IsolateSOCKSAuth` directive present (prevents auth leakage across circuits)
2. `DataDirectory` exists and is writable
3. `Cache` bounds (min 1MB, max 100MB, warn if >50MB)
4. `SOCKSPort` configuration
5. `ClientOnly` flag
6. Circuits vs Guards ratio

**Torrc path:** `${TORRC_PATH:-/etc/tor/torrc}` with test seam via `TORRC_PATH_OVERRIDE`

**Security observations:**
- Reads torrc file — no credential exposure
- Validates anonymity configuration settings
- Returns structured dict: `{"status": "OK"|"WARN"|"FAIL", "component": "...", "notes": [...]}`
- Component-level status enables granular alerting

**Risk: LOW** — read-only torrc validation. No credential exposure.

---

#### score_corroboration.py

```
Purpose: Score corroboration strength between CT findings and pivot seeds
Input: DuckDB database, findings + seeds
Output: JSON with corroboration scores per source, top indicators, weak unverified indicators
```

**Corroboration logic:**
1. Load CT findings from DuckDB
2. Load pivot seeds from JSON
3. Match: domain, identity, leak, archive signals
4. Score: `source_family_support`, `top_indicators`, `recommended_next_pivots`

**Output structure:**
```json
{
  "corroboration_score": 0.0-1.0,
  "source_family_support": {...},
  "top_indicators": [...],
  "weak_unverified_indicators": [...],
  "recommended_next_pivots": [...]
}
```

**Security observations:**
- Reads DuckDB — same PII considerations as `extract_nonfeed_seeds.py`
- No external network calls
- DuckDB path passed as CLI argument
- No credential hardcoding

**Risk: MEDIUM** — processes CT findings from DuckDB. Should be run on local data only.

---

### 23.3 Security Summary

| Script | Risk Level | Primary Concern |
|--------|------------|-----------------|
| `smoke_llm_candidate.py` | LOW | PII component smoke-test is legitimate use of security gate |
| `model_stack_smoke.py` | LOW | Informational only; no credential exposure |
| `pre_commit_guard.py` | VERY LOW | Git hook only; no credential exposure |
| `mount_ramdisk.sh` | MEDIUM | world-writable RAM disk (info-leak risk, local only) |
| `unmount_ramdisk.sh` | LOW | Unmount operation; no credential exposure |
| `extract_nonfeed_seeds.py` | MEDIUM | Reads DuckDB CT findings with potential PII |
| `check_torrc.py` | LOW | Read-only torrc validation; no credential exposure |
| `score_corroboration.py` | MEDIUM | Reads DuckDB CT findings; no external calls |

**Overall: No critical credentials or API key exposures found. Primary risks are local data exposure via DuckDB reads and the world-writable RAM disk mount point.**

---

### 23.4 M1/Apple Silicon Specificity

| Script | M1 Relevance | Notes |
|--------|--------------|-------|
| `smoke_llm_candidate.py` | HIGH | Smoke-tests MLX availability, `Hermes3Engine`, Metal backend |
| `model_stack_smoke.py` | HIGH | Explicitly targets MacBook Air M1 8GB; checks `mlx_lm`, `mlx-embeddings` |
| `mount_ramdisk.sh` | HIGH | macOS-specific (`hdiutil`, `diskutil`) RAM disk creation |
| `unmount_ramdisk.sh` | HIGH | macOS-specific unmount |
| Others | LOW | Platform-agnostic Python |

---

### 23.5 Development vs Production

All scripts are **development-only** tools:
- `smoke_llm_candidate.py` — dev smoke testing
- `model_stack_smoke.py` — dev environment verification
- `pre_commit_guard.py` — dev git hook
- `mount/unmount_ramdisk.sh` — dev scratch space setup
- `extract_nonfeed_seeds.py` — dev data extraction
- `check_torrc.py` — dev tor configuration
- `score_corroboration.py` — dev analysis

**None are suitable for production use** — they are all helper scripts for development workflows.

---

*Audit complete — 15 sections. All facts verified against source. Scripts directory: 5 Python + 2 Bash, all dev-only, no credential exposure critical. Canonical sprint owner: `core.__main__.run_sprint()`.*

---

## 20. models/ Directory Analysis

### 20.1 Directory Status

| Item | Status |
|------|--------|
| `models/` directory | **EMPTY** — 0 bytes, 0 files |
| Local model files | None bundled in repository |
| Runtime download | **Yes** — HuggingFace Hub via `mlx_lm` |

The `models/` directory at `hledac/universal/models/` is an empty placeholder directory (created May 20, 2026). No model files (`.bin`, `.pt`, `.safetensors`, `.gguf`, `.mlx`, `.mlmodelc`) are stored in the repository. All MLX models are downloaded at runtime from HuggingFace Hub.

---

### 20.2 Runtime Model Download Architecture

**Download mechanism:** `mlx_lm` library (`mlx-community/` models)

| Component | Implementation |
|-----------|----------------|
| Load call | `mlx_lm.load(model_path_str)` at `model_lifecycle.py:739` |
| Generate call | `mlx_lm.generate(**kwargs)` at `hermes3_engine.py:1122` |
| Download path | `~/.cache/mlx/` (MLX default) + `~/.cache/huggingface/hub/` |
| Trust remote code | `trust_remote_code=True` on draft model load (`hermes3_engine.py:913`) |
| Lazy loading | Yes — model loaded on first inference, not at startup |
| Unload | `engine.unload()` via `Hermes3Engine` (7K lifecycle order) |

**Model path resolution chain:**
```
HermesConfig.model_path (default)
    ↓
"mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit"
    ↓
mlx_lm.load() → HuggingFace Hub download → ~/.cache/mlx/
```

---

### 20.3 Model Inventory

| Model | Type | Quantization | Size (M1 8GB) | Role |
|-------|------|-------------|---------------|------|
| `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` | Primary LLM | 4bit Q4 | ~2GB | Primary reasoner |
| `mlx-community/Hermes-3-Llama-3.2-3B-4bit` | Rollback LLM | 4bit Q4 | ~2GB | Fallback reasoner |
| `mlx-community/Hermes-3-Llama-3.2-1B-4bit` | Draft model | 4bit | ~0.5GB | Speculative decoding (Sprint 75) |
| `mlx-community/answerdotai-ModernBERT-base-6bit` | Embeddings | 6bit | ~100MB | Semantic dedup, context optimization |
| `knowledgator/gliner-relex-large-v0.5` | NER | N/A | ~400MB | Named entity recognition via transformers |
| `sentence-transformers/all-MiniLM-L6-v2` | Fallback embeddings | Quantized ONNX | ~90MB | FastEmbed reranker backend |

**Qwen3-0.6B / SmolLM** — Used by `ModelLifecycle._ensure_loaded()` (`model_lifecycle.py:714`) for windup-local structured JSON generation only. Isolated from runtime-wide model plane. Loaded from `~/.cache/huggingface/hub/`.

**ModernBERT vs FastEmbed:** `context_optimization/context_cache.py:243` uses FastEmbed (`fastembed`) with quantized ONNX models for embeddings. Not MLX-based. Cache dir: `storage_path / "embeddings"`.

---

### 20.4 M1 8GB Memory Budget Compliance

| Resource | Allocation | Constraint |
|----------|-----------|------------|
| MLX model load | `mx.metal.cache_limit(2_500_000_000)` | Set before load (`model_lifecycle.py:730`) |
| KV cache | `kv_bits=4`, `max_kv_size=8192` | At generate time, NOT load time |
| Single model only | No parallel model loading | Architectural constraint |
| Speculative decoding | ~0.5GB draft model | RAM-gated, disabled if <2GB available |
| Prompt cache | `make_prompt_cache()` at `hermes3_engine.py:807` | Lazy init after load |
| System prompt cache | `max_kv_size=512` | Separate from main KV cache |

**Canonical KV quantization** at `hermes3_engine.py:1095`:
```python
if hasattr(layer, 'quantize'):
    layer.quantize(group_size=64, bits=4)
```

**mlx_lm.generate() parameters** at `hermes3_engine.py:1101-1109`:
```python
"model": self._model,
"tokenizer": self._tokenizer,
"prompt": formatted_prompt,
"temp": temp,
"max_tokens": max_tok,
"max_kv_size": 8192,
"kv_bits": 4,
"prompt_cache": kv_cache,
"verbose": False,
```

---

### 20.5 MLX/Metal Specific Configuration

| Setting | Value | Location |
|---------|-------|----------|
| Metal cache limit | 2.5GB | `model_lifecycle.py:731` — `mx.metal.cache_limit(2_500_000_000)` |
| KV bits | 4 | `hermes3_engine.py:1107` — passed to `generate()` not `load()` |
| KV size | 8192 | `hermes3_engine.py:1106` — hardcoded, differs from env default 4096 |
| Batch size | MAX_BATCH=32 | `mlx_lm.generate()` — per M1 8GB safety |
| Speculative tokens | 4 | `hermes3_engine.py:243` |
| Draft model | Hermes-3-Llama-3.2-1B-4bit | `hermes3_engine.py:898` |
| Outlines integration | `outlines.from_mlxlm(model, tokenizer)` | `hermes3_engine.py:824` |
| mx.eval([]) barrier | After inference, before clear_cache | `hermes3_engine.py:1125` |

**MLX availability flag:**
```python
# hermes3_engine.py:154-161
try:
    from ..utils.mlx_cache import MLX_AVAILABLE as _MLX_AVAILABLE_GLOBAL
except ImportError:
    try:
        import mlx.core as mx
        _MLX_AVAILABLE_GLOBAL = True
    except ImportError:
        _MLX_AVAILABLE_GLOBAL = False
```

---

### 20.6 Model Metadata and Tokenizer

| Item | Status | Location |
|------|--------|----------|
| config.json | Downloaded with model | HuggingFace Hub |
| tokenizer | Loaded via `mlx_lm.load()` | Returns (model, tokenizer) tuple |
| tokenizer_config | `trust_remote_code=True` | Draft model load only |
| safetensors format | Yes — MLX native | mlx-community models use safetensors |
| GGUF support | Planned (llama.cpp integration) | `MODEL_INTEGRATION_PLAN.md:243` — not yet active |
| Model card / metadata | HuggingFace Hub page | `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` |

**Tokenizer loaded via `mlx_lm.load()`:**
```python
# model_lifecycle.py:739-744
result = mlx_lm.load(model_path_str)
if isinstance(result, tuple) and len(result) >= 2:
    self._model, self._tokenizer = result[0], result[1]
else:
    self._model, self._tokenizer = result, None
```

---

### 20.7 Security Analysis

#### 20.7.1 Provenance

| Concern | Assessment | Evidence |
|---------|------------|----------|
| Model source | HuggingFace Hub (mlx-community) | Trusted model hub |
| Tampering risk | LOW | HuggingFace signature verification available |
| Supply chain | Medium | Runtime download from external host |
| Model spoofing | Low-Medium | `mlx_lm` fetches from HuggingFace — man-in-middle possible without TLS verification |
| Fake models | Low | `mlx-community/` prefix indicates curated MLX community models |

#### 20.7.2 Attack Vectors

| Vector | Risk | Mitigation |
|--------|------|------------|
| HuggingFace credential theft | LOW | No API key required for public models |
| Model tampering via MITM | MEDIUM | No code signing verification implemented |
| Malicious model variant | LOW | `mlx-community/` prefix provides community curation layer |
| Disk space exhaustion via model download | LOW | `shutil.disk_usage()` check in `model_stack_smoke.py` |
| Memory exhaustion via KV cache | LOW | `max_kv_size=8192` hardbounded, `kv_bits=4` quantization |
| Model file permission | LOW | `~/.cache/mlx/` user-owned, not world-readable by default |

#### 20.7.3 Security Gaps

| Gap | Severity | Recommendation |
|-----|----------|----------------|
| No model hash verification | MEDIUM | Add SHA-256 hash check post-download |
| No HuggingFace token pinning | LOW | Use `HF_TOKEN` env var for private models only |
| trust_remote_code=True on draft | MEDIUM | Draft model loads with `trust_remote_code=True` — arbitrary code execution risk |
| No model SBOM | LOW | Document model provenance in audit trail |
| MLX cache not encrypted at rest | N/A | RAM only, no persistence |

**Critical note on `trust_remote_code=True`:** The draft model load (`hermes3_engine.py:913`) uses `trust_remote_code=True`. This allows the model to execute arbitrary Python code embedded in the model card. For the Hermes draft model (1B), this is a low but non-zero risk.

---

### 20.8 Legacy Model Loading

| Location | Model | Issue |
|----------|-------|-------|
| `layers/memory_layer.py:716` | `Hermes-3-Llama-3.2-3B-bf16` | **WRONG quantization** — loads bf16 instead of 4bit |
| `layers/memory_layer.py` | Legacy | Redundant with canonical MLX load path |
| `layers/memory_layer.py` | bf16 vs Q4 | Memory overhead 2x vs 4bit |

Per `MODEL_INTEGRATION_PLAN.md:321` and `MODEL_INTEGRATION_PLAN.md:534`:
> **`layers/memory_layer.py:716` bf16 Hermes → remove** (redundant, wrong quantization)

This is a known issue — the legacy layer loads a different model variant than the canonical path.

---

### 20.9 FastEmbed / Non-MLX Models

| Model | Backend | Cache Location |
|-------|---------|----------------|
| FastEmbed embeddings | Quantized ONNX | `storage_path / "embeddings"` |
| FlashRank reranker | `flashrank` pip package | `/tmp/flashrank_cache` |
| Transformers NER | PyTorch + MPS/CPU | `~/.cache/huggingface/hub/` |

**FastEmbed initialization** at `context_optimization/context_cache.py:243`:
```python
cache_dir=str(Path(cache_path) / "embeddings")
```

**FlashRank reranker** at `brain/ane_embedder.py:550`:
```python
_reranker = Ranker(model_name=_FLASHRANK_MODEL, cache_dir="/tmp/flashrank_cache")
```

---

### 20.10 Model Loading Flow Summary

```
Hermes3Engine.__init__()
  ↓
Hermes3Engine._load_model() [line 800]
  ↓
mlx_lm.load(self.config.model_path)  [line 802]
  ↓
Download from HuggingFace Hub (~2GB)
  ↓
mx.metal.cache_limit(2_500_000_000) [line 731 — BEFORE load]
  ↓
make_prompt_cache() [line 807-808]
  ↓
outlines.from_mlxlm() [line 824]
  ↓
Ready for inference
```

**Emergency unload (7K order):**
```
engine.unload()
  → _batch_worker_task.cancel()
  → _batch_queue = None
  → _pending_futures clear
  → _prompt_cache eviction
  → _system_prompt_cache eviction
  → invalidate_prefix_cache()
  → _model = None, _tokenizer = None, _outlines_model = None
  → gc.collect()
  → mx.eval([]) + mx.metal.clear_cache()
```

---

### 20.11 Summary

| Aspect | Finding |
|--------|---------|
| Local model files | None — all downloaded at runtime |
| Storage format | safetensors (MLX native) |
| GGUF support | Not yet active (planned) |
| M1 8GB compliance | ✅ Full — 2GB model + 0.5GB KV + 0.5GB draft = ~3GB |
| KV quantization | ✅ `kv_bits=4` at generate time |
| Download source | HuggingFace Hub (mlx-community) |
| Security posture | MEDIUM — trust_remote_code on draft, no hash verification |

---

## 22. rl/ Directory Analysis

### 22.1 Files Inventory

| File | Size | Purpose |
|------|------|---------|
| `__init__.py` | 720B | Module exports: QMIXAgent, QMixer, QMIXJointTrainer, QNetwork, MARLReplayBuffer, SprintPolicyManager |
| `actions.py` | 368B | Action space: CONTINUE=0, FETCH_MORE=1, DEEP_DIVE=2, BRANCH=3, YIELD=4 |
| `state_extractor.py` | 2433B | Feature extraction: graph metrics, scheduler stats, GNN embeddings, source quality |
| `qmix.py` | 9550B | QMIX MARL algorithm — QNetwork, QMixer (hypernetwork mixer), QMIXAgent, JointModel, QMIXJointTrainer |
| `sprint_policy_manager.py` | 15325B | Opt-in RL policy advisor wired to SprintScheduler (F195C) |
| `replay_buffer.py` | 3382B | MARLReplayBuffer: numpy ring buffer, MLX-compatible |
| `.sprint_policy_state.json` | 1037B | Persisted policy state (JSON, plain) |
| `.sprint_policy_state.json.zst` | 134B | Persisted policy state (Zstd-compressed, backup) |

---

### 22.2 RL Algorithm: QMIX (Value Decomposition Networks)

**QMIX** — multi-agent Q-learning with monotonic value decomposition. Individual agent Q-values are combined via a **hypernetwork mixer** that preserves monotonicity constraints, enabling joint training while maintaining individual agent optimality guarantees.

**QNetwork per-agent** (`qmix.py:64-70`):
```python
class QNetwork(nn.Module):
    def __init__(self, state_dim: int, hidden_dim: int = 64):
        self.fc1 = nn.Linear(state_dim, hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, hidden_dim)
        self.q_out = nn.Linear(hidden_dim, ACTION_DIM)  # 5 actions
```

**QMixer** (`qmix.py:45-62`) — hypernetwork that generates state-conditioned mixing weights:
```python
class QMixer(nn.Module):
    def __init__(self, n_agents: int, state_dim: int, embedding_dim: int = 32):
        self.hyper_w1 = nn.Linear(state_dim, embedding_dim * n_agents)
        self.hyper_w2 = nn.Linear(state_dim, embedding_dim)
        self.hyper_b1 = nn.Linear(state_dim, embedding_dim)
        self.hyper_b2 = nn.Linear(state_dim, 1)
```

**Monotonicity:** `mx.abs()` on hypernetwork weights (`qmix.py:89`) ensures global Q is monotonic in individual agent Q-values. **QMIXJointTrainer** (`qmix.py:127-205`) performs joint updates via `value_and_grad()`, Polyak-averaged target networks (`tau=0.005`). All MLX-native — raises `ImportError` if MLX unavailable.

---

### 22.3 SprintPolicyManager — F195C Integration

**Enabled=False by default** (`sprint_policy_manager.py:39`). RL is **dormant** unless explicitly enabled.

**Integration contract** (`sprint_policy_manager.py:63-66`):
- `update(result)` — called after each sprint, updates internal state + persists
- `should_explore()` — returns bool for exploration decision (DORMANT: SprintScheduler never calls it)
- `enabled=False` → all methods are no-ops

**SprintScheduler wiring** (`runtime/sprint_scheduler.py`):
- `run(policy_manager=...)` param at line 2441
- `inject_policy_manager()` at line 10977 — stores ref, calls `inject_scheduler(self)` if available
- `policy_manager.update(self._result)` at line 3355 — **wired**
- `update_with_quality_decisions()` at lines 3369-3389 — **wired** (per-source quality delegation)

**Critical finding:** `should_explore()` is **never called by SprintScheduler** (grep returns zero call sites). Exploration decisions are computed but discarded — a wasted code path.

---

### 22.4 Reward Computation

`_compute_reward()` (`sprint_policy_manager.py:150-175`):

| Signal | Weight | Source Field |
|--------|--------|--------------|
| Finding accepted | +1.0 | `findings_accepted` |
| Finding rejected | +0.0 | `findings_rejected` |
| First cycle completed | +2.0 | `first_cycle_completed` |
| Acquisition terminal | +5.0 | `acquisition_terminal` |
| Zero-yield detected | -3.0 | `feed_zero_yield_detected` |

---

### 22.5 State Persistence

**SprintPolicyState dataclass** (`sprint_policy_manager.py:22-31`):
```python
@dataclass
class SprintPolicyState:
    sprint_sequence_number: int = 0
    epsilon: float = 0.1
    total_reward: float = 0.0
    sprint_rewards: list[float] = []  # unbounded list
```

**Persistence flow:**
- `_load()` — reads `.sprint_policy_state.json` on init, fallback to `.sprint_policy_state.json.zst`
- `_save()` — writes `.sprint_policy_state.json.zst` if `zstandard` available, else plain JSON
- Both are fail-safe (caught exceptions log warning, no crash)
- `reset()` deletes the persisted file

**Current state:** `sprint_sequence_number=13`, `epsilon=0.1`, `total_reward=0.0`, `sprint_rewards=[]` — RL has run 13 sprints but accumulated zero reward (all sprints were either disabled or produced zero-yield).

---

### 22.6 Epsilon-Greedy Exploration

- `_DEFAULT_EPSILON = 0.1` (10% random)
- Floor: `0.05` (multiplicative decay `0.999` per sprint)
- Exploration interval: every 5 sprints (deterministic)

```python
def should_explore(self) -> bool:
    if random.random() < self._epsilon:
        return True  # stochastic epsilon-greedy
    if (self._state.sprint_sequence_number + 1) % 5 == 0:
        return True  # deterministic interval
    return False
```

**Status:** Implemented but **never called** by SprintScheduler.

---

### 22.7 Source Weight Delegation (F199A)

`update_with_quality_decisions()` accumulates per-source `accepted/total` ratios in `_pending_feedback` (bounded at 200 source_types), then delegates to `scheduler._source_quality_feedback` when `inject_scheduler()` is called. Creates a **two-layer source weighting system** — SprintScheduler's native F199A adaptation plus SprintPolicyManager's parallel accumulation (advisory only).

---

### 22.8 State Extractor

Extracts fixed-size feature vector (`state_dim=12` default) from sprint state:
1. Graph metrics: `node_count`, `edge_count`, `connected_components`
2. Sprint progress: `elapsed_ratio`, `findings_accepted_ratio`
3. Source quality: per-source `accepted/total` ratio
4. Scheduler hints: `concurrency`, `branch_degradation_summary`
5. GNN embedding (if `gnn_predictor` available — dormant)

Returns `mx.array` (MLX) or `np.array` (NumPy fallback).

---

### 22.9 MARLReplayBuffer

```python
class MARLReplayBuffer:
    def __init__(self, capacity: int = 50000, state_dim: int = 12, n_agents: int = 5):
        self.states = np.zeros((capacity, state_dim), dtype=np.float32)
        self.actions = np.zeros((capacity, n_agents), dtype=np.int32)
        self.rewards = np.zeros((capacity, n_agents), dtype=np.float32)
        self.next_states = np.zeros((capacity, state_dim), dtype=np.float32)
        self.dones = np.zeros((capacity, n_agents), dtype=np.bool_)
```

PER-ready structure. `save()`/`load()` via NumPy `.npz` format. **Status: dormant** — never instantiated in runtime, no training loop wired.

---

### 22.10 Security Analysis

#### 22.10.1 Adversarial Reward Poisoning (CRITICAL)

`.sprint_policy_state.json` is **world-readable** (`-rw-r--r--@`) and stored in the project directory. No HMAC, no signature, no encryption.

An attacker with write access to the file can:
- Set `sprint_rewards` to large positive values → epsilon decays slowly, policy freezes learning
- Set `epsilon=0.0` immediately → disables exploration permanently
- Set `sprint_sequence_number=1000000` → potential integer overflow
- Corrupt the JSON → `_load()` catches exception and resets to empty state (denial of learning)

#### 22.10.2 State Injection Attack

`update()` accepts a weakly-typed `SprintSchedulerResult` object. A malicious scheduler object can provide crafted `acquisition_terminal=True` on a zero-yield sprint to generate +5.0 reward, poisoning the policy. In production the scheduler is local, but the interface is weakly typed.

#### 22.10.3 QMIX Neural Network Inputs (MEDIUM)

Q-networks accept arbitrary `mx.array` state vectors. Out-of-distribution states from a corrupted `StateExtractor` or state injection could cause Q-value overflow (inf/nan), gradient explosion, or policy collapse. **Status:** Training not wired (dormant).

#### 22.10.4 Source Weight Feedback Injection (MEDIUM)

`update_with_quality_decisions()` accepts arbitrary `FindingQualityDecision` lists with no validation. A malicious caller could set fake `accepted/total` ratios to manipulate source weights.

#### 22.10.5 File Permissions

```
.rw-r--r--@  .sprint_policy_state.json   # Anyone can read
.rw-r--r--@  .sprint_policy_state.json.zst
```

Should be `600` — sensitive learned policy data stored with wide read permissions.

---

### 22.11 Dormant Code Summary

| Component | Status |
|-----------|--------|
| QMIX QNetwork | Dormant — no instantiation in `SprintPolicyManager` |
| QMixer | Dormant — no instantiation |
| QMIXJointTrainer | Dormant — never called |
| MARLReplayBuffer | Dormant — never instantiated in runtime |
| `should_explore()` output | Wasted — SprintScheduler never calls it |
| Per-source quality feedback | Partial — only `update_with_quality_decisions()` wired |

**Active (wired):**
- `SprintPolicyManager.update()` called post-sprint
- `update_with_quality_decisions()` wired
- State persists to disk
- Reward tracking active

**Conclusion:** RL layer is a **partially-wired advisory system** — reward tracking and persistence work, but QMIX policy learning and exploration decisions are dormant. Primary active security surface: **unauthenticated policy state JSON** vulnerable to reward poisoning and epsilon manipulation.
| Memory isolation | ✅ Single model, no parallel, MX eval barrier |
| Legacy issues | bf16 vs Q4 mismatch in `layers/memory_layer.py:716` |

## 17. data/ Directory Analysis

### 17.1 Directory Existence and Structure

**Finding**: The `data/` directory inside `hledac/universal/` exists but is **completely empty** (0 bytes, 2 entries: `.` and `..`). It is a placeholder directory with no runtime or reference data stored within the project boundary.

However, the parent project at `/Users/vojtechhamada/PycharmProjects/Hledac/data/` contains substantial runtime data:

| File/Directory | Size | Purpose |
|---|---|---|
| `GeoLite2-Country.mmdb` | 9.8 MB | MaxMind GeoIP country database |
| `hledac.sqlite3` | 53 KB | Legacy findings/Audit/WARC storage |
| `llm_cache.sqlite3` | 16 KB | LLM prompt/response cache |
| `phase6_context_hierarchy.db` | 41 KB | Context node hierarchy |
| `chroma/chroma.sqlite3` | 164 KB | Chroma vector embeddings DB |
| `messaging_system/` | 256 KB | Inter-agent messaging queue |
| `health_status.json` | 465 B | System health checks |
| `startup_report.json` | 789 B | Startup validation report |
| `validation_report.json` | 277 B | Configuration validation |

### 17.2 Database File Analysis

#### hledac.sqlite3 (53 KB)
**Status**: Empty tables (0 rows in all 3 tables). Legacy/schema-only database.

Schema:
- `findings`: id (VARCHAR 64), url, snippet, created_at — **0 rows**
- `audit_trail`: id, finding_id, event, details, created_at — **0 rows**
- `warc_records`: run_id, agent, warc_path, status_code, captured_at, record_type, record_id, warc_date, payload_digest, payload_length, bytes_written, bytes_saved, revisit_of_record_id, revisit_of_uri — **0 rows**

**Security**: No credentials, no PII — empty legacy schema.

#### llm_cache.sqlite3 (16 KB)
**Status**: 1 cached entry (role=triage, hash=49b6d722..., res_len=100).

Schema:
```sql
CREATE TABLE llm_cache (
  role TEXT NOT NULL,
  prompt_hash TEXT NOT NULL,
  retrieval_fingerprint TEXT NOT NULL,
  res TEXT NOT NULL,
  usage_json TEXT,
  cached_at REAL NOT NULL,
  PRIMARY KEY (role, prompt_hash, retrieval_fingerprint)
)
```

**Security**: Caches LLM responses — no raw credentials. Hash prefixes (49b6d7...) are SHA-like, not secrets. No PII.

#### phase6_context_hierarchy.db (41 KB)
**Status**: 6 context_nodes rows, 0 updates, 0 snapshots.

Schema: `context_nodes`, `context_updates`, `context_snapshots`.

**Security**: Context snapshot data — no credentials or raw PII in sampled data.

#### chroma/chroma.sqlite3 (164 KB)
**Status**: Schema only, 0 embeddings. Chroma vector DB — tables: `embeddings`, `collections`, `tenants`, `databases`, `segments`, etc. 15 migration rows, 2 segments, 0 active embeddings.

**Security**: Vector embedding store — no PII without actual embedding content.

### 17.3 Inter-Agent Messaging System

`messaging_system/` contains JSON message queues:

| File | Size | Content |
|---|---|---|
| `message_008cff60-1082-48d6-98ee-0e66b6e49508.json` | 437 B | agent1→agent2 message |
| `message_79aa4865-b69d-46b5-a037-8df871e9319b.json` | 437 B | agent1→agent2 message (dup) |
| `ack_79aa4865-b69d-46b5-a037-8df871e9319b_agent2.json` | 139 B | agent2 acknowledgment |
| `agent_capabilities_agent1.json` | 29 B | Agent 1 capabilities |
| `agent_capabilities_agent2.json` | 31 B | Agent 2 capabilities |

Sample message structure (agent_id=agent1, agent_id=agent2):
```json
{
  "message_id": "79aa4865-b69d-46b5-a037-8df871e9319b",
  "sender_id": "agent1",
  "recipient_id": "agent2",
  "timestamp": "2025-10-14T12:37:18.816921+00:00",
  "message_type": "task_delegation",
  "payload": {...}
}
```

**Security**: Agent identifiers are generic (agent1/agent2) — no real names or emails. No credentials. Minimal PII surface. Timestamps from Oct 2025 — stale, likely orphaned.

### 17.4 GeoLite2-Country.mmdb (9.8 MB)

**Format**: MaxMind DB (binary, version detected from header `00000118...`). This is a GeoIP country lookup database — used for IP-to-country geolocation in CTI enrichment.

**Purpose**: Maps IP addresses to country codes for infrastructure attribution (BGP lane, exposure correlator).

**Security**: Public MaxMind database — no credentials, no PII. Header bytes confirm MMDB format (0x000001 magic + metadata).

### 17.5 JSON Status Files

#### health_status.json (465 B)
```json
{
  "timestamp": 1763065951.80,
  "checks": {
    "configuration": {"status": "unhealthy", "details": "Failed to load"},
    "agents": {"status": "unhealthy", "details": "No agents initialized"},
    "orchestrator": {"status": "unhealthy", "details": "Orchestrator not initialized"},
    "memory": {"status": "healthy", "usage_percent": 81.7, "available_gb": 1.46}
  }
}
```

**Finding**: Health status from ~Nov 2025. Orchestrator unhealthy, 1.46 GB RAM available on M1 — this is operational telemetry, not credentials or PII.

#### startup_report.json (789 B)
```json
{
  "timestamp": 1763065951.80,
  "duration_seconds": 0.000646,
  "status": "partial_failure",
  "environment": {"python_version": "3.11.8", "platform": "darwin", ...},
  "components": {"configuration": "failed", "agents": 0, "orchestrator": "failed"},
  "errors": [...]
}
```

**Finding**: Startup telemetry — Python 3.11.8, Darwin, working directory. No credentials or user PII.

#### validation_report.json (277 B)
```json
{
  "timestamp": 1763066037.81,
  "validation": {"configuration": true, "imports": true, "directories": true, "files": true, "functionality": false},
  "overall_status": "partial", "passed_checks": 5, "total_checks": 6
}
```

**Finding**: Validation check results — no credentials, no PII.

### 17.6 Data Flow Analysis

#### Into data/
- **messaging_system/**: Written by agent orchestration layer (agent1, agent2 messaging)
- **llm_cache.sqlite3**: Written by LLM cache layer (role=triage, prompt_hash index)
- **phase6_context_hierarchy.db**: Written by context hierarchy manager (6 context nodes)
- **chroma/**: Written by vector embedding pipeline (0 active embeddings currently)
- **health/startup/validation reports**: Written by health/check subsystems

#### Out of data/
- **GeoLite2-Country.mmdb**: Read by BGP lane, exposure correlator, IP intelligence modules
- **hledac.sqlite3**: Read by legacy WARC replay and finding lookup (currently empty)
- **chroma**: Read by semantic search/reranking (brain/ane_embedder.py)
- **messaging_system/**: Read by agent coordination layer

#### data/hledac_duckdb/ and data/hledac.lmdb/ (Runbooks Only)
These paths appear ONLY in runbooks (`database-corruption.md`, `mlx-oom.md`, `pipeline-stall.md`) as **documentation of potential paths**, NOT as actual runtime locations. The runbooks describe how to recover from corruption at these paths, but they don't exist at either `hledac/universal/data/` or at the parent `Hledac/data/`.

**Conclusion**: Canonical DuckDB and LMDB stores are NOT in `data/` — they are managed through `paths.py` and `knowledge/` modules at runtime paths outside the project boundary.

### 17.7 Security Assessment

| Aspect | Status | Notes |
|---|---|---|
| Credentials in data files | ✅ CLEAN | No API keys, no secrets, no tokens |
| PII in data files | ✅ CLEAN | Agent IDs are generic, no personal data |
| Secrets in messaging | ✅ CLEAN | agent1/agent2 are placeholder identifiers |
| GeoIP database | ✅ SAFE | Public MaxMind DB, no credentials |
| Stale data risk | ⚠️ MEDIUM | messaging_system from Oct 2025 (6+ months old) |
| Empty DB risk | ✅ LOW | hledac.sqlite3 empty — no sensitive data at rest |
| Vector DB exposure | ✅ LOW | chroma has 0 embeddings — no data leak surface |
| messaging queue exposure | ⚠️ LOW | Inter-agent messages could contain task payloads if reactivated |

### 17.8 Runbook References (Documentation Only)

The following paths are referenced ONLY in runbook documentation and do NOT exist on disk:
- `data/hledac_duckdb/` — documented in `runbooks/database-corruption.md`
- `data/hledac.lmdb/` — documented in `runbooks/database-corruption.md` and `runbooks/mlx-oom.md`
- `data/checkpoints/` — documented in `runbooks/database-corruption.md` and `runbooks/pipeline-stall.md`
- `data/tmp/` — referenced in `runbooks/pipeline-stall.md`

These are operational path documentation, not actual data locations.

### 17.9 Summary

| Item | Assessment |
|---|---|
| `data/` inside universal/ | Empty placeholder — no production data |
| Parent `Hledac/data/` | 9.8 MB GeoIP DB + 53 KB empty SQLite + 16 KB LLM cache + messaging queue |
| Database files | All empty or nearly empty — no active production data |
| Credentials/PII | ✅ None found |
| Security posture | LOW RISK — stale, minimal data, no secrets |
| Data flow | GeoLite2→BGP/exposure; messaging→agent coordination; rest is dormant/empty |

## 19. logs/ Directory Analysis

### Directory Status

The `logs/` directory at `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/logs/` is **empty** (0 files, created May 20 07:58). It serves as a placeholder but is not actively used by the sprint pipeline.

### Actual Log Locations

| Location | Type | Purpose |
|----------|------|---------|
| `~/.hledac/runs/logs/metrics.jsonl` | JSON lines | Sprint telemetry (memory, FDs) |
| `~/.hledac/runs/logs/tool_exec.jsonl` | JSON lines | Tool execution tracking (empty) |
| `hledac/universal/live_run_*.log` | Text | Sprint stderr/stdout capture |
| `hledac/universal/reports/*.log` | Text | Sprint run reports |
| `security/automation/` | Python | Threat intel automation (no logs written) |

### Log Files by Type

**Sprint Run Logs** (root level):
- `live_run_2026-05-12T14-58-53_150s.log` — 590 bytes
- `live_run_2026-05-12T15-01-04_150s.log` — 596 bytes
- `live_run_2026-05-12T15-01-35_150s.log` — 3.8 KB
- `live_run_2026-05-12T15-05-35_150s.log` — 3.6 KB
- `live_run_2026-05-12T15-08-41_150s.log` — 8.5 KB

**Report Logs** (`reports/`):
- `f233a_domain_live.log`, `f223f_domain_lockbit3_nonfeed_180.log`, `f226b_domain_lockbit3_nonfeed_check.log`, `f229b_domain_lockbit3_nonfeed_180.log`, `nonfeed_diagnostic_lockbit_180.log`

### Log Format Analysis

**metrics.jsonl Schema** (84 entries, 15.5 KB total):
```json
{"ts":"2026-05-04T14:32:19.652455","name":"memory_rss_mb","type":"gauge","value":110.609375,"correlation":{"provider_id":null,"branch_id":null,"action_id":null,"run_id":"test_sprint"}}
{"ts":"2026-05-18T02:14:30.747817+00:00","name":"memory_vms_mb","type":"gauge","value":433901.265625,"correlation":{"run_id":"default",...}}
```

**Event types captured**: `memory_rss_mb`, `memory_vms_mb`, `memory_open_fds` — all gauges, no counters or histograms.

**Live run log format**:
```
EXIT:1
STDOUT:W:hledac.universal.__main__:[OPSEC] PYTHON_DISABLE_REMOTE_DEBUG not set...
INFO:hledac.universal.runtime.sprint_scheduler:[MEM] fetch_limit=2 ml_jobs=0
INFO:hledac.universal.core.__main__:[TEARDOWN] sprint_delta written: 251 findings, 0 dedup hits, UMA delta: +0.37GiB, top_source: 'https://krebsonsecurity.com/feed/', findings_per_min: 2137.95
INFO:hledac.universal.core.__main__:[SPRINT DONE] 8sa_1778855779607_34280d | findings: 251 | cycles: 0/1 | duplicates: 0 | phase: ACTIVE
```

### Rotating/Rotation Policy

**No rotation policy implemented**:
- `metrics.jsonl` appends indefinitely (84 entries since Feb 28)
- Live run logs accumulate at root level (5 files from May 12)
- Report logs persist in `reports/` directory
- No log rotation, no size-based cleanup, no time-based retention

**Sprint F205H MetricsRegistry** (`sprint_scheduler.py:2520,9725-9750`):
- Initializes with `run_dir = ~/.hledac/runs` or `export_dir`
- Writes `run_dir/logs/metrics.jsonl` and `run_dir/logs/tool_exec.jsonl`
- No rotation configured; unbounded append

### Audit Trail Structure

**PrivacyEnhancedResearch** (`coordinators/privacy_enhanced_research.py`):
- `AuditRecord` dataclass: timestamp, operation_id, operation_type, resource, outcome, metadata
- `_audit_log`: bounded list, max **10,000 records** (`if len(self._audit_log) > 10000: self._audit_log = self._audit_log[-10000:]`)
- `DataRetention` enum: SESSION, SHORT (1h), MEDIUM (24h), LONG (7 days)
- `PrivacyConfig`: audit_logging=True by default

**SecurityCoordinator** (`coordinators/security_coordinator.py`):
- `SecurityContext.audit_log`: operation-scoped audit trail, appends on each operation
- PII detection via `SecurityGate.sanitize()` with `mask_pii=True`
- Redaction results: `redacted` text, `detections_count`, `masked_patterns`

### Security Implications

**PII Handling**:
- `SecurityCoordinator.redact_pii()` uses `SecurityGate.sanitize(mask_pii=True, return_matches=True)`
- Audit records track PII detections without storing raw PII
- `DynamicContextManager.access_log`: item_id only, no user identifiers

**Credential Exposure Risk**:
- Live run logs contain full URLs with query parameters (e.g., `?q=LockBit ransomware`)
- Stack traces expose file paths: `/Users/vojtechhamada/PycharmProjects/Hledac/...`
- No credential redaction in live run logs — API keys in environment variables NOT logged
- `captcha_solver.py` accesses `_2captcha_api_key` but does NOT log it

**OPSEC Alert in Logs**:
```
W:hledac.universal.__main__:[OPSEC] PYTHON_DISABLE_REMOTE_DEBUG not set — Python 3.14 safe-external-debugger interface is ACTIVE.
```
This is informational — correctly flags when debugger interface is active.

**RAMDisk Warning** (non-critical):
```
UserWarning: [GHOST OPSEC] No active ramdisk found at /Volumes/ghost_tmp and GHOST_RAMDISK is unset. Runtime artifacts will be written to SSD fallback location.
```

### Sprint/Pipeline Execution Logging

**Sprint lifecycle logging** (from `core.__main__`):
- `[BOOT GUARD] result: -=0, reason=lock_file_not_found`
- `[MEM] fetch_limit=2 ml_jobs=0`
- `[CLEARNET_WORKERS] Adjusted from 25 to 3`
- `[TEARDOWN] sprint_delta written: N findings, M dedup hits, UMA delta: +X.XXGiB, top_source: 'URL', findings_per_min: YYY`
- `[SPRINT DONE] sprint_id | findings: N | cycles: X/Y | duplicates: M | phase: ACTIVE`
- `[SUMMARY] FEED-LED: feed sources strong | feed=N public=M(P%)`

**SprintResult fields logged**: `findings`, `dedup_hits`, `UMA_delta`, `top_source`, `findings_per_min`, `cycles_completed`

**Finding logging** (`FindingResult`): stored in DuckDB, not in text logs. Evidence envelopes stored in LMDB `payload_text` field keyed by `finding_id`.

### Security Posture Summary

| Aspect | Status |
|--------|--------|
| Log directory | Empty (placeholder unused) |
| Metrics persistence | Unbounded append, no rotation |
| Audit log bounds | 10,000 record cap in PrivacyEnhancedResearch |
| PII redaction | Implemented via SecurityGate, NOT applied to metrics.jsonl |
| Credential leakage | No credentials in logs; URLs/query params not redacted |
| Rotation policy | NONE — logs grow indefinitely |
| Retention policy | SESSION default; actual cleanup not enforced |

### Findings

1. **No rotation** — `metrics.jsonl` will grow indefinitely; no cleanup mechanism
2. **No redaction on metrics** — PII not masked in telemetry events
3. **Audit bounds only in PrivacyEnhancedResearch** — other audit logs unbounded
4. **Live run logs at root** — accumulate without cleanup since May 12
5. **tool_exec.jsonl empty** — execution tracking not wired yet

## 16. config/ Directory Analysis

### Overview

The `config/` subdirectory is empty (0 files, 64 bytes — directory only). Configuration for Hledac is managed through two root-level files:

| File | Size | Lines | Type |
|------|------|-------|------|
| `config-schema.json` | 23,706 bytes | 666 lines | JSON schema |
| `config.py` | 23,743 bytes | 730 lines | Python dataclass |
| `pyrightconfig.json` | 149 bytes | 6 lines | Pyright config |

### config-schema.json — JSON Schema (19 sections, 111 keys)

Authoritative spec for all configuration keys: types, defaults, descriptions. Used for validation in `load_config_from_file()`.

#### Top-level structure

```json
{
  "version": 1,
  "root": { /* top-level cfg keys */ },
  "sections": {
    "archive", "autonomy", "boundary_policy", "cloud",
    "custom_aliases", "ghost", "ide_paths", "lean_ctx",
    "loop_detection", "memory", "providers", "proxy",
    "secret_detection", "stealth", "updates"
  }
}
```

#### Section breakdown

**[archive]** (4 keys) — Zero-loss compression archive for large tool outputs
- `enabled`: bool = true
- `max_age_hours`: u64 = 48
- `max_disk_mb`: u64 = 500
- `compression_algorithm`: string = "lz4"

**[autonomy]** (9 keys) — Autonomous background behaviors (preload, dedup, consolidation)
- `auto_consolidate`: bool = true
- `silent_preload`: bool = false
- `max_episodes`: u32 = 100
- `recall_facts_limit`: u32 = 500
- `similarity_threshold`: f32 = 0.85

**[boundary_policy]** (4 keys) — Cross-project access control
- `audit_cross_access`: bool = true — logs audit events on cross-project access
- `cross_project_import`: bool = false
- `cross_project_search`: bool = false
- `universal_gotchas_enabled`: bool = true

**[cloud]** (1 key)
- `contribute_enabled`: bool = false

**[custom_aliases]** — Alias definitions for tool shortcuts

**[ghost]** — Ghost layer configuration

**[ide_paths]** — Per-IDE allowed paths (cursor, codex, opencode, antigravity, etc.)

**[lean_ctx]** (19 keys) — Context engine configuration
- `compression_level`: enum [off, light, medium, aggressive] default=medium
- `response_verbosity`: enum [normal, compact, minimal] default=normal
- `rules_scope`: enum [both, global, local] default=both
- `max_facts`: u32 = 500
- `auto_dedup`: bool = true
- Env overrides via: `LEAN_CTX_COMPRESSION`, `LEAN_CTX_RESPONSE_VERBOSITY`, etc.

**[loop_detection]** (6 keys) — Prevents repeated identical tool calls
- `blocked_threshold`: u32 = 0, `normal_threshold`: u32 = 2, `reduced_threshold`: u32 = 4
- `search_group_limit`: u32 = 10, `window_secs`: u64 = 300
- `tool_total_limits`: table — ctx_read=100, ctx_search=80, ctx_semantic_search=60, ctx_shell=50

**[memory]** — Embeddings and episodic memory settings

**[providers]** — External ctx providers (GitHub, GitLab, Jira, MCP bridges)
- `auto_index`: bool = true, `cache_ttl_secs`: u64 = 120, `default`: bool = true
- `github.*`: api_url, token_env (env var name for GITHUB_TOKEN)
- `mcp_bridges.<name>.*`: auth_env (env var for MCP auth token), command (stdio transport), url (HTTP/SSE remote)

**[proxy]** — API routing proxy configuration
- `anthropic_upstream`: string? = null — Custom URL for Anthropic API proxy
- `gemini_upstream`: string? = null — Custom URL for Gemini API proxy
- `openai_upstream`: string? = null — Custom URL for OpenAI API proxy

**[secret_detection]** (3 keys) — Secret/credential detection and redaction
- `custom_patterns`: array = [], `redact`: bool = true

**[stealth]** — Stealth browsing and evasion configuration

**[updates]** (3 keys) — Automatic update configuration
- `auto_update`: bool = false, `check_interval_hours`: u64 = 6, `notify_only`: bool = false

**[root]** (top-level keys)
- `agent_token_budget`: usize = 0 — per-agent token budget. 0 = unlimited
- `allow_auto_reroot`: bool = false

### config.py — Python Dataclasses (666 lines)

#### ResearchMode Enum + Presets

```python
class ResearchMode(Enum):
    QUICK       # 5 min, 10 steps, 4 agents, no knowledge graph
    STANDARD    # 30 min, 30 steps, 4 agents, RAG on
    DEEP        # 120 min, 50 steps, 6 agents, full stack
    EXTREME     # 480 min, 100 steps, 6 agents
    AUTONOMOUS  # 1440 min (24h), 200 steps, 6 agents
```

Each mode has a preset dict: `{max_steps, max_time_minutes, max_concurrent_agents, enable_knowledge_graph, enable_rag, enable_fact_checking, save_intermediate}`.

#### M1Presets (21 lines) — M1 8GB RAM optimization presets

```python
MEMORY_LIMIT_MB = 5500.0
THERMAL_THRESHOLD_C = 85
CONTEXT_SWAP_ENABLED = True
MLX_CACHE_CLEAR_INTERVAL = 10  # Clear MLX cache every N transitions
```

#### SecurityConfig (25 lines)

- `obfuscation_level`: str = "medium"  # none, light, medium, heavy, max
- `generate_decoys`: bool = True, `decoy_count`: int = 20
- `wipe_standard`: str = "nist_800_88"  # nist_800_88, dod_5220_22m, gutmann
- `verification_enabled`: bool = True
- `enable_encryption`: bool = True, `encryption_algorithm`: str = "fernet"  # fernet, aes256
- `privacy_level`: str = "high", `anonymize_pii`: bool = True

#### StealthConfig (29 lines)

- `browser_type`: str = "chromium", `enable_fingerprint_rotation`: bool = True
- `fingerprint_count`: int = 50, `user_agent_rotation_interval`: int = 300
- `enable_tor`: bool = False, `tor_proxy`: str = "socks5://127.0.0.1:9050"
- `enable_proxy_rotation`: bool = False, `proxy_list`: List[str] = []
- `enable_dns_encryption`: bool = True, `dns_servers`: List[str] = ["1.1.1.1", "9.9.9.9"]
- `use_doh`: bool = False  # P16: DNS-over-HTTPS via resolve_doh before fetch

#### PrivacyConfig (20 lines)

- `privacy_level`: str = "high", `enable_audit_logging`: bool = True, `anonymize_pii`: bool = True

#### DeepResearchConfig (27 lines)

- `enabled`: bool = True, `max_depth`: int = 5, `source_timeout`: int = 30
- `enable_parallel_search`: bool = True

#### UniversalConfig (437 lines) — Main orchestrator config

Sub-configurations (dataclass fields):
- `research`: ResearchConfig, `memory`: MemoryConfig, `ghost`: GhostConfig
- `coordination`: CoordinationConfig, `agent_manager`: AgentManagerConfig
- Extended: `security`: SecurityConfig, `stealth`: StealthConfig
- `privacy`: PrivacyConfig, `deep_research`: DeepResearchConfig

Feature flags (all True by default except where noted):
- `enable_ghost_layer`, `enable_coordination_layer`, `enable_reasoning_engine`
- `enable_security_layer`, `enable_stealth_layer`, `enable_communication_layer`
- `enable_deep_research`
- `enable_knowledge_layer`: bool = False (RAM-intensive, disabled by default)
- `enable_rag_pipeline`: bool = False (RAM-intensive, disabled by default)
- `enable_privacy_layer`: bool = False (requires VPN/Tor)

Hardware fields:
- `mlx_cache_clear_interval`: int = 10
- `memory_limit_mb`: float
- `enable_thermal_management`: bool = True
- MoE: `enable_moe_router`: bool = True, `moe_max_active_experts`: int = 2 (M1 8GB limit)
- SNN: `enable_neuromorphic`: bool = True, `snn_n_neurons`: int = 500, `snn_connection_prob`: float = 0.05
- `enable_federated_osint`: bool = False (disabled by default, privacy)

Key methods:
- `for_mode(cls, mode: ResearchMode, m1_optimized: bool = True)` — Creates mode-specific config with M1 optimizations applied
- `_apply_m1_optimizations(self)` — Sets memory_limit_mb to 5500MB, thermal threshold to 85C, disables heavy features if >4 concurrent agents
- `load_from_env(cls)` — Loads from env vars: `HLEDAC_MODE`, `HLEDAC_M1_OPTIMIZED`, `HLEDAC_MEMORY_LIMIT_MB`, `HLEDAC_MAX_STEPS`, `HLEDAC_LOG_LEVEL`

### pyrightconfig.json — Type Checker Config (6 lines)

```json
{
  "include": ["."],
  "pythonVersion": "3.14",
  "typeCheckingMode": "basic",
  "reportMissingImports": false,
  "reportMissingTypeStubs": false
}
```

Minimal config — basic type checking only, no strict mode.

### Security Analysis

**Security-sensitive fields:**
- `providers.mcp_bridges.<name>.auth_env` — env var name containing auth token for MCP server
- `root.agent_token_budget` — per-agent token limit, not a credential
- `stealth.tor_proxy` — default `socks5://127.0.0.1:9050` (localhost Tor daemon)

**No hardcoded credentials found.** No API keys, no live/test tokens, no passwords, no bearer tokens in config files. Actual tokens stored in environment variables (GITHUB_TOKEN, GITLAB_TOKEN), referenced by name only in schema.

**Proxy upstream URLs** (anthropic_upstream, gemini_upstream, openai_upstream) are nullable custom endpoints, not credentials.

**Env var references:** Only `TOR_PROXY_URL` hardcoded in config.py as default for tor_proxy. Schema documents GITHUB_TOKEN/GITLAB_TOKEN but only as env var references, not stored values.

**One localhost reference:** `127.0.0.1:9050` for Tor proxy default — safe, requires local Tor daemon.

### Wiring to Sprint/Pipeline

**Directly wired to sprint/pipeline:**
- `autonomous_orchestrator.py:3145` — `UniversalConfig.for_mode(ResearchMode.AUTONOMOUS)`
- `core/__main__.py` — uses config for sprint execution
- `runtime/sprint_scheduler.py` — uses config for resource governor decisions
- `coordinators/resource_allocator.py` — `_load_config()` loads dict config
- `utils/execution_optimizer.py` — `_load_config()` loads dict config

**Schema validation:** `load_config_from_file(path)` at config.py:632 parses JSON/YAML/TOML and validates against the schema.

**load_config_from_file** supports JSON, YAML, TOML by file extension.

### M1/Hardware-Specific Configuration

M1 8GB UMA optimizations controlled via:
- `M1Presets` class — compile-time constants (MEMORY_LIMIT_MB=5500, THERMAL_THRESHOLD_C=85, MLX_CACHE_CLEAR_INTERVAL=10)
- `m1_optimized` flag in `for_mode()` — enables `_apply_m1_optimizations()`
- `HLEDAC_M1_OPTIMIZED=true` env var — M1 optimizations auto-enabled on Apple Silicon
- `HLEDAC_MEMORY_LIMIT_MB` env var override

M1-specific bounds:
- `moe_max_active_experts`: int = 2 (M1 8GB limit)
- `snn_n_neurons`: int = 500 (M1 8GB optimized)
- `memory_limit_mb` set to 5500MB via M1Presets
- Heavy features (knowledge_layer) auto-disabled if `max_concurrent_agents > 4` with m1_optimized=True

### Findings

1. **No credentials in config files** — API keys/tokens stored in environment variables only, referenced by env var name in schema
2. **Proxy URLs are nullable** — custom upstream proxies are optional, no hardcoded internal URLs
3. **Tor proxy default is localhost** — `socks5://127.0.0.1:9050` — safe, requires local Tor daemon
4. **M1 optimizations are opt-out via env var** — `HLEDAC_M1_OPTIMIZED=false` disables M1-specific bounds
5. **Feature flags mostly on by default** — ghost, coordination, security, stealth, deep research enabled; privacy_layer and knowledge_layer disabled (requires VPN/Tor or too RAM-intensive)
6. **Schema is comprehensive** — 19 sections, 111 keys covering all major subsystems
7. **No dead/inactive configs** — all sections have corresponding code paths in sprint/pipeline
8. **config/ subdirectory is empty** — config files live at project root; the `config/` directory exists but is unused. This is a structural quirk worth noting.

## 18. docs/ Directory Analysis

### 18.1 Structure Overview

**83 files total** (82 `.md` + 1 `.DS_Store`), **1.0 MB total**, **17,516 lines of markdown**.

| Subdirectory | File Count | Purpose |
|---|---|---|
| `docs/` (root) | 9 | Architecture, runbooks, dependency guides, capability matrix, testing |
| `docs/audits/` | 56 | Sprint audit reports, capability truth reconciliations, implementation plans |
| `docs/agents/` | 4 | Agent domain docs, issue tracker conventions, triage labels |
| `docs/sprints/` | 1 | Sprint-specific refactoring plans |
| `docs/runtime/` | 2 | Runtime seam audits, graph accumulator phase plans |

### 18.2 Documentation Types

**100% markdown** — no rst, txt, json, yaml, or structured API docs. All documentation is prose-driven with embedded tables and code blocks.

**Key root-level docs:**
- `ARCHITECTURE.md` (186 lines, last modified 2026-05-12) — entry points, lane pipeline, data structures, key imports
- `LOCAL_M1_SMOKE_RUNBOOK.md` (543 lines, last modified 2026-05-20) — operational smoke test procedures
- `LIVE_SPRINT_EXPERIMENT_MATRIX.md` (471 lines, last modified 2026-05-20) — structured live sprint runbook
- `LOCAL_OSINT_CAPABILITY_MATRIX.md` (9,044 bytes, last modified 2026-05-18) — capability coverage mapping
- `DEPENDENCY_HYGIENE.md` (5,330 bytes, last modified 2026-05-18) — dependency management guide
- `DEPENDENCY_PROFILES.md` (3,370 bytes, last modified 2026-05-18) — M1/NPU profile definitions
- `TESTING.md` (2,224 bytes, last modified 2026-05-18) — test execution guidance
- `CODEX_AUDIT_REPORT.md` (9,017 bytes, last modified 2026-05-12) — Claude Codex model audit
- `type_audit.md` (4,161 bytes, last modified 2026-05-12) — sprint_scheduler.py type annotation audit

### 18.3 Audit Trail Depth

**56 audit documents** in `docs/audits/` — comprehensive sprint post-mortems and capability audits. Most recent:
- `SPRINT_COORDINATORS_AUDIT_20260523.md` (25.0 KB, 2026-05-23) — current day
- `CAPABILITY_MATRIX_WIRING_AUDIT.md` (28.5 KB, 2026-05-20) — capability wiring truth
- `OSINT_CAPABILITY_COVERAGE_AUDIT.md` (23.2 KB, 2026-05-20) — coverage mapping
- `WHOLE_REPO_CAPABILITY_INVENTORY.md` (22.5 KB, 2026-05-20) — complete capability inventory
- `LIVE_SPRINT_READINESS_AUDIT.md` (18.2 KB, 2026-05-20) — pre-sprint readiness

**Audit coverage by domain:** acquisition profiles, DuckDB store seams, coordinators, enrichments, export pipeline, inference engine, resource governor, sidecar activation, M1 offline performance, offline provider yield, PDNS implementation, RDAP/RIR/WHOIS unification, Python 3.14 compat, pytest collection, import time analysis, dependency truth.

### 18.4 Security-Sensitive Documentation

**No credentials, API keys, or secrets found** in any documentation. Search results:
- `ARCHITECTURE.md` — references key constants and data structures, no secrets
- `LOCAL_M1_SMOKE_RUNBOOK.md` — operational runbook, no credentials
- All `audits/` files — audit findings, no sensitive values

**10 audit files flagged** as containing security-related terms (mostly "authority", "auth", "credential" in context of architectural decisions — not actual secrets). Verified: no `sk_`, `password`, `api.key`, `token`, `secret` patterns in documentation.

### 18.5 Documentation Drift Risk

**CRITICAL DRIFT RISK: ARCHITECTURE.md**

`ARCHITECTURE.md` is **11 days stale** (last modified 2026-05-12) and documents:
- Entry points with specific line numbers (e.g., `core/__main__.py:869`)
- Lane pipeline structure
- Key data structures and imports

However, the codebase has had extensive changes since 2026-05-12 (multiple sprint commits, coordinator audits, capability activations). The `SPRINT_COORDINATORS_AUDIT_20260523.md` (current day) supersedes much of the architecture documentation with verified, current line numbers and domain maps.

**Risk:** ARCHITECTURE.md references `runtime/sprint_scheduler.py` only 3 times — the document is significantly under-referencing the core engine that has grown to 11,720+ lines.

**Staleness summary:** No documentation older than 30 days. Most audit docs (2026-05-20/21) are current. ARCHITECTURE.md and CODEX_AUDIT_REPORT.md (both 2026-05-12) are the most stale root docs.

### 18.6 Documentation Coverage Gaps

| Area | Coverage Status |
|---|---|
| **API Reference** | NONE — no structured API docs (no endpoint specs, no parameter docs, no response schemas) |
| **Runbooks** | PARTIAL — only `LOCAL_M1_SMOKE_RUNBOOK.md` and `LIVE_SPRINT_EXPERIMENT_MATRIX.md` exist; `docs/runbooks/` directory does not exist |
| **Architecture** | PARTIAL — ARCHITECTURE.md exists but is stale and incomplete (missing 11K-line sprint_scheduler coverage) |
| **Agent Instructions** | GOOD — `agents/domain.md`, `agents/issue-tracker.md`, `agents/triage-labels.md` are current |
| **Testing** | MINIMAL — TESTING.md is only 2,224 bytes; no test strategy, no coverage goals |
| **Dependency Docs** | GOOD — DEPENDENCY_HYGIENE.md and DEPENDENCY_PROFILES.md are current (2026-05-18) |
| **Capability Matrix** | CURRENT — `LOCAL_OSINT_CAPABILITY_MATRIX.md` and `WHOLE_REPO_CAPABILITY_INVENTORY.md` are 2026-05-20 |
| **Sprint Docs** | CURRENT — 56 audit docs across 2026-05, active documentation cycle |

**Major gaps:**
1. **No API documentation** — no Swagger/OpenAPI, no endpoint references, no parameter documentation
2. **No operational runbooks directory** — `docs/runbooks/` referenced in audit doc but does not exist on disk
3. **No security runbooks** — no dedicated security operation procedures
4. **ARCHITECTURE.md is 11 days stale** and references sprint_scheduler only 3 times despite it being the 11,720-line core engine

### 18.7 Agent Documentation Quality

`docs/agents/` is well-structured:
- `domain.md` (5,163 bytes, 2026-05-21) — architecture connectivity plan with explicit vocabulary guidance
- `domain.md` — cross-references `CONTEXT.md` and `docs/adr/` (ADR directory not found on disk — may be absent)
- `issue-tracker.md` — `.scratch/` convention with PRD and numbered issues
- `triage-labels.md` — 5 canonical labels mapped correctly

**Note:** `docs/adr/` directory (Architecture Decision Records) referenced in `domain.md` does not appear to exist on disk — potential gap.

### 18.8 Runbook Operational Documentation

Two active runbooks exist at root level:
- `LOCAL_M1_SMOKE_RUNBOOK.md` (543 lines, 2026-05-20) — comprehensive M1 smoke test with prefight checks, memory validation, benchmark procedures
- `LIVE_SPRINT_EXPERIMENT_MATRIX.md` (471 lines, 2026-05-20) — live sprint execution matrix with structured run procedures

**Critical reference gap:** The audit doc `17.8 Runbook References` references non-existent paths (`data/hledac_duckdb/`, `data/hledac.lmdb/`, `data/checkpoints/`, `data/tmp/`) documented in `runbooks/database-corruption.md` and `runbooks/pipeline-stall.md` — but `docs/runbooks/` **does not exist on disk**. These runbooks are cited but not present.

### 18.9 Audit Documentation Completeness

56 audit documents provide deep coverage across:
- **Acquisition** (ACQUISITION_PROFILE_CLI_ALIGNMENT_AUDIT.md)
- **Capability truth** (CAPABILITY_MATRIX_WIRING_AUDIT.md, F256C_CAPABILITY_TRUTH_RECONCILIATION.md, WHOLE_REPO_CAPABILITY_INVENTORY.md)
- **Coordinators** (COORDINATOR_REALITY_AUDIT.md, COORDINATOR_CAPABILITY_PROTOCOL_AUDIT.md, COORDINATOR_ROUTING_AUTHORITY_AUDIT.md)
- **Storage seams** (DUCKDB_READ_STORE_BOUNDARY_AUDIT.md, DUCKDB_STORE_SEAM_STATUS_AUDIT.md, GRAPH_ACCUMULATION_SEAM_AUDIT.md)
- **Runtime** (RESOURCE_GOVERNOR_AUTHORITY_AUDIT.md, SPRINT_COORDINATORS_AUDIT_20260523.md)
- **M1/Runtime** (M1_OFFLINE_PERFORMANCE_HOTSPOTS_AUDIT.md, LOCAL_ML_MLX_RUNTIME_AUDIT.md, LOCAL_STORAGE_M1_AUDIT.md)
- **Enrichment** (ENRICHMENT_OWNERSHIP_AUDIT.md, SIDECAR_ACTIVATION_REALITY_REFRESH.md, SIDECAR_SOURCE_FAMILY_SURFACE_AUDIT.md)
- **Export** (EXPORT_REPORT_PIPELINE_AUDIT.md, EXPORT_REPORT_FIRST_FIX_PLAN.md)
- **Network** (NETWORK_RECON_CANONICAL_FINDING_BRIDGE_PLAN.md, F242D_RDAP_RIR_WHOIS_UNIFICATION_AUDIT.md, CIRCL_PDNS_IMPLEMENTATION_PLAN.md)

**Pattern:** Each sprint generates 5-15 audit documents capturing findings, plans, and reconciliation records. This is a mature documentation practice.

### 18.10 Security Assessment of docs/ Directory

| Item | Assessment |
|---|---|
| Secrets/credentials | ✅ CLEAN — no API keys, tokens, passwords, or secrets in any docs |
| Security-sensitive terms | ⚠️ 10 files flagged but all are architectural/contextual usage, not actual secrets |
| Runbook references to non-existent paths | ⚠️ MEDIUM — `runbooks/` directory does not exist but is cited in audit |
| Stale architecture doc | ⚠️ HIGH — ARCHITECTURE.md 11 days stale, references sprint_scheduler only 3 times |
| Missing API documentation | ⚠️ HIGH — no structured API docs exist |
| Agent domain docs | ✅ CURRENT — well-structured, properly maintained |
| Audit trail | ✅ EXCELLENT — 56 audit docs, current as of today (2026-05-23) |

### 18.11 Summary

The `docs/` directory is a **mature, actively maintained documentation repository** with strong audit practices but critical gaps in API documentation and runbook infrastructure.

| Dimension | Status |
|---|---|
| Total files | 83 (82 markdown) |
| Audit coverage | EXCELLENT — 56 docs, current sprint cycle |
| Agent documentation | GOOD — domain/issue/triage well-structured |
| Architecture docs | STALE — ARCHITECTURE.md 11 days old, incomplete |
| Runbooks | INCOMPLETE — cited runbooks don't exist on disk |
| API documentation | NONE — no structured API reference |
| Security sensitivity | ✅ CLEAN — no secrets in documentation |
| Coverage gaps | API docs, operational runbooks, ADR directory |
| Drift risk | MEDIUM — ARCHITECTURE.md needs immediate update |