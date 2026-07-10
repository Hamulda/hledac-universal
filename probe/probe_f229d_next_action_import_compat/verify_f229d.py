#!/usr/bin/env python3
"""
F229D: NEXT ACTION IMPORT COMPATIBILITY — STANDALONE VERIFIER
Self-contained: AST + import checks, no pytest collection chain.
"""

import ast
import json
import pathlib
import sys

UNIVERSAL_ROOT = pathlib.Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
LSM_PATH = UNIVERSAL_ROOT / "benchmarks" / "live_sprint_measurement.py"
NAM_PATH = UNIVERSAL_ROOT / "benchmarks" / "live_measurement_next_action.py"
PROBE_ROOT = pathlib.Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/probe_f229d_next_action_import_compat")

results = []
passed = 0
failed = 0

def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    status = "PASS" if condition else "FAIL"
    results.append({"check": name, "status": status, "detail": detail})
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}: {detail}")

# ─── Source assertions ───────────────────────────────────────────────────────

def test_lsm_file_exists():
    check("lsm_file_exists", LSM_PATH.exists(), str(LSM_PATH))

def test_nam_file_exists():
    check("nam_file_exists", NAM_PATH.exists(), str(NAM_PATH))

def test_lsm_imports_derive_next_action():
    src = LSM_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "benchmarks.live_measurement_next_action":
            names = [a.name for a in node.names]
            check("lsm_imports_derive_next_action", "_derive_next_action" in names,
                  f"import names: {names}")
            return
    check("lsm_imports_derive_next_action", False, "import not found")

def test_lsm_imports_next_action_input():
    src = LSM_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "benchmarks.live_measurement_next_action":
            names = [a.name for a in node.names]
            check("lsm_imports_next_action_input", "NextActionInput" in names,
                  f"import names: {names}")
            return
    check("lsm_imports_next_action_input", False, "import not found")

def test_lsm_imports_was_family_attempted():
    src = LSM_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "benchmarks.live_measurement_next_action":
            names = [a.name for a in node.names]
            check("lsm_imports_was_family_attempted", "_was_family_attempted" in names,
                  f"import names: {names}")
            return
    check("lsm_imports_was_family_attempted", False, "import not found")

def test_lsm_no_local_next_action_input():
    src = LSM_PATH.read_text()
    tree = ast.parse(src)
    local = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "NextActionInput"]
    check("lsm_no_local_next_action_input", len(local) == 0,
          f"found: {local}" if local else "")

def test_lsm_no_local_rule_helpers():
    src = LSM_PATH.read_text()
    tree = ast.parse(src)
    local = [n.name for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name.startswith("_rule")]
    check("lsm_no_local_rule_helpers", len(local) == 0,
          f"found: {local}" if local else "")

def test_lsm_no_local_was_family_attempted():
    src = LSM_PATH.read_text()
    tree = ast.parse(src)
    local = [n.name for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "_was_family_attempted"]
    check("lsm_no_local_was_family_attempted", len(local) == 0,
          f"found: {local}" if local else "")

def test_lsm_no_local_derive_next_action():
    src = LSM_PATH.read_text()
    tree = ast.parse(src)
    local = [n.name for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "_derive_next_action"]
    check("lsm_no_local_derive_next_action", len(local) == 0,
          f"found: {local}" if local else "")

def test_nam_exports_expected_symbols():
    src = NAM_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    if isinstance(node.value, ast.List):
                        names = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
                        expected = {"NextActionInput", "_derive_next_action", "_was_family_attempted"}
                        check("nam_exports_expected_symbols",
                              expected.issubset(set(names)),
                              f"found: {names}")
                        return
    check("nam_exports_expected_symbols", False, "__all__ not found")

# ─── Import assertions (NAM only — LSM requires full hledac) ────────────────

def test_nam_import_succeeds():
    sys.path.insert(0, str(UNIVERSAL_ROOT))
    sys.path.insert(0, str(UNIVERSAL_ROOT / "benchmarks"))
    try:
        import benchmarks.live_measurement_next_action as m
        check("nam_import_succeeds", m is not None)
    except Exception as e:  # noqa: BLE001 — probe: import errors are expected test failures
        check("nam_import_succeeds", False, str(e))

