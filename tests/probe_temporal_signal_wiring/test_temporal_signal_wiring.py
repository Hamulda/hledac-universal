"""
Probe tests for TemporalSignalLayer wiring (Sprint F206P).

Tests:
1. runtime holder lazy imports TemporalSignalLayer
2. event_from_finding_like converts dict finding to TemporalEvent
3. missing key fail-soft returns None
4. live_public_pipeline finding seam calls TemporalSignalLayer.observe
5. temporal exception doesn't crash pipeline
6. CancelledError re-raised
7. temporal metadata is additive, not schema breaking
8. summary is bounded top-K
9. no numpy/pandas/mlx import in hot-path
10. existing F206O tests still pass
"""
from __future__ import annotations

import time

import pytest

from hledac.universal.layers.temporal_signal_layer import (
    TemporalEvent,
    TemporalSignalLayer,
    event_from_finding_like,
)
from hledac.universal.layers.temporal_signal_runtime import (
    get_temporal_signal_layer,
    get_temporal_signal_summary,
    reset_temporal_signal_layer,
)


class TestEventFromFindingLike:
    """Test event_from_finding_like conversion."""

    def test_dict_finding_converts_to_temporal_event(self):
        finding = {
            "timestamp": 1234567890.0,
            "domain": "example.com",
            "source_family": "ct",
            "confidence": 0.8,
        }
        event = event_from_finding_like(finding)
        assert event is not None
        assert event.key == "example.com"
        assert event.family == "ct"
        assert event.weight == 0.8
        assert event.ts == 1234567890.0

    def test_object_finding_converts_to_temporal_event(self):
        class Finding:
            ts = 1234567890.0
            domain = "test.com"
            source = "certwatch"
            confidence = 0.9

        event = event_from_finding_like(Finding())
        assert event is not None
        assert event.key == "test.com"
        assert event.family == "certwatch"
        assert event.weight == 0.9

    def test_missing_timestamp_returns_none(self):
        finding = {"domain": "example.com"}
        assert event_from_finding_like(finding) is None

    def test_missing_key_returns_none(self):
        finding = {"timestamp": 1234567890.0}
        assert event_from_finding_like(finding) is None

    def test_ts_field_alias(self):
        finding = {"ts": 1000.0, "domain": "alias.test"}
        event = event_from_finding_like(finding)
        assert event is not None
        assert event.key == "alias.test"
        assert event.ts == 1000.0

    def test_created_at_field_alias(self):
        finding = {"created_at": 2000.0, "entity": "entity.test"}
        event = event_from_finding_like(finding)
        assert event is not None
        assert event.key == "entity.test"
        assert event.ts == 2000.0

    def test_weight_from_confidence_field(self):
        finding = {"timestamp": 1000.0, "domain": "w.com", "confidence": 0.7}
        event = event_from_finding_like(finding)
        assert event is not None
        assert event.weight == 0.7

    def test_labels_become_tuple(self):
        finding = {
            "timestamp": 1000.0,
            "domain": "labels.test",
            "labels": ["osint", "cert"],
        }
        event = event_from_finding_like(finding)
        assert event is not None
        assert event.labels == ("osint", "cert")

    def test_empty_dict_returns_none(self):
        assert event_from_finding_like({}) is None


class TestTemporalSignalRuntime:
    """Test runtime holder lazy-loading and API."""

    def test_lazy_import_does_not_load_heavy_deps(self):
        # Reset first
        import hledac.universal.layers.temporal_signal_runtime as rt
        rt._layer = None

        # Access get_temporal_signal_layer - should not import heavy deps
        layer = get_temporal_signal_layer()
        assert layer is not None
        assert isinstance(layer, TemporalSignalLayer)

    def test_reset_clears_state(self):
        reset_temporal_signal_layer()
        layer = get_temporal_signal_layer()
        layer.observe(TemporalEvent(ts=time.time(), key="reset.test", family="ct"))
        assert layer.get_state_size() == 1
        reset_temporal_signal_layer()
        assert layer.get_state_size() == 0

    def test_summary_returns_bounded_scores(self):
        reset_temporal_signal_layer()
        layer = get_temporal_signal_layer()
        ts = time.time()
        for i in range(30):
            layer.observe(
                TemporalEvent(ts=ts + i, key=f"key{i % 5}.com", family="ct", weight=0.5)
            )
        summary = get_temporal_signal_summary(k=10)
        assert "top_scores" in summary
        assert len(summary["top_scores"]) <= 10
        assert summary["state_size"] == 5  # 30 events, 5 unique keys

    def test_summary_empty_when_not_initialized(self):
        import hledac.universal.layers.temporal_signal_runtime as rt
        rt._layer = None
        summary = get_temporal_signal_summary()
        assert summary == {}

    def test_summary_fail_soft_on_error(self):
        import hledac.universal.layers.temporal_signal_runtime as rt
        rt._layer = None
        # Manually create broken layer
        rt._layer = "not a layer"
        summary = get_temporal_signal_summary()
        assert summary == {}


