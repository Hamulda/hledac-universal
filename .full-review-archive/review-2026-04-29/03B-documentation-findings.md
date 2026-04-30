# Documentation Completeness and Accuracy Review
**Review Date**: 2026-04-29
**Review Scope**: hledac/universal/
**Reviewer**: Documentation Architecture Review

---

## Executive Summary

Documentation is partially accurate but has **critical gaps** in several areas:

1. **httpx_transport integration (F206L)** - NOT documented in REAL_ARCHITECTURE.md
2. **asyncio.run M1 crash vectors** - Partially documented in GHOST_INVARIANTS.md but scattered
3. **SprintScheduler 15+ injected dependencies** - Only 5 inject_* methods documented, 10+ missing
4. **DuckDB/LanceDB/Kuzu boundary** - Accurately documented in STORAGE_LAYER_DOCUMENTATION.md
5. **Lightpanda browser pool** - NOT documented anywhere

---

## Severity: Critical

### Finding D-01: httpx_transport Integration Not Documented

**Severity**: Critical
**File**: `transport/httpx_transport.py` (13855 chars, Sprint F206K/F206L)
**Documentation Gap**: `REAL_ARCHITECTURE.md` has **ZERO references** to httpx, httpx_transport, or HTTPX H2 lane routing.

**What exists**:
- `transport/httpx_transport.py` - 391 lines with full URL classification and HTTPX H2 lane routing
- `transport/httpx_client.py` - HTTPX async client wrapper
- `tests/probe_transport_cap_2026/test_httpx_transport.py` - Transport tests

**What is missing from documentation**:
- REAL_ARCHITECTURE.md has no mention of HTTPX H2 transport layer
- No documentation of transport capability routing truth table
- No documentation of fail-soft fallback behavior when h2 not installed
- No SSRF validation for redirect URLs in httpx path
- No ADR for why HTTPX H2 is an optional lane vs default

**Recommendation**:
Add to REAL_ARCHITECTURE.md under F206K section:
```
## F206K — HTTPX H2 Transport Lane (2026-04-29)

HTTPX H2 is an OPTIONAL clearnet capability lane for API-like and same-host batch URLs.

TRANSPORT ROUTING TRUTH TABLE:
  URL Type                    | Lane      | Transport
  ---------------------------+-----------+------------------
  random clearnet HTML       | aiohttp   | TCPConnector
  same-host/API clearnet     | httpx_h2  | HTTP/2
  .onion                     | aiohttp   | ProxyConnector
  .i2p / .b32.i2p           | aiohttp   | ProxyConnector
  use_js=True                | aiohttp   | TCPConnector
  use_stealth=True           | aiohttp   | StealthSession
```

**Code snippet** (`transport/httpx_transport.py:149-217`):
```python
def should_use_httpx_h2(
    url: str,
    use_stealth: bool = False,
    use_js: bool = False,
) -> tuple[bool, str]:
    """
    Determine if URL should use HTTPX H2 lane.
    
    HTTPX H2 is only selected when ALL of:
      1. URL is clearnet (not .onion/.i2p/.b32.i2p/.freenet)
      2. use_stealth is False (stealth uses aiohttp/StealthSession)
      3. use_js is False (JS rendering uses Camoufox/nodriver)
      4. URL is API-like OR same-host pattern detected
    """
```

---

### Finding D-02: SprintScheduler Inject Dependencies Incompletely Documented

**Severity**: Critical
**File**: `runtime/sprint_scheduler.py` (~260K chars)
**Documentation Gap**: Only 5 `inject_*` methods documented; SprintScheduler has 15+ injected dependencies in `__init__`.

**Actual inject methods found** (lines 4397-4430):
1. `inject_ioc_graph(self, ioc_graph: Any) -> None` (line 4397)
2. `inject_policy_manager(self, policy_manager: Any) -> None` (line 4401)
3. `inject_prefetch_oracle(self, oracle: Any) -> None` (line 4405)
4. `inject_pivot_planner(self, planner: Any) -> None` (line 4415)
5. `inject_analyst_workbench(self, workbench: Any) -> None` (line 4426)

