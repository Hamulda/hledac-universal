# MODERN-48: Migration Sequencing / Blast Radius Plan

## Executive Summary

**Problem**: Big-bang migration of I/O + memory + scheduling risks M1 regressions.

**Solution**: Wave-based migration with gates, ensuring each wave's stability before proceeding.

---

## Migration Waves

### W0: Pre-migration Baseline (Before any changes)
**Duration**: 1 day
**Purpose**: Establish baseline metrics and verify current system health.

**Tasks**:
1. Run full test suite (`python -m pytest tests/ -x --timeout=30 -q`)
2. Measure baseline metrics:
   - Memory usage (RSS, system-used)
   - Fetch throughput (URLs/second)
   - Latency (p50, p95, p99)
   - GIL contention (via `sys.getswitchinterval`)
3. Capture baseline for MODERN-47 tests (Phase 28 verification tests)
4. Document any existing failures

**Gate**: All baseline tests pass, metrics captured.

---

### W1: P0 Fixes (MODERN-02/03/05/06 + verify 01/04)
**Duration**: 2-3 days
**Priority**: CRITICAL - These fix existing M1 regressions

**MODERN-02**: Memory leak in AIMD controller
**MODERN-03**: GIL release in PyO3 extensions
**MODERN-05**: Release GIL for CPU-bound Rust work (rayon)
**MODERN-06**: Clear asyncio event loop leak
**MODERN-01**: Verify P0 fixes for memory governor
**MODERN-04**: Verify AIMD stability

**Key Files**:
- `coordinators/fetch_coordinator.py`
- `rust_extensions/src/gil.rs`
- `coordinators/aimd_controllers.py`
- `core/resource_governor.py`

**Changes**:
1. Implement AIMD memory leak fix
2. Ensure `py.detach()` releases GIL correctly
3. Verify asyncio loop cleanup
4. Add MODERN-47 guard tests

**Gate**: Phase 28 tests pass on M1 8GB:
```bash
python -m pytest tests/test_phase28_*.py -x --timeout=60 -q
```

---

### W2: UMA Singleton (Phases 42-45)
**Duration**: 2-3 days

**MODERN-42**: Unified Memory Allocator (UMA) singleton pattern
**MODERN-43**: M1 8GB ceiling enforcement
**MODERN-44**: Memory pressure detection
**MODERN-45**: GC budget optimization

**Key Files**:
- `utils/uma_budget.py` (SSOT for 6.25 GiB)
- `core/resource_governor.py`
- `brain/mlx_cache.py`

**Changes**:
1. Centralize all memory budget tracking in UmaBudget SSOT
2. Remove hardcoded 6.25 values elsewhere
3. Add GC budget controller
4. Implement pressure level detection

**UMA SSOT Verification**:
```python
# All memory constants must derive from UmaBudget.UMA_HARD_CEILING_GIB = 6.25
assert UmaBudget.UMA_HARD_CEILING_GIB == 6.25
assert UmaBudget.MISSION_PEAK_RSS_GIB == 5.5  # 88% of ceiling
assert UmaBudget.THRESHOLD_WARN_GIB >= 5.5  # ~95% of ceiling
```

**Gate**: Phase 28 tests pass, memory usage within budget:
```bash
python -m pytest tests/test_phase28_uma_ceiling.py -x --timeout=60 -q
```

---

### W3: Arrow Fabric (Phases 17-25)
**Duration**: 3-4 days

**MODERN-17** through **MODERN-25**: Arrow-based data pipeline

**Key Changes**:
1. Replace dict-based evidence with Arrow arrays
2. Zero-copy batch processing
3. Columnar storage for DuckDB
4. Memory-mapped Arrow IPC

**Key Files**:
- `knowledge/duckdb_store.py`
- `evidence/arrow_fabric.py`
- `pipeline/arrow_batch.py`

**Architecture**:
```
Input → Arrow RecordBatch → [Transform] → DuckDB → Arrow IPC
```

**Gate**: Arrow integration tests pass:
```bash
python -m pytest tests/test_f261_arrow_fetch_batch.py -x --timeout=60 -q
```

---

### W4: M1 Topology (Phases 26-35)
**Duration**: 3-4 days

**MODERN-26** through **MODERN-35**: M1-specific optimizations

**Key Changes**:
1. P-core / E-core scheduling hints
2. ANE (Apple Neural Engine) integration
3. Metal GPU memory management
4. kqueue-based async I/O (already in uvloop)

**Key Files**:
- `brain/metal_device.py`
- `brain/mlx_worker.py`
- `core/scheduler_v2.py`

**M1 Topology Detection**:
```python
import platform
is_m1 = platform.machine() == "arm64" and platform.mac_ver()[0] >= "12.0"
```

**Gate**: M1 topology tests pass:
```bash
python -m pytest tests/test_brain_metal_device.py -x --timeout=60 -q
```

---

### W5: Tokio Engine (Phases 07-16)
**Duration**: 4-5 days

**MODERN-07** through **MODERN-16**: Tokio async runtime integration

**Key Changes**:
1. Replace asyncio with Tokio for Rust async
2. Multi-threaded runtime for CPU-bound tasks
3. Channel-based IPC
4. Structured concurrency

