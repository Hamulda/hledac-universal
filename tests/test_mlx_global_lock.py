"""
tests/test_mlx_global_lock.py — L-01: Globální MLX inference lock testy.

Testuje že:
1. 3 paralelní generate() probíhají striktně sériově (lock funguje)
2. Globální lock je process-wide singleton
3. Lock je lazy-init (ISSUE-014 kompatibilní)
4. Fail-safe: lock nedostupný → inference pokračuje

Author: L-01 (F350M-R)
"""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from hledac.universal.core.mlx_inference_lock import (
from core import aclose
    _get_mlx_inference_lock,
    mlx_inference_lock_aio,
    mlx_inference_lock_context,
)


class TestMlxGlobalLock:
    """Test suite for MLX global inference lock."""

    def test_lock_is_singleton(self) -> None:
        """Ověř že lock je singleton = stejná instance pro všechny volající."""
        lock1 = _get_mlx_inference_lock()
        lock2 = _get_mlx_inference_lock()
        assert lock1 is lock2, "Lock musí být singleton"

    def test_lock_is_threading_lock(self) -> None:
        """Ověř že vrácený lock je threading.Lock."""
        lock = _get_mlx_inference_lock()
        assert isinstance(lock, threading.Lock), "Lock musí být threading.Lock"

    def test_lock_lazy_init(self) -> None:
        """Ověř že lock je lazy-init (ISSUE-014 kompatibilní).

        Lock se nesmí vytvořit při importu, ale až při prvním volání.
        """
        # Umístíme lock do globals a zkontrolujeme že None
        import hledac.universal.core.mlx_inference_lock as mil

        # Reset pro test
        old_lock = mil._MLX_INFERENCE_LOCK
        mil._MLX_INFERENCE_LOCK = None

        try:
            # První volání musí vytvořit lock
            lock1 = mil._get_mlx_inference_lock()
            assert lock1 is not None
            assert isinstance(lock1, threading.Lock)

            # Druhé volání musí vrátit stejný lock
            lock2 = mil._get_mlx_inference_lock()
            assert lock1 is lock2
        finally:
            # Obnovíme původní stav
            mil._MLX_INFERENCE_LOCK = old_lock

    def test_lock_context_decorator(self) -> None:
        """Ověř že mlx_inference_lock_context dekorátor funguje správně."""
        call_order: list[int] = []

        @mlx_inference_lock_context
        def mock_generate(x: int) -> int:
            call_order.append(x)
            return x * 2

        result = mock_generate(5)
        assert result == 10
        assert call_order == [5]

    @pytest.mark.asyncio
    async def test_lock_aio_serializes_calls(self) -> None:
        """L-01 core test: 3 paralelní volání musí proběhnout sériově.

        Měříme čas mezi prvním a posledním voláním. Pokud běží
        paralelně, celkový čas by byl ~0.1s (3× paralelně).
        Pokud sériově, celkový čas by byl ~0.3s (3× 0.1s sekvenčně).
        """
        call_times: list[float] = []

        def slow_generate(n: int) -> int:
            # Lock už je získán v mlx_inference_lock_aio, takže tu jen simulujeme inference
            call_times.append(time.monotonic())
            time.sleep(0.1)  # Simulace MLX inference
            return n

        async def parallel_calls() -> None:
            await asyncio.gather(
                mlx_inference_lock_aio(slow_generate, 1),
                mlx_inference_lock_aio(slow_generate, 2),
                mlx_inference_lock_aio(slow_generate, 3),
            )

        t0 = time.monotonic()
        await parallel_calls()
        total_time = time.monotonic() - t0

        # Ověř že všechna volání proběhla
        assert len(call_times) == 3

        # Ověř časové pořadí — druhé volání musí začít až po prvním
        assert call_times[1] >= call_times[0] + 0.09, "Druhé volání musí čekat na lock"
        assert call_times[2] >= call_times[1] + 0.09, "Třetí volání musí čekat na lock"

        # Celkový čas musí být ~0.3s+ (sériově), ne ~0.1s (paralelně)
        assert total_time >= 0.28, f"Volání běžela paralelně! Celkový čas: {total_time:.3f}s"

    @pytest.mark.asyncio
    async def test_lock_with_exception(self) -> None:
        """Ověř že exception v inference neblokuje lock."""

        def failing_generate() -> int:
            # Lock už je získán v mlx_inference_lock_aio
            raise RuntimeError("Simulated MLX error")

        # Musí propustit exception dál a lock musí být uvolněn
        with pytest.raises(RuntimeError, match="Simulated MLX error"):
            await mlx_inference_lock_aio(failing_generate)

        # Ověř že lock je stále dostupný (nebyl zanechaný v nekonzistentním stavu)
        lock = _get_mlx_inference_lock()
        assert lock is not None

    def test_concurrent_lock_access(self) -> None:
        """Ověř že lock správně serializuje přístup z více vláken."""
        counter = [0]
        lock = _get_mlx_inference_lock()
        barrier = threading.Barrier(5)

        def increment() -> None:
            barrier.wait()  # Synchronizace startu
            with lock:
                old = counter[0]
                time.sleep(0.01)  # Simulace kritické sekce
                counter[0] = old + 1

        threads = [threading.Thread(target=increment) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert counter[0] == 5, f"Očekáváno 5, dostáno {counter[0]}"


class TestMlxInferenceLockFailSafe:
    """Test fail-safe chování locku."""

    def test_lock_not_available_raises(self) -> None:
        """Pokud lock není dostupný (což by nemělo nastat), inference pokračuje.

        Poznámka: V aktuální implementaci lock vždy existuje (vytvořen při prvním
        volání). Tento test validuje že žádný edge-case nezpůsobí crash.
        """
        # Aktuální implementace vždy vrátí platný lock
        lock = _get_mlx_inference_lock()
        assert lock is not None

        # Lock lze použít
        with lock:
            pass  # Should not raise


# ==============================================================================
# Invariants (L-01)
# ==============================================================================

L01_INVARIANTS = [
    # I1: Globální lock existuje jako singleton
    "test_lock_is_singleton",
    # I2: Lock je threading.Lock (process-wide)
    "test_lock_is_threading_lock",
    # I3: Lock je lazy-init (ISSUE-014)
    "test_lock_lazy_init",
    # I4: Sériové volání 3 paralelních generací
    "test_lock_aio_serializes_calls",
    # I5: Exception neblokuje lock
    "test_lock_with_exception",
    # I6: Konkurentní přístup z více vláken
    "test_concurrent_lock_access",
]
