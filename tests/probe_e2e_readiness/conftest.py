"""pytest fixtures for probe_e2e_readiness tests."""
import json
import pathlib
import pytest

REPORT_GLOB = pathlib.Path.home() / ".hledac/reports/*.json"


@pytest.fixture
def report_artifact():
    """
    Return the most recent report JSON artifact as a parsed dict.
    Falls back to the canonical E2E baseline artifact if no recent report exists.
    """
    reports_dir = pathlib.Path.home() / ".hledac/reports"
    reports = sorted(reports_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for report_path in reports:
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "synthesis_engine_used" in data:
                return data
        except Exception:
            continue

    # Fallback: canonical baseline artifact
    baseline = pathlib.Path.home() / "PycharmProjects/Hledac/hledac/universal/probe_e2e_readiness/e2e_run_result.json"
    if baseline.exists():
        # Parse the report_path from the baseline's report_paths_found
        baseline_data = json.loads(baseline.read_text(encoding="utf-8"))
        report_paths = baseline_data.get("report_paths_found", [])
        for rp in report_paths:
            p = pathlib.Path(rp)
            if p.exists():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass

    pytest.skip("No report artifact available for testing")