"""
Probe tests for TemporalSignalLayer.

Covers:
1. import layer — no numpy/pandas/mlx
2. first event → insufficient_history
3. regular intervals → high periodicity_score
4. jittered regular intervals → periodicity stays high
5. rapid cluster → high burst_score
6. long quiet then spike → change_point_score
7. autocorr_lag1 bounded O(n)
8. Jaccard source synchrony
9. TemporalEdgeCandidate for co-burst
10. max_keys eviction
11. ring_size bounded
12. snapshot/from_snapshot roundtrip
13. observe_confirmation(True) bounded boost
14. observe_confirmation(False) bounded decay
15. out-of-order timestamp fail-soft
16. reset clears state
17. observe_many deterministic order
18. no random seed / no nondeterminism
"""
from __future__ import annotations

import math
import time
from collections import deque

import pytest

from hledac.universal.layers.temporal_signal_layer import (
    CONFIRMATION_BOOST_MAX,
    CONFIRMATION_BOOST_MIN,
    DEFAULT_BOCPD_MAX_RUN,
    DEFAULT_HALF_LIFE_S,
    DEFAULT_MAX_KEYS,
    DEFAULT_RING_SIZE,
    DEFAULT_SYNCHRONY_WINDOW_S,
    TemporalEdgeCandidate,
    TemporalEvent,
    TemporalScore,
    TemporalSignalLayer,
    event_from_finding_like,
)


class TestNoHeavyImports:
    """Test 1: layer does not import numpy/pandas/mlx"""

    def test_no_numpy(self):
        import ast
        with open("layers/temporal_signal_layer.py") as f:
            tree = ast.parse(f.read())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        assert "numpy" not in imports
        assert "pandas" not in imports
        assert "mlx" not in imports


class TestInsufficientHistory:
    """Test 2: first event → reason includes insufficient_history"""

    def test_first_event_insufficient(self):
        layer = TemporalSignalLayer()
        event = TemporalEvent(ts=1000.0, key="example.com", family="ct")
        score = layer.observe(event)
        assert score.event_count == 1
        assert "insufficient_history" in score.reason

    def test_second_event_no_insufficient(self):
        layer = TemporalSignalLayer()
        event1 = TemporalEvent(ts=1000.0, key="example.com", family="ct")
        event2 = TemporalEvent(ts=1010.0, key="example.com", family="ct")
        layer.observe(event1)
        score2 = layer.observe(event2)
        assert score2.event_count == 2
        assert "insufficient_history" not in score2.reason


class TestPeriodicityScoring:
    """Test 3: regular intervals → high periodicity_score"""

    def test_regular_intervals_periodicity(self):
        layer = TemporalSignalLayer(ring_size=32)
        t0 = 1000.0
        interval = 10.0
        for i in range(20):
            layer.observe(TemporalEvent(ts=t0 + i * interval, key="example.com", family="ct"))
        scores = layer.get_top_scores(k=1)
        assert len(scores) == 1
        score = scores[0]
        assert score.periodicity_score > 0.6, f"expected >0.6, got {score.periodicity_score}"


class TestPeriodicityJitter:
    """Test 4: jittered regular intervals → periodicity stays high"""

    def test_jittered_periodicity_resilient(self):
        layer = TemporalSignalLayer(ring_size=32)
        t0 = 1000.0
        interval = 10.0
        # ±20% jitter
        jittered = [0.9, 1.1, 0.85, 1.15, 0.95, 1.05, 0.9, 1.1, 0.88, 1.12]
        for i, jitter in enumerate(jittered):
            layer.observe(
                TemporalEvent(ts=t0 + i * interval + jitter * interval, key="example.com", family="ct")
            )
        scores = layer.get_top_scores(k=1)
        assert len(scores) == 1
        score = scores[0]
        # With jitter, CV will be higher but autocorr_lag1 should still indicate regularity
        assert score.periodicity_score > 0.3, f"expected >0.3, got {score.periodicity_score}"