**Missing from documentation** (dependencies set in `__init__` that have corresponding setters):
- `_duckdb_store` - set at line 667, used for canonical finding persistence
- `_forensics_enricher` - set at line 669
- `_multimodal_enricher` - set at line 672
- `_ct_log_client` - set at line 701
- `_identity_adapter` - set at line 705
- `_exposure_adapter` - set at line 707
- `_leak_sentinel_adapter` - set at line 709
- `_evidence_triage_adapter` - set at line 711
- `_diff_engine` - set at line 713
- `_pivot_planner` - set at line 715
- `_sidecar_bus` - set at line 718
- `_target_memory_service` - set at line 720
- `_analyst_brief` - set at line 722
- `_governor` - set at line 654 (M1 resource governor)
- `_hermes_engine` - set at line 648
- `_prefetch_oracle` - set at line 683

**Recommendation**:
Add comprehensive documentation of ALL injected dependencies to REAL_ARCHITECTURE.md:

```markdown
## SprintScheduler Dependency Injection

SprintScheduler uses constructor injection for most dependencies (passed via `__init__`),
and setter injection for optional/lazy dependencies via `inject_*` methods.

### Constructor Dependencies
| Dependency | Type | Purpose |
|------------|------|---------|
| config | SprintSchedulerConfig | Sprint configuration |
| ct_log_client | Any | CT log canonical discovery client |
| _duckdb_store | Any | Canonical finding persistence |
| _forensics_enricher | Any | Forensics enrichment layer |
| _multimodal_enricher | Any | Multimodal enrichment layer |
| _governor | Any | M1 resource governor advisory |

### Setter Injection (inject_* methods)
| Method | Type | Purpose |
|--------|------|---------|
| inject_ioc_graph() | IOCGraph | Pivot IOC graph reference |
| inject_policy_manager() | SprintPolicyManager | RL adaptive policy layer |
| inject_prefetch_oracle() | PrefetchOracle | Advisory prefetch suggestions |
| inject_pivot_planner() | PivotPlanner | Hypothesis-driven pivot planning |
| inject_analyst_workbench() | AnalystWorkbench | Sprint brief generation |
```

---

## Severity: High

### Finding D-03: asyncio.run M1 Crash Vectors - Incomplete Documentation

**Severity**: High
**Files**: Multiple
**Documentation Gap**: GHOST_INVARIANTS.md documents `asyncio.run()` prohibition but REAL_ARCHITECTURE.md has only **1 mention** of `asyncio.run`. The specific crash vectors (nested asyncio.run in ThreadPoolExecutor) are not comprehensively documented.

**What GHOST_INVARIANTS.md says**:
- "asyncio.to_thread is forbidden for DNS / CoreML / DuckDB"
- No explicit "nested asyncio.run() in thread contexts crashes M1"

**What is NOT documented**:
- The specific pattern that crashes M1: `asyncio.run()` called from within a ThreadPoolExecutor worker thread
- The known safe pattern: `loop.run_until_complete()` within existing loop
- List of all known safe/unsafe asyncio.run() sites

**Evidence** (global_scheduler.py:149):
```python
# Use get_event_loop() instead of asyncio.run() to avoid creating
# a new event loop on every call, which is expensive and can cause
# issues on M1 when the process shares memory with the main event loo
```

**Recommendation**:
Add to GHOST_INVARIANTS.md:

```markdown
### Nested asyncio.run() in ThreadPoolExecutor is FORBIDDEN (M1 crash vector)

Calling `asyncio.run()` from within a ThreadPoolExecutor worker thread
creates a NEW event loop in that thread, which can crash M1's shared
memory architecture.

SAFE pattern:
  loop = asyncio.get_event_loop()
  loop.run_until_complete(coro())

UNSAFE pattern (M1 crash vector):
  asyncio.run(coro())  # NEVER do this in a worker thread
```

---

### Finding D-04: Lightpanda Browser Pool Lifecycle Not Documented

