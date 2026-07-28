# ISSUE A5: Feature-Flag Sprawl — 410 HLEDAC_* Variables

**Date:** 2026-07-27
**Status:** Phases 1+2 (high-value call sites) Complete ✅
**Sprint:** F350M-R+

---

## 1. Current State

### 1.1 The Problem — By the Numbers

| Metric | Value |
|--------|-------|
| **Distinct HLEDAC_\* vars** | **410** |
| **Files that reference HLEDAC_ENABLE_\*** | **~330** |
| **Actual `os.environ.get("HLEDAC_ENABLE_...")` checks** | **~15** (lane-gate checks via LANE_REGISTRY) |
| **ENABLE vars referenced ≥2× in code** | **~20** |
| **ENABLE vars defined but never checked** | **~60+** |
| **`core/capabilities.py` usage (CAPS.require)** | **5 files** |
| **`utils/flag_registry.py` FlagSpec registered** | **~40** |
| **`utils/flag_presets.py` PRESETS dict** | **MINIMAL/OSINT/RECON/RESEARCH/FULL** |
| **`SprintFlags` msgspec.Struct fields** | **7 fields** |

### 1.2 Var Taxonomy — What 410 Variables Actually Are

| Category | Count | Example | Appropriate? |
|----------|-------|---------|---------------|
| **ENABLE (lane/sidecar)** | 97 | `HLEDAC_ENABLE_TOR`, `HLEDAC_ENABLE_DHT` | ❌ Sprint profile, not env |
| **RESOURCE_TUNING** | 28 | `HLEDAC_METAL_CACHE_LIMIT_GIB` | ✅ Env OK |
| **DUCKDB** | 15 | `HLEDAC_DUCKDB_INPROCESS` | ✅ Env OK |
| **LANCEDB** | 11 | `HLEDAC_LANCEDB_QUANTIZE` | ✅ Env OK |
| **MLX** | 5 | `HLEDAC_MLX_EMBED_BATCH` | ✅ Env OK |
| **HTTP3** | 5 | `HLEDAC_HTTP3_CACHE_MAX` | ✅ Env OK |
| **PROFILE_RAG** | 20 | `HLEDAC_RAG_CHUNK_SIZE` | ✅ Env OK |
| **CIRCUIT_BREAKER** | 14 | `HLEDAC_CB_CIRCUIT_FAILURE_THRESHOLD` | ✅ Env OK |
| **OVERRIDE_EMERGENCY** | 14 | `HLEDAC_FORCE_PYTHON`, `HLEDAC_FORCE_RUST` | ✅ Env OK (emergency only) |
| **INTEL_API_KEYS** | 6 | `HLEDAC_SHODAN_API_KEY` | ✅ Env OK |
| **PROFILE_ACQUISITION** | 9 | `HLEDAC_ACQUISITION_PROFILE` | ❌ Should be CLI arg |
| **OTHER (tuning constants)** | 140 | `HLEDAC_MAX_PENDING_OPS`, `HLEDAC_RUNTIME_MODE` | ✅ Env OK |
| **SYSTEM (paths/logs)** | ~15 | `HLEDAC_ROOT`, `HLEDAC_LOG_LEVEL` | ✅ Env OK |
| **WINDUP_TIMING** | 7 | `HLEDAC_WINDUP_LEAD_S` | ⚠️ Some in code, some env |
| **COALESCER** | 5 | `HLEDAC_COALESCER_FLUSH_MS` | ✅ Env OK |
| **GRAPH** | 3 | `HLEDAC_GRAPH_MAX_HOPS` | ✅ Env OK |
| **TEST_ONLY** | 6 | `HLEDAC_TEST_NO_NETWORK` | ✅ Test-only env |
| **RUNTIME_INFRA** | 15 | `HLEDAC_OTEL_ENDPOINT` | ✅ Env OK |

**Root cause:** 97 `HLEDAC_ENABLE_*` lane-gate flags (24%) are the primary sprawl — each sprint added `os.environ.get("HLEDAC_ENABLE_X")` instead of wiring into the existing capability registry.

---

## 2. Architecture That Already Exists But Is Underused

### 2.1 `core/capabilities.py` — CapabilityRegistry (Underutilized)

```
CAPS.is_available("mlx")           → True/False (lazy import)
CAPS.require(ZSTD)                → raises if unavailable  
CAPS.try_import(PYPDF)            → (available, module)
CAPS.dump()                       → {name: bool}
```

**Status:** Only **5 files** use it. Infrastructure exists, adoption is near-zero.

### 2.2 `utils/flag_registry.py` — FlagSpec Registry (Partial)

