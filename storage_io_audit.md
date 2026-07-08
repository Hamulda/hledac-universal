# Storage & IO Subsystem Audit Report

**Scope:** `knowledge/`, `fetching/`, `transport/`, `coordinators/`,
`runtime/sidecar_*`, `core/rust_backend*`. Python only, no tests/audits.
**Date:** 2026-07-08. **CRITICAL** = CLAUDE.md top-10 invariant violation.

---

## CRITICAL INVARIANT VIOLATIONS

### #1 [CRITICAL #6] LMDB per-item `env.begin(write=True)`
**Where:** `knowledge/sprint_seeds_store.py:134` `with env.begin(write=True)
as txn: txn.put(key, val)`; `knowledge/wal.py:484-487` eviction opens one
write txn for N deletes (uses single txn, OK) but bypasses the canonical
`lmdb_subdb.putmulti_bounded` (`knowledge/lmdb_subdb.py:278`);
`knowledge/duckdb_store.py:857,897,10372` single-key query-cache writes.
**Root cause:** Per-txn writes instead of batched `putmulti_bounded`.
**Why bad:** N lock acquisitions per batch where one batched call would
suffice; the canonical helper exists for a reason.
**Fix:** Route multi-row writes through `lmdb_subdb.putmulti_bounded`.

### #2 [DO-NOT #7] `bytes()` on LMDB data
**Where:** `knowledge/ioc_dedup_adapter.py:568` `txn.put(...,
bytes(state_bytes))`.
**Root cause:** Rust returns `bytes`/`memoryview`; forced re-encoding.
**Why bad:** May allocate; defeats zero-copy handoff.
**Fix:** Pass `state_bytes` directly (assert `bytes`/`memoryview`).

---

## HTTP FETCHING

### #3 `aiohttp_socks` residual after httpx-socks migration
**Where:** `fetching/_session_mgr.py:234,276` (fallback paths in
`get_tor_session`/`get_i2p_session`); `transport/connection_pool_manager.py
:46,86,121,214,248,256` (entire module); `transport/i2p_client.py:109-112,
228-230,252`; `transport/i2p_transport.py:33-36` (top-level import);
`transport/httpx_transport.py:342` (comment).
**Root cause:** CLAUDE.md says httpx-socks replaces aiohttp-socks, yet
priority-3 fallback still constructs `aiohttp_socks.ProxyConnector` when
curl_cffi is unavailable.
**Why bad:** Two SOCKS libs → two connection pools per process on M1 8GB;
subtle DNS/CONNECT divergence.
**Fix:** Replace priority-3 fallback with `httpx-socks AsyncProxyTransport`
(already wired in `i2p_transport.py:157`, `tor_transport.py:209`). Remove
top-level `import aiohttp_socks` from `i2p_transport.py:33`.

### #4 `bytes()` zero-copy violation in curl_cffi result
**Where:** `transport/curl_cffi_fetch.py:682` `"content": bytes(
content_bytes)`.
**Root cause:** Unneeded wrap; `content_bytes` is already `bytes` from
`body_limiter.read_body_with_cap`.
**Why bad:** ~5 MB avoidable alloc per request on M1 8GB.
**Fix:** `"content": content_bytes` (direct).

---

## CIRCUIT BREAKERS

### #5 TransportRouter omits circuit-breaker state
**Where:** `transport/transport_router.py:286-293` default-lane return has
no breaker field; breaker lives in `fetching/public_fetcher.py:2980`.
**Root cause:** Decision-engine and breaker are decoupled.
**Why bad:** New callers using `route_transport()` directly skip the breaker.
**Fix:** Add `circuit_breaker_state: CircuitBreakerSnapshot | None` on
`TransportDecision`, populated from `circuit_breaker.get_breaker(host)`.

---

## VERIFIED-INVARIANT BOUNDS (no defects)

### HTTP/3 lane — `transport/http3_lane.py` ✓
All M1 8GB bounds met: `_H3_CACHE_MAX` via `M1_BOUNDS().http3_lru_max:72`,
`_H3_CONCURRENCY_MAX:73`, `_H3_TIMEOUT_S=8.0:74`, `_H3_CACHE_TTL_S:76` 24h
sliding, `_H3_RSS_BLOCK_GIB` blocks at 5.5 GiB:77. LRU eviction via
`OrderedDict.popitem(last=False):290`. Alt-Svc parser `_altsvc_advertises_h3
:298-331` correct (handles dict/CIMultiDict/Headers, lowercases, accepts
`h3=`, `h3 "`, `h3="`). psutil lazy:85-107. Speculative probe bounded by
`_MAX_PROBE_TASKS=16:144`. `extract_host` is `@lru_cache(2048):334`.
**Minor:** `_probe_semaphore._value == 0` race pre-check:543 (bounded by
`_MAX_PROBE_TASKS`); `QuicConfiguration(is_client=True):684` no
`verify_mode`/`alpn_protocols` pins.

