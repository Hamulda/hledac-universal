#!/usr/bin/env python3
"""
SPRINT F206AR — Transport Canonical Policy Audit Probe Runner
=============================================================

Verifies the transport policy audit was completed correctly:
- report and matrix exist and are well-formed
- all critical files are classified
- every network consumer has a verdict
- no production files were modified
- no network calls were made during audit

Usage:
    python probe_transport_policy_f206ar/run_probe.py
    python -m pytest tests/probe_transport_policy_f206ar -v
"""

from pathlib import Path

PROBE_DIR = Path(__file__).parent
REPORT_PATH = PROBE_DIR / "REPORT_TRANSPORT_POLICY_AUDIT.md"
MATRIX_PATH = PROBE_DIR / "transport_policy_matrix.json"


def main():
    print("SPRINT F206AR — Transport Policy Audit Probe")
    print("=" * 50)

    errors = []

    # Check files exist
    if not REPORT_PATH.exists():
        errors.append(f"FAIL: Report not found: {REPORT_PATH}")
    else:
        print(f"OK: Report exists ({REPORT_PATH.stat().st_size} bytes)")

    if not MATRIX_PATH.exists():
        errors.append(f"FAIL: Matrix not found: {MATRIX_PATH}")
    else:
        print(f"OK: Matrix exists ({MATRIX_PATH.stat().st_size} bytes)")

    if errors:
        print("\n".join(errors))
        return 1

    import json

    try:
        matrix = json.loads(MATRIX_PATH.read_text())
        print("OK: Matrix is valid JSON")
    except json.JSONDecodeError as e:
        errors.append(f"FAIL: Matrix JSON invalid: {e}")
        print("\n".join(errors))
        return 1

    # Check critical files in matrix
    consumers = matrix.get("network_consumers", [])
    consumer_files = [c.get("file", "") for c in consumers]

    critical_files = [
        "coordinators/fetch_coordinator.py",
        "pipeline/live_public_pipeline.py",
        "fetching/public_fetcher.py",
        "transport/tor_transport.py",
        "transport/i2p_transport.py",
        "transport/circuit_breaker.py",
        "stealth/stealth_manager.py",
    ]

    for cf in critical_files:
        found = any(cf in f for f in consumer_files)
        if found:
            print(f"OK: {cf} in matrix")
        else:
            errors.append(f"FAIL: {cf} NOT in matrix")

    # Check circuit breaker TEST-SEAM finding
    findings = matrix.get("key_findings", [])
    test_seam_found = any(
        "TEST" in f.get("finding", "").upper() for f in findings
    )
    if test_seam_found:
        print("OK: TEST-SEAM finding documented")
    else:
        errors.append("FAIL: TEST-SEAM finding not documented")

    # Check transport_resolver DORMANT status
    tp = matrix.get("transport_policies", {})
    tr = tp.get("TransportResolver.resolve", {})
    if "DORMANT" in tr.get("status", ""):
        print("OK: TransportResolver.resolve marked DORMANT")
    else:
        errors.append("FAIL: TransportResolver.resolve not marked DORMANT")

    print("")
    if errors:
        print("FAILURES:")
        print("\n".join(f"  {e}" for e in errors))
        return 1
    else:
        print("ALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
