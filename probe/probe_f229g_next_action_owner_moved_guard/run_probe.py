#!/usr/bin/env python3
"""Standalone probe runner for F229G — avoids pytest.ini probe_* ignore."""
from __future__ import annotations

import sys
import textwrap
import ast
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.codehealth_guard import (
    GuardVerdict,
    _scan_imports_for_symbol,
    run_guard,
)


def _parse(source: str) -> ast.Module:
    return ast.parse(textwrap.dedent(source))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GOOD_FIXTURE = '''
from dataclasses import dataclass

@dataclass
class NextActionInput:
    status: str
    is_memory_gate_abort: bool
    nonfeed_accepted_findings: int

def _rule_starvation(x): pass
def _rule_dominance(x): pass
def _rule_public_rejection(x): pass
def _rule_callback_wiring(x): pass

def _derive_next_action(input_data: NextActionInput) -> tuple[str, str | None]:
    return ("continue", None)
'''

TOO_MANY_ARGS_BAD_FIXTURE = '''
from dataclasses import dataclass

@dataclass
class NextActionInput:
    status: str

def _rule_starvation(x): pass
def _rule_dominance(x): pass
def _rule_public_rejection(x): pass
def _rule_callback_wiring(x): pass

def _derive_next_action(
    a1, a2, a3, a4, a5, a6, a7, a8, a9, a10,
    a11, a12, a13, a14, a15, a16, a17, a18, a19, a20,
    a21, a22, a23, a24, a25, a26, a27, a28, a29, a30,
    a31, a32, a33, a34, a35,
):
    if a1 > 0:
        return ("a", None)
    return ("b", None)
'''

OWNER_IMPORTED_FIXTURE = '''
from benchmarks.live_measurement_next_action import (
    NextActionInput,
    _derive_next_action,
)

def other_helper():
    pass
'''

SYMBOL_MISSING_FIXTURE = '''
from dataclasses import dataclass

def some_other_function():
    pass
'''


def test_scan_imports_for_symbol():
    source = "from benchmarks.live_measurement_next_action import _derive_next_action"
    owner, symbol = _scan_imports_for_symbol(source, "_derive_next_action")
    assert owner == "benchmarks.live_measurement_next_action", f"expected owner, got {owner}"
    assert symbol == "_derive_next_action", f"expected symbol, got {symbol}"
    print("  PASS: _scan_imports_for_symbol finds imported symbol")

    source = textwrap.dedent("""
    from benchmarks.live_measurement_next_action import (
        NextActionInput,
        _derive_next_action,
    )
    """)
    owner, symbol = _scan_imports_for_symbol(source, "_derive_next_action")
    assert owner == "benchmarks.live_measurement_next_action"
    assert symbol == "_derive_next_action"
    print("  PASS: _scan_imports_for_symbol handles multiline import")

    source = "from benchmarks.live_measurement_next_action import NextActionInput"
    owner, symbol = _scan_imports_for_symbol(source, "_derive_next_action")
    assert owner is None
    print("  PASS: _scan_imports_for_symbol returns None when not imported")

    source = "from some.other.module import _derive_next_action"
    owner, symbol = _scan_imports_for_symbol(source, "_derive_next_action")
    assert owner is None
    print("  PASS: _scan_imports_for_symbol returns None for wrong module")


def test_run_guard_owner_imported():
    tmp = Path("/tmp/probe_f229g_test")
    tmp.mkdir(exist_ok=True)
    src = tmp / "subject.py"
    src.write_text(OWNER_IMPORTED_FIXTURE)
    result = run_guard(str(src), "_derive_next_action")
    assert result.verdict == GuardVerdict.PASS_OWNER_IMPORTED, (
        f"expected PASS_OWNER_IMPORTED, got {result.verdict.value}: {result.error_message}"
    )
    assert result.owner_imported_detected is True
    assert result.owner_module == "benchmarks.live_measurement_next_action"
    assert result.imported_symbol == "_derive_next_action"
    assert result.compatibility_wrapper_detected is False
    assert result.owner_delegated_detected is False
    print("  PASS: run_guard returns PASS_OWNER_IMPORTED for import-only symbol")


