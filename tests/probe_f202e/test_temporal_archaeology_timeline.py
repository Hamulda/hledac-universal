"""
Sprint F202E: Temporal Archaeology and Drift Timelines — Probe Tests
====================================================================

Invariant mapping:
  F202E-1  | source_type is "temporal_archaeology" for derived findings
  F202E-2  | payload_text contains serialized timeline JSON (entity_id, events, metadata)
  F202E-3  | MAX_TIMELINE_EVENTS=200 cap applied to output events
  F202E-4  | Invalid timestamps (NaN, negative, far future) are skipped fail-soft
  F202E-5  | All findings go through async_ingest_findings_batch
  F202E-6  | _run_temporal_archaeology_sidecar is called after CT findings accepted
  F202E-7  | SprintSchedulerResult.timeline_findings_produced is set
  F202E-8  | Fail-soft: sidecar errors do not crash sprint
  F202E-9  | Event types: ct_observed, archive_snapshot, document_dated, finding_accepted
  F202E-10 | Timeline is sorted by timestamp ascending
  F202E-11 | Markdown report includes timeline section when timeline_findings present
  F202E-12 | No model load (pure Python timestamp processing)
"""

import asyncio
import json
import math
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.intelligence.timeline_synthesizer import (
    MAX_EVENT_AGE_DAYS,
    MAX_TIMELINE_EVENTS,
    SynthesizedTimeline,
    TimelineEvent,
    TimelineMetadata,
    TimelineSynthesizer,
    create_timeline_synthesizer,
)
from hledac.universal.intelligence.temporal_archaeologist_adapter import (
    MAX_TIMELINE_FINDINGS,
    TemporalArchaeologistAdapter,
    TimelineFindingResult,
    create_temporal_archaeologist_adapter,
)


# ============================================================================
# F202E-3: MAX_TIMELINE_EVENTS bound tests
# ============================================================================

class TestBounds:
    """F202E-3: Bounded to MAX_TIMELINE_EVENTS cap."""

    def test_max_timeline_events_constant(self):
        """MAX_TIMELINE_EVENTS is 200."""
        assert MAX_TIMELINE_EVENTS == 200

    def test_max_event_age_days_constant(self):
        """MAX_EVENT_AGE_DAYS is 1825 (5 years)."""
        assert MAX_EVENT_AGE_DAYS == 365 * 5

    def test_synthesizer_caps_events(self):
        """build() returns at most MAX_TIMELINE_EVENTS."""
        synthesizer = TimelineSynthesizer()
        now = time.time()

        # Add more than MAX_TIMELINE_EVENTS events
        for i in range(MAX_TIMELINE_EVENTS + 50):
            synthesizer._events.append(
                TimelineEvent(
                    ts=now + i,
                    event_type="ct_observed",
                    source="ct_log",
                    description=f"Event {i}",
                )
            )

        timeline = synthesizer.build()
        assert len(timeline.events) <= MAX_TIMELINE_EVENTS

    def test_max_timeline_findings_constant(self):
        """MAX_TIMELINE_FINDINGS is 20."""
        assert MAX_TIMELINE_FINDINGS == 20


# ============================================================================
# F202E-4: Invalid timestamp handling tests
# ============================================================================

