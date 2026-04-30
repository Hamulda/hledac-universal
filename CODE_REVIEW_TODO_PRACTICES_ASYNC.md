# Code Review Report: TODO/Placeholder + Python 3.14+ Best Practices + Async Audit

**Generated:** 2026-04-30  
**Review Scope:** `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/`  
**Reviewers:** todo-reviewer, python-practices-reviewer, async-reviewer

---

## TODO/Placeholder Markers (22 found)

### Active TODOs Needing Implementation

| File | Line | Content | Priority |
|------|------|---------|----------|
| `intelligence/pastebin_monitor.py` | 24 | `# TODO F198x: migrate to FetchCoordinator.fetch() — circuit breaker bypass` | HIGH |
| `brain/decision_engine.py` | 196 | `# TODO: Implementovat LLM fallback` | HIGH |
| `brain/research_flow_decider.py` | 199 | `# TODO: Implementovat LLM fallback` | HIGH |
| `execution/ghost_executor.py` | 632 | `# TODO: Implementovat vlastní vyhledávání nebo Google` | MEDIUM |
| `execution/ghost_executor.py` | 651 | `# TODO: Implementovat stealth google search` | MEDIUM |
| `execution/ghost_executor.py` | 753 | `# TODO: Implementovat akademické vyhledávání` | MEDIUM |
| `transport/i2p_transport.py` | 242 | `raise NotImplementedError("I2P SAM messaging not yet implemented")` | MEDIUM |
| `benchmarks/e2e_canonical_benchmark.py` | 410 | `raise NotImplementedError("Live mode not yet implemented — use --hermetic")` | LOW |
| `layers/stealth_layer.py` | 2300 | `# TODO: Extract image and solve` | LOW |
| `knowledge/rag_engine.py` | 851 | `# TODO: Implementovat secure processing` | LOW |
| `legacy/atomic_storage.py` | 1179 | `# TODO: Use Hermes for extraction (requires integration)` | LOW |
| `legacy/autonomous_orchestrator.py` | 26711 | `# TODO: actual archive fetch (future)` | LOW |
| `planning/htn_planner.py` | 660 | `# TODO 8S/8T: further refine per-task instrumentation if Hermes` | LOW |
| `planning/htn_planner.py` | 724 | `# TODO §7.4/§5.15: nahradit quality/corroboration score` | LOW |
| `planning/htn_planner.py` | 752 | `# TODO §7.4/§5.15: nahradit quality/corroboration score` | LOW |
| `utils/predictive_planner.py` | 227 | `# TODO: Lepší predikce pomocí modelu` | LOW |

### NotImplementedError Stubs

| File | Line | Content |
|------|------|---------|
| `deep_probe.py` | 386 | `raise NotImplementedError("PathPattern.generate_predictions must be implemented by subclass")` |
| `brain/ane_embedder.py` | 84 | `raise NotImplementedError("ANE embedder not loaded, use fallback")` |
| `brain/ane_embedder.py` | 89 | `raise NotImplementedError("Real CoreML inference not implemented yet")` |
| `project_types.py` | 755 | `raise NotImplementedError("Subclasses must implement research()")` |
| `transport/i2p_transport.py` | 242 | `raise NotImplementedError("I2P SAM messaging not yet implemented")` |
| `benchmarks/e2e_canonical_benchmark.py` | 410 | `raise NotImplementedError("Live mode not yet implemented — use --hermetic")` |
| `tests/decision_log/build_decision_log.py` | 18,22,26 | `raise NotImplementedError("Stub - rekonstruovat z bytecode")` (x3) |

### Stub Functions (pass body only)

| File | Line | Content |
|------|------|---------|
| `evidence_log.py` | 105-108 | `trace_evidence_append`, `trace_evidence_flush`, `trace_queue_drop`, `trace_counter` — all `pass` |

### DEPRECATED/Dormant Modules

| File | Notes |
|------|-------|
| `orchestrator_integration.py` | DEPRECATED and DORMANT |
| `enhanced_research.py` | DEPRECATED F187A, backward-compat only |
| `pipeline/live_feed_pipeline.py` | DEPRECATED — Sprint 8AN |
| `legacy/autonomous_orchestrator.py` | Legacy path |

---

## Python 3.14+ Best Practices Issues

### HIGH Severity: Optional[X] → X | None (50+ instances)

**Simple search-replace, high impact.**

| File | Lines |
|------|-------|
| `tot_integration.py` | 58,67,202,210,315,521,638,822 |
| `__main__.py` | 350,450,572,718,788,927,1040,1270 |
| `benchmarks/run_sprint82j_benchmark.py` | 365,454-457,829-830 |
| `fetching/public_fetcher.py` | 63,68,1046 |
| `network/session_runtime.py` | 92,96 |
| `knowledge/duckdb_store.py` | 655,667,682,686 |

### HIGH Severity: Union[X, Y] → X | Y (25+ instances)

**Simple search-replace, high impact.**

| File | Lines |
|------|-------|
| `export/markdown_reporter.py` | 416 |
| `export/stix_exporter.py` | 953, 1144 |
| `export/jsonld_exporter.py` | 345 |
| `brain/distillation_engine.py` | 207, 785 |
| `network/dns_tunnel_detector.py` | 323, 770 |
| `utils/find_files.py` | 10,91,92 |
| `brain/ane_embedder.py` | 78 |
| `brain/hypothesis_engine.py` | 2343, 3355, 3620 |
| `knowledge/rag_engine.py` | 1127 |
| `intelligence/relationship_discovery.py` | 157,187,587,1250 |

