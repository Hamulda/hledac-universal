"""# noqa: N999
Sprint Phase Gate Manifest
==========================

ARCHITECTURE NOTE (O-03):
    This file is kept for documentation only. The old static list of test_sprint*.py
    files has been REPLACED by dynamic auto-discovery via pytest_collection_modifyitems
    in conftest.py.

    At collection time, conftest.py scans tests/ for actual test_sprint*.py files
    on disk and auto-tags any test items from those files with the `phase_gate`
    marker. This means the phase_gate is always in sync with reality — no more
    stale snapshots.

Usage:
    # Probe gate (fastest, always safe)
    pytest tests/ -m probe_gate -q

    # AO Canary (fast canary, no production risk)
    pytest tests/test_ao_canary.py -q

    # Phase gate (per-sprint focused) — DYNAMIC, auto-discovered
    pytest tests/ -m phase_gate -q

    # Full sprint suite
    pytest tests/test_sprint*.py -q

    # Manual only (heavy/integration - never as default)
    pytest tests/ -m manual_only -q

    # Tools smoke tests (O-04)
    pytest tests/test_tools_smoke.py -q
    pytest tests/test_tools_smoke.py -k tools -q    # Python tools only
    pytest tests/test_tools_smoke.py -k scripts -q   # Shell scripts only
    pytest tests/ -m tools_smoke -q

Markers:
    probe_gate   — Instant smoke tests, no imports
    ao_canary   — Fast lifecycle checks (~5-10s, fully mocked)
    phase_gate  — Sprint tests, auto-discovered from test_sprint*.py files
    manual_only — Heavy tests requiring special conditions
    tools_smoke — O-04 tools and scripts smoke tests
    tool_smoke  — Python module import smoke tests
    script_smoke — Shell script validation tests

How phase_gate works (O-03):
    conftest.py defines _discover_sprint_tests() which scans tests/ directory
    at collection time for files matching test_sprint*.py. It then uses
    pytest_collection_modifyitems hook to auto-tag items from those files
    with pytest.mark.phase_gate. No static list needed.
"""

# Marker registration is now in conftest.py pytest_configure()
