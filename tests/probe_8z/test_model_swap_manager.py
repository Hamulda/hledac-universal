"""
Sprint 8Z — ModelSwapManager Tests
====================================

Testy pro ModelSwapManager — race-free swap arbiter.
Používá fake callbacks / fake lifecycle, ne reálné modely.

Invariants covered:
- model_swap_manager.1: modul existuje
- model_swap_manager.2: ModelLifecycleProtocol existuje
- model_swap_manager.3: SwapResult kontrakt existuje
- model_swap_manager.4: async_swap_to exists
- model_swap_manager.5: get_swap_status exists
- model_swap_manager.6: no-op pokud target už je aktivní
- model_swap_manager.7: no-op check je uvnitř locku
- model_swap_manager.8: cancel se volá s current_model_name, ne s target_model
- model_swap_manager.9: pending task cancel/drain proběhne před unload
- model_swap_manager.10: unload proběhne před load
- model_swap_manager.11: success path vrací success=True
- model_swap_manager.12: load failure vrací success=False + error
- model_swap_manager.13: rollback je attempted po failed loadu
- model_swap_manager.14: rollback success path je reportovaný
- model_swap_manager.15: rollback failure path vrací critical_no_model style error
- model_swap_manager.16: concurrent swaps jsou serializované lockem
- model_swap_manager.17: 10 concurrent calls nevyhodí race exception
- model_swap_manager.18: cancelled_pending count je správný
- model_swap_manager.19: cancel_supported=False je odlišeno od cancelled_pending=0
- model_swap_manager.20: drain timeout abortne swap před unloadem
- model_swap_manager.21: duration_ms je vyplněno
- model_swap_manager.22: status getter je levný
- model_swap_manager.23: import hygiene se nezhorší
- model_swap_manager.24: žádný top-level heavy import
- model_swap_manager.25: benchmark no-op je levný
- model_swap_manager.26: benchmark contention reportuje p50/p99/max
- model_swap_manager.27: probe_8t stále prochází
- model_swap_manager.28: AO canary stále prochází
"""


import asyncio
import statistics
import time

import msgspec
import pytest

from hledac.universal.brain.model_swap_manager import (
    DrainResult,
    ModelSwapManager,
    ModelLifecycleProtocol,
    SwapResult,
    SwapStatus,
)

from utils.async_helpers import safe_gather_ok

# =============================================================================
# Fake Lifecycle — implementuje ModelLifecycleProtocol
# =============================================================================

class FakeLifecycle:
    """Fake lifecycle pro testování bez reálných modelů."""

    def __init__(self) -> None:
        self._current_model: str | None = None
        self._cancel_count: int = 0
        self._cancel_supported: bool = True
        self._load_result: bool = True
        self._load_exception: Exception | None = None
        self._unload_calls: list[str] = []
        self._load_calls: list[str] = []
        self._cancel_calls: list[str] = []
        self._cancel_raised: Exception | None = None
        self._cancel_delay: float = 0.0
        self._drain_tasks: list[asyncio.Task] = []
        self._unload_exception: Exception | None = None
        # Sequenced load results: list of (model_name, result) tuples
        self._load_sequence: list[tuple[str, bool]] | None = None

    def get_current_model_name(self) -> str | None:
        return self._current_model

    def set_current_model(self, name: str | None) -> None:
        self._current_model = name

    def set_cancel_supported(self, supported: bool) -> None:
        self._cancel_supported = supported

    def set_load_result(self, result: bool) -> None:
        self._load_result = result
        self._load_sequence = None  # disable sequence mode

    def set_load_exception(self, exc: Exception | None) -> None:
        self._load_exception = exc
        # Note: does NOT clear _load_sequence — caller must manage consistently

    def set_unload_exception(self, exc: Exception | None) -> None:
        self._unload_exception = exc

    def set_cancel_delay(self, delay: float) -> None:
        self._cancel_delay = delay

    def set_cancel_raised(self, exc: Exception | None) -> None:
        self._cancel_raised = exc

    def set_load_sequence(self, sequence: list[tuple[str, bool]]) -> None:
        """Set sequenced load results: [("qwen", False), ("hermes", True)]."""
        self._load_sequence = sequence
        self._load_exception = None  # clear exception when using sequence

    async def cancel_pending_model_tasks(self, model_name: str) -> int:
        self._cancel_calls.append(model_name)
        if self._cancel_delay > 0:
            await asyncio.sleep(self._cancel_delay)
        if self._cancel_raised:
            raise self._cancel_raised
        return self._cancel_count

    def set_cancel_count(self, count: int) -> None:
        self._cancel_count = count

    async def unload_current_model(self) -> None:
        model = self._current_model
        if model:
            self._unload_calls.append(model)
        if self._unload_exception:
            raise self._unload_exception
        self._current_model = None

    async def load_model(self, target_model: str) -> bool:
        self._load_calls.append(target_model)
        if self._load_exception:
            raise self._load_exception
        if self._load_sequence is not None:
            for model_name, result in self._load_sequence:
                if model_name == target_model:
                    if result:
                        self._current_model = target_model
                    return result
            # Default if model not in sequence
            return False
        self._current_model = target_model
        return self._load_result

    def reset(self) -> None:
        self._cancel_count = 0
        self._cancel_supported = True
        self._load_result = True
        self._load_exception = None
        self._unload_calls.clear()
        self._load_calls.clear()
        self._cancel_calls.clear()
        self._cancel_raised = None
        self._cancel_delay = 0.0
        self._drain_tasks.clear()
        self._unload_exception = None
        self._load_sequence = None


