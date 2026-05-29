#!/usr/bin/env python3
"""
F229B2 LIVE KPI EXTRACTION GUARD

Hermetic AST guard for KPI extraction from live_sprint_measurement.py to live_measurement_kpi.py.
Machine-checkable contract for safe future extraction.

VERDICTS (mutually exclusive):
    KPI_EXTRACTION_PREP_PASS          — current repo passes pre-extraction checks
    FAIL_KPI_INPUT_MISSING           — LiveKpiInput dataclass not found in live_sprint_measurement.py
    FAIL_KPI_WRAPPER_MISSING         — _derive_live_kpi compatibility wrapper missing
    FAIL_KPI_BARE_PARAM_USAGE        — _derive_live_kpi_from_input body uses bare old params (not inp.*)
    FAIL_KPI_MODULE_BAD_IMPORT       — live_sprint_measurement.py imports _derive_next_action locally (not from next_action module)
    FAIL_NEXT_ACTION_NOT_EXTRACTED   — next_action not wired from live_measurement_next_action module
    FAIL_KPI_MODULE_RUNTIME_IMPORT   — extracted module would import runtime/network/MLX

POST-EXTRACTION VERDICTS:
    FAIL_KPI_EXTRACTION_NOT_READY     — pre-extraction checks not met
    FAIL_KPI_MODULE_RUNTIME_IMPORT   — live_measurement_kpi.py imports runtime
    FAIL_KPI_MISSING_EXPORTS          — module missing required exports

CLI:
    python tools/live_kpi_extraction_guard.py --repo-root . \\
        --output-json probe_f229b_live_kpi_extraction_guard/live_kpi_extraction_guard.json \\
        --output-md probe_f229b_live_kpi_extraction_guard/REPORT_LIVE_KPI_EXTRACTION_GUARD.md
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS = REPO_ROOT / "benchmarks"

LIVE_SPRINT_MEASUREMENT = BENCHMARKS / "live_sprint_measurement.py"
NEXT_ACTION_MODULE = BENCHMARKS / "live_measurement_next_action.py"
KPI_MODULE = BENCHMARKS / "live_measurement_kpi.py"

# Runtime module prefixes that KPI module must NOT import
RUNTIME_IMPORT_PREFIXES = frozenset([
    "hledac.universal.runtime",
    "hledac.universal.core",
    "hledac.universal.pipeline",
    "hledac.universal.discovery",
    "hledac.universal.fetching",
    "hledac.universal.export",
    "hledac.universal.intelligence",
    "hledac.universal.knowledge",
    "hledac.universal.coordinators",
    "hledac.universal.brain",
    "hledac.universal.security",
    "mlx",
    "aiohttp",
    "curl_cffi",
    "asyncio",
])

# Old param names that must only be accessed via inp.attr in _derive_live_kpi_from_input
# These are the flat params that LiveKpiInput replaces — bare access is a violation
OLD_PARAM_NAMES = frozenset([
    "status",
    "runtime_truth",
    "actual_duration_s",
    "primary_signal_source",
    "run_quality_verdict",
    "hardware_constrained",
    "public_pipeline",
    "timing_truth",
    "acquisition_strategy",
    "windup_guard_observation",
    "return_guard_observation",
    "scheduler_exit",
    "acquisition_report",
    "profile_verdict",
    "acquisition_terminality_checked",
    "acquisition_terminality_satisfied",
    "acquisition_terminality_missing_lanes",
    "planned_duration_s",
    "claims_runtime_status",
])

# Required exports from live_measurement_kpi.py (post-extraction)
KPI_MODULE_REQUIRED_EXPORTS = frozenset([
    "LiveKpiInput",
    "_derive_live_kpi_from_input",
    "_derive_live_kpi",
])


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------

class Verdict:
    PRE_PASS = "KPI_EXTRACTION_PREP_PASS"
    FAIL_KPI_INPUT_MISSING = "FAIL_KPI_INPUT_MISSING"
    FAIL_KPI_WRAPPER_MISSING = "FAIL_KPI_WRAPPER_MISSING"
    FAIL_KPI_BARE_PARAM_USAGE = "FAIL_KPI_BARE_PARAM_USAGE"
    FAIL_KPI_MODULE_BAD_IMPORT = "FAIL_KPI_MODULE_BAD_IMPORT"
    FAIL_NEXT_ACTION_NOT_EXTRACTED = "FAIL_NEXT_ACTION_NOT_EXTRACTED"
    FAIL_KPI_EXTRACTION_NOT_READY = "FAIL_KPI_EXTRACTION_NOT_READY"
    FAIL_KPI_MODULE_RUNTIME_IMPORT = "FAIL_KPI_MODULE_RUNTIME_IMPORT"
    FAIL_KPI_MISSING_EXPORTS = "FAIL_KPI_MISSING_EXPORTS"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _read_source(path: Path) -> str:
    with path.open() as f:
        return f.read()


def _parse_ast(source: str) -> ast.AST:
    return ast.parse(source)


def _get_import_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.module:
            return [f"{node.module}.{alias.name}" for alias in node.names]
        return [alias.name for alias in node.names]
    return []


def _check_module_imports_runtime(module_path: Path) -> tuple[bool, str]:
    """Check if a module imports any runtime prefix. Returns (has_violation, message)."""
    try:
        source = _read_source(module_path)
    except Exception as e:
        return True, f"Cannot read {module_path}: {e}"
    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, f"Cannot parse {module_path}: {e}"

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for name in _get_import_names(node):
                for prefix in RUNTIME_IMPORT_PREFIXES:
                    if name == prefix or name.startswith(f"{prefix}."):
                        return True, f"Runtime import: {name}"
    return False, ""


def _check_live_kpi_input_exists(runner_path: Path) -> tuple[bool, str]:
    """LiveKpiInput dataclass must exist in live_sprint_measurement.py."""
    try:
        source = _read_source(runner_path)
    except Exception as e:
        return True, f"Cannot read {runner_path}: {e}"
    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, f"Cannot parse {runner_path}: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "LiveKpiInput":
            return False, "OK"
    return True, "LiveKpiInput not found in live_sprint_measurement.py"


def _check_kpi_compat_wrapper(runner_path: Path) -> tuple[bool, str]:
    """
    _derive_live_kpi compatibility wrapper must exist and accept the flat param list.
    Wrapper builds LiveKpiInput and delegates to _derive_live_kpi_from_input.
    """
    try:
        source = _read_source(runner_path)
    except Exception as e:
        return True, f"Cannot read {runner_path}: {e}"
    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, f"Cannot parse {runner_path}: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_derive_live_kpi":
            # Must call _derive_live_kpi_from_input
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    fn = child.func
                    if isinstance(fn, ast.Name) and fn.id == "_derive_live_kpi_from_input":
                        return False, "OK"
            return True, "_derive_live_kpi does not call _derive_live_kpi_from_input"
    return True, "_derive_live_kpi function not found"


def _check_kpi_from_input_bare_params(runner_path: Path) -> tuple[bool, str]:
    """
    _derive_live_kpi_from_input body must use inp.* for old params, not bare names.
    Returns (has_bare_params, detail).
    """
    try:
        source = _read_source(runner_path)
    except Exception as e:
        return True, f"Cannot read {runner_path}: {e}"
    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, f"Cannot parse {runner_path}: {e}"

    kpi_func_node: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_derive_live_kpi_from_input":
            kpi_func_node = node
            break

    if kpi_func_node is None:
        return True, "_derive_live_kpi_from_input not found"

    # Collect bare usages: Name nodes in old_param_names that are NOT the object of Attribute
    bare_usages: set[str] = set()
    for child in ast.walk(kpi_func_node):
        if isinstance(child, ast.Name) and child.id in OLD_PARAM_NAMES:
            # Check if parent is Attribute where child is the value (obj of inp.X)
            # Walk all parents from child — if any parent is Attribute and parent.value == child, skip
            is_attribute_obj = False
            for parent in ast.walk(kpi_func_node):
                if isinstance(parent, ast.Attribute) and parent.value == child:
                    is_attribute_obj = True
                    break
            if not is_attribute_obj:
                bare_usages.add(child.id)

    if bare_usages:
        return True, f"bare param usage: {sorted(bare_usages)}"
    return False, "OK"


def _check_next_action_wired_from_module(runner_path: Path) -> tuple[bool, str]:
    """
    live_sprint_measurement.py must import _derive_next_action from
    live_measurement_next_action, not locally own it.
    """
    try:
        source = _read_source(runner_path)
    except Exception as e:
        return True, f"Cannot read {runner_path}: {e}"
    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, f"Cannot parse {runner_path}: {e}"

    # Must have ImportFrom that imports _derive_next_action from live_measurement_next_action
    next_action_imported = False
    local_definition = False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "live_measurement_next_action" in node.module:
                for alias in node.names:
                    if alias.name == "_derive_next_action":
                        next_action_imported = True
        if isinstance(node, ast.FunctionDef) and node.name == "_derive_next_action":
            local_definition = True

    if next_action_imported and not local_definition:
        return False, "OK"
    if local_definition and not next_action_imported:
        return True, "local definition of _derive_next_action without importing from next_action module"
    if local_definition and next_action_imported:
        return True, "_derive_next_action both imported and locally defined"
    return True, "_derive_next_action not imported from live_measurement_next_action"


def _check_kpi_module_exports(kpi_path: Path) -> tuple[bool, list[str]]:
    """Check required exports from live_measurement_kpi.py. Returns (missing_any, list_missing)."""
    if not kpi_path.exists():
        # File not existing is handled separately by the existence check
        return False, []

    try:
        source = _read_source(kpi_path)
    except Exception as e:
        return True, [f"Cannot read: {e}"]
    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, [f"Cannot parse: {e}"]

    exported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            exported.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    exported.add(target.id)

    missing = [n for n in KPI_MODULE_REQUIRED_EXPORTS if n not in exported]
    return bool(missing), missing


# --------------------------------------------------------------------------
# Post-extraction checks (when KPI_MODULE exists)
# --------------------------------------------------------------------------

def _run_post_extraction_checks(repo_root: Path) -> dict[str, Any]:
    """Run post-extraction checks on live_measurement_kpi.py."""
    checks: list[dict[str, Any]] = []
    verdict = Verdict.PRE_PASS

    kpi_path = repo_root / "benchmarks" / "live_measurement_kpi.py"

    if not kpi_path.exists():
        return {
            "verdict": Verdict.FAIL_KPI_EXTRACTION_NOT_READY,
            "checks": [{"check": "kpi_module_exists", "pass": False, "detail": "not found"}],
            "phase": "post",
        }

    # 1. KPI module must not import runtime (critical security — checked first)
    has_rt, msg = _check_module_imports_runtime(kpi_path)
    checks.append({"check": "kpi_module_no_runtime_import", "pass": not has_rt, "detail": msg or "OK"})
    if has_rt:
        verdict = Verdict.FAIL_KPI_MODULE_RUNTIME_IMPORT

    # 2. Required exports (only check if no runtime import violation)
    missing_any, missing = _check_kpi_module_exports(kpi_path)
    checks.append({
        "check": "kpi_module_has_exports",
        "pass": not missing_any,
        "detail": f"Missing: {missing}" if missing else "OK",
    })
    if missing_any and verdict == Verdict.PRE_PASS:
        verdict = Verdict.FAIL_KPI_MISSING_EXPORTS

    # 3. Pre-extraction checks (informational only in post-extraction — runner no longer owns KPI)
    # In post-extraction phase, the runner has exported to KPI module, so
    # LiveKpiInput/no_bare_params/next_action_wired checks are expected to fail.
    # Only runtime import and exports are blocking in post-extraction.
    runner = repo_root / "benchmarks" / "live_sprint_measurement.py"
    pre_pass, pre_checks = _run_pre_extraction_checks_inner(runner, post_extraction=True)
    for pc in pre_checks:
        checks.append({**pc, "phase": "pre"})
    # Only override if pre fails AND we haven't already assigned a more specific post verdict
    if not pre_pass and verdict == Verdict.PRE_PASS:
        verdict = Verdict.FAIL_KPI_EXTRACTION_NOT_READY

    return {"verdict": verdict, "checks": checks, "phase": "post"}


# --------------------------------------------------------------------------
# Pre-extraction checks
# --------------------------------------------------------------------------

def _run_pre_extraction_checks_inner(runner: Path, post_extraction: bool = False) -> tuple[bool, list[dict[str, Any]]]:
    """Core pre-extraction checks shared between standalone and post-extraction.

    In post-extraction mode (post_extraction=True), the runner no longer owns
    LiveKpiInput, _derive_live_kpi, or _derive_live_kpi_from_input — they live in
    the extracted KPI module. Skip those checks so pre-extraction verdicts don't
    override the more specific post-extraction verdicts (runtime import, exports).
    """
    checks: list[dict[str, Any]] = []

    # 1. LiveKpiInput exists (runner must own it before extraction)
    has_input, msg = _check_live_kpi_input_exists(runner)
    checks.append({"check": "live_kpi_input_exists", "pass": not has_input, "detail": msg})
    if has_input and not post_extraction:
        return False, checks

    # 2. _derive_live_kpi wrapper exists and delegates (runner must own it before extraction)
    has_wrapper, msg = _check_kpi_compat_wrapper(runner)
    checks.append({"check": "kpi_compat_wrapper_exists", "pass": not has_wrapper, "detail": msg})
    if has_wrapper and not post_extraction:
        return False, checks

    # 3. _derive_live_kpi_from_input uses inp.* not bare params (runner must own it before extraction)
    has_bare, msg = _check_kpi_from_input_bare_params(runner)
    checks.append({"check": "kpi_from_input_no_bare_params", "pass": not has_bare, "detail": msg})
    if has_bare and not post_extraction:
        return False, checks

    # 4. _derive_next_action imported from live_measurement_next_action, not locally owned
    bad_next_action, msg = _check_next_action_wired_from_module(runner)
    checks.append({"check": "next_action_wired_from_module", "pass": not bad_next_action, "detail": msg})
    if bad_next_action:
        return False, checks

    return True, checks


def run_guard(repo_root: Path) -> dict[str, Any]:
    """
    Run the full guard.
    Phase=pre (no KPI module yet): run pre-extraction checks.
    Phase=post (KPI module exists): also run post-extraction checks.
    """
    benchmarks = repo_root / "benchmarks"
    runner = benchmarks / "live_sprint_measurement.py"
    kpi_path = benchmarks / "live_measurement_kpi.py"

    if kpi_path.exists():
        return _run_post_extraction_checks(repo_root)

    # PRE-EXTRACTION PHASE
    pre_pass, checks = _run_pre_extraction_checks_inner(runner)
    verdict = Verdict.PRE_PASS if pre_pass else _derive_fail_verdict(checks)

    return {
        "verdict": verdict,
        "checks": checks,
        "phase": "pre",
        "kpi_module_exists": False,
    }


def _derive_fail_verdict(checks: list[dict[str, Any]]) -> str:
    """Map failed check to the most specific verdict."""
    check_map = {
        "live_kpi_input_exists": Verdict.FAIL_KPI_INPUT_MISSING,
        "kpi_compat_wrapper_exists": Verdict.FAIL_KPI_WRAPPER_MISSING,
        "kpi_from_input_no_bare_params": Verdict.FAIL_KPI_BARE_PARAM_USAGE,
        "next_action_wired_from_module": Verdict.FAIL_NEXT_ACTION_NOT_EXTRACTED,
    }
    for check in checks:
        if not check["pass"]:
            name = check["check"]
            if name in check_map:
                return check_map[name]
    return Verdict.FAIL_KPI_EXTRACTION_NOT_READY


# --------------------------------------------------------------------------
# Output formatters
# --------------------------------------------------------------------------

def format_json(result: dict) -> str:
    return json.dumps(result, indent=2, default=str)


def format_markdown(result: dict) -> str:
    phase = result.get("phase", "pre")
    title = "F229B2 Live KPI Extraction Guard — Post-Extraction" if phase == "post" else "F229B2 Live KPI Extraction Guard — Pre-Extraction"

    lines = [
        f"# {title}",
        "",
        f"**Verdict:** `{result['verdict']}`",
        f"**Phase:** {phase}",
        "",
        "## Checks",
        "",
        "| Check | Pass | Detail |",
        "| --- | --- | --- |",
    ]
    for check in result["checks"]:
        detail = str(check.get("detail", ""))
        # Truncate long details for readability
        if len(detail) > 120:
            detail = detail[:117] + "..."
        pass_str = "PASS" if check.get("pass") else "FAIL"
        lines.append(f"| {check['check']} | {pass_str} | {detail} |")

    lines.extend([
        "",
        "## Pre-Extraction Contract",
        "",
        "Before `live_measurement_kpi.py` can be extracted, these must all be true:",
        "",
        "| # | Condition | Verdict on fail |",
        "|---|-----------|-----------------|",
        "| 1 | `LiveKpiInput` dataclass exists in `live_sprint_measurement.py` | `FAIL_KPI_INPUT_MISSING` |",
        "| 2 | `_derive_live_kpi(inp)` compatibility wrapper exists | `FAIL_KPI_WRAPPER_MISSING` |",
        "| 3 | `_derive_live_kpi_from_input` body uses `inp.*` not bare params | `FAIL_KPI_BARE_PARAM_USAGE` |",
        "| 4 | `_derive_next_action` imported from `live_measurement_next_action` | `FAIL_NEXT_ACTION_NOT_EXTRACTED` |",
        "",
        "## Post-Extraction Contract",
        "",
        "After extraction, `live_measurement_kpi.py` must:",
        "",
        "| # | Condition | Verdict on fail |",
        "|---|-----------|-----------------|",
        "| 1 | No runtime/network/MLX imports | `FAIL_KPI_MODULE_RUNTIME_IMPORT` |",
        "| 2 | Exports: `LiveKpiInput`, `derive_live_kpi_from_input`, `derive_live_kpi` | `FAIL_KPI_MISSING_EXPORTS` |",
        "| 3 | Pre-extraction contract still satisfied | `FAIL_KPI_EXTRACTION_NOT_READY` |",
        "",
        "## Verdict Reference",
        "",
        "```",
        "KPI_EXTRACTION_PREP_PASS       — pre-extraction checks pass; ready when next_action is wired",
        "FAIL_KPI_INPUT_MISSING          — LiveKpiInput not found",
        "FAIL_KPI_WRAPPER_MISSING        — _derive_live_kpi wrapper missing or broken",
        "FAIL_KPI_BARE_PARAM_USAGE       — _derive_live_kpi_from_input uses bare old params",
        "FAIL_NEXT_ACTION_NOT_EXTRACTED   — next_action not imported from live_measurement_next_action",
        "FAIL_KPI_EXTRACTION_NOT_READY    — post-extraction: pre-checks no longer pass",
        "FAIL_KPI_MODULE_RUNTIME_IMPORT — KPI module imports runtime",
        "FAIL_KPI_MISSING_EXPORTS        — KPI module missing required exports",
        "```",
    ])

    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="F229B2 Live KPI Extraction Guard")
    parser.add_argument("--repo-root", default=".", help="Repository root path")
    parser.add_argument("--output-json", help="Write JSON result to this path")
    parser.add_argument("--output-md", help="Write markdown result to this path")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if not repo_root.exists():
        print(f"ERROR: repo-root does not exist: {repo_root}", file=sys.stderr)
        return 1

    result = run_guard(repo_root)

    json_out = format_json(result)
    md_out = format_markdown(result)

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(json_out)
        print(f"JSON written to: {args.output_json}")

    if args.output_md:
        Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_md).write_text(md_out)
        print(f"Markdown written to: {args.output_md}")

    print(f"\nVerdict: {result['verdict']}")
    return 0 if result["verdict"] == Verdict.PRE_PASS else 1


if __name__ == "__main__":
    sys.exit(main())
