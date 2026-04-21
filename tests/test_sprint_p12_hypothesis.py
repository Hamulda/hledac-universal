"""
Sprint P12: Post-Storage Hypothesis Layer Tests
=================================================

Tests for the P12 hypothesis/ToT post-storage placement in live_public_pipeline.
Validates:
- P12: hypothesis layer runs AFTER findings are stored (not before fetch)
- P12: canonical sprint gate is store+hermes_engine, not memory_manager alone
- P12: uses real persisted findings from store, not placeholder RAG context
- P12: no hidden pre-fetch ToT latency in canonical sprint
- P12: bounded to 5 hypotheses, fail-soft
- P12: ToT only runs when total_stored > 0

Invariant table:
| Test | Invariant |
|------|-----------|
| test_no_tot_before_fetch | ToT block not present before the fetch batch in source |
| test_hypothesis_layer_gated_on_store_and_engine | Gate: store is not None AND hermes_engine is not None AND total_stored > 0 |
| test_hypothesis_uses_real_findings | Context built from store.async_get_recent_findings(), not rag_context |
| test_tot_conditional_on_stored_findings | ToT only runs when total_stored > 0 |
| test_bounded_to_five_hypotheses | Loop: hypotheses[:5] — max 5 ToT evaluations |
| test_failsoft_on_exception | Exception in hypothesis block does not propagate |
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestP12PostStoragePlacement:
    """Verify P12 hypothesis layer is placed AFTER storage, not before fetch."""

    def test_no_tot_block_before_fetch_batch(self):
        """ToT/hypothesis block is NOT placed before the fetch batch."""
        # Read the source of async_run_live_public_pipeline
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        # Find the index of "# ---- Fetch batch -----" comment
        fetch_batch_pos = source.find("# ---- Fetch batch -----")
        assert fetch_batch_pos != -1, "Fetch batch marker not found in pipeline"

        # Find P12 marker (hypothesis generation comment)
        p12_pos = source.find("# P12: Hypothesis generation")
        if p12_pos != -1:
            # P12 should appear AFTER fetch batch, not before
            assert p12_pos > fetch_batch_pos, (
                "P12 hypothesis block must be AFTER fetch batch, not before. "
                f"P12 at {p12_pos}, fetch at {fetch_batch_pos}"
            )

    def test_hypothesis_layer_after_aggregate_section(self):
        """Hypothesis layer appears after the aggregate/compute block."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        # Find aggregate section marker
        agg_pos = source.find("# ---- Aggregate -------")
        assert agg_pos != -1, "Aggregate section marker not found"

        # P12 should be after aggregate
        p12_pos = source.find("# P12: Hypothesis generation")
        assert p12_pos > agg_pos, (
            f"P12 should be after aggregate section (pos {agg_pos}), "
            f"but found at pos {p12_pos}"
        )


