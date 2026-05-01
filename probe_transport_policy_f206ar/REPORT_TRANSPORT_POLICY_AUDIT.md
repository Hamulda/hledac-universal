# SPRINT F206AR — Transport Canonical Policy Audit Report

**Date**: 2026-05-01
**Commit**: d1d548ef
**Scope**: read-only static analysis of transport production files

---

## Executive Summary

This audit maps every HTTP client instantiation in `hledac/universal/` against the canonical transport policy infrastructure:
- `transport/circuit_breaker.py` — domain circuit breaker (TEST-SEAM ONLY)
- `transport/transport_resolver.py` — `get_transport_for_url()` + `TransportResolver.resolve()` (DORMANT)
- `network/session_runtime.py` — `async_get_aiohttp_session()` (shared PLAIN TCP surface)
- `coordinators/fetch_coordinator.py` — canonical fetch authority with its own circuit breaker

**Key finding**: `transport/circuit_breaker.py` is explicitly marked TEST-SEAM ONLY — NOT wired to any production fetch path. The production circuit breaker lives in `FetchCoordinator` as `_domain_blocked_until` + `_domain_failures`.

---

## Verdict Classification

| Code | Meaning |
|------|---------|
| CANONICAL_TRANSPORT | Uses canonical session/transport infrastructure |
| POLICY_GATED | Gated by `should_use_curl_cffi`/`should_use_httpx_h2` policy |
| CIRCUIT_BREAKER_GATED | Protected by domain circuit breaker |
| SHARED_SESSION_OK | Uses `async_get_aiohttp_session()` shared surface |
| DIRECT_SESSION_BYPASS | Creates own session pool, no circuit breaker |
| OPTIONAL_DORMANT | Not wired in production, requires env flag |
| TEST_ONLY | Only used in test fixtures |

---

## Canonical Infrastructure

### Circuit Breaker (PRODUCTION: FetchCoordinator)
- **File**: `coordinators/fetch_coordinator.py`
- **Implementation**: `_domain_blocked_until: Dict[str, float]` + `_domain_failures: Dict[str, int]`
- **Exposed via**: `FetchCoordinator.get_blocked_domains()` + `_record_domain_failure()`
- **NOT using**: `transport/circuit_breaker.py` functions

### Circuit Breaker (TEST-SEAM ONLY)
- **File**: `transport/circuit_breaker.py`
- **Functions**: `checked_aiohttp_get()`, `checked_aiohttp_post()`, `domain_breaker_check()`
- **Status**: **TEST-SEAM ONLY** — comment: "NOT wired into any production fetch path"
- **Registry**: `_BREAKERS` (shared DomainBreaker instances)

### Transport Policy Gates
| Function | File | Status |
|----------|------|--------|
| `should_use_curl_cffi()` | `transport/curl_cffi_transport.py` | ACTIVE — used in `public_fetcher` |
| `should_use_httpx_h2()` | `transport/httpx_transport.py` | OPTIONAL — gated by `HLEDAC_ENABLE_HTTPX_H2` |
| `get_transport_for_url()` | `transport/transport_resolver.py` | ACTIVE — used by `FetchCoordinator._fetch_url()` |
| `TransportResolver.resolve()` | `transport/transport_resolver.py` | **DORMANT** — not wired in production |

### Shared Sessions
| Function | File | Type |
|----------|------|------|
| `async_get_aiohttp_session()` | `network/session_runtime.py` | lazy singleton aiohttp.ClientSession |
| `async_get_httpx_client()` | `transport/httpx_client.py` | lazy singleton httpx.AsyncClient (DORMANT) |

---

## Network Consumer Map

### CANONICAL_TRANSPORT + CIRCUIT_BREAKER_GATED

**`coordinators/fetch_coordinator.py`** — `FetchCoordinator`
- `get_transport_for_url()` for onion/i2p classification
- Own `_domain_blocked_until` + `_domain_failures` circuit breaker
- Tor via `_fetch_with_tor()` + `_darknet_connector`
- I2P via `_darknet_connector.fetch_i2p()`
- Stealth via `fetch_via_curl_cffi()` (policy-gated)
- HTTPX H2 via `fetch_via_httpx_h2()` (policy-gated)

### SHARED_SESSION_OK (no circuit breaker)

**`pipeline/live_public_pipeline.py`** — `LivePublicPipeline`
- Uses `async_get_aiohttp_session()` for article text fetch (line 1651)
- Uses `async_get_aiohttp_session()` for Wayback fetch (line 1739)
- Part of live public pipeline hot path

### DIRECT_SESSION_BYPASS (no circuit breaker, own sessions)

