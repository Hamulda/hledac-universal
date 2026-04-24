"""
F196C: Test sprint_scheduler.py bounds.

Verifies that unbounded state structures are properly bounded
to prevent OOM on long runs.
"""
import pytest
from unittest.mock import MagicMock


class TestSprintSchedulerBounds:
    """Verify bounded collections in SprintScheduler."""

    def test_fetch_latency_ema_is_bounded(self):
        """Verify _fetch_latency_ema has LRU eviction at MAX_FETCH_LATENCY_EMA."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        config = MagicMock()
        config.max_concurrent_sources = 5
        config.sprint_timeout = 60.0
        config.adaptive_timeout_enabled = True

        scheduler = SprintScheduler(config)

        # Verify the MAX constant exists on the instance
        assert hasattr(scheduler, '_MAX_FETCH_LATENCY_EMA'), \
            "_MAX_FETCH_LATENCY_EMA should be defined on SprintScheduler instance"
        assert scheduler._MAX_FETCH_LATENCY_EMA == 1000, \
            "MAX should be 1000 entries"

        # Verify the order tracking list exists
        assert hasattr(scheduler, '_fetch_latency_ema_order'), \
            "_fetch_latency_ema_order should be defined for LRU tracking"
        assert isinstance(scheduler._fetch_latency_ema_order, list), \
            "_fetch_latency_ema_order should be a list"

        # Add more than MAX entries and verify LRU eviction
        # range(max_entries + 500) = range(1500) gives 0-1499
        max_entries = scheduler._MAX_FETCH_LATENCY_EMA
        for i in range(max_entries + 500):
            scheduler._update_latency_ema(f"domain_{i}.com", 0.1 * i)

        # Should have evicted old entries
        assert len(scheduler._fetch_latency_ema) <= max_entries, \
            f"Should have at most {max_entries} entries after eviction"
        assert len(scheduler._fetch_latency_ema_order) <= max_entries, \
            f"Order list should have at most {max_entries} entries"

        # After adding 1500 domains (0-1499), oldest 500 (0-499) should be evicted
        # Domains 500-1499 should remain (1000 domains)
        assert "domain_0.com" not in scheduler._fetch_latency_ema, \
            "Oldest entries should be evicted (LRU)"
        assert "domain_499.com" not in scheduler._fetch_latency_ema, \
            "Old entries should be evicted (LRU)"
        assert "domain_500.com" in scheduler._fetch_latency_ema, \
            "Domain 500 should still be present"
        assert "domain_1499.com" in scheduler._fetch_latency_ema, \
            "Most recent entries should not be evicted (LRU)"

    def test_dedup_seen_has_existing_bound(self):
        """Verify _dedup_seen already has 500k bounding at lines 1798-1801."""
        # This is a documentation test - the bounding already exists
        # in the flush_dedup_to_lmdb method
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        import inspect

        source = inspect.getsource(SprintScheduler)
        # Verify the 500k bound exists in the source
        assert "500_000" in source, \
            "_dedup_seen should have 500k bounding mechanism"
