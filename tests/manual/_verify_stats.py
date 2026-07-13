#!/usr/bin/env python3
"""Verify thread-safe stats classes import correctly."""
import sys
sys.path.insert(0, ".")

print("Testing imports...")

from intelligence.passive_fingerprint import _StatsState, _GlobalStatsState, _stats, _GLOBAL_STATS  # noqa: E402
print(f"passive_fingerprint: _stats={type(_stats).__name__}, _GLOBAL_STATS={type(_GLOBAL_STATS).__name__}")

from transport.conditional_cache import _stats as cc_stats  # noqa: E402
print(f"conditional_cache: _stats={type(cc_stats).__name__}")

from transport.http3_lane import _stats as h3_stats  # noqa: E402
print(f"http3_lane: _stats={type(h3_stats).__name__}")

from intelligence.exposure_correlator import _stats as ec_stats  # noqa: E402
print(f"exposure_correlator: _stats={type(ec_stats).__name__}")

# Test methods exist
assert hasattr(_stats, 'increment')
assert hasattr(_stats, 'set')
assert hasattr(_stats, 'snapshot')
assert hasattr(_stats, 'reset')
print("All methods present OK")

# Test basic operation
_stats.increment("test_key")
_stats.set("test_key2", 42)
snap = _stats.snapshot()
print(f"snapshot: {snap}")
assert snap["test_key"] == 1
assert snap["test_key2"] == 42

_stats.reset()
snap2 = _stats.snapshot()
assert snap2.get("test_key") == 0
print("Basic operations OK")

print("\n✅ All imports and operations verified successfully")