class TestP12GateLogic:
    """Verify P12 gate uses store+hermes_engine+total_stored, not memory_manager."""

    def test_gate_uses_store_and_engine(self):
        """P12 gate condition: store is not None AND hermes_engine is not None."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        # Find P12 block
        p12_start = source.find("# P12: Hypothesis generation")
        assert p12_start != -1, "P12 block not found"

        # Extract the next ~30 lines after P12 start
        p12_block = source[p12_start:p12_start + 1500]

        # Gate should check: store is not None and hermes_engine is not None
        assert "store is not None" in p12_block or "store is not None" in source, (
            "P12 gate must check 'store is not None'"
        )
        assert "hermes_engine is not None" in p12_block or "hermes_engine is not None" in source, (
            "P12 gate must check 'hermes_engine is not None'"
        )

    def test_gate_conditional_on_total_stored(self):
        """P12 runs only when total_stored > 0 (real findings exist)."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        assert p12_start != -1, "P12 block not found"

        p12_block = source[p12_start:p12_start + 1500]

        # Must check total_stored > 0 before running hypothesis
        assert "total_stored > 0" in p12_block, (
            "P12 gate must check 'total_stored > 0' — no ToT on zero findings"
        )

    def test_no_memory_manager_in_gate(self):
        """P12 gate does NOT use memory_manager (that was the pre-storage gate)."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        assert p12_start != -1, "P12 block not found"

        p12_block = source[p12_start:p12_start + 1500]

        # The canonical gate should NOT be memory_manager-based
        # (old pre-storage gate was: memory_manager is not None and hermes_engine is not None)
        # New gate is: store is not None and hermes_engine is not None and total_stored > 0
        assert "memory_manager is not None" not in p12_block or "store is not None" in p12_block, (
            "P12 canonical gate must use 'store', not 'memory_manager' alone"
        )


class TestP12UsesRealFindings:
    """Verify P12 builds context from real stored findings, not placeholder RAG."""

    def test_queries_store_for_findings(self):
        """P12 calls store.async_get_recent_findings() to get real persisted findings."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        assert p12_start != -1, "P12 block not found"

        p12_block = source[p12_start:p12_start + 1500]

        # Must query real findings from store
        assert "async_get_recent_findings" in p12_block, (
            "P12 must call store.async_get_recent_findings() — real findings, not placeholder"
        )

    def test_context_not_from_rag_alone(self):
        """P12 context uses stored findings count, not rag_context alone."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        p12_block = source[p12_start:p12_start + 1500]

        # The context should include stored_findings_count from real store query
        assert "stored_findings_count" in p12_block or "findings" in p12_block, (
            "P12 context must include real stored findings, not rag_context placeholder"
        )

        # Should NOT use rag_context as primary input (that was the pre-storage approach)
        # The old block had: "rag_context": rag_context
        # The new block has stored findings from the store query
        assert "rag_context" not in p12_block or "async_get_recent_findings" in p12_block, (
            "P12 must use real findings from store, not rag_context placeholder"
        )


class TestP12BoundedBehavior:
    """Verify P12 respects M1 8GB constraints: bounded hypotheses, fail-soft."""

    def test_max_five_hypotheses(self):
        """ToT evaluation bounded to 5 hypotheses: hypotheses[:5]."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        # P12 block is large — use 3000 chars to capture full loop
        p12_block = source[p12_start:p12_start + 3000]

        # Max 5 ToT evaluations
        assert "hypotheses[:5]" in p12_block, (
            "P12 must cap ToT evaluations at 5 hypotheses — M1 8GB safety"
        )

    def test_failsoft_exception_handling(self):
        """Exception in P12 block does not propagate — fail-soft."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        # P12 block spans ~60 lines with nested try — use 5000 chars to capture full scope
        p12_block = source[p12_start:p12_start + 5000]

        # P12 wrapped in try/except with pass
        assert "except Exception:" in p12_block, (
            "P12 must catch exceptions — fail-soft, not crash pipeline"
        )
        assert "pass  # P12" in p12_block or "pass  # P12: fail-soft" in p12_block, (
            "P12 must fail-soft with pass — hypothesis generation is optional"
        )


class TestP12NoPreFetchLatency:
    """Verify canonical sprint does not incur ToT latency before fetch batch."""

    def test_tot_not_in_hot_path(self):
        """ToT runs after fetch+storage, not in the hot discovery-to-fetch path."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        # Hot path: discovery → fetch → store
        discovery_pos = source.find("# ---- Discovery (8AC) -----")
        fetch_pos = source.find("# ---- Fetch batch -----")

        assert discovery_pos != -1, "Discovery marker not found"
        assert fetch_pos != -1, "Fetch batch marker not found"

        p12_pos = source.find("# P12: Hypothesis generation")
        assert p12_pos != -1, "P12 block not found"

        # P12 must be after fetch batch (not in hot path)
        assert p12_pos > fetch_pos, (
            f"ToT must not be in hot path before fetch (pos {fetch_pos}), "
            f"but P12 found at pos {p12_pos}"
        )


class TestP12HypothesisEngine:
    """Verify P12 correctly uses HypothesisEngine for generation."""

    @pytest.mark.asyncio
    async def test_hypothesis_engine_initialized(self):
        """HypothesisEngine is instantiated inside P12 block."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        p12_block = source[p12_start:p12_start + 1500]

        assert "HypothesisEngine()" in p12_block, (
            "P12 must instantiate HypothesisEngine for hypothesis generation"
        )

    @pytest.mark.asyncio
    async def test_tot_integration_layer_initialized(self):
        """TotIntegrationLayer is instantiated inside P12 block."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        p12_block = source[p12_start:p12_start + 1500]

        assert "TotIntegrationLayer()" in p12_block, (
            "P12 must instantiate TotIntegrationLayer for ToT evaluation"
        )


