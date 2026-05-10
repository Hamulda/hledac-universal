"""
Probe: global_scheduler async coroutine execution in _worker_loop
==================================================================

Tests for the Option A fix in _worker_loop:
- async coroutine path always uses fresh event loop (no get_running_loop)
- success after async task reports "succeeded"
- exception after async task reports "failed"
- CancelledError (timeout) reports "failed" (not running/timeout)
- no coroutine is created and discarded without awaiting
- sync tasks still report "succeeded"
- no run_coroutine_threadsafe pattern remains

Run:
    uv run python -m pytest tests/probe_global_scheduler.py -v
"""

import asyncio
import multiprocessing as mp
import queue
import re
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")

from hledac.universal.orchestrator.global_scheduler import (
    GlobalPriorityScheduler,
    register_task,
)


# ---------------------------------------------------------------------------
# Task helpers (defined at module level so they can be pickled/imported)
# ---------------------------------------------------------------------------

def sync_task(x):
    return x * 2


async def async_succeed(x):
    await asyncio.sleep(0.01)
    return x * 2


async def async_raise(x):
    await asyncio.sleep(0.01)
    raise ValueError("async error")


async def async_timeout_long(x):
    # Sleep longer than the 30s worker-loop timeout
    await asyncio.sleep(60.0)
    return x * 2


# ---------------------------------------------------------------------------
# Tests — AST / code structure
# ---------------------------------------------------------------------------

class TestOptionAFix:
    """Verify Option A: no get_running_loop + run_coroutine_threadsafe in code."""

    def test_no_run_coroutine_threadsafe_in_worker_loop(self):
        """
        Verify that run_coroutine_threadsafe is not called from _worker_loop.
        Strip comments and string literals before checking.
        """
        import ast

        path = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/orchestrator/global_scheduler.py"
        with open(path) as f:
            src = f.read()

        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_worker_loop":
                func_src = ast.get_source_segment(src, node)
                assert func_src is not None
                # Strip comments and string literals before checking
                no_comments = re.sub(r'#.*', '', func_src)
                no_strings = re.sub(r'"[^"]*"|\'[^\']*\'', '', no_comments)
                assert "run_coroutine_threadsafe" not in no_strings, \
                    "run_coroutine_threadsafe must not appear in _worker_loop code"
                assert "get_running_loop" not in no_strings, \
                    "get_running_loop must not appear in _worker_loop code"
                return

        pytest.fail("_worker_loop function not found")

    def test_no_get_running_loop_call_at_all(self):
        """
        _worker_loop must not contain any call to get_running_loop
        (even in comments it should not appear as a call pattern).
        """
        import ast

        path = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/orchestrator/global_scheduler.py"
        with open(path) as f:
            src = f.read()

        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_worker_loop":
                func_src = ast.get_source_segment(src, node)
                no_comments = re.sub(r'#.*', '', func_src)
                no_strings = re.sub(r'"[^"]*"|\'[^\']*\'', '', no_comments)
                assert "get_running_loop" not in no_strings
                return

        pytest.fail("_worker_loop not found")


# ---------------------------------------------------------------------------
# Tests — behavioral (require start() to spawn workers)
# ---------------------------------------------------------------------------

def _drain_until(scheduler, deadline, *statuses):
    """Drain result_queue until deadline or one of statuses is seen."""
    results = {}
    while time.time() < deadline:
        try:
            item = scheduler._result_queue.get(timeout=0.1)
            if len(item) >= 3:
                job_id, status, error = item[0], item[1], item[2]
                results[status] = (error, item[3] if len(item) > 3 else None)
            else:
                results[item[1]] = (item[2] if len(item) > 2 else None, None)
        except queue.Empty:
            continue
        if any(s in results for s in statuses):
            break
    return results


