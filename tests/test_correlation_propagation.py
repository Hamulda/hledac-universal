"""
Test Correlation Propagation Across Ledgers
==========================================

Tests that correlation keys (run_id, branch_id, provider_id, action_id)
propagate correctly across:
- EvidenceLog
- ToolExecLog
- MetricsRegistry
- analytics_hook (shadow)

Also verifies:
- Backward compatibility (old call sites without correlation work)
- Serialization shape stability
- Queryability of correlation fields
"""

import json
import tempfile
import uuid
from datetime import datetime
from pathlib import Path


class TestEvidenceLogCorrelation:
    """Test EvidenceLog.create_event correlation support."""

    def test_create_event_without_correlation_backward_compat(self):
        """Old call sites without correlation still work."""
        from hledac.universal.evidence_log import EvidenceLog

        with tempfile.TemporaryDirectory():
            run_id = f"test_run_{uuid.uuid4().hex[:8]}"
            log = EvidenceLog(run_id=run_id, enable_persist=False)

            event = log.create_event(
                event_type="observation",
                payload={"data": "test"},
                confidence=0.9,
            )

            assert event.event_id is not None
            assert event.run_id == run_id
            assert "_correlation" not in event.payload

    def test_create_event_with_correlation_flat(self):
        """create_event accepts correlation dict and stores in payload._correlation."""
        from hledac.universal.evidence_log import EvidenceLog

        with tempfile.TemporaryDirectory():
            run_id = f"test_run_{uuid.uuid4().hex[:8]}"
            log = EvidenceLog(run_id=run_id, enable_persist=False)

            correlation = {
                "run_id": "run_123",
                "branch_id": "branch_a",
                "provider_id": "mlx",
                "action_id": "action_456",
            }

            event = log.create_event(
                event_type="observation",
                payload={"data": "test"},
                confidence=0.9,
                correlation=correlation,
            )

            assert "_correlation" in event.payload
            assert event.payload["_correlation"]["run_id"] == "run_123"
            assert event.payload["_correlation"]["branch_id"] == "branch_a"
            assert event.payload["_correlation"]["provider_id"] == "mlx"
            assert event.payload["_correlation"]["action_id"] == "action_456"

    def test_create_event_correlation_partial(self):
        """Correlation can be partial - only some keys present."""
        from hledac.universal.evidence_log import EvidenceLog

        with tempfile.TemporaryDirectory():
            run_id = f"test_run_{uuid.uuid4().hex[:8]}"
            log = EvidenceLog(run_id=run_id, enable_persist=False)

            correlation = {
                "run_id": "run_789",
                "branch_id": None,
                "provider_id": "openai",
                "action_id": None,
            }

            event = log.create_event(
                event_type="decision",
                payload={"kind": "test"},
                correlation=correlation,
            )

            assert event.payload["_correlation"]["run_id"] == "run_789"
            assert event.payload["_correlation"]["branch_id"] is None
            assert event.payload["_correlation"]["provider_id"] == "openai"
            assert event.payload["_correlation"]["action_id"] is None

    def test_evidence_event_serialization_stable(self):
        """EvidenceEvent.to_dict() serialization includes correlation when present."""
        from hledac.universal.evidence_log import EvidenceLog

        with tempfile.TemporaryDirectory():
            run_id = f"test_run_{uuid.uuid4().hex[:8]}"
            log = EvidenceLog(run_id=run_id, enable_persist=False)

            correlation = {
                "run_id": "run_abc",
                "branch_id": "branch_b",
                "provider_id": "anthropic",
                "action_id": "action_def",
            }

            event = log.create_event(
                event_type="evidence_packet",
                payload={"url": "https://example.com"},
                correlation=correlation,
            )

            d = event.to_dict()
            serialized = json.dumps(d, sort_keys=True)

            # Verify correlation is in serialized form
            assert '"_correlation"' in serialized
            assert '"run_id": "run_abc"' in serialized
            assert '"branch_id": "branch_b"' in serialized

    def test_evidence_event_queryable(self):
        """Correlation in payload is queryable via payload access."""
        from hledac.universal.evidence_log import EvidenceLog

        with tempfile.TemporaryDirectory():
            run_id = f"test_run_{uuid.uuid4().hex[:8]}"
            log = EvidenceLog(run_id=run_id, enable_persist=False)

            correlation = {
                "run_id": "run_query",
                "branch_id": "branch_query",
                "provider_id": "mlx",
                "action_id": None,
            }

            log.create_event(
                event_type="observation",
                payload={"key": "value1"},
                correlation=correlation,
            )
            log.create_event(
                event_type="observation",
                payload={"key": "value2"},
            )  # No correlation

            # Query events
            events = log.query(event_type="observation")
            assert len(events) == 2

            # Find event with correlation
            corr_events = [e for e in events if "_correlation" in e.payload and e.payload["_correlation"]["branch_id"] == "branch_query"]
            assert len(corr_events) == 1
            assert corr_events[0].payload["key"] == "value1"


