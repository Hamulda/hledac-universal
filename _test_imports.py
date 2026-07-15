#!/usr/bin/env python3
"""Test imports of P2-3 pipeline stages."""
import importlib.util
import sys
import os

ROOT = '/Users/vojtechhamada/PycharmProjects/Hledac/hledac'
sys.path.insert(0, ROOT)
os.chdir(f'{ROOT}/universal')

results = []

# _stage_protocol
try:
    from hledac.universal.pipeline._stage_protocol import (
        StageMetrics, StageContext, BoundedStageQueue, StageResult
    )
    results.append(("OK: _stage_protocol", True, None))
except Exception as e:
    results.append(("FAIL: _stage_protocol: " + str(e), False, type(e).__name__))

# aimd_controllers — load directly via importlib (bypasses coordinators/__init__.py)
try:
    import os
    path = os.path.join(ROOT, 'universal', 'coordinators', 'aimd_controllers.py')
    spec = importlib.util.spec_from_file_location(
        'hledac.universal.coordinators.aimd_controllers', path
    )
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules['hledac.universal.coordinators.aimd_controllers'] = mod
        spec.loader.exec_module(mod)
    results.append(("OK: aimd_controllers", True, None))
except Exception as e:
    results.append(("FAIL: aimd_controllers: " + str(e), False, type(e).__name__))

# Stage modules
for mod_name, cls_name in [
    ("_discovery_stage", "DiscoveryStage"),
    ("_dedup_stage", "DedupStage"),
    ("_fetch_stage", "FetchStage"),
    ("_match_stage", "MatchStage"),
    ("_enrich_stage", "EnrichStage"),
    ("_store_stage", "StoreStage"),
]:
    try:
        mod = __import__(
            f"hledac.universal.pipeline.{mod_name}",
            fromlist=[cls_name]
        )
        getattr(mod, cls_name)
        results.append((f"OK: {mod_name}", True, None))
    except Exception as e:
        results.append((f"FAIL: {mod_name}: " + str(e), False, type(e).__name__))

# Pipeline orchestrator
try:
    from hledac.universal.pipeline._pipeline_orchestrator import (
        PipelineOrchestrator, run_public_pipeline
    )
    results.append(("OK: _pipeline_orchestrator", True, None))
except Exception as e:
    results.append(("FAIL: _pipeline_orchestrator: " + str(e), False, type(e).__name__))

# AIMDController basic test
try:
    mod = sys.modules.get('hledac.universal.coordinators.aimd_controllers')
    AIMDController = getattr(mod, 'AIMDController')
    ctrl = AIMDController(
        min_value=1, max_value=16, additive_increment=1,
        decrease_factor=0.75, success_threshold=2, name="test"
    )
    assert ctrl.window == 2, f"expected window=2, got {ctrl.window}"
    results.append(("OK: AIMDController basic", True, None))
except Exception as e:
    results.append(("FAIL: AIMDController basic: " + str(e), False, type(e).__name__))

# Summary
print("=" * 60)
for msg, ok, err in results:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {msg}")

ok_count = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"\n{ok_count}/{total} tests passed")
sys.exit(0 if ok_count == total else 1)
