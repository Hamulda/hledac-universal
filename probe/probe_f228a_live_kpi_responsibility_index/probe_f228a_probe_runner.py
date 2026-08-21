#!/usr/bin/env python3
"""Stand-alone probe runner for F228A — bypasses hledac package imports.

Usage:
    python3 probe_f228a_probe_runner.py
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

TOOLS_INDEX = Path(
    "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/tools/live_kpi_responsibility_index.py"
)
SOURCE_FILE = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/benchmarks/live_sprint_measurement.py")
PROBE_DIR = Path(
    "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/probe_f228a_live_kpi_responsibility_index"
)
JSON_INDEX = PROBE_DIR / "live_kpi_responsibility_index.json"


def load_index() -> dict:
    return json.loads(JSON_INDEX.read_text())


def probe_detections(index: dict) -> list[str]:
    errors = []
    checks = [
        ("_derive_live_kpi in function_specs", lambda i: "_derive_live_kpi" in i["function_specs"]),
        ("_derive_next_action in function_specs", lambda i: "_derive_next_action" in i["function_specs"]),
        ("NextActionInput in function_specs", lambda i: "NextActionInput" in i["function_specs"]),
        ("total_functions == 24", lambda i: i["total_functions"] == 24),
        (
            "extraction_order correct",
            lambda i: (
                i["extraction_order"]
                == [
                    "benchmarks/live_measurement_quality.py",
                    "benchmarks/live_measurement_terminality.py",
                    "benchmarks/live_measurement_next_action.py",
                    "benchmarks/live_measurement_kpi.py",
                ]
            ),
        ),
    ]
    for desc, fn in checks:
        try:
            if not fn(index):
                errors.append(f"FAIL: {desc}")
        except Exception as e:  # noqa: BLE001 — probe context: index validation errors expected
            errors.append(f"ERROR: {desc}: {e}")
    return errors


def probe_no_runtime_imports() -> list[str]:
    errors = []
    content = TOOLS_INDEX.read_text()
    # Check for import statements, not string references (file path appears in docstrings/comments)
    lines = content.splitlines()
    import_violations = []
    runtime_imports = [
        "from benchmarks.live_sprint_measurement",
        "import benchmarks.live_sprint_measurement",
        "from .benchmarks",
        "import benchmarks",
    ]
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
            continue  # skip comments and string literals
        for imp in runtime_imports:
            if imp in line:
                import_violations.append(f"  {imp} found in: {line.strip()}")
    if import_violations:
        for v in import_violations:
            errors.append(f"FAIL: runtime import{v}")
    # AST-level: no runtime functions called
    tree = ast.parse(content)
    call_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            call_names.add(node.func.id)
    runtime_funcs = {
        "_derive_live_kpi",
        "_stamp_live_kpi",
        "_derive_next_action",
        "_derive_run_quality_verdict",
        "_stamp_run_quality_verdict",
        "_is_active_domain_query",
        "_has_terminal_source_outcomes",
        "_has_scheduler_exit_path",
        "_was_family_attempted",
        "NextActionInput",
    }
    illegal = call_names & runtime_funcs
    if illegal:
        errors.append(f"FAIL: runtime functions called in indexer: {illegal}")
    return errors


def probe_no_live_execution() -> list[str]:
    errors = []
    if not JSON_INDEX.exists():
        errors.append("FAIL: live_kpi_responsibility_index.json missing")
        return errors
    try:
        data = json.loads(JSON_INDEX.read_text())
    except Exception as e:  # noqa: BLE001 — probe context: malformed JSON is a test failure
        errors.append(f"FAIL: JSON invalid: {e}")
        return errors
    if "function_specs" not in data:
        errors.append("FAIL: function_specs missing from index")
    if "extraction_order" not in data:
        errors.append("FAIL: extraction_order missing from index")
    if "live_sprint_measurement.py" not in data.get("source_file", ""):
        errors.append("FAIL: source_file doesn't reference live_sprint_measurement.py")
    return errors


def probe_risk_classification(index: dict) -> list[str]:
    errors = []
    high = index.get("high_risk", [])
    medium = index.get("medium_risk", [])
    low = index.get("low_risk", [])
    if "_derive_live_kpi" not in high:
        errors.append("FAIL: _derive_live_kpi not in high_risk")
    if "_stamp_live_kpi" not in high:
        errors.append("FAIL: _stamp_live_kpi not in high_risk")
    if len(high) != 2:
        errors.append(f"FAIL: high_risk has {len(high)} items, expected 2")
    if "_derive_next_action" not in medium:
        errors.append("FAIL: _derive_next_action not in medium_risk")
    if len(medium) != 9:
        errors.append(f"FAIL: medium_risk has {len(medium)} items, expected 9")
    if len(low) != 13:
        errors.append(f"FAIL: low_risk has {len(low)} items, expected 13")
    spec = index["function_specs"].get("_derive_live_kpi", {})
    if spec.get("extraction_risk") != "HIGH":
        errors.append(f"FAIL: _derive_live_kpi risk is {spec.get('extraction_risk')}, expected HIGH")
    return errors


def probe_module_assignment(index: dict) -> list[str]:
    errors = []
    term_module = "benchmarks/live_measurement_terminality.py"
    quality_module = "benchmarks/live_measurement_quality.py"
    next_action_module = "benchmarks/live_measurement_next_action.py"
    kpi_module = "benchmarks/live_measurement_kpi.py"
    checks = [
        ("_uma_state_is_critical_or_emergency → terminality", ["_uma_state_is_critical_or_emergency"], term_module),
        ("_is_active_domain_query → terminality", ["_is_active_domain_query"], term_module),
        ("_has_terminal_source_outcomes → terminality", ["_has_terminal_source_outcomes"], term_module),
        ("_has_scheduler_exit_path → terminality", ["_has_scheduler_exit_path"], term_module),
        ("_was_family_attempted → terminality", ["_was_family_attempted"], term_module),
        ("_derive_run_quality_verdict → quality", ["_derive_run_quality_verdict"], quality_module),
        ("_stamp_run_quality_verdict → quality", ["_stamp_run_quality_verdict"], quality_module),
        ("_derive_next_action → next_action", ["_derive_next_action"], next_action_module),
        ("NextActionInput → next_action", ["NextActionInput"], next_action_module),
        ("_derive_live_kpi → kpi", ["_derive_live_kpi"], kpi_module),
        ("_stamp_live_kpi → kpi", ["_stamp_live_kpi"], kpi_module),
    ]
    specs = index.get("function_specs", {})
    for _desc, names, expected_mod in checks:
        for name in names:
            actual_mod = specs.get(name, {}).get("suggested_target_module", "")
            if actual_mod != expected_mod:
                errors.append(f"FAIL: {name} → {actual_mod}, expected {expected_mod}")
    return errors


def probe_called_helpers(index: dict) -> list[str]:
    errors = []
    specs = index.get("function_specs", {})
    checks = [
        ("_derive_live_kpi calls _derive_next_action", "_derive_live_kpi", "_derive_next_action"),
        ("_derive_live_kpi calls _is_active_domain_query", "_derive_live_kpi", "_is_active_domain_query"),
        ("_derive_live_kpi calls _has_terminal_source_outcomes", "_derive_live_kpi", "_has_terminal_source_outcomes"),
        ("_derive_live_kpi calls _has_scheduler_exit_path", "_derive_live_kpi", "_has_scheduler_exit_path"),
        ("_derive_next_action calls NextActionInput", "_derive_next_action", "NextActionInput"),
        (
            "_derive_run_quality_verdict calls _uma_state_is_critical_or_emergency",
            "_derive_run_quality_verdict",
            "_uma_state_is_critical_or_emergency",
        ),
        (
            "_derive_run_quality_verdict calls _is_active_domain_query",
            "_derive_run_quality_verdict",
            "_is_active_domain_query",
        ),
    ]
    for desc, caller, callee in checks:
        helpers = specs.get(caller, {}).get("called_helpers", [])
        if callee not in helpers:
            errors.append(f"FAIL: {caller} does not call {callee} — {desc}")
    return errors


def main() -> int:
    print("=" * 60)
    print("F228A — live_kpi_responsibility_index probe")
    print("=" * 60)

    index = load_index()
    all_errors: list[tuple[str, list[str]]] = []

    probes = [
        ("Detections", probe_detections),
        ("NoRuntimeImport", probe_no_runtime_imports),
        ("NoLiveExecution", probe_no_live_execution),
        ("RiskClassification", probe_risk_classification),
        ("ModuleAssignment", probe_module_assignment),
        ("CalledHelpers", probe_called_helpers),
    ]

    passed = 0
    failed = 0

    for name, fn in probes:
        if name in ("NoRuntimeImport", "NoLiveExecution"):
            errors = fn()
        else:
            errors = fn(index)
        if errors:
            all_errors.append((name, errors))
            failed += len(errors)
            print(f"\nFAIL: {name} ({len(errors)} error(s))")
            for e in errors:
                print(f"  {e}")
        else:
            print(f"OK   {name}")
            passed += 1

    print(f"\n{'=' * 60}")
    print(f"Result: {passed} passed, {failed} failed")
    print(f"Total functions: {index['total_functions']}")
    print(f"HIGH risk: {index['high_risk']}")
    print(f"MEDIUM risk: {index['medium_risk']}")
    print(f"LOW risk: {index['low_risk']}")

    # Script sanity
    print("\nScript sanity:")
    result = subprocess.run(
        [sys.executable, str(TOOLS_INDEX)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd="/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal",
    )
    if result.returncode == 0:
        print("OK   script runs without error")
    else:
        print(f"FAIL script error: {result.stderr[:200]}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
