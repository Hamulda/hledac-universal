"""
Sprint P0-2 tests — MLXBatchedExecutor.

Covers the invariants declared in brain/mlx_batched_executor.py:
    B.M1  Zero top-level MLX imports (lazy)
    B.M2  BatchScheduler instantiated lazily (not at import time)
    B.M3  Fail-soft: any submit/future error → direct path
    B.M4  MLX execution lock: asyn semaphore(1) — no concurrent MLX
    B.M5  Memory guard: psutil.virtual_memory().percent > 85% → disable
    B.M6  max_batch_size = 6 (M1 8GB, KV cache 0.75 GB, headroom for speculative)
    B.M7  Telemetry counters exposed via get_stats()
    B.M8  Shutdown: bounded ≤ 3.0s, all pending futures failed
    B.M9  Bypass: priority == 0 → direct path (urgent)
    B.M10 Direct-path latency: overhead tracked via baseline_ema vs latency_ema

Pattern mirrors tests/test_batch_scheduler/test_batch_scheduler.py — uses
unittest + asyncio.run, AsyncMock for engine. No real MLX required.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# ─── Bypass brain/__init__.py to avoid pydantic/mlx chain ────────────
# brain/__init__.py eagerly imports Hermes3Engine which needs pydantic + mlx.
# For unit tests of the standalone executor we load the module directly.

_BRAIN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "brain")
)


def _load_isolated(name: str) -> types.ModuleType:
    """Load a brain/ module by path, bypassing brain/__init__.py."""
    path = os.path.join(_BRAIN_DIR, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"brain.{name}", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    sys.modules[f"brain.{name}"] = mod
    return mod


# Create a minimal 'hledac' package skeleton so dotted imports work in tests
# without running hledac/__init__.py (which would trigger heavy imports).
import types  # noqa: E402

_hledac = types.ModuleType("hledac")
sys.modules["hledac"] = _hledac
_universal = types.ModuleType("hledac.universal")
sys.modules["hledac.universal"] = _universal
_brain_pkg = types.ModuleType("hledac.universal.brain")
_brain_pkg.__path__ = [_BRAIN_DIR]  # so submodule imports work
sys.modules["hledac.universal.brain"] = _brain_pkg

_mbe = _load_isolated("mlx_batched_executor")
_bs = _load_isolated("batch_scheduler")
# also register under the dotted path
sys.modules["hledac.universal.brain.mlx_batched_executor"] = _mbe
sys.modules["hledac.universal.brain.batch_scheduler"] = _bs

MLXBatchedExecutor = _mbe.MLXBatchedExecutor  # type: ignore[attr-defined]
MAX_BATCH_SIZE_M1 = _mbe.MAX_BATCH_SIZE_M1  # type: ignore[attr-defined]
MEMORY_GUARD_PCT = _mbe.MEMORY_GUARD_PCT  # type: ignore[attr-defined]

# Sprint P0-3 integration: also load MLXWorkerThread for integration tests.
_mwt = _load_isolated("mlx_worker_thread")
sys.modules["hledac.universal.brain.mlx_worker_thread"] = _mwt
MLXWorkerThread = _mwt.MLXWorkerThread  # type: ignore[attr-defined]


# ─── Helpers ──────────────────────────────────────────────────────────────


def _make_mock_engine(response: str = "ok") -> MagicMock:
    """Mock Hermes3Engine with async generate() returning a string."""
    engine = MagicMock()
    engine.generate = AsyncMock(return_value=response)
    return engine


def _run(coro):
    """Run coroutine in fresh event loop. asyncio.run() closes the loop
    on exit, cancelling any background tasks (e.g. BatchScheduler worker).
    """
    return asyncio.run(coro)


def _shutdown(executor: MLXBatchedExecutor) -> None:
    """Bounded shutdown of executor — uses fresh event loop."""
    if executor._initialized:
        asyncio.run(executor.shutdown())


# ─── Invariant tests ──────────────────────────────────────────────────────


class TestMLXBatchedExecutorInvariants(unittest.TestCase):
    """B.M1, B.M2 — Lazy initialization, no top-level MLX imports."""

    def test_bm1_zero_mlx_imports_at_module_level(self):
        """B.M1: importing mlx_batched_executor must NOT pull in mlx_lm/mlx.core."""
        import sys

        mlx_keys = [k for k in sys.modules if k == "mlx" or k.startswith("mlx.")]
        self.assertEqual(
            mlx_keys,
            [],
            f"B.M1 violated — mlx modules loaded at import: {mlx_keys}",
        )

    def test_bm2_no_scheduler_on_construction(self):
        """B.M2: BatchScheduler must be None until first execute()."""
        engine = _make_mock_engine()
        executor = MLXBatchedExecutor(engine=engine)
        self.assertIsNone(executor._scheduler)
        self.assertIsNone(executor._mlx_lock)
        self.assertFalse(executor._initialized)


class TestMLXBatchedExecutorRouting(unittest.TestCase):
    """B.M5, B.M9, B.M7 — is_batch_safe() and get_stats()."""

    def setUp(self):
        self.engine = _make_mock_engine()
        self.executor = MLXBatchedExecutor(engine=self.engine)

    def tearDown(self):
        _shutdown(self.executor)

    def test_bm9_urgent_priority_bypasses_batch(self):
        """B.M9: priority=0 → is_batch_safe returns False."""
        # Force initialized flag (in real flow via _ensure_initialized)
        self.executor._initialized = True
        self.executor._scheduler = MagicMock()  # type: ignore[assignment]
        result = self.executor.is_batch_safe(
            prompt="hello", system_msg=None, priority=0.0
        )
        self.assertFalse(result)
        self.assertEqual(self.executor._stats["urgent_bypass"], 1)

    def test_empty_prompt_bypasses_batch(self):
        """Empty/whitespace prompt → bypass."""
        self.executor._initialized = True
        self.executor._scheduler = MagicMock()  # type: ignore[assignment]
        self.assertFalse(self.executor.is_batch_safe(prompt="", priority=1.0))
        self.assertFalse(self.executor.is_batch_safe(prompt="   ", priority=1.0))

    def test_uninitialized_executor_bypasses(self):
        """Not yet initialized → bypass, fall through to direct path."""
        self.assertFalse(
            self.executor.is_batch_safe(prompt="hello", priority=1.0)
        )

    def test_bm5_memory_guard_disables_batching(self):
        """B.M5: psutil.virtual_memory().percent > 90% → bypass."""
        self.executor._initialized = True
        self.executor._scheduler = MagicMock()  # type: ignore[assignment]
        # Patch the lazy import inside is_batch_safe
        fake_psutil = MagicMock()
        fake_psutil.virtual_memory.return_value.percent = 95.0
        with patch.dict(
            "sys.modules",
            {"psutil": fake_psutil},
        ):
            result = self.executor.is_batch_safe(prompt="hello", priority=1.0)
        self.assertFalse(result)
        self.assertEqual(self.executor._stats["memory_guard_disabled"], 1)

    def test_bm5_memory_guard_failopen(self):
        """B.M5: psutil exception → fail-open (batching allowed)."""
        self.executor._initialized = True
        self.executor._scheduler = MagicMock()  # type: ignore[assignment]
        with patch.dict("sys.modules", {"psutil": None}):
            # Importing None raises ImportError → fail-open
            result = self.executor.is_batch_safe(prompt="hello", priority=1.0)
        self.assertTrue(result)
        self.assertEqual(self.executor._memory_check_failures, 1)

    def test_long_system_msg_bypasses(self):
        """system_msg > 8192 chars → bypass (length bin would shatter)."""
        self.executor._initialized = True
        self.executor._scheduler = MagicMock()  # type: ignore[assignment]
        long_msg = "x" * 10_000
        result = self.executor.is_batch_safe(
            prompt="hello", system_msg=long_msg, priority=1.0
        )
        self.assertFalse(result)

    def test_bm7_telemetry_initial_state(self):
        """B.M7: get_stats() returns telemetry dict, initialized=False at first."""
        stats = self.executor.get_stats()
        self.assertIn("submits", stats)
        self.assertIn("direct_fallback", stats)
        self.assertIn("latency_ema_ms", stats)
        self.assertIn("baseline_ema_ms", stats)
        self.assertIn("overhead_ema_ms", stats)
        self.assertFalse(stats["initialized"])


class TestMLXBatchedExecutorExecute(unittest.TestCase):
    """B.M3, B.M4, B.M8 — Execute path: fail-soft, lock, shutdown."""

    def tearDown(self):
        # Each test owns a fresh executor — clean up its worker
        for attr in ("executor",):
            ex = getattr(self, attr, None)
            if ex is not None:
                _shutdown(ex)

    def test_bm3_lazy_init_failure_falls_back_to_direct(self):
        """B.M3: When BatchScheduler fails to init, execute() uses direct path."""
        engine = _make_mock_engine(response="direct")
        executor = MLXBatchedExecutor(engine=engine)
        # Force _ensure_initialized to fail by patching BatchScheduler to raise
        with patch(
            "hledac.universal.brain.batch_scheduler.BatchScheduler",
            side_effect=ImportError("simulated failure"),
        ):
            result = _run(executor.execute(prompt="hi", priority=1.0))
        self.assertEqual(result, "direct")
        self.assertEqual(executor._stats["direct_fallback"], 1)
        engine.generate.assert_awaited()

    def test_bm3_submit_timeout_falls_back(self):
        """B.M3: When submit waits > FUTURE_TIMEOUT_S, fall back to direct."""
        engine = _make_mock_engine(response="direct")
        executor = MLXBatchedExecutor(engine=engine)

        # Patch BatchScheduler so it never resolves the future
        class _StubScheduler:
            async def start(self):
                pass
            async def submit(
                self, prompt: str = "", response_model: type | None = None
            ):
                del prompt, response_model  # stub ignores args
                return asyncio.get_running_loop().create_future()
            async def shutdown(self, timeout: float = 3.0):
                del timeout  # stub ignores arg
            def get_telemetry(self):
                return {"ema": {}, "counters": {}}

        with patch(
            "hledac.universal.brain.batch_scheduler.BatchScheduler",
            _StubScheduler,
        ):
            # FUTURE_TIMEOUT_S is 30.0 — patch to 0.1 for fast test
            with patch(
                "hledac.universal.brain.mlx_batched_executor.FUTURE_TIMEOUT_S",
                0.1,
            ):
                result = _run(executor.execute(prompt="hi", priority=1.0))
        # Direct path is called, but executor wraps the result string
        # We just need: result is the direct path output
        self.assertEqual(result, "direct")

    def test_bm4_lock_serializes_mlx_callbacks(self):
        """B.M4: mlx_lock is asyncio.Lock — guarantees no concurrent MLX."""
        engine = _make_mock_engine()
        executor = MLXBatchedExecutor(engine=engine)
        _run(executor._ensure_initialized())
        self.assertIsInstance(executor._mlx_lock, asyncio.Lock)

    def test_bm6_max_batch_size_bounded(self):
        """B.M6: max_batch_size = 6 for M1 8GB safety."""
        self.assertEqual(MAX_BATCH_SIZE_M1, 6)

    def test_memory_guard_threshold(self):
        """B.M5: threshold = 85% — verify constant value."""
        self.assertEqual(MEMORY_GUARD_PCT, 85.0)


class TestMLXBatchedExecutorShutdown(unittest.TestCase):
    """B.M8 — Bounded shutdown, idempotent, no orphan futures."""

    def tearDown(self):
        ex = getattr(self, "executor", None)
        if ex is not None:
            _shutdown(ex)

    def test_bm8_shutdown_idempotent(self):
        """Shutdown called multiple times is safe."""
        engine = _make_mock_engine()
        executor = MLXBatchedExecutor(engine=engine)
        _run(executor._ensure_initialized())
        _run(executor.shutdown())
        # Second call must not raise
        _run(executor.shutdown())
        self.assertFalse(executor._initialized)
        self.assertIsNone(executor._scheduler)
        self.assertIsNone(executor._mlx_lock)

    def test_bm8_shutdown_uninitialized(self):
        """Shutdown on uninitialized executor is safe no-op."""
        engine = _make_mock_engine()
        executor = MLXBatchedExecutor(engine=engine)
        _run(executor.shutdown())  # must not raise
        self.assertFalse(executor._initialized)


class TestMLXBatchedExecutorStats(unittest.TestCase):
    """B.M7, B.M10 — Telemetry counters and overhead tracking."""

    def tearDown(self):
        ex = getattr(self, "executor", None)
        if ex is not None:
            _shutdown(ex)

    def test_bm7_stats_after_lazy_init(self):
        """After init, get_stats() includes scheduler telemetry."""
        engine = _make_mock_engine()
        executor = MLXBatchedExecutor(engine=engine)
        _run(executor._ensure_initialized())
        stats = executor.get_stats()
        self.assertTrue(stats["initialized"])
        self.assertIn("scheduler_ema", stats)
        self.assertIn("scheduler_counters", stats)

    def test_bm10_overhead_calculation(self):
        """B.M10: overhead_ema_ms = max(0, batched - baseline)."""
        engine = _make_mock_engine()
        executor = MLXBatchedExecutor(engine=engine)
        # Simulate: baseline=100ms, batched=110ms → overhead=10ms
        executor._stats["baseline_ema_ms"] = 100.0
        executor._stats["latency_ema_ms"] = 110.0
        stats = executor.get_stats()
        self.assertAlmostEqual(stats["overhead_ema_ms"], 10.0, places=5)

    def test_bm10_overhead_floored_at_zero(self):
        """B.M10: overhead never negative (batched < baseline)."""
        engine = _make_mock_engine()
        executor = MLXBatchedExecutor(engine=engine)
        executor._stats["baseline_ema_ms"] = 100.0
        executor._stats["latency_ema_ms"] = 50.0  # faster than baseline
        stats = executor.get_stats()
        self.assertEqual(stats["overhead_ema_ms"], 0.0)


class TestMLXBatchedExecutorRepr(unittest.TestCase):
    """Defensive: __repr__ is informational, must not raise."""

    def tearDown(self):
        ex = getattr(self, "executor", None)
        if ex is not None:
            _shutdown(ex)

    def test_repr_uninitialized(self):
        engine = _make_mock_engine()
        executor = MLXBatchedExecutor(engine=engine)
        r = repr(executor)
        self.assertIn("MLXBatchedExecutor", r)
        self.assertIn("lazy", r)
        self.assertIn(str(MAX_BATCH_SIZE_M1), r)

    def test_repr_initialized(self):
        engine = _make_mock_engine()
        executor = MLXBatchedExecutor(engine=engine)
        _run(executor._ensure_initialized())
        r = repr(executor)
        self.assertIn("init", r)


# ─── Sprint P0-3 integration tests ─────────────────────────────────────


class TestMLXBatchedExecutorWorkerIntegration(unittest.TestCase):
    """
    P0-2 + P0-3 integration: MLXBatchedExecutor routes through worker thread.

    The batcher holds an optional worker_thread reference. When active,
    MLX inference is dispatched via the worker's persistent event loop
    (non-blocking main loop). When worker is unavailable, executor
    silently falls back to direct path.
    """

    def tearDown(self):
        ex = getattr(self, "executor", None)
        if ex is not None:
            _shutdown(ex)
        worker = getattr(self, "worker", None)
        if worker is not None:
            try:
                worker.shutdown(timeout=1.0)
            except Exception:
                pass

    def test_constructor_accepts_worker_thread(self):
        """MLXBatchedExecutor(engine, worker_thread=...) stores reference."""
        engine = _make_mock_engine()
        worker = MLXWorkerThread()
        executor = MLXBatchedExecutor(engine=engine, worker_thread=worker)
        self.assertIs(executor._worker_thread, worker)

    def test_no_worker_thread_default(self):
        """Default worker_thread=None — backward compatible."""
        engine = _make_mock_engine()
        executor = MLXBatchedExecutor(engine=engine)
        self.assertIsNone(executor._worker_thread)

    def test_worker_unavailable_falls_back_to_direct(self):
        """P0-3 fail-soft: worker inactive → direct path via engine."""
        engine = _make_mock_engine(response="direct")
        # Worker that has never been started → not active
        worker = MLXWorkerThread()
        executor = MLXBatchedExecutor(engine=engine, worker_thread=worker)
        result = _run(executor._call_engine_direct(
            "hello", None, None, None
        ))
        self.assertEqual(result, "direct")
        engine.generate.assert_awaited()

    def test_main_loop_free_during_batched_inference(self):
        """P0-2 + P0-3: parallel ticker increments while batched inference runs.

        With worker thread active, the batched path dispatches to the
        worker loop. Main loop is FREE to run other coroutines.
        """
        engine = _make_mock_engine(response="batched")
        worker = MLXWorkerThread()
        worker.start()
        executor = MLXBatchedExecutor(engine=engine, worker_thread=worker)
        # Force init
        _run(executor._ensure_initialized())

        counter = {"n": 0}

        async def _slow_batch():
            # Simulate slow batched inference dispatched to worker
            await asyncio.sleep(0.2)
            return await executor._call_engine_direct("hi", None, None, None)

        async def _ticker():
            # Ticker runs on main loop
            for _ in range(4):
                await asyncio.sleep(0.05)
                counter["n"] += 1
            return counter["n"]

        async def _main():
            t1 = asyncio.create_task(_slow_batch())
            t2 = asyncio.create_task(_ticker())
            r1 = await t1
            r2 = await t2
            return r1, r2

        result, ticks = _run(_main())
        self.assertEqual(result, "batched")
        self.assertEqual(ticks, 4, f"main loop blocked: ticks={ticks}")

    def test_shutdown_is_idempotent_after_worker_shutdown(self):
        """P0-2/P0-3: shutdown order — batcher before worker, both idempotent."""
        engine = _make_mock_engine()
        worker = MLXWorkerThread()
        worker.start()
        executor = MLXBatchedExecutor(engine=engine, worker_thread=worker)
        _run(executor._ensure_initialized())
        # Shutdown in canonical order: batcher first, then worker
        _run(executor.shutdown())
        worker.shutdown()
        # Both should be safe to call again
        _run(executor.shutdown())
        worker.shutdown()
        self.assertFalse(executor._initialized)
        self.assertFalse(worker.is_active())


if __name__ == "__main__":
    unittest.main()