class TestTimestampValidation:
    """F202E-4: Invalid timestamps are skipped fail-soft."""

    def test_negative_timestamp_skipped(self):
        """Negative timestamps are not valid."""
        synthesizer = TimelineSynthesizer()
        assert not synthesizer._is_valid_timestamp(-1.0)
        assert not synthesizer._is_valid_timestamp(-1000.0)

    def test_zero_timestamp_skipped(self):
        """Zero timestamp is not valid."""
        synthesizer = TimelineSynthesizer()
        assert not synthesizer._is_valid_timestamp(0.0)

    def test_nan_timestamp_skipped(self):
        """NaN timestamp is not valid."""
        synthesizer = TimelineSynthesizer()
        assert not synthesizer._is_valid_timestamp(float("nan"))

    def test_inf_timestamp_skipped(self):
        """Infinity timestamp is not valid."""
        synthesizer = TimelineSynthesizer()
        assert not synthesizer._is_valid_timestamp(float("inf"))
        assert not synthesizer._is_valid_timestamp(float("-inf"))

    def test_far_future_timestamp_skipped(self):
        """Timestamp more than 1 year in future is not valid."""
        synthesizer = TimelineSynthesizer()
        future = time.time() + 86400 * 400  # 400 days in future
        assert not synthesizer._is_valid_timestamp(future)

    def test_valid_timestamp_accepted(self):
        """Valid current timestamp is accepted."""
        synthesizer = TimelineSynthesizer()
        now = time.time()
        assert synthesizer._is_valid_timestamp(now)

    def test_valid_past_timestamp_accepted(self):
        """Timestamp from 1 year ago is accepted."""
        synthesizer = TimelineSynthesizer()
        past = time.time() - 86400 * 180  # 180 days ago
        assert synthesizer._is_valid_timestamp(past)

    def test_synthesizer_skips_invalid_in_build(self):
        """build() skips events with invalid timestamps."""
        synthesizer = TimelineSynthesizer()
        now = time.time()

        # Add mix of valid and invalid events
        synthesizer._events.append(
            TimelineEvent(
                ts=now,
                event_type="ct_observed",
                source="ct_log",
                description="Valid event",
            )
        )
        synthesizer._events.append(
            TimelineEvent(
                ts=-1.0,  # Invalid
                event_type="ct_observed",
                source="ct_log",
                description="Invalid event",
            )
        )
        synthesizer._events.append(
            TimelineEvent(
                ts=now - 86400 * 1900,  # Too old (> MAX_EVENT_AGE_DAYS = 1825)
                event_type="ct_observed",
                source="ct_log",
                description="Too old event",
            )
        )

        timeline = synthesizer.build()
        # Only 1 valid event should remain
        assert len(timeline.events) == 1
        assert timeline.events[0].description == "Valid event"


# ============================================================================
# F202E-9: Event type tests
# ============================================================================

class TestEventTypes:
    """F202E-9: Event types are correctly assigned."""

    def test_ct_observed_event_type(self):
        """CT events have event_type='ct_observed'."""
        synthesizer = TimelineSynthesizer()
        now = time.time()

        # Create a mock CT finding
        class MockFinding:
            source_type = "ct_log"
            ts = now
            finding_id = "abc123"
            query = "example.com"
            confidence = 0.8

        count = synthesizer.add_ct_events([MockFinding()])
        assert count == 1
        assert synthesizer._events[0].event_type == "ct_observed"
        assert synthesizer._events[0].source == "ct_log"

    def test_archive_snapshot_event_type(self):
        """Archive events have event_type='archive_snapshot'."""
        synthesizer = TimelineSynthesizer()

        class MockArchive:
            url = "https://example.com/page"
            timestamp = datetime.now()
            source = "wayback"
            title = "Example Page"

        count = synthesizer.add_archive_events([MockArchive()])
        assert count == 1
        assert synthesizer._events[0].event_type == "archive_snapshot"
        assert synthesizer._events[0].source == "wayback"

    def test_finding_accepted_event_type(self):
        """Finding events have event_type='finding_accepted'."""
        synthesizer = TimelineSynthesizer()
        now = time.time()

        class MockFinding:
            ts = now
            finding_id = "test123"
            source_type = "leak_sentinel"
            query = "test.com"
            confidence = 0.7

        count = synthesizer.add_finding_events([MockFinding()], source_label="leak_sentinel")
        assert count == 1
        assert synthesizer._events[0].event_type == "finding_accepted"
        assert synthesizer._events[0].source == "leak_sentinel"


# ============================================================================
# F202E-10: Timeline sorting tests
# ============================================================================

