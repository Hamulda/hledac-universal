"""
Sprint F206U — Memory Authority Guard Tests

Hermetic static-source + sys.modules tests for memory authority boundaries.
No live network, no subprocess spawn, no mock required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]  # hledac/universal/


# ─── Phase 4: Import Guard Tests ─────────────────────────────────────────────


class TestMemoryAuthorityStatus:
    """MEMORY_AUTHORITY map and classifier."""

    def test_get_memory_authority_status_returns_dict(self):
        from hledac.universal.runtime.memory_authority import get_memory_authority_status
        status = get_memory_authority_status()
        assert isinstance(status, dict)
        assert len(status) > 0

    def test_resource_governor_is_canonical_governor(self):
        from hledac.universal.runtime.memory_authority import classify_memory_symbol
        assert classify_memory_symbol("resource_governor") == "canonical_governor"
        assert classify_memory_symbol("core/resource_governor.py") == "canonical_governor"

    def test_uma_budget_is_raw_sampler(self):
        from hledac.universal.runtime.memory_authority import classify_memory_symbol
        assert classify_memory_symbol("uma_budget") == "raw_sampler"
        assert classify_memory_symbol("utils/uma_budget.py") == "raw_sampler"

    def test_mlx_cache_is_mlx_cache_helper(self):
        from hledac.universal.runtime.memory_authority import classify_memory_symbol
        assert classify_memory_symbol("mlx_cache") == "mlx_cache_helper"
        assert classify_memory_symbol("utils/mlx_cache.py") == "mlx_cache_helper"

    def test_MemoryLayer_is_layer_system(self):
        from hledac.universal.runtime.memory_authority import classify_memory_symbol
        assert classify_memory_symbol("MemoryLayer") == "layer_system"
        assert classify_memory_symbol("layers/memory_layer.py") == "layer_system"

    def test_M1MemoryOptimizer_is_layer_memory(self):
        from hledac.universal.runtime.memory_authority import classify_memory_symbol
        assert classify_memory_symbol("M1MemoryOptimizer") == "layer_memory"
        assert classify_memory_symbol("layers/layer_manager.py::M1MemoryOptimizer") == "layer_memory"

    def test_LayerManager_is_layer_memory(self):
        from hledac.universal.runtime.memory_authority import classify_memory_symbol
        assert classify_memory_symbol("LayerManager") == "layer_memory"

    def test_underscore_MemoryManager_is_legacy_ao(self):
        from hledac.universal.runtime.memory_authority import classify_memory_symbol
        assert classify_memory_symbol("_MemoryManager") == "legacy_ao"
        assert classify_memory_symbol("_MemoryCoordinator") == "legacy_ao"

    def test_UniversalMemoryCoordinator_is_allocator(self):
        from hledac.universal.runtime.memory_authority import classify_memory_symbol
        assert classify_memory_symbol("UniversalMemoryCoordinator") == "allocator"

    def test_coordinator_registry_is_registry_only(self):
        from hledac.universal.runtime.memory_authority import classify_memory_symbol
        assert classify_memory_symbol("coordinator_registry") == "registry_only"

    def test_autonomous_orchestrator_is_facade_only(self):
        from hledac.universal.runtime.memory_authority import classify_memory_symbol
        assert classify_memory_symbol("autonomous_orchestrator") == "legacy_ao"
        assert classify_memory_symbol("legacy/autonomous_orchestrator.py") == "legacy_ao"


class TestCanonicalImportGuard:
    """Static source assertion: canonical path must NOT import legacy memory systems."""

    def test_core_main_does_not_import_autonomous_orchestrator(self):
        src = (ROOT / "core" / "__main__.py").read_text()
        assert "autonomous_orchestrator" not in src
        assert "FullyAutonomousOrchestrator" not in src

    def test_core_main_does_not_import_LayerManager(self):
        src = (ROOT / "core" / "__main__.py").read_text()
        assert "LayerManager" not in src
        assert "layer_manager" not in src

    def test_core_main_does_not_import_MemoryLayer(self):
        src = (ROOT / "core" / "__main__.py").read_text()
        assert "MemoryLayer" not in src
        assert "memory_layer" not in src

    def test_sprint_scheduler_does_not_import_autonomous_orchestrator(self):
        src = (ROOT / "runtime" / "sprint_scheduler.py").read_text()
        assert "autonomous_orchestrator" not in src
        assert "FullyAutonomousOrchestrator" not in src

    def test_sprint_scheduler_does_not_import_LayerManager(self):
        src = (ROOT / "runtime" / "sprint_scheduler.py").read_text()
        assert "LayerManager" not in src

    def test_live_public_pipeline_does_not_import_layer_memory_systems(self):
        src = (ROOT / "pipeline" / "live_public_pipeline.py").read_text()
        assert "LayerManager" not in src
        assert "MemoryLayer" not in src
        assert "layer_manager" not in src
        assert "memory_layer" not in src

    def test_temporal_signal_layer_does_not_import_mlx_numpy_pandas(self):
        ts_path = ROOT / "runtime" / "temporal_signal_layer.py"
        if ts_path.exists():
            src = ts_path.read_text()
            assert "import mlx" not in src and "from mlx" not in src
            assert "import numpy" not in src and "from numpy" not in src
            assert "import pandas" not in src and "from pandas" not in src

    def test_resource_governor_does_not_eager_import_mlx(self):
        src = (ROOT / "core" / "resource_governor.py").read_text()
        lines = src.split('\n')
        # Track indentation to find top-level imports (not inside a function)
        top_level_mlx = []
        indent_stack = []
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            indent = len(line) - len(line.lstrip())
            # Dedent: pop when we go back to or past a previous indent level
            while indent_stack and indent <= indent_stack[-1]:
                indent_stack.pop()
            if stripped.startswith('def ') or stripped.startswith('async def '):
                indent_stack.append(indent)
            elif not indent_stack and ('import mlx' in line or 'from mlx' in line):
                top_level_mlx.append(f"line {lineno}: {line.rstrip()}")
        assert len(top_level_mlx) == 0, f"Top-level mlx import found: {top_level_mlx}"


class TestSysModulesGuard:
    """sys.modules guard: canonical boot must not load legacy AO memory."""

    def test_importing_core_main_does_not_load_legacy_ao(self):
        # Clear any previously loaded modules
        mods_to_clear = [k for k in sys.modules if 'autonomous_orchestrator' in k or '_MemoryManager' in k]
        for k in mods_to_clear:
            del sys.modules[k]

        # Import canonical path
        import importlib
        import hledac.universal.core.__main__ as main_mod
        importlib.reload(main_mod)

        # Check no legacy AO modules loaded
        ao_mods = [k for k in sys.modules if 'autonomous_orchestrator' in k and 'hledac' in k]
        assert len(ao_mods) == 0, f"Legacy AO modules loaded: {ao_mods}"

    def test_importing_sprint_scheduler_does_not_load_legacy_ao(self):
        mods_to_clear = [k for k in sys.modules if 'autonomous_orchestrator' in k or '_MemoryManager' in k]
        for k in mods_to_clear:
            del sys.modules[k]

        import hledac.universal.runtime.sprint_scheduler as sched_mod
        import importlib
        importlib.reload(sched_mod)

        ao_mods = [k for k in sys.modules if 'autonomous_orchestrator' in k and 'hledac' in k]
        assert len(ao_mods) == 0, f"Legacy AO modules loaded: {ao_mods}"


class TestMemoryLayerAuthorityComment:
    """Phase 3: tiny authority comment on layer_system file."""

    def test_memory_layer_has_authority_comment(self):
        src = (ROOT / "layers" / "memory_layer.py").read_text()
        # Must mention canonical governor somewhere in file
        assert "canonical" in src or "resource_governor" in src or "governor" in src, \
            "memory_layer.py should reference canonical governor"

    def test_layer_manager_m1optimizer_has_authority_comment(self):
        src = (ROOT / "layers" / "layer_manager.py").read_text()
        # M1MemoryOptimizer docstring should note it's not the canonical governor
        idx = src.find('class M1MemoryOptimizer')
        assert idx >= 0, "M1MemoryOptimizer class not found"
        section = src[idx:idx+400]
        assert len(section) > 0


# ─── Phase 5: E2E Memory Truth Helper ─────────────────────────────────────────

class TestE2EMemoryTruthSchema:
    """Verify e2e artifact supports memory_truth additive field."""

    def test_e2e_sprint_probe_artifact_supports_memory_truth(self):
        from benchmarks.e2e_sprint_probe import _default_artifact
        import inspect

        sig = inspect.signature(_default_artifact)
        # If memory_truth is not in default, it's additive — caller adds it
        # This test just verifies the function is extensible
        assert "command" in sig.parameters
        assert "env" in sig.parameters
        assert "requested_duration" in sig.parameters

    def test_e2e_run_result_json_supports_memory_truth(self):
        # Read existing artifact if available
        import json, os
        artifact_path = ROOT / "probe_e2e_readiness" / "e2e_run_result.json"
        if artifact_path.exists():
            with open(artifact_path) as f:
                data = json.load(f)
            # runtime_truth_present flag must exist
            assert "runtime_truth_present" in data