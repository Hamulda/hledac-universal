#!/usr/bin/env python3
"""Test RuntimeWarning fix for suggest_scores_async."""
import asyncio
import sys
import warnings

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')

print("=== Test 1: suggest_scores from sync context (no running loop) ===")
from prefetch.prefetch_oracle_integration import PrefetchOracleIntegration

oracle = PrefetchOracleIntegration()
items = []  # empty list
result = oracle.suggest_scores(items, current_cycle=0)
print(f"OK: suggest_scores returned: {result}")

print("\n=== Test 2: suggest_scores from async context (running loop) ===")
warnings.filterwarnings('error', category=RuntimeWarning)
async def test_from_async():
    items = []
    try:
        result = oracle.suggest_scores(items, current_cycle=0)
        print(f"FAIL: Should have raised RuntimeError, got: {result}")
    except RuntimeError as e:
        expected_msg = "suggest_scores() called from running event loop"
        if expected_msg in str(e):
            print(f"OK: Correct RuntimeError raised: {e}")
        else:
            print(f"PARTIAL: RuntimeError raised but wrong message: {e}")
    except Exception as e:
        print(f"FAIL: Wrong exception type: {type(e).__name__}: {e}")

asyncio.run(test_from_async())

print("\n=== Test 3: RuntimeWarning detection ===")
warnings.filterwarnings('error', category=RuntimeWarning)
async def test_no_warning():
    items = []
    try:
        result = oracle.suggest_scores(items, current_cycle=0)
        print(f"No RuntimeWarning, returned: {result}")
    except RuntimeWarning as e:
        print(f"FAIL: RuntimeWarning detected: {e}")
    except Exception as e:
        print(f"Exception caught (expected for async context): {type(e).__name__}")

asyncio.run(test_no_warning())

print("\n=== All tests complete ===")