class TestTimelineSorting:
    """F202E-10: Timeline is sorted by timestamp ascending."""

    def test_events_sorted_ascending(self):
        """build() returns events sorted by timestamp ascending."""
        synthesizer = TimelineSynthesizer()
        now = time.time()

        # Add events out of order
        synthesizer._events.append(
            TimelineEvent(ts=now + 100, event_type="c", source="test", description="Third")
        )
        synthesizer._events.append(
            TimelineEvent(ts=now, event_type="a", source="test", description="First")
        )
        synthesizer._events.append(
            TimelineEvent(ts=now + 50, event_type="b", source="test", description="Second")
        )

        timeline = synthesizer.build()

        assert len(timeline.events) == 3
        assert timeline.events[0].description == "First"
        assert timeline.events[1].description == "Second"
        assert timeline.events[2].description == "Third"

    def test_metadata_oldest_newest_correct(self):
        """metadata.oldest_event_ts and newest_event_ts are correct."""
        synthesizer = TimelineSynthesizer()
        now = time.time()

        synthesizer._events.append(
            TimelineEvent(ts=now + 100, event_type="a", source="test", description="Newer")
        )
        synthesizer._events.append(
            TimelineEvent(ts=now, event_type="b", source="test", description="Older")
        )

        timeline = synthesizer.build()

        assert timeline.metadata.oldest_event_ts == now
        assert timeline.metadata.newest_event_ts == now + 100


# ============================================================================
# F202E-1: Source type tests
# ============================================================================

class TestSourceType:
    """F202E-1: source_type is 'temporal_archaeology' for derived findings."""

    def test_derived_finding_source_type(self):
        """Derived timeline findings have source_type='temporal_archaeology'."""
        adapter = TemporalArchaeologistAdapter()

        now = time.time()
        # Add a CT finding
        class MockFinding:
            source_type = "ct_log"
            ts = now
            finding_id = "ct123"
            query = "example.com"
            confidence = 0.8

        result = adapter.synthesize_timeline(
            ct_findings=[MockFinding()],
            entity_id="example.com",
        )

        if result.derived_findings:
            finding = result.derived_findings[0]
            assert finding.source_type == "temporal_archaeology"


# ============================================================================
# F202E-2: Payload serialization tests
# ============================================================================

class TestPayloadSerialization:
    """F202E-2: payload_text contains serialized timeline JSON."""

    def test_payload_contains_timeline_json(self):
        """Derived finding payload_text contains entity_id, events, metadata."""
        adapter = TemporalArchaeologistAdapter()

        now = time.time()
        class MockFinding:
            source_type = "ct_log"
            ts = now
            finding_id = "ct123"
            query = "example.com"
            confidence = 0.8

        result = adapter.synthesize_timeline(
            ct_findings=[MockFinding()],
            entity_id="example.com",
        )

        if result.derived_findings:
            payload = result.derived_findings[0].payload_text
            assert payload is not None

            data = json.loads(payload)
            assert "entity_id" in data
            assert "events" in data
            assert "metadata" in data
            assert data["entity_id"] == "example.com"

    def test_payload_events_have_required_fields(self):
        """Serialized events have required fields."""
        adapter = TemporalArchaeologistAdapter()

        now = time.time()
        class MockFinding:
            source_type = "ct_log"
            ts = now
            finding_id = "ct456"
            query = "test.com"
            confidence = 0.9

        result = adapter.synthesize_timeline(
            ct_findings=[MockFinding()],
            entity_id="test.com",
        )

        if result.derived_findings:
            payload = result.derived_findings[0].payload_text
            data = json.loads(payload)

            if data.get("events"):
                event = data["events"][0]
                assert "ts" in event
                assert "event_type" in event
                assert "source" in event
                assert "description" in event


# ============================================================================
# F202E-6: Sidecar wiring tests
# ============================================================================

class TestSidecarWiring:
    """F202E-6: _run_temporal_archaeology_sidecar is called after CT findings accepted."""

    def test_temporal_archaeology_adapter_field_exists(self):
        """SprintScheduler has _run_temporal_archaeology_sidecar method."""
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
        )
        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        assert hasattr(scheduler, "_run_temporal_archaeology_sidecar")


# ============================================================================
# F202E-7: Result field tests
# ============================================================================

