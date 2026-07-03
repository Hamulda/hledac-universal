"""Conftest for Sprint 8BO tests."""

import pytest

from hledac.universal.utils.patterns.pattern_matcher import (
    reset_pattern_matcher,
)


@pytest.fixture(autouse=True)
def clean_matcher():
    """Reset pattern matcher before each test."""
    reset_pattern_matcher()
    yield
    reset_pattern_matcher()