**Severity**: High
**Documentation Gap**: `REAL_ARCHITECTURE.md` and `known_issues.md` have **ZERO references** to Lightpanda or browser pool lifecycle.

**What exists in code**:
- `fetch_coordinator.py` has `_fetch_with_lightpanda()` method (line 742)
- Tests reference `lightpanda` and `pyppeteer`

**Recommendation**:
Document browser pool lifecycle in REAL_ARCHITECTURE.md or create dedicated BROWSER_POOL_DOCUMENTATION.md.

---

### Finding D-05: DuckDB Canonical Write Path - Storage Layer Accurate but Incomplete

**Severity**: High
**File**: `STORAGE_LAYER_DOCUMENTATION.md`
**Status**: ACCURATE - `async_ingest_findings_batch` is correctly documented as canonical write path.

**Verification** (duckdb_store.py:4926):
```python
async def async_ingest_findings_batch(
    self,
    findings: list[CanonicalFinding],
) -> list[FindingQualityDecision | ActivationResult]:
    """
    Sprint 8W: Quality-gated batch ingest.
    Layer ABOVE async_record_canonical_findings_batch — applies quality gate to each
    finding, then delegates acceptable ones to legacy batch storage.
    """
```

**Recommendation**:
STORAGE_LAYER_DOCUMENTATION.md is accurate. No changes needed.

---

## Severity: Medium

### Finding D-06: GHOST_INVARIANTS.md - Missing Update Timestamp for Recent Sprints

**Severity**: Medium
**File**: `GHOST_INVARIANTS.md`
**Issue**: Last updated line says "Sprint F205J (2026-04-29)" but does not reflect F206K (HTTPX H2 lane) invariants.

**Recommendation**:
Update GHOST_INVARIANTS.md footer:
```
*Last updated: Sprint F206K (2026-04-29) — verified all invariants still current*
```

Add new invariant section for HTTPX H2:
```markdown
## Sprint F206K: HTTPX H2 Transport Lane

### H2 Lane URL Classification
HTTPX H2 lane is selected ONLY for API-like URLs or same-host batch candidates:
- URL is clearnet (not .onion/.i2p/.freenet)
- use_stealth is False
- use_js is False
- URL matches _is_api_like_url() OR same-host pattern

### H2 Fallback is Fail-Soft
If HTTPX H2 selected but h2 not installed → fall back to aiohttp
```

---

### Finding D-07: known_issues.md Partially Stale

**Severity**: Medium
**File**: `known_issues.md` (1545 chars)
**Issue**: Last updated date unclear; pastebin_monitor HTTP seam violation marked as "Deferred to F198x" but no F198x sprint reference.

**Content verified**:
- AdaptiveSemaphore smoke failures - still accurate
- Stub test files - still accurate
- Pastebin Monitor HTTP seam violation - still present (line 25 TODO)
- DuckDB import resolution - cosmetic, still accurate

**Recommendation**:
Add date header and F198x sprint link:
```markdown
# Known Issues
**Last Updated**: 2026-04-29
**Status**: Partial - some issues from F196A-F205J not yet reflected
```

---

### Finding D-08: CLAUDE.md Missing httpx_transport Reference

**Severity**: Medium
**File**: `CLAUDE.md`
**Issue**: CLAUDE.md does not mention httpx_transport in architecture section.

**Recommendation**:
Add to Architecture section:
```markdown
### Transport Layer
- `transport/httpx_transport.py` - HTTPX H2 lane routing (optional, F206K)
- `transport/curl_cffi_transport.py` - curl_cffi JA3 fingerprint spoofing
- `fetch_coordinator.py` - HTTP transport coordinator
```

---

## Severity: Low

### Finding D-09: REAL_ARCHITECTURE.md - No Architecture Diagram

**Severity**: Low
**File**: `REAL_ARCHITECTURE.md` (172K chars)
**Issue**: No mermaid/ASCII diagram of system components.