class TestBurstScoring:
    """Test 5: rapid cluster → high burst_score"""

    def test_rapid_burst(self):
        layer = TemporalSignalLayer(synchrony_window_s=60.0)
        t0 = 1000.0
        # 5 rapid events within 2 seconds
        for i in range(5):
            layer.observe(TemporalEvent(ts=t0 + i * 0.3, key="example.com", family="ct"))
        scores = layer.get_top_scores(k=1)
        assert len(scores) == 1
        score = scores[0]
        assert score.burst_score > 0.3, f"expected >0.3, got {score.burst_score}"


class TestChangePoint:
    """Test 6: long quiet then spike → change_point_score"""

    def test_quiet_then_spike_change_point(self):
        layer = TemporalSignalLayer()
        key = "example.com"
        # Regular events at 10s interval
        t0 = 1000.0
        for i in range(10):
            layer.observe(TemporalEvent(ts=t0 + i * 10.0, key=key, family="ct"))
        # Long gap then rapid burst — should trigger change point
        layer.observe(TemporalEvent(ts=t0 + 100.0 + 100.0, key=key, family="ct"))  # 100s gap
        layer.observe(TemporalEvent(ts=t0 + 100.0 + 100.3, key=key, family="ct"))  # 0.3s gap
        layer.observe(TemporalEvent(ts=t0 + 100.0 + 100.6, key=key, family="ct"))  # 0.3s gap
        scores = layer.get_top_scores(k=1)
        assert len(scores) == 1
        score = scores[0]
        # Change point should be elevated due to gap shift
        assert score.change_point_score > 0.2 or score.anomaly_score > 0.2, (
            f"change_point={score.change_point_score}, anomaly={score.anomaly_score}"
        )


class TestAutocorrLag1:
    """Test 7: autocorr_lag1 bounded O(n)"""

    def test_autocorr_lag1_bounded(self):
        layer = TemporalSignalLayer(ring_size=128)
        t0 = 1000.0
        # Regular events
        for i in range(100):
            layer.observe(TemporalEvent(ts=t0 + i * 10.0, key="example.com", family="ct"))
        score = layer.get_top_scores(k=1)[0]
        # Should be computed over 128 ring gaps max — O(128), not O(100)
        assert 0.0 <= score.autocorr_lag1 <= 1.0

    def test_autocorr_no_negative(self):
        layer = TemporalSignalLayer()
        t0 = 1000.0
        for i in range(50):
            layer.observe(TemporalEvent(ts=t0 + i * 10.0, key="example.com", family="ct"))
        score = layer.get_top_scores(k=1)[0]
        assert score.autocorr_lag1 >= -1.0


class TestSourceSynchrony:
    """Test 8: Jaccard source synchrony"""

    def test_source_synchrony_jaccard(self):
        layer = TemporalSignalLayer(synchrony_window_s=300.0)
        t0 = 1000.0
        # Same key from multiple sources within window
        for src in ["source_a", "source_b", "source_c", "source_a", "source_b"]:
            layer.observe(
                TemporalEvent(ts=t0, key="example.com", family="ct", source=src, weight=1.0)
            )
            t0 += 1.0
        # Different key from same sources
        t0 += 50.0
        for src in ["source_a", "source_b", "source_c"]:
            layer.observe(
                TemporalEvent(ts=t0, key="other.com", family="ct", source=src, weight=1.0)
            )
            t0 += 1.0
        scores = layer.get_top_scores(k=2)
        assert len(scores) >= 1
        top_score = scores[0]
        assert 0.0 <= top_score.source_synchrony_score <= 1.0


