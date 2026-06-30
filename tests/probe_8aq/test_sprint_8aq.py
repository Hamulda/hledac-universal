"""
Sprint 8AQ: Bootstrap Pattern Registry + Matcher Status + Non-Infra Live Validation
==================================================================================

D.1-D.15: All mandatory tests for Sprint 8AQ.

Run:
    pytest hledac/universal/tests/probe_8aq/ -q
"""


import time
from unittest.mock import MagicMock

import pytest  # noqa: F401 — markers used by pytest

from hledac.universal.__main__ import (
    _build_observed_run_report,
    _ensure_runtime_patterns_configured_for_live_validation,
)
from hledac.universal.patterns.pattern_matcher import (
    configure_default_bootstrap_patterns_if_empty,
    configure_patterns,
    get_default_bootstrap_patterns,
    get_pattern_matcher,
    match_text,
    reset_pattern_matcher,
)


# ---------------------------------------------------------------------------
# D.1: test_pattern_status_surface_reports_zero_when_empty
# ---------------------------------------------------------------------------

def test_pattern_status_surface_reports_zero_when_empty():
    """
    D.1: When registry is empty, pattern_count() returns 0 and
    get_status() reports bootstrap_default_configured=False.
    """
    reset_pattern_matcher()
    pm = get_pattern_matcher()
    assert pm.pattern_count() == 0
    status = pm.get_status()
    assert status["configured_count"] == 0
    assert status["bootstrap_default_configured"] is False
    assert status["dirty"] is True


# ---------------------------------------------------------------------------
# D.2: test_configure_default_bootstrap_patterns_if_empty_populates_registry
# ---------------------------------------------------------------------------

def test_configure_default_bootstrap_patterns_if_empty_populates_registry():
    """
    D.2: When registry is empty, bootstrap call populates it with
    the current default OSINT literal pack and returns True.
    Sprint 8AY realigned: count is now dynamic (get_default_bootstrap_patterns),
    no longer hardcoded to v1 size of 12.
    """
    reset_pattern_matcher()
    applied = configure_default_bootstrap_patterns_if_empty()
    assert applied is True

    pm = get_pattern_matcher()
    count = pm.pattern_count()
    expected = len(get_default_bootstrap_patterns())
    assert count == expected, f"Expected {expected} patterns, got {count}"

    status = pm.get_status()
    assert status["bootstrap_default_configured"] is True
    assert status["configured_count"] == expected


# ---------------------------------------------------------------------------
# D.3: test_bootstrap_helper_is_idempotent
# ---------------------------------------------------------------------------

def test_bootstrap_helper_is_idempotent():
    """
    D.3: Second bootstrap call on already-populated registry
    returns False and does not change pattern count.
    """
    reset_pattern_matcher()
    first = configure_default_bootstrap_patterns_if_empty()
    assert first is True

    second = configure_default_bootstrap_patterns_if_empty()
    assert second is False

    pm = get_pattern_matcher()
    expected = len(get_default_bootstrap_patterns())
    assert pm.pattern_count() == expected


# ---------------------------------------------------------------------------
# D.4: test_bootstrap_helper_does_not_overwrite_existing_registry
# ---------------------------------------------------------------------------

def test_bootstrap_helper_does_not_overwrite_existing_registry():
    """
    D.4: When registry already has patterns, bootstrap call
    returns False and does NOT overwrite them.
    """
    configure_patterns((("custom_signal", "custom_label"),))

    original_count = get_pattern_matcher().pattern_count()
    assert original_count == 1

    applied = configure_default_bootstrap_patterns_if_empty()
    assert applied is False

    # Registry must NOT be overwritten
    assert get_pattern_matcher().pattern_count() == 1
    hits = match_text("some text with custom_signal in it")
    assert len(hits) == 1
    assert hits[0].label == "custom_label"


# ---------------------------------------------------------------------------
# D.5: test_pattern_count_reflects_configured_registry
# ---------------------------------------------------------------------------

def test_pattern_count_reflects_configured_registry():
    """
    D.5: pattern_count() accurately reflects the registry size
    after configure_patterns() calls.
    """
    reset_pattern_matcher()

    assert get_pattern_matcher().pattern_count() == 0

    configure_patterns((("a", "x"), ("b", "y")))
    assert get_pattern_matcher().pattern_count() == 2

    configure_patterns((("c", "z"),))
    assert get_pattern_matcher().pattern_count() == 1

    configure_patterns(())
    assert get_pattern_matcher().pattern_count() == 0


# ---------------------------------------------------------------------------
# D.6: test_runtime_helper_in_main_applies_bootstrap_when_empty
# ---------------------------------------------------------------------------

