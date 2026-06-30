# hledac/universal/tests/probe_8bb/conftest.py
"""Test fixtures for Sprint 8BB markdown reporter tests."""

import msgspec
import pytest


def _dict():
    return {}


# ---------------------------------------------------------------------------
# Minimal fixture — zero findings, unknown root cause
# ---------------------------------------------------------------------------
class MinimalReport(msgspec.Struct, frozen=True, gc=False):
    started_ts: float = 1_234_567.0
    finished_ts: float = 1_234_568.0
    elapsed_ms: float = 1000.0
    total_sources: int = 0
    completed_sources: int = 0
    fetched_entries: int = 0
    accepted_findings: int = 0
    stored_findings: int = 0
    batch_error: str | None = None
    per_source: tuple[dict, ...] = ()
    patterns_configured: int = 0
    bootstrap_applied: bool = False
    content_quality_validated: bool = False
    dedup_before: dict = msgspec.field(default_factory=_dict)
    dedup_after: dict = msgspec.field(default_factory=_dict)
    dedup_delta: dict = msgspec.field(default_factory=_dict)
    dedup_surface_available: bool = False
    uma_snapshot: dict = msgspec.field(default_factory=_dict)
    slow_sources: tuple[dict, ...] = ()
    error_summary: dict = msgspec.field(default_factory=_dict)
    success_rate: float = 0.0
    failed_source_count: int = 0
    baseline_delta: dict = msgspec.field(default_factory=_dict)
    health_breakdown: dict = msgspec.field(default_factory=_dict)
    entries_seen: int = 0
    entries_with_empty_assembled_text: int = 0
    entries_with_text: int = 0
    entries_scanned: int = 0
    entries_with_hits: int = 0
    total_pattern_hits: int = 0
    findings_built_pre_store: int = 0
    avg_assembled_text_len: float = 0.0
    signal_stage: str = "unknown"
    accepted_count_delta: int = 0
    low_information_rejected_count_delta: int = 0
    in_memory_duplicate_rejected_count_delta: int = 0
    persistent_duplicate_rejected_count_delta: int = 0
    other_rejected_count_delta: int = 0
    diagnostic_root_cause: str = "unknown"
    is_network_variance: bool = False


# ---------------------------------------------------------------------------
# Report with accepted findings (accepted_present root cause)
# ---------------------------------------------------------------------------
class AcceptedPresentReport(msgspec.Struct, frozen=True, gc=False):
    started_ts: float = 1_234_567.0
    finished_ts: float = 1_234_570.0
    elapsed_ms: float = 3000.0
    total_sources: int = 3
    completed_sources: int = 3
    fetched_entries: int = 30
    accepted_findings: int = 5
    stored_findings: int = 4
    batch_error: str | None = None
    per_source: tuple[dict, ...] = (
        {
            "feed_url": "https://example.com/feed1",
            "label": "Example1",
            "origin": "curated",
            "priority": 1,
            "fetched_entries": 10,
            "accepted_findings": 2,
            "stored_findings": 2,
            "elapsed_ms": 500.0,
            "error": None,
        },
        {
            "feed_url": "https://example.com/feed2",
            "label": "Example2",
            "origin": "curated",
            "priority": 2,
            "fetched_entries": 20,
            "accepted_findings": 3,
            "stored_findings": 2,
            "elapsed_ms": 800.0,
            "error": None,
        },
    )
    patterns_configured: int = 10
    bootstrap_applied: bool = True
    content_quality_validated: bool = True
    dedup_before: dict = msgspec.field(default_factory=lambda: {"persistent_duplicate_count": 5})
    dedup_after: dict = msgspec.field(default_factory=lambda: {"persistent_duplicate_count": 12})
    dedup_delta: dict = msgspec.field(default_factory=lambda: {"persistent_duplicate_count": 7})
    dedup_surface_available: bool = True
    uma_snapshot: dict = msgspec.field(default_factory=lambda: {"system_used_gib": 4.2, "rss_gib": 3.8})
    slow_sources: tuple[dict, ...] = ()
    error_summary: dict = msgspec.field(default_factory=_dict)
    success_rate: float = 1.0
    failed_source_count: int = 0
    baseline_delta: dict = msgspec.field(default_factory=_dict)
    health_breakdown: dict = msgspec.field(
        default_factory=lambda: {
            "health_breakdown": {"SUCCESS": 3, "TIMEOUT_ERROR": 0, "NETWORK_ERROR": 0},
            "success_count": 3,
        }
    )
    entries_seen: int = 30
    entries_with_empty_assembled_text: int = 2
    entries_with_text: int = 28
    entries_scanned: int = 28
    entries_with_hits: int = 10
    total_pattern_hits: int = 25
    findings_built_pre_store: int = 8
    avg_assembled_text_len: float = 450.0
    signal_stage: str = "complete"
    accepted_count_delta: int = 5
    low_information_rejected_count_delta: int = 1
    in_memory_duplicate_rejected_count_delta: int = 1
    persistent_duplicate_rejected_count_delta: int = 1
    other_rejected_count_delta: int = 0
    diagnostic_root_cause: str = "accepted_present"
    is_network_variance: bool = False