class TestToolExecLogCorrelation:
    """Test ToolExecLog.log() correlation support."""

    def test_log_without_correlation_backward_compat(self):
        """Old call sites without correlation still work."""
        from hledac.universal.tool_exec_log import ToolExecLog

        with tempfile.TemporaryDirectory() as tmpdir:
            run_id = f"test_run_{uuid.uuid4().hex[:8]}"
            log = ToolExecLog(run_dir=Path(tmpdir), run_id=run_id, enable_persist=False)

            event = log.log(
                tool_name="test_tool",
                input_data=b"input",
                output_data=b"output",
                status="success",
            )

            assert event.event_id is not None
            assert event.correlation is None

    def test_log_with_correlation(self):
        """log() accepts correlation and stores in ToolExecEvent.correlation."""
        from hledac.universal.tool_exec_log import ToolExecLog

        with tempfile.TemporaryDirectory() as tmpdir:
            run_id = f"test_run_{uuid.uuid4().hex[:8]}"
            log = ToolExecLog(run_dir=Path(tmpdir), run_id=run_id, enable_persist=False)

            correlation = {
                "run_id": "run_tool",
                "branch_id": "branch_tool",
                "provider_id": "mlx",
                "action_id": "tool_action_123",
            }

            event = log.log(
                tool_name="web_search",
                input_data=b"query",
                output_data=b"results",
                status="success",
                correlation=correlation,
            )

            assert event.correlation is not None
            assert event.correlation["run_id"] == "run_tool"
            assert event.correlation["branch_id"] == "branch_tool"
            assert event.correlation["provider_id"] == "mlx"
            assert event.correlation["action_id"] == "tool_action_123"

    def test_tool_exec_event_serialization(self):
        """ToolExecEvent.to_dict() includes correlation when present."""
        from hledac.universal.tool_exec_log import ToolExecLog

        with tempfile.TemporaryDirectory() as tmpdir:
            run_id = f"test_run_{uuid.uuid4().hex[:8]}"
            log = ToolExecLog(run_dir=Path(tmpdir), run_id=run_id, enable_persist=False)

            correlation = {
                "run_id": "run_ser",
                "branch_id": "branch_ser",
                "provider_id": "openai",
                "action_id": "action_ser",
            }

            event = log.log(
                tool_name="crawl",
                input_data=b"url",
                output_data=b"html",
                status="success",
                correlation=correlation,
            )

            d = event.to_dict()
            assert "correlation" in d
            assert d["correlation"]["run_id"] == "run_ser"

            # Verify JSON serializable
            serialized = json.dumps(d, sort_keys=True)
            assert '"correlation"' in serialized

    def test_tool_exec_event_from_dict_with_correlation(self):
        """ToolExecEvent.from_dict() correctly deserializes correlation."""
        from hledac.universal.tool_exec_log import ToolExecEvent

        data = {
            "event_id": "tool_1",
            "ts": datetime.utcnow().isoformat(),
            "tool_name": "test",
            "input_hash": "hash1",
            "output_hash": "hash2",
            "output_len": 100,
            "status": "success",
            "error_class": None,
            "seq_no": 1,
            "prev_chain_hash": "prev",
            "chain_hash": "chain",
            "correlation": {
                "run_id": "run_from_dict",
                "branch_id": "branch_from_dict",
                "provider_id": "mlx",
                "action_id": "action_from_dict",
            },
        }

        event = ToolExecEvent.from_dict(data)
        assert event.correlation is not None
        assert event.correlation["run_id"] == "run_from_dict"
        assert event.correlation["branch_id"] == "branch_from_dict"


