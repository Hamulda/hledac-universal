# Circular Dependencies Analysis & Remediation Roadmap

**Date:** 2026-07-16  
**Status:** Identified 46 circular dependency cycles  
**Project:** Hledac Universal OSINT Orchestrator

---

## Executive Summary

| Category | Count | Risk Level |
|----------|-------|------------|
| Total cycles | 46 | — |
| Real 2-way cycles | 7 | Varied |
| PEP 562 self-references | 39 | 🟢 Low (legitimate) |
| High-risk cycles | 2 | 🔴 HIGH |
| Medium-risk cycles | 5 | 🟡 MEDIUM |

---

## 1. Real Circular Import Cycles (Actionable)

### 🔴 HIGH Risk

#### Cycle A: `knowledge/duckdb_store.py` ↔ `knowledge/quality_assessment.py`

**Files:**
- `knowledge/duckdb_store.py` (11 893 lines, 16 imports including quality_assessment)
- `knowledge/quality_assessment.py` (920+ lines, imports `CanonicalFinding`, `FindingQualityDecision`)

**Import chain:**
```
duckdb_store.py
  └── imports FindingQualityDecision from quality_assessment.py (line 231, 6624, 6833, 6990)
  └── imports CanonicalFinding from quality_assessment.py (line 231)

quality_assessment.py  
  └── imports CanonicalFinding, FindingQualityDecision from duckdb_store.py (line 36, 920)
```

**Root cause:** `quality_assessment.py` defines `FindingQualityDecision` enum/dataclass AND uses `CanonicalFinding` from duckdb_store. But `duckdb_store.py` needs quality decision types for its `assess_findings_quality_batch()` call.

**Affected dependents:** 11 files import `duckdb_store.py`

**Fix strategy:** Move `FindingQualityDecision` to a new `knowledge/_quality_types.py` (or `knowledge/base_types.py`) that has ZERO dependencies on duckdb_store. Both modules import from it without circular risk.

---

#### Cycle B: `knowledge/duckdb_store.py` ↔ `knowledge/sprint_boundary.py`

**Files:**
- `knowledge/duckdb_store.py` imports `_DuckDBQueryCache` from sprint_boundary (line 221)
- `knowledge/sprint_boundary.py` imports `_DuckDBQueryCache` from duckdb_store (line 24)

**Root cause:** `SprintBoundaryCoordinator` uses `DuckDBQueryCache` but the cache lives in duckdb_store.

**Fix strategy:** Move `_DuckDBQueryCache` to a shared `knowledge/_query_cache.py` that both can import from. Or inline the cache class into `sprint_boundary.py` if it's only used there.

---

### 🟡 MEDIUM Risk

#### Cycle C: `transport/base.py` ↔ `transport/__init__.py`

**Import chain:**
```
transport/base.py
  └── imports transport_router, circuit_breaker, httpx_transport, curl_cffi_transport, curl_cffi_fetch from transport/__init__.py (lines 312-324)

transport/__init__.py
  └── imports Transport, TransportAdapter, TransportConfig, TransportResult from transport/base.py (line 7)
```

**Root cause:** `__init__.py` re-exports base types AND submodules that depend on those types. Classic Python circular import pattern.

**Fix strategy:** Move re-exports in `__init__.py` to be lazy (PEP 562 `__getattr__`) or restructure so base types are in `transport/base.py` and `__init__.py` only does `from .base import *` plus submodule lazy imports.

---

#### Cycle D: `export/formatters.py` ↔ `export/sprint_exporter.py`

**Import chain:**
```
formatters.py
  └── imports 16 symbols from sprint_exporter.py (lines 125, 262, 265, 268, 273)

sprint_exporter.py
  └── imports JSONFormatter from formatters.py (line 851)
```

**Root cause:** `formatters.py` uses helper functions from `sprint_exporter.py` (e.g., `_build_capability_synthesis`) but `sprint_exporter.py` uses `JSONFormatter`.

**Fix strategy:** Extract `JSONFormatter` base class to `export/_base.py` that neither depends on sprint_exporter helpers.

---

#### Cycle E: `rust_extensions/src/quality_gate.rs` ↔ `rust_extensions/src/zero_copy.rs`

**Import chain:**
```
quality_gate.rs
  └── imports url_engine, zero_copy (line 38, 41)

zero_copy.rs
  └── imports quality_gate for entropy functions (line 33)
```

