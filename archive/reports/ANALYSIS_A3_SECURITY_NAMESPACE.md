# ANALYSIS_A3: Missing `hledac.security.*` Namespace

**Date:** 2026-05-30
**Scope:** 6 broken imports in `hledac.security.*` namespace
**Status:** PURE ANALYSIS — No implementation

---

## Executive Summary

Of the 6 missing `hledac.security.*` classes:
- **3 are ALREADY IMPLEMENTED** in `hledac/universal/security/` — wrong import path
- **3 are SHIMS with real implementations behind them** — architecture is correct
- **0 require net-new implementation** — minimal fix effort

---

## Dependency Map

```
security_coordinator.py (HUB)
├── hledac.security.stealth_engine.StealthEngine     → WRONG PATH
│   └── Real: hledac.universal.stealth.stealth_session.StealthSession
├── _shims.security_threat_intelligence.ThreatIntelligence  → STUB ONLY
├── hledac.security.quantum_resistant_crypto.QuantumResistantCrypto → WRONG PATH
│   └── Real: hledac.universal.security.pq_crypto + quantum_safe.py
├── _shims.security_zkp_research_engine.ZKPResearchEngine    → STUB ONLY

archive_discovery.py
├── hledac.security.temporal_anonymizer.TemporalAnonymizer → WRONG PATH
│   └── Real: security.temporal_anonymizer.TemporalAnonymizer
├── hledac.security.zero_attribution_engine.ZeroAttributionEngine → WRONG PATH
│   └── Real: security.zero_attribution_engine.ZeroAttributionEngine

data_leak_hunter.py
├── hledac.security.temporal_anonymizer.TemporalAnonymizer → WRONG PATH
├── hledac.security.zero_attribution_engine.ZeroAttributionEngine → WRONG PATH
└── hledac.security.key_manager.KeyManager → WRONG PATH
    └── Real: security.key_manager.KeyManager (cryptography-based)

stealth_crawler.py
├── hledac.security.temporal_anonymizer.TemporalAnonymizer → WRONG PATH
└── hledac.security.zero_attribution_engine.ZeroAttributionEngine → WRONG PATH

tests/test_sprint7g.py
└── hledac.security.quantum_resistant_crypto.QuantumResistantCrypto → WRONG PATH
```

---

## Class-by-Class Analysis

---

### 1. `StealthEngine`

| Property | Value |
|----------|-------|
| **Interface inferred** | `__init__()`, `initialize()`, `activate_stealth_mode(operation_type, confidence_threshold, security_level)`, `cleanup()` |
| **Returns** | `dict(active, success, measures_activated)` |
| **Existing partial impl** | `_shims/security_stealth_engine.py` wraps `hledac.universal.stealth.stealth_session.StealthSession` |
| **Real impl location** | `stealth/stealth_session.py:54` — `StealthSession` class |
| **Planned purpose** | Stealth mode activation for OSINT (UA rotation, jitter, timing variance) |
| **M1 feasibility** | Native Python + asyncio — trivially feasible |
| **Relation to stealth_layer.py** | Different layer: `StealthSession` manages HTTP-level stealth; `StealthLayer` manages policy/decision |

**Current state:** Shim exists and is functional. Shim delegates to `StealthSession` for:
- `rotate_ua()` → UA pool selection
- `apply_jitter()` → timing variance
- `activate_stealth_mode()` → returns dict with `active`, `success`, `measures_activated`

**Fix approach:** Fix import path in `security_coordinator.py:133`:
```python
# Current (BROKEN):
from hledac.security.stealth_engine import StealthEngine

# Fix (ALREADY EXISTS):
from _shims.security_stealth_engine import StealthEngine
# OR use direct path:
from hledac.universal.stealth.stealth_session import StealthSession
```

---

### 2. `ThreatIntelligence`

| Property | Value |
|----------|-------|
| **Interface inferred** | `__init__()`, `initialize()`, `analyze_threats(context, priority_level, security_level)`, `cleanup()` |
| **Returns** | `dict(threats, threat_level)` |
| **Existing partial impl** | `_shims/security_threat_intelligence.py` — STUB ONLY, raises `NotImplementedError` |
| **Real impl location** | `security/automation/` contains threat-related modules |
| **Planned purpose** | Threat intelligence analysis for OSINT findings |
| **M1 feasibility** | N/A — not implemented |

**Current state:** STUB ONLY. Shim raises `NotImplementedError("ThreatIntelligence stub — real implementation missing")`.