```python
FlagSpec(name="HLEDAC_ENABLE_TOR", group="dark_surface", default="0",
         implies=["HLEDAC_ENABLE_STEALTH"], min_ram_mb=80)
FLAG_REGISTRY: dict[str, FlagSpec] = {}
register(spec) → FlagSpec  # validates group, stores
```

**Status:** ~40 flags registered. **Phase 3 spec exists, Phase 3 implementation incomplete.**

### 2.3 `utils/flag_presets.py` — Named Bundles (Working)

```python
PRESETS = {"MINIMAL": {...}, "OSINT": {...}, "RECON": {...}, "FULL": {...}}
FULL dynamically built from FLAG_REGISTRY at import time.
```

**Status:** 5 presets, works, but **preset names are never shown to operators**.

### 2.4 `SprintFlags` msgspec.Struct (Minimal, Good)

```python
class SprintFlags(msgspec.Struct, frozen=True):
    force: bool = False
    no_communication: bool = False
    no_stealth: bool = False
    no_ghost: bool = False
    no_coordination: bool = False
    production: bool = False
    hermes_force: bool = False
```

**Status:** Only 7 fields — already minimal. But the **ENABLE lane gates are not here**.

### 2.5 `runtime/sidecar_protocol_adapters.py` — SidecarRegistry (Working)

```python
@SidecarRegistry.register("fediverse")
class FediverseAdapter(BaseSidecarAdapter):
    sidecar_id = "fediverse"
    env_gate = "HLEDAC_ENABLE_FEDIVERSE"   # ← stringly-typed
    ram_budget_mb = 50
    priority = 6
```

**Status:** 14 sidecars registered with `env_gate: str`. **Stringly-typed — no compile-time check.**

### 2.6 `config/settings.py` — Runtime Config (Working but Env-Heavy)

`config.settings` already resolves `HLEDAC_ENABLE_*` → typed Python fields.
But it resolves **48 flags directly from env**, not from a profile.

---

## 3. The Three-Layer Model

### Layer 1: Capabilities (Dependency Resolution) — `core/capabilities.py`
- **What:** Optional dependency availability (MLX, DuckDB, curl_cffi, etc.)
- **Mechanism:** Lazy import + cached availability check
- **Current:** 5 files use it; should be **all optional-dependency code**
- **No change needed to structure** — just migrate callers

### Layer 2: Sprint Profiles (Lane Composition) — `runtime/acquisition/profile.py`
- **What:** Which lanes (sidecars, acquisition lanes) run for a given mission
- **Current:** `AcquisitionProfile` class with 7 named profiles
- **Problem:** `HLEDAC_ACQUISITION_PROFILE` env var (79× referenced) bypasses this
- **Fix:** Make profile a **CLI-only concept**, env var is legacy override

### Layer 3: Runtime Config (Resource Tuning) — `config/settings.py`
- **What:** Memory limits, cache sizes, thread counts, timeouts, paths
- **Mechanism:** Env vars with `ENV.get_*` accessors
- **Current:** 48 HLEDAC_ENABLE_ + 100+ other vars
- **Fix:** Keep as-is for tuning; add schema validation from `FlagSpec`

---

## 4. Implementation Plan

### Phase 1: Consolidate ENABLE Flags into SprintProfile (Critical Path)

**Before:**
```python
# Everywhere in code
if os.environ.get("HLEDAC_ENABLE_TOR"):  # 14× for TOR
    run_tor_lane()
```

**After:**
```python
# SprintProfile.lanes is a frozenset of enabled lane IDs
profile = AcquisitionProfile.for_name("recon")
if "tor" in profile.lanes:
    run_tor_lane()

# Or via SidecarContext (already passed to sidecars)
ctx: SidecarContext  # has .sprint_mode, .memory_pressure
```

**Steps:**
1. Extend `runtime/acquisition/profile.py` with `lanes: frozenset[str]` for each profile
2. Add `LaneRegistry` in `runtime/lane_registry.py` — single source of `sidecar_id → env_gate` mapping
3. Codemod all `os.environ.get("HLEDAC_ENABLE_X")` → `LaneRegistry.is_enabled("x")`
4. Deprecate `HLEDAC_ACQUISITION_PROFILE` env var (keep as legacy override only)
5. Remove `env_gate: str` from `BaseSidecarAdapter` — replace with `lane_id` reference

### Phase 2: Grow CapabilityRegistry Usage (Low Risk, High Value)

**Before:**
```python
try:
    import mlx_lm
    HAS_MLX = True
except ImportError:
    HAS_MLX = False
```

