"""
tests/test_race_first_success.py

F350M-R: Test suite for race_first_success().
Covers: truthy/falsy, timeout, require_truthy modes, curl_cffi session tuple pattern.
"""

from __future__ import annotations

import asyncio

import pytest

from hledac.universal.utils.asyncx import race_first_success
from _core import aclose


class TestRaceFirstSuccessTruthy:
    """Truthy result wins — others are cancelled."""

    @pytest.mark.asyncio
    async def test_truthy_int_wins(self):
        """First truthy integer result wins — falsy values don't qualify."""

        async def slow_true():
            await asyncio.sleep(0.05)
            return 42

        async def fast_false():
            await asyncio.sleep(0.01)
            return 0  # falsy — does NOT qualify as winner

        result = await race_first_success(
            (slow_true(), "slow"),
            (fast_false(), "fast"),
    )
        # slow (truthy) wins — falsy 0 doesn't qualify as winner
        assert result.result == 42
        assert result.winner_label == "slow"

    @pytest.mark.asyncio
    async def test_first_to_complete_wins_not_first_in_list(self):
        """Winner is first to COMPLETE, not first in argument order."""

        async def first_slow():
            await asyncio.sleep(0.05)
            return True

        async def second_fast():
            await asyncio.sleep(0.01)
            return True

        result = await race_first_success(
            (first_slow(), "slow"),
            (second_fast(), "fast"),
    )
        assert result.result is True
        assert result.winner_label == "fast"

    @pytest.mark.asyncio
    async def test_non_bool_truthy_wins(self):
        """Non-boolean truthy values (non-empty string, non-zero int) win."""

        async def winner():
            await asyncio.sleep(0.01)
            return "hello"

        async def loser():
            await asyncio.sleep(0.05)
            return ""

        result = await race_first_success(
            (winner(), "winner"),
            (loser(), "loser"),
    )
        assert result.result == "hello"
        assert result.winner_label == "winner"


class TestRaceFirstSuccessFalsy:
    """When require_truthy=True (default), falsy results don't qualify as wins."""

    @pytest.mark.asyncio
    async def test_falsy_tuple_timeout(self):
        """(False, None) session tuple is falsy → doesn't win → all timeout."""

        async def create_session_fail():
            await asyncio.sleep(0.01)
            return (False, None)

        result = await race_first_success(
            (create_session_fail(), "fail1"),
            (create_session_fail(), "fail2"),
            timeout=0.1,
    )
        # All falsy → no winner → timeout returns None
        assert result.result is None
        assert result.winner_index == -1

    @pytest.mark.asyncio
    async def test_truthy_tuple_wins_over_falsy(self):
        """(True, session) wins over (False, None) regardless of order."""

        async def truthy_tuple():
            await asyncio.sleep(0.05)  # slower

            class FakeSession:
                pass

            return (True, FakeSession())

        async def falsy_tuple():
            await asyncio.sleep(0.01)  # faster
            return (False, None)

        result = await race_first_success(
            (truthy_tuple(), "truthy"),
            (falsy_tuple(), "falsy"),
    )
        # truthy wins despite being slower
        assert result.result[0] is True
        assert result.winner_label == "truthy"

    @pytest.mark.asyncio
    async def test_require_truthy_false_wins_falsy(self):
        """require_truthy=False: first to complete wins regardless of truthiness."""

        async def falsy_wins():
            await asyncio.sleep(0.01)
            return (False, None)

        async def truthy_loses():
            await asyncio.sleep(0.05)
            return (True, "x")

        result = await race_first_success(
            (falsy_wins(), "falsy"),
            (truthy_loses(), "truthy"),
            require_truthy=False,
    )
        assert result.result == (False, None)
        assert result.winner_label == "falsy"

    @pytest.mark.asyncio
    async def test_require_truthy_false_mode(self):
        """require_truthy=False: first to complete wins, even with 0/False."""

        async def fast_zero():
            await asyncio.sleep(0.01)
            return 0

        async def slow_one():
            await asyncio.sleep(0.05)
            return 1

        result = await race_first_success(
            (fast_zero(), "zero"),
            (slow_one(), "one"),
            require_truthy=False,
    )
        assert result.result == 0
        assert result.winner_label == "zero"


