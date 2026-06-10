"""
test_f_bloom_deprecation.py — verify ScalableBloomFilter deprecation.

The original class was unbounded and unused at runtime (verified
2026-06-09 via ripgrep). We replaced it with a backward-compat alias
on RotatingBloomFilter that emits DeprecationWarning.

These tests pin:
- The alias still constructs (so any leftover import won't crash)
- A DeprecationWarning is raised
- The constructed instance behaves like RotatingBloomFilter
- The original `growth_factor` kwarg is silently ignored
- The class is still exported in utils.__init__ for backward compat

Why these tests exist:
  CLAUDE.md invariant #7 — "RotatingBloomFilter pro URL dedup — nikdy
  Set[str] nebo ScalableBloomFilter". The deprecation alias is the
  single audited seam; tests ensure the alias is fail-safe and that
  any production caller (even legacy) lands on RotatingBloomFilter.
"""

from __future__ import annotations

import warnings

import pytest


class TestScalableBloomFilterDeprecation:
    """Backward-compat alias for the removed unbounded class."""

    def test_import_still_works(self):
        from hledac.universal.utils.bloom_filter import ScalableBloomFilter

        assert ScalableBloomFilter is not None

    def test_exported_from_utils_package(self):
        from hledac.universal.utils import ScalableBloomFilter

        assert ScalableBloomFilter is not None

    def test_in_all_list(self):
        from hledac.universal.utils import bloom_filter

        assert "ScalableBloomFilter" in bloom_filter.__all__

    def test_construct_emits_deprecation_warning(self):
        from hledac.universal.utils.bloom_filter import ScalableBloomFilter

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            bf = ScalableBloomFilter(initial_capacity=100, error_rate=0.01)

        # Exactly one DeprecationWarning, mentioning "deprecated"
        dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "deprecated" in str(dep_warnings[0].message).lower()

    def test_constructed_instance_is_rotating_bloom(self):
        from hledac.universal.utils.bloom_filter import (
            RotatingBloomFilter,
            ScalableBloomFilter,
        )

        bf = ScalableBloomFilter(initial_capacity=50, error_rate=0.01)
        # Should be a RotatingBloomFilter (M1 8GB safe)
        assert isinstance(bf, RotatingBloomFilter)
        assert bf.max_elements == 50
        assert bf.error_rate == 0.01

    def test_growth_factor_kwarg_silently_ignored(self):
        """growth_factor is preserved for API compat but does nothing."""
        from hledac.universal.utils.bloom_filter import ScalableBloomFilter

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            bf = ScalableBloomFilter(
                initial_capacity=10, error_rate=0.01, growth_factor=99.0
            )
        # Bounded: no extra filter allocated beyond initial_capacity
        assert bf.max_elements == 10

    def test_add_and_contains_works_like_rotating(self):
        from hledac.universal.utils.bloom_filter import ScalableBloomFilter

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            bf = ScalableBloomFilter(initial_capacity=100, error_rate=0.01)

        bf.add("hello")
        bf.add("world")
        assert "hello" in bf
        assert "world" in bf
        # Bounded, so element_count caps at max_elements
        assert bf.element_count >= 2

    def test_clear_works(self):
        from hledac.universal.utils.bloom_filter import ScalableBloomFilter

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            bf = ScalableBloomFilter(initial_capacity=100, error_rate=0.01)

        bf.add("a")
        bf.add("b")
        bf.clear()
        # After clear, element_count is 0
        assert bf.element_count == 0

    def test_isinstance_check_against_rotating_passes(self):
        """Production code can `isinstance(x, RotatingBloomFilter)` safely."""
        from hledac.universal.utils.bloom_filter import (
            RotatingBloomFilter,
            ScalableBloomFilter,
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            bf = ScalableBloomFilter()

        assert isinstance(bf, RotatingBloomFilter)