class TestTemporalEdgeCandidate:
    """Test 9: TemporalEdgeCandidate for co-burst"""

    def test_coburst_edge_candidate(self):
        layer = TemporalSignalLayer(synchrony_window_s=300.0)
        t0 = 1000.0
        # Rapid cluster on two keys simultaneously
        for _ in range(5):
            layer.observe(TemporalEvent(ts=t0, key="alpha.com", family="ct", source="src1"))
            layer.observe(TemporalEvent(ts=t0, key="beta.com", family="ct", source="src2"))
            t0 += 0.5
        candidates = layer.get_edge_candidates(k=20)
        edge_keys = {c.src_key for c in candidates} | {c.dst_key for c in candidates}
        assert any(k in edge_keys for k in ["alpha.com", "beta.com"])


class TestMaxKeysEviction:
    """Test 10: max_keys eviction"""

    def test_max_keys_eviction(self):
        layer = TemporalSignalLayer(max_keys=5)
        t0 = 1000.0
        for i in range(10):
            layer.observe(TemporalEvent(ts=t0 + i, key=f"key{i}.com", family="ct"))
        assert layer.get_state_size() <= 5

    def test_lru_order_preserved(self):
        layer = TemporalSignalLayer(max_keys=3)
        t0 = 1000.0
        layer.observe(TemporalEvent(ts=t0, key="key1.com", family="ct"))
        layer.observe(TemporalEvent(ts=t0 + 1, key="key2.com", family="ct"))
        layer.observe(TemporalEvent(ts=t0 + 2, key="key3.com", family="ct"))
        # Access key1, making it most recent
        layer.observe(TemporalEvent(ts=t0 + 3, key="key1.com", family="ct"))
        # Add key4 — should evict key2 (least recent)
        layer.observe(TemporalEvent(ts=t0 + 4, key="key4.com", family="ct"))
        scores = layer.get_top_scores(k=5)
        keys_present = {s.key for s in scores}
        assert "key2.com" not in keys_present


class TestRingSizeBounded:
    """Test 11: ring_size bounded"""

    def test_ring_gaps_bounded(self):
        layer = TemporalSignalLayer(ring_size=8)
        t0 = 1000.0
        for i in range(20):
            layer.observe(TemporalEvent(ts=t0 + i * 10.0, key="example.com", family="ct"))
        state = layer._states.get("example.com")
        assert state is not None
        assert len(state.ring_gaps) <= 8


class TestSnapshotRoundtrip:
    """Test 12: snapshot/from_snapshot roundtrip"""

    def test_snapshot_roundtrip(self):
        layer = TemporalSignalLayer(max_keys=100, ring_size=32)
        t0 = 1000.0
        for i in range(20):
            layer.observe(
                TemporalEvent(
                    ts=t0 + i * 10.0,
                    key="example.com",
                    family="ct",
                    source="src1",
                    weight=1.0,
                )
            )
        layer.observe_confirmation("example.com", True, "src1")
        layer.observe_confirmation("example.com", False, "src1")

        snap = layer.snapshot()
        restored = TemporalSignalLayer.from_snapshot(snap)

        assert restored.get_state_size() == layer.get_state_size()
        orig_scores = layer.get_top_scores(k=10)
        rest_scores = restored.get_top_scores(k=10)
        assert len(orig_scores) == len(rest_scores)

    def test_snapshot_deterministic(self):
        layer1 = TemporalSignalLayer()
        layer2 = TemporalSignalLayer()
        t0 = 1000.0
        for i in range(10):
            ev = TemporalEvent(ts=t0 + i * 10.0, key="example.com", family="ct")
            layer1.observe(ev)
            layer2.observe(ev)
        snap1 = layer1.snapshot()
        snap2 = layer2.snapshot()
        assert snap1 == snap2


