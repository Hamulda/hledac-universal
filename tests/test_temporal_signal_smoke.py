"""
Sprint F26X-GC: Temporal signal chain smoke test.

Confirms the layers package import chain is live end-to-end:
  hledac.universal.layers → temporal_signal_layer + temporal_signal_runtime

Note: "TemporalSignalRuntime" in the original sprint spec refers to the
module-level runtime accessors (get_temporal_signal_layer / observe / etc.)
NOT a single class. The actual "tick" surface is TemporalSignalLayer.observe().
"""

import sys
import time
from pathlib import Path

REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT.parent))


def test_temporal_signal_chain_end_to_end():
    """Import chain + observe() with mock event must not raise."""
    from hledac.universal.layers import (  # type: ignore[import-not-found]
        TemporalEvent,
        build_temporal_priority_hints,
        get_temporal_signal_layer,
        get_temporal_signal_summary,
        reset_temporal_signal_layer,
    )

    reset_temporal_signal_layer()  # fresh state for test isolation
    layer = get_temporal_signal_layer()
    assert layer is not None, "TemporalSignalLayer singleton returned None"

    # Observe a mock event — this is the "tick" equivalent
    score = layer.observe(TemporalEvent(ts=time.time(), key="smoke-key", family="smoke"))
    assert score is not None
    assert score.key == "smoke-key"
    assert 0.0 <= score.anomaly_score <= 1.0

    # Summary + hints must work without raising
    summary = get_temporal_signal_summary(k=5)
    hints = build_temporal_priority_hints(k=5)
    assert "state_size" in summary
    assert isinstance(hints, list)


if __name__ == "__main__":
    test_temporal_signal_chain_end_to_end()
    print("OK: temporal_signal chain end-to-end")