def test_run_guard_symbol_missing():
    tmp = Path("/tmp/probe_f229g_test")
    tmp.mkdir(exist_ok=True)
    src = tmp / "subject.py"
    src.write_text(SYMBOL_MISSING_FIXTURE)
    result = run_guard(str(src), "_derive_next_action")
    assert result.verdict == GuardVerdict.FAIL_SYMBOL_MISSING, (
        f"expected FAIL_SYMBOL_MISSING, got {result.verdict.value}"
    )
    print("  PASS: run_guard returns FAIL_SYMBOL_MISSING for truly missing symbol")


def test_run_guard_old_bad_fixture():
    tmp = Path("/tmp/probe_f229g_test")
    tmp.mkdir(exist_ok=True)
    src = tmp / "subject.py"
    src.write_text(TOO_MANY_ARGS_BAD_FIXTURE)
    result = run_guard(str(src), "_derive_next_action")
    assert result.verdict == GuardVerdict.FAIL_TOO_MANY_ARGS, (
        f"expected FAIL_TOO_MANY_ARGS, got {result.verdict.value}"
    )
    print("  PASS: old bad fixture still fails FAIL_TOO_MANY_ARGS")


def test_live_sprint_measurement_owner_imported():
    result = run_guard(
        "benchmarks/live_sprint_measurement.py",
        "_derive_next_action",
    )
    assert result.verdict == GuardVerdict.PASS_OWNER_IMPORTED, (
        f"live_sprint_measurement._derive_next_action is imported from "
        f"benchmarks.live_measurement_next_action.py — got {result.verdict.value}: "
        f"{result.error_message}"
    )
    assert result.owner_imported_detected is True
    assert result.owner_module == "benchmarks.live_measurement_next_action"
    assert result.imported_symbol == "_derive_next_action"
    print("  PASS: live_sprint_measurement._derive_next_action => PASS_OWNER_IMPORTED")


def test_live_measurement_next_action_passes():
    result = run_guard(
        "benchmarks/live_measurement_next_action.py",
        "_derive_next_action",
    )
    assert result.verdict in (GuardVerdict.PASS, GuardVerdict.PASS_COMPAT_WRAPPER), (
        f"live_measurement_next_action._derive_next_action should pass, "
        f"got {result.verdict.value}: {result.error_message}"
    )
    print("  PASS: live_measurement_next_action._derive_next_action => PASS/COMPAT_WRAPPER")


def test_no_live_execution():
    tmp = Path("/tmp/probe_f229g_test")
    tmp.mkdir(exist_ok=True)
    src = tmp / "subject.py"
    src.write_text(GOOD_FIXTURE)
    executed = False

    import builtins
    original_print = builtins.print

    def tracking_print(*args, **kwargs):
        nonlocal executed
        executed = True
        return original_print(*args, **kwargs)

    builtins.print = tracking_print
    try:
        run_guard(str(src), "_derive_next_action")
    finally:
        builtins.print = original_print
    assert not executed, "run_guard executed the target function!"
    print("  PASS: run_guard does not execute target function")


def main():
    print("\n=== F229G Probe Tests ===\n")

    tests = [
        ("_scan_imports_for_symbol", test_scan_imports_for_symbol),
        ("run_guard PASS_OWNER_IMPORTED", test_run_guard_owner_imported),
        ("run_guard FAIL_SYMBOL_MISSING", test_run_guard_symbol_missing),
        ("run_guard old bad fixture", test_run_guard_old_bad_fixture),
        ("live_sprint_measurement PASS_OWNER_IMPORTED", test_live_sprint_measurement_owner_imported),
        ("live_measurement_next_action PASS", test_live_measurement_next_action_passes),
        ("no live execution", test_no_live_execution),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL [{name}]: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001 — probe: assertion errors expected in test context
            print(f"  ERROR [{name}]: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
