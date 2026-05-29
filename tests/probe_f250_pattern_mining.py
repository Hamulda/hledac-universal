"""Smoke tests for F250 Pattern Mining Canonical Integration.

Verifies:
- pattern_mining_canonical module is importable
- PatternMiningAdapter can be created
- PatternMiningResult has correct structure
- Finding conversion methods work
- Sidecar runner is registered in sidecar_bus
- Source types are correct
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Ensure hledac.universal is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def test_pattern_mining_canonical_importable():
    """Verify pattern_mining_canonical module imports."""
    from intelligence.pattern_mining_canonical import (
        PatternCandidate,
        PatternMiningResult,
        PatternMiningAdapter,
        create_pattern_mining_adapter,
        MAX_FINDINGS,
        MAX_PATTERNS,
    )
    assert PatternCandidate is not None
    assert PatternMiningResult is not None
    assert PatternMiningAdapter is not None
    assert create_pattern_mining_adapter is not None
    assert MAX_FINDINGS == 500
    assert MAX_PATTERNS == 200
    print("PASS: pattern_mining_canonical imports")


def test_pattern_candidate_structure():
    """Verify PatternCandidate has correct structure."""
    from intelligence.pattern_mining_canonical import PatternCandidate

    cand = PatternCandidate(
        pattern_id="test_pattern_1",
        pattern_type="temporal",
        pattern_data={"trend": "INCREASING"},
        confidence=0.75,
        severity=0.5,
        description="Test pattern",
        source_findings=["finding_1", "finding_2"],
    )
    assert cand.pattern_id == "test_pattern_1"
    assert cand.pattern_type == "temporal"
    assert cand.pattern_data["trend"] == "INCREASING"
    assert cand.confidence == 0.75
    d = cand.to_dict()
    assert d["pattern_type"] == "temporal"
    print("PASS: PatternCandidate structure")


def test_pattern_mining_result_structure():
    """Verify PatternMiningResult has correct structure."""
    from intelligence.pattern_mining_canonical import PatternCandidate, PatternMiningResult

    result = PatternMiningResult()
    assert result.temporal_patterns == []
    assert result.behavioral_patterns == []
    assert result.anomalies == []
    assert result.stats == {}

    result.temporal_patterns.append(
        PatternCandidate(
            pattern_id="temp_1",
            pattern_type="temporal",
            pattern_data={},
            confidence=0.8,
            severity=0.5,
            description="Test",
            source_findings=[],
        )
    )
    assert len(result.temporal_patterns) == 1
    print("PASS: PatternMiningResult structure")


def test_adapter_creation():
    """Verify PatternMiningAdapter can be created."""
    from intelligence.pattern_mining_canonical import create_pattern_mining_adapter

    adapter = create_pattern_mining_adapter(use_mlx=True)
    assert adapter is not None
    stats = adapter.get_stats()
    assert "findings_processed" in stats
    print("PASS: PatternMiningAdapter creation")


def test_stats_tracking():
    """Verify adapter tracks statistics."""
    from intelligence.pattern_mining_canonical import create_pattern_mining_adapter

    adapter = create_pattern_mining_adapter()
    stats = adapter.get_stats()
    assert stats["findings_processed"] == 0
    assert stats["events_extracted"] == 0
    assert stats["temporal_patterns_found"] == 0
    print("PASS: stats tracking")


def test_sidecar_runner_registered():
    """Verify pattern_mining runner is registered in sidecar_bus."""
    from runtime.sidecar_bus import DEFAULT_SIDECAR_RUNNERS

    names = [name for name, _ in DEFAULT_SIDECAR_RUNNERS]
    assert "pattern_mining" in names
    print("PASS: pattern_mining registered in DEFAULT_SIDECAR_RUNNERS")


def test_heavy_sidecar_classification():
    """Verify pattern_mining is classified as heavy sidecar."""
    from runtime.sidecar_bus import _HEAVY_SIDECARS

    assert "pattern_mining" in _HEAVY_SIDECARS
    print("PASS: pattern_mining in _HEAVY_SIDECARS")


def test_network_classification():
    """Verify pattern_mining network classification."""
    from runtime.sidecar_bus import SIDECAR_NETWORK_CLASS, classify_sidecar_network

    assert "pattern_mining" in SIDECAR_NETWORK_CLASS
    assert SIDECAR_NETWORK_CLASS["pattern_mining"] == "core"
    assert classify_sidecar_network("pattern_mining") == "core"
    print("PASS: pattern_mining network classification")


def test_sidecar_runner_function():
    """Verify _pattern_mining_runner exists and is callable."""
    from runtime.sidecar_bus import _pattern_mining_runner
    import asyncio

    assert callable(_pattern_mining_runner)
    # Call with empty findings should return None (fail-soft)
    result = asyncio.run(_pattern_mining_runner([], None, "test query"))
    assert result is None
    print("PASS: _pattern_mining_runner callable and fail-soft")


def test_source_types():
    """Verify pattern source types are correct."""
    from intelligence.pattern_mining_canonical import create_pattern_mining_adapter

    adapter = create_pattern_mining_adapter()

    # Create mock findings
    class MockFinding:
        def __init__(self, fid, stype, conf):
            self.finding_id = fid
            self.source_type = stype
            self.ts = 1700000000.0
            self.query = "test query"
            self.payload_text = None

    mock_findings = [
        MockFinding("f1", "web", 0.8),
        MockFinding("f2", "ct", 0.9),
        MockFinding("f3", "dns", 0.7),
    ]

    # Test event extraction
    events = adapter._to_events(mock_findings)
    assert len(events) == 3
    print("PASS: event extraction from findings")


def test_adapter_clear():
    """Verify adapter clear method works."""
    from intelligence.pattern_mining_canonical import create_pattern_mining_adapter

    adapter = create_pattern_mining_adapter()
    adapter.clear()
    stats = adapter.get_stats()
    assert stats["findings_processed"] == 0
    print("PASS: adapter clear")


if __name__ == "__main__":
    test_pattern_mining_canonical_importable()
    test_pattern_candidate_structure()
    test_pattern_mining_result_structure()
    test_adapter_creation()
    test_stats_tracking()
    test_sidecar_runner_registered()
    test_heavy_sidecar_classification()
    test_network_classification()
    test_sidecar_runner_function()
    test_source_types()
    test_adapter_clear()
    print("\nAll F250 Pattern Mining smoke tests passed.")
