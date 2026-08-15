"""
Issue M-04 — MoE synchronně volá mlx_lm.load() a generate() uvnitř async funkcí
Acceptance Test: Ověří že během load/generate může jiná coroutine await asyncio.sleep(0.01) proběhnout.

Vzor: Pokud _load_expert běží v MLXWorker thread, event loop zůstává volný
a jiné coroutines mohou běžet. Bez MLXWorker by event loop freeze na celou
dobu load().

Test strategy:
- Mock mlx_lm.load() s time.sleep(delay) — simuluje 1-20s realného loadu
- Spusť dva concurrent tasks: jedna volá run_in_mlx_worker(load, ...), druhá asyncio.sleep(0.01)
- Pokud MLXWorker správně offloaduje do thread, druhá task proběhne během ~10ms
- Pokud ne (přímo v event loop), druhá task musí čekat na konec load
"""
from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from _core import aclose


class TestMoELoadAsync(unittest.IsolatedAsyncioTestCase):
    """Test that MLX operations don't freeze the event loop (Issue M-04)."""

    async def test_mlx_worker_does_not_freeze_event_loop(self):
        """
        ACCEPTANCE TEST: během run_in_mlx_worker jiné coroutines běží.

        Ověření: Když sync blocking funkce (simulující mlx_lm.load) trvá 500ms,
        asyncio.sleep(0.01) v jiné coroutine proběhne takřka okamžitě (ne po 500ms).

        run_sync správně offloaduje sync blocking do worker thread,
        event loop zůstává volný.
        """
        from hledac.universal._core.mlx_inference_lock import MLXWorker

        # Flags shared across threads
        load_started = asyncio.Event()
        load_done = asyncio.Event()

        def slow_blocking_load() -> tuple[str, str]:
            """
            Synchroní blocking funkce — simuluje mlx_lm.load().
            Používá time.sleep (blocking), ne asyncio.sleep.
            """
            import time as t
            load_started.set()
            t.sleep(0.5)  # Simuluje realný load 1-20s
            load_done.set()
            return ("mock_model", "mock_tokenizer")

        results: dict[str, object] = {}

        async def worker_task():
            """Task který běží v MLX worker thread."""
            worker = MLXWorker(name="test-mlx-worker", max_active_experts=1)
            result = await worker.run_sync(slow_blocking_load)
            results["worker"] = result
            return result

        async def sleeper_task():
            """Nezávislá coroutine — musí proběhnout i když worker běží."""
            await load_started.wait()  # Počkej než load začne
            t0 = time.monotonic()
            await asyncio.sleep(0.01)  # Krátký sleep — měl by proběhnout okamžitě
            elapsed = time.monotonic() - t0
            results["sleeper_elapsed"] = elapsed
            # Měl by být ~0.01s (ne 0.5s)
            if not (elapsed < 0.2):
                self.fail(
                    f"Sleeper task was blocked! Took {elapsed:.3f}s instead of ~0.01s. "
                    "Event loop was frozen during MLX operation."
                )

        # Spusť obě tasky souběžně
        async with asyncio.timeout(3.0):
            await asyncio.gather(
                worker_task(),
                sleeper_task(),
            )

        # Ověření: sleeper běžel nezávisle
        self.assertIn("sleeper_elapsed", results)
        elapsed = float(results["sleeper_elapsed"])  # type: ignore[arg-type]
        self.assertLess(elapsed, 0.2)
        print(f"[M-04 PASS] Sleeper proběhl za {elapsed*1000:.1f}ms — event loop nebyl frozen")

    async def test_mlx_worker_concurrent_load_serialized(self):
        """
        Ověření že dva souběžné MLX load požadavky jsou serializovány přes semaphore.

        Na M1 Metal single-stream: max 1 souběžná operace.
        Správný test používá SHARED worker (singleton), ne dva oddělené workery.
        """
        from hledac.universal._core.mlx_inference_lock import MLXWorker, _get_mlx_worker

        # NOTE: run_sync expects a sync function, not an async coroutine.
        # We use a sync wrapper that simulates blocking MLX operation.
        def sync_blocking_op(op_id: int, delay: float = 0.3) -> int:
            # In real code, this would be mlx_lm.load() or mlx_lm.generate()
            # We use time.sleep (blocking) to simulate it
            import time as t
            t.sleep(delay)
            return op_id

        # Use the SINGLETON worker — this is what moe_router.py uses
        # All concurrent calls share ONE worker with semaphore=1 → serialized
        shared_worker = _get_mlx_worker()

        async def task_a():
            return await shared_worker.run_sync(sync_blocking_op, 1, 0.3)

        async def task_b():
            return await shared_worker.run_sync(sync_blocking_op, 2, 0.3)

        t0 = time.monotonic()
        async with asyncio.timeout(3.0):
            results = await asyncio.gather(task_a(), task_b())

        elapsed = time.monotonic() - t0

        # Since both tasks use the SAME singleton worker with semaphore=1,
        # they ARE serialized: total time ≈ sum of both delays = 0.6s
        # If they ran in parallel: ~0.3s
        self.assertEqual(results, [1, 2])
        # Elapsed should be >= 0.6s (serialized) not ~0.3s (parallel)
        self.assertGreaterEqual(elapsed, 0.55, "Calls should be serialized, not parallel")
        print(f"[M-04 SEMAPHORE] Obě operace dokončeny, elapsed={elapsed:.2f}s ≥ 0.6s — semaphore=1 serializuje — OK")

    async def test_run_in_mlx_worker_api(self):
        """Test module-level run_in_mlx_worker convenience function with sync callable."""
        from hledac.universal._core.mlx_inference_lock import run_in_mlx_worker

        # Use sync function — run_in_mlx_worker is for sync blocking operations
        def dummy_sync_op(x: int, y: int) -> int:
            import time as t
            t.sleep(0.01)  # Simulated work
            return x + y

        # Non-blocking call
        result = await asyncio.wait_for(
            run_in_mlx_worker(dummy_sync_op, 10, 20),
            timeout=2.0
        )
        self.assertEqual(result, 30)
        print("[M-04 API] run_in_mlx_worker() funguje správně")

    async def test_mlx_worker_is_active(self):
        """Test MLXWorker.is_active() and shutdown()."""
        from hledac.universal._core.mlx_inference_lock import MLXWorker

        worker = MLXWorker(name="test-mlx-worker", max_active_experts=1)
        self.assertFalse(worker.is_active())  # Not started yet

        # Start implicitly via first run_sync (must use sync function)
        def sync_noop() -> None:
            return None

        await worker.run_sync(sync_noop)
        self.assertTrue(worker.is_active())

        worker.shutdown(timeout=1.0)
        self.assertFalse(worker.is_active())
        print("[M-04 LIFECYCLE] is_active() + shutdown() OK")


if __name__ == "__main__":
    unittest.main()