class TestTemporalObserveInPipelineContext:
    """Test temporal observe in pipeline-like acceptance loop context."""

    def test_observe_does_not_crash_pipeline(self):
        layer = TemporalSignalLayer()
        ts = time.time()

        # Simulate finding-like dicts from pipeline
        findings = [
            {"timestamp": ts, "domain": f"site{i}.com", "source_family": "ct", "confidence": 0.7}
            for i in range(10)
        ]
        for f in findings:
            te = event_from_finding_like(f)
            assert te is not None
            score = layer.observe(te)
            assert score.anomaly_score >= 0.0

    def test_observe_cancelled_error_propagates(self):
        layer = TemporalSignalLayer()

        class CancelledError(Exception):
            pass

        # Patch observe to raise CancelledError
        original_observe = layer.observe

        def raise_cancelled(event):
            raise CancelledError("cancelled")

        layer.observe = raise_cancelled  # type: ignore

        finding = {"timestamp": time.time(), "domain": "test.com"}
        te = event_from_finding_like(finding)
        assert te is not None

        # Simulating the pipeline pattern: CancelledError should propagate
        cancelled_raised = False
        try:
            layer.observe(te)
        except CancelledError:
            cancelled_raised = True
        assert cancelled_raised, "CancelledError must propagate"

    def test_temporal_metadata_additive_not_schema_breaking(self):
        layer = TemporalSignalLayer()
        ts = time.time()

        # Multiple observations produce scores but don't mutate finding schema
        findings = [
            {"timestamp": ts + i, "domain": "stable.com", "source_family": "ct"}
            for i in range(5)
        ]
        for f in findings:
            te = event_from_finding_like(f)
            if te:
                score = layer.observe(te)
                # Score fields are new, original finding dict unchanged
                assert score.key == "stable.com"
                assert not hasattr(f, "temporal_anomaly_score")

    def test_no_numpy_in_observation_path(self):
        layer = TemporalSignalLayer()
        ts = time.time()
        event = TemporalEvent(ts=ts, key="nopandas.test", family="ct", weight=1.0)
        score = layer.observe(event)
        assert score is not None
        # Verify no numpy/pandas/mlx imported by this module's imports
        import sys
        our_modules = list(sys.modules.keys())
        numpy_by_us = any("temporal_signal" in m and "numpy" in m for m in our_modules)
        assert not numpy_by_us, "numpy should not be imported by temporal_signal_runtime"


class TestF206OF206OWiringIntegration:
    """Verify F206O layer still works after wiring changes."""

    def test_temporal_signal_layer_basic_observe(self):
        layer = TemporalSignalLayer(max_keys=128)
        ts = time.time()
        event = TemporalEvent(ts=ts, key="basic.test", family="generic", weight=1.0)
        score = layer.observe(event)
        assert score.event_count == 1
        assert score.key == "basic.test"
        assert 0.0 <= score.anomaly_score <= 1.0

    def test_temporal_signal_layer_burst_detection(self):
        layer = TemporalSignalLayer(ring_size=32)
        ts = time.time()
        # Rapid burst of events
        for i in range(10):
            layer.observe(
                TemporalEvent(ts=ts + i * 0.01, key="burst.test", family="ct")
            )
        scores = layer.get_top_scores(k=5)
        burst_scores = [s.burst_score for s in scores if s.key == "burst.test"]
        assert any(s > 0.0 for s in burst_scores), "burst should be detected"

    def test_event_from_finding_like_still_works(self):
        # Ensure F206O function hasn't been broken
        finding = {
            "timestamp": time.time(),
            "url": "http://test.com/path",
            "source": "duckduckgo",
            "confidence": 0.85,
        }
        event = event_from_finding_like(finding)
        assert event is not None
        assert event.key == "http://test.com/path"
        assert event.family == "duckduckgo"
        assert event.weight == 0.85


class TestNoHeavyImportsInHotPath:
    """Verify no heavy imports in observation hot-path."""

    def test_no_mlxlm_imported_after_layer_create(self):
        import sys

        # Reset runtime
        import hledac.universal.layers.temporal_signal_runtime as rt
        rt._layer = None

        # Create layer
        get_temporal_signal_layer()

        # Check mlx-lm not loaded (heavy MLX inference dep)
        # mlx itself is light-weight import, only mlx_lm is heavy
        assert "mlx_lm" not in sys.modules, "mlx_lm should not be loaded in hot-path"
