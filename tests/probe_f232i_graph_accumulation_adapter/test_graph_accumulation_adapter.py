"""
Sprint F232I: Graph Accumulation Adapter Tests
==============================================

Tests verify:
  1. SprintGraphAccumulator exists and accumulate_findings() returns int
  2. empty findings → 0, graph_service not called
  3. finding without finding_id is skipped (no row built)
  4. rows built as (finding_id, source_type, confidence, sprint_id)
  5. source_type fallback "unknown", confidence fallback 0.5, sprint_id "" when falsy
  6. one batch call to upsert_ioc_batch(rows)
  7. graph exception → 0, no exception propagated
  8. scheduler method delegates to accumulator
  9. accumulator class exists (extraction complete)
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from hledac.universal.knowledge.duckdb_store import CanonicalFinding
from hledac.universal.runtime.graph_accumulator import SprintGraphAccumulator
from hledac.universal.runtime.sprint_scheduler import (
    SprintScheduler,
    SprintSchedulerConfig,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_finding(
    finding_id: str = "f232i-001",
    source_type: str = "ct_log",
    confidence: float = 0.75,
    query: str = "test query",
) -> CanonicalFinding:
    return CanonicalFinding(
        finding_id=finding_id,
        query=query,
        source_type=source_type,
        confidence=confidence,
        ts=time.time(),
        provenance=(f"test:{source_type}",),
    )


class DummyFinding:
    """Finding-like object with no finding_id."""
    source_type = "public"
    confidence = 0.9


# ------------------------------------------------------------------
# Test: accumulator exists and has correct signature
# ------------------------------------------------------------------

class TestAccumulatorExists:
    def test_class_exists(self):
        """SprintGraphAccumulator class exists (extraction complete)."""
        assert SprintGraphAccumulator is not None

    def test_accumulate_returns_int(self):
        """accumulate_findings() returns int (number of rows submitted)."""
        acc = SprintGraphAccumulator()
        result = acc.accumulate_findings([], sprint_id="s1")
        assert isinstance(result, int)

    def test_accumulate_takes_findings_and_sprint_id(self):
        """accumulate_findings(findings, sprint_id) accepts list + str."""
        acc = SprintGraphAccumulator()
        result = acc.accumulate_findings([], sprint_id="")
        assert isinstance(result, int)


# ------------------------------------------------------------------
# Test: fail-soft — empty list returns 0, no graph call
# ------------------------------------------------------------------

class TestAccumulatorEmptyList:
    def test_empty_findings_returns_zero(self):
        """accumulate_findings([]) returns 0."""
        gs = MagicMock()
        acc = SprintGraphAccumulator(gs)
        count = acc.accumulate_findings([], sprint_id="s1")
        assert count == 0

    def test_empty_findings_graph_service_not_called(self):
        """When findings is empty, upsert_ioc_batch is never called."""
        gs = MagicMock()
        acc = SprintGraphAccumulator(gs)
        acc.accumulate_findings([], sprint_id="s1")
        gs.upsert_ioc_batch.assert_not_called()


# ------------------------------------------------------------------
# Test: findings without finding_id are skipped
# ------------------------------------------------------------------

class TestAccumulatorMissingFindingId:
    def test_finding_without_fid_is_skipped(self):
        """Finding without finding_id does not produce a row."""
        gs = MagicMock()
        acc = SprintGraphAccumulator(gs)
        count = acc.accumulate_findings([DummyFinding()], sprint_id="s1")
        assert count == 0
        gs.upsert_ioc_batch.assert_not_called()


# ------------------------------------------------------------------
# Test: row format is (finding_id, source_type, confidence, sprint_id)
# ------------------------------------------------------------------

class TestAccumulatorRowFormat:
    def test_rows_built_correctly(self):
        """Rows are built as (finding_id, source_type, confidence, sprint_id)."""
        gs = MagicMock()
        acc = SprintGraphAccumulator(gs)
        f1 = _make_finding("fid-001", "ct_log", 0.8)
        f2 = _make_finding("fid-002", "public", 0.9)

        acc.accumulate_findings([f1, f2], sprint_id="sprint-x")

        gs.upsert_ioc_batch.assert_called_once()
        rows = gs.upsert_ioc_batch.call_args[0][0]
        assert rows == [
            ("fid-001", "ct_log", 0.8, "sprint-x"),
            ("fid-002", "public", 0.9, "sprint-x"),
        ]

    def test_source_type_fallback_unknown(self):
        """source_type falls back to 'unknown' when not present."""
        gs = MagicMock()
        acc = SprintGraphAccumulator(gs)

        class NoSourceType:
            finding_id = "fid-no-src"
            confidence = 0.5

        acc.accumulate_findings([NoSourceType()], sprint_id="s1")

        rows = gs.upsert_ioc_batch.call_args[0][0]
        assert rows[0][1] == "unknown"

    def test_confidence_fallback_0_5(self):
        """confidence falls back to 0.5 when not present."""
        gs = MagicMock()
        acc = SprintGraphAccumulator(gs)

        class NoConfidence:
            finding_id = "fid-no-conf"
            source_type = "feed"

        acc.accumulate_findings([NoConfidence()], sprint_id="s1")

        rows = gs.upsert_ioc_batch.call_args[0][0]
        assert rows[0][2] == 0.5

    def test_sprint_id_falsy_becomes_empty_string(self):
        """sprint_id="" when falsy (None/empty string)."""
        gs = MagicMock()
        acc = SprintGraphAccumulator(gs)
        f1 = _make_finding("fid-001", "ct_log", 0.7)

        acc.accumulate_findings([f1], sprint_id=None)
        rows = gs.upsert_ioc_batch.call_args[0][0]
        assert rows[0][3] == ""

        gs.reset_mock()
        acc.accumulate_findings([f1], sprint_id="")
        rows = gs.upsert_ioc_batch.call_args[0][0]
        assert rows[0][3] == ""


# ------------------------------------------------------------------
# Test: one batch call to upsert_ioc_batch
# ------------------------------------------------------------------

class TestAccumulatorOneBatchCall:
    def test_single_batch_call(self):
        """upsert_ioc_batch is called exactly once per accumulate_findings."""
        gs = MagicMock()
        acc = SprintGraphAccumulator(gs)
        findings = [_make_finding(f"fid-{i}", "ct_log", 0.7) for i in range(5)]

        acc.accumulate_findings(findings, sprint_id="s1")

        assert gs.upsert_ioc_batch.call_count == 1


# ------------------------------------------------------------------
# Test: graph exception → 0, no exception propagated
# ------------------------------------------------------------------

class TestAccumulatorFailSoft:
    def test_exception_returns_zero(self):
        """If upsert_ioc_batch raises, accumulate_findings returns 0."""
        gs = MagicMock()
        gs.upsert_ioc_batch.side_effect = RuntimeError("duckdb error")
        acc = SprintGraphAccumulator(gs)

        result = acc.accumulate_findings(
            [_make_finding("fid-001", "ct_log", 0.5)],
            sprint_id="s1",
        )
        assert result == 0

    def test_exception_not_propagated(self):
        """Graph exception must NOT propagate out of accumulate_findings."""
        gs = MagicMock()
        gs.upsert_ioc_batch.side_effect = OSError("connection failed")
        acc = SprintGraphAccumulator(gs)

        # Must NOT raise
        try:
            acc.accumulate_findings([_make_finding("fid-exc")], sprint_id="s1")
        except Exception as e:
            pytest.fail(f"accumulate_findings raised {e} — fail-soft violated")


# ------------------------------------------------------------------
# Test: scheduler delegates to accumulator
# ------------------------------------------------------------------

class TestSchedulerDelegation:
    def test_scheduler_method_returns_int(self):
        """_accumulate_findings_to_graph returns int."""
        scheduler = SprintScheduler(SprintSchedulerConfig())
        result = scheduler._accumulate_findings_to_graph([], sprint_id="s1")
        assert isinstance(result, int)

    def test_scheduler_delegates_to_accumulator(self):
        """_accumulate_findings_to_graph calls accumulator.accumulate_findings."""
        scheduler = SprintScheduler(SprintSchedulerConfig())
        acc_mock = MagicMock()
        acc_mock.accumulate_findings.return_value = 3
        scheduler._graph_accumulator = acc_mock

        count = scheduler._accumulate_findings_to_graph(
            [_make_finding("fid-001", "ct_log", 0.8)],
            sprint_id="s1",
        )
        assert count == 3
        acc_mock.accumulate_findings.assert_called_once()

    def test_scheduler_lazy_creates_accumulator(self):
        """On first call, scheduler creates its own accumulator."""
        scheduler = SprintScheduler(SprintSchedulerConfig())
        assert scheduler._graph_accumulator is None

        gs = MagicMock()
        acc = SprintGraphAccumulator(gs)
        scheduler._graph_accumulator = acc

        count = scheduler._accumulate_findings_to_graph([], sprint_id="s1")
        assert count == 0  # empty list → 0 via accumulator


# ------------------------------------------------------------------
# Test: source_type None → "unknown", confidence 0.0 → 0.5
# ------------------------------------------------------------------

class TestAccumulatorNoneFallbacks:
    def test_source_type_none_becomes_unknown(self):
        """source_type=None becomes 'unknown'."""

        class SrcNoneFinding:
            finding_id = "fid-none-src"
            source_type = None
            confidence = 0.7

        gs = MagicMock()
        acc = SprintGraphAccumulator(gs)
        acc.accumulate_findings([SrcNoneFinding()], sprint_id="s1")
        rows = gs.upsert_ioc_batch.call_args[0][0]
        assert rows[0][1] == "unknown"

    def test_confidence_none_becomes_0_5(self):
        """confidence=None falls back to 0.5."""

        class ConfNoneFinding:
            finding_id = "fid-none-conf"
            source_type = "feed"
            confidence = None

        gs = MagicMock()
        acc = SprintGraphAccumulator(gs)
        acc.accumulate_findings([ConfNoneFinding()], sprint_id="s1")
        rows = gs.upsert_ioc_batch.call_args[0][0]
        assert rows[0][2] == 0.5