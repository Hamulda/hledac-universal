# TRANSPORT_CONTEXT_COMPLETE.md
**Date:** 2026-05-24
**Sprint:** MISSING_MODULES_FIX_A follow-up
**Task:** Resolve two unresolved categories + verify transport singleton matrix

---

## PART 1 — transport/transport_context.py

### Finding: Module Already Exists

`transport/transport_context.py` does **not** need to be created as a separate file.
`TransportContext` is defined in `transport/transport_resolver.py` (line 69-74) and
re-exported via `transport/__init__.py`:

```python
from .transport_resolver import TransportResolver, TransportContext
```

The test file `tests/test_sprint64_transport_resolver.py` imports it as:
```python
from hledac.universal.transport import TransportContext
```

This works correctly — `transport/__init__.py` re-exports `TransportContext`.

### TransportContext Dataclass (from transport_router.py)
```python
@dataclass
class TransportContext:
    """Runtime context for transport selection."""
    requires_anonymity: bool = False
    risk_level: str = "medium"   # "low", "medium", "high"
    allow_inmemory: bool = False  # Only for testing/internal bus
```

### Conclusion
No work needed — TransportContext is correctly wired.

---

## PART 2 — hledac.core.* Path Resolution

### Finding: Dual-Layer Architecture + Shim Pattern

The codebase has **two separate `hledac` packages** at the filesystem level:

| Path | Role |
|------|------|
| `hledac/universal/` | Main application package (pip-installed) |
| `hledac/` (parent of universal) | Sibling project at same directory level |

