"""Tests for DSPy optimizer - mock psutil, _memory_mgr, dspy, evidence log."""
from unittest.mock import MagicMock, patch

import pytest


class TestDSPyOptimizerInit:
    """Test DSPy optimizer initialization."""

    def test_optimizer_init(self):
        """Test optimizer initializes with defaults."""
        from hledac.universal.brain.dspy_optimizer import DSPyOptimizer

        mock_brain = MagicMock()
        optimizer = DSPyOptimizer(mock_brain)

        assert optimizer._brain == mock_brain
        assert optimizer._failure_count == 0
        assert optimizer._max_failures == 3


class TestDSPyOptimizerGuards:
    """Test idle guards - memory, CPU, thermal, energy."""

    @patch('hledac.universal.brain.dspy_optimizer.psutil')
    def test_memory_guard_blocks(self, mock_psutil):
        """Test memory guard blocks when RAM < 4GB."""
        from hledac.universal.brain.dspy_optimizer import DSPyOptimizer

        mock_brain = MagicMock()
        mock_brain._orch = None
        optimizer = DSPyOptimizer(mock_brain)

        mock_psutil.virtual_memory.return_value = MagicMock(available=2.0 * 1024**3)
        mock_psutil.cpu_percent.return_value = 10

        assert optimizer._should_optimize() is False


class TestDSPyOptimizerDefaultPrompts:
    """Test default prompts."""

    def test_analysis_prompt(self):
        """Test analysis prompt template."""
        from hledac.universal.brain.dspy_optimizer import DSPyOptimizer

        mock_brain = MagicMock()
        optimizer = DSPyOptimizer(mock_brain)

        prompt = optimizer.get_prompt('analysis', {'complexity': 'medium'})

        assert isinstance(prompt, str)
        assert len(prompt) > 0


class TestDSPyOptimizerPerformance:
    """Test performance recording."""

    def test_record_performance(self):
        """Test performance recording."""
        from hledac.universal.brain.dspy_optimizer import DSPyOptimizer

        mock_brain = MagicMock()
        optimizer = DSPyOptimizer(mock_brain)

        optimizer.record_performance('test_task', 0.8)
        optimizer.record_performance('test_task', 0.9)

        assert 0.8 in optimizer._performance_history['test_task']
        assert 0.9 in optimizer._performance_history['test_task']


class TestDSPyOptimizerCache:
    """Test cache persistence."""

    def test_load_cache_nonexistent(self):
        """Test loading non-existent cache."""
        from hledac.universal.brain.dspy_optimizer import DSPyOptimizer

        mock_brain = MagicMock()
        optimizer = DSPyOptimizer(mock_brain)

        assert optimizer._optimized_prompts == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