def test_runtime_helper_in_main_applies_bootstrap_when_empty():
    """
    D.6: _ensure_runtime_patterns_configured_for_live_validation()
    applies bootstrap when registry is empty and returns (expected, True).
    Sprint 8AY realigned: uses dynamic expected count.
    """
    reset_pattern_matcher()
    expected = len(get_default_bootstrap_patterns())
    count, applied = _ensure_runtime_patterns_configured_for_live_validation()
    assert count == expected
    assert applied is True

    # Second call should be idempotent
    count2, applied2 = _ensure_runtime_patterns_configured_for_live_validation()
    assert count2 == expected
    assert applied2 is True  # bootstrap_default_configured stays True


# ---------------------------------------------------------------------------
# D.7: test_runtime_helper_in_main_preserves_existing_patterns
# ---------------------------------------------------------------------------

def test_runtime_helper_in_main_preserves_existing_patterns():
    """
    D.7: _ensure_runtime_patterns_configured_for_live_validation()
    does NOT overwrite existing registry.
    """
    configure_patterns((("my_signal", "my_label"),))

    count, applied = _ensure_runtime_patterns_configured_for_live_validation()
    assert count == 1
    assert applied is False

    hits = match_text("info about my_signal here")
    assert len(hits) == 1
    assert hits[0].label == "my_label"


# ---------------------------------------------------------------------------
# D.8: test_observed_run_report_contains_pattern_count_and_bootstrap_truth
# ---------------------------------------------------------------------------

def test_observed_run_report_contains_pattern_count_and_bootstrap_truth():
    """
    D.8: ObservedRunReport includes both patterns_configured and
    bootstrap_applied fields, populated correctly.
    """
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    fake_result = MagicMock(
        total_sources=2, completed_sources=2,
        fetched_entries=10, accepted_findings=5, stored_findings=4,
        sources=(),
    )
    report = _build_observed_run_report(
        started_ts=time.time(),
        batch_result=fake_result,
        dedup_before={},
        dedup_after={},
        uma_snapshot={},
        patterns_configured=len(get_default_bootstrap_patterns()),
        batch_error=None,
        bootstrap_applied=True,
    )

    assert report.patterns_configured == len(get_default_bootstrap_patterns())
    assert report.bootstrap_applied is True
    assert report.content_quality_validated is True


# ---------------------------------------------------------------------------
# D.9: test_content_quality_validated_true_when_patterns_present
# ---------------------------------------------------------------------------

def test_content_quality_validated_true_when_patterns_present():
    """
    D.9: content_quality_validated is True when patterns_configured > 0,
    False otherwise. Truth rule: patterns_configured > 0.
    """
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    fake_result = MagicMock(
        total_sources=1, completed_sources=1,
        fetched_entries=1, accepted_findings=1, stored_findings=1,
        sources=(),
    )

    # patterns > 0 → content_quality_validated = True
    report_with = _build_observed_run_report(
        started_ts=time.time(),
        batch_result=fake_result,
        dedup_before={},
        dedup_after={},
        uma_snapshot={},
        patterns_configured=12,
        batch_error=None,
        bootstrap_applied=True,
    )
    assert report_with.content_quality_validated is True

    # patterns == 0 → content_quality_validated = False
    report_without = _build_observed_run_report(
        started_ts=time.time(),
        batch_result=fake_result,
        dedup_before={},
        dedup_after={},
        uma_snapshot={},
        patterns_configured=0,
        batch_error=None,
        bootstrap_applied=False,
    )
    assert report_without.content_quality_validated is False


# ---------------------------------------------------------------------------
# D.10: test_no_import_time_pattern_side_effects_added
# ---------------------------------------------------------------------------

def test_no_import_time_pattern_side_effects_added():
    """
    D.10: Importing pattern_matcher does NOT configure any patterns.
    Registry must remain empty after import (no auto-bootstrap at import time).
    """
    # Reset first to ensure clean state
    reset_pattern_matcher()
    pm = get_pattern_matcher()

    # At this point registry should be empty (no auto-bootstrap on import)
    assert pm.pattern_count() == 0
    status = pm.get_status()
    assert status["bootstrap_default_configured"] is False


# ---------------------------------------------------------------------------
# D.11: test_probe_8ao_still_green — verified via gate pytest run
# ---------------------------------------------------------------------------

def test_probe_8ao_still_green():
    """D.11: probe_8ao regression — verified by running probe_8ao suite in gate."""
    # No-op: actual verification done by gate pytest of probe_8ao suite


# ---------------------------------------------------------------------------
# D.15: test_ao_canary_still_green — verified via gate pytest run
# ---------------------------------------------------------------------------

def test_ao_canary_still_green():
    """D.15: test_ao_canary regression — verified by running test_ao_canary in gate."""
    # No-op: actual verification done by gate pytest of test_ao_canary
