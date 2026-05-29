#!/usr/bin/env python3
"""
Sprint 5V: BoundedOrderedDict FIFO Eviction Test
Hard rule #11-13: Bounded domain dedup with FIFO eviction
"""
import sys
import unittest

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

from collections import OrderedDict


class BoundedOrderedDict(OrderedDict):
    """Hard rule #12: BoundedOrderedDict wrapper with FIFO eviction."""

    def __init__(self, maxsize: int):
        super().__init__()
        self.maxsize = maxsize

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            self.popitem(last=False)  # FIFO: remove oldest


class TestBoundedOrderedDict(unittest.TestCase):

    def test_fifo_eviction(self):
        """Hard rule #13: FIFO eviction test."""
        d = BoundedOrderedDict(maxsize=5)

        # Add 10 items - should keep last 5
        for i in range(10):
            d[f"key{i}"] = i

        # Should have max 5
        self.assertEqual(len(d), 5)

        # Oldest (key0-key4) should be evicted, key5-key9 should remain
        self.assertIn('key5', d)
        self.assertIn('key9', d)
        self.assertNotIn('key0', d)
        self.assertNotIn('key1', d)

        # First key should be key5 (oldest remaining)
        first_key = next(iter(d))
        self.assertEqual(first_key, 'key5')

    def test_deduplication(self):
        """Test that duplicate keys don't increase size."""
        d = BoundedOrderedDict(maxsize=5)

        d['a'] = 1
        d['b'] = 2
        d['c'] = 3
        d['a'] = 4  # Update existing

        self.assertEqual(len(d), 3)
        # Value should be updated
        self.assertEqual(d['a'], 4)

    def test_clear(self):
        """Test clear resets properly."""
        d = BoundedOrderedDict(maxsize=5)
        d['a'] = 1
        d['b'] = 2

        d.clear()

        self.assertEqual(len(d), 0)
        # Should still respect maxsize after clear
        d['x'] = 1
        self.assertEqual(len(d), 1)


if __name__ == '__main__':
    unittest.main()