| File | Class | Session Type | Verdict |
|------|-------|-------------|---------|
| `fetching/public_fetcher.py` | module globals | `_tor_session`, `_i2p_session` (global ProxyConnector pools) | OWN DARKNET POOL — bypasses FetchCoordinator |
| `stealth/stealth_manager.py` | `StealthManager` | `OrderedDict[profile, curl_cffi AsyncSession]` | OWN CURL POOL |
| `intelligence/blockchain_analyzer.py` | `BlockchainAnalyzer` | own `httpx.AsyncClient` | OWN HTTPX CLIENT |
| `intelligence/rir_correlator.py` | `RIRCorrelator` | ephemeral `httpx.AsyncClient` per call | EPHEMERAL HTTPX |
| `intelligence/wayback_diff_miner.py` | `WaybackDiffMiner` | own `_session: aiohttp.ClientSession` | OWN AIOHTTP |
| `security/passive_dns.py` | `PassiveDNS` | ephemeral `aiohttp.ClientSession` per call | EPHEMERAL AIOHTTP |
| `security/automation/threat-intelligence-automation.py` | `ThreatIntelligenceAutomation` | ephemeral `aiohttp.ClientSession` per call | EPHEMERAL AIOHTTP |
| `deep_research/utils.py` | `LinkRotDetector` | own `_session: aiohttp.ClientSession` | OWN AIOHTTP |
| `core/__main__.py` | module | ephemeral `aiohttp.ClientSession` at line 1215 | EPHEMERAL AIOHTTP |
| `legacy/autonomous_orchestrator.py` | `AutonomousOrchestrator` | own `_httpx_client: httpx.AsyncClient` | LEGACY HTTPX |

### OPTIONAL_DORMANT

| File | Class | Notes |
|------|-------|-------|
| `transport/tor_transport.py` | `TorTransport` | NOT used by FetchCoordinator — `_fetch_with_tor()` is used instead |
| `transport/i2p_transport.py` | `I2PTransport` | NOT used by FetchCoordinator — `_darknet_connector` is used instead |
| `transport/httpx_transport.py` | `HTTPXTransport` | Requires `HLEDAC_ENABLE_HTTPX_H2=1` |

---

## Key Findings

### F1: `transport/circuit_breaker.py` is TEST-SEAM ONLY (CRITICAL)

`checked_aiohttp_get()`, `checked_aiohttp_post()`, `domain_breaker_check()` in `transport/circuit_breaker.py` are **NOT wired to any production fetch path**. The module header explicitly states:

> "TEST-SEAM ONLY — NOT wired into any production fetch path. Production code must NOT call these; use FetchCoordinator instead."

**Impact**: The "canonical" circuit breaker infrastructure is a test seam only. The production circuit breaker is in `FetchCoordinator._domain_blocked_until`.

### F2: FetchCoordinator has its own circuit breaker (HIGH)

`FetchCoordinator` uses `_domain_blocked_until` dict + `_domain_failures` dict — a **separate implementation** from `transport/circuit_breaker.py`. These two circuit breaker systems are completely independent.

### F3: `public_fetcher` owns independent darknet session pools (HIGH)

`_tor_session` and `_i2p_session` are module-level lazy singletons in `public_fetcher.py`. `FetchCoordinator._fetch_url` uses `_fetch_with_tor()` and `_darknet_connector.fetch_onion()`. These are separate session pools — darknet traffic via `public_fetcher` does NOT go through `FetchCoordinator`.

### F4: `TransportResolver.resolve()` is DORMANT (MEDIUM)

`get_transport_for_url()` (fast dict lookup) IS used by `FetchCoordinator._fetch_url()`. But `TransportResolver.resolve()` (full lifecycle resolution) is NOT wired — requires TorTransport/I2PTransport lifecycle management preconditions.

### F5: Multiple consumers bypass circuit breaker entirely (MEDIUM)

`stealth_manager`, `blockchain_analyzer`, `rir_correlator`, `wayback_diff_miner`, `passive_dns`, `threat-intelligence-automation`, `deep_research/utils`, `core/__main__`, `legacy/autonomous_orchestrator` — all create their own sessions without any circuit breaker protection.

### F6: `live_public_pipeline` uses shared session correctly (LOW)

`pipeline/live_public_pipeline.py` uses `async_get_aiohttp_session()` for article text fetches — the canonical PLAIN TCP surface. No circuit breaker on these calls.

---

## Bypassers Requiring Review Before Patching

| File | Session Owner | Circuit Breaker | Review Action |
|------|-------------|----------------|--------------|
| `fetching/public_fetcher.py` | own Tor/I2P pools | NONE | Decide: merge into FetchCoordinator or keep separate |
| `stealth/stealth_manager.py` | own curl_cffi pool | NONE | Add `domain_breaker_check()` guard |
| `intelligence/blockchain_analyzer.py` | own httpx client | NONE | Add `domain_breaker_check()` guard |
| `intelligence/rir_correlator.py` | ephemeral httpx | NONE | Low priority (per-call ephemeral) |
| `security/passive_dns.py` | ephemeral aiohttp | NONE | Low priority (per-call ephemeral) |
| `security/automation/threat-intelligence-automation.py` | ephemeral aiohttp | NONE | Low priority |
| `intelligence/wayback_diff_miner.py` | own aiohttp | NONE | Add circuit breaker guard |

---

## Next Sprint Patching Priority

1. **HIGH**: Wire `checked_aiohttp_get()` into `FetchCoordinator.aiohttp_preview` path — makes the test-seam circuit breaker production
2. **MEDIUM**: Add `domain_breaker_check()` to `stealth_manager` curl_cffi session creation
3. **MEDIUM**: Add `domain_breaker_check()` to `blockchain_analyzer` httpx client
4. **LOW**: `live_public_pipeline` — add circuit breaker to `async_get_aiohttp_session()` calls

---

## Files Not Reviewed (OUT OF SCOPE)

- `benchmarks/` — test fixtures, not production
- `tests/` — test code only
- `probe_*/` — probe files
- `network/session_runtime.py` — shared surface definition (already reviewed)
- `transport/curl_cffi_fetch.py` — curl_cffi fetch implementation (already reviewed via transport files)