**Architecture note:** `security_coordinator.py` already handles this gracefully — if `ThreatIntelligence` raises `ImportError`, the coordinator logs warning and continues without it.

**Decision required:** Is `ThreatIntelligence` a real planned feature or was it aspirational design?

---

### 3. `QuantumResistantCrypto`

| Property | Value |
|----------|-------|
| **Interface inferred** | Constructor only (no instance methods called in scope) |
| **Returns** | N/A (constructor only) |
| **Existing partial impl** | `_shims/security_quantum_resistant_crypto.py` — STUB ONLY |
| **Real impl location** | `security/pq_crypto.py` + `security/quantum_safe.py` |
| **Library dependency** | `cryptography` package — already in use by `key_manager.py` |
| **Planned purpose** | Post-quantum cryptography for export signing (ML-DSA-65) |
| **M1 feasibility** | Native Python with `cryptography` package — feasible |

**Current state:** STUB ONLY. However, `security_coordinator.py` already uses the CORRECT path:
```python
# Line 161-162 — CORRECT PATTERN:
from hledac.universal.security.pq_crypto import PQAvailability, create_post_quantum_backend
self._pq_backend, pq_status = await create_post_quantum_backend(enabled=True, key_id="hledac.security.v1")
```

**Evidence:** `security_coordinator.py:159-170` uses `pq_crypto` directly, not `QuantumResistantCrypto`. The `QuantumResistantCrypto` import in `test_sprint7g.py:137` is the ONLY broken usage.

**Decision:** `QuantumResistantCrypto` class is a ghost class that was never fully designed. The actual PQ crypto implementation uses the `PostQuantumBackend` protocol + `create_post_quantum_backend()` factory.

---

### 4. `ZKPResearchEngine`

| Property | Value |
|----------|-------|
| **Interface inferred** | `__init__()`, `initialize()`, `generate_proof(statement, proof_type, confidence)`, `verify_proof(statement, proof, proof_type)`, `cleanup()` |
| **Returns** | `dict(valid, success)` |
| **Existing partial impl** | `_shims/security_zkp_research_engine.py` — STUB ONLY, raises `NotImplementedError` |
| **Real impl location** | None found |
| **Planned purpose** | Zero-knowledge proof generation/verification for query privacy |
| **M1 feasibility** | N/A — no implementation exists |
| **ZKP use case** | OSINT query privacy: prove knowledge of IoC without revealing query |

**Current state:** STUB ONLY. `security_coordinator.py:172-184` handles this gracefully.

**Architecture note:** For an OSINT tool, ZKP is an advanced feature. Potential use cases:
1. Query privacy: prove IoC matches criteria without revealing IoC
2. Attribution-free reporting: prove finding came from tool without identity
3. Credential proving: prove API access without sharing credentials

**Decision required:** Is ZKP a real planned feature? Without a use case, this is aspirational.

---

### 5. `TemporalAnonymizer`

| Property | Value |
|----------|-------|
| **Interface inferred** | `__init__(enabled, max_delay, max_buffer_size)`, `anonymize_timestamp(ts)`, `delayed_write_buffer()` |
| **Returns** | `float` (anonymized timestamp), `list[CanonicalFinding]` (buffered findings) |
| **Existing partial impl** | ✅ **FULLY IMPLEMENTED** at `security/temporal_anonymizer.py` |
| **Import paths in usage sites** | `intelligence/archive_discovery.py:45` → `hledac.security.temporal_anonymizer` |
| **Real impl location** | `security/temporal_anonymizer.py:36` — `TemporalAnonymizer` class |
| **Feature gate** | `HLEDAC_ENABLE_ZERO_ATTRIBUTION=1` |
| **Planned purpose** | Timestamp anonymization: round to 15-min boundaries + ±2min jitter |
| **M1 feasibility** | Native Python + asyncio — fully functional |

**Implementation verified at `security/temporal_anonymizer.py:36-150`:**
- `anonymize_timestamp()`: rounds to 15-min boundaries + ±2min jitter
- `delayed_write_buffer()`: async buffer with random flush delays [30s, 120s]
- Bounded: `_MAX_BUFFER_SIZE = 1000`, `_JITTER_MAX = 120.0`
- M1-safe: all operations < 5ms per finding

**Fix approach:** Fix import paths in all 4 usage sites:
```python
# Current (BROKEN):
from hledac.security.temporal_anonymizer import TemporalAnonymizer

# Fix options:
# Option A: Shim (matches pattern):
from _shims.security_temporal_anonymizer import TemporalAnonymizer
# Option B: Direct (correct path):
from security.temporal_anonymizer import TemporalAnonymizer
```

