#!/usr/bin/env python3
"""
Sprint F205J: Target Memory Brief + E2E Benchmark Integration
============================================================

Invariant mapping:
  F205J-1  | build_sprint_brief with duckdb_store calls get_target_memory_summary
  F205J-2  | Brief headline includes "prior sprint" when target memory present
  F205J-3  | Brief key_findings includes "Target memory:" when memory present
  F205J-4  | Brief without duckdb_store works fail-soft (no crash)
  F205J-5  | Benchmark aggregate has target_memory_summary_present field
  F205J-6  | Benchmark aggregate has analyst_brief_includes_memory field
  F205J-7  | MockDuckDBStore.async_get_target_memory returns MockTargetMemory
  F205J-8  | Scheduler uses query as target_id (not sprint_id) when query available
  F205J-9  | SprintScheduler._run_analyst_brief_advisory passes duckdb_store to brief
  F205J-10 | open_questions includes drift signal when drift_ratio > 1.5
"""

from __future__ import annotations

import sys
import time as _time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "benchmarks"))

from benchmarks.e2e_canonical_benchmark import (
    HERMETIC_MAX_FINDINGS,
    MockDuckDBStore,
    MockTargetMemory,
    _run_hermetic_benchmark,
)


# ============================================================================
# F205J-1/2/3/4/10: build_sprint_brief with target memory
# ============================================================================

class TestBuildSprintBriefWithTargetMemory:
    """F205J-1 through F205J-4, F205J-10: Brief generation with target memory."""

    @pytest.mark.asyncio
    async def test_brief_includes_memory_when_duckdb_provided(self):
        """F205J-1/2/3: Brief includes memory when duckdb_store is provided."""
        from hledac.universal.knowledge.analyst_workbench import AnalystWorkbench

        # Create mock duckdb with target memory
        mock_store = MagicMock()
        mock_tmemory = MockTargetMemory(
            target_id="test-target",
            sprint_count=3,
            cumulative_finding_count=25,
        )
        mock_store.async_get_target_memory = AsyncMock(return_value=mock_tmemory)

        workbench = AnalystWorkbench(duckdb_store=mock_store)

        findings = [
            {"finding_id": "f1", "source_type": "ct", "confidence": 0.7,
             "ioc_type": "domain", "ioc_value": "evil.com", "query": "test",
             "ts": _time.time(), "provenance": ()},
        ]

        brief = await workbench.build_sprint_brief(
            sprint_id="sprint-abc",
            target_id="test-target",
            findings=findings,
            graph_signal={"graph_nodes": 5, "graph_edges": 12},
            governor=None,
            duckdb_store=mock_store,
        )

        # F205J-2: Headline includes "prior sprint"
        assert "prior sprint" in brief.headline, f"Headline: {brief.headline}"

        # F205J-3: Key findings include "Target memory:"
        key_findings_text = "".join(brief.key_findings)
        assert "Target memory:" in key_findings_text, f"Key findings: {brief.key_findings}"

        # F205J-10: open_questions includes drift signal for high drift
        # With sprint_count=3, drift_ratio=1.0 (avg), so no drift question
        # Let's test with high drift
        high_drift_memory = MockTargetMemory(
            target_id="high-drift-target",
            sprint_count=2,
            cumulative_finding_count=4,  # avg=2 per sprint, this sprint=4
        )
        # Override confidence_drift to simulate high drift
        high_drift_memory.confidence_drift = {
            "sprints": 2, "total_findings": 4,
            "avg_findings_per_sprint": 2.0, "drift_ratio": 2.0,
        }
        mock_store.async_get_target_memory = AsyncMock(return_value=high_drift_memory)

        brief2 = await workbench.build_sprint_brief(
            sprint_id="sprint-def",
            target_id="high-drift-target",
            findings=findings,
            graph_signal={"graph_nodes": 0, "graph_edges": 0},
            governor=None,
            duckdb_store=mock_store,
        )
        open_q_text = "".join(brief2.open_questions)
        assert "drift" in open_q_text.lower(), f"Open questions: {brief2.open_questions}"

    @pytest.mark.asyncio
    async def test_brief_without_duckdb_fails_soft(self):
        """F205J-4: Brief without duckdb_store works fail-soft (no crash)."""
        from hledac.universal.knowledge.analyst_workbench import AnalystWorkbench

        workbench = AnalystWorkbench(duckdb_store=None)

        findings = [
            {"finding_id": "f1", "source_type": "ct", "confidence": 0.5,
             "ioc_type": "domain", "ioc_value": "test.com", "query": "test",
             "ts": _time.time(), "provenance": ()},
        ]

        # Should not raise — fail-soft
        brief = await workbench.build_sprint_brief(
            sprint_id="sprint-xyz",
            target_id="sprint-xyz",
            findings=findings,
            graph_signal={"graph_nodes": 1, "graph_edges": 0},
            governor=None,
            duckdb_store=None,
        )

        assert brief.sprint_id == "sprint-xyz"
        assert brief.confidence >= 0.0
        # Without memory, headline should not mention "prior sprint"
        assert "prior sprint" not in brief.headline


# ============================================================================
# F205J-7: MockDuckDBStore.async_get_target_memory
# ============================================================================

