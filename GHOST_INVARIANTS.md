# Ghost Invariants — Async Hygiene & Runtime Contracts

This document records the mandatory runtime invariants enforced across the Hledac universal OSINT orchestrator.

---

## Async Hygiene Rules

### `asyncio.gather` always uses `return_exceptions=True`
All `asyncio.gather()` calls MUST pass `return_exceptions=True` to prevent one failed task from cancelling sibling tasks.

```python
# WRONG
results = await asyncio.gather(task1(), task2())

# CORRECT
results = await asyncio.gather(task1(), task2(), return_exceptions=True)
```

### `_check_gathered` is called after every `gather`
After every `asyncio.gather(return_exceptions=True)` call, the results MUST be passed through `_check_gathered()` from `network.session_runtime` to partition ok results from errors.

**Canonical contract** (`network.session_runtime._check_gathered`):
- Returns `Tuple[List[Any], List[Any]]` — `(ok_results, error_results)`
- `asyncio.CancelledError` is **re-raised** immediately (never swallowed)
- Other `BaseException` (`KeyboardInterrupt`, `SystemExit`) is **re-raised** immediately
- Regular `Exception` items are appended to `error_results`

**Legacy variant** (`utils.async_helpers._check_gathered`): returns `List[Any]` only (exceptions logged at debug level, not re-raised). Used by `network_reconnaissance.py`. Do not use in new canonical-path code.

### `async_getaddrinfo` is used instead of `socket.getaddrinfo`
DNS resolution in async contexts MUST use `async_getaddrinfo()` from `utils.async_helpers`, which wraps `loop.getaddrinfo`. Never use blocking `socket.getaddrinfo` in async code.

### `time.monotonic` for all interval measurements
All time deltas and interval measurements MUST use `time.monotonic()`. Never use `time.time()` for measuring elapsed durations.

### bare except is forbidden
All `except:` clauses MUST catch a specific exception type (`except Exception:` or `except SpecificError:`). Bare `except:` silently catches `SystemExit`, `KeyboardInterrupt`, and `GeneratorExit`.

### `asyncio.to_thread` is forbidden for DNS / CoreML / DuckDB
`asyncio.to_thread()` MUST NOT be used for blocking I/O that spans the async event loop — specifically DNS resolution, CoreML inference, and DuckDB operations. Use dedicated thread pools or `async_getaddrinfo`.

---

## Sprint 7A: Runtime Primitives & Lifecycle Seams

### `PersistentActorExecutor` uses `call_soon_threadsafe`
`PersistentActorExecutor` (from `utils.thread_pools`) communicates with worker threads via `call_soon_threadsafe()` on the running event loop, NOT `loop.call_soon()`.

### `SprintContext` uses `msgspec.structs.replace()`
Phase transitions in `SprintContext` MUST use `msgspec.structs.replace()` to create new frozen struct instances. Direct attribute mutation on a frozen struct raises `AttributeError`.

### `TokenBucket` supports Gaussian jitter
`TokenBucket` (from `utils.rate_limiters`) accepts a `jitter_sigma` parameter. When set, wait times are sampled from `N(0, sigma)` to decorrelate request bursts.

### `maybe_resume()` reads LMDB keys
`maybe_resume()` (from `utils.sprint_lifecycle`) reads sprint phase from LMDB using keys:
- `b"sprint:last_phase"` — last active phase
- `b"sprint:current_id"` — current sprint identifier

### Teardown order is LIFO
Sprint teardown is performed in LIFO (reverse) order relative to startup to respect dependency ordering. The lifecycle manager guarantees teardown hooks run in reverse registration order.

---

## Sprint F206L: M1 Async Crash Vectors

### `asyncio.run()` in ThreadPoolExecutor is a crash vector on M1
Running `asyncio.run()` inside a `ThreadPoolExecutor` worker on M1 causes nested event loop crashes. The correct pattern is `loop.run_until_complete()`.