def test_nam_derive_next_action_callable():
    sys.path.insert(0, str(UNIVERSAL_ROOT))
    sys.path.insert(0, str(UNIVERSAL_ROOT / "benchmarks"))
    try:
        import benchmarks.live_measurement_next_action as m
        check("nam_derive_next_action_callable", callable(m._derive_next_action))
    except Exception as e:  # noqa: BLE001 — probe: callable check errors expected in test context
        check("nam_derive_next_action_callable", False, str(e))

def test_nam_next_action_input_fields():
    sys.path.insert(0, str(UNIVERSAL_ROOT))
    sys.path.insert(0, str(UNIVERSAL_ROOT / "benchmarks"))
    try:
        import benchmarks.live_measurement_next_action as m
        fields = set(m.NextActionInput.__dataclass_fields__.keys())
        required = {"status", "is_memory_gate_abort", "nonfeed_accepted_findings",
                    "public_fetch_attempted", "public_findings", "feed_findings",
                    "total_findings", "ct_findings", "runtime_truth"}
        check("nam_next_action_input_fields",
              required.issubset(fields),
              f"missing: {required - fields}")
    except Exception as e:  # noqa: BLE001 — probe: field inspection errors expected in test context
        check("nam_next_action_input_fields", False, str(e))

def test_nam_rule_count():
    src = NAM_PATH.read_text()
    tree = ast.parse(src)
    rules = [n.name for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name.startswith("_rule")]
    check("nam_rule_count", len(rules) == 8,
          f"found {len(rules)}: {rules}")

def test_nam_was_family_attempted_callable():
    sys.path.insert(0, str(UNIVERSAL_ROOT))
    sys.path.insert(0, str(UNIVERSAL_ROOT / "benchmarks"))
    try:
        import benchmarks.live_measurement_next_action as m
        check("nam_was_family_attempted_callable", callable(m._was_family_attempted))
    except Exception as e:  # noqa: BLE001 — probe: callable check errors expected in test context
        check("nam_was_family_attempted_callable", False, str(e))

# ─── Behavior assertions (NAM only — LSM requires full hledac) ────────────────

def test_nam_minimal_input_produces_tuple():
    sys.path.insert(0, str(UNIVERSAL_ROOT))
    sys.path.insert(0, str(UNIVERSAL_ROOT / "benchmarks"))
    try:
        import benchmarks.live_measurement_next_action as m
        rt = {"branch_mix": {"public_findings": 0}, "public_branch_timed_out": False,
              "feed_branch_timed_out": False, "cycles_started": 0}
        result = m._derive_next_action(
            status=m.NextActionInput.__dataclass_fields__["status"].default,
            is_memory_gate_abort=False,
            nonfeed_accepted_findings=0, public_fetch_attempted=False,
            public_findings=0, feed_findings=0, total_findings=0, ct_findings=0,
            runtime_truth=rt, feed_dominance_score=None,
            top_public_reject_reason=None, nonfeed_starvation_suspected=False,
            prewindup_barrier_checked=False, prewindup_barrier_satisfied=False,
            prewindup_required_lanes=None, prewindup_attempted_lanes=None,
            acquisition_strategy=None, return_guard_observation=None,
            scheduler_exit=None, acquisition_terminality_checked=False,
            acquisition_terminality_satisfied=False,
            acquisition_terminality_missing_lanes=None, run_quality_verdict=None,
            acquisition_prelude_checked=False, acquisition_prelude_ran=False,
            acquisition_prelude_required_lanes=None,
            acquisition_prelude_terminal_lanes=None,
            acquisition_prelude_missing_lanes=None,
            acquisition_prelude_skipped_lanes=None,
            acquisition_prelude_errors=None, acquisition_prelude_duration_s=None,
            acquisition_prelude_reason=None, windup_guard_observation=None,
            scheduler_deadline_enforced=False, scheduler_deadline_checks=0,
        )
        check("nam_minimal_input_produces_tuple",
              isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], str),
              f"got: {result}")
    except Exception as e:  # noqa: BLE001 — probe: runtime errors expected in test context
        check("nam_minimal_input_produces_tuple", False, str(e))