**Key Files**:
- `runtime/tokio_runtime.py`
- `transport/http3_lane.py` (already tokio-aware)
- `rust_extensions/src/tokio_integration.rs`

**Migration Strategy**:
1. Dual-runtime: asyncio for Python, Tokio for Rust
2. Gradually migrate Python async to Tokio
3. Use `tokio::task::spawn_blocking` for Python interop

**Warning**: This is the highest-risk wave. Requires careful coordination.

**Gate**: Tokio integration tests pass:
```bash
python -m pytest tests/test_pep734_isolated_executors.py -x --timeout=60 -q
```

---

## Rollback Strategy

### Per-Wave Rollback
Each wave includes:
1. **Before**: Git stash of current state (or snapshot)
2. **During**: Feature flags to disable new code paths
3. **After**: Metrics comparison with baseline

### Feature Flags
```python
# Feature flags for progressive rollout
HLEDAC_TOKIO_RUNTIME = False  # W5 gate
HLEDAC_ARROW_FABRIC = False   # W3 gate
HLEDAC_UMA_BUDGET = False     # W2 gate
HLEDAC_GIL_RELEASE = True     # Always on (P0 fix)
```

### Rollback Triggers
- Memory usage > 110% of baseline
- Fetch throughput < 90% of baseline
- Latency p99 > 150% of baseline
- Any Phase 28 test failure

---

## Testing Strategy

### CI/CD Gates
```
┌─────────────────────────────────────────────────────────────┐
│ W0: Baseline                                              │
│   └── All existing tests pass                             │
├─────────────────────────────────────────────────────────────┤
│ W1: P0 Fixes                                              │
│   └── MODERN-47 tests (Phase 28)                          │
│   └── GIL release verified                                 │
├─────────────────────────────────────────────────────────────┤
│ W2: UMA Singleton                                         │
│   └── test_phase28_uma_ceiling.py                         │
│   └── Memory budget enforced                              │
├─────────────────────────────────────────────────────────────┤
│ W3: Arrow Fabric                                          │
│   └── Arrow integration tests                              │
│   └── Zero-copy benchmarks                                 │
├─────────────────────────────────────────────────────────────┤
│ W4: M1 Topology                                           │
│   └── Metal device tests                                  │
│   └── ANE inference tests                                 │
├─────────────────────────────────────────────────────────────┤
│ W5: Tokio Engine                                          │
│   └── Tokio integration tests                             │
│   └── Dual-runtime stability                               │
└─────────────────────────────────────────────────────────────┘
```

### Performance Benchmarks
```bash
# Run benchmarks at each gate
python -m pytest tests/benchmarks/test_hot_paths.py --benchmark-only
```

**Benchmarks to track**:
- `fetch_throughput`: URLs/second
- `memory_rss_mb`: Process RSS in MB
- `uma_pressure`: System-used percentage
- `gil_contention`: Switch interval changes

---

## Risk Assessment

| Wave | Risk Level | Primary Concerns | Mitigation |
|------|------------|------------------|------------|
| W0 | None | Establishing baseline | Well-defined procedures |
| W1 | Medium | GIL semantics changes | MODERN-47 tests guard |
| W2 | Low | Memory budget tight | SSOT verification |
| W3 | Medium | Arrow migration | Zero-copy validation |
| W4 | Low | M1-specific | Platform detection |
| W5 | **HIGH** | Tokio/asyncio conflict | Dual-runtime isolation |

**W5 Special Handling**:
- Run W5 in isolated environment first
- Extensive load testing required
- Feature flag rollout (10% → 50% → 100%)

---

## Timeline

```
Week 1: W0 (baseline) + W1 (P0 fixes)
Week 2: W1 (completion) + W2 (UMA)
Week 3: W2 (completion) + W3 (Arrow)
Week 4: W3 (completion) + W4 (M1 topology)
Week 5: W4 (completion) + W5 (Tokio) start
Week 6: W5 (completion) + integration
Week 7: Final verification + release prep
```

**Total Duration**: ~7 weeks

---

## Success Criteria

1. **Memory**: RSS < 5.5 GiB under full load
2. **Throughput**: ≥ baseline fetch throughput
3. **Latency**: p99 < 2× baseline
4. **Stability**: No regressions in test suite
5. **M1**: All MODERN-47 tests pass on M1 8GB

---

## Appendix: Phase 28 Test Files

Created for MODERN-47:

1. `tests/test_phase28_fetch_coordinator.py`
   - FetchCoordinator construction
   - enqueue_pivot method existence
   - clearance_jar attribute
   - darknet_connector attribute

2. `tests/test_phase28_darknet_guard.py`
   - .onion URL detection
   - Tor availability checks
   - fail-closed behavior

3. `tests/test_phase28_gil_release.py`
   - release_gil function
   - PyO3 py.detach() semantics
   - GIL release measurement
   - rayon integration

4. `tests/test_phase28_uma_ceiling.py`
   - UmaBudget SSOT verification
   - 6.25 GiB ceiling consistency
   - Threshold ladder validation

5. `tests/test_phase28_qos_constants.py`
   - QoS class constants
   - qos_class_t mapping
   - set_thread_qos function
   - QoSProfile struct
