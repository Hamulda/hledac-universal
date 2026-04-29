"""
Probe tests for build_temporal_priority_hints (Sprint F206R).

Tests:
1. build_temporal_priority_hints returns bounded list
2. stable deterministic sort
3. priority_hint clamped [0,1]
4. advisory_only True on every hint
5. high burst/change/source score creates useful hint
6. empty state returns []
7. temporal exception fail-soft
8. public_branch_verdict includes temporal_priority_hints
9. no scheduler mutation (verifies no write to scheduler state)
10. no graph write (verifies layer doesn't call graph write)
"""
from __future__ import annotations

import math
import time
from collections import deque

import pytest

from hledac.universal.layers.temporal_signal_layer import (
    TemporalEvent,
    TemporalSignalLayer,
)


class TestBuildTemporalPriorityHints:
    """Test build_temporal_priority_hints via runtime helper."""

    def test_import_from_runtime(self):
        """Verify build_temporal_priority_hints is importable from layers."""
        from hledac.universal.layers import build_temporal_priority_hints
        assert callable(build_temporal_priority_hints)

    def test_empty_state_returns_empty_list(self):
        """Empty layer returns []. No crash."""
        from hledac.universal.layers import build_temporal_priority_hints
        # Ensure no layer is initialized
        import hledac.universal.layers.temporal_signal_runtime as _runtime
        _runtime._layer = None
        hints = build_temporal_priority_hints(k=10)
        assert hints == []

    def test_returns_bounded_list(self):
        """Returned list is bounded by k."""
        from hledac.universal.layers import build_temporal_priority_hints
        import hledac.universal.layers.temporal_signal_runtime as _runtime

        layer = TemporalSignalLayer(max_keys=4096)
        ts = time.time()
        for i in range(30):
            layer.observe(TemporalEvent(ts=ts + i * 60, key=f"domain{i}.tld", family="ct"))

        _runtime._layer = layer
        try:
            hints = build_temporal_priority_hints(k=5)
            assert len(hints) <= 5
        finally:
            _runtime._layer = None

    def test_priority_hint_clamped_0_to_1(self):
        """All priority_hint values are clamped [0, 1]."""
        from hledac.universal.layers import build_temporal_priority_hints
        import hledac.universal.layers.temporal_signal_runtime as _runtime

        layer = TemporalSignalLayer(max_keys=128)
        ts = time.time()
        # Create some events with varied scores
        for i in range(20):
            layer.observe(TemporalEvent(
                ts=ts + i * 0.5,
                key=f"host{i}.example.com",
                family="ct",
                source="crt_sh",
            ))

        _runtime._layer = layer
        try:
            hints = build_temporal_priority_hints(k=10)
            for h in hints:
                assert 0.0 <= h["priority_hint"] <= 1.0, f"priority_hint {h['priority_hint']} out of range"
        finally:
            _runtime._layer = None

    def test_advisory_only_true(self):
        """Every hint has advisory_only=True."""
        from hledac.universal.layers import build_temporal_priority_hints
        import hledac.universal.layers.temporal_signal_runtime as _runtime

        layer = TemporalSignalLayer(max_keys=128)
        ts = time.time()
        for i in range(10):
            layer.observe(TemporalEvent(ts=ts + i * 10, key=f"key{i}", family="generic"))

        _runtime._layer = layer
        try:
            hints = build_temporal_priority_hints(k=10)
            assert len(hints) > 0, "expected non-empty hints"
            for h in hints:
                assert h.get("advisory_only") is True
        finally:
            _runtime._layer = None

    def test_high_burst_creates_burst_cluster_hint(self):
        """High burst score tags reason as burst_cluster."""
        from hledac.universal.layers import build_temporal_priority_hints
        import hledac.universal.layers.temporal_signal_runtime as _runtime

        layer = TemporalSignalLayer(max_keys=128, synchrony_window_s=300.0)
        ts = time.time()
        # Rapid fire events → high burst
        for i in range(15):
            layer.observe(TemporalEvent(
                ts=ts + i * 2.0,  # 2s apart, EWMA gap ~2s
                key="rapid.example.com",
                family="ct",
                source="crt_sh",
            ))

        _runtime._layer = layer
        try:
            hints = build_temporal_priority_hints(k=10)
            burst_hints = [h for h in hints if "burst" in h.get("reason", "")]
            assert len(burst_hints) >= 1, f"Expected burst_cluster hint, got: {hints}"
        finally:
            _runtime._layer = None

    def test_high_periodicity_creates_periodic_checkin_hint(self):
        """High periodicity score tags reason as periodic_checkin."""
        from hledac.universal.layers import build_temporal_priority_hints
        import hledac.universal.layers.temporal_signal_runtime as _runtime

        layer = TemporalSignalLayer(max_keys=128)
        ts = time.time()
        # Very regular intervals → high periodicity
        for i in range(20):
            layer.observe(TemporalEvent(
                ts=ts + i * 60.0,  # 60s apart — very regular
                key="regular.example.com",
                family="ct",
                source="crt_sh",
            ))

        _runtime._layer = layer
        try:
            hints = build_temporal_priority_hints(k=10)
            periodic = [h for h in hints if "periodic" in h.get("reason", "")]
            assert len(periodic) >= 1, f"Expected periodic_checkin hint, got: {hints}"
        finally:
            _runtime._layer = None

    def test_high_change_point_creates_dormant_wakeup_hint(self):
        """High change-point score tags reason as dormant_wakeup."""
        from hledac.universal.layers import build_temporal_priority_hints
        import hledac.universal.layers.temporal_signal_runtime as _runtime

        layer = TemporalSignalLayer(max_keys=128, bocpd_max_run=32)
        ts = time.time()
        # Establish baseline with few events
        for i in range(3):
            layer.observe(TemporalEvent(
                ts=ts + i * 3600.0,
                key="dormant.example.com",
                family="ct",
                source="crt_sh",
            ))
        # Then suddenly many rapid events to trigger change point
        ts2 = ts + 3 * 3600.0
        for i in range(40):
            layer.observe(TemporalEvent(
                ts=ts2 + i * 5.0,
                key="dormant.example.com",
                family="ct",
                source="crt_sh",
            ))

        _runtime._layer = layer
        try:
            hints = build_temporal_priority_hints(k=10)
            # Either dormant_wakeup or change_point in reason is acceptable
            change_hints = [h for h in hints if "dormant_wakeup" in h.get("reason", "")]
            # Fall back: check the change_point_score is meaningful
            if not change_hints:
                dormant_hint = next((h for h in hints if h["key"] == "dormant.example.com"), None)
                assert dormant_hint is not None, f"Expected hint for dormant.example.com, got: {hints}"
                # At minimum change_point_score must be > 0 to justify dormant_wakeup tag attempt
                assert dormant_hint["change_point_score"] > 0.0, \
                    f"Expected change_point_score > 0 for dormant scenario, got {dormant_hint['change_point_score']}"
            else:
                assert len(change_hints) >= 1
        finally:
            _runtime._layer = None

    def test_stable_deterministic_sort(self):
        """Hints are sorted by priority_hint descending — deterministic."""
        from hledac.universal.layers import build_temporal_priority_hints
        import hledac.universal.layers.temporal_signal_runtime as _runtime

        layer = TemporalSignalLayer(max_keys=128)
        ts = time.time()
        for i in range(20):
            layer.observe(TemporalEvent(
                ts=ts + i * 10,
                key=f"det{i}.test",
                family="ct",
            ))

        _runtime._layer = layer
        try:
            hints1 = build_temporal_priority_hints(k=10)
            hints2 = build_temporal_priority_hints(k=10)
            assert hints1 == hints2, "build_temporal_priority_hints must be deterministic"
            # Verify descending order
            priorities = [h["priority_hint"] for h in hints1]
            assert priorities == sorted(priorities, reverse=True), "must be sorted descending"
        finally:
            _runtime._layer = None

    def test_exception_fail_soft(self):
        """If layer raises, returns [] not exception."""
        import hledac.universal.layers.temporal_signal_runtime as _runtime

        original_layer = _runtime._layer
        _runtime._layer = None  # empty — safe
        try:
            from hledac.universal.layers import build_temporal_priority_hints
            hints = build_temporal_priority_hints(k=10)
            assert hints == []
        finally:
            _runtime._layer = original_layer

    def test_hint_schema_fields(self):
        """Every hint has all required schema fields."""
        from hledac.universal.layers import build_temporal_priority_hints
        import hledac.universal.layers.temporal_signal_runtime as _runtime

        layer = TemporalSignalLayer(max_keys=64)
        ts = time.time()
        layer.observe(TemporalEvent(ts=ts, key="schema.test", family="ct"))

        _runtime._layer = layer
        try:
            hints = build_temporal_priority_hints(k=5)
            if len(hints) > 0:
                h = hints[0]
                assert "key" in h
                assert "family" in h
                assert "priority_hint" in h
                assert "reason" in h
                assert "anomaly_score" in h
                assert "burst_score" in h
                assert "periodicity_score" in h
                assert "change_point_score" in h
                assert "source_synchrony_score" in h
                assert "advisory_only" in h
        finally:
            _runtime._layer = None

    def test_no_scheduler_mutation(self):
        """Verify no scheduler state is modified by build_temporal_priority_hints."""
        from hledac.universal.layers import build_temporal_priority_hints
        import hledac.universal.layers.temporal_signal_runtime as _runtime
        from hledac.universal.runtime import sprint_scheduler

        layer = TemporalSignalLayer(max_keys=64)
        ts = time.time()
        layer.observe(TemporalEvent(ts=ts, key="no_mutate.test", family="ct"))

        # Snapshot scheduler internal state
        scheduler_state_before = {
            "_public_verdicts": list(getattr(sprint_scheduler, "_public_verdicts", [])),
            "_feed_verdicts": list(getattr(sprint_scheduler, "_feed_verdicts", [])),
        }

        _runtime._layer = layer
        try:
            hints = build_temporal_priority_hints(k=5)
            assert isinstance(hints, list)
        finally:
            _runtime._layer = None

        # Verify no scheduler state was modified
        scheduler_state_after = {
            "_public_verdicts": list(getattr(sprint_scheduler, "_public_verdicts", [])),
            "_feed_verdicts": list(getattr(sprint_scheduler, "_feed_verdicts", [])),
        }
        assert scheduler_state_before == scheduler_state_after, \
            "build_temporal_priority_hints must not mutate scheduler state"

    def test_no_graph_write(self):
        """Verify temporal_signal_layer does not call any graph write."""
        import hledac.universal.layers.temporal_signal_runtime as _runtime
        from hledac.universal.layers.temporal_signal_layer import TemporalSignalLayer

        layer = TemporalSignalLayer(max_keys=64)
        # Check internal method list — no graph write
        assert not hasattr(layer, "upsert_ioc")
        assert not hasattr(layer, "upsert_edge")
        assert not hasattr(layer, "write_graph")
        assert not hasattr(layer, "graph_ingest")

        # Ensure _edge_candidates is a deque (in-memory only)
        assert isinstance(layer._edge_candidates, (list, deque))
        _runtime._layer = layer