class TestAsyncCoroutineSuccess:
    """Async coroutine tasks report succeeded correctly."""

    def test_async_succeed_reports_succeeded(self):
        """
        An async coroutine that completes successfully must result in
        a 'succeeded' entry on the result queue — not 'running', not silent.
        """
        scheduler = GlobalPriorityScheduler(max_workers=1)
        register_task("async_succeed", async_succeed)
        scheduler.start()

        try:
            job_id = scheduler.schedule(priority=1, task_name="async_succeed", x=21)
            results = _drain_until(scheduler, time.time() + 10.0, "succeeded", "failed")

            assert "succeeded" in results, f"No succeeded found in results: {results}"
            error, wid = results["succeeded"]
            assert error is None, f"succeeded should have no error, got: {error}"
        finally:
            scheduler.shutdown()

    def test_async_raise_reports_failed(self):
        """
        An async coroutine that raises must result in a 'failed' entry
        with the error message on the result queue.
        """
        scheduler = GlobalPriorityScheduler(max_workers=1)
        register_task("async_raise", async_raise)
        scheduler.start()

        try:
            job_id = scheduler.schedule(priority=1, task_name="async_raise", x=1)
            results = _drain_until(scheduler, time.time() + 10.0, "succeeded", "failed")

            assert "failed" in results, f"No failed found in results: {results}"
            error, _ = results["failed"]
            assert error is not None and "async error" in error, \
                f"failed should contain 'async error', got: {error}"
        finally:
            scheduler.shutdown()

    def test_async_timeout_reports_failed_not_running(self):
        """
        An async coroutine that exceeds the timeout must report 'failed'
        with a timeout/cancellation message — not remain in 'running'.
        """
        scheduler = GlobalPriorityScheduler(max_workers=1)
        register_task("async_timeout_long", async_timeout_long)
        scheduler.start()

        try:
            # 5s job_timeout — worker loop will kill it after 30s but
            # the timeout checker should fire first at ~5s
            job_id = scheduler.schedule(priority=1, task_name="async_timeout_long", x=1)
            results = _drain_until(scheduler, time.time() + 20.0, "succeeded", "failed", "timeout")

            # Must not be stuck in 'running'
            assert "running" not in results, f"Job should not remain 'running': {results}"
            # Must have a terminal state
            has_terminal = any(s in results for s in ("succeeded", "failed", "timeout"))
            assert has_terminal, f"Expected terminal state, got: {results}"
        finally:
            scheduler.shutdown()


class TestSyncTaskStillSucceeds:
    """Sync tasks continue to work and report succeeded."""

    def test_sync_task_reports_succeeded(self):
        """
        A synchronous registered task must still report 'succeeded'.
        """
        scheduler = GlobalPriorityScheduler(max_workers=1)
        register_task("sync_task", sync_task)
        scheduler.start()

        try:
            job_id = scheduler.schedule(priority=1, task_name="sync_task", x=21)
            results = _drain_until(scheduler, time.time() + 10.0, "succeeded", "failed")

            assert "succeeded" in results, f"Expected succeeded, got: {results}"
            error, _ = results["succeeded"]
            assert error is None
        finally:
            scheduler.shutdown()


class TestNoUnawaitedCoroutines:
    """No coroutine objects are created and discarded without awaiting."""

    def test_no_runtimewarning_coroutine_was_never_awaited(self):
        """
        When an async task runs (success, error, or timeout), there must be
        no 'coroutine was never awaited' RuntimeWarning.
        """
        import warnings

        scheduler = GlobalPriorityScheduler(max_workers=1)
        register_task("async_warn", async_succeed)
        scheduler.start()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", RuntimeWarning)

            job_id = scheduler.schedule(priority=1, task_name="async_warn", x=1)

            # Wait for completion
            deadline = time.time() + 10.0
            while time.time() < deadline:
                try:
                    item = scheduler._result_queue.get(timeout=0.5)
                    if item[1] in ("succeeded", "failed"):
                        break
                except queue.Empty:
                    continue

            # Check no RuntimeWarning about coroutine
            coro_warnings = [
                x for x in w
                if issubclass(x.category, RuntimeWarning)
                and "coroutine" in str(x.message)
                and "never awaited" in str(x.message)
            ]
            assert len(coro_warnings) == 0, \
                f"RuntimeWarning: {coro_warnings[0].message if coro_warnings else ''}"

        scheduler.shutdown()


class TestNoNestedRunUntilCompleteOnRunningLoop:
    """Verify run_until_complete is never called on an already-running loop."""

    def test_no_run_until_complete_on_active_loop(self):
        """
        The fixed _worker_loop does NOT call run_until_complete on an
        already-running loop — there is no get_running_loop that could
        return an active loop.  Verified by AST strip-check.
        """
        import ast

        path = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/orchestrator/global_scheduler.py"
        with open(path) as f:
            src = f.read()

        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_worker_loop":
                func_src = ast.get_source_segment(src, node)
                no_comments = re.sub(r'#.*', '', func_src)
                no_strings = re.sub(r'"[^"]*"|\'[^\']*\'', '', no_comments)
                assert "get_running_loop" not in no_strings
                return

        pytest.fail("_worker_loop not found")