### Conditional cache — `transport/conditional_cache.py` ✓
`_MAX_ENTRIES=5000`, `_MAX_BODY=2 MB`, `_MIN_BODY=256` enforced:83-86.
Migrated LMDB→diskcache (SQLite) with in-memory `OrderedDict` fallback
(`_Backend._init_diskcache:273-309`). zstd→zlib→uncompressed chain
(`_compress:123-152`), fail-soft. sha256 integrity check on read:497-513.
**#6 (stale docs):** Lines 4, 179, 622-624 still say "LMDB" although
actual storage is diskcache/SQLite.

### Prewarm pool — `transport/prewarm_pool.py` ✓
4-slot ring buffer via `(_next_slot_var+1) % _POOL_SIZE:464`. Per-host
circuit-breaker for probes (threshold 3, reset 30s):80-82. Staleness TTL
60s default. 5 CDN probe hosts:62-68. `fill_all_slots` uses
`safe_gather_ok:564`.

### Sidecar orchestration — `runtime/sidecar_orchestrator.py` ✓
**Truly parallel** via `asyncio.TaskGroup` (PEP 654, F314-3) in two stages
(`:365-450`). `_ADVISORY_SIDECAR_SEMAPHORE_LIMIT=2:79` wraps every sidecar
(`_run_bounded_sidecar:102-119`). `_PLUGIN_SIDECAR_SEMAPHORE_LIMIT=4:85`.
Fan-out covers BGP, Wayback, CommonCrawl, IPFS, onion, I2P, BGP-enrich,
banner, DHT, Gopher, digital_ghost, stego, TI-feed — all parallel.
**#7 (minor):** Line 573 cap `result[:50]` per plugin silently drops
overflow; add `dropped_count` telemetry.

### DuckDB canonical writes (INVARIANT #5) ✓
All findings funnel through `DuckDBShadowStore.async_ingest_findings_batch`
(`knowledge/duckdb_store.py:6807+`, `safe_gather_return_exceptions:6838`).
`conn.execute(... "INSERT ...")` calls at `:1408,1422,573-578,2173-2197`
are all schema/PRAGMA setup, not finding writes. No rogue `INSERT INTO`
in `ioc_dedup_adapter.py`, `sprint_seeds_store.py`, `research_memory.py`
or other knowledge modules — they all call into the ingest path.

### RotatingBloomFilter (INVARIANT #7) ✓
Only `knowledge/dedup.py:180 class RotatingBloomFilter`. No
`ScalableBloomFilter` or `Set[str]` URL dedup found in `transport/`,
`fetching/`, `knowledge/`, or `coordinators/`.

### Fetch parallelism — `coordinators/fetch_coordinator.py` ✓
`safe_gather_ok(*[self._fetch_url(url) for url in urls_to_fetch]):1659`
— parallel fetch. `safe_gather_ok(...ddgs, news, wayback, urlscan...):2213`
parallel SERP fan-out. Pre-dedup sequential (`for url in unique_batch:
...:1631`) is CPU-light filter — fine.

### Fail-soft invariants ✓
No bare `except:` in touched files. `http3_lane.fetch_http3_aioquic`
returns `None:727-730`. `curl_cffi_fetch.fetch_via_curl_cffi` returns
error dict:740+; never raises. `prewarm_pool.acquire_session` returns
(False, None, reason):540. `conditional_cache.lookup` → None:519; `store`
→ False:575. `transport_router` is pure function, no I/O.

---

## RACE CONDITIONS

**Mild** `prewarm_pool.py:543` check-then-act on `_probe_semaphore._value`
— bounded by `_MAX_PROBE_TASKS=16`.

**Mild** `curl_cffi_fetch.py:254` deque.remove(host) is O(n); bounded by
`_MAX_HOST_SESSIONS`.

No data-corruption races detected.

---

## TOP FIXES TO LAND (priority order)

1. `transport/curl_cffi_fetch.py:682` — drop `bytes()` wrap (zero-copy).
2. `knowledge/ioc_dedup_adapter.py:568` — drop `bytes()` wrap on LMDB put.
3. `knowledge/sprint_seeds_store.py:134` — route through
   `lmdb_subdb.putmulti_bounded`.
4. `fetching/_session_mgr.py:234,276` — replace aiohttp_socks fallback
   with httpx-socks; remove top-level `import aiohttp_socks` in
   `transport/i2p_transport.py:33`.
5. `transport/conditional_cache.py:4,179,622-624` — rewrite stale
   "LMDB" docstrings (now diskcache/SQLite).
6. `transport/transport_router.py:286` — add circuit-breaker state to
   default-lane `TransportDecision`.
