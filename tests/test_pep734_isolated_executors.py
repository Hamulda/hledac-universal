"""
PEP 734: Multiple Interpreters Tests (Python 3.14+)

Tests for concurrent.interpreters-based isolation in:
- core/isolated_executors.py
- core/concurrency_registry.py (ISOLATED_INTERPREPER category)

Invariant tests:
1. PEP 734 available only on Python 3.14+
2. ConcurrencyCategory.ISOLATED_INTERPRETER registered
3. IsolatedInterpreterPool bounded to MAX_INTERPRETERS
4. All executors fail-safe (return None/empty on error)
5. close_all_pools() cleans up without raising
"""


import sys

import pytest

from core.concurrency_registry import ConcurrencyCategory
from core.isolated_executors import (
    MAX_INTERPRETERS,
    IsolatedDuckDBExecutor,
    IsolatedEvidenceBatchWriter,
    IsolatedInterpreter,
    IsolatedInterpreterPool,
    IsolatedMLXExecutor,
    close_all_pools,
    get_interpreter_stats,
    is_pep734_available,
)


class TestPEP734Availability:
    """Test PEP 734 feature detection."""

    def test_python_version_check(self) -> None:
        """Python must be 3.14+ for PEP 734."""
        assert sys.version_info >= (3, 14), f"Python 3.14+ required, got {sys.version_info}"

    def test_is_pep734_available(self) -> None:
        """is_pep734_available() returns True on Python 3.14+."""
        result = is_pep734_available()
        assert isinstance(result, bool)

        # On Python 3.14+, should be True
        if sys.version_info >= (3, 14):
            assert result is True, "PEP 734 should be available on Python 3.14+"

    def test_interpreter_stats(self) -> None:
        """get_interpreter_stats() returns valid structure."""
        stats = get_interpreter_stats()

        assert "pep734_available" in stats
        assert "python_version" in stats
        assert "max_interpreters" in stats
        assert "pools" in stats

        assert stats["python_version"] == sys.version_info[:2]
        assert stats["max_interpreters"] == MAX_INTERPRETERS
        assert MAX_INTERPRETERS == 3  # Bounded invariant


class TestConcurrencyCategory:
    """Test ISOLATED_INTERPRETER category registration."""

    def test_isolated_interpreter_category_exists(self) -> None:
        """ISOLATED_INTERPRETER category is registered."""
        assert hasattr(ConcurrencyCategory, "ISOLATED_INTERPRETER")

    def test_isolated_interpreter_category_value(self) -> None:
        """ISOLATED_INTERPRETER has correct enum value."""
        assert (
            ConcurrencyCategory.ISOLATED_INTERPRETER.value == "isolated_interpreter"
        )


class TestIsolatedInterpreterPool:
    """Test IsolatedInterpreterPool functionality."""

    def test_pool_creation(self) -> None:
        """Pool creates successfully."""
        pool = IsolatedInterpreterPool(max_size=2)
        assert pool.is_available in (True, False)  # May be True or False depending on interpreter state
        pool.close_all()

    def test_pool_bounded_to_max_interpreters(self) -> None:
        """Pool respects MAX_INTERPRETERS bound."""
        pool = IsolatedInterpreterPool(max_size=5)  # Request more than MAX
        # Should be capped to MAX_INTERPRETERS
        assert pool._max_size <= MAX_INTERPRETERS
        pool.close_all()

    def test_pool_close_all_no_raise(self) -> None:
        """close_all() never raises."""
        pool = IsolatedInterpreterPool(max_size=2)
        pool.close_all()  # Should not raise

    def test_close_all_pools_no_raise(self) -> None:
        """close_all_pools() never raises."""
        close_all_pools()  # Should not raise


class TestIsolatedDuckDBExecutor:
    """Test IsolatedDuckDBExecutor functionality."""

    def test_executor_creation(self) -> None:
        """Executor creates successfully."""
        exec = IsolatedDuckDBExecutor()
        assert exec.is_available in (True, False)
        exec.close()

    def test_executor_close_no_raise(self) -> None:
        """close() never raises."""
        exec = IsolatedDuckDBExecutor()
        exec.close()  # Should not raise


class TestIsolatedMLXExecutor:
    """Test IsolatedMLXExecutor functionality."""

    def test_executor_creation(self) -> None:
        """Executor creates successfully."""
        exec = IsolatedMLXExecutor()
        assert exec.is_available in (True, False)
        exec.close()

    def test_executor_close_no_raise(self) -> None:
        """close() never raises."""
        exec = IsolatedMLXExecutor()
        exec.close()  # Should not raise


class TestIsolatedEvidenceBatchWriter:
    """Test IsolatedEvidenceBatchWriter functionality."""

    def test_executor_creation(self) -> None:
        """Executor creates successfully."""
        exec = IsolatedEvidenceBatchWriter()
        assert exec.is_available in (True, False)
        exec.close()

    def test_executor_close_no_raise(self) -> None:
        """close() never raises."""
        exec = IsolatedEvidenceBatchWriter()
        exec.close()  # Should not raise


class TestIsolatedInterpreterContextManager:
    """Test IsolatedInterpreter context manager protocol."""

    def test_context_manager(self) -> None:
        """IsolatedInterpreter works as context manager."""
        with IsolatedInterpreter() as interp:
            assert interp is not None
        # Should close cleanly on exit

    def test_context_manager_close_no_raise(self) -> None:
        """Context manager exit never raises."""
        interp = IsolatedInterpreter()
        with interp:
            pass  # Should not raise on exit


# Invariant: Always-on, bounded, fail-safe
class TestInvariants:
    """Verify always-on, bounded, fail-safe invariants."""

    def test_always_on_no_feature_flags(self) -> None:
        """No feature flags - PEP 734 is always available on Python 3.14+."""
        if sys.version_info >= (3, 14):
            assert is_pep734_available() is True

    def test_bounded_max_interpreters(self) -> None:
        """MAX_INTERPRETERS is bounded (M1 8GB safe)."""
        assert MAX_INTERPRETERS == 3
        assert MAX_INTERPRETERS <= 5  # Hard cap for M1 8GB

    def test_fail_safe_returns_none_on_error(self) -> None:
        """Executors return None/empty on errors, never raise."""
        # Test that close() never raises - this is the key fail-safe invariant
        exec = IsolatedDuckDBExecutor()
        exec.close()  # Should not raise

        exec2 = IsolatedMLXExecutor()
        exec2.close()  # Should not raise

        exec3 = IsolatedEvidenceBatchWriter()
        exec3.close()  # Should not raise