### MEDIUM Severity: asyncio.wait() → asyncio.gather() (4 production files)

**Modern Python prefers `asyncio.gather()` with `return_exceptions=True`.**

| File | Line | Current |
|------|------|---------|
| `multimodal/evidence_triage.py` | 265 | `asyncio.wait(futures)` |
| `dht/kademlia_node.py` | 437 | `asyncio.wait(futures, timeout=3.0)` |
| `runtime/sprint_scheduler.py` | 1599 | `_asyncio.wait(...)` |
| `legacy/autonomous_orchestrator.py` | 6363, 24335 | `asyncio.wait(fetch_tasks, timeout=15.0)` |

### LOW Severity: Plain @dataclass (40+ files)

**Python 3.14+ encourages `slots=True` for memory efficiency and `kw_only=True` for immutability.**

| File | Lines (sample) |
|------|----------------|
| `tot_integration.py` | 55, 97 |
| `orchestrator/phase_controller.py` | 40, 52 |
| `orchestrator/memory_pressure_broker.py` | 70 |
| `brain/distillation_engine.py` | 51 |
| `brain/decision_engine.py` | 33 |
| `security/self_healing.py` | 64, 76, 90, 104 |
| `utils/execution_optimizer.py` | 53, 68, 81, 979, 993, 1385 |

### LOW Severity: if-elif Chains → match/case (15+ files)

**Could benefit from structural pattern matching (Python 3.10+).**

| File | Lines |
|------|-------|
| `fetching/public_fetcher.py` | 1341, 1488, 1494, 1533, 1574, 1610 |
| `orchestrator/phase_controller.py` | 212, 227 |
| `orchestrator/memory_pressure_broker.py` | 251, 279, 285 |
| `benchmarks/run_sprint82j_benchmark.py` | 844-894 |

### COMPLIANT

- `super(ClassName, self)` — 0 instances found, correctly uses `super()`
- `raise` syntax — 0 instances of old `raise X, Y` syntax

---

## Async/Concurrency Antipatterns

### HIGH Severity: ThreadPoolExecutor Resource Leak

**File:** `text/unicode_analyzer.py:710-711`
```python
_sync_exec = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_sync_exec.submit(loop.run_until_complete, self.cleanup())
```
**Issue:** Inline ThreadPoolExecutor created without shutdown. Each call creates a new executor that is never shut down — resource leak.
**Fix:** Use persistent executor pattern with lazy init and proper shutdown(), or `asyncio.get_event_loop().run_in_executor()` directly.

### MEDIUM Severity: list.pop(0) O(n) in Hot Path

**File:** `layers/temporal_signal_layer.py:576`
```python
oldest = self._lru_order.pop(0)
```
**Issue:** `list.pop(0)` is O(n) — shifts all remaining elements. LRU eviction should be O(1).
**Fix:** Use `collections.deque.popleft()` for O(1) eviction, or `OrderedDict.move_to_end()` + `popitem()` pattern.

### Previously Fixed (Prior Sprints)

| Pattern | Status |
|---------|--------|
| `asyncio.run()` in nested loops | ✅ Fixed — uses `loop.run_until_complete()` pattern |
| `mx.eval([])` before `clear_cache()` | ✅ Correct barrier order in mlx_cache.py |
| `asyncio.gather` missing `return_exceptions` | ✅ All instances have `return_exceptions=True` |
| `pickle` usage | ✅ None found — uses orjson |
| `bytes +=` concatenation | ✅ Uses `bytearray.extend()` pattern |
| `time.sleep` in async contexts | ✅ global_scheduler/hermes3_engine: documented as sync worker-safe |
| ThreadPoolExecutor shutdown | ✅ duckdb_store, model_store properly shutdown |

---

## Summary of Findings

| Category | Severity | Count | Action Required |
|----------|----------|-------|----------------|
| TODO/FIXME/PLACEHOLDER | Various | 22 | Review each for necessity |
| NotImplementedError stubs | Various | 7 | Implement or remove |
| Optional[X] → X \| None | HIGH | 50+ | Search-replace |
| Union[X, Y] → X \| Y | HIGH | 25+ | Search-replace |
| asyncio.wait() → gather() | MEDIUM | 4 prod files | Migrate |
| ThreadPoolExecutor leak | HIGH | 1 file | Fix unicode_analyzer.py:710 |
| list.pop(0) → deque.popleft() | MEDIUM | 1 file | Fix temporal_signal_layer.py:576 |
| @dataclass improvements | LOW | 40+ | Consider slots=True |
| if-elif → match/case | LOW | 15+ | Optional modernization |

---

## Priority Actions

1. **CRITICAL:** Fix `unicode_analyzer.py:710-711` ThreadPoolExecutor leak
2. **HIGH:** Replace `Optional[` with `X | None` (bulk search-replace)
3. **HIGH:** Replace `Union[` with `X | Y` (bulk search-replace)
4. **MEDIUM:** Migrate `asyncio.wait()` to `asyncio.gather()` in production files
5. **MEDIUM:** Fix `temporal_signal_layer.py:576` list.pop(0) → deque.popleft()
6. **LOW:** Evaluate/remove stale TODO comments
7. **LOW:** Consider match/case for complex if-elif chains