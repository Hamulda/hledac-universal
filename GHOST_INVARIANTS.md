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
- Method: `SprintScheduler._run_ipfs_discovery_sidecar()` → `sidecar_orchestrator._run_ipfs_discovery_sidecar()`
- Entry: `run_advisory_runner()` step 5 (non-blocking, via create_task)
- Fail-soft: all errors return empty list, never crash sprint
- Bounds: max 20 CIDs per search, 30s timeout per fetch
- Returns: CanonicalFinding list via `ipfs_search_as_findings()`
- Provenance: (cid, gateway, query)

### F229: BGP Enrichment Sidecar
- Env gate: `HLEDAC_ENABLE_BGP` (default: disabled)
- Method: `SprintScheduler._run_bgp_enrichment_sidecar()` → `sidecar_orchestrator._run_bgp_enrichment_sidecar()`
- Entry: `run_advisory_runner()` step 6 (non-blocking, via create_task)
- Fail-soft: all errors return empty list, never crash sprint
- Bounds: max 10 prefixes, 60s duration, max 1000 events
- Returns: CanonicalFinding list via `monitor_bgp_as_findings()`
- Prefix extraction: CIDR notation regex + bare IP
- Provenance: (prefix, as_path, event_type)

### F229: Banner Grab Sidecar
- Env gate: `HLEDAC_ENABLE_BANNER_GRAB` (default: disabled)
- Method: `SprintScheduler._run_banner_grab_sidecar()` → `sidecar_orchestrator._run_banner_grab_sidecar()`
- Entry: `run_advisory_runner()` step 7 (non-blocking, via create_task)
- Fail-soft: all errors return empty list, never crash sprint
- Bounds: max 20 IPs, top 10 ports per IP, 100 results cap
- Returns: CanonicalFinding list via `grab_batch_as_findings()`
- Target extraction: IP regex, common ports [21,22,23,25,53,80,110,143,443,465,587,993,995,3306,3389,5432,6379,8080,8443]
- Provenance: (ip, port, protocol)