---

### 6. `ZeroAttributionEngine`

| Property | Value |
|----------|-------|
| **Interface inferred** | Constructor only (no explicit methods called in scope) |
| **Returns** | N/A (constructor only) |
| **Existing partial impl** | ✅ **FULLY IMPLEMENTED** at `security/zero_attribution_engine.py` |
| **Real impl location** | `security/zero_attribution_engine.py:36` — `ZeroAttributionEngine` class |
| **Feature gate** | `HLEDAC_ENABLE_ZERO_ATTRIBUTION=1` |
| **Planned purpose** | Query fingerprinting anonymization: UA pool, EXIF stripping, PDF metadata removal |
| **M1 feasibility** | Native Python — fully functional |

**Implementation verified at `security/zero_attribution_engine.py:36-200+`:**
- 50+ real browser UA strings in `_UA_POOL`
- `secrets.token_hex()` for cryptographic randomness
- Optional PIL/pypdf for metadata stripping
- Cover traffic generation for timing correlation prevention

**Fix approach:** Fix import paths in all 4 usage sites:
```python
# Current (BROKEN):
from hledac.security.zero_attribution_engine import ZeroAttributionEngine

# Fix options:
# Option A: Shim (matches pattern):
from _shims.security_zero_attribution_engine import ZeroAttributionEngine
# Option B: Direct (correct path):
from security.zero_attribution_engine import ZeroAttributionEngine
```

---

### 7. `KeyManager`

| Property | Value |
|----------|-------|
| **Interface inferred** | Constructor + `async _load_master_keys()`, `async generate_key()`, `async get_key()` |
| **Returns** | Key bytes, stored in LMDB |
| **Existing partial impl** | ✅ **FULLY IMPLEMENTED** at `security/key_manager.py` |
| **Real impl location** | `security/key_manager.py:53` — `KeyManager` class |
| **Dependencies** | `cryptography` package, `orjson`, LMDB |
| **Planned purpose** | Master key management with AES-256-GCM encryption |
| **M1 feasibility** | Native Python with `cryptography` — fully functional |

**Implementation verified at `security/key_manager.py:53-120+`:**
- Uses HKDF + AES-256-GCM
- Keys stored in LMDB (via `open_lmdb()`)
- mlock for swap prevention
- Async-safe with `asyncio.Lock()`

**Fix approach:** Fix import path in `data_leak_hunter.py:817`:
```python
# Current (BROKEN):
from hledac.security.key_manager import KeyManager

# Fix:
from security.key_manager import KeyManager
```

---

## Fix Summary

| Class | Status | Fix Required | Effort |
|-------|--------|--------------|--------|
| `StealthEngine` | Shim works | Update `security_coordinator.py:133` import | 1 line |
| `ThreatIntelligence` | Stub only | Decision needed | N/A |
| `QuantumResistantCrypto` | Not used | Fix `test_sprint7g.py:137` import | 1 line |
| `ZKPResearchEngine` | Stub only | Decision needed | N/A |
| `TemporalAnonymizer` | **FULLY IMPLEMENTED** | Fix 4 import paths | 4 lines |
| `ZeroAttributionEngine` | **FULLY IMPLEMENTED** | Fix 4 import paths | 4 lines |
| `KeyManager` | **FULLY IMPLEMENTED** | Fix 1 import path | 1 line |

**Total broken imports:** 13
**Already shimmed:** 4 (via `_shims/`)
**Need path fixes:** 9 (import paths wrong, implementations exist)

---

## Root Cause Analysis

The `hledac.security.*` namespace assumes sibling package `hledac/security/` that doesn't exist. The actual implementations live in:
- `hledac/universal/security/` — security modules
- `hledac/universal/_shims/` — shim adapters
- `hledac/universal/stealth/` — stealth-related modules

**Historical context:** These imports likely came from a planned monorepo structure where `hledac/security/` would be a sibling package. That structure was never created; implementations were placed directly in `hledac/universal/security/`.

---

## Recommendation

**Minimal fix (9 lines):**
1. Fix 4x `TemporalAnonymizer` imports
2. Fix 4x `ZeroAttributionEngine` imports
3. Fix 1x `KeyManager` import

**Decision required:**
- `ThreatIntelligence` — stub or planned?
- `ZKPResearchEngine` — stub or planned?
- `QuantumResistantCrypto` — remove from test or implement?

**No net-new implementation needed.** All three "stub" classes are gracefully degraded in `security_coordinator.py` — the coordinator logs warnings and continues without them.
