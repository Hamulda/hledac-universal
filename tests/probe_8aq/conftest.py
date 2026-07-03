"""
conftest for probe_8aq — Bootstrap Pattern Registry tests.

ENV BLOCKER: pattern_matcher requires pyahocorasick. Use importorskip to
ensure collection succeeds when the C extension is not installed.
"""
import pytest

# ENV BLOCKER: skip entire conftest if ahocorasick not available
pytest.importorskip("ahocorasick", reason="ENV BLOCKER: pyahocorasick not installed")

from hledac.universal.utils.patterns.pattern_matcher import reset_pattern_matcher


@pytest.fixture(autouse=True)
def _reset_pattern_matcher():
    """Reset PatternMatcher state before each test."""
    reset_pattern_matcher()
    yield
    reset_pattern_matcher()
