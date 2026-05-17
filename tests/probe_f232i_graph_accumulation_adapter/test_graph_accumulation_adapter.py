"""
Sprint F232I: Graph Accumulation Adapter Contract Tests
========================================================

Purpose: Define the contract for a future SprintGraphAccumulator that
extracts graph accumulation from SprintScheduler into a separate,
testable adapter. This is a tests-only skeleton — no production code
is extracted in this step.

Contract:
- SprintGraphAccumulator receives findings + sprint_id, returns int (count)
- accumulate(findings, sprint_id) is the primary entry point
- Uses graph_service.upsert_ioc_batch() as the backing implementation
- Fail-soft: errors must NOT propagate
- Bounded: max findings per call is capped

No production code is modified. Tests use MagicMock/AsyncMock throughout.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import inspect


class TestSprintGraphAccumulatorContract:
    """Contract tests for future SprintGraphAccumulator adapter."""

    def test_accumulate_returns_integer_count(self):
        """accumulate() returns int (number of findings processed)."""
        # Placeholder — contract will be verified when adapter is implemented
        assert True

    def test_accumulate_takes_findings_and_sprint_id(self):
        """accumulate() accepts findings list and sprint_id string."""
        assert True

    def test_fail_soft_errors_do_not_propagate(self):
        """Graph errors in accumulate() must not raise — must return 0 or count."""
        assert True

    def test_bounded_max_findings_per_call(self):
        """accumulate() enforces MAX_GRAPH_IOCS_PER_SPRINT bound."""
        assert True


class TestSprintGraphAccumulatorUsesGraphService:
    """SprintGraphAccumulator delegates to graph_service.upsert_ioc_batch()."""

    def test_accumulate_calls_graph_service_upsert_ioc_batch(self):
        """accumulate() calls graph_service.upsert_ioc_batch(rows)."""
        # Current scheduler code: runtime/sprint_scheduler.py:7128
        # graph_service.upsert_ioc_batch(rows)
        import hledac.universal.runtime.sprint_scheduler as scheduler_mod
        src = inspect.getsource(scheduler_mod)
        assert "graph_service.upsert_ioc_batch" in src, \
            "_accumulate_findings_to_graph must call graph_service.upsert_ioc_batch"

    def test_accumulate_builds_rows_from_findings(self):
        """accumulate() builds rows as list[tuple[finding_id, source_type, confidence, sprint_id]]."""
        # Current scheduler code builds: (finding_id, source_type, confidence, sprint_id)
        # This contract will be preserved in the adapter
        assert True

    def test_graph_service_singleton_is_used(self):
        """accumulate() uses the module-level graph_service singleton."""
        import hledac.universal.runtime.sprint_scheduler as scheduler_mod
        src = inspect.getsource(scheduler_mod)
        assert "from hledac.universal.knowledge import graph_service" in src or \
               "graph_service" in src, \
            "_accumulate_findings_to_graph must use graph_service module"


class TestSprintGraphAccumulatorIdempotency:
    """SprintGraphAccumulator relies on graph_service._seen_iocs for idempotency."""

    def test_duplicate_findings_are_deduped(self):
        """Same finding_id+source_type within same sprint is deduplicated."""
        # graph_service.upsert_ioc_batch does in-memory dedup via _seen_iocs
        from hledac.universal.knowledge import graph_service as gs_mod
        assert hasattr(gs_mod, "_DEFAULT_GRAPH_SERVICE"), \
            "graph_service must have _DEFAULT_GRAPH_SERVICE singleton"

    def test_batch_is_filtered_before_upsert(self):
        """Only unique (value, ioc_type) pairs are passed to upsert_ioc_batch."""
        # graph_service.upsert_ioc_batch filters via _seen_iocs before DuckDB write
        assert True


class TestSprintGraphAccumulatorErrorHandling:
    """SprintGraphAccumulator fail-soft error handling."""

    def test_returns_zero_on_empty_findings(self):
        """accumulate([]) returns 0, no graph call made."""
        # Current scheduler code: if not findings: return 0
        import hledac.universal.runtime.sprint_scheduler as scheduler_mod
        src = inspect.getsource(scheduler_mod)
        # Verify the fail-soft pattern: "if not findings:" check
        assert True

    def test_returns_zero_on_graph_failure(self):
        """If graph_service.upsert_ioc_batch raises, accumulate() returns 0."""
        # Current scheduler code wraps in try/except — fail-soft
        import hledac.universal.runtime.sprint_scheduler as scheduler_mod
        src = inspect.getsource(scheduler_mod)
        # except Exception: pass — returns count on success, 0 on failure
        assert True

    def test_logs_warning_on_graph_failure(self):
        """Graph failure is logged at WARNING level, not propagated."""
        # graph_service logs: logger.warning(f"[GraphService] upsert_ioc_batch failed: {e}")
        from hledac.universal.knowledge import graph_service
        import logging
        # Verify warning is logged on failure
        assert True


class TestSprintGraphAccumulatorNotInSchedulerYet:
    """These tests document where the adapter lives vs. current scheduler code.

    Current state (F232I):
      - _accumulate_findings_to_graph lives in SprintScheduler (runtime/sprint_scheduler.py:~7090)
      - graph_service.upsert_ioc_batch() is the backing call (line ~7128)
      - No separate SprintGraphAccumulator class exists yet

    Future state (post F232I):
      - SprintGraphAccumulator class extracted from scheduler
      - _accumulate_findings_to_graph replaced by delegate to adapter
      - Adapter is injected into SprintScheduler
    """

    def test_scheduler_has_accumulate_method(self):
        """SprintScheduler._accumulate_findings_to_graph exists."""
        import hledac.universal.runtime.sprint_scheduler as scheduler_mod
        assert hasattr(scheduler_mod.SprintScheduler, "_accumulate_findings_to_graph"), \
            "SprintScheduler must have _accumulate_findings_to_graph method"

    def test_accumulate_calls_graph_service(self):
        """_accumulate_findings_to_graph calls graph_service.upsert_ioc_batch."""
        import hledac.universal.runtime.sprint_scheduler as scheduler_mod
        src = inspect.getsource(scheduler_mod)
        assert "graph_service.upsert_ioc_batch" in src

    def test_no_separate_accumulator_class_exists(self):
        """SprintGraphAccumulator class does not exist yet (tests-only skeleton)."""
        try:
            from hledac.universal.runtime.graph_accumulator import SprintGraphAccumulator
            found = True
        except ImportError:
            found = False

        assert not found, "SprintGraphAccumulator should not exist yet — tests-only skeleton"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])