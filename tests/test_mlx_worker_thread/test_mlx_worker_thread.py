"""
Sprint P0-3 tests — MLXWorkerThread.

Covers invariants declared in brain/mlx_worker_thread.py:
    M.T1  Single thread, single model — no concurrent MLX
    M.T2  Lazy start: thread created on first submit()
    M.T3  Fail-soft: start failure or thread death → submit raises RuntimeError
    M.T4  Bounded shutdown ≤ 5.0s
    M.T5  request_count telemetry
    M.T6  Default submit timeout 60s
    M.T7  is_alive() check on every submit
    M.T8  Daemon thread — never blocks process exit

Pattern mirrors test_mlx_batched_executor — uses importlib.util to bypass
brain/__init__.py chain. asyncio.run() for cleanup.
"""


import asyncio
import importlib.util
import os
import sys
import time
import types
import unittest

# ─── Bypass brain/__init__.py ──────────────────────────────────────────
_BRAIN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "brain")
)


def _load_isolated(name: str) -> types.ModuleType:
    path = os.path.join(_BRAIN_DIR, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"brain.{name}", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    sys.modules[f"brain.{name}"] = mod
    return mod


_hledac = types.ModuleType("hledac")
sys.modules["hledac"] = _hledac
_universal = types.ModuleType("hledac.universal")
sys.modules["hledac.universal"] = _universal
_brain_pkg = types.ModuleType("hledac.universal.brain")
_brain_pkg.__path__ = [_BRAIN_DIR]
sys.modules["hledac.universal.brain"] = _brain_pkg

_mwt = _load_isolated("mlx_worker_thread")
sys.modules["hledac.universal.brain.mlx_worker_thread"] = _mwt

MLXWorkerThread = _mwt.MLXWorkerThread  # type: ignore[attr-defined]
DEFAULT_SUBMIT_TIMEOUT_S = _mwt.DEFAULT_SUBMIT_TIMEOUT_S  # type: ignore[attr-defined]
THREAD_START_TIMEOUT_S = _mwt.THREAD_START_TIMEOUT_S  # type: ignore[attr-defined]
SHUTDOWN_TIMEOUT_S = _mwt.SHUTDOWN_TIMEOUT_S  # type: ignore[attr-defined]


# ─── Helpers ────────────────────────────────────────────────────────────


def _run(coro):
    """Run coroutine with fresh event loop. Closes loop on exit."""
    return asyncio.run(coro)


# ─── Invariant tests ────────────────────────────────────────────────────


class TestMLXWorkerThreadInvariants(unittest.TestCase):
    """M.T1, M.T2, M.T8 — Lazy start, daemon, single thread."""

    def test_mt2_no_thread_on_construction(self):
        """M.T2: thread is None at __init__ time."""
        worker = MLXWorkerThread()
        self.assertIsNone(worker._thread)
        self.assertIsNone(worker._loop)
        self.assertFalse(worker.is_active())

    def test_mt8_daemon_thread(self):
        """M.T8: thread is daemon — never blocks process exit."""
        worker = MLXWorkerThread()
        worker.start()
        self.assertTrue(worker._thread is not None)
        self.assertTrue(worker._thread.daemon)
        worker.shutdown()

    def test_start_is_idempotent(self):
        """start() called multiple times is safe."""
        worker = MLXWorkerThread()
        worker.start()
        first_thread = worker._thread
        worker.start()  # second call — should not create a new thread
        self.assertIs(worker._thread, first_thread)
        worker.shutdown()

    def test_constants_bounded(self):
        """Verify M.T6/M.T4 default values match docs."""
        self.assertEqual(DEFAULT_SUBMIT_TIMEOUT_S, 60.0)
        self.assertEqual(SHUTDOWN_TIMEOUT_S, 5.0)
        self.assertEqual(THREAD_START_TIMEOUT_S, 5.0)


class TestMLXWorkerThreadLifecycle(unittest.TestCase):
    """M.T3, M.T4 — start, submit, shutdown, fail-soft."""

    def tearDown(self):
        # Cleanup any running workers
        for attr in ("worker",):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.shutdown(timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass

    def test_mt3_start_creates_active_worker(self):
        """After start(), is_active() returns True."""
        worker = MLXWorkerThread()
        worker.start()
        self.assertTrue(worker.is_active())
        self.assertTrue(worker._thread.is_alive())
        worker.shutdown()

    def test_mt4_shutdown_is_idempotent(self):
        """shutdown() called multiple times is safe."""
        worker = MLXWorkerThread()
        worker.start()
        worker.shutdown()
        worker.shutdown()  # second call — should be no-op
        self.assertIsNone(worker._thread)
        self.assertIsNone(worker._loop)

    def test_mt4_shutdown_unstarted_worker(self):
        """shutdown() on never-started worker is safe no-op."""
        worker = MLXWorkerThread()
        worker.shutdown()  # must not raise
        self.assertIsNone(worker._thread)

    def test_mt7_is_active_after_shutdown(self):
        """After shutdown, is_active() returns False."""
        worker = MLXWorkerThread()
        worker.start()
        worker.shutdown()
        self.assertFalse(worker.is_active())


class TestMLXWorkerThreadSubmit(unittest.TestCase):
    """M.T1, M.T3, M.T5, M.T7 — submit, fail-soft, telemetry."""

    def tearDown(self):
        w = getattr(self, "worker", None)
        if w is not None:
            try:
                w.shutdown(timeout=1.0)
            except Exception:  # noqa: BLE001
                pass

    def test_submit_simple_coroutine(self):
        """submit() schedules a coroutine on worker loop and returns result."""
        worker = MLXWorkerThread()
        worker.start()

        async def _echo(x: int) -> int:
            return x * 2

        result = _run(worker.submit(_echo(21)))
        self.assertEqual(result, 42)
        worker.shutdown()

    def test_mt3_submit_without_start_raises(self):
        """M.T3: submit on unstarted worker → raises RuntimeError after lazy start fails."""
        # Make start() fail by patching the import to raise.
        # Simpler: create worker, force _failed=True, verify submit raises.
        worker = MLXWorkerThread()
        worker._failed = True
        worker._failure_reason = "test_simulated"
        with self.assertRaises(RuntimeError) as ctx:
            _run(worker.submit(_trivial()))
        self.assertIn("mlx_worker_unavailable", str(ctx.exception))
        self.assertIn("test_simulated", str(ctx.exception))

    def test_mt3_submit_after_thread_death_raises(self):
        """M.T3: submit after thread death → raises RuntimeError."""
        worker = MLXWorkerThread()
        worker.start()
        # Simulate thread death by clearing state
        worker._failed = True
        worker._failure_reason = "thread_died"
        with self.assertRaises(RuntimeError):
            _run(worker.submit(_trivial()))

    def test_mt1_main_loop_not_blocked(self):
        """M.T1: hlavní asyncio loop is FREE to process other coroutines
        while inference runs in worker thread.

        Pattern: submit a coroutine that sleeps 0.3s, while main loop
        runs a counter that increments every 50ms. If main loop is blocked,
        counter stays at 0. If main loop is free, counter increments.
        """
        worker = MLXWorkerThread()
        worker.start()

        counter = {"n": 0}

        async def _inf_sleep():
            # Simulate slow MLX inference in worker thread
            await asyncio.sleep(0.3)
            return "done"

        async def _ticker():
            # Ticker that runs on MAIN loop while _inf_sleep runs in worker
            for _ in range(5):
                await asyncio.sleep(0.05)
                counter["n"] += 1
            return counter["n"]

        async def _main():
            # Schedule inference and ticker concurrently
            t1 = asyncio.create_task(worker.submit(_inf_sleep()))
            t2 = asyncio.create_task(_ticker())
            r1 = await t1
            r2 = await t2
            return r1, r2

        result, ticks = _run(_main())
        self.assertEqual(result, "done")
        # If main loop is free, ticker should run all 5 iterations
        self.assertEqual(ticks, 5, f"main loop blocked: ticks={ticks}")
        worker.shutdown()

    def test_mt5_telemetry_request_count(self):
        """M.T5: get_stats() tracks request_count."""
        worker = MLXWorkerThread()
        worker.start()

        async def _noop():
            return 1

        _run(worker.submit(_noop()))
        _run(worker.submit(_noop()))
        _run(worker.submit(_noop()))

        stats = worker.get_stats()
        self.assertEqual(stats["request_count"], 3)
        self.assertGreaterEqual(stats["peak_inflight"], 1)
        worker.shutdown()

    def test_submit_timeout_raises(self):
        """submit() with timeout < coro duration raises TimeoutError."""
        worker = MLXWorkerThread()
        worker.start()

        async def _slow():
            await asyncio.sleep(1.0)
            return "should_not_reach"

        with self.assertRaises((asyncio.TimeoutError, TimeoutError)):
            _run(worker.submit(_slow(), timeout=0.1))
        worker.shutdown()

    def test_submit_exception_propagates(self):
        """Coroutine that raises → exception propagates to caller."""
        worker = MLXWorkerThread()
        worker.start()

        async def _fail():
            raise ValueError("simulated failure")

        with self.assertRaises(ValueError) as ctx:
            _run(worker.submit(_fail()))
        self.assertIn("simulated failure", str(ctx.exception))
        worker.shutdown()


class TestMLXWorkerThreadStats(unittest.TestCase):
    """M.T5 — Telemetry snapshots."""

    def tearDown(self):
        w = getattr(self, "worker", None)
        if w is not None:
            try:
                w.shutdown(timeout=1.0)
            except Exception:  # noqa: BLE001
                pass

    def test_get_stats_keys(self):
        """get_stats() returns expected keys."""
        worker = MLXWorkerThread()
        worker.start()
        stats = worker.get_stats()
        for key in (
            "active",
            "failed",
            "failure_reason",
            "request_count",
            "inflight_count",
            "peak_inflight",
            "thread_alive",
            "thread_name",
            "thread_id",
            "uptime_s",
        ):
            self.assertIn(key, stats, f"missing key: {key}")
        self.assertEqual(stats["thread_name"], "mlx-worker")
        worker.shutdown()

    def test_get_stats_uptime_increases(self):
        """uptime_s increases between samples."""
        worker = MLXWorkerThread()
        worker.start()
        s1 = worker.get_stats()["uptime_s"]
        time.sleep(0.1)
        s2 = worker.get_stats()["uptime_s"]
        self.assertGreater(s2, s1)
        worker.shutdown()


class TestMLXWorkerThreadRepr(unittest.TestCase):
    """Defensive: __repr__ is informational, must not raise."""

    def tearDown(self):
        w = getattr(self, "worker", None)
        if w is not None:
            try:
                w.shutdown(timeout=1.0)
            except Exception:  # noqa: BLE001
                pass

    def test_repr_unstarted(self):
        worker = MLXWorkerThread()
        r = repr(worker)
        self.assertIn("MLXWorkerThread", r)
        self.assertIn("stopped", r)

    def test_repr_active(self):
        worker = MLXWorkerThread()
        worker.start()
        r = repr(worker)
        self.assertIn("active", r)
        worker.shutdown()

    def test_repr_failed(self):
        worker = MLXWorkerThread()
        worker._failed = True
        worker._failure_reason = "test"
        r = repr(worker)
        self.assertIn("failed", r)
        self.assertIn("test", r)


# ─── Helpers ────────────────────────────────────────────────────────────


async def _trivial() -> None:
    return None


if __name__ == "__main__":
    unittest.main()