**Sites fixed (F196A):**
- `tool_registry.py:478` — uses `loop.run_in_executor(pool, lambda: loop.run_until_complete(coro))`
- `document_intelligence.py:1325` — uses `loop.run_in_executor(pool, lambda: loop.run_until_complete(...))`
- `unicode_analyzer.py:711` — direct `asyncio.run()` (no pool wrapper, safe)
- `execution_optimizer.py:407` — direct `asyncio.run()` (no pool wrapper, safe)
- `brain/inference_engine.py:445` — uses `loop.run_until_complete(coro)`
- `graph_rag.py:436` — already safe (`loop.run_until_complete`)

### `mx.eval([])` before `mx.metal.clear_cache()`
Before clearing MLX Metal cache, always drain the GPU queue with `mx.eval([])`. Without this barrier, `clear_cache()` is a no-op. Order: `gc.collect() → mx.eval([]) → mx.metal.clear_cache()`.

---

## Sprint 8T: MLX Metal Memory Limits (M1 8GB UMA)

### Metal cache limit is 2.5 GiB
`mx.metal.set_cache_limit(2_684_354_560)` is set at MLX init via `init_mlx_buffers()` in `utils.mlx_cache`. This prevents Metal from consuming the entire unified memory bus.

### Metal wired limit is 2.5 GiB
`mx.metal.set_wired_limit(2_684_354_560)` is set alongside the cache limit. Wired memory cannot be paged out by the OS.

### Cleanup order: GC → eval barrier → clear_cache
The canonical MLX cleanup sequence (via `mlx_cleanup_sync()`):
1. `gc.collect()` — release Python refs to MLX objects
2. `mx.eval([])` — GPU queue drain barrier
3. `mx.metal.clear_cache()` — Metal cache release

---

## Sprint 8VC: Fetch Coordinator Contracts

### Timeout matrix is consumed by name
Fetch timeout constants (`TIMEOUT_CLEARNET_API`, `TIMEOUT_CLEARNET_HTML`, `TIMEOUT_TOR`, `TIMEOUT_I2P`) are referenced by name in `FetchCoordinator`, not hardcoded.

### AIMD concurrency uses `AIMD_*` constants
Adaptive increase/decrease uses `AIMD_ADDITIVE_INCREMENT`, `AIMD_DECREASE_FACTOR`, `AIMD_MIN_CONCURRENCY`, `AIMD_MAX_CONCURRENCY`, `AIMD_SUCCESS_THRESHOLD`.

### `_resolve_host_ips` is synchronous
`FetchCoordinator._resolve_host_ips()` is a blocking synchronous method that delegates to `asyncio.to_thread` internally for DNS lookups. The async variant is `_resolve_host_ips_async()`.

---

## Sprint F193A+: DuckDB Shadow Store

### `async_ingest_findings_batch` is the only canonical write path
All findings written to persistent storage flow through `DuckDBShadowStore.async_ingest_findings_batch()` in `knowledge/duckdb_store.py`. No module writes findings directly to DuckDB outside this seam.

---

*Last updated: Sprint F206L (2026-04-30) — verified all invariants still current*

---

## Sprint F206K: HTTPX/H2 Transport Capability Layer

### HTTPX H2 auto-disable after 3 failures
`transport/httpx_transport.py` maintains per-process failure state:
- `_httpx_h2_auto_disabled` — set True after 3 failures, permanent for process lifetime
- `_httpx_h2_failure_count` — increments on each httpx_h2 failure, resets on auto-disable
- `record_httpx_h2_failure()` — called by `public_fetcher.py` on httpx_h2 exceptions
- `classify_httpx_h2_error()` — maps httpx exceptions to error types (NOT for CancelledError)

### HTTPX H2 never used for Tor/I2P/Freenet/JS/stealth
`should_use_httpx_h2()` returns `(False, reason)` immediately for:
- `.onion`, `.i2p`, `.b32.i2p`, `.freenet` URLs
- `use_stealth=True`
- `use_js=True`

### CancelledError is NOT classified — must be re-raised
`classify_httpx_h2_error()` raises `asyncio.CancelledError` immediately. Callers must handle CancelledError propagation separately.

### HTTPX H2 env gate: `HLEDAC_ENABLE_HTTPX_H2`
HTTPX H2 lane is gated by `HLEDAC_ENABLE_HTTPX_H2` env var (default: disabled). When env is not "1"/"true"/"yes"/"on", `should_use_httpx_h2()` returns `(False, "httpx_h2_disabled_env")`.

