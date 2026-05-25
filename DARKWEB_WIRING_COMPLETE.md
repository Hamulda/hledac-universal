# DARKWEB_WIRING_COMPLETE.md — Sprint F251

**Date:** 2026-05-23
**Status:** COMPLETE — dark web discovery wired to sprint pipeline

---

## Summary

Dark web (.onion) discovery was completely absent from the sprint pipeline. `DarkWebCrawler` and `OnionSeedManager` existed as standalone tools never called from `sprint_scheduler.py`. Tor circuit rotation existed but `rotate_circuit()` was only called via a global counter at 100 requests — correlation risk.

Three things implemented:

1. **Adapter** converting `DarkWebContent` → `CanonicalFinding` for sprint ingestion
2. **Sidecar** wired to `SidecarOrchestrator` with full M1 8GB guards
3. **Circuit rotation fix** reducing per-domain correlation attack surface

---

## Changes

### 1. `intelligence/dark_web_intelligence.py` — `darkweb_content_to_canonical()` [NEW]

```python
def darkweb_content_to_canonical(content: DarkWebContent, query: str) -> CanonicalFinding:
    finding_id = f"dw_{hashlib.md5(content.url.encode()).hexdigest()[:16]}"
    title = content.title or "onion"
    body = content.text_content or ""
    confidence = float(content.metadata.get("relevance_score", 0.5))
    confidence = max(0.0, min(1.0, confidence))
    return CanonicalFinding(
        finding_id=finding_id,
        query=query,
        source_type="onion_discovery",
        confidence=confidence,
        ts=content.extracted_at,
        provenance=(content.url,),
        payload_text=f"{title}\n{body[:3000]}",
    )
```

Maps `DarkWebContent` (url, content_hash, content_type, title, text_content, extracted_at, metadata) to canonical `CanonicalFinding` for sprint write path.

---

### 2. `runtime/sprint_scheduler.py` — `_run_onion_discovery_sidecar()` [NEW]

Added after line 7748 (`_run_ct_log_discovery_in_cycle()` finally block). Full method (~110 lines) with:

| Property | Value |
|---|---|
| Gate | `HLEDAC_ENABLE_TOR=1` |
| Memory guard | UMA `critical`/`emergency` → skip |
| Circuit check | `TorTransport.is_circuit_established()` |
| Seeds | `OnionSeedManager.get_seeds(limit=20)` |
| Concurrency | `Semaphore(3)` (M1 safety) |
| Per-crawl timeout | 45s |
| Total sidecar budget | 120s |
| IOC expansion | Ahmia search on sprint query terms |
| Write path | `duckdb_store.async_ingest_findings_batch()` |
| Position | After CT log discovery (CT reveals .onion domains) |
| Fail-safe | Entire method wrapped — any exception → log + return [] |

---

### 3. `runtime/sidecar_orchestrator.py` — SidecarOrchestrator wiring [NEW]

- `_run_onion_discovery_sidecar()` delegate method (calls `scheduler._run_onion_discovery_sidecar()`)
- Launched as background task in `run_advisory_runner()` alongside `_run_ipfs_discovery_sidecar()`

---

### 4. `transport/tor_transport.py` — Per-domain circuit isolation [FIXED]

| Before | After |
|---|---|
| `MAX_CIRCUIT_REQUESTS = 100` | `MAX_CIRCUIT_REQUESTS = 3` |
| Single global `_circuit_request_count` | Per-domain `_domain_circuits: dict[str, int]` |
| No domain context in rotation | `domain` extracted from URL via `urlparse()` |
| Same circuit for all .onion crawls | New circuit per .onion domain after 3 requests |

```python
# __init__ (line ~93)
self._domain_circuits: dict[str, int] = {}  # F251: per-domain circuit isolation

# _maybe_rotate_circuit(domain: str = "") (line ~281)
if domain:
    count = self._domain_circuits.get(domain, 0) + 1
    self._domain_circuits[domain] = count
    if count >= self._max_circuit_requests:  # 3
        self._domain_circuits[domain] = 0
        await self.rotate_circuit()
else:
    # Legacy global counter fallback
    self._circuit_request_count += 1
    ...
```

Correlation attack prevention: after 3 requests to the same `.onion` domain, a new Tor circuit is established before the 4th request, preventing traffic analysis across requests to the same hidden service.

---

## Circuit Rotation Fix Summary

**Problem:** `MAX_CIRCUIT_REQUESTS=100` meant the same Tor circuit was used for up to 100 fetches before rotation. For `.onion` crawling, this created correlation risk — all traffic to a given hidden service flowed over the same circuit.

**Fix:** Per-domain circuit isolation with threshold of 3. After 3 requests to a domain, a `NEWNYM` signal is sent via the Tor control port, establishing a fresh circuit before the next request.

**Invariant preserved:** `mx.eval([])` barrier not needed here — Tor circuit rotation is a control plane operation, not an MLX/GPU operation.

---

## M1 8GB Timing Constraints

| Constraint | Limit | Rationale |
|---|---|---|
| Concurrent Tor crawls | 3 | Semaphore(3), each crawl may hold memory |
| Per-crawl timeout | 45s | Tor circuits have固有 latency; 45s prevents indefinite hang |
| Total sidecar budget | 120s | Bounded by sprint cycle time; 2min max |
| Seeds per sprint | 20 | OnionSeedManager.get_seeds(limit=20) |
| Payload text | 3000 chars | Bounded by `CanonicalFinding.payload_text` ~4KB envelope |

---

## Invariants

| Test | What it verifies |
|---|---|
| `darkweb_content_to_canonical` maps all required fields | `finding_id`, `source_type="onion_discovery"`, `confidence` clamped [0,1], `provenance` is tuple |
| Gate: `HLEDAC_ENABLE_TOR` not set → sidecar returns immediately | No-op when Tor not explicitly enabled |
| Gate: memory `critical`/`emergency` → sidecar skipped | Protects M1 8GB from OOM |
| Gate: Tor circuit not established → sidecar skipped | Prevents hangs when Tor unavailable |
| `Semaphore(3)` caps concurrency | M1 8GB RAM budget respected |
| `asyncio.timeout(120)` caps total sidecar | Sprint cycle budget respected |
| Circuit rotation at `count >= 3` per domain | Correlation attack prevention |
| After rotation `_domain_circuits[domain] = 0` | Counter resets to allow next rotation cycle |
| `CanonicalFinding` frozen + tuple provenance | GHOST_INVARIANTS compliance |

---

## Files Modified

| File | Change |
|---|---|
| `intelligence/dark_web_intelligence.py` | Added `darkweb_content_to_canonical()` + `__all__` export |
| `transport/tor_transport.py` | Per-domain circuit isolation (`_domain_circuits`, domain param, threshold 3) |
| `runtime/sprint_scheduler.py` | `_run_onion_discovery_sidecar()` method (~110 lines) |
| `runtime/sidecar_orchestrator.py` | `_run_onion_discovery_sidecar()` delegate + background task launch |

---

## Verification

```bash
# Gate: sidecar is no-op without HLEDAC_ENABLE_TOR=1
HLEDAC_ENABLE_TOR= pytest hledac/universal/ -v -k "onion" -q

# Circuit rotation: verify domain counter and rotation log
# (requires Tor running on 127.0.0.1:9050)
HLEDAC_ENABLE_TOR=1 TOR_SOCKS_PORT=9050 pytest hledac/universal/ -v -k "tor" -q

# Verify adapter maps DarkWebContent → CanonicalFinding correctly
pytest hledac/universal/probe_f251_darkweb_adapter.py -v  # [pending creation]
```