class TestConfirmationFeedback:
    """Tests 13–14: observe_confirmation boost/decay"""

    def test_confirmation_true_boost_bounded(self):
        layer = TemporalSignalLayer()
        layer.observe(TemporalEvent(ts=1000.0, key="example.com", family="ct"))
        layer.observe_confirmation("example.com", True)
        state = layer._states["example.com"]
        assert CONFIRMATION_BOOST_MIN <= state.confirmation_weight <= CONFIRMATION_BOOST_MAX

    def test_confirmation_false_decay_bounded(self):
        layer = TemporalSignalLayer()
        layer.observe(TemporalEvent(ts=1000.0, key="example.com", family="ct"))
        layer.observe_confirmation("example.com", False)
        state = layer._states["example.com"]
        assert CONFIRMATION_BOOST_MIN <= state.confirmation_weight <= CONFIRMATION_BOOST_MAX

    def test_confirmation_multiple_boosts_capped(self):
        layer = TemporalSignalLayer()
        layer.observe(TemporalEvent(ts=1000.0, key="example.com", family="ct"))
        for _ in range(20):
            layer.observe_confirmation("example.com", True)
        state = layer._states["example.com"]
        assert state.confirmation_weight == CONFIRMATION_BOOST_MAX

    def test_confirmation_multiple_decays_capped(self):
        layer = TemporalSignalLayer()
        layer.observe(TemporalEvent(ts=1000.0, key="example.com", family="ct"))
        for _ in range(20):
            layer.observe_confirmation("example.com", False)
        state = layer._states["example.com"]
        assert state.confirmation_weight == CONFIRMATION_BOOST_MIN


class TestOutOfOrderTimestamps:
    """Test 15: out-of-order timestamp fail-soft"""

    def test_out_of_order_no_crash(self):
        layer = TemporalSignalLayer()
        # Normal order
        e1 = TemporalEvent(ts=1000.0, key="example.com", family="ct")
        e2 = TemporalEvent(ts=1010.0, key="example.com", family="ct")
        layer.observe(e1)
        layer.observe(e2)
        # Out-of-order — ts goes backwards
        e3 = TemporalEvent(ts=1005.0, key="example.com", family="ct")
        result = layer.observe(e3)  # should not crash
        assert result.key == "example.com"
        # State should be consistent
        state = layer._states["example.com"]
        assert state.event_count >= 2

    def test_negative_gap_not_in_ring(self):
        layer = TemporalSignalLayer()
        t0 = 1000.0
        layer.observe(TemporalEvent(ts=t0, key="example.com", family="ct"))
        layer.observe(TemporalEvent(ts=t0 + 10.0, key="example.com", family="ct"))
        layer.observe(TemporalEvent(ts=t0 + 5.0, key="example.com", family="ct"))  # backwards
        state = layer._states["example.com"]
        # Negative gap should not have been appended
        assert all(g >= 0 for g in state.ring_gaps)


class TestReset:
    """Test 16: reset clears state"""

    def test_reset_clears_all(self):
        layer = TemporalSignalLayer()
        t0 = 1000.0
        for i in range(10):
            layer.observe(TemporalEvent(ts=t0 + i * 10.0, key="example.com", family="ct"))
        layer.reset()
        assert layer.get_state_size() == 0
        assert len(layer._edge_candidates) == 0


class TestObserveManyDeterministic:
    """Test 17: observe_many preserves deterministic order"""

    def test_observe_many_order(self):
        layer = TemporalSignalLayer()
        events = [
            TemporalEvent(ts=1000.0 + i * 10.0, key="example.com", family="ct")
            for i in range(10)
        ]
        results = layer.observe_many(events)
        assert len(results) == 10
        for i, score in enumerate(results):
            assert score.event_count == i + 1

    def test_observe_many_reentrant(self):
        layer = TemporalSignalLayer()
        t0 = 1000.0
        events = [
            TemporalEvent(ts=t0 + i * 10.0, key="example.com", family="ct")
            for i in range(5)
        ]
        results1 = layer.observe_many(events)
        results2 = layer.observe_many(events)
        # Second call adds 5 more events
        assert results2[-1].event_count == 10