# ---------------------------------------------------------------------------
# Duplicate-rejection-dominant report
# ---------------------------------------------------------------------------
class DuplicateRejectionReport(msgspec.Struct, frozen=True, gc=False):
    started_ts: float = 1_234_567.0
    finished_ts: float = 1_234_568.5
    elapsed_ms: float = 1500.0
    total_sources: int = 2
    completed_sources: int = 2
    fetched_entries: int = 20
    accepted_findings: int = 0
    stored_findings: int = 0
    batch_error: str | None = None
    per_source: tuple[dict, ...] = ()
    patterns_configured: int = 8
    bootstrap_applied: bool = False
    content_quality_validated: bool = True
    dedup_before: dict = msgspec.field(default_factory=_dict)
    dedup_after: dict = msgspec.field(default_factory=_dict)
    dedup_delta: dict = msgspec.field(default_factory=_dict)
    dedup_surface_available: bool = False
    uma_snapshot: dict = msgspec.field(default_factory=_dict)
    slow_sources: tuple[dict, ...] = ()
    error_summary: dict = msgspec.field(default_factory=_dict)
    success_rate: float = 1.0
    failed_source_count: int = 0
    baseline_delta: dict = msgspec.field(default_factory=_dict)
    health_breakdown: dict = msgspec.field(default_factory=_dict)
    entries_seen: int = 20
    entries_with_empty_assembled_text: int = 0
    entries_with_text: int = 20
    entries_scanned: int = 20
    entries_with_hits: int = 15
    total_pattern_hits: int = 30
    findings_built_pre_store: int = 5
    avg_assembled_text_len: float = 300.0
    signal_stage: str = "complete"
    accepted_count_delta: int = 0
    low_information_rejected_count_delta: int = 0
    in_memory_duplicate_rejected_count_delta: int = 2
    persistent_duplicate_rejected_count_delta: int = 3
    other_rejected_count_delta: int = 0
    diagnostic_root_cause: str = "duplicate_rejection_dominant"
    is_network_variance: bool = False