### Fallback is one-shot per URL
When httpx_h2 fails and h2 is not installed, `_httpx_reason="httpx_h2_fallback"` is set and aiohttp path is taken. No infinite loop — the fallback is not retried via httpx_h2.

### F229: IPFS Discovery Sidecar
- Env gate: `HLEDAC_ENABLE_IPFS` (default: disabled)
- Method: `sidecar_orchestrator._run_ipfs_discovery_sidecar()` → `SprintScheduler._run_ipfs_enrichment_sidecar()` (no args; fetches findings from self._result)
- Entry: `run_advisory_runner()` step 5 (non-blocking, via create_task)
- Fail-soft: all errors return empty list, never crash sprint
- Bounds: max 20 CIDs per search, 25s timeout per fetch (F234 spec)
- Returns: CanonicalFinding list via `fetch_findings_from_cids()`
- Provenance: (cid, gateway, query)

### F229: BGP Enrichment Sidecar
- Env gate: `HLEDAC_ENABLE_BGP` (default: disabled)
- Method: `sidecar_orchestrator._run_bgp_enrichment_sidecar()` → `SprintScheduler._run_bgp_advisory_sidecar()`
- Entry: `run_advisory_runner()` step 6 (non-blocking, via create_task)
- Fail-soft: all errors return empty list, never crash sprint
- Bounds: max 3 IP/ASN seeds per sprint, 30s timeout per query, Semaphore(1)
- Returns: CanonicalFinding list via `BGPAdapter.enrich_org()`
- Provenance: (prefix, as_path, event_type)
- INVARIANT: BGP sidecar requires HLEDAC_ENABLE_BGP=1, max 10 AS lookups per sprint

### F229: Banner Grab Sidecar
- Env gate: `HLEDAC_ENABLE_BANNER_GRAB` (default: disabled)
- Method: `SprintScheduler._run_banner_grab_sidecar()` → `sidecar_orchestrator._run_banner_grab_sidecar()`
- Entry: `run_advisory_runner()` step 7 (non-blocking, via create_task)
- Fail-soft: all errors return empty list, never crash sprint
- Bounds: max 3 IPs per sprint, max 5 ports per IP, 100 results cap
- Returns: CanonicalFinding list via `banner_grab_to_canonical()`
- Target extraction: IP regex, default ports [22,80,443,8080,8443], configurable via HLEDAC_BANNER_GRAB_PORTS
- Provenance: (ip, port, protocol)
- INVARIANT: Banner grab = CLEARNET ONLY (active TCP probe), gated by HLEDAC_ENABLE_BANNER_GRAB=1

### F235: External Intelligence API Invariants
External intelligence APIs (Shodan, Censys, GreyNoise) provide high-value unindexed data
that Google does not crawl. All integration follows these invariants:

#### Capability Gating
- All three lanes gated by env vars + API key presence:
  - `HLEDAC_ENABLE_SHODAN` + `SHODAN_API_KEY`
  - `HLEDAC_ENABLE_CENSYS` + `CENSYS_API_ID` + `CENSYS_SECRET`
  - `HLEDAC_ENABLE_GREYNOISE` + `GREYNOISE_API_KEY`
- If capability not enabled or API key absent → lane returns [] silently
- Never block sprint if API key missing — fail-soft is mandatory

#### API Key Protection
- API keys must NEVER appear in logs, payload_text, or SprintExporter output
- Keys read from env vars at query time, not stored in state
- Keys never included in CanonicalFinding provenance tuples

#### Rate Limiting
- Rate limit via TokenBucket from `utils.rate_limiters.py`
- Shodan free tier: 1 req/sec → bucket "shodan_api"
- Censys free tier: 0.4 req/sec → bucket "censys_api"
- GreyNoise free tier: ~1 req/sec → bucket "greynoise_api"
- No blocking sleep() — always via bucket.acquire()

#### HTTP Transport
- Use aiohttp directly for these specialized TI sources (not FetchCoordinator)
- Each lane creates its own scoped ClientSession with 30s timeout
- Fail-soft: any error (timeout, 429, 5xx) → return [] with warning log