**Recommendation**:
Add ASCII architecture diagram at top of REAL_ARCHITECTURE.md:
```
┌─────────────────────────────────────────────────────────┐
│                    Hledac Universal                     │
├─────────────────────────────────────────────────────────┤
│ Entry: __main__.py::run_sprint()                        │
│   └→ SprintScheduler (runtime/sprint_scheduler.py)     │
│       ├→ DuckDBShadowStore (knowledge/duckdb_store.py)   │
│       ├→ FetchCoordinator (coordinators/fetch_*.py)     │
│       │   ├→ httpx_transport (transport/httpx_*.py)    │
│       │   └→ curl_cffi_transport (transport/curl_*.py) │
│       ├→ IOCGraph (knowledge/ioc_graph.py)               │
│       └→ LanceDB stores (knowledge/lancedb_*.py)         │
└─────────────────────────────────────────────────────────┘
```

---

### Finding D-10: __main__.py Entry Point Documentation Adequate

**Severity**: Low
**File**: `__main__.py`
**Status**: Module docstring is clear and accurate.

**Verified**:
```python
"""
Hledac Universal - Async Entry Point
====================================

Sprint 8AI: Boot Hygiene Closure
- AsyncExitStack as unified teardown backbone
- 8AG LMDB boot guard as FIRST boot step
- LIFO teardown order for existing surfaces
- Signal-safe teardown (no direct cleanup in signal handler)
- Graceful task cancellation before loop close

Usage:
    python -m hledac.universal [--benchmark]
"""
```

**Recommendation**: No changes needed.

---

## Verified Accurate Documentation

### STORAGE_LAYER_DOCUMENTATION.md - ACCURATE
- DuckDB canonical write path: `async_ingest_findings_batch` ✓
- Storage ownership matrix: 7 active + 3 deprecated ✓
- ADR-001: DuckDB/LanceDB write boundary documented ✓

### GHOST_INVARIANTS.md - MOSTLY ACCURATE
- asyncio.gather with return_exceptions=True ✓
- _check_gathered contract ✓
- async_getaddrinfo usage ✓
- time.monotonic ✓
- bare except forbidden ✓
- asyncio.to_thread prohibition ✓
- MLX cleanup sequence (GC→eval→clear_cache) ✓

### autonomous_orchestrator.py Facade Documentation - ADEQUATE
- Clear docstring explaining facade/non-canonical status ✓
- Authority chain documented ✓
- Migration blocker noted ✓

---

## Summary Table

| Finding | Severity | Status | File(s) |
|---------|----------|--------|---------|
| D-01 httpx_transport missing | Critical | NOT DOCUMENTED | REAL_ARCHITECTURE.md |
| D-02 SprintScheduler deps incomplete | Critical | PARTIAL | REAL_ARCHITECTURE.md |
| D-03 asyncio.run M1 vectors | High | PARTIAL | GHOST_INVARIANTS.md |
| D-04 Lightpanda lifecycle | High | NOT DOCUMENTED | - |
| D-05 DuckDB canonical write | High | ACCURATE ✓ | STORAGE_LAYER |
| D-06 GHOST_INVARIANTS stale | Medium | NEEDS UPDATE | GHOST_INVARIANTS.md |
| D-07 known_issues.md stale | Medium | PARTIAL | known_issues.md |
| D-08 CLAUDE.md httpx | Medium | MISSING | CLAUDE.md |
| D-09 No architecture diagram | Low | WISH LIST | REAL_ARCHITECTURE.md |
| D-10 __main__.py entry | Low | ADEQUATE ✓ | __main__.py |

---

## Recommended Priority Actions

1. **P0 (Critical)**: Document httpx_transport integration in REAL_ARCHITECTURE.md (F206K section)
2. **P0 (Critical)**: Document all SprintScheduler injected dependencies
3. **P1 (High)**: Document asyncio.run M1 crash vector pattern in GHOST_INVARIANTS.md
4. **P1 (High)**: Document Lightpanda browser pool lifecycle
5. **P2 (Medium)**: Update GHOST_INVARIANTS.md timestamp and add F206K invariants
6. **P2 (Medium)**: Update known_issues.md with current sprint references
7. **P3 (Low)**: Add architecture diagram to REAL_ARCHITECTURE.md