**Root cause:** `zero_copy.rs` imports `compute_histogram_neon`, `entropy_from_histogram` from `quality_gate.rs` for entropy computation.

**Fix strategy:** Extract shared entropy/histogram functions to a new `rust_extensions/src/_entropy.rs` module. Both can import from it without circular dependency.

---

### 🟢 LOW Risk (Post-Quantum Crypto Stubs)

#### Cycle F: `security/pq_crypto.py` ↔ `security/pq_crypto_swift.py`
#### Cycle G: `security/pq_export_encryption.py` ↔ `security/pq_export_encryption_swift.py`

**Note:** These are intentional crypto abstraction layers where the Swift backend implements the ABC defined in the base module. They are architectural design patterns, not bugs.

**Fix strategy:** **Do not fix** — these are deliberate protocol/adapter patterns.

---

## 2. Legitimate PEP 562 Self-References (No Action Required)

These files show "cycles" of length 1 (self-references) due to PEP 562 lazy loading `__getattr__`. They are **NOT real circular dependencies:

| File | Pattern |
|------|---------|
| `brain/__init__.py` | 10 self-refs — lazy engine loading |
| `core/rust_backend/__init__.py` | 12 self-refs — lazy domain loading |
| `discovery/academic/__init__.py` | 7 self-refs — lazy module loading |
| `federated/transports/__init__.py` | 4 self-refs — lazy transport loading |
| `utils/mlx_memory/__init__.py` | 6 self-refs — lazy memory backend loading |
| `transport/__init__.py` | 1 self-ref — already analyzed (Cycle C) |

---

## 3. Remediation Roadmap

### Phase 1: Quick Wins (P1 — 1-2 days)

| Priority | Action | Files | Effekt |
|----------|--------|-------|--------|
| P1.1 | Extract `FindingQualityDecision` to `knowledge/_quality_types.py` | duckdb_store.py, quality_assessment.py | Break Cycle A |
| P1.2 | Move `_DuckDBQueryCache` to `knowledge/_query_cache.py` | duckdb_store.py, sprint_boundary.py | Break Cycle B |
| P1.3 | Extract entropy helpers to `rust_extensions/src/_entropy.rs` | quality_gate.rs, zero_copy.rs | Break Cycle E |

### Phase 2: Medium Restructuring (P2 — 3-5 days)

| Priority | Action | Files | Effekt |
|----------|--------|-------|--------|
| P2.1 | PEP 562-ify `transport/__init__.py` with lazy re-exports | transport/base.py, transport/__init__.py | Break Cycle C |
| P2.2 | Extract `JSONFormatter` base to `export/_base.py` | formatters.py, sprint_exporter.py | Break Cycle D |

### Phase 3: Architectural (P3 — 1+ weeks)

| Priority | Action | Rationale |
|----------|--------|-----------|
| P3.1 | Audit 981 unused files — identify dead code vs intentional isolation | Reduce maintenance burden |
| P3.2 | Break 1147 islands into logical package groups | Improve cohesion |
| P3.3 | Consider `brain/`, `core/rust_backend/` monomorphism (12+ self-refs each) | If performance issue, consider breaking into subpackages |

---

## 4. Testing Strategy

After each fix:
```bash
pytest tests/ -x --timeout=30 -q
rtk pytest  # for token-optimized output
```

Verify no new circular dependencies introduced:
```bash
rtk ruff check .  # or equivalent circular import linter
```

---

## 5. Files to Edit for Each Fix

### P1.1: `knowledge/_quality_types.py` (NEW)
```python
# New file: knowledge/_quality_types.py
# Move FindingQualityDecision here — zero duckdb_store imports
```

### P1.2: `knowledge/_query_cache.py` (NEW)
```python
# New file: knowledge/_query_cache.py
# Move _DuckDBQueryCache here — zero circular imports
```

### P1.3: `rust_extensions/src/_entropy.rs` (NEW)
```rust
// New file: rust_extensions/src/_entropy.rs
// Move entropy/histogram functions here
```

---

## 6. Verification Commands

```bash
# Verify circular deps are resolved
rtk ruff check hledac/universal --select=F401  # or circular import rule

# Run full test suite
rtk pytest tests/ -x -q

# Smoke test
python -m hledac.universal --sprint "test" --duration 30
```

---

*Generated: 2026-07-16 | Analyzer: reflex-find-circular + dependency analysis*