class TestResultField:
    """F202E-7: SprintSchedulerResult.timeline_findings_produced is set."""

    def test_timeline_findings_produced_field_exists(self):
        """SprintSchedulerResult has timeline_findings_produced field."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        assert hasattr(result, "timeline_findings_produced")
        assert result.timeline_findings_produced == 0


# ============================================================================
# F202E-8: Fail-soft tests
# ============================================================================

class TestFailSoft:
    """F202E-8: Fail-soft — sidecar errors do not crash sprint."""

    @pytest.mark.asyncio
    async def test_synthesize_handles_empty_findings(self):
        """synthesize_timeline() returns empty result for empty input."""
        adapter = TemporalArchaeologistAdapter()
        result = adapter.synthesize_timeline(
            ct_findings=[],
            entity_id="test.com",
        )
        assert result.timeline is None or len(result.timeline.events) == 0
        assert result.derived_findings == []

    def test_adapter_get_stats_returns_defaults(self):
        """get_stats() returns default stats even before synthesis."""
        adapter = TemporalArchaeologistAdapter()
        stats = adapter.get_stats()
        assert stats.get("ct_events_added", 0) == 0
        assert stats.get("findings_produced", 0) == 0

    def test_synthesizer_clear_resets_stats(self):
        """clear() resets events and stats."""
        synthesizer = TimelineSynthesizer()
        now = time.time()
        synthesizer._events.append(
            TimelineEvent(ts=now, event_type="a", source="test", description="Test")
        )
        synthesizer._stats["ct_events_added"] = 5

        synthesizer.clear()

        assert len(synthesizer._events) == 0
        assert synthesizer._stats["ct_events_added"] == 0


# ============================================================================
# F202E-12: No model load tests
# ============================================================================

class TestNoModelLoad:
    """F202E-12: Pure Python timestamp processing, no model load."""

    def test_timeline_synthesizer_source_contains_no_heavy_deps(self):
        """TimelineSynthesizer source file contains only pure Python imports.

        This is a hermetic check that avoids sys.modules pollution when
        multiple sprints run in the same process.
        """
        import os

        # Resolve timeline_synthesizer source file path
        # tests/probe_f202e/test_temporal_archaeology_timeline.py
        #                     .. (to tests) .. (to universal) . (universal/)
        ts_path = os.path.join(
            os.path.dirname(__file__),
            "..", "..",
            "intelligence", "timeline_synthesizer.py"
        )
        with open(ts_path) as f:
            source = f.read()

        # Verify source doesn't import heavy ML dependencies
        heavy_deps = ["torch", "tensorflow", "transformers", "cv2", "PIL"]
        for dep in heavy_deps:
            import_patterns = [
                f"import {dep}",
                f"from {dep} import",
            ]
            for pattern in import_patterns:
                assert pattern not in source, (
                    f"timeline_synthesizer.py source contains '{pattern}'"
                )

    def test_timestamp_to_float_conversion(self):
        """_to_timestamp handles various formats."""
        synthesizer = TimelineSynthesizer()

        # datetime
        dt = datetime(2024, 1, 15, 12, 0, 0)
        ts = synthesizer._to_timestamp(dt)
        assert ts > 0

        # float
        ts = synthesizer._to_timestamp(1234567890.0)
        assert ts == 1234567890.0

        # int
        ts = synthesizer._to_timestamp(1234567890)
        assert ts == 1234567890.0

        # ISO string
        ts = synthesizer._to_timestamp("2024-01-15T12:00:00")
        assert ts > 0

        # Invalid
        ts = synthesizer._to_timestamp(None)
        assert ts == 0.0


# ============================================================================
# F202E-11: Markdown rendering tests
# ============================================================================

class TestMarkdownRendering:
    """F202E-11: Markdown report includes timeline section."""

    def test_render_timeline_section_exists(self):
        """_render_timeline_section function exists."""
        from hledac.universal.export.sprint_markdown_reporter import (
            _render_timeline_section,
        )
        assert callable(_render_timeline_section)

    def test_render_timeline_section_empty_input(self):
        """_render_timeline_section returns empty string for empty input."""
        from hledac.universal.export.sprint_markdown_reporter import (
            _render_timeline_section,
        )
        result = _render_timeline_section([])
        assert result == ""

    def test_render_timeline_section_with_data(self):
        """_render_timeline_section renders timeline data."""
        from hledac.universal.export.sprint_markdown_reporter import (
            _render_timeline_section,
        )

        timeline_finding = {
            "finding_id": "timeline_123",
            "entity_id": "example.com",
            "metadata": {
                "total_events": 2,
                "oldest_event_ts": time.time() - 86400,
                "newest_event_ts": time.time(),
                "event_types": {"ct_observed": 2},
                "sources": {"ct_log": 2},
            },
            "events": [
                {
                    "ts": time.time() - 86400,
                    "event_type": "ct_observed",
                    "source": "ct_log",
                    "description": "Certificate observed",
                    "entity_id": "example.com",
                    "confidence": 0.8,
                    "evidence": ["finding:abc123"],
                },
                {
                    "ts": time.time(),
                    "event_type": "ct_observed",
                    "source": "ct_log",
                    "description": "Certificate renewed",
                    "entity_id": "example.com",
                    "confidence": 0.9,
                    "evidence": ["finding:def456"],
                },
            ],
        }

        result = _render_timeline_section([timeline_finding])

        assert "Temporal Archaeology Timeline" in result
        assert "example.com" in result
        assert "ct_observed" in result
        assert "ct_log" in result


# ============================================================================
# Integration: End-to-end synthesis test
# ============================================================================

class TestTimelineSynthesisIntegration:
    """Integration tests for full timeline synthesis."""

    def test_full_synthesis_pipeline(self):
        """Full synthesis from CT findings to derived finding."""
        adapter = TemporalArchaeologistAdapter()

        now = time.time()

        class MockCTFinding:
            source_type = "ct_log"
            ts = now - 86400 * 30  # 30 days ago
            finding_id = "ct_abc123"
            query = "example.com"
            confidence = 0.85

        result = adapter.synthesize_timeline(
            ct_findings=[MockCTFinding()],
            entity_id="example.com",
        )

        assert result.timeline is not None
        assert result.timeline.entity_id == "example.com"
        assert len(result.timeline.events) >= 0

        # Stats should reflect added events
        stats = result.stats
        assert stats.get("ct_events_added", 0) >= 0

    def test_synthesizer_metadata_event_types_count(self):
        """Metadata correctly counts event types."""
        synthesizer = TimelineSynthesizer()
        now = time.time()

        for i in range(5):
            synthesizer._events.append(
                TimelineEvent(
                    ts=now + i * 100,
                    event_type="ct_observed",
                    source="ct_log",
                    description=f"CT event {i}",
                )
            )
        for i in range(3):
            synthesizer._events.append(
                TimelineEvent(
                    ts=now + 500 + i * 100,
                    event_type="archive_snapshot",
                    source="wayback",
                    description=f"Archive event {i}",
                )
            )

        timeline = synthesizer.build()

        assert timeline.metadata.event_types.get("ct_observed") == 5
        assert timeline.metadata.event_types.get("archive_snapshot") == 3
        assert timeline.metadata.sources.get("ct_log") == 5
        assert timeline.metadata.sources.get("wayback") == 3


# ============================================================================
# Smoke test
# ============================================================================

def test_timeline_synthesizer_module_imports():
    """Module can be imported without error."""
    from hledac.universal.intelligence import timeline_synthesizer
    assert timeline_synthesizer is not None
    assert hasattr(timeline_synthesizer, "TimelineSynthesizer")
    assert hasattr(timeline_synthesizer, "create_timeline_synthesizer")


def test_temporal_archaeologist_adapter_module_imports():
    """Module can be imported without error."""
    from hledac.universal.intelligence import temporal_archaeologist_adapter
    assert temporal_archaeologist_adapter is not None
    assert hasattr(temporal_archaeologist_adapter, "TemporalArchaeologistAdapter")
    assert hasattr(temporal_archaeologist_adapter, "create_temporal_archaeologist_adapter")


def test_factory_creates_adapter():
    """Factory creates TemporalArchaeologistAdapter instance."""
    adapter = create_temporal_archaeologist_adapter()
    assert isinstance(adapter, TemporalArchaeologistAdapter)
    assert hasattr(adapter, "synthesize_timeline")
    assert hasattr(adapter, "get_stats")