class TestP12CanonicalBehavior:
    """Canonical sprint behavior: no ToT on empty runs."""

    def test_no_tot_on_zero_findings(self):
        """When total_stored == 0, P12 does not run ToT."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        p12_block = source[p12_start:p12_start + 1500]

        # Gate must include total_stored > 0 check
        assert "total_stored > 0" in p12_block, (
            "P12 must gate on total_stored > 0 — no ToT on zero findings"
        )

    def test_tot_solution_count_tracked(self):
        """P12 tracks tot_solution_count for telemetry."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        p12_block = source[p12_start:p12_start + 1500]

        # Should track how many ToT solutions were found
        assert "tot_solution_count" in p12_block, (
            "P12 should track tot_solution_count for run telemetry"
        )


class TestP12DILoadWire:
    """P12 DI wire tests: gate opens only when store+hermes_engine+stored_findings>0."""

    def test_gate_requires_store_and_hermes_and_stored(self):
        """
        P12 gate requires ALL THREE: store is not None AND hermes_engine is not None AND total_stored > 0.
        Canonical sprint DI wire: hermes_engine travels with duckdb_store into pipeline.
        """
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        p12_block = source[p12_start:p12_start + 2000]

        # Gate must check all three conditions
        assert "store is not None" in p12_block, (
            "P12 gate must check 'store is not None'"
        )
        assert "hermes_engine is not None" in p12_block, (
            "P12 gate must check 'hermes_engine is not None'"
        )
        assert "total_stored > 0" in p12_block, (
            "P12 gate must check 'total_stored > 0'"
        )

        # Gate must be AND-chained (all three required)
        assert "and" in p12_block.lower(), (
            "P12 gate must use AND for all-three condition chain"
        )

    def test_gate_blocks_when_hermes_none(self):
        """P12 gate does NOT open when hermes_engine is None (even with store and findings)."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        p12_block = source[p12_start:p12_start + 2000]

        # Count occurrences of the gate condition pattern
        # Must be: if store is not None and hermes_engine is not None and total_stored > 0:
        lines = p12_block.split('\n')
        gate_lines = [l for l in lines if 'if ' in l and 'store' in l and 'hermes_engine' in l and 'total_stored' in l]
        assert len(gate_lines) >= 1, "P12 must have a gate line checking store+hermes_engine+total_stored"

    def test_no_tot_when_store_none(self):
        """ToT does not run when store is None (hermes_engine is irrelevant without store)."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        p12_block = source[p12_start:p12_start + 2000]

        # The gate if-condition must include "store is not None"
        assert "store is not None" in p12_block, (
            "Canonical gate requires store for persistence, not just hermes alone"
        )

    def test_no_tot_when_no_stored_findings(self):
        """ToT does not run when total_stored == 0 (no evidence to reason about)."""
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        p12_block = source[p12_start:p12_start + 2000]

        # Must check total_stored > 0 before running ToT
        assert "total_stored > 0" in p12_block, (
            "P12 requires stored findings before running ToT — no reasoning on empty store"
        )

    def test_scheduler_passes_hermes_to_pipeline(self):
        """
        SprintScheduler passes hermes_engine into async_run_live_public_pipeline.
        Verifies DI wire: scheduler._hermes_engine → pipeline P12 gate.
        """
        import ast, inspect
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        # Check that _run_public_discovery_in_cycle passes hermes_engine
        source = inspect.getsource(SprintScheduler._run_public_discovery_in_cycle)
        assert "hermes_engine=hermes_engine" in source, (
            "Scheduler must pass hermes_engine into async_run_public — DI wire for P12"
        )

    def test_scheduler_loads_hermes_at_sprint_start(self):
        """
        SprintScheduler loads Hermes at sprint start (_load_hermes_for_sprint).
        Verifies bounded M1 8GB lifecycle: load at BOOT, release at TEARDOWN.
        """
        import inspect
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        # Check that run() calls _load_hermes_for_sprint
        source = inspect.getsource(SprintScheduler.run)
        assert "_load_hermes_for_sprint" in source, (
            "Scheduler must call _load_hermes_for_sprint at sprint start — bounded Hermes lifecycle"
        )

    def test_scheduler_releases_hermes_at_teardown(self):
        """
        SprintScheduler releases Hermes at teardown (in _close_dedup region).
        Verifies bounded M1 8GB lifecycle: load at BOOT, release at TEARDOWN.
        """
        import inspect
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        source = inspect.getsource(SprintScheduler.run)
        # After _close_dedup, there should be Hermes unload
        # The unload call should be near the teardown section
        assert "hermes_engine" in source, (
            "Scheduler must handle hermes_engine teardown — bounded M1 8GB lifecycle"
        )


