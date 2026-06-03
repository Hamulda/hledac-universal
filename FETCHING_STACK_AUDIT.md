# FETCHING_STACK_AUDIT.md

**Date:** 2026-06-03
**Scope:** `hledac/universal/{fetching,transport,network,coordinators,discovery,dht}`
**Method:** Static read-only audit. All findings cite `file:line`.

---

## Executive Summary

The fetching stack is **mature and well-bounded** at the core path
(`public_fetcher.py` + `curl_cffi_fetch.py` + `body_limiter.py` + `session_runtime.py`)
and has clear production seams. The main gaps are **per-call session churn in
sidecar modules** (IPFS, BGP, I2P), **missing rate-limits in sidecar fetchers**,
and **TransportResolver/TransportRouter are policy candidates, not production
authority** (resolve() is DORMANT per `transport_resolver.py:120`). No bare
`except:` was found in `fetching/transport/network/`.

**Verdict:** GREEN for the canonical write path; YELLOW for sidecar fetchers;
RED for the dormant policy engine (code-as-documentation, no enforced
lifecycle).

---

## Part A — Transport Inventory

### A.1 Module Map (53 files)

| Directory | Files | Role |
|-----------|-------|------|
| `fetching/` | 2 | `public_fetcher.py` (canonical clearnet) + `alternative_protocol_fetcher.py` (IPFS/Gopher/Gemini/I2P/Fediverse/Matrix fanout) |
| `transport/` | 16 | `base`, `circuit_breaker`, `body_limiter`, `transport_router`/`resolver`, per-protocol adapters (curl_cffi/httpx/tor/i2p/gopher/nym) + 2 sibling runtimes |
| `network/` | 20 | Sidecar modules: jarm, dns_tunnel, ipv6, passive_dns, bgp, ct_log, open_storage, banner_grab, ipfs_client, i2p_client, tor_manager, gemini, session_runtime, js_bundle, favicon_hasher, passive_fingerprint, js_source_map |
| `coordinators/fetch_coordinator.py` | 1 | AIMD semaphore + Zstd + LMDB record seam (1707L) |
| `discovery/` | 21 | Provider adapters: crtsh, circl_pdns, wayback_cdx, duckduckgo, rss_atom, fediverse, matrix, dht, gopher_crawler, academic/* |
| `dht/` | 5 | Kademlia UDP node + metadata_fetcher + local_graph + sketch_exchange |

### A.2 Per-Transport Status

| Transport | Module | Async | Wire to scheduler | Circuit breaker | Timeout | Notes |
|-----------|--------|-------|-------------------|-----------------|---------|-------|
| **Clearnet HTTP (shared aiohttp)** | `network/session_runtime.py:227` async_get_aiohttp_session | yes | YES (all callers) | YES (FetchCoordinator host-penalties) | `aiohttp.ClientTimeout` (default) | TCPConnector: `limit=25, limit_per_host=8, ttl_dns_cache=300, connector_owner=True` (L247-260). Single shared session. |
| **clearnet via httpx/h2** | `transport/httpx_client.py:92` async_get_httpx_client | yes | opt-in | inherited via curl_cffi path | yes | Singleton client, HTTP/2. Status: WIRED. |
| **clearnet via curl_cffi (JA3)** | `transport/curl_cffi_runtime.py:101` `_get_or_create_session` | yes (async API) | YES (public_fetcher curl branch) | YES | yes | Profile-based session pool. **JA3 coverage** — see C.1. |
| **Tor (SOCKS5 via curl_cffi)** | `transport/tor_transport.py:356` `TorTransport.fetch` + `fetching/public_fetcher.py:861` `_get_tor_session` | yes | YES (dark pivots) | YES (record_failure propagated) | yes | NEWNYM rotation. See C.3. |
| **Tor (SOCKS5 plain aiohttp)** | `fetching/public_fetcher.py:881` `_tor_session = aiohttp.ClientSession(connector=connector)` (ProxyConnector via aiohttp_socks) | yes | YES (sprint teardown closes) | YES | yes | Separate singleton from clearnet session. **NOT curl_cffi** — JA3 = plain aiohttp TLS. |
| **I2P** | `transport/i2p_transport.py:382` I2PTransport.fetch + `fetching/public_fetcher.py:909` `_i2p_session` | yes | YES (sprint teardown) | YES | yes | Two singletons: SOCKS + HTTP. |
| **IPFS (Kubo HTTP API)** | `network/ipfs_client.py:133/341/563` + `fetching/alternative_protocol_fetcher.py:105` | yes | YES (alt protocol sidecar) | partial (max_concurrent=10) | `client_timeout` per call | **5× `aiohttp.ClientSession` created inside call bodies (L105, 169, 232, 274, 393)** — see B.5. |
| **Gopher** | `transport/gopher_transport.py:146/177` + `alternative_protocol_fetcher.py:161` | yes | YES (sidecar) | NO (semaphore only) | per-call | DIY TCP. Sem=2 (M1). See D.11. |
| **Gemini** | `network/gemini_transport.py` + `alternative_protocol_fetcher.py:196` | yes | YES (sidecar) | NO (sem=2 only) | per-call | `ssl_context.verify_mode=ssl.CERT_NONE` (L107) — see F.2. |
| **Nym (mixnet)** | `transport/nym_transport.py` | yes | DORMANT (env-gated) | — | per-call | No callers in `runtime/`. |
| **JS renderer (Camoufox/nodriver)** | `fetching/public_fetcher.py:1326/1378` `_fetch_with_camoufox/_fetch_with_nodriver` | yes | YES (post-403/429 escalation) | inherits | yes | Env-gated. Capability cache at L1202-1204. **Gated on HLEDAC_ENABLE_NODRIVER=1** plus Chrome binary check. |
| **BGP monitor** | `network/bgp_monitor.py:562` | yes | YES (advisory) | NO | yes | Fresh `aiohttp.ClientSession` per call — see B.5. |
| **Banner grab** | `network/banner_grabber.py` | yes | YES (advisory) | sem=1 only | yes | `User-Agent: curl/8.4.0` hardcoded (L1028) — see C.2. |
| **CT log** | `network/ct_log_scanner.py:54` `_CTLogScanner.get_subdomains` | yes | YES (acquisition lane) | NO | yes | Optional injected session (L58) — good. |
| **DoH (passive_dns)** | `network/passive_dns.py:132` | yes | YES (intelligence) | sem per source | yes | HTTP, multiple DoH providers (Cloudflare/Google/Quad9/AdGuard/NextDNS). |
| **Fediverse / Matrix** | `fetching/alternative_protocol_fetcher.py:273/328` | yes | YES (sidecar) | NO | yes | HTTP-only (no curl_cffi). |
| **DHT (UDP Kademlia)** | `dht/kademlia_node.py` + `metadata_fetcher.py` | yes | YES (dark pivots) | NO | yes | Real UDP. No HTTP. Out of scope for this audit. |
| **In-memory test transport** | `transport/inmemory_transport.py` | yes | test only | — | — | Safe. |

### A.3 Decorator-based cross-cutting: NONE

`@timeout` / `@retry` / `@backoff` / `@circuit_breaker` / `@rate_limit` — **zero hits** in
`fetching/transport/network/`. All cross-cutting is implemented inline in
`public_fetcher.py:435-475` (`_RETRYABLE_STATUS_CODES`, `_compute_backoff_seconds`,
`_extract_retry_after`, `_build_retry_error`) and
`transport/circuit_breaker.py` (state machine). Acceptable but not reusable.

---

## Part B — Concurrency Model

### B.1 Semaphore Inventory

| Location | Limit | Comment |
|----------|------:|---------|
| `network/session_runtime.py:247` `aiohttp.TCPConnector(limit=25, limit_per_host=get_default_limit())` | 25 / 8 | Conservative. M1-safe. |
| `coordinators/fetch_coordinator.py:347,681` `_aimd_semaphore` (AIMD) | dynamic | Adaptive per-host. The canonical concurrency authority. |
| `transport/httpx_client.py:92` | singleton client, no explicit sem | OK — bounded by `httpx` pool. |
| `network/banner_grabber.py:1719` `Semaphore(1)` | 1 | "TCP probes are heavyweight" — correct. |
| `network/i2p_client.py:271` `Semaphore(2)` | 2 | "M1 memory: max 2 concurrent" — good. |
| `transport/gopher_transport.py:463` `Semaphore(2)` | 2 | "M1 memory: max 2 concurrent" — good. |
| `network/gemini_transport.py:335` `Semaphore(2)` | 2 | "M1 memory: max 2 concurrent" — good. |
| `network/passive_fingerprint.py:78-84` `_source_rate_limiters` | 1/source | Per-source. Reasonable for stealth. |
| `network/ipfs_client.py:603` | 10 | "F230: max_concurrent" — bounded but **session-per-call**, see B.5. |
| `coordinators/research_optimizer.py:106` `_request_semaphore` | config-driven | OK. |
| `runtime/pivot_executor.py:164` | `_max_active` | OK. |
| `coordinators/agent_coordination_engine.py:151,176,274` | per AgentType | OK. |
| `coordinators/execution_coordinator.py:562` | `max_parallel` | OK. |

### B.2 AdaptiveSemaphore

- Found via grep in `utils/concurrency.py` and `runtime/` only — not in
  `fetching/` or `network/`. **AdaptiveSemaphore is NOT used in any HTTP/transport
  fetch path**. The AIMD semaphore in `fetch_coordinator.py:347` is the adaptive
  primitive, but it lives outside the transport layer and is consumed by
  `FetchCoordinator`, not by sidecar fetchers.
- Sidecar fetchers (IPFS, Gopher, Gemini, Fediverse, Matrix, banner_grab, BGP)
  use **static `asyncio.Semaphore(N)`** which does not adapt to memory pressure.
  On M1 8GB, a passive RSS pressure trigger would be safer.

### B.3 Fetch functions NOT gated by a Semaphore

| Function | Module:Line | Notes |
|----------|-------------|-------|
| `_fetch_with_camoufox` | `fetching/public_fetcher.py:1326` | Gated by capability + env. Should be a semaphore too (browser process is RAM-heavy). |
| `_fetch_with_nodriver` | `fetching/public_fetcher.py:1378` | Same. |
| `fetch_via_httpx_h2` | `transport/httpx_transport.py:360` | Bounded by httpx pool — OK. |
| `fetch_ipfs` | `network/ipfs_client.py:341` | Sem exists at L603, but the per-call fetchers at L105/169/232/274 do not check it. |
| All `network/bgp_monitor.py:562` calls | bgp | No semaphore — fresh session per call, concurrent calls could thrash. |
| `discovery/duckduckgo_adapter.py`, `circl_pdns_adapter.py`, `wayback_cdx_adapter.py` | (out of strict scope, but they all go through `session_runtime`) | OK — bounded by `async_get_aiohttp_session` connector limit. |

### B.4 Connection lifecycle — what is shared vs per-call

| Pattern | Examples | Verdict |
|---------|----------|---------|
| **Module-level singleton session** | `network/session_runtime.py:227`, `transport/httpx_client.py:92`, `transport/curl_cffi_runtime.py:101`, `fetching/public_fetcher.py:881/909` (Tor/I2P) | GOOD — pooled, kept open, closed on teardown. |
| **`async with aiohttp.ClientSession(...)` per call** | `network/ipfs_client.py:105,169,232,274,393`, `network/bgp_monitor.py:562` | **BAD on hot path** — each call: TCP handshake → TLS → close. Inexpensive per-call (~50ms TLS on M1) but at 100 IPFS CIDs/sprint = 5s wasted. |
| **Per-instance `aiohttp_socks.ProxyConnector` per `I2PTransport`** | `transport/i2p_transport.py:160,251,355,369,460` | OK because I2P is slow, and connector is reused across calls on the same instance. |

### B.5 Per-call session churn — concrete fix candidates

| File:Line | Function | Cost (per call) | Sprint |
|-----------|----------|-----------------|--------|
| `network/ipfs_client.py:105` | directory listing | ~80ms (TLS to gateway) | F230 — but the per-call session defeats the `max_concurrent=10` sem's purpose (sema unlocks before TLS). |
| `network/ipfs_client.py:169/232/274/393` | cat / pin / get / head | same | Same. |
| `network/bgp_monitor.py:562` | RIS dump | ~50ms | Cold path, low-volume — leave as-is. |
| `network/open_storage_scanner.py` (S3/GCS) | bucket probe | ~50ms | OK. |

**Fix:** add a module-level singleton in `network/ipfs_client.py` mirroring
`network/session_runtime.py`. This is a 30-line change with measurable speedup
on IPFS-rich sprints.

---

## Part C — Stealth & Anti-Detection

### C.1 JA3 / TLS fingerprint coverage

| Transport | TLS plane | JA3 spoof? | Evidence |
|-----------|-----------|------------|----------|
| Clearnet aiohttp (default) | OpenSSL 3 via Python | **NO** — static Python TLS | `network/session_runtime.py:87-88` comment explicitly forbids unifying with curl world. Plain aiohttp JA3 is **highly detectable** (CREM, ML). |
| Clearnet curl_cffi (stealth) | curl_cffi with `tls_impersonate=profile` | **YES** | `transport/curl_cffi_fetch.py:90,110,123,134,165,177,186,211,221,234`. |
| Tor via curl_cffi | curl_cffi + SOCKS5 | **YES** | `transport/curl_cffi_fetch.py:32` `fetch_via_tor_curl_cffi`. |
| Tor plain aiohttp (alt path) | OpenSSL via aiohttp_socks | **NO** | `fetching/public_fetcher.py:881` (line 881, 909). When this path is used, the exit guard sees Python TLS. |
| I2P (SOCKS) | OpenSSL via aiohttp_socks | **NO** | Same risk. |
| Nym | OpenSSL | **NO** | DORMANT anyway. |
| HTTPS probes (jarm) | ssl stdlib | N/A (server-side fingerprint) | `transport/tor_transport.py:466` "3 handshakes". |
| IPFS / DoH / BGP | OpenSSL via aiohttp | **NO** | Acceptable — these are API calls, not web. |
| Browser (Camoufox) | Firefox-bundled | **YES** (real Firefox profile) | Heavy. |
| Browser (nodriver) | Chrome-bundled | **YES** (real Chrome profile) | Heavy. |

**Stealth gap:** the **Tor-via-aiohttp** path in `public_fetcher.py:881` and
**I2P** in `:909` are **fingerprint-detectable** even though the user expects
anonymity. Recommend routing all Tor/I2P traffic through `curl_cffi_fetch`
(stealth) when JA3 is the threat model.

### C.2 Header randomization

| Surface | Implementation | Verdict |
|---------|----------------|---------|
| `fetching/public_fetcher.py:167-251` `_USER_AGENT_POOL`, `_ACCEPT_LANGUAGE_POOL`, `get_random_ua()`, `get_random_accept_language()` | Realistic browser UA + locale rotation per request | **GOOD** — covers F229. |
| `transport/httpx_transport.py:396-399` | Hardcoded Chrome 120 UA, `Accept-Language: en-US,en;q=0.9`, `Accept-Encoding: gzip, deflate, br` | **BAD** — static fingerprint. Only used when `HLEDAC_ENABLE_HTTPX_H2=1`. |
| `network/banner_grabber.py:1028` | `User-Agent: curl/8.4.0` | **ACCEPTABLE** — banner_grab intentionally identifies as curl. |
| `fetching/public_fetcher.py:1992-1994` | `stealth_session.rotate_ua()` (per-request rotation) | **GOOD** — real rotation. |
| `fetching/public_fetcher.py:228-230` | `Accept-Encoding: gzip, deflate, br` (advertises brotli) | See D.2 — **advertised but not actually decompressed**. |

Missing headers from rotation pool: `Sec-Ch-Ua`, `Sec-Fetch-Site`, `Sec-Fetch-Mode`,
`Connection: keep-alive`. Real browsers send these on every request — a server
that compares against a corpus of "headless" fingerprints will flag requests
without them.

### C.3 Tor circuit rotation

| Surface | Policy | Evidence |
|---------|--------|----------|
| `transport/tor_transport.py:16` `MAX_CIRCUIT_REQUESTS=3` | **3 requests per circuit** (per-domain counter) | Excellent. Anti-correlation. |
| `transport/tor_transport.py:323-354` `_maybe_rotate_circuit` | Per-domain counter + global fallback | GOOD. |
| `transport/tor_transport.py:297-316` `rotate_circuit` | NEWNYM via stem control port | GOOD. |
| `fetching/public_fetcher.py:917-925` `_renew_tor_circuit` | NEWNYM, sync via `loop.run_in_executor` | GOOD. |
| `network/tor_manager.py:116-135` | Stem NEWNYM, executor-wrapped | GOOD. |
| **Per-domain isolation** (`transport/tor_transport.py:103`) | `_domain_circuits: dict[str, int]` | **GOOD** (F251) — per-domain circuit isolation prevents cross-site correlation. |

**No time-based rotation** — purely request-count-based. For long-running
sprints with idle periods, a circuit may persist for minutes. Acceptable but
consider adding a wall-clock fallback (e.g. rotate after 120s idle).

### C.4 Stealth Coverage Gaps (summary)

1. **Tor/I2P aiohttp paths** (C.1) — fingerprint-detectable. Route through curl_cffi.
2. **Missing browser hint headers** (C.2) — `Sec-Ch-Ua*` and `Sec-Fetch-*` absent.
3. **httpx h2 path** (C.2) — static UA/headers.
4. **Accept-Encoding false advertising** — claims `br` but no brotli decoder wired (D.2).

---

## Part D — Performance

### D.1 Synchronous I/O in async code

`rg "requests\.|urllib\.request\.|urlopen\(" fetching/transport/network/` →
**zero hits** in production code (3 hits are docstrings/comments). ✅

### D.2 Decompression coverage

- **gzip/deflate:** aiohttp auto-decompresses by default. ✅
- **brotli:** `Accept-Encoding: gzip, deflate, br` is sent (`public_fetcher.py:230, 251`; `httpx_transport.py:399`), but **no `brotli` library import found**. If the server returns `br` and Python aiohttp has no `brotli` installed, the response comes back as **raw brotli bytes** that downstream parsers (HTML→text, regex) will choke on.
- **zstd:** `coordinators/fetch_coordinator.py` imports `ZstdCompressor` — this is for **outbound** storage, not for HTTP response decompression. So zstd-encoded HTTP responses will not be decoded.
- **Manual gzip path** in `fetching/public_fetcher.py` (search `decompress`): **none**.

**Severity:** MEDIUM. Most CDN-served HTML uses gzip, but a non-trivial fraction
of API endpoints use brotli. Recommend: (a) add `brotli` to default deps;
(b) drop `br` from Accept-Encoding if brotli is missing.

### D.3 Response size limits — GOOD

| Surface | Cap | Verdict |
|---------|-----|---------|
| `transport/base.py:69` `TransportConfig.max_bytes` | 2_000_000 (default) | OK. |
| `fetching/public_fetcher.py:262-263` `MAX_BYTES_DEFAULT=2MB`, `MAX_BYTES_HARD=10MB` | hard 10MB ceiling | **GOOD** — explicit `max_bytes > MAX_BYTES_HARD → cap` (L1561-1562). |
| `transport/curl_cffi_fetch.py:36,71,144-149` `read_body_with_cap(chunks, max_bytes)` via `transport/body_limiter.py` | `DEFAULT_MAX_BYTES` | **GOOD** — pure async helper, bytearray.extend() O(1), `del content_bytes[max_bytes:]` truncate-in-place. Excellent implementation. |
| `network/ipfs_client.py:399-402` | Reads `Content-Length` header, gates on `file_size > limit` | **GOOD**. |
| **Inline duplicate loop** | `fetching/public_fetcher.py:1679-1688, 2179-2181` | Two inline copies of the body-cap loop exist — see `TODO(F226-body-cap)` at L1679. **REFACTOR** to use `read_body_with_cap`. |

### D.4 Connection pooling

- `aiohttp.TCPConnector(limit=25, limit_per_host=8)` (B.1) — sensible for M1.
- `ttl_dns_cache=300` — good, reduces getaddrinfo cost.
- `connector_owner=True` — correct, lifecycle owned by session.
- **No HTTP/1.1 `force_close=False` toggle** — defaults to keep-alive. ✅

### D.5 Performance quick wins (priority order)

| # | Win | Effort | Win |
|---|-----|--------|-----|
| 1 | **Singleton IPFS session** in `network/ipfs_client.py` (B.5) | 30 min | -5s/sprint on IPFS-heavy runs |
| 2 | **Decompression fix** — drop `br` from Accept-Encoding OR add `brotli` dep | 15 min | Eliminates garbled-content parser failures |
| 3 | **Refactor inline body-cap loops** → `read_body_with_cap` | 30 min | -60 LOC duplication, single audit point |
| 4 | **Add `Sec-Ch-Ua` + `Sec-Fetch-*` to UA pool** | 1 hr | Better stealth under browser-fingerprint checks |
| 5 | **Singleton BGP session** | 15 min | Low impact (low volume) |
| 6 | **Semaphore on Camoufox/nodriver paths** | 15 min | Prevents browser-process OOM on M1 |

---

## Part E — Error Handling

### E.1 Bare except — CLEAN

`rg "except:" fetching/transport/network/` → **zero hits** in production code. ✅

`except Exception` count by file: `public_fetcher.py` (18), `i2p_transport.py` (3),
`i2p_client.py` (2), `passive_dns.py` (1), `bgp_monitor.py` (1), `gemini_transport.py` (1),
`banner_grabber.py` (1). All re-raise or log with context. ✅

### E.2 Status code handling — GOOD

`fetching/public_fetcher.py:436` `_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504, 520})`:
- 429 (rate limit) ✅ retried with `Retry-After` header backoff
- 502/503/504 (gateway/availability) ✅ retried
- 520 (Cloudflare "Web server returned an unknown error") ✅ retried
- 403 (forbidden) → **escalates to curl_cffi stealth path** (`L2042-2050`), then aiohttp retry. ✅
- 404 → not retried, marked as `fetch_exception:not_found`. ✅
- 5xx (general) — only 502/503/504/520. **Missing 500, 501, 505, 507**. Low impact — most production 5xx are in the covered set.

Backoff (`public_fetcher.py:454-475`):
- `Retry-After` honored, capped at 60s.
- Exponential backoff capped at 8s.
- `MAX_RETRIES = 1` — no infinite loops. ✅

### E.3 Error classification — EXCELLENT

`public_fetcher.py:647-727` maps exceptions to stable telemetry codes:
- `fetch_exception:asyncio.TimeoutError → connect_timeout`
- `fetch_exception:TimeoutError → read_timeout`
- `max_bytes_exceeded`, `body_empty`, `circuit_breaker_blocked`, etc.

This is **production-grade error classification**. ✅

### E.4 CircuitBreaker — WIRED

`transport/circuit_breaker.py:175` `record_failure(is_timeout, failure_kind)`:
- Wired into `fetch_coordinator.py` (canonical path).
- Returns `(None, "circuit_breaker_open:<reason>")` on open circuit (L405).
- Per-domain isolation.
- **Not used in sidecar fetchers** (IPFS, BGP, Fediverse, Matrix, banner_grab) —
  they will hammer a domain even if it's down.

---

## Part F — M1-Specific Optimizations

### F.1 DNS resolution — PARTIALLY FIXED

| Location | API | Verdict |
|----------|-----|---------|
| `utils/async_helpers.py:91` `async_getaddrinfo` | asyncio.to_thread + getaddrinfo | **GOOD** — used by `fetch_coordinator.py:23`. |
| `network/tor_manager.py:52` `loop.run_in_executor(... self._controller = ...)` | OK | Good. |
| `network/session_runtime.py:249` `ttl_dns_cache=300` | aiohttp-native | Good — caches for 5 min. |
| `coordinators/fetch_coordinator.py:23` `from ..utils.async_helpers import async_getaddrinfo` | Used at `_validate_fetch_target` | Good. |
| `transport/tor_transport.py:466-504` ssl JARM | OK in executor | Good. |
| `transport/transport_resolver.py:178` `async def resolve(self, context)` | **DORMANT** (L120) | OK — dormant. |

**No `socket.getaddrinfo()` direct calls in async context.** ✅

### F.2 SSL / TLS

| Location | Verify | Verdict |
|----------|--------|---------|
| `network/session_runtime.py:258` (aiohttp default) | `ssl=True` (cert verification ON by default) | ✅ |
| `transport/curl_cffi_fetch.py` (curl_cffi) | Profile-driven (default verify) | ✅ |
| `transport/tor_transport.py:487-489` JARM | `verify_mode = ssl.CERT_NONE` | **ACCEPTABLE** — JARM is reconnaissance, not trust. |
| `network/gemini_transport.py:103-107` Gemini | `ssl_context.check_hostname = False; verify_mode = ssl.CERT_NONE` | **ACCEPTABLE** — Gemini protocol over TOFU model. Document this. |
| `network/bgp_monitor.py` | aiohttp default | ✅ |
| `network/passive_dns.py:132` (DoH) | aiohttp default | ✅ |

**No `verify=False` in production HTTP paths.** ✅

### F.3 Apple-native opportunities (NOT yet used)

| Opportunity | Module candidates | Effort | Win |
|-------------|-------------------|--------|-----|
| `krb5` / `Security.framework` for cert verify (already used in macOS) | n/a | n/a | Default on M1 — no action. |
| `vm_copy` for zero-copy buffer handoff | none (current uses `bytearray`+`del` truncate) | n/a | Skip — complexity exceeds win. |
| `kqueue` event loop (default on M1) | already default | n/a | ✅ |
| `os.sendfile` for raw transfers | n/a (HTTP, not files) | n/a | Skip. |
| `mlx.core.fast.metal_kernel` for in-place body parsing | `fetching/public_fetcher.py:extract_html_metadata` | high | Out of scope. |

### F.4 Per-connection memory pressure

- `aiohttp.TCPConnector(limit=25)` on M1 8GB → max 25 concurrent TCP sockets.
  Each socket buffer ~64KB receive buffer = **~1.6MB steady-state for the
  pool alone** (idle). With 2MB response cap × 25 in flight = **~50MB**.
  Comfortable on M1.
- `MAX_BYTES_HARD=10MB` is the per-response cap. With 25 in flight = **250MB
  worst-case body buffer**. **TIGHT on M1 8GB** if other subsystems are loaded.
  Recommend reducing to 5MB or introducing a "high-water" early-truncation
  that queries `uma_budget.is_critical` and cuts max_bytes by 50%.

---

## Summary Table — Per-Transport Health

| Transport | async | shared session | cap | CB | JA3 | Status |
|-----------|:-----:|:-------------:|:---:|:--:|:---:|:------:|
| clearnet aiohttp | ✅ | ✅ | 2MB/10MB | ✅ | ❌ | 🟢 |
| clearnet httpx/h2 | ✅ | ✅ | 2MB | ✅ (curl path) | ❌ (static UA) | 🟡 |
| clearnet curl_cffi | ✅ | ✅ | 2MB/10MB | ✅ | ✅ | 🟢 |
| Tor curl_cffi | ✅ | ✅ | 2MB | ✅ | ✅ | 🟢 |
| Tor aiohttp (alt) | ✅ | ✅ | 2MB | ✅ | ❌ | 🟡 |
| I2P aiohttp | ✅ | ✅ | 2MB | ✅ | ❌ | 🟡 |
| IPFS | ✅ | ❌ (per-call) | per-call | partial | n/a | 🟡 |
| Gopher | ✅ | n/a | sem=2 | ❌ | n/a | 🟢 |
| Gemini | ✅ | per-call | sem=2 | ❌ | ❌ (cert none) | 🟡 |
| Nym | ✅ | n/a | env-gated | ❌ | ❌ | ⚪ DORMANT |
| Camoufox | ✅ | n/a | none | ❌ | ✅ | 🟡 (needs sem) |
| nodriver | ✅ | n/a | none | ❌ | ✅ | 🟡 (needs sem) |
| BGP | ✅ | ❌ (per-call) | none | ❌ | n/a | 🟡 |
| banner_grab | ✅ | n/a | sem=1 | ❌ | n/a (curl UA) | 🟢 |
| DoH | ✅ | injected | per-source | ❌ | n/a | 🟢 |
| Fediverse/Matrix | ✅ | shared | none | ❌ | n/a | 🟡 |
| CT log | ✅ | optional | none | ❌ | n/a | 🟢 |

Legend: 🟢 = wired and healthy · 🟡 = works but has gaps · 🔴 = broken · ⚪ = dormant

---

## Critical Findings (ordered)

1. **CRITICAL: Tor/I2P aiohttp path leaks Python JA3 fingerprint** —
   `fetching/public_fetcher.py:881, 909`. Anonymity goal is undermined.
   Fix: route through `curl_cffi_fetch` when JA3 is the threat model.
2. **HIGH: Missing brotli decoder** despite advertising `br` in
   `Accept-Encoding`. Fix: add `brotli` to default deps or drop `br`.
3. **HIGH: IPFS per-call session churn** — 5× fresh `aiohttp.ClientSession`
   in `network/ipfs_client.py:105,169,232,274,393`. Fix: module-level
   singleton.
4. **HIGH: Sidecar fetchers do not use CircuitBreaker** (IPFS, BGP, Fediverse,
   Matrix, banner_grab). They will hammer broken domains. Fix: add
   `get_breaker(domain)` calls in each sidecar fetch.
5. **MEDIUM: Camoufox/nodriver paths lack a semaphore** — browser processes
   are RAM-heavy. Fix: add `Semaphore(1)` to `_fetch_with_camoufox` and
   `_fetch_with_nodriver`.
6. **MEDIUM: Inline body-cap duplication** at `public_fetcher.py:1679-1688,
   2179-2181` (two copies). Fix: use `transport.body_limiter.read_body_with_cap`.
7. **MEDIUM: `MAX_BYTES_HARD=10MB × 25 in-flight` = 250MB worst case** — tight
   on M1 8GB. Fix: cap connector `limit` to 15 OR halve `MAX_BYTES_HARD` when
   `uma_budget.is_critical`.
8. **LOW: `httpx_transport.py:396` has static Chrome UA** — leaks "we are
   always Chrome 120" pattern. Fix: hook into the same UA pool as
   `public_fetcher`.
9. **LOW: `TransportResolver.resolve()` is DORMANT** (`transport_resolver.py:120`)
   — well-documented but not in the production path. No code action; this is
   a known design.
10. **LOW: `Accept-Encoding: gzip, deflate, br` advertised but not honored** —
    see D.2.
11. **LOW: 500/501/505/507 not in `_RETRYABLE_STATUS_CODES`** — low impact.

---

## M1-Specific Recommendations

1. **Add `uma_budget`-aware early truncation** in `body_limiter.py`:
   when `is_critical` → reduce `max_bytes` to 1MB; when `is_emergency` →
   abort body read entirely.
2. **Add a `brotli` (or `brotlicffi`) import to `public_fetcher.py`** and
   register a response decoder hook. aiohttp 3.9+ supports auto-decompress if
   the lib is importable.
3. **Reduce `aiohttp.TCPConnector(limit=25)` to `limit=15`** when
   `uma_budget.is_critical`. This is a 1-line patch in `session_runtime.py`.
4. **Consider `brotli` extra dep vs drop `br` from `Accept-Encoding`** —
   pick one. Currently we get the worst of both: we ask for `br` but can't
   decode it, so servers that prefer brotli (e.g. Cloudflare) deliver
   compressed bytes the parser then misreads.
5. **Per-source rate limiters** in `network/passive_fingerprint.py:78-84` is
   a good pattern. Reuse it for IPFS, BGP, and Fediverse sidecars.

---

## WIRED Summary (canonical authority)

| Seam | Authority |
|------|-----------|
| Canonical HTTP fetch | `network/session_runtime.async_get_aiohttp_session` (clearnet) + `transport/curl_cffi_runtime` (stealth) |
| Canonical body cap | `transport/body_limiter.read_body_with_cap` |
| Canonical timeout/retry | `public_fetcher._compute_backoff_seconds` + `_is_retryable_status` |
| Canonical circuit breaker | `transport/circuit_breaker.get_breaker` (used in `fetch_coordinator.py`) |
| Canonical rate limit | `utils/concurrency.adjust_fetch_workers` (AIMD) |
| Tor/I2P session | `fetching/public_fetcher._get_tor_session/_get_i2p_session` (singletons) |

All **canonical seams are healthy**. Sidecars should re-use these where
possible (especially circuit breaker and body limiter).

---

*End of audit. Compiled: 2026-06-03, from 53 files in 5 directories, all
findings cite `file:line`.*