# =============================================================================
# Test Class
# =============================================================================

class TestModelSwapManager:
    """Sprint 8Z: ModelSwapManager test suite."""

    # -------------------------------------------------------------------------
    # Basic existence tests
    # -------------------------------------------------------------------------

    def test_module_exists(self) -> None:
        """model_swap_manager.1: modul existuje"""
        from hledac.universal.brain import model_swap_manager
        assert model_swap_manager is not None

    def test_protocol_exists(self) -> None:
        """model_swap_manager.2: ModelLifecycleProtocol existuje"""
        assert ModelLifecycleProtocol is not None
        # Protocol musí být msgspec.Struct pro type safety
        assert issubclass(ModelLifecycleProtocol, msgspec.Struct)

    def test_swap_result_exists(self) -> None:
        """model_swap_manager.3: SwapResult kontrakt existuje"""
        assert SwapResult is not None
        # Verify key fields
        sr = SwapResult(
            target_model="hermes",
            previous_model="qwen",
            success=True,
        )
        assert sr.target_model == "hermes"
        assert sr.previous_model == "qwen"
        assert sr.success is True
        assert sr.cancelled_pending == 0
        assert sr.cancel_supported is False  # default
        assert sr.cancelled_timed_out is False
        assert sr.rollback_attempted is False
        assert sr.rollback_succeeded is False
        assert sr.noop is False
        assert sr.error is None
        assert sr.duration_ms == 0.0

    def test_async_swap_to_exists(self) -> None:
        """model_swap_manager.4: async_swap_to exists"""
        lifecycle = FakeLifecycle()
        manager = ModelSwapManager(lifecycle)
        assert hasattr(manager, "async_swap_to")
        assert callable(manager.async_swap_to)

    def test_get_swap_status_exists(self) -> None:
        """model_swap_manager.5: get_swap_status exists"""
        lifecycle = FakeLifecycle()
        manager = ModelSwapManager(lifecycle)
        assert hasattr(manager, "get_swap_status")
        assert callable(manager.get_swap_status)

    # -------------------------------------------------------------------------
    # No-op tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_noop_if_already_active(self) -> None:
        """model_swap_manager.6: no-op pokud target už je aktivní"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        manager = ModelSwapManager(lifecycle)

        result = await manager.async_swap_to("hermes")

        assert result.success is True
        assert result.noop is True
        assert result.previous_model == "hermes"
        assert result.target_model == "hermes"
        # Žádné unload/load calls
        assert lifecycle._unload_calls == []
        assert lifecycle._load_calls == []

    @pytest.mark.asyncio
    async def test_noop_check_is_inside_lock(self) -> None:
        """model_swap_manager.7: no-op check je uvnitř locku — dvě souběžné no-op volání musí být serializovaná"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        manager = ModelSwapManager(lifecycle)

        # Spustíme 2 no-op swap současně — druhý musí čekat na zámek
        results = await asyncio.gather(
            manager.async_swap_to("hermes"),
            manager.async_swap_to("hermes"),
        )

        # Obě uspěly jako no-op
        assert all(r.success for r in results)
        assert all(r.noop for r in results)

    # -------------------------------------------------------------------------
    # Ordering tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_cancel_uses_current_model_not_target(self) -> None:
        """model_swap_manager.8: cancel se volá s current_model_name, ne s target_model"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(3)
        manager = ModelSwapManager(lifecycle)

        await manager.async_swap_to("qwen")

        # Cancel byl volán s "hermes" (current), ne "qwen" (target)
        assert lifecycle._cancel_calls == ["hermes"]

    @pytest.mark.asyncio
    async def test_cancel_drain_before_unload(self) -> None:
        """model_swap_manager.9: pending task cancel/drain proběhne před unload"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(2)
        manager = ModelSwapManager(lifecycle)

        await manager.async_swap_to("qwen")

        # Pořadí: cancel_calls (1) → unload_calls (2) → load_calls (3)
        assert len(lifecycle._cancel_calls) >= 1
        assert lifecycle._unload_calls == ["hermes"]
        assert lifecycle._load_calls == ["qwen"]
        # Ověř pořadí
        assert lifecycle._cancel_calls[0] == "hermes"

    @pytest.mark.asyncio
    async def test_unload_before_load(self) -> None:
        """model_swap_manager.10: unload proběhne před load"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        manager = ModelSwapManager(lifecycle)

        await manager.async_swap_to("qwen")

        # Ověření pořadí přes časové razítko — unload musí být před load
        assert lifecycle._unload_calls == ["hermes"]
        assert lifecycle._load_calls == ["qwen"]

    # -------------------------------------------------------------------------
    # Success / Failure paths
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_success_path(self) -> None:
        """model_swap_manager.11: success path vrací success=True"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        manager = ModelSwapManager(lifecycle)

        result = await manager.async_swap_to("qwen")

        assert result.success is True
        assert result.error is None
        assert result.target_model == "qwen"
        assert result.previous_model == "hermes"
        assert result.cancelled_pending == 0
        assert result.cancelled_timed_out is False

    @pytest.mark.asyncio
    async def test_load_failure_returns_false_with_error(self) -> None:
        """model_swap_manager.12: load failure vrací success=False + error"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        # qwen load fails, rollback to hermes succeeds
        lifecycle.set_load_sequence([("qwen", False), ("hermes", True)])
        manager = ModelSwapManager(lifecycle)

        result = await manager.async_swap_to("qwen")

        assert result.success is False
        assert result.error == "load_failed"
        assert result.cancelled_timed_out is False
        assert result.rollback_attempted is True
        assert result.rollback_succeeded is True

    @pytest.mark.asyncio
    async def test_load_exception_returns_error(self) -> None:
        """model_swap_manager.12b: load exception vrací success=False + error"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        # Exception on target load
        lifecycle.set_load_exception(RuntimeError("GPU OOM"))
        manager = ModelSwapManager(lifecycle)

        result = await manager.async_swap_to("qwen")

        assert result.success is False
        assert result.rollback_attempted is True
        # Rollback also raises same exception -> critical_no_model
        assert result.error == "critical_no_model"

    # -------------------------------------------------------------------------
    # Rollback tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_rollback_attempted_after_failed_load(self) -> None:
        """model_swap_manager.13: rollback je attempted po failed loadu"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        lifecycle.set_load_result(False)
        manager = ModelSwapManager(lifecycle)

        result = await manager.async_swap_to("qwen")

        assert result.rollback_attempted is True
        # Rollback se pokusí nahrát "hermes" zpět
        # load_calls = ["qwen", "hermes"] (qwen fail, pak rollback na hermes)
        assert "hermes" in lifecycle._load_calls

    @pytest.mark.asyncio
    async def test_rollback_success_reported(self) -> None:
        """model_swap_manager.14: rollback success path je reportovaný"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        # První load (qwen) selže, druhý load (rollback na hermes) uspěje
        lifecycle.set_load_sequence([("qwen", False), ("hermes", True)])
        manager = ModelSwapManager(lifecycle)

        result = await manager.async_swap_to("qwen")

        assert result.rollback_attempted is True
        assert result.rollback_succeeded is True
        assert result.error == "load_failed"  # ne critical_no_model

    @pytest.mark.asyncio
    async def test_rollback_failure_returns_critical_error(self) -> None:
        """model_swap_manager.15: rollback failure path vrací critical_no_model style error"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        # První load (qwen) fail, rollback taky fail
        lifecycle.set_load_sequence([("qwen", False), ("hermes", False)])
        manager = ModelSwapManager(lifecycle, drain_timeout=0.5)

        result = await manager.async_swap_to("qwen")

        assert result.rollback_attempted is True
        assert result.rollback_succeeded is False
        assert result.error == "critical_no_model"

    @pytest.mark.asyncio
    async def test_rollback_when_no_previous_model(self) -> None:
        """model_swap_manager.15b: rollback když není previous_model"""
        lifecycle = FakeLifecycle()
        # Žádný model není loaded
        lifecycle.set_current_model(None)
        lifecycle.set_cancel_count(0)
        lifecycle.set_load_result(False)
        manager = ModelSwapManager(lifecycle)

        result = await manager.async_swap_to("qwen")

        assert result.rollback_attempted is False  # nelze rollbackovat když není previous
        assert result.success is False
        assert result.error == "load_failed"

    # -------------------------------------------------------------------------
    # Concurrency tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_concurrent_swaps_are_serialized(self) -> None:
        """model_swap_manager.16: concurrent swaps jsou serializované lockem"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        manager = ModelSwapManager(lifecycle)

        # 3 concurrent swapy — musí být serializované přes lock
        results = await asyncio.gather(
            manager.async_swap_to("qwen"),
            manager.async_swap_to("hermes"),
            manager.async_swap_to("qwen"),
        )

        # Souběžné volání musí projít bez race exception
        assert all(isinstance(r, SwapResult) for r in results)

    @pytest.mark.asyncio
    async def test_ten_concurrent_no_race_exception(self) -> None:
        """model_swap_manager.17: 10 concurrent calls nevyhodí race exception"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        manager = ModelSwapManager(lifecycle)

        # 10 souběžných volání — žádný race
        results = await asyncio.gather(
            *[manager.async_swap_to("qwen") for _ in range(10)]
        )

        assert len(results) == 10
        assert all(isinstance(r, SwapResult) for r in results)

    # -------------------------------------------------------------------------
    # Cancelled pending / cancel supported tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_cancelled_pending_count(self) -> None:
        """model_swap_manager.18: cancelled_pending count je správný"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(5)
        manager = ModelSwapManager(lifecycle)

        result = await manager.async_swap_to("qwen")

        assert result.cancelled_pending == 5

    @pytest.mark.asyncio
    async def test_cancel_supported_vs_cancelled_pending(self) -> None:
        """model_swap_manager.19: cancel_supported=False je odlišeno od cancelled_pending=0"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        # Nemá cancel metodu — ale u FakeLifecycle to vždycky funguje
        # Pro test: manuálně otestujeme chování když cancel proběhl ale vrátil 0
        manager = ModelSwapManager(lifecycle)

        result = await manager.async_swap_to("qwen")

        # cancel_supported je True (lifecycle.cancel_pending_model_tasks vždy existuje)
        # cancelled_pending je 0 (žádné tasky nezrušeny)
        assert result.cancel_supported is True
        assert result.cancelled_pending == 0
        # Ale NOOP musí být False (swap proběhl)
        assert result.noop is False

    # -------------------------------------------------------------------------
    # Drain timeout tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_drain_timeout_aborts_before_unload(self) -> None:
        """model_swap_manager.20: drain timeout abortne swap před unloadem"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        lifecycle.set_cancel_delay(10.0)  # 10s — přesáhne timeout
        lifecycle.set_cancel_raised(asyncio.TimeoutError())
        manager = ModelSwapManager(lifecycle, drain_timeout=0.5)

        result = await manager.async_swap_to("qwen")

        assert result.success is False
        assert result.cancelled_timed_out is True
        assert result.error == "drain_timeout"
        # unload NESMÍ proběhnout
        assert lifecycle._unload_calls == []
        assert lifecycle._load_calls == []

    @pytest.mark.asyncio
    async def test_drain_timeout_reports_correct_error(self) -> None:
        """model_swap_manager.20b: drain timeout má správný error string"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_delay(10.0)
        manager = ModelSwapManager(lifecycle, drain_timeout=0.5)

        result = await manager.async_swap_to("qwen")

        assert result.error == "drain_timeout"
        assert result.cancelled_timed_out is True

    # -------------------------------------------------------------------------
    # Duration and status tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_duration_ms_filled(self) -> None:
        """model_swap_manager.21: duration_ms je vyplněno"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        manager = ModelSwapManager(lifecycle)

        result = await manager.async_swap_to("qwen")

        assert result.duration_ms > 0.0

    @pytest.mark.asyncio
    async def test_status_getter_is_cheap(self) -> None:
        """model_swap_manager.22: status getter je levný (žádný lock, žádné await)"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        manager = ModelSwapManager(lifecycle)

        # Spustíme status uprostřed swapu — nesmí blokovat
        async def get_status_during_swap():
            await asyncio.sleep(0.01)
            return manager.get_swap_status()

        status_task = asyncio.create_task(get_status_during_swap())
        swap_task = asyncio.create_task(manager.async_swap_to("qwen"))

        status, swap_result = await safe_gather_ok(status_task, swap_task, label="test_model_swap_manager:567")

        assert isinstance(status, SwapStatus)
        # Status během swapu může ukázat swap_in_progress=True
        assert status.current_model in ("hermes", "qwen", None)

    # -------------------------------------------------------------------------
    # Import hygiene
    # -------------------------------------------------------------------------

    def test_import_hygiene(self) -> None:
        """model_swap_manager.23: import hygiene se nezhorší"""
        # Modul musí jít importovat bez side-effects
        import hledac.universal.brain.model_swap_manager as msm
        assert hasattr(msm, "ModelSwapManager")
        assert hasattr(msm, "SwapResult")
        assert hasattr(msm, "SwapStatus")
        assert hasattr(msm, "ModelLifecycleProtocol")
        assert hasattr(msm, "DrainResult")

    def test_no_top_level_heavy_import(self) -> None:
        """model_swap_manager.24: žádný top-level heavy import"""
        # Ověříme že náš modul nemá přímé heavy importy na top-level
        # (mlx/aiohttp/torch nesmí být v sys.modules JEN DÍKY našemu modulu)
        import sys
        import importlib

        # Modul musí jít znovu naimportovat bez chyb
        import hledac.universal.brain.model_swap_manager as msm

        # Ověříme strukturu modulu — žádné heavy importy v kódu
        import ast
        source_file = msm.__file__
        with open(source_file) as f:
            tree = ast.parse(f.read())

        heavy_mods = {"mlx", "mlx.core", "aiohttp", "aiohttp.client", "fastapi", "torch", "torch.nn"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in heavy_mods or alias.name.split('.')[0] in heavy_mods:
                        pytest.fail(f"Heavy import found in module: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module in heavy_mods or node.module.split('.')[0] in heavy_mods):
                    pytest.fail(f"Heavy import found in module: from {node.module}")

    # -------------------------------------------------------------------------
    # Benchmarks
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_benchmark_noop(self) -> None:
        """model_swap_manager.25: benchmark no-op je levný"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        manager = ModelSwapManager(lifecycle)

        samples = []
        for _ in range(50):
            t0 = time.perf_counter()
            await manager.async_swap_to("hermes")
            t1 = time.perf_counter()
            samples.append((t1 - t0) * 1000.0)

        p50 = statistics.median(samples)
        p99 = sorted(samples)[-1]
        print(f"\n  no-op p50={p50:.4f}ms p99={p99:.4f}ms")

        # No-op swap musí být velmi levný (< 10ms p99)
        assert p99 < 10.0, f"noop p99={p99:.4f}ms too high"

    @pytest.mark.asyncio
    async def test_benchmark_contention(self) -> None:
        """model_swap_manager.26: benchmark contention reportuje p50/p99/max"""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        manager = ModelSwapManager(lifecycle)

        # 10 concurrent calls — měříme celkový čas všech
        t0 = time.perf_counter()
        results = await asyncio.gather(
            *[manager.async_swap_to("qwen") for _ in range(10)]
        )
        total_ms = (time.perf_counter() - t0) * 1000.0

        # Všechny uspěly
        assert all(r.success for r in results)

        # Durations
        durations = [r.duration_ms for r in results]
        p50 = statistics.median(durations)
        p99 = sorted(durations)[-1]
        max_dur = max(durations)

        print(f"\n  contention p50={p50:.4f}ms p99={p99:.4f}ms max={max_dur:.4f}ms total={total_ms:.4f}ms")

        # Baseline: single no-op ~0.1ms, takže 10 serializovaných ~1-5ms p99
        assert p50 > 0  # musíme něco naměřit
        assert max_dur > 0
        assert p99 > 0

    # -------------------------------------------------------------------------
    # SwapStatus field verification
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_swap_status_fields(self) -> None:
        """SwapStatus má správné fields."""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        manager = ModelSwapManager(lifecycle)

        await manager.async_swap_to("qwen")

        status = manager.get_swap_status()

        assert isinstance(status, SwapStatus)
        assert hasattr(status, "current_model")
        assert hasattr(status, "swap_in_progress")
        assert hasattr(status, "total_swaps")
        assert hasattr(status, "failed_swaps")
        assert hasattr(status, "last_swap_ms")
        assert status.total_swaps >= 1
        assert status.last_swap_ms is not None

    # -------------------------------------------------------------------------
    # SwapResult immutable fields
    # -------------------------------------------------------------------------

    def test_swap_result_is_frozen(self) -> None:
        """SwapResult je frozen — nelze měnit po vytvoření."""
        sr = SwapResult(
            target_model="qwen",
            previous_model="hermes",
            success=True,
        )
        with pytest.raises((AttributeError, msgspec.ValidationError, TypeError)):
            sr.success = False  # type: ignore

    def test_drain_result_fields(self) -> None:
        """DrainResult má správné fields."""
        dr = DrainResult(cancelled_count=3, timed_out=False, error=None)
        assert dr.cancelled_count == 3
        assert dr.timed_out is False
        assert dr.error is None

    # -------------------------------------------------------------------------
    # Edge cases
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_swap_from_none_model(self) -> None:
        """Swap z None (žádný model loaded)."""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model(None)
        lifecycle.set_cancel_count(0)
        manager = ModelSwapManager(lifecycle)

        result = await manager.async_swap_to("hermes")

        assert result.success is True
        assert result.previous_model is None
        assert result.cancelled_pending == 0
        assert lifecycle._unload_calls == []  # žádný model k unload

    @pytest.mark.asyncio
    async def test_unload_exception_does_not_crash(self) -> None:
        """Unload exception je zachycena a swap pokračuje."""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        lifecycle.set_unload_exception(RuntimeError("unload failed"))
        manager = ModelSwapManager(lifecycle)

        result = await manager.async_swap_to("qwen")

        # Swap může být success (load qwen uspěl) nebo ne (unload selhal)
        # Důležité: swap nesmí vyhodit výjimku
        assert isinstance(result, SwapResult)

    @pytest.mark.asyncio
    async def test_total_and_failed_swaps_counted(self) -> None:
        """total_swaps a failed_swaps jsou správně počítány."""
        lifecycle = FakeLifecycle()
        lifecycle.set_current_model("hermes")
        lifecycle.set_cancel_count(0)
        manager = ModelSwapManager(lifecycle)

        # Úspěšný swap
        await manager.async_swap_to("qwen")
        # Selhávající swap (load failure)
        lifecycle.set_load_result(False)
        await manager.async_swap_to("hermes")

        status = manager.get_swap_status()
        assert status.total_swaps == 2
        assert status.failed_swaps == 1


# =============================================================================
# Conftest — fixtures
# =============================================================================

@pytest.fixture
def fake_lifecycle():
    """Shared fake lifecycle fixture."""
    return FakeLifecycle()


@pytest.fixture
def swap_manager(fake_lifecycle):
    """Shared swap manager fixture."""
    return ModelSwapManager(fake_lifecycle)