**After:**
```python
from core.capabilities import CAPS, MLX_LM
_mlx_lm = CAPS.require(MLX_LM)  # raises RuntimeError if unavailable
```

**Files to migrate:** ~5 files that do their own `try/except ImportError` for core deps.

### Phase 3: Wire FlagSpec into config/settings.py

**Before:**
```python
http3_enabled: bool = True  # HLEDAC_ENABLE_HTTPX_H3 (default ON)
http3_enabled=ENV.get_bool("HLEDAC_ENABLE_HTTPX_H3", True),
```

**After:**
```python
http3_enabled: bool = FlagSpec("HLEDAC_ENABLE_HTTPX_H3").resolve_bool(default=True)
```

`FlagSpec.resolve_bool()` reads env, applies implies/conflicts logic, returns typed value.

### Phase 4: Codemod Pattern

**Tool:** `ast-grep` or custom Python rewrite

**From:**
```python
os.environ.get("HLEDAC_ENABLE_TOR", "0") in ("1", "true", "yes", "on")
```

**To:**
```python
LaneRegistry.is_enabled("tor")
```

**Scope:** 330 files, ~1000 reference sites.
**Approach:** Batch codemod per category (sidecar, transport, brain).

---

## 5. Emergency Override Pattern (For Future)

```
HLEDAC_FORCE_PYTHON=1   → bypass Rust, use Python impl
HLEDAC_FORCE_RUST=1     → reject Python fallback
HLEDAC_FORCE_LANE=tor   → always enable specific lane (overrides profile)
HLEDAC_MEM_CEILING_GIB=5.5  → override memory hard cap (dangerous, logged)
```

These are **genuinely emergency** and should stay as env vars.
They should be **logged prominently** when activated.

---

## 6. M1 8GB Constraints

| Concern | Impact | Mitigation |
|---------|--------|------------|
| RAM budget for profile objects | ~few KB | Negligible — profiles are frozen msgspec structs |
| Codemod blast radius | 330 files | Per-phase, each phase verified with tests |
| Lane dynamic enable at runtime | None | Profiles frozen at sprint start, not mutable |

---

## 7. Python 3.14 Compatibility

- `msgspec.Struct` with `frozen=True, gc=False` is 3.14 native
- `frozenset` lane membership is O(1) and allocation-free
- No `os.environ` in hot path — profiles resolved once at sprint init

---

## 8. Files Modified (Phase 1)

| File | Change | Status |
|------|--------|--------|
| `runtime/lane_registry.py` | **NEW** — LaneSpec registry, _PROFILE_LANES, LaneRegistry class | ✅ |
| `runtime/acquisition/profile.py` | Added `lanes()` method + `is_lane_enabled()` helper | ✅ |
| `runtime/sidecar_protocol.py` | `env_gate: str` → `lane_id: str` + `LANE_REGISTRY.is_enabled()` in `BaseSidecarAdapter.is_available()` | ✅ |
| `runtime/sidecar_protocol_adapters.py` | 18 adapters: `env_gate` → `lane_id` | ✅ |

## 8b. Files Modified (Phase 2 — High-Value Call Sites)

| File | Change | Status |
|------|--------|--------|
| `runtime/sidecar_orchestrator.py` | CommonCrawl, IPFS, Gopher, DigitalGhost, Steganography, TI Feeds → `LANE_REGISTRY.is_enabled()` | ✅ |
| `pipeline/live_public_pipeline.py` | Academic, Synthesis, DSPy, Providerless discovery → `LANE_REGISTRY.is_enabled()` | ✅ |
| `export/hypothesis_builder.py` | `HYPOTHESIS_ENABLED` module-var → `LANE_REGISTRY.is_enabled("hypothesis")` | ✅ |
| `hledac_hypothesis/__init__.py` | `HAS_DSPY` → `_is_dspy_enabled()` helper via LaneRegistry | ✅ |
| `tests/test_hypothesis_builder.py` | Updated import to use `LANE_REGISTRY` | ✅ |

---

## 9. Verification

```bash
# Before: 410 HLEDAC_* vars, 97 ENABLE flags
# After Phase 1: ~313 vars, ~0 ENABLE flags checked at runtime
# After Phase 3: FlagSpec validated, PRESETS discoverable

# Count ENABLE flags actually checked at runtime
rg 'os\.environ\.get\([\'"]HLEDAC_ENABLE_' --type py | wc -l
# Target: < 10 (only emergency overrides)

# Count files using CAPS
rg 'CAPS\.(require|is_available)' --type py -l | wc -l
# Target: > 30 (from 5 current)
```
