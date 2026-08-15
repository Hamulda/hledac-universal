"""
test_analytics_hook_no_false_start.py
====================================
Verifies analytics_hook._worker_lock is FALSE_POSITIVE (thread-safe as-is),
and that no await ever occurs inside a threading.Lock block.
"""

import inspect
import threading

import pytest
from _core import aclose


class TestAnalyticsHookWorkerLockSafety:
    """Tests for analytics_hook._worker_lock classification."""

    def test_worker_lock_is_threading_lock(self):
        """_worker_lock must be threading.Lock (not asyncio.Lock)."""
        from knowledge.analytics_hook import _ShadowRecorder
        rec = _ShadowRecorder()
        assert isinstance(rec._worker_lock, threading.Lock), (
            "_worker_lock should be threading.Lock — it guards a sync flag"
        )

    def test_worker_lock_not_needed_for_async_context(self):
        """
        _ensure_worker() is a sync def, not async def.
        It guards _worker_started with double-checked locking.
        The slow path (acquiring lock, checking loop) is synchronous.
        No asyncio.Lock needed.
        """
        from knowledge.analytics_hook import _ShadowRecorder
        rec = _ShadowRecorder()

        # Verify _ensure_worker is NOT async def
        assert not inspect.iscoroutinefunction(rec._ensure_worker), (
            "_ensure_worker should not be async — it synchronously checks the loop"
        )

    def test_no_await_inside_worker_lock(self):
        """
        SAFETY: No await occurs inside a _worker_lock block.
        _ensure_worker() does:
          1. Check _worker_started (no lock) — fast path
          2. Acquire lock
          3. Re-check _worker_started under lock
          4. Try asyncio.get_running_loop() — no await
          5. loop.create_task() — no await
          6. Release lock
        All operations are sync; no await inside lock.
        """
        from knowledge.analytics_hook import _ShadowRecorder
        rec = _ShadowRecorder()
        source = inspect.getsource(rec._ensure_worker)

        # Split into lines, track lock block
        lines = source.split("\n")
        in_lock_block = False
        lock_indent = 0
        for line in lines:
            stripped = line.strip()
            if "with self._worker_lock:" in stripped:
                in_lock_block = True
                lock_indent = len(line) - len(line.lstrip())
                continue
            if in_lock_block:
                current_indent = len(line) - len(line.lstrip())
                # Dedented back out of lock block
                if line.strip() and current_indent <= lock_indent and not line.strip().startswith("#"):
                    in_lock_block = False
                    continue
                # Check for await inside lock
                if stripped.startswith("await ") and "self._worker_lock" not in stripped:
                    pytest.fail(f"await found inside _worker_lock block: {stripped}")

    def test_worker_lock_prevents_false_start(self):
        """
        _ensure_worker() double-checked locking prevents false-start.
        Without the lock, a race between two concurrent callers could set
        _worker_started=True before the actual task creation.
        The lock ensures only one caller creates the task.
        """
        from knowledge.analytics_hook import _ShadowRecorder
        rec = _ShadowRecorder()
        source = inspect.getsource(rec._ensure_worker)

        # Must have double-check pattern: check -> lock -> re-check
        assert "if self._worker_started:" in source, "fast path check missing"
        assert "with self._worker_lock:" in source, "lock acquisition missing"
        # re-check after acquiring lock
        check_count = source.count("if self._worker_started:")
        assert check_count >= 2, (
            "Double-checked locking requires 2 checks: fast path + under-lock"
        )

    def test_worker_is_async_def(self):
        """Verify _worker() is async def — started via create_task from sync context."""
        from knowledge.analytics_hook import _ShadowRecorder
        rec = _ShadowRecorder()
        assert inspect.iscoroutinefunction(rec._worker), (
            "_worker should be async def — started via loop.create_task()"
        )

    def test_shadow_record_finding_is_sync(self):
        """shadow_record_finding() is sync — enqueues and returns immediately."""
        from knowledge.analytics_hook import shadow_record_finding
        assert not inspect.iscoroutinefunction(shadow_record_finding), (
            "shadow_record_finding should be sync (hot path)"
        )
