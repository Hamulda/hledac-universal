"""
Sprint 8AL conftest: PatternMatcher singleton hygiene + reload isolation.

8AL tests PatternMatcher via live_feed_pipeline.
autouse reset ensures clean state regardless of prior suite pollution.

NOTE: TestDefusedxmlPrimary in 8AH calls importlib.reload() which creates
a new MergedFeedSource class object. This pollutes the module-level
class identity. This is a known limit — 8AH must be fixed separately.
"""

import pytest

from hledac.universal.utils.patterns.pattern_matcher import (
    reset_pattern_matcher,
)


@pytest.fixture(autouse=True)
def _reset_pattern_matcher():
    """Reset PatternMatcher singleton before each test."""
    reset_pattern_matcher()
    yield
    reset_pattern_matcher()
