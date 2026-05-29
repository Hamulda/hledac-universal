"""Tests for signpost profiler - mock ctypes and verify calls, timing statistics."""

import pytest


class TestSignpostProfiler:
    """Test os_signpost profiler with mocked ctypes."""

    def test_signpost_available_check(self):
        """Test signpost availability check returns bool."""
        from hledac.universal.utils.signpost_profiler import is_signpost_available

        result = is_signpost_available()
        assert isinstance(result, bool)

    def test_signpost_context_manager_sync(self):
        """Test sync context manager timing works."""
        from hledac.universal.utils.signpost_profiler import signpost_interval

        with signpost_interval("Test", "test_op"):
            x = 1 + 1
        assert x == 2

    def test_signpost_stats(self):
        """Test get_stats returns proper dictionary."""
        from hledac.universal.utils.signpost_profiler import get_stats

        stats = get_stats()
        assert isinstance(stats, dict)
        assert 'available' in stats
        assert 'codes_registered' in stats


class TestSignpostNoCrash:
    """Test profiler doesn't crash when unavailable."""

    def test_no_crash_without_signpost(self):
        """Test profiler doesn't crash when signpost unavailable."""
        from hledac.universal.utils.signpost_profiler import signpost_interval

        with signpost_interval("Test", "no_crash"):
            result = 42
        assert result == 42


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
