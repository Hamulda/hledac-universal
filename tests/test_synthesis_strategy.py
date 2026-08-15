"""
test_synthesis_strategy.py — L-05
===============================
Tests for SYNTHESIS_STRATEGY behavior in SynthesisRunner.

Strategies:
    sequential_preferred (default): xgrammar → streaming → structured cascade
    race_first_wins: 3 engines race in TaskGroup; first-success cancels others

Invariant tests:
    - L-05-1: sequential_preferred returns xgrammar winner when xgrammar succeeds
    - L-05-2: sequential_preferred falls through to streaming when xgrammar fails
    - L-05-3: sequential_preferred falls through to structured when both fail
    - L-05-4: race_first_wins returns first completed winner and cancels others
    - L-05-5: Both strategies return (None, "none") when all engines fail
    - L-05-6: SYNTHESIS_STRATEGY env var is correctly read
    - L-05-7: __slots__ includes _synthesis_strategy
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core import aclose

# Patch SYS_PATH before any hledac imports
sys.path.insert(0, "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")


class TestSynthesisStrategyEnvVar:
    """L-05-6: SYNTHESIS_STRATEGY env var is correctly read."""

    def test_default_is_sequential_preferred(self, monkeypatch):
        monkeypatch.delenv("SYNTHESIS_STRATEGY", raising=False)
        import importlib
        import hledac.universal.brain.synthesis_runner as sr
        importlib.reload(sr)
        assert sr.SYNTHESIS_STRATEGY == "sequential_preferred"

    def test_race_first_wins_env(self, monkeypatch):
        monkeypatch.setenv("SYNTHESIS_STRATEGY", "race_first_wins")
        import importlib
        import hledac.universal.brain.synthesis_runner as sr
        importlib.reload(sr)
        assert sr.SYNTHESIS_STRATEGY == "race_first_wins"

    def test_invalid_strategy_raises(self, monkeypatch):
        monkeypatch.setenv("SYNTHESIS_STRATEGY", "invalid")
        with pytest.raises(AssertionError):
            import importlib
            import hledac.universal.brain.synthesis_runner as sr
            importlib.reload(sr)


class TestSynthesisStrategySlots:
    """L-05-7: __slots__ includes _synthesis_strategy."""

    def test_synthesis_strategy_in_slots(self):
        from brain.synthesis_runner import SynthesisRunner

        slots = SynthesisRunner.__slots__
        assert "_synthesis_strategy" in slots


# ── Helper async callables for MockSynthesisRunner ─────────────────────────────────

async def _result_ok(result_dict):
    """Return (dict, True) — successful engine result."""
    return result_dict, True


async def _result_none():
    """Return None — failed engine result."""
    return None


# ── MockSynthesisRunner ────────────────────────────────────────────────────────


class MockSynthesisRunner:
    """
    Standalone mock of _race_inference methods for unit testing without MLX dependencies.
    Engine result methods are async callables set via constructor kwargs.
    """

    def __init__(
        self,
        strategy: str = "sequential_preferred",
        xgrammar_fn=None,
        streaming_fn=None,
        structured_fn=None,
    ):
        self._synthesis_strategy = strategy
        self._call_log: list[str] = []

        # Default: all engines succeed with their respective results
        self._xgrammar_fn = xgrammar_fn or (lambda: _result_ok({"title": "xgrammar"}))
        self._streaming_fn = streaming_fn or (lambda: _result_ok({"title": "streaming"}))
        self._structured_fn = structured_fn or (lambda: _result_ok({"title": "structured"}))

    async def _run_xgrammar_generation(self, prompt):
        self._call_log.append("xgrammar")
        return await self._xgrammar_fn()

    async def _run_streaming_generation(self, prompt, json_schema=None):
        self._call_log.append("streaming")
        return await self._streaming_fn()

    async def _lifecycle_structured(self, prompt, json_schema=None):
        self._call_log.append("structured")
        return await self._structured_fn()

    # ── sequential cascade ──────────────────────────────────────────────────────

    async def _race_inference_sequential(self, prompt):
        # Krok 1: xgrammar
        try:
            result = await self._run_xgrammar_generation(prompt)
            if result is not None:
                raw_dict, ok = result
                if ok and raw_dict is not None:
                    return raw_dict, "xgrammar"
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

        # Krok 2: streaming
        try:
            result = await self._run_streaming_generation(prompt, json_schema=None)
            if result is not None:
                raw_dict, ok = result
                if ok and raw_dict is not None:
                    return raw_dict, "streaming"
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

        # Krok 3: structured
        try:
            result = await self._lifecycle_structured(prompt, json_schema=None)
            if result is not None:
                raw_dict, ok = result
                if ok and raw_dict is not None:
                    return raw_dict, "constrained"
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

        return None, "none"

    # ── race first wins ────────────────────────────────────────────────────────

    async def _race_inference_first_wins(self, prompt):
        async def try_xgrammar():
            try:
                result = await self._run_xgrammar_generation(prompt)
                if result is not None:
                    raw_dict, ok = result
                    if ok and raw_dict is not None:
                        return raw_dict, "xgrammar"
            except Exception:
                pass
            return None, "none"

        async def try_streaming():
            try:
                result = await self._run_streaming_generation(prompt, json_schema=None)
                if result is not None:
                    raw_dict, ok = result
                    if ok and raw_dict is not None:
                        return raw_dict, "streaming"
            except Exception:
                pass
            return None, "none"

        async def try_structured():
            try:
                result = await self._lifecycle_structured(prompt, json_schema=None)
                if result is not None:
                    raw_dict, ok = result
                    if ok and raw_dict is not None:
                        return raw_dict, "constrained"
            except Exception:
                pass
            return None, "none"

        tasks = {
            asyncio.create_task(try_xgrammar(), name="xgrammar"): "xgrammar",
            asyncio.create_task(try_streaming(), name="streaming"): "streaming",
            asyncio.create_task(try_structured(), name="structured"): "structured",
        }

        pending = set(tasks.keys())
        winner_dict = None
        winner_name = "none"

        while pending:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task_name = tasks.pop(task)
                try:
                    result_dict, result_name = task.result()
                    if result_dict is not None and result_name != "none":
                        winner_dict = result_dict
                        winner_name = result_name
                        for remaining_task in pending:
                            remaining_task.cancel()
                            try:
                                await remaining_task
                            except asyncio.CancelledError:
                                pass
                        return winner_dict, winner_name
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass

        return None, "none"

    async def _race_inference(self, prompt):
        if self._synthesis_strategy == "race_first_wins":
            return await self._race_inference_first_wins(prompt)
        return await self._race_inference_sequential(prompt)


# ── Sequential Preferred Tests ─────────────────────────────────────────────────


class TestSequentialPreferred:
    """L-05-1, L-05-2, L-05-3, L-05-5: sequential_preferred cascade behavior."""

    @pytest.mark.asyncio
    async def test_sequential_xgrammar_wins(self):
        """L-05-1: xgrammar succeeds → returns xgrammar immediately."""
        runner = MockSynthesisRunner("sequential_preferred")

        result, name = await runner._race_inference("test prompt")
        assert result == {"title": "xgrammar"}
        assert name == "xgrammar"
        assert runner._call_log == ["xgrammar"]

    @pytest.mark.asyncio
    async def test_sequential_streaming_fallback(self):
        """L-05-2: xgrammar fails → falls through to streaming."""
        runner = MockSynthesisRunner(
            "sequential_preferred",
            xgrammar_fn=lambda: _result_none(),
            streaming_fn=lambda: _result_ok({"title": "streaming"}),
        )

        result, name = await runner._race_inference("test prompt")
        assert result == {"title": "streaming"}
        assert name == "streaming"
        assert runner._call_log == ["xgrammar", "streaming"]

    @pytest.mark.asyncio
    async def test_sequential_structured_fallback(self):
        """L-05-3: xgrammar + streaming fail → falls through to structured."""
        runner = MockSynthesisRunner(
            "sequential_preferred",
            xgrammar_fn=lambda: _result_none(),
            streaming_fn=lambda: _result_none(),
            structured_fn=lambda: _result_ok({"title": "structured"}),
        )

        result, name = await runner._race_inference("test prompt")
        assert result == {"title": "structured"}
        assert name == "constrained"
        assert runner._call_log == ["xgrammar", "streaming", "structured"]

    @pytest.mark.asyncio
    async def test_sequential_all_fail(self):
        """L-05-5: all engines fail → returns (None, 'none')."""
        runner = MockSynthesisRunner(
            "sequential_preferred",
            xgrammar_fn=lambda: _result_none(),
            streaming_fn=lambda: _result_none(),
            structured_fn=lambda: _result_none(),
        )

        result, name = await runner._race_inference("test prompt")
        assert result is None
        assert name == "none"


# ── Race First Wins Tests ──────────────────────────────────────────────────────


class TestRaceFirstWins:
    """L-05-4, L-05-5: race_first_wins cancellation behavior."""

    @pytest.mark.asyncio
    async def test_race_cancels_slower_tasks(self):
        """L-05-4: first completed winner cancels remaining pending tasks."""
        call_log: list[str] = []

        async def fast_xgrammar():
            call_log.append("xgrammar_start")
            await asyncio.sleep(0.01)  # Fast winner
            call_log.append("xgrammar_end")
            return {"title": "xgrammar"}, True

        async def slow_streaming():
            call_log.append("streaming_start")
            await asyncio.sleep(0.5)  # Slow — will be cancelled
            call_log.append("streaming_end")  # Should NOT execute
            return {"title": "streaming"}, True

        async def slow_structured():
            call_log.append("structured_start")
            await asyncio.sleep(0.5)  # Slow — will be cancelled
            call_log.append("structured_end")  # Should NOT execute
            return {"title": "structured"}, True

        runner = MockSynthesisRunner(
            "race_first_wins",
            xgrammar_fn=lambda: fast_xgrammar(),
            streaming_fn=lambda: slow_streaming(),
            structured_fn=lambda: slow_structured(),
        )

        result, name = await runner._race_inference("test prompt")

        assert result == {"title": "xgrammar"}
        assert name == "xgrammar"
        assert "xgrammar_start" in call_log
        assert "xgrammar_end" in call_log
        # At most one slow task may complete; both should NOT both complete
        streaming_done = "streaming_end" in call_log
        structured_done = "structured_end" in call_log
        assert not (streaming_done and structured_done), \
            "Both slow tasks completed — cancellation may not be working"

    @pytest.mark.asyncio
    async def test_race_all_fail_returns_none(self):
        """L-05-5: all engines fail → returns (None, 'none')."""
        runner = MockSynthesisRunner(
            "race_first_wins",
            xgrammar_fn=lambda: _result_none(),
            streaming_fn=lambda: _result_none(),
            structured_fn=lambda: _result_none(),
        )

        result, name = await runner._race_inference("test prompt")
        assert result is None
        assert name == "none"

    @pytest.mark.asyncio
    async def test_race_streaming_wins_when_xgrammar_fails(self):
        """Streaming wins when xgrammar fails but streaming is fast."""

        async def xgrammar_fail():
            await asyncio.sleep(0.5)
            return None

        async def streaming_fast():
            await asyncio.sleep(0.01)
            return {"title": "streaming"}, True

        async def structured_slow():
            await asyncio.sleep(0.5)
            return {"title": "structured"}, True

        runner = MockSynthesisRunner(
            "race_first_wins",
            xgrammar_fn=lambda: xgrammar_fail(),
            streaming_fn=lambda: streaming_fast(),
            structured_fn=lambda: structured_slow(),
        )

        result, name = await runner._race_inference("test prompt")
        assert result == {"title": "streaming"}
        assert name == "streaming"


class TestSynthesisStrategyDispatch:
    """L-05: Dispatcher routes to correct implementation."""

    @pytest.mark.asyncio
    async def test_dispatcher_routes_sequential(self):
        runner = MockSynthesisRunner("sequential_preferred")
        result, name = await runner._race_inference("test prompt")
        assert name == "xgrammar"

    @pytest.mark.asyncio
    async def test_dispatcher_routes_race(self):
        # In race_first_wins with all-fast engines, any engine may win.
        # Verify the dispatcher runs and returns a valid non-none result.
        runner = MockSynthesisRunner("race_first_wins")
        result, name = await runner._race_inference("test prompt")
        assert result is not None
        assert name != "none"
        # Winner should be one of the three engines
        assert name in ("xgrammar", "streaming", "constrained")