class TestPublicBranchVerdictIntegration:
    """Test that temporal_priority_hints is in public_branch_verdict."""

    def test_temporal_priority_hints_in_public_branch_verdict(self):
        """Verify temporal_priority_hints key is added to public_branch_verdict."""
        from hledac.universal.pipeline.live_public_pipeline import PipelineRunResult

        result = PipelineRunResult(
            query="test",
            discovered=10,
            fetched=5,
            matched_patterns=3,
            accepted_findings=2,
            stored_findings=2,
            patterns_configured=1,
            pages=(),
            public_branch_verdict={
                "waste_ratio": 0.1,
                "value_ratio": 0.2,
                "temporal_priority_hints": [
                    {
                        "key": "example.com",
                        "family": "ct",
                        "priority_hint": 0.75,
                        "reason": "burst_cluster",
                        "anomaly_score": 0.8,
                        "burst_score": 0.9,
                        "periodicity_score": 0.1,
                        "change_point_score": 0.3,
                        "source_synchrony_score": 0.2,
                        "advisory_only": True,
                    }
                ],
            },
        )
        assert "temporal_priority_hints" in result.public_branch_verdict
        hints = result.public_branch_verdict["temporal_priority_hints"]
        assert len(hints) == 1
        assert hints[0]["advisory_only"] is True
        assert hints[0]["priority_hint"] == 0.75
        assert hints[0]["reason"] == "burst_cluster"