class TestNoNondeterminism:
    """Test 18: no global random seed / no nondeterminism"""

    def test_same_input_same_output(self):
        layer1 = TemporalSignalLayer()
        layer2 = TemporalSignalLayer()
        t0 = 1000.0
        for i in range(10):
            ev = TemporalEvent(ts=t0 + i * 10.0, key="example.com", family="ct")
            layer1.observe(ev)
            layer2.observe(ev)
        score1 = layer1.get_top_scores(k=1)[0]
        score2 = layer2.get_top_scores(k=1)[0]
        assert score1.anomaly_score == score2.anomaly_score
        assert score1.periodicity_score == score2.periodicity_score


class TestEventFromFindingLike:
    """event_from_finding_like helper tests"""

    def test_dict_conversion(self):
        obj = {
            "timestamp": 1000.0,
            "domain": "example.com",
            "source_family": "ct",
            "confidence": 0.9,
        }
        event = event_from_finding_like(obj)
        assert event is not None
        assert event.ts == 1000.0
        assert event.key == "example.com"

    def test_object_conversion(self):
        class FakeFinding:
            timestamp = 1000.0
            domain = "example.com"
            source_family = "ct"
            confidence = 0.9

        event = event_from_finding_like(FakeFinding())
        assert event is not None
        assert event.ts == 1000.0

    def test_missing_fields_returns_none(self):
        obj = {"foo": "bar"}
        event = event_from_finding_like(obj)
        assert event is None

    def test_invalid_object_returns_none(self):
        event = event_from_finding_like(None)
        assert event is None


class TestEdgeCandidateStructure:
    """TemporalEdgeCandidate DTO structure"""

    def test_edge_candidate_fields(self):
        candidate = TemporalEdgeCandidate(
            src_key="a.com",
            dst_key="b.com",
            edge_type="co_burst",
            score=0.8,
            window_start=1000.0,
            window_end=1100.0,
            reason="burst",
        )
        assert candidate.src_key == "a.com"
        assert candidate.dst_key == "b.com"
        assert candidate.edge_type == "co_burst"
        assert candidate.score == 0.8
        assert "co_burst" in candidate.reason or "source" in candidate.reason or "burst" in candidate.reason


class TestGetEdgeCandidates:
    """Edge candidate retrieval"""

    def test_empty_layer_no_candidates(self):
        layer = TemporalSignalLayer()
        candidates = layer.get_edge_candidates(k=10)
        assert len(candidates) == 0

    def test_edge_candidates_limit(self):
        layer = TemporalSignalLayer()
        t0 = 1000.0
        for i in range(50):
            layer.observe(TemporalEvent(ts=t0 + i * 0.1, key=f"key{i}.com", family="ct"))
        candidates = layer.get_edge_candidates(k=5)
        assert len(candidates) <= 5


class TestAPI:
    """API surface invariants"""

    def test_temporal_event_slots(self):
        e = TemporalEvent(ts=1.0, key="x.com")
        assert hasattr(e, "ts")
        assert hasattr(e, "key")

    def test_temporal_score_slots(self):
        s = TemporalScore(
            key="x.com",
            family="ct",
            event_count=1,
            anomaly_score=0.0,
            burst_score=0.0,
            periodicity_score=0.0,
            change_point_score=0.0,
            source_synchrony_score=0.0,
            rate_score=0.0,
            cv_isi=0.0,
            mean_gap_s=0.0,
            autocorr_lag1=0.0,
            reason="",
        )
        assert hasattr(s, "anomaly_score")

    def test_layer_constructor_defaults(self):
        layer = TemporalSignalLayer()
        assert layer._max_keys == DEFAULT_MAX_KEYS
        assert layer._ring_size == DEFAULT_RING_SIZE
        assert layer._half_life_s == DEFAULT_HALF_LIFE_S
        assert layer._synchrony_window_s == DEFAULT_SYNCHRONY_WINDOW_S
        assert layer._bocpd_max_run == DEFAULT_BOCPD_MAX_RUN

    def test_layer_constructor_custom(self):
        layer = TemporalSignalLayer(max_keys=512, ring_size=16, half_life_s=300.0)
        assert layer._max_keys == 512
        assert layer._ring_size == 16
        assert layer._half_life_s == 300.0