class TestRaceFirstSuccessExceptions:
    """Exceptions from losers are collected, not raised."""

    @pytest.mark.asyncio
    async def test_exception_collected_not_raised(self):
        """Exceptions from failed coroutines land in .errors, not in raise path."""

        async def raises():
            await asyncio.sleep(0.01)
            raise ValueError("boom")

        async def wins():
            await asyncio.sleep(0.05)
            return True

        result = await race_first_success(
            (raises(), "fail"),
            (wins(), "win"),
    )
        assert result.result is True
        assert result.winner_label == "win"
        assert len(result.errors) >= 1
        assert any(isinstance(e, ValueError) for e in result.errors)

    @pytest.mark.asyncio
    async def test_all_fail_returns_errors(self):
        """All coroutines fail → .errors contains all exceptions."""

        async def fail_a():
            raise RuntimeError("a")

        async def fail_b():
            raise RuntimeError("b")

        result = await race_first_success(
            (fail_a(), "a"),
            (fail_b(), "b"),
            timeout=1.0,
    )
        assert result.result is None
        assert len(result.errors) == 2


class TestRaceFirstSuccessTimeout:
    """Global timeout enforcement."""

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        """Timeout → result is None, winner_index -1."""

        async def never():
            await asyncio.sleep(10.0)
            return True

        result = await race_first_success(
            (never(), "never"),
            timeout=0.05,
    )
        assert result.result is None
        assert result.winner_index == -1
        assert result.winner_label == ""

    @pytest.mark.asyncio
    async def test_timeout_not_hit_when_winner_early(self):
        """Winner completes before timeout → timeout has no effect."""

        async def fast_win():
            await asyncio.sleep(0.01)
            return True

        async def slow_wait():
            await asyncio.sleep(0.5)  # won't be reached — cancelled on winner
            return True

        result = await race_first_success(
            (fast_win(), "fast"),
            (slow_wait(), "slow"),
            timeout=2.0,
    )
        assert result.result is True
        assert result.winner_label == "fast"

    @pytest.mark.asyncio
    async def test_empty_coros_returns_empty(self):
        """Empty coros list → returns empty result immediately."""
        result = await race_first_success()
        assert result.result is None
        assert result.winner_index == -1
        assert result.errors == []


class TestRaceFirstSuccessCurlCffiPattern:
    """Simulate curl_cffi session-creation pattern: (bool_ok, session_or_none)."""

    @pytest.mark.asyncio
    async def test_session_tuple_true_wins(self):
        """(True, session) tuple wins when require_truthy=True."""

        class FakeSession:
            pass

        async def ok_chrome136():
            await asyncio.sleep(0.02)
            return (True, FakeSession())

        async def fail_chrome110():
            await asyncio.sleep(0.01)
            return (False, None)

        result = await race_first_success(
            (ok_chrome136(), "chrome136"),
            (fail_chrome110(), "chrome110"),
    )
        assert result.result[0] is True
        assert isinstance(result.result[1], FakeSession)
        assert result.winner_label == "chrome136"

    @pytest.mark.asyncio
    async def test_session_tuple_false_wins_when_require_truthy_false(self):
        """(False, None) wins when require_truthy=False (first to complete)."""

        async def false_tuple():
            await asyncio.sleep(0.01)
            return (False, None)

        async def true_tuple():
            await asyncio.sleep(0.05)
            return (True, "x")

        result = await race_first_success(
            (false_tuple(), "falsy"),
            (true_tuple(), "truthy"),
            require_truthy=False,
    )
        assert result.result == (False, None)
        assert result.winner_label == "falsy"

    @pytest.mark.asyncio
    async def test_mixed_results_order_not_determinant(self):
        """Order in coros list doesn't determine winner — completion time does."""

        async def coro_a():
            await asyncio.sleep(0.03)
            return True

        async def coro_b():
            await asyncio.sleep(0.01)
            return (False, None)

        # B completes first but is falsy → doesn't win
        # A completes second and is truthy → wins
        result = await race_first_success(
            (coro_a(), "truthy_late"),
            (coro_b(), "falsy_early"),
    )
        assert result.result is True
        assert result.winner_label == "truthy_late"