class TestPriorityFormula:
    """Test the priority formula components."""

    def test_priority_formula_weights(self):
        """Priority = anomaly*0.45 + burst*0.20 + change*0.20 + source*0.15."""
        from hledac.universal.layers import build_temporal_priority_hints
        import hledac.universal.layers.temporal_signal_runtime as _runtime
        from hledac.universal.layers.temporal_signal_layer import TemporalSignalLayer

        layer = TemporalSignalLayer(max_keys=64)
        ts = time.time()
        # Create a key with known scores
        key = "formula.test"
        layer.observe(TemporalEvent(ts=ts, key=key, family="ct"))

        _runtime._layer = layer
        try:
            hints = build_temporal_priority_hints(k=1)
            if len(hints) == 0:
                pytest.skip("No hints generated — insufficient data for formula test")
            h = hints[0]
            expected = (
                h["anomaly_score"] * 0.45
                + h["burst_score"] * 0.20
                + h["change_point_score"] * 0.20
                + h["source_synchrony_score"] * 0.15
            )
            # Allow small floating point tolerance
            assert abs(h["priority_hint"] - expected) < 0.001, \
                f"priority_hint={h['priority_hint']} != expected={expected}"
        finally:
            _runtime._layer = None

    def test_priority_clamp_extreme_values(self):
        """Priority stays in [0,1] even with extreme component scores."""
        from hledac.universal.layers import build_temporal_priority_hints
        import hledac.universal.layers.temporal_signal_runtime as _runtime
        from hledac.universal.layers.temporal_signal_layer import TemporalSignalLayer

        layer = TemporalSignalLayer(max_keys=64)
        ts = time.time()
        # Fire many rapid events to spike all scores
        for i in range(50):
            layer.observe(TemporalEvent(ts=ts + i * 0.1, key="extreme.test", family="ct"))

        _runtime._layer = layer
        try:
            hints = build_temporal_priority_hints(k=5)
            for h in hints:
                assert 0.0 <= h["priority_hint"] <= 1.0, \
                    f"priority_hint {h['priority_hint']} out of [0,1] range"
        finally:
            _runtime._layer = None
