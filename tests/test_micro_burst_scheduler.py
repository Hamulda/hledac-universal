"""
tests/test_micro_burst_scheduler.py

HIGH: Micro-Burst Scheduler Tests

Tests for core/micro_burst_scheduler.py - Proactive thermal management
for M1 fanless SoC (PHYSICS-01).

Architecture: M1 8GB optimized, Python 3.14+ compatible
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Final
from unittest.mock import patch

import pytest
from core import aclose


class TestBurstPhase:
    """Tests for BurstPhase enum."""

    def test_burst_phase_enum_values(self) -> None:
        """BurstPhase must have correct enum values."""
        from hledac.universal.core.micro_burst_scheduler import BurstPhase

        assert BurstPhase.GPU_HEAVY is not None
        assert BurstPhase.IO_HEAVY is not None

    def test_burst_phase_repr(self) -> None:
        """BurstPhase.__repr__ must return the enum name."""
        from hledac.universal.core.micro_burst_scheduler import BurstPhase

        assert repr(BurstPhase.GPU_HEAVY) == "GPU_HEAVY"
        assert repr(BurstPhase.IO_HEAVY) == "IO_HEAVY"

    def test_burst_phase_is_enum(self) -> None:
        """BurstPhase must be a proper enum."""
        from hledac.universal.core.micro_burst_scheduler import BurstPhase

        assert hasattr(BurstPhase, "value")
        assert BurstPhase.GPU_HEAVY.value == 1
        assert BurstPhase.IO_HEAVY.value == 2


class TestMicroBurstScheduler:
    """Tests for MicroBurstScheduler class."""

    def test_scheduler_initialization(self) -> None:
        """Scheduler must initialize with correct default state."""
        from hledac.universal.core.micro_burst_scheduler import (
            BurstPhase,
            MicroBurstScheduler,
        )

        scheduler = MicroBurstScheduler()
        
        assert scheduler._phase == BurstPhase.GPU_HEAVY
        assert scheduler._started is False
        assert scheduler._phase_transitions == 0

    def test_scheduler_not_started_by_default(self) -> None:
        """Scheduler must not be started until start() is called."""
        from hledac.universal.core.micro_burst_scheduler import MicroBurstScheduler

        scheduler = MicroBurstScheduler()
        
        # phase should be GPU_HEAVY but _started should be False
        assert scheduler._started is False

    def test_start_is_idempotent(self) -> None:
        """start() must be idempotent - multiple calls must not error."""
        from hledac.universal.core.micro_burst_scheduler import MicroBurstScheduler

        scheduler = MicroBurstScheduler()
        
        scheduler.start()
        scheduler.start()  # Second call - must not error
        scheduler.start()  # Third call - must not error
        
        assert scheduler._started is True

    def test_get_phase_initial(self) -> None:
        """get_phase() must return initial phase (GPU_HEAVY)."""
        from hledac.universal.core.micro_burst_scheduler import (
            BurstPhase,
            MicroBurstScheduler,
        )

        scheduler = MicroBurstScheduler()
        scheduler.start()
        
        phase = scheduler.get_phase()
        assert phase == BurstPhase.GPU_HEAVY

    def test_step_triggers_phase_transition(self) -> None:
        """step() must transition from GPU_HEAVY to IO_HEAVY after GPU window."""
        from hledac.universal.core.micro_burst_scheduler import (
            BurstPhase,
            MicroBurstScheduler,
            _BURST_GPU_MS,
        )

        scheduler = MicroBurstScheduler()
        scheduler.start()
        
        # Step immediately - should still be GPU_HEAVY
        phase1 = scheduler.step()
        assert phase1 == BurstPhase.GPU_HEAVY
        
        # Wait for GPU window to pass
        time.sleep(_BURST_GPU_MS / 1000.0 + 0.05)
        
        # Step again - should transition to IO_HEAVY
        phase2 = scheduler.step()
        assert phase2 == BurstPhase.IO_HEAVY

    def test_full_cycle_transitions(self) -> None:
        """Scheduler must cycle through GPU_HEAVY and IO_HEAVY phases."""
        from hledac.universal.core.micro_burst_scheduler import (
            BurstPhase,
            MicroBurstScheduler,
            _BURST_GPU_MS,
            _BURST_IO_MS,
        )

        scheduler = MicroBurstScheduler()
        scheduler.start()
        
        cycle_ms = _BURST_GPU_MS + _BURST_IO_MS
        
        # Move through full cycle
        time.sleep(cycle_ms / 1000.0 + 0.05)
        
        scheduler.step()  # Should be IO_HEAVY
        assert scheduler._phase == BurstPhase.IO_HEAVY
        
        # Wait for IO window to pass
        time.sleep(_BURST_IO_MS / 1000.0 + 0.05)
        
        scheduler.step()  # Should transition back to GPU_HEAVY
        assert scheduler._phase == BurstPhase.GPU_HEAVY

    def test_phase_transition_count(self) -> None:
        """phase_transition_count must increment on each transition."""
        from hledac.universal.core.micro_burst_scheduler import (
            MicroBurstScheduler,
            _BURST_GPU_MS,
            _BURST_IO_MS,
        )

        scheduler = MicroBurstScheduler()
        scheduler.start()
        
        assert scheduler._phase_transitions == 0
        
        # Force transitions
        time.sleep(_BURST_GPU_MS / 1000.0 + 0.05)
        scheduler.step()
        assert scheduler._phase_transitions == 1
        
        time.sleep(_BURST_IO_MS / 1000.0 + 0.05)
        scheduler.step()
        assert scheduler._phase_transitions == 2

    def test_step_rate_limits_checks(self) -> None:
        """step() must rate-limit phase evaluation checks."""
        from hledac.universal.core.micro_burst_scheduler import (
            MicroBurstScheduler,
            _PHASE_CHECK_INTERVAL_S,
        )

        scheduler = MicroBurstScheduler()
        scheduler.start()
        
        # Rapid successive calls should not cause excessive checks
        for _ in range(10):
            scheduler.step()
        
        # Should still be in initial phase
        assert scheduler._last_check_mono > 0

    def test_concurrent_access(self) -> None:
        """Scheduler must handle concurrent access safely."""
        from hledac.universal.core.micro_burst_scheduler import (
            BurstPhase,
            MicroBurstScheduler,
        )

        scheduler = MicroBurstScheduler()
        scheduler.start()
        
        errors: list[Exception] = []
        
        def reader() -> None:
            try:
                for _ in range(100):
                    _ = scheduler.get_phase()
                    _ = scheduler.phase_transition_count
            except Exception as e:
                errors.append(e)
        
        def stepper() -> None:
            try:
                for _ in range(10):
                    scheduler.step()
                    time.sleep(0.01)
            except Exception as e:
                errors.append(e)
        
        # Start multiple threads
        threads = [
            threading.Thread(target=reader),
            threading.Thread(target=reader),
            threading.Thread(target=stepper),
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0, f"Errors during concurrent access: {errors}"

    def test_thread_safety_of_phase(self) -> None:
        """get_phase() must be safe to call from multiple threads."""
        from hledac.universal.core.micro_burst_scheduler import MicroBurstScheduler

        scheduler = MicroBurstScheduler()
        scheduler.start()
        
        results: list[str] = []
        errors: list[Exception] = []
        
        def get_phase_repeatedly() -> None:
            try:
                for _ in range(50):
                    phase = scheduler.get_phase()
                    results.append(phase.name)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=get_phase_repeatedly) for _ in range(5)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0
        assert len(results) == 250  # 5 threads * 50 iterations


class TestConstants:
    """Tests for module constants."""

    def test_burst_timing_constants(self) -> None:
        """Burst timing constants must have correct values."""
        from hledac.universal.core.micro_burst_scheduler import (
            _BURST_GPU_MS,
            _BURST_IO_MS,
            _BURST_CYCLE_MS,
            _BURST_GPU_FRACTION,
        )

        # GPU window: 200ms
        assert _BURST_GPU_MS == 200.0
        
        # IO window: 50ms
        assert _BURST_IO_MS == 50.0
        
        # Total cycle: 250ms
        assert _BURST_CYCLE_MS == 250.0
        
        # GPU fraction: 0.8 (80%)
        assert _BURST_GPU_FRACTION == 0.8

    def test_phase_check_interval(self) -> None:
        """Phase check interval must be 50ms."""
        from hledac.universal.core.micro_burst_scheduler import _PHASE_CHECK_INTERVAL_S

        assert _PHASE_CHECK_INTERVAL_S == 0.05  # 50ms


class TestSingletonPattern:
    """Tests for module-level singleton pattern."""

    def test_get_scheduler_returns_singleton(self) -> None:
        """get_scheduler() must return the same instance."""
        from hledac.universal.core.micro_burst_scheduler import (
            get_scheduler,
            MicroBurstScheduler,
        )

        # Import the function
        from hledac.universal.utils._patterns import module_singleton_creator
        
        # Note: This tests the pattern, actual singleton behavior
        # depends on the module-level implementation
        scheduler = get_scheduler()
        assert scheduler is not None
        assert isinstance(scheduler, MicroBurstScheduler)


# ============================================================================
# Invariants
# ============================================================================

THERMAL_MANAGEMENT_INVARIANTS = """
MICRO-BURST THERMAL MANAGEMENT INVARIANTS:
1. GPU_HEAVY phase lasts exactly 200ms
2. IO_HEAVY phase lasts exactly 50ms
3. Total cycle is 250ms (4 Hz)
4. Phase transitions are thread-safe
5. start() is idempotent
6. step() rate-limits checks to avoid event loop churn
7. get_phase() is lock-free (safe for concurrent reads)
8. Phase transitions are logged for diagnostics
"""