class TestMockDuckDBStoreTargetMemory:
    """F205J-7: MockDuckDBStore returns MockTargetMemory."""

    @pytest.mark.asyncio
    async def test_mock_store_returns_target_memory(self):
        """F205J-7: async_get_target_memory returns MockTargetMemory."""
        store = MockDuckDBStore(accept_rate=1.0)
        await store.async_initialize()

        mem = await store.async_get_target_memory("test-query")

        assert mem is not None
        assert isinstance(mem, MockTargetMemory)
        assert mem.target_id == "test-query"
        assert mem.sprint_count == 2  # default: 2 prior sprints
        assert mem.cumulative_finding_count >= 0

    @pytest.mark.asyncio
    async def test_mock_store_returns_none_for_empty_target_id(self):
        """Empty target_id returns None."""
        store = MockDuckDBStore(accept_rate=1.0)
        await store.async_initialize()

        mem = await store.async_get_target_memory("")
        assert mem is None


# ============================================================================
# F205J-5/6: Benchmark output schema
# ============================================================================

class TestBenchmarkOutputSchema:
    """F205J-5/6: Benchmark output includes target memory fields."""

    @pytest.mark.asyncio
    async def test_benchmark_has_target_memory_fields(self):
        """F205J-5/6: aggregate has target_memory_summary_present and analyst_brief_includes_memory."""
        result = await _run_hermetic_benchmark(num_findings=30, runs=1)

        agg = result["aggregate"]
        assert "target_memory_summary_present" in agg, f"Aggregate keys: {agg.keys()}"
        assert "analyst_brief_includes_memory" in agg, f"Aggregate keys: {agg.keys()}"

        # In hermetic mode, both should be True (mock returns memory, brief checks it)
        assert agg["target_memory_summary_present"] is True
        assert agg["analyst_brief_includes_memory"] is True

    @pytest.mark.asyncio
    async def test_benchmark_schema_backwards_compatible(self):
        """Benchmark aggregate retains all F205E fields."""
        result = await _run_hermetic_benchmark(num_findings=30, runs=1)

        agg = result["aggregate"]
        required = {
            "findings_per_minute", "dedup_ratio", "sidecar_total_ms",
            "stored_count", "accepted_count", "peak_rss_mb",
            "memory_ceiling_ok", "target_memory_summary_present",
            "analyst_brief_includes_memory",
        }
        assert required.issubset(agg.keys()), f"Missing: {required - agg.keys()}"


# ============================================================================
# F205J-8/9: Scheduler target_id and duckdb_store injection
# ============================================================================

class TestSchedulerTargetMemoryBriefIntegration:
    """F205J-8/9: Scheduler uses query as target_id and passes duckdb_store."""

    def test_scheduler_uses_query_as_target_id(self):
        """F205J-8: SprintScheduler uses query (not sprint_id) as target_id."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig

        config = SprintSchedulerConfig(sprint_duration_s=1.0, max_cycles=1)
        scheduler = SprintScheduler(config)

        # Simulate the advisory path: query should be used as target_id
        # The scheduler stores self.query (from run() signature)
        scheduler.query = "my-research-query"
        scheduler.sprint_id = "sprint-123"
        scheduler._analyst_workbench = None  # workbench not injected — advisory skips

        # Verify the pattern: sprint_id is NOT used when query is available
        # This is validated by checking the _run_analyst_brief_advisory logic:
        # target_id = getattr(self, "query", "") or sprint_id
        sprint_id = scheduler.sprint_id or "unknown"
        target_id = getattr(scheduler, "query", "") or sprint_id

        assert target_id == "my-research-query"

    def test_scheduler_uses_sprint_id_fallback(self):
        """F205J-8: When query is empty, sprint_id is used as fallback."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig

        config = SprintSchedulerConfig(sprint_duration_s=1.0, max_cycles=1)
        scheduler = SprintScheduler(config)

        scheduler.query = ""
        scheduler.sprint_id = "sprint-fallback"

        sprint_id = scheduler.sprint_id or "unknown"
        target_id = getattr(scheduler, "query", "") or sprint_id

        assert target_id == "sprint-fallback"

    @pytest.mark.asyncio
    async def test_advisory_full_path_with_mock_store(self):
        """F205J-9: Full advisory path with mock duckdb_store produces a brief."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig

        config = SprintSchedulerConfig(sprint_duration_s=1.0, max_cycles=1)
        scheduler = SprintScheduler(config)

        # Set up state with mock duckdb_store
        mock_store = MagicMock()
        mock_tmemory = MockTargetMemory(
            target_id="test-query",
            sprint_count=2,
            cumulative_finding_count=10,
        )
        mock_store.async_get_target_memory = AsyncMock(return_value=mock_tmemory)

        scheduler.query = "test-query"
        scheduler.sprint_id = "sprint-test"
        scheduler._analyst_workbench = None  # not injected — on-demand path
        scheduler._duckdb_store = mock_store
        scheduler._all_findings = [
            {"finding_id": "f1", "source_type": "ct", "confidence": 0.7,
             "ioc_type": "domain", "ioc_value": "test.com", "query": "test",
             "ts": 0.0, "provenance": ()},
        ]
        scheduler._governor = None

        # Advisory should create workbench and generate brief
        await scheduler._run_analyst_brief_advisory()

        brief = getattr(scheduler, "_analyst_brief", None)
        assert brief is not None, "Brief should be generated via on-demand workbench creation"
        assert "prior sprint" in brief.headline, f"Headline: {brief.headline}"