#### Confidence Scoring
- CanonicalFinding confidence = 0.9 for verified external source
- Banner richness / tag count can push to 0.92
- Never mark external intel findings below 0.85 confidence

#### Acquisition Lane Wiring
- SHODAN: enabled when query has IP/CIDR indicator (`ctx.has_ip`)
- CENSYS: enabled when query has domain indicator (`ctx.has_domain`)
- GREYNOISE: enabled when query has IP/CIDR indicator (`ctx.has_ip`)
- Both run via `_run_shodan_lane`, `_run_censys_lane`, `_run_greynoise_lane`
  in `run_enabled_acquisition_lanes()` inner closure

---

## Sprint 2026-05-27 — Stability & Memory Hardening

### LMDB bulk write via cursor.putmulti()
Canonical write path in `duckdb_store.py` uses `cursor.putmulti()` for batch LMDB writes
(~15–30× faster than per-item `env.begin(write=True)` loops).
Always use `put_many()` on a cursor, never per-item write in a loop.

### adjust_fetch_workers atomicity
`utils/concurrency.adjust_fetch_workers()` must update BOTH `_FETCH_SEMAPHORE` and
`_clearnet_semaphore` atomically. Split-brain updates cause unbounded concurrency divergence.
Both semaphores are adjusted together via the same call.

### IPFS sidecar gate
IPFS discovery sidecar is gated by `HLEDAC_ENABLE_IPFS` (default: disabled).
See Sprint F229 section above for full invariant text.

---

## Sprint F214Q: Cover Traffic OPSEC Noise

Cover traffic is probabilistic inline injection (not background task — too complex for M1).

#### Invariant: Cover traffic NESMÍ go to storage pipeline
Cover traffic is OPSEC noise, not data. Fire-and-forget only. Never ingested into DuckDB.

#### Transport matching
Cover traffic MUST use identical transport as real request:
- Tor URL → Tor cover traffic
- Clearnet URL → Clearnet cover traffic
- .onion → Tor, .i2p → I2P

#### Rate & limits
- `HLEDAC_COVER_TRAFFIC_RATE=0.15` (15% chance after each successful real fetch)
- Max 2 cover traffic fires per sprint (M1 RAM protection)
- Short random delay (0.5–3s) to desynchronize cover from real request

#### Implementation
- `FetchCoordinator._maybe_fire_cover_traffic(transport)` — probabilistic check
- `FetchCoordinator._fire_cover_traffic(url, delay, transport)` — fire-and-forget
- Uses `curl_cffi.AsyncSession` with JA3 fingerprint (same as real requests)
- `MetricsRegistry.inc("cover_traffic_fired")` for observability
- Fail-soft: all errors are silent (cover traffic failures are expected behavior)

---

## Sprint F214K: Dark Surface Pivots

Dark surface pivot advisory: generate onion/IPFS/DHT/I2P pivot queries from IOC findings.

### Transport gate (CRITICAL - zero clearnet leakage)
- Dark pivots MUST use Tor and/or I2P transport - NEVER clearnet aiohttp
- `generate_dark_surface_queries()` gate: `if not (tor_available or i2p_available): return`
- `SprintScheduler` detects availability via `self._tor_transport.available` and `self._i2p_transport.available`
  (NOT class-existence check - TorTransport/I2PTransport classes always exist)
- Gate: `HLEDAC_ENABLE_DARK_PIVOTS=1` env var + `accepted_findings >= 5`

### Query bounds
- Max `MAX_DARK_QUERIES_PER_SPRINT = 3` dark pivot queries per sprint (from `hypothesis_engine.py`)
- Query content: NEVER log at INFO/WARNING level - only DEBUG level with `[REDACTED]` redaction
- Query sources: onion addresses, IPFS CIDs, paste sites, I2P destinations from IOC clusters

### Telemetry
- `SprintSchedulerResult.dark_surface_pivots_attempted` = len(dark_queries) after generation
- `SprintSchedulerResult.dark_surface_pivots_accepted` = len(items) after lane planning
- Both fields updated at end of `_run_dark_surface_pivot_advisory()`

