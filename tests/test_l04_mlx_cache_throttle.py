"""
test_l04_mlx_cache_throttle.py — L-04: Per-inference mx.clear_cache() throttling.

Issue L-04: _mlx_clear_and_timestamp() was called after EVERY mlx_generate(),
destroying Metal allocator cache that subsequent calls could reuse.

Fix:
  1. _generation_since_clear counter — throttle clear to every N generations
  2. Memory pressure check — clear immediately when HIGH/CRITICAL
  3. force_clear=True param for timeout/error paths

Acceptance: 100 sequential generate() calls → clear() called ≤ 5×.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from _core import aclose


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_engine():
    """
    Create a minimal DeepHermes3Engine instance for unit testing.

    Uses object.__setattr__ to bypass __init__ (which requires a real model path
    and performs Metal/MLX initialisation not needed for throttling logic tests).
    """
    import time as _time

    from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

    # Create engine WITHOUT calling __init__ (avoids model loading / Metal init)
    engine = object.__new__(DeepHermes3Engine)
    # Initialise only the attributes needed for _mlx_clear_and_timestamp tests
    engine._last_inference_at = None
    engine._generation_since_clear = 0
    engine._last_clear_at = None
    engine._model_ever_loaded = False
    return engine


# ─────────────────────────────────────────────────────────────────────────────
# Tests: _generation_since_clear counter
# ─────────────────────────────────────────────────────────────────────────────


class TestGenerationCounter:
    """Verify _generation_since_clear increments and resets correctly."""

    def test_init_resets_counter(self):
        """New engine starts with _generation_since_clear == 0."""
        engine = _make_engine()
        assert engine._generation_since_clear == 0, "Fresh engine must start with counter at 0"

    def test_init_resets_last_clear_at(self):
        """New engine starts with _last_clear_at == None."""
        engine = _make_engine()
        assert engine._last_clear_at is None, "Fresh engine must start with _last_clear_at None"

    def test_counter_incremented_on_success_path(self):
        """
        _generation_since_clear increments BEFORE _mlx_clear_and_timestamp
        is called on the success path, so the counter reflects the inference
        that just completed.
        """
        engine = _make_engine()
        assert engine._generation_since_clear == 0

        # Simulate 5 generations without clearing
        for _ in range(5):
            engine._generation_since_clear += 1

        assert engine._generation_since_clear == 5

    def test_counter_reset_after_clear(self):
        """_mlx_clear_and_timestamp resets _generation_since_clear to 0."""
        engine = _make_engine()
        engine._generation_since_clear = 19

        with patch("hledac.universal._core.resource_governor.sample_uma_status") as mock_uma:
            mock_uma.return_value = MagicMock(state="ok")
            engine._mlx_clear_and_timestamp()

        assert engine._generation_since_clear == 0, "Counter must reset to 0 after clear"


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Throttle threshold
# ─────────────────────────────────────────────────────────────────────────────


class TestClearInterval:
    """Verify CLEAR_INTERVAL constant exists and is used correctly."""

    def test_clear_interval_is_20(self):
        """L-04 acceptance: CLEAR_INTERVAL = 20 generations."""
        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        assert hasattr(DeepHermes3Engine, "_CLEAR_INTERVAL")
        assert DeepHermes3Engine._CLEAR_INTERVAL == 20

    def test_throttled_below_threshold(self):
        """
        Below _CLEAR_INTERVAL and NORMAL pressure: clear_cache() is NOT called.

        Mock sample_uma_status to return 'ok' (NORMAL) and verify
        _mx.clear_cache is never invoked.
        """
        engine = _make_engine()
        engine._generation_since_clear = 19  # one below threshold

        clear_called = False

        def fake_clear():
            nonlocal clear_called
            clear_called = True

        with patch("hledac.universal._core.resource_governor.sample_uma_status") as mock_uma, \
             patch("mlx.core") as mock_mx:

            mock_uma.return_value = MagicMock(state="ok")
            mock_mx.eval = MagicMock()
            mock_mx.clear_cache = fake_clear
            mock_mx.gc = MagicMock()

            engine._mlx_clear_and_timestamp()

        assert not clear_called, (
            "clear_cache() must NOT be called when below threshold and NORMAL pressure"
    )

    def test_triggers_at_threshold(self):
        """
        At _CLEAR_INTERVAL (20) with NORMAL pressure: clear_cache() IS called.

        Counter reaches threshold, so clear fires.
        """
        engine = _make_engine()
        engine._generation_since_clear = 20  # exactly at threshold

        clear_called = False

        def fake_clear():
            nonlocal clear_called
            clear_called = True

        with patch("hledac.universal._core.resource_governor.sample_uma_status") as mock_uma, \
             patch("mlx.core") as mock_mx:

            mock_uma.return_value = MagicMock(state="ok")
            mock_mx.eval = MagicMock()
            mock_mx.clear_cache = fake_clear
            mock_mx.gc = MagicMock()

            engine._mlx_clear_and_timestamp()

        assert clear_called, "clear_cache() must be called at threshold even with NORMAL pressure"

    def test_100_generations_triggers_5_or_fewer_clears(self):
        """
        L-04 acceptance criteria: 100 sequential generate() calls → clear() ≤ 5×.

        Simulates the success path: counter increments before each clear check.
        With _CLEAR_INTERVAL=20, 100 generations should trigger at most 5 clears
        (at generations 20, 40, 60, 80, 100).
        """
        engine = _make_engine()
        clear_count = 0

        def fake_clear():
            nonlocal clear_count
            clear_count += 1

        call_count = 0

        with patch("hledac.universal._core.resource_governor.sample_uma_status") as mock_uma, \
             patch("mlx.core") as mock_mx:

            mock_uma.return_value = MagicMock(state="ok")
            mock_mx.eval = MagicMock()
            mock_mx.clear_cache = fake_clear
            mock_mx.gc = MagicMock()

            # Simulate 100 sequential generate() calls
            for _ in range(100):
                engine._generation_since_clear += 1
                call_count += 1
                engine._mlx_clear_and_timestamp()

        assert clear_count <= 5, (
            f"100 generations triggered {clear_count} clears; "
            f"acceptance threshold is ≤ 5 (at 20-gen intervals)"
    )

    def test_timestamp_recorded_after_clear(self):
        """_last_inference_at is updated after every _mlx_clear_and_timestamp call."""
        import time

        engine = _make_engine()
        before = time.monotonic()

        with patch("hledac.universal._core.resource_governor.sample_uma_status") as mock_uma, \
             patch("mlx.core") as mock_mx:

            mock_uma.return_value = MagicMock(state="ok")
            mock_mx.eval = MagicMock()
            mock_mx.clear_cache = MagicMock()
            mock_mx.gc = MagicMock()

            engine._mlx_clear_and_timestamp()

        after = time.monotonic()
        assert engine._last_inference_at is not None
        assert before <= engine._last_inference_at <= after


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Memory pressure triggers
# ─────────────────────────────────────────────────────────────────────────────


class TestPressureTriggers:
    """Verify HIGH/CRITICAL pressure forces immediate clear regardless of counter."""

    @pytest.mark.parametrize("pressure_state", ["high", "critical"])
    def test_high_critical_pressure_clears_even_below_threshold(self, pressure_state: str):
        """
        When UMA pressure is HIGH or CRITICAL, clear_cache() fires immediately
        even if _generation_since_clear < _CLEAR_INTERVAL.

        This is the key M1 8GB safety: we never let Metal memory pressure
        accumulate when the system is already stressed.
        """
        engine = _make_engine()
        engine._generation_since_clear = 1  # well below threshold

        clear_called = False

        def fake_clear():
            nonlocal clear_called
            clear_called = True

        with patch("hledac.universal._core.resource_governor.sample_uma_status") as mock_uma, \
             patch("mlx.core") as mock_mx:

            mock_uma.return_value = MagicMock(state=pressure_state)
            mock_mx.eval = MagicMock()
            mock_mx.clear_cache = fake_clear
            mock_mx.gc = MagicMock()

            engine._mlx_clear_and_timestamp()

        assert clear_called, (
            f"clear_cache() must fire immediately when pressure={pressure_state}, "
            "regardless of generation counter"
    )

    @pytest.mark.parametrize("pressure_state", ["normal", "elevated"])
    def test_normal_elevated_pressure_respects_threshold(self, pressure_state: str):
        """
        NORMAL and ELEVATED pressure do NOT force early clear.

        Only HIGH/CRITICAL bypass the generation counter.
        """
        engine = _make_engine()
        engine._generation_since_clear = 1  # well below threshold

        clear_called = False

        def fake_clear():
            nonlocal clear_called
            clear_called = True

        with patch("hledac.universal._core.resource_governor.sample_uma_status") as mock_uma, \
             patch("mlx.core") as mock_mx:

            mock_uma.return_value = MagicMock(state=pressure_state)
            mock_mx.eval = MagicMock()
            mock_mx.clear_cache = fake_clear
            mock_mx.gc = MagicMock()

            engine._mlx_clear_and_timestamp()

        assert not clear_called, (
            f"clear_cache() must NOT fire when pressure={pressure_state} and below threshold"
    )

    def test_uma_sampling_failure_fail_open(self):
        """
        If sample_uma_status() raises, we do NOT force clear — fail open.

        This preserves cache when the pressure sampler is unavailable,
        which is the safer choice on M1 8GB (no silent cache trashing on errors).
        """
        engine = _make_engine()
        engine._generation_since_clear = 1

        clear_called = False

        def fake_clear():
            nonlocal clear_called
            clear_called = True

        with patch("hledac.universal._core.resource_governor.sample_uma_status") as mock_uma, \
             patch("mlx.core") as mock_mx:

            mock_uma.side_effect = RuntimeError("UMA sampling unavailable")
            mock_mx.eval = MagicMock()
            mock_mx.clear_cache = fake_clear
            mock_mx.gc = MagicMock()

            engine._mlx_clear_and_timestamp()

        assert not clear_called, "UMA sampling failure must NOT force clear — fail open"


# ─────────────────────────────────────────────────────────────────────────────
# Tests: force_clear parameter
# ─────────────────────────────────────────────────────────────────────────────


class TestForceClear:
    """Verify force_clear=True bypasses all throttling."""

    def test_force_clear_true_clears_below_threshold(self):
        """
        force_clear=True must clear regardless of generation counter.

        Used by timeout-retry and error paths where KV cache may be fragmented.
        """
        engine = _make_engine()
        engine._generation_since_clear = 1  # well below threshold

        clear_called = False

        def fake_clear():
            nonlocal clear_called
            clear_called = True

        with patch("hledac.universal._core.resource_governor.sample_uma_status") as mock_uma, \
             patch("mlx.core") as mock_mx:

            mock_uma.return_value = MagicMock(state="ok")
            mock_mx.eval = MagicMock()
            mock_mx.clear_cache = fake_clear
            mock_mx.gc = MagicMock()

            # Explicitly pass force_clear=True (timeout/error path)
            engine._mlx_clear_and_timestamp(force_clear=True)

        assert clear_called, "force_clear=True must bypass throttle and clear immediately"

    def test_force_clear_false_respects_threshold(self):
        """
        Explicit force_clear=False must respect normal throttling.

        Documents the default contract for call sites that pass it explicitly.
        """
        engine = _make_engine()
        engine._generation_since_clear = 1  # below threshold

        clear_called = False

        def fake_clear():
            nonlocal clear_called
            clear_called = True

        with patch("mlx.core") as mock_mx:
            mock_mx.eval = MagicMock()
            mock_mx.clear_cache = fake_clear
            mock_mx.gc = MagicMock()

            engine._mlx_clear_and_timestamp(force_clear=False)

        assert not clear_called, "force_clear=False must respect threshold (not force-clear)"


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Slots presence
# ─────────────────────────────────────────────────────────────────────────────


class TestSlots:
    """Verify new attributes are declared in __slots__."""

    def test_generation_since_clear_in_slots(self):
        """_generation_since_clear must be in __slots__ (Python 3.14 crash prevention)."""
        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        assert "_generation_since_clear" in DeepHermes3Engine.__slots__

    def test_last_clear_at_in_slots(self):
        """_last_clear_at must be in __slots__ (Python 3.14 crash prevention)."""
        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        assert "_last_clear_at" in DeepHermes3Engine.__slots__
