"""
F196C: Test misc optimizations.

Verifies micro-optimizations from F196C sprint.
"""
import inspect
import pytest

from hledac.universal.brain.hypothesis_engine import HypothesisEngine
from hledac.universal.graph import quantum_pathfinder
from hledac.universal.network.jarm_fingerprinter import _JARMFingerprinter


class TestStringConcatOptimization:
    """Verify string concat was converted to list+join."""

    def test_hypothesis_engine_uses_list_for_reasoning(self):
        """Verify attempt_falsification uses reasoning_parts list instead of string concat."""
        source = inspect.getsource(HypothesisEngine.attempt_falsification)

        # Should use list for O(1) append
        assert "reasoning_parts" in source, \
            "Should use reasoning_parts list for O(1) append"

        # Should NOT use += for string concatenation on plain 'reasoning'
        # (reasoning_parts += is fine since it's a list)
        lines_with_concat = [line.strip() for line in source.split('\n')
                          if 'reasoning +=' in line and 'reasoning_parts' not in line]
        assert len(lines_with_concat) == 0, \
            f"Should not use string concat 'reasoning +=', found: {lines_with_concat}"

        # Should join at the end
        assert '"; ".join(reasoning_parts)' in source, \
            "Should use '; '.join() at the end"


class TestDuckPGQCaching:
    """Verify DuckPGQ install is cached."""

    def test_duckpgq_install_is_cached(self):
        """Verify duckpgq installation check uses _duckpgq_checked flag."""
        source = inspect.getsource(quantum_pathfinder._ensure_duckpgq)

        # Should have the caching flag
        assert "_duckpgq_checked" in source, \
            "_duckpgq_checked flag should exist for caching"

        # Should check flag before installing
        assert "if _duckpgq_checked:" in source, \
            "Should check _duckpgq_checked before attempting install"

        # After check, should set the flag
        assert "_duckpgq_checked = True" in source, \
            "Should set _duckpgq_checked = True after first check"


class TestJarmFingerprinterOptimization:
    """Verify jarm_fingerprinter optimization."""

    def test_jarm_uses_async_sleep(self):
        """Verify jarm uses asyncio.sleep instead of blocking time.sleep in _compute_jarm_async."""
        source = inspect.getsource(_JARMFingerprinter._compute_jarm_async)

        # Should use asyncio.sleep
        assert 'asyncio.sleep' in source, \
            "Should use asyncio.sleep for rate limiting"

        # Should NOT have blocking time.sleep for the rate limiting delay
        actual_sleep_calls = [line.strip() for line in source.split('\n')
                           if 'sleep(' in line and 'time.sleep' in line]
        assert len(actual_sleep_calls) == 0, \
            f"Should not use blocking time.sleep in _compute_jarm_async, found: {actual_sleep_calls}"