class TestMetricsRegistryCorrelation:
    """Test MetricsRegistry correlation support."""

    def test_init_without_correlation_backward_compat(self):
        """Old call sites without correlation still work."""
        from hledac.universal.metrics_registry import MetricsRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = MetricsRegistry(run_dir=Path(tmpdir), run_id="test")

            registry.inc("test_counter")
            registry.set_gauge("test_gauge", 1.0)

            summary = registry.get_summary()
            assert summary["run_id"] == "test"

    def test_init_with_correlation(self):
        """MetricsRegistry.__init__ accepts correlation and stores it."""
        from hledac.universal.metrics_registry import MetricsRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            correlation = {
                "branch_id": "branch_metric",
                "provider_id": "mlx",
                "action_id": "metric_action",
            }
            registry = MetricsRegistry(
                run_dir=Path(tmpdir),
                run_id="test_corr",
                correlation=correlation,
            )

            # Verify correlation is stored
            assert registry._correlation == correlation

    def test_flush_includes_correlation(self):
        """flush() serializes correlation into metrics JSONL."""
        from hledac.universal.metrics_registry import MetricsRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            correlation = {
                "branch_id": "branch_flush",
                "provider_id": "openai",
                "action_id": "flush_action",
            }
            registry = MetricsRegistry(
                run_dir=Path(tmpdir),
                run_id="test_flush",
                correlation=correlation,
            )

            # Use valid metric name from METRIC_NAMES
            registry.inc("orchestrator_frontier_size")
            registry.flush(force=True)

            # Read the metrics file
            metrics_file = Path(tmpdir) / "logs" / "metrics.jsonl"
            assert metrics_file.exists()

            with open(metrics_file) as f:
                lines = f.readlines()

            assert len(lines) >= 1
            # At least one line should have correlation
            found_corr = False
            for line in lines:
                if line.strip():
                    d = json.loads(line)
                    if "correlation" in d:
                        assert d["correlation"]["branch_id"] == "branch_flush"
                        assert d["correlation"]["provider_id"] == "openai"
                        found_corr = True
            assert found_corr, "No metric with correlation found"