def test_nam_was_family_attempted_behavior():
    sys.path.insert(0, str(UNIVERSAL_ROOT))
    sys.path.insert(0, str(UNIVERSAL_ROOT / "benchmarks"))
    try:
        import benchmarks.live_measurement_next_action as m
        # Case 1: findings > 0
        rt1 = {"branch_mix": {"public_findings": 5}, "public_branch_timed_out": False}
        r1 = m._was_family_attempted(rt1, "public")
        check("nam_was_family_attempted_findings>0", r1 is True,
              f"got: {r1}")
        # Case 2: timed out
        rt2 = {"branch_mix": {}, "public_branch_timed_out": True}
        r2 = m._was_family_attempted(rt2, "public")
        check("nam_was_family_attempted_timed_out", r2 is True,
              f"got: {r2}")
        # Case 3: never attempted
        rt3 = {"branch_mix": {}, "public_branch_timed_out": False}
        r3 = m._was_family_attempted(rt3, "public")
        check("nam_was_family_attempted_never", r3 is False,
              f"got: {r3}")
    except Exception as e:  # noqa: BLE001 — probe: behavior check errors expected in test context
        check("nam_was_family_attempted_behavior", False, str(e))

# ─── Run ─────────────────────────────────────────────────────────────────────

print("\nF229D: NEXT ACTION IMPORT COMPATIBILITY SEAL")
print("=" * 60)

print("\n[Source Assertions]")
test_lsm_file_exists()
test_nam_file_exists()
test_lsm_imports_derive_next_action()
test_lsm_imports_next_action_input()
test_lsm_imports_was_family_attempted()
test_lsm_no_local_next_action_input()
test_lsm_no_local_rule_helpers()
test_lsm_no_local_was_family_attempted()
test_lsm_no_local_derive_next_action()
test_nam_exports_expected_symbols()

print("\n[Import Assertions — NAM]")
test_nam_import_succeeds()
test_nam_derive_next_action_callable()
test_nam_next_action_input_fields()
test_nam_rule_count()
test_nam_was_family_attempted_callable()

print("\n[Behavior Assertions — NAM]")
test_nam_minimal_input_produces_tuple()
test_nam_was_family_attempted_behavior()

print("\n" + "=" * 60)
print(f"Results: {passed} passed, {failed} failed")

# Write JSON report
report_path = PROBE_ROOT / "next_action_import_compat.json"
PROBE_ROOT.mkdir(parents=True, exist_ok=True)
with open(report_path, "w") as f:
    json.dump({
        "probe": "F229D",
        "description": "NEXT ACTION IMPORT COMPATIBILITY SEAL",
        "total": passed + failed,
        "passed": passed,
        "failed": failed,
        "checks": results
    }, f, indent=2)
print(f"\nReport: {report_path}")

# Write markdown summary
md_path = PROBE_ROOT / "REPORT_NEXT_ACTION_IMPORT_COMPAT.md"
with open(md_path, "w") as f:
    f.write("# F229D: NEXT ACTION IMPORT COMPATIBILITY SEAL\n\n")
    f.write("**Result:** ")
    if failed == 0:
        f.write("ALL PASS\n\n")
    else:
        f.write(f"{failed} FAILURES\n\n")
    f.write(f"**Date:** 2026-05-10\n\n")
    f.write("## Checks\n\n")
    f.write("| Check | Status | Detail |\n")
    f.write("|-------|--------|--------|\n")
    for r in results:
        detail = r["detail"].replace("|", "/") if r["detail"] else ""
        f.write(f"| {r['check']} | {r['status']} | {detail[:80]} |\n")
print(f"Markdown: {md_path}")

sys.exit(0 if failed == 0 else 1)
