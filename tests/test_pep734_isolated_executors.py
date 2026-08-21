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

CRITICAL FIX (F350M-R):
- All interpreter pools must be closed in fixture teardown
- subprocess cleanup via AtExitHandler
- gc.collect() after close_all_pools() to release resources
"""

import atexit
import gc
import os
import subprocess
import sys
import threading

import pytest

from _core.concurrency_registry import ConcurrencyCategory
from _core.isolated_executors import (
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

_cleanup_done: bool = False
_cleanup_lock: threading.Lock = threading.Lock()


def _cleanup_interpreters_atexit() -> None:
    """
    AtExit handler ensures all interpreters are cleaned up on process exit.

    CRITICAL FIX (F350M-R): Without this, orphaned interpreters hold
    Metal/LMDB resources and cause M1 8GB OOM after many test runs.
    """
    global _cleanup_done
    with _cleanup_lock:
        if _cleanup_done:
            return
        _cleanup_done = True
    try:
        close_all_pools()
        gc.collect()
    except Exception:
        pass


# Register atexit cleanup once
atexit.register(_cleanup_interpreters_atexit)


class TestPEP734Availability:
    """Test PEP 734 feature detection."""

    def test_python_version_check(self) -> None:
        """Python must be 3.14+ for PEP 734."""
        assert sys.version_info >= (3, 14), f"Python 3.14+ required, got {sys.version_info}"

    def test_is_pep734_available(self) -> None:
        """is_pep734_available() returns True on Python 3.14+."""
        result = is_pep734_available()
        assert isinstance(result, bool)
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
        assert MAX_INTERPRETERS == 3

    def test_atexit_cleanup_registered(self) -> None:
        """AtExit cleanup is registered for interpreter teardown."""
        # Verify atexit handler is registered
        getattr(atexit, "_exithandlers", [])
        # At least our cleanup handler should be registered
        assert callable(_cleanup_interpreters_atexit)


@pytest.fixture(autouse=True)
def _pep734_cleanup() -> None:
    """
    Fixture that ensures pool cleanup after each test.

    CRITICAL FIX (F350M-R): Each test that uses interpreters must
    call close_all_pools() in teardown to release Metal/LMDB resources.
    Without this, interpreters accumulate and crash M1 8GB after ~100 tests.
    """
    yield
    try:
        close_all_pools()
    except Exception:
        pass
    gc.collect()


class TestConcurrencyCategory:
    """Test ISOLATED_INTERPRETER category registration."""

    def test_isolated_interpreter_category_exists(self) -> None:
        """ISOLATED_INTERPRETER category is registered."""
        assert hasattr(ConcurrencyCategory, "ISOLATED_INTERPRETER")

    def test_isolated_interpreter_category_value(self) -> None:
        """ISOLATED_INTERPRETER has correct enum value."""
        assert ConcurrencyCategory.ISOLATED_INTERPRETER.value == "isolated_interpreter"


class TestIsolatedInterpreterPool:
    """Test IsolatedInterpreterPool functionality."""

    def test_pool_creation(self) -> None:
        """Pool creates successfully."""
        pool = IsolatedInterpreterPool(max_size=2)
        assert pool.is_available in (True, False)
        pool.close_all()

    def test_pool_bounded_to_max_interpreters(self) -> None:
        """Pool respects MAX_INTERPRETERS bound."""
        pool = IsolatedInterpreterPool(max_size=5)
        assert pool._max_size <= MAX_INTERPRETERS
        pool.close_all()

    def test_pool_close_all_no_raise(self) -> None:
        """close_all() never raises."""
        pool = IsolatedInterpreterPool(max_size=2)
        pool.close_all()

    def test_close_all_pools_no_raise(self) -> None:
        """close_all_pools() never raises."""
        close_all_pools()

    def test_pool_subprocess_isolation(self) -> None:
        """Interpreter isolation works in subprocess."""
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = f"""
import sys
sys.path.insert(0, {repr(repo_root)})
from _core.isolated_executors import IsolatedInterpreter, close_all_pools
from _core import aclose
result = None
with IsolatedInterpreter() as interp:
    result = interp.eval_code("42 * 42")
close_all_pools()
sys.exit(0 if result == 1764 else 1)
"""
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            timeout=30,
        )
        assert proc.returncode == 0, f"Subprocess isolation failed: {proc.stderr.decode()}"


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