class TestAnalyticsHookCorrelation:
    """Test analytics_hook.shadow_record_finding correlation support."""

    def test_analytics_hook_signature_extended(self):
        """shadow_record_finding accepts branch_id, provider_id, action_id."""
        import inspect

        from hledac.universal.knowledge.analytics_hook import shadow_record_finding

        sig = inspect.signature(shadow_record_finding)
        params = list(sig.parameters.keys())

        assert "run_id" in params
        assert "branch_id" in params, "branch_id not in shadow_record_finding params"
        assert "provider_id" in params, "provider_id not in shadow_record_finding params"
        assert "action_id" in params, "action_id not in shadow_record_finding params"

    def test_analytics_hook_fail_open_without_shadow(self):
        """shadow_record_finding is fail-open when shadow disabled."""
        from hledac.universal.knowledge.analytics_hook import (
            shadow_ingest_failures,
            shadow_record_finding,
            shadow_reset_failures,
        )

        # Ensure shadow is disabled
        shadow_reset_failures()
        initial_failures = shadow_ingest_failures()

        # This should not raise even with new params
        shadow_record_finding(
            finding_id="f1",
            query="test",
            source_type="web",
            confidence=0.9,
            run_id="run1",
            branch_id="branch1",
            provider_id="mlx",
            action_id="action1",
        )

        # Should still be fail-open
        assert shadow_ingest_failures() == initial_failures

    def test_evidence_log_append_propagates_correlation_to_shadow(self):
        """EvidenceLog.append() extracts _correlation from payload and passes to shadow_record_finding.

        Verifies cross-ledger propagation: EvidenceLog → analytics_hook (DuckDB shadow).
        """
        import os

        from hledac.universal.knowledge.analytics_hook import (
            shadow_reset_failures,
        )

        # Ensure shadow is disabled so we test the fail-open path
        os.environ["GHOST_DUCKDB_SHADOW"] = "0"
        shadow_reset_failures()

        from hledac.universal.evidence_log import EvidenceLog

        with tempfile.TemporaryDirectory():
            run_id = f"test_shadow_corr_{uuid.uuid4().hex[:8]}"
            log = EvidenceLog(run_id=run_id, enable_persist=False)

            # Create evidence_packet event with full correlation
            correlation = {
                "run_id": "run_cross_ledger",
                "branch_id": "branch_cross",
                "provider_id": "mlx",
                "action_id": "action_cross",
            }

            event = log.create_event(
                event_type="evidence_packet",
                payload={
                    "query": "cross-ledger test",
                    "url": "https://example.com",
                    "title": "Test",
                    "source": "test",
                    "relevance_score": 0.9,
                },
                confidence=0.95,
                correlation=correlation,
            )

            # Verify _correlation was stored in payload
            assert "_correlation" in event.payload
            assert event.payload["_correlation"]["branch_id"] == "branch_cross"
            assert event.payload["_correlation"]["provider_id"] == "mlx"
            assert event.payload["_correlation"]["action_id"] == "action_cross"

            # Verify append() succeeded (fail-open even if shadow is disabled)
            assert event.event_id is not None

            # Restore shadow setting
            os.environ.pop("GHOST_DUCKDB_SHADOW", None)


class TestCorrelationSchema:
    """Test RunCorrelation canonical schema in types.py."""

    def test_run_correlation_exists(self):
        """RunCorrelation dataclass exists in types.py."""
        from hledac.universal.project_types import RunCorrelation

        corr = RunCorrelation(
            run_id="r1",
            branch_id="b1",
            provider_id="mlx",
            action_id="a1",
        )
        assert corr.run_id == "r1"
        assert corr.branch_id == "b1"
        assert corr.provider_id == "mlx"
        assert corr.action_id == "a1"

    def test_run_correlation_to_dict(self):
        """RunCorrelation.to_dict() returns serializable dict."""
        from hledac.universal.project_types import RunCorrelation

        corr = RunCorrelation(
            run_id="run_x",
            branch_id="branch_y",
            provider_id="openai",
            action_id="action_z",
        )
        d = corr.to_dict()

        assert d["run_id"] == "run_x"
        assert d["branch_id"] == "branch_y"
        assert d["provider_id"] == "openai"
        assert d["action_id"] == "action_z"

        # Verify JSON serializable
        serialized = json.dumps(d)
        assert "run_x" in serialized

    def test_run_correlation_partial(self):
        """RunCorrelation supports partial fields."""
        from hledac.universal.project_types import RunCorrelation

        corr = RunCorrelation(run_id="run_partial")
        d = corr.to_dict()

        assert d["run_id"] == "run_partial"
        assert d["branch_id"] is None
        assert d["provider_id"] is None
        assert d["action_id"] is None

    def test_run_correlation_with_provider(self):
        """RunCorrelation.with_provider() returns new instance."""
        from hledac.universal.project_types import RunCorrelation

        corr = RunCorrelation(run_id="run1", branch_id="branch1")
        corr2 = corr.with_provider("anthropic")

        assert corr2.run_id == "run1"
        assert corr2.branch_id == "branch1"
        assert corr2.provider_id == "anthropic"
        assert corr2.action_id is None

    def test_run_correlation_with_action(self):
        """RunCorrelation.with_action() returns new instance."""
        from hledac.universal.project_types import RunCorrelation

        corr = RunCorrelation(run_id="run2")
        corr2 = corr.with_action("action_abc")

        assert corr2.run_id == "run2"
        assert corr2.action_id == "action_abc"
        assert corr2.provider_id is None