Universal code imports `from hledac.core.X` — these resolve to
`/Users/vojtechhamada/PycharmProjects/Hledac/hledac/core/X.py` via Python path
(`sys.path` includes universal's parent directory).

### hledac.core.* Module Inventory

| Module | Status | Location | Used By |
|--------|--------|----------|---------|
| `hledac.core.resilience` | ✓ EXISTS + shim | `hledac/core/resilience.py` + `_shims/core_resilience.py` | `performance_coordinator.py` (lazy, try/except) |
| `hledac.core.http` | ✓ EXISTS + shim | `hledac/core/http.py` + `_shims/core_http.py` | `blockchain_analyzer.py` (lazy, try/except) |
| `hledac.core.mlx_embeddings` | ✓ SHIM CREATED | `_shims/core_mlx_embeddings.py` proxies to `universal/core/mlx_embeddings.py` | All context_optimization files, lancedb_store, deduplication |
| `hledac.core.watchdog` | ✓ STUB | `_shims/core_watchdog.py` | `monitoring_coordinator.py` (lazy, try/except) |
| `hledac.core.unified_ai_orchestrator` | ✓ STUB | `_shims/core_unified_ai_orchestrator.py` | `research_coordinator.py` (lazy, try/except) |

### Shim Created: `_shims/core_mlx_embeddings.py`

This shim proxies `MLXEmbeddingManager` and `get_embedding_manager` from
`universal/core/mlx_embeddings.py` using the same `importlib.util.spec_from_file_location`
pattern as `core_resilience.py`. It is needed because:
- `hledac/core/` (sibling) has no `mlx_embeddings.py`
- Universal's `core/mlx_embeddings.py` self-imports `from hledac.core.mlx_embeddings`
- The shim breaks the cycle by injecting `hledac.core.mlx_embeddings` into `sys.modules`
  before the self-import resolves.

### Shim Pattern (from `_shims/core_resilience.py`)
```python
spec = importlib.util.spec_from_file_location("hledac.core.resilience", _RESILIENCE_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules["hledac.core.resilience"] = mod
spec.loader.exec_module(mod)
```

---

## PART 3 — Transport Singleton Matrix

### Clearnet Curl_cffi — ✓ LRU Cache (JA3 Fingerprint Reuse)
- Module: `transport/curl_cffi_runtime.py`
- Pattern: `_curl_cffi_sessions: Dict[str, Any] = {}` with `_MAX_CURL_CFFI_PROFILES = 3`
- `async_get_curl_cffi_session(profile: str)` — lazy creation with O(1) LRU eviction
- Bounded to 3 profiles — prevents per-request JA3 fingerprint reconstruction
- **Purpose**: expensive TLS handshake cost avoided

### Tor Transport — ✓ Singleton
- Module: `transport/tor_transport.py`
- `_TOR_TRANSPORT_SINGLETON` + `get_tor_transport_singleton()` / `set_tor_transport_singleton()`
- F214Q finding: max 1 STEM Controller per process

### I2P Transport — ✓ Session Singleton
- Module: `transport/i2p_transport.py`  
- Internal `async def get_i2p_session()` with module-level `_i2p_session` singleton
- Exported: `I2PTransport`, `get_i2p_session()`, `close_i2p_session()`
- `set_i2p_transport_singleton()` not exported (internal only)

### Nym Transport — ✓ Singleton
- Module: `transport/nym_transport.py`
- `_NYM_TRANSPORT_SINGLETON` + `set_nym_transport_singleton()`
- Exported via module-level singleton

### Conclusion
All transport layers have appropriate session reuse. Clearnet uses bounded LRU cache
(multi-profile JA3 support); Tor/I2P/Nym use module-level singletons.

---

## Verification

### Import Health: 2630/2630 OK ✓

```
Results: 2630/2630 files OK
Report written to IMPORT_HEALTH_REPORT.json
```

Zero `hledac.core.*` import errors. All compile errors from prior session
(bgp_monitor.py, banner_grabber.py `ret` → `return` syntax) were already fixed
in this session.

### TransportContext: Importable ✓
```python
from transport import TransportContext
ctx = TransportContext(requires_anonymity=True, risk_level='high', allow_inmemory=False)
# → TransportContext(requires_anonymity=True, risk_level='high', allow_inmemory=False)
```

### All Shims Compile ✓
```python
# All 10 hledac.core.* callers compile without errors
performance_coordinator.py, research_coordinator.py, monitoring_coordinator.py,
context_compressor.py, context_cache.py, dynamic_context_manager.py,
core/mlx_embeddings.py, blockchain_analyzer.py, deduplication.py, lancedb_store.py
```

### Transport Layer: All Components Importable ✓
```python
transport.TransportContext ✓
transport.base.should_use_curl_cffi ✓
transport.base.fetch_via_curl_cffi ✓
transport.tor_transport.get_tor_transport_singleton ✓
```

---

## GHOST_INVARIANTS Compliance

| Invariant | Status |
|-----------|--------|
| Fail-safe shims | ✓ `raise NotImplementedError` for stubs, proxy pattern for mlx |
| Zero prod call-sites | ✓ All callers use lazy imports with try/except |
| Bounded re-exports | ✓ Only `MLXEmbeddingManager`, `get_embedding_manager` from mlx shim |
| gather return_exceptions | N/A — no gather used in shims |
| mx.eval([]) before clear_cache | N/A |

---

## Summary of Changes Made

| File | Action |
|------|--------|
| `_shims/core_mlx_embeddings.py` | **Created** — proxies to `universal/core/mlx_embeddings.py`, breaks self-import cycle |
| `network/bgp_monitor.py` | **Fixed** — `ret` → `return` (syntax error from prior session) |
| `network/banner_grabber.py` | **Fixed** — `ret` → `return` (syntax error from prior session) |
| `runtime/sprint_scheduler.py` | **Verified OK** — was already using `return`, verify_imports fixed |
| `_shims/core_watchdog.py` | Already existed — stub for missing `hledac.core.watchdog` |
| `_shims/core_unified_ai_orchestrator.py` | Already existed — stub for missing `hledac.core.unified_ai_orchestrator` |
| `_shims/core_http.py` | Already existed — working shim for `hledac.core.http` |
| `_shims/core_resilience.py` | Already existed — working shim for `hledac.core.resilience` |
| `transport/__init__.py` | Already correct — re-exports `TransportContext` |
| `transport/curl_cffi_runtime.py` | Already correct — LRU session cache |
| `transport/tor_transport.py` | Already correct — singleton |
| `transport/i2p_transport.py` | Already correct — session singleton |
| `transport/nym_transport.py` | Already correct — singleton |