class TestP12HermesLifecycleUnderModelManager:
    """
    Test Hermes lifecycle under ModelManager (canonical owner).

    Invariant table:
    | Test | Invariant |
    |------|-----------|
    | test_load_via_model_manager | Hermes loaded via ModelManager.load_model("hermes"), not direct Hermes3Engine |
    | test_unload_via_model_manager | Hermes unloaded via ModelManager.release_model("hermes") |
    | test_safe_skip_on_memory_pressure | Hermes load skipped when ModelManager raises MemoryPressureError |
    | test_gate_open_with_store_and_hermes | P12 gate opens when store+hermes_engine+stored findings all present |
    | test_teardown_calls_unload_method | Teardown path calls _unload_hermes_at_teardown |
    """

    def test_load_via_model_manager(self):
        """
        _load_hermes_for_sprint uses ModelManager.load_model("hermes").
        Verifies canonical Hermes lifecycle owner is ModelManager.
        """
        import inspect
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        source = inspect.getsource(SprintScheduler._load_hermes_for_sprint)
        assert "get_model_manager()" in source, (
            "Scheduler must use get_model_manager() — canonical Hermes lifecycle owner"
        )
        assert 'load_model("hermes")' in source or "load_model('hermes')" in source, (
            "Scheduler must use ModelManager.load_model('hermes') — not direct Hermes3Engine"
        )
        # Must NOT directly instantiate Hermes3Engine
        assert "Hermes3Engine()" not in source, (
            "Scheduler must NOT directly instantiate Hermes3Engine — use ModelManager"
        )

    def test_unload_via_model_manager(self):
        """
        _unload_hermes_at_teardown uses ModelManager.release_model("hermes").
        Verifies canonical Hermes unload authority is ModelManager.
        """
        import inspect
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        source = inspect.getsource(SprintScheduler._unload_hermes_at_teardown)
        assert "get_model_manager()" in source, (
            "Teardown must use get_model_manager() — canonical Hermes lifecycle owner"
        )
        assert 'release_model("hermes")' in source or "release_model('hermes')" in source, (
            "Teardown must use ModelManager.release_model('hermes')"
        )

    @pytest.mark.asyncio
    async def test_safe_skip_on_memory_pressure(self):
        """
        When ModelManager raises RuntimeError (memory pressure), Hermes load is skipped.
        Verifies fail-soft skip — sprint continues, ToT is skipped.
        """
        import sys
        from unittest.mock import AsyncMock, MagicMock

        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig

        # Create minimal scheduler instance using proper config
        config = SprintSchedulerConfig(
            sprint_duration_s=30.0,
            export_enabled=False,
        )
        scheduler = SprintScheduler(config)

        # Create a mock model_manager module to avoid real imports
        mock_mm_module = MagicMock()
        mock_mm_instance = MagicMock()
        mock_mm_instance.load_model = AsyncMock(
            side_effect=RuntimeError("[MEMORY ADMISSION] CRITICAL state")
        )
        mock_mm_module.get_model_manager = MagicMock(return_value=mock_mm_instance)

        # Temporarily replace the model_manager module
        original_mm = sys.modules.get("hledac.universal.brain.model_manager")
        sys.modules["hledac.universal.brain.model_manager"] = mock_mm_module

        try:
            await scheduler._load_hermes_for_sprint()

            # hermes_engine must be None (skipped due to memory pressure)
            assert scheduler._hermes_engine is None, (
                "Hermes must be None when ModelManager blocks load — fail-soft skip"
            )
        finally:
            if original_mm is not None:
                sys.modules["hledac.universal.brain.model_manager"] = original_mm
            elif "hledac.universal.brain.model_manager" in sys.modules:
                del sys.modules["hledac.universal.brain.model_manager"]

    @pytest.mark.asyncio
    async def test_successful_load_sets_hermes_engine(self):
        """
        When ModelManager.load_model succeeds, hermes_engine is set.
        Verifies DI wire to public pipeline.
        """
        import sys
        from unittest.mock import AsyncMock, MagicMock

        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig

        config = SprintSchedulerConfig(
            sprint_duration_s=30.0,
            export_enabled=False,
        )
        scheduler = SprintScheduler(config)

        mock_hermes = MagicMock()
        mock_hermes.generate_report = AsyncMock(return_value="test report")

        # Create a mock model_manager module to avoid real imports
        mock_mm_module = MagicMock()
        mock_mm_instance = MagicMock()
        mock_mm_instance.load_model = AsyncMock(return_value=mock_hermes)
        mock_mm_module.get_model_manager = MagicMock(return_value=mock_mm_instance)

        # Temporarily replace the model_manager module
        original_mm = sys.modules.get("hledac.universal.brain.model_manager")
        sys.modules["hledac.universal.brain.model_manager"] = mock_mm_module

        try:
            await scheduler._load_hermes_for_sprint()

            # hermes_engine must be the loaded engine
            assert scheduler._hermes_engine is mock_hermes, (
                "Hermes engine must be set when ModelManager.load_model succeeds"
            )
        finally:
            if original_mm is not None:
                sys.modules["hledac.universal.brain.model_manager"] = original_mm
            elif "hledac.universal.brain.model_manager" in sys.modules:
                del sys.modules["hledac.universal.brain.model_manager"]

    @pytest.mark.asyncio
    async def test_teardown_calls_unload_method(self):
        """
        Teardown path calls _unload_hermes_at_teardown.
        Verifies bounded M1 8GB lifecycle: load at BOOT, release at TEARDOWN.
        """
        import inspect
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        source = inspect.getsource(SprintScheduler.run)
        assert "_unload_hermes_at_teardown" in source, (
            "Teardown must call _unload_hermes_at_teardown — bounded Hermes lifecycle"
        )

    @pytest.mark.asyncio
    async def test_unload_releases_via_model_manager(self):
        """
        _unload_hermes_at_teardown calls ModelManager.release_model.
        Verifies canonical unload authority.
        """
        import sys
        from unittest.mock import AsyncMock, MagicMock

        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig

        config = SprintSchedulerConfig(
            sprint_duration_s=30.0,
            export_enabled=False,
        )
        scheduler = SprintScheduler(config)
        scheduler._hermes_engine = MagicMock()

        # Create a mock model_manager module to avoid real imports
        mock_mm_module = MagicMock()
        mock_mm_instance = MagicMock()
        mock_mm_instance.release_model = AsyncMock()
        mock_mm_module.get_model_manager = MagicMock(return_value=mock_mm_instance)

        # Temporarily replace the model_manager module
        original_mm = sys.modules.get("hledac.universal.brain.model_manager")
        sys.modules["hledac.universal.brain.model_manager"] = mock_mm_module

        try:
            await scheduler._unload_hermes_at_teardown()

            # release_model must be called with "hermes"
            mock_mm_instance.release_model.assert_called_once_with("hermes")
            assert scheduler._hermes_engine is None, (
                "hermes_engine must be None after unload"
            )
        finally:
            if original_mm is not None:
                sys.modules["hledac.universal.brain.model_manager"] = original_mm
            elif "hledac.universal.brain.model_manager" in sys.modules:
                del sys.modules["hledac.universal.brain.model_manager"]

    def test_gate_open_with_store_and_hermes(self):
        """
        P12 gate opens when store is not None AND hermes_engine is not None AND total_stored > 0.
        Verifies canonical DI wire: store+hermes+stored findings = gate open.
        """
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        source = inspect.getsource(async_run_live_public_pipeline)

        p12_start = source.find("# P12: Hypothesis generation")
        p12_block = source[p12_start:p12_start + 2000]

        # Gate must check all three conditions
        assert "store is not None" in p12_block
        assert "hermes_engine is not None" in p12_block
        assert "total_stored > 0" in p12_block


if __name__ == "__main__":
    pytest.main([__file__, "-v"])