"""
Sprint 8AH conftest: optional pattern registry seeding.

NOT autouse — some tests verify entry-backed behavior (no patterns = zero findings).
Tests that need patterns for integration testing opt-in via explicit fixture.

The 8AH test suite has tests that verify BOTH:
- Entry-backed legacy behavior (no patterns -> zero findings per entry)
- Pattern-backed new behavior (patterns -> findings per match)

Use the _seed_pattern_registry fixture explicitly in tests that need pattern matching.
"""

import pytest

from hledac.universal.utils.patterns.pattern_matcher import (
    configure_patterns,
    reset_pattern_matcher,
)


@pytest.fixture
def _seed_pattern_registry():
    """
    Optional: seed the pattern registry with generic catch-all patterns.

    Use explicitly in integration tests that call async_run_live_feed_pipeline
    without mocking the pattern matcher.

    Pattern "e" (lowercase due to PatternMatcher case-insensitivity)
    matches entry titles E0..E99 and "Example.com" etc.
    """
    reset_pattern_matcher()
    configure_patterns((("e", "generic"),))
    yield
    reset_pattern_matcher()
