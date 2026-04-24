"""
F196C: Test asyncio.run() patterns are M1-safe.

Verifies that asyncio.run() calls in critical files are properly guarded
against nested event loops which crash Metal on Apple Silicon M1.
"""
import inspect
import pytest

from hledac.universal.utils.execution_optimizer import ParallelExecutionOptimizer
from hledac.universal.network.jarm_fingerprinter import _JARMFingerprinter


class TestAsyncioRunPatterns:
    """Verify M1-safe asyncio.run() patterns."""

    def test_execution_optimizer_has_proper_async_handling(self):
        """Verify ParallelExecutionOptimizer._run_in_executor_safe handles async correctly."""
        # Check the method exists and has proper structure
        assert hasattr(ParallelExecutionOptimizer, '_run_in_executor_safe'), \
            "Should have _run_in_executor_safe method"
        source = inspect.getsource(ParallelExecutionOptimizer._run_in_executor_safe)
        # Should use asyncio.run() in the RuntimeError path
        assert "RuntimeError" in source, "Should catch RuntimeError for loop detection"
        # Should not blindly call run_until_complete without loop check
        assert "get_running_loop()" in source, "Should check for running loop"

    def test_jarm_fingerprint_uses_async_sleep_in_compute(self):
        """Verify jarm_fingerprinter _compute_jarm_async uses asyncio.sleep."""
        source = inspect.getsource(_JARMFingerprinter._compute_jarm_async)
        # Should use asyncio.sleep for rate limiting
        assert "asyncio.sleep" in source, "Should use asyncio.sleep for rate limiting"
        # Should NOT have blocking time.sleep for rate limiting delay
        # (time module is used for time.time() in other methods, but not sleep for delay)
        # Check that actual sleep call uses asyncio, not time.sleep
        actual_sleep_calls = [line.strip() for line in source.split('\n')
                           if 'sleep(' in line and 'time.sleep' in line]
        assert len(actual_sleep_calls) == 0, \
            f"Should not use blocking time.sleep for delays, found: {actual_sleep_calls}"

    def test_jarm_compute_is_async(self):
        """Verify _compute_jarm_async is an async function."""
        assert inspect.iscoroutinefunction(_JARMFingerprinter._compute_jarm_async), \
            "_compute_jarm_async should be an async function"
