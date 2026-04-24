"""
Sprint F198A: Cross-Sprint Graph Accumulation Tests
===================================================

Tests verify:
 1. _accumulate_findings_to_graph() upserts findings to graph_service idempotently
 2. _accumulate_findings_to_graph() is fail-soft (graph error never blocks sprint)
 3. _get_graph_signal() is called at teardown and included in diagnostic report
 4. reset_session() clears session idempotency trackers at sprint reset

Invariant table:
  invariant_1 | _accumulate_findings_to_graph returns 0 for empty list
  invariant_2 | _accumulate_findings_to_graph upserts each finding once (idempotent)
  invariant_3 | _accumulate_findings_to_graph fail-soft on graph_service.upsert_ioc exception
  invariant_4 | _get_graph_signal returns graph_stats dict when graph available
  invariant_5 | _get_graph_signal returns empty dict when graph unavailable
  invariant_6 | _get_graph_signal fail-soft on exception (never raises)
  invariant_7 | reset_session called in _reset_result (graph session cleared per sprint)
  invariant_8 | graph_signal included in _build_diagnostic_report output
"""
from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from hledac.universal.knowledge.duckdb_store import CanonicalFinding
from hledac.universal.runtime.sprint_scheduler import (
    SprintScheduler,
    SprintSchedulerConfig,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_finding(
    finding_id: str = "f198a-test-001",
    source_type: str = "ct_log",
    confidence: float = 0.75,
    query: str = "test query for graph accumulation",
) -> CanonicalFinding:
    return CanonicalFinding(
        finding_id=finding_id,
        query=query,
        source_type=source_type,
        confidence=confidence,
        ts=time.time(),
        provenance=(f"test:{source_type}",),
    )


# ------------------------------------------------------------------
# Test: _accumulate_findings_to_graph — empty list returns 0
# ------------------------------------------------------------------

class TestAccumulateEmptyList:
    def test_invariant_1_empty_list_returns_zero(self):
        """_accumulate_findings_to_graph returns 0 when given no findings."""
        scheduler = SprintScheduler(SprintSchedulerConfig())
        count = scheduler._accumulate_findings_to_graph([], sprint_id="sprint-1")
        assert count == 0


# ------------------------------------------------------------------
# Test: _accumulate_findings_to_graph — idempotent upsert
# ------------------------------------------------------------------

class TestAccumulateIdempotent:
    def test_invariant_2_findings_upserted_once_with_correct_args(self):
        """Each finding is upserted once with source_type as ioc_type and finding_id as value."""
        f1 = _make_finding("fid-001", "ct_log", 0.8)
        f2 = _make_finding("fid-002", "public", 0.9)

        upsert_calls = []

        def mock_upsert(value, ioc_type, confidence, source):
            upsert_calls.append((value, ioc_type, confidence, source))
            return True  # newly upserted

        scheduler = SprintScheduler(SprintSchedulerConfig())

        # Patch the functions directly on the module object
        gs_module = MagicMock()
        gs_module.upsert_ioc = mock_upsert
        with patch.dict(sys.modules, {
            "hledac.universal.knowledge.graph_service": gs_module,
        }):
            count = scheduler._accumulate_findings_to_graph([f1, f2], sprint_id="sprint-x")

        assert count == 2
        assert len(upsert_calls) == 2
        assert upsert_calls[0] == ("fid-001", "ct_log", 0.8, "sprint-x")
        assert upsert_calls[1] == ("fid-002", "public", 0.9, "sprint-x")

    def test_invariant_2b_second_call_is_skipped_by_graph_service_idempotency(self):
        """Second call within same sprint returns 0 because _SEEN_IOCS blocks duplicates."""
        f1 = _make_finding("fid-same", "ct_log", 0.7)

        upsert_calls = []

        def mock_upsert(value, ioc_type, confidence, source):
            upsert_calls.append((value, ioc_type, confidence, source))
            return False  # already seen — graph_service skips

        scheduler = SprintScheduler(SprintSchedulerConfig())

        gs_module = MagicMock()
        gs_module.upsert_ioc = mock_upsert
        with patch.dict(sys.modules, {
            "hledac.universal.knowledge.graph_service": gs_module,
        }):
            count1 = scheduler._accumulate_findings_to_graph([f1], sprint_id="s1")
            count2 = scheduler._accumulate_findings_to_graph([f1], sprint_id="s1")

        assert count1 == 0  # upsert_ioc returned False
        assert count2 == 0  # still 0
        assert len(upsert_calls) == 2  # still called (scheduler doesn't check return)


# ------------------------------------------------------------------
# Test: _accumulate_findings_to_graph — fail-soft
# ------------------------------------------------------------------

class TestAccumulateFailSoft:
    def test_invariant_3_upsert_exception_does_not_raise(self):
        """Graph service exception is caught and swallowed — sprint continues."""
        scheduler = SprintScheduler(SprintSchedulerConfig())

        gs_module = MagicMock()
        gs_module.upsert_ioc = MagicMock(side_effect=RuntimeError("graph connection failed"))
        with patch.dict(sys.modules, {
            "hledac.universal.knowledge.graph_service": gs_module,
        }):
            # Must NOT raise
            count = scheduler._accumulate_findings_to_graph(
                [_make_finding("fid-exc", "ct_log", 0.5)],
                sprint_id="s1",
            )
            assert count == 0  # no successes

    def test_accumulate_skips_findings_without_finding_id(self):
        """Findings without finding_id are skipped without error."""
        calls = []

        class NoIdFinding:
            source_type = "ct_log"
            confidence = 0.8

        scheduler = SprintScheduler(SprintSchedulerConfig())

        def track_upsert(*args, **kwargs):
            calls.append(args)

        gs_module = MagicMock()
        gs_module.upsert_ioc = track_upsert
        with patch.dict(sys.modules, {
            "hledac.universal.knowledge.graph_service": gs_module,
        }):
            count = scheduler._accumulate_findings_to_graph(
                [NoIdFinding()],
                sprint_id="s1",
            )
        assert count == 0
        assert len(calls) == 0


# ------------------------------------------------------------------
# Test: _get_graph_signal
# ------------------------------------------------------------------

class TestGetGraphSignal:
    def test_invariant_4_returns_stats_dict_when_graph_available(self):
        """Returns {graph_nodes, graph_edges, graph_pgq_available} when graph responds."""
        scheduler = SprintScheduler(SprintSchedulerConfig())

        gs_module = MagicMock()
        gs_module.graph_stats.return_value = {"nodes": 42, "edges": 137, "pgq_available": True}
        with patch.dict(sys.modules, {
            "hledac.universal.knowledge.graph_service": gs_module,
        }):
            signal = scheduler._get_graph_signal()

        assert signal == {"graph_nodes": 42, "graph_edges": 137, "graph_pgq_available": True}

    def test_invariant_5_returns_empty_when_graph_unavailable(self):
        """Returns empty dict when graph_service returns empty/falsy."""
        scheduler = SprintScheduler(SprintSchedulerConfig())

        gs_module = MagicMock()
        gs_module.graph_stats.return_value = {}
        with patch.dict(sys.modules, {
            "hledac.universal.knowledge.graph_service": gs_module,
        }):
            signal = scheduler._get_graph_signal()

        assert signal == {}

    def test_invariant_6_graph_stats_exception_is_swallowed(self):
        """Exception from graph_stats() is caught — never propagates."""
        scheduler = SprintScheduler(SprintSchedulerConfig())

        gs_module = MagicMock()
        gs_module.graph_stats.side_effect = OSError("duckdb corrupted")
        with patch.dict(sys.modules, {
            "hledac.universal.knowledge.graph_service": gs_module,
        }):
            # Must NOT raise
            signal = scheduler._get_graph_signal()
            assert signal == {}


# ------------------------------------------------------------------
# Test: reset_session called in _reset_result
# ------------------------------------------------------------------

class TestResetSession:
    def test_invariant_7_reset_session_called_in_reset_result(self):
        """_reset_result calls graph_service.reset_session() to clear idempotency trackers."""
        scheduler = SprintScheduler(SprintSchedulerConfig())

        gs_module = MagicMock()
        with patch.dict(sys.modules, {
            "hledac.universal.knowledge.graph_service": gs_module,
        }):
            scheduler._reset_result()
            gs_module.reset_session.assert_called_once()


# ------------------------------------------------------------------
# Test: graph_signal in diagnostic report
# ------------------------------------------------------------------

class TestGraphSignalInReport:
    def test_invariant_8_graph_signal_in_diagnostic_report(self):
        """_build_diagnostic_report includes graph_signal dict when graph available."""
        scheduler = SprintScheduler(SprintSchedulerConfig())

        # Minimal lifecycle mock
        mock_lifecycle = MagicMock()
        mock_phase = MagicMock()
        mock_phase.name = "TEARDOWN"
        mock_lifecycle.current_phase = mock_phase
        mock_lifecycle.snapshot.return_value = {}

        gs_module = MagicMock()
        gs_module.graph_stats.return_value = {"nodes": 10, "edges": 50, "pgq_available": False}
        with patch.dict(sys.modules, {
            "hledac.universal.knowledge.graph_service": gs_module,
        }):
            report = scheduler._build_diagnostic_report(mock_lifecycle)

        assert "graph_signal" in report
        assert report["graph_signal"]["graph_nodes"] == 10
        assert report["graph_signal"]["graph_edges"] == 50

    def test_invariant_8b_no_graph_signal_when_graph_unavailable(self):
        """graph_signal key absent from report when graph_stats returns empty."""
        scheduler = SprintScheduler(SprintSchedulerConfig())

        mock_lifecycle = MagicMock()
        mock_phase = MagicMock()
        mock_phase.name = "TEARDOWN"
        mock_lifecycle.current_phase = mock_phase
        mock_lifecycle.snapshot.return_value = {}

        gs_module = MagicMock()
        gs_module.graph_stats.return_value = {}
        with patch.dict(sys.modules, {
            "hledac.universal.knowledge.graph_service": gs_module,
        }):
            report = scheduler._build_diagnostic_report(mock_lifecycle)

        # Empty dict — key should be absent (not included if empty)
        assert "graph_signal" not in report