# ---------------------------------------------------------------------------
# Low-information-rejection-dominant report
# ---------------------------------------------------------------------------
class LowInfoRejectionReport(msgspec.Struct, frozen=True, gc=False):
    started_ts: float = 1_234_567.0
    finished_ts: float = 1_234_568.0
    elapsed_ms: float = 1000.0
    total_sources: int = 1
    completed_sources: int = 1
    fetched_entries: int = 5
    accepted_findings: int = 0
    stored_findings: int = 0
    batch_error: str | None = None
    per_source: tuple[dict, ...] = ()
    patterns_configured: int = 5
    bootstrap_applied: bool = True
    content_quality_validated: bool = True
    dedup_before: dict = msgspec.field(default_factory=_dict)
    dedup_after: dict = msgspec.field(default_factory=_dict)
    dedup_delta: dict = msgspec.field(default_factory=_dict)
    dedup_surface_available: bool = False
    uma_snapshot: dict = msgspec.field(default_factory=_dict)
    slow_sources: tuple[dict, ...] = ()
    error_summary: dict = msgspec.field(default_factory=_dict)
    success_rate: float = 1.0
    failed_source_count: int = 0
    baseline_delta: dict = msgspec.field(default_factory=_dict)
    health_breakdown: dict = msgspec.field(default_factory=_dict)
    entries_seen: int = 5
    entries_with_empty_assembled_text: int = 3
    entries_with_text: int = 2
    entries_scanned: int = 2
    entries_with_hits: int = 1
    total_pattern_hits: int = 2
    findings_built_pre_store: int = 1
    avg_assembled_text_len: float = 50.0
    signal_stage: str = "complete"
    accepted_count_delta: int = 0
    low_information_rejected_count_delta: int = 5
    in_memory_duplicate_rejected_count_delta: int = 0
    persistent_duplicate_rejected_count_delta: int = 0
    other_rejected_count_delta: int = 0
    diagnostic_root_cause: str = "low_information_rejection_dominant"
    is_network_variance: bool = False


# ---------------------------------------------------------------------------
# Network variance report
# ---------------------------------------------------------------------------
class NetworkVarianceReport(msgspec.Struct, frozen=True, gc=False):
    started_ts: float = 1_234_567.0
    finished_ts: float = 1_234_569.0
    elapsed_ms: float = 2000.0
    total_sources: int = 5
    completed_sources: int = 3
    fetched_entries: int = 15
    accepted_findings: int = 0
    stored_findings: int = 0
    batch_error: str | None = "Partial failure"
    per_source: tuple[dict, ...] = (
        {
            "feed_url": "https://slow.example/feed",
            "label": "Slow",
            "origin": "curated",
            "priority": 1,
            "fetched_entries": 5,
            "accepted_findings": 0,
            "stored_findings": 0,
            "elapsed_ms": 2000.0,
            "error": "Timeout",
        },
    )
    patterns_configured: int = 10
    bootstrap_applied: bool = False
    content_quality_validated: bool = False
    dedup_before: dict = msgspec.field(default_factory=_dict)
    dedup_after: dict = msgspec.field(default_factory=_dict)
    dedup_delta: dict = msgspec.field(default_factory=_dict)
    dedup_surface_available: bool = False
    uma_snapshot: dict = msgspec.field(default_factory=_dict)
    slow_sources: tuple[dict, ...] = ()
    error_summary: dict = msgspec.field(default_factory=_dict)
    success_rate: float = 0.6
    failed_source_count: int = 2
    baseline_delta: dict = msgspec.field(default_factory=_dict)
    health_breakdown: dict = msgspec.field(
        default_factory=lambda: {
            "health_breakdown": {"SUCCESS": 3, "TIMEOUT_ERROR": 2},
            "success_count": 3,
        }
    )
    entries_seen: int = 15
    entries_with_empty_assembled_text: int = 0
    entries_with_text: int = 15
    entries_scanned: int = 10
    entries_with_hits: int = 0
    total_pattern_hits: int = 0
    findings_built_pre_store: int = 0
    avg_assembled_text_len: float = 200.0
    signal_stage: str = "unknown"
    accepted_count_delta: int = 0
    low_information_rejected_count_delta: int = 0
    in_memory_duplicate_rejected_count_delta: int = 0
    persistent_duplicate_rejected_count_delta: int = 0
    other_rejected_count_delta: int = 0
    diagnostic_root_cause: str = "network_variance"
    is_network_variance: bool = True


@pytest.fixture
def minimal_report():
    return MinimalReport()


@pytest.fixture
def accepted_present_report():
    return AcceptedPresentReport()


@pytest.fixture
def duplicate_rejection_report():
    return DuplicateRejectionReport()


@pytest.fixture
def low_info_rejection_report():
    return LowInfoRejectionReport()


@pytest.fixture
def network_variance_report():
    return NetworkVarianceReport()