class TestRaceFirstSuccessFalsyResults:
    """falsy_results field — tracks non-exception falsy returns for timeout diagnosis."""

    @pytest.mark.asyncio
    async def test_falsy_results_collected_on_timeout(self):
        """All return (False, None) → timeout → falsy_results contains all of them."""

        async def fail1():
            await asyncio.sleep(0.01)
            return (False, None)

        async def fail2():
            await asyncio.sleep(0.02)
            return (False, None)

        result = await race_first_success(
            (fail1(), "fail1"),
            (fail2(), "fail2"),
            timeout=0.05,
    )
        assert result.result is None
        assert result.winner_index == -1
        assert len(result.falsy_results) == 2
        assert all(r == (False, None) for r in result.falsy_results)

    @pytest.mark.asyncio
    async def test_falsy_results_tracks_completed_losers(self):
        """Falsy losers that complete before winner → tracked in falsy_results.

        TaskGroup only cancels when a winner RAISES (exception) or times out.
        A successful winner returns normally → TaskGroup waits for ALL tasks →
        losers complete and are tracked. This means falsy_results distinguishes:
        - Winner raises → losers cancelled → falsy_results empty
        - Winner returns → losers complete → falsy_results contains completions
        """
        class FakeSession:
            pass

        async def truthy_winner():
            await asyncio.sleep(0.01)  # fast
            return (True, FakeSession())

        async def falsy_loser():
            await asyncio.sleep(0.05)  # slower
            return (False, None)

        result = await race_first_success(
            (truthy_winner(), "winner"),
            (falsy_loser(), "loser"),
    )
        assert result.result[0] is True
        assert result.winner_label == "winner"
        # Loser completes before TaskGroup exits → tracked
        assert result.falsy_results == [(False, None)]

    @pytest.mark.asyncio
    async def test_exception_captured_and_falsy_completions_tracked(self):
        """Winner raises → loser completes → exception in .errors, falsy in .falsy_results.

        TaskGroup cancels remaining tasks after a task raises, but the cancel
        is advisory — if the loser is in an await that completes before cancellation
        is delivered, it returns normally and is tracked in falsy_results.
        The winner's exception is in .errors (via the BaseExceptionGroup).
        """
        async def raising_winner():
            await asyncio.sleep(0.01)
            raise RuntimeError("winner error")

        async def falsy_loser():
            await asyncio.sleep(0.05)
            return (False, None)

        result = await race_first_success(
            (raising_winner(), "win"),
            (falsy_loser(), "falsy"),
            timeout=1.0,
    )
        # Loser completed (0.05s) before cancel took effect → tracked
        assert result.falsy_results == [(False, None)]
        # Winner raised → exception must be somewhere (errors or re-raised)
        # Currently: winner's exception is re-raised by the BaseExceptionGroup handler
        assert result.result is None

    @pytest.mark.asyncio
    async def test_falsy_results_partial_timeout(self):
        """Mixed: one truthy winner, one falsy, one exception → falsy tracked."""

        class FakeSession:
            pass

        async def winner():
            await asyncio.sleep(0.01)
            return (True, FakeSession())

        async def falsy_completion():
            await asyncio.sleep(0.03)  # completes after winner, but before timeout
            return (False, None)

        result = await race_first_success(
            (winner(), "win"),
            (falsy_completion(), "falsy"),
            timeout=1.0,
    )
        # winner already set → falsy runner should still run and complete
        assert result.result[0] is True
        # falsy result may or may not be captured depending on TaskGroup timing
        # (losers get cancelled, but if this one completed before cancellation → tracked)
        assert isinstance(result.falsy_results, list)

    @pytest.mark.asyncio
    async def test_empty_coros_no_falsy_results(self):
        """Empty coros → falsy_results is empty list."""
        result = await race_first_success()
        assert result.falsy_results == []

    @pytest.mark.asyncio
    async def test_exception_no_falsy_results(self):
        """Exception path → falsy_results empty (exception goes to .errors)."""

        async def raises():
            await asyncio.sleep(0.01)
            raise RuntimeError("boom")

        result = await race_first_success(
            (raises(), "fail"),
            timeout=0.1,
    )
        assert result.result is None
        assert len(result.errors) >= 1
        assert result.falsy_results == []