### Skip conditions (all fail-soft return, no exceptions)
- `HLEDAC_ENABLE_DARK_PIVOTS != "1"` -> return
- `accepted_findings < 5` -> return
- No dark transport available (tor + i2p both False) -> return, 0 pivots logged
- No findings available for query generation -> return
- No dark queries generated -> return
---

## Sidecar Memory Gates (F234)

### IPFS sidecar — skip tier
IPFS discovery sidecar skips when governor uma_state in (critical, emergency).
See Sprint F229 section above for full invariant text.

### BGP sidecar — skip tier
BGP enrichment sidecar skips when governor uma_state in (critical, emergency).
See Sprint F234 section above for full invariant text.

### Dark surface pivot — skip tier
Dark surface pivot advisory skips when governor uma_state in (critical, emergency).
See Sprint F214K section above for full invariant text.

### Všechny sidecary — hard timeout
All sidecar operations use asyncio.wait_for with hard timeout <= 25s.
Never exceed 25s per sidecar call — sidecar timeouts must not block sprint lifecycle.

### M1ResourceGovernor.sidecar_admission() (Sprint F214K / F234)
All heavy sidecar ops (IPFS, BGP, dark pivots, external intel) call
M1ResourceGovernor.sidecar_admission() before executing.
The governor returns (admitted: bool, reason: str) — if not admitted, sidecar skips silently.
See: network_intelligence.py GHOST_INVARIANTS comment.

---

## Sprint F234: BGP Enrichment Sidecar

BGP enrichment maps IP → ASN → owner → geoloc → netblocks → threat intel correlation.

### IP Extraction (F234)
- `extract_public_ips_from_text(text)` in `network/bgp_monitor.py`
- Filters RFC1918 (10.x, 172.16-31.x, 192.168.x), loopback (127.x), link-local (fe80::, ::1)
- **INVARIANT**: Private IPs are NEVER sent to BGP enrichment

### Gate & bounds
- Gate: `HLEDAC_ENABLE_BGP=1` env var
- RAM guard: skip if governor `uma_state` in (`critical`, `emergency`)
- Max 20 IPs per sprint (dedup + cap), was 3 before F234
- Per-IP timeout: 30s via `asyncio.wait_for`

### Telemetry
- `SprintSchedulerResult.bgp_sidecar_ips_found`: IPs extracted before BGP lookup
- `SprintSchedulerResult.bgp_sidecar_findings_returned`: findings returned from BGP
- `SprintSchedulerResult.bgp_enrichment_findings_ingested`: findings written to DuckDB

### Fail-soft
- `bgp_enrich_to_canonical()` returns `[]` on any error
- `_run_bgp_enrichment_sidecar()` returns `[]` on exception
- No exceptions propagate to sprint lifecycle

---

## Sprint F220K: SOFT_WARN Memory Tier

M1 8GB UMA threshold ladder (see also `uma_budget.py` M1_FETCH_SOFT_CEILING_GB):
- 5.5 GiB → soft ceiling (fetch concurrency hard-cap via resource_allocator)
- 5.8 GiB → SOFT_WARN (reduce concurrency 50%, proactive signal)
- 6.0 GiB → WARN (reduce concurrency 75%)
- 6.5 GiB → CRITICAL (stop new fetches)
- 7.0 GiB → EMERGENCY (flush + GC)

### SOFT_WARN state
- `UMA_STATE_SOFT_WARN = "soft_warn"` in `core/resource_governor.py`
- `evaluate_uma_state()` returns `"soft_warn"` at >= 5.8 GiB
- `should_enter_io_only_mode()` enters io_only at SOFT_WARN when swap detected
- `memory_high_water_mb` default lowered from 6000 → 5632 (5.5 GiB)

### Sidecar skip at SOFT_WARN
IPFS, BGP, dark pivots skip at CRITICAL/EMERGENCY only (unchanged — SOFT_WARN does not block sidecars).

---

## Sprint F220K: tor_transport.py blocking I/O fix

`transport/tor_transport.py` `async def start()` contained blocking `open(hostname_file)` calls
that could deadlock the async event loop. All file I/O in async methods must use
`asyncio.to_thread()` or `run_in_executor()`.

Fixed: `open(hostname_file)` → `await asyncio.to_thread(lambda: open(hostname_file))`

