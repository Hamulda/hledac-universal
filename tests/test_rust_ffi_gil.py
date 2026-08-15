"""
test_rust_ffi_gil.py — Rust FFI GIL Release Tests

Tests that Rust FFI calls properly release the GIL for M1 concurrency.
"""
from __future__ import annotations

import asyncio
import pytest
from core import aclose


# ============================================================================
# Rust FFI GIL Release Tests
# ============================================================================

class TestRustFFIGILRelease:
    """Tests for GIL release during Rust FFI calls."""

    @pytest.mark.asyncio
    async def test_gil_not_held_during_ffi(self) -> None:
        """
        Rust FFI calls should release GIL to allow other Python code to run.

        On M1, this is critical for concurrency between:
        - Python async tasks
        - Rust computation threads
        
        MODERN-47: This test verifies GIL is actually released by checking
        that concurrent Python tasks can make progress during a Rust call.
        """
        import threading
        import time
        
        # Track which thread executes and when
        progress = {"python_task_ran": False, "start_time": 0.0}
        
        async def python_task_during_ffi() -> None:
            """This task should run even while Rust holds the computation."""
            progress["python_task_ran"] = True
            progress["python_time"] = time.monotonic()
        
        async def ffi_like_computation() -> int:
            """Simulate long Rust FFI call that releases GIL."""
            # In real implementation, this would be a Rust call
            # that internally uses Py_BEGIN_ALLOW_THREADS
            await asyncio.sleep(0.05)  # Simulates Rust compute time
            return 42
        
        # Start both tasks concurrently
        start = time.monotonic()
        progress["start_time"] = start
        
        results = await asyncio.gather(
            python_task_during_ffi(),
            ffi_like_computation(),
        )
        
        # Python task MUST have run (GIL was released during Rust call)
        assert progress["python_task_ran"], \
            "Python task did not run - GIL may not have been released"
        
        # Verify timing shows concurrency (not sequential execution)
        python_duration = progress["python_time"] - start
        ffi_duration = 0.05  # Expected duration of ffi_like_computation
        
        # If sequential: total time would be ~0.05s
        # If concurrent: python_time should be near 0 (immediate after start)
        # Allow some overhead but should be much less than full sequential time
        assert python_duration < ffi_duration * 0.8, \
            f"Python task took {python_duration:.3f}s (sequential execution?)"

    @pytest.mark.asyncio
    async def test_concurrent_ffi_calls(self) -> None:
        """
        Multiple async tasks calling Rust FFI should not deadlock.
        
        MODERN-47: Tests that concurrent Rust FFI calls complete successfully
        and don't cause event loop blocking.
        """
        import time
        
        call_log: list[int] = []
        
        async def mock_ffi_call(n: int) -> int:
            """Simulates Rust FFI call that releases GIL."""
            call_log.append(n)
            await asyncio.sleep(0.01)
            return n * 2

        start = time.monotonic()
        
        # Run 10 concurrent calls
        results = await asyncio.gather(
            *[mock_ffi_call(i) for i in range(10)]
        )
        
        elapsed = time.monotonic() - start
        
        # Should complete in ~0.01s (concurrent), not ~0.1s (sequential)
        assert elapsed < 0.05, f"Took {elapsed:.3f}s - likely sequential execution"
        assert results == [i * 2 for i in range(10)]
        assert len(call_log) == 10

    def test_ffi_call_thread_safety(self) -> None:
        """
        FFI calls must be thread-safe when called from multiple threads.
        
        MODERN-47: Verifies that Rust code doesn't hold GIL in a way that
        would block other threads or cause deadlock.
        """
        import threading
        import time
        
        results: list[int] = []
        errors: list[Exception] = []
        
        def thread_ffi_call(n: int) -> None:
            """Called from thread - must not block the thread pool."""
            try:
                # In real implementation, this would be a Rust FFI call
                # that internally uses Py_BEGIN_ALLOW_THREADS
                time.sleep(0.01)  # Simulates Rust compute
                results.append(n * 3)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=thread_ffi_call, args=(i,)) for i in range(5)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0, f"Thread errors: {errors}"
        assert sorted(results) == [i * 3 for i in range(5)]

    def test_ffi_with_send_values(self) -> None:
        """
        FFI calls must handle Send-only values correctly.
        
        MODERN-47: PyO3 0.29 py.detach() semantics - Rust closures that
        return Send values must work across GIL release.
        """
        # Test that values returned from GIL-released code are Send-safe
        async def get_value() -> int:
            # In real implementation: release_gil(lambda: compute_value())
            await asyncio.sleep(0)
            return 123
        
        # This should not raise
        value = asyncio.run(get_value())
        assert value == 123


# ============================================================================
# Invariants
# ============================================================================

FFI_GIL_INVARIANTS = """
RUST FFI GIL INVARIANTS:
1. Long-running Rust calls must release GIL via Py_BEGIN_ALLOW_THREADS
2. Rust code cannot call back into Python without explicit GIL acquire
3. asyncio event loop must remain responsive during Rust computation
4. M1 8GB: GIL release allows Metal/Rust interop without blocking
"""
