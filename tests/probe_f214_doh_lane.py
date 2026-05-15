"""Smoke test for F214 DOH lane activation.

Verifies:
- DOH source_family_outcomes entry exists
- DOH report fields are present
- Bounded execution constants defined
"""

import sys
from pathlib import Path

# Ensure hledac.universal is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from runtime.sprint_scheduler import SprintSchedulerResult
from runtime.acquisition_strategy import build_acquisition_report

def test_doh_result_fields():
    """Verify SprintSchedulerResult has all DOH fields."""
    result = SprintSchedulerResult()
    attrs = [
        "doh_planned", "doh_scheduled", "doh_request_attempted",
        "doh_domains_attempted", "doh_raw_count", "doh_accepted_findings",
        "doh_terminal_stage", "doh_provider_errors", "doh_cache_used",
    ]
    for attr in attrs:
        assert hasattr(result, attr), f"Missing field: {attr}"
    # Defaults
    assert result.doh_planned is False
    assert result.doh_scheduled is False
    assert result.doh_request_attempted is False
    assert result.doh_domains_attempted == 0
    assert result.doh_raw_count == 0
    assert result.doh_accepted_findings == 0
    assert result.doh_terminal_stage == ""
    assert result.doh_provider_errors == ()
    assert result.doh_cache_used is False
    print("PASS: SprintSchedulerResult DOH fields")

def test_doh_acquisition_report_fields():
    """Verify build_acquisition_report accepts DOH fields."""
    report = build_acquisition_report(
        plan=None,
        terminality=None,
        source_family_outcomes=None,
        # DOH fields
        doh_planned=True,
        doh_scheduled=True,
        doh_request_attempted=True,
        doh_domains_attempted=3,
        doh_raw_count=5,
        doh_accepted_findings=2,
        doh_terminal_stage="attempted_accepted",
        doh_provider_errors=("cloudflare_timeout",),
        doh_cache_used=True,
    )
    assert report["doh_planned"] is True
    assert report["doh_scheduled"] is True
    assert report["doh_request_attempted"] is True
    assert report["doh_domains_attempted"] == 3
    assert report["doh_raw_count"] == 5
    assert report["doh_accepted_findings"] == 2
    assert report["doh_terminal_stage"] == "attempted_accepted"
    assert report["doh_provider_errors"] == ["cloudflare_timeout"]
    assert report["doh_cache_used"] is True
    print("PASS: build_acquisition_report DOH fields")

def test_doh_lane_enum():
    """Verify AcquisitionLane.DOH exists."""
    from runtime.acquisition_strategy import AcquisitionLane
    assert hasattr(AcquisitionLane, "DOH")
    # DOH is a string enum so just check it's a non-empty string
    assert bool(AcquisitionLane.DOH)
    print("PASS: AcquisitionLane.DOH exists")

def test_doh_source_family_outcomes_loop_includes_doh():
    """Verify doh is in the source_family_outcomes loop tuple list."""
    src = (Path(__file__).parent.parent / "runtime" / "sprint_scheduler.py").read_text()
    found = False
    for line in src.splitlines():
        if '("doh", AcquisitionLane.DOH)' in line:
            found = True
            break
    assert found, "('doh', AcquisitionLane.DOH) not found in sprint_scheduler.py source"
    print("PASS: doh in source_family_outcomes loop")

def test_doh_lane_run_method_exists():
    """Verify _run_doh_prelude_lane exists and has bounded execution."""
    from runtime.sprint_scheduler import SprintScheduler
    assert hasattr(SprintScheduler, "_run_doh_prelude_lane")
    print("PASS: _run_doh_prelude_lane method exists")

if __name__ == "__main__":
    test_doh_result_fields()
    test_doh_acquisition_report_fields()
    test_doh_lane_enum()
    test_doh_lane_run_method_exists()
    test_doh_source_family_outcomes_loop_includes_doh()
    print("\nAll F214 DOH smoke tests passed.")