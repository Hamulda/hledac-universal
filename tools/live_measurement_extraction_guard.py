#!/usr/bin/env python3
"""
F227D/F228G LIVE MEASUREMENT EXTRACTION GUARD

Hermetic AST/source guard for the F227 extraction boundary + F228G shadow guard.
Prevents live_sprint_measurement.py from silently re-absorbing schema/parser/markdown code
or locally redefining extracted quality/terminality helpers.

Verdicts:
    EXTRACTION_GUARD_PASS
    FAIL_SCHEMA_DRIFT
    FAIL_PARSER_DRIFT
    FAIL_MARKDOWN_DRIFT
    FAIL_RUNTIME_IMPORT_IN_EXTRACTED_MODULE
    FAIL_MISSING_EXTRACTED_MODULE
    FAIL_QUALITY_SHADOWING
    FAIL_TERMINALITY_SHADOWING
    FAIL_KPI_INPUT_WIRING

CLI:
    python tools/live_measurement_extraction_guard.py --repo-root . \\
        --output-json probe_f227d_live_measurement_extraction_guard/live_extraction_guard.json \\
        --output-md probe_f227d_live_measurement_extraction_guard/LIVE_EXTRACTION_GUARD.md
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


# --------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent  # tools/ → hledac/universal/
BENCHMARKS = REPO_ROOT / "benchmarks"

LIVE_SPRINT_MEASUREMENT = BENCHMARKS / "live_sprint_measurement.py"
SCHEMA_MODULE = BENCHMARKS / "live_measurement_schema.py"
PARSER_MODULE = BENCHMARKS / "live_measurement_parser.py"
MARKDOWN_MODULE = BENCHMARKS / "live_measurement_markdown.py"

# Schema class names that must NOT be defined in live_sprint_measurement.py
SCHEMA_CLASSES = {"RunMode", "MeasurementStatus", "RunQualityVerdict", "LiveMeasurementResult"}

# Runtime module prefixes that extracted modules must NOT import
RUNTIME_IMPORT_PREFIXES = frozenset([
    "hledac.universal.runtime",
    "hledac.universal.core",
    "hledac.universal.pipeline",
    "hledac.universal.discovery",
    "hledac.universal.fetching",
    "hledac.universal.export",
    "hledac.universal.intelligence",
    "hledac.universal.knowledge",
    "mlx",
    "aiohttp",
    "curl_cffi",
    "asyncio",
])

# Required public exports from extracted modules
REQUIRED_EXPORTS = {
    SCHEMA_MODULE: set(),  # schema-only, no required exports
    PARSER_MODULE: {"parse_sprint_report"},
    MARKDOWN_MODULE: {"render_live_measurement_markdown"},
}


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------#

class Verdict:
    PASS = "EXTRACTION_GUARD_PASS"
    FAIL_SCHEMA_DRIFT = "FAIL_SCHEMA_DRIFT"
    FAIL_PARSER_DRIFT = "FAIL_PARSER_DRIFT"
    FAIL_MARKDOWN_DRIFT = "FAIL_MARKDOWN_DRIFT"
    FAIL_RUNTIME_IMPORT = "FAIL_RUNTIME_IMPORT_IN_EXTRACTED_MODULE"
    FAIL_MISSING_MODULE = "FAIL_MISSING_EXTRACTED_MODULE"
    FAIL_QUALITY_SHADOWING = "FAIL_QUALITY_SHADOWING"
    FAIL_TERMINALITY_SHADOWING = "FAIL_TERMINALITY_SHADOWING"
    FAIL_KPI_INPUT_WIRING = "FAIL_KPI_INPUT_WIRING"


# --------------------------------------------------------------------------
# Check helpers
# --------------------------------------------------------------------------#

def _read_source(path: Path) -> str:
    with path.open() as f:
        return f.read()


def _parse_ast(source: str) -> ast.AST:
    return ast.parse(source)


def _get_import_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            return [f"{node.module}.{alias.name}" for alias in node.names]
        return [alias.name for alias in node.names]
    return []


def _check_module_imports_runtime(module_path: Path) -> tuple[bool, str]:
    """
    Check if a module imports any runtime/problematic prefixes.
    Returns (has_violation, first_violation_message).
    """
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
                # Check if any runtime prefix is a prefix of the imported name
                for prefix in RUNTIME_IMPORT_PREFIXES:
                    if name == prefix or name.startswith(f"{prefix}."):
                        return True, f"Runtime import found: {name}"
    return False, ""


def _check_schema_classes_not_in_runner(runner_path: Path) -> tuple[bool, str | list[str]]:
    """
    Check that schema classes are NOT defined in live_sprint_measurement.py.
    Returns (has_violation, list_of_found_classes).
    """
    try:
        source = _read_source(runner_path)
    except Exception as e:
        return True, [f"Cannot read {runner_path}: {e}"]

    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, [f"Cannot parse {runner_path}: {e}"]

    found_classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if node.name in SCHEMA_CLASSES:
                found_classes.append(node.name)

    return bool(found_classes), found_classes if found_classes else "OK"


def _check_render_md_delegation(runner_path: Path) -> tuple[bool, str]:
    """
    Check that _render_md delegates to the extracted markdown module.
    Returns (has_violation, message).
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
        if isinstance(node, ast.FunctionDef) and node.name == "_render_md":
            # Check for lazy import pattern: from hledac.universal.benchmarks.live_measurement_markdown import
            for child in ast.walk(node):
                if isinstance(child, ast.ImportFrom):
                    if child.module and "live_measurement_markdown" in child.module:
                        return False, ""
            # Also check if it just calls the extracted function
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Attribute):
                        if child.func.attr == "render_live_measurement_markdown":
                            return False, ""
                    elif isinstance(child.func, ast.Name):
                        if child.func.id == "render_live_measurement_markdown":
                            return False, ""
            # If no delegation found, it's a violation
            return True, "_render_md does not delegate to extracted markdown module"

    return True, "_render_md function not found in live_sprint_measurement.py"


def _check_parse_sprint_report_delegation(runner_path: Path) -> tuple[bool, str]:
    """
    Check that _parse_sprint_report in runner delegates to extracted parser.
    The runner may have its own wrapper but it must call the extracted parse_sprint_report.
    Returns (has_violation, message).
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
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_sprint_report":
            # Look for delegation: calls _parse_sprint_report_impl or imports from parser
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id == "_parse_sprint_report_impl":
                    return False, ""
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name) and child.func.id == "_parse_sprint_report_impl":
                        return False, ""
            # Check if it imports and calls parse_sprint_report from parser module
            for child in ast.walk(node):
                if isinstance(child, ast.ImportFrom):
                    if child.module and "live_measurement_parser" in child.module:
                        return False, ""
                # Also detect direct calls to parse_sprint_report (imported from parser)
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name) and child.func.id == "parse_sprint_report":
                        return False, ""
            return True, "_parse_sprint_report does not delegate to extracted parser"

    return True, "_parse_sprint_report function not found"


def _check_runner_imports_schema(runner_path: Path) -> tuple[bool, str]:
    """
    Check that live_sprint_measurement.py imports from the schema module.
    Returns (imports_correctly, message).
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
        if isinstance(node, ast.ImportFrom):
            if node.module and "live_measurement_schema" in node.module:
                return False, ""

    return True, "live_sprint_measurement.py does not import from live_measurement_schema"


def _check_required_exports(module_path: Path, required: set[str]) -> tuple[bool, list[str]]:
    """
    Check that a module exports the required symbols.
    Returns (all_present, list_of_missing).
    """
    try:
        source = _read_source(module_path)
    except Exception as e:
        return True, [f"Cannot read {module_path}: {e}"]

    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, [f"Cannot parse {module_path}: {e}"]

    exported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            exported.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    exported.add(target.id)  # type: ignore[attr-defined]

    missing = [name for name in required if name not in exported]
    return bool(missing), missing


def _check_extracted_module_exists(module_path: Path) -> bool:
    return module_path.exists()


# --------------------------------------------------------------------------
# F228G Shadow Guard helpers
# --------------------------------------------------------------------------

# Quality helpers that live in live_measurement_quality.py and must NOT be
# locally redefined in live_sprint_measurement.py (unless body is single delegation)
QUALITY_HELPERS = frozenset([
    "_derive_run_quality_verdict",
    "_uma_state_is_critical_or_emergency",
    "_is_active_domain_query",
])

# Terminality helpers that live in live_measurement_parser.py and must NOT be
# locally redefined (unless body is single delegation)
TERMINALITY_HELPERS = frozenset([
    "_has_terminal_source_outcomes",
    "_has_scheduler_exit_path",
])


def _is_thin_delegation(node: ast.FunctionDef) -> bool:
    """
    Return True if the function body is a thin delegation alias:
    - optional docstring
    - exactly one remaining statement: Return(Call(...))
    - call target may be ast.Name or ast.Attribute (e.g. _qm._helper)
    - no other logic allowed
    """
    if len(node.body) == 0:
        return False
    # Strip optional leading docstring (Expr(Constant(str)))
    body = node.body
    if (len(body) >= 1
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    # Must have exactly one remaining statement
    if len(body) != 1:
        return False
    stmt = body[0]
    if not isinstance(stmt, ast.Return):
        return False
    value = stmt.value
    if not isinstance(value, ast.Call):
        return False
    # Accept ast.Name or ast.Attribute call targets
    func = value.func
    if isinstance(func, ast.Name) and func.id.startswith("_"):
        return True
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return True
    return False


def _check_shadowed_helpers(
    runner_path: Path,
    helper_names: frozenset[str],
    source: str | None = None,
) -> tuple[bool, list[dict]]:
    """
    Check that helper names are NOT locally defined as FunctionDef in runner,
    unless the body is a single thin delegation to an imported helper.

    Returns (has_violation, list_of_violations).
    """
    if source is None:
        try:
            source = _read_source(runner_path)
        except Exception as e:
            return True, [{"name": "?", "reason": f"Cannot read: {e}"}]

    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, [{"name": "?", "reason": f"Cannot parse: {e}"}]

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in helper_names:
            if _is_thin_delegation(node):
                continue  # thin alias/delegation is allowed
            violations.append({
                "name": node.name,
                "line": node.lineno,
                "reason": "local definition — body is not single delegation to imported helper",
            })

    return bool(violations), violations


def _check_live_kpi_input_wiring(runner_path: Path, source: str | None = None) -> tuple[bool, list[dict]]:
    """
    Check LiveKpiInput wiring in live_sprint_measurement.py:
    - LiveKpiInput dataclass exists
    - _derive_live_kpi_from_input exists and has exactly one param named 'inp'
    - _derive_live_kpi_from_input body must NOT load bare old param names
      (status, runtime_truth, actual_duration_s, primary_signal_source, etc.)
      as free variables — it must use inp.attr access.

    Returns (has_violation, list_of_violations).
    """
    if source is None:
        try:
            source = _read_source(runner_path)
        except Exception as e:
            return True, [{"name": "?", "reason": f"Cannot read: {e}"}]

    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, [{"name": "?", "reason": f"Cannot parse: {e}"}]

    violations = []

    # 1. LiveKpiInput dataclass must exist
    has_live_kpi_input = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "LiveKpiInput":
            has_live_kpi_input = True

    if not has_live_kpi_input:
        violations.append({
            "name": "LiveKpiInput",
            "reason": "LiveKpiInput dataclass not found in live_sprint_measurement.py",
        })

    # 2. _derive_live_kpi_from_input must exist with exactly one param named 'inp'
    has_kpi_func = False
    kpi_func_single_inp = False

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_derive_live_kpi_from_input":
            has_kpi_func = True
            args = [a.arg for a in node.args.args]
            if len(args) == 1 and args[0] == "inp":
                kpi_func_single_inp = True
            else:
                violations.append({
                    "name": "_derive_live_kpi_from_input",
                    "reason": f"expected single 'inp' param, got {len(args)} params: {args}",
                })

            # 3. Body must not load bare old param names as local vars
            # Old param names that must be accessed via inp.*
            old_param_names = frozenset([
                "status", "runtime_truth", "actual_duration_s", "primary_signal_source",
                "run_quality_verdict", "hardware_constrained", "public_pipeline",
                "timing_truth", "acquisition_strategy", "windup_guard_observation",
                "return_guard_observation", "scheduler_exit", "acquisition_report",
                "profile_verdict", "acquisition_terminality_checked",
                "acquisition_terminality_satisfied", "acquisition_terminality_missing_lanes",
                "planned_duration_s", "claims_runtime_status",
            ])

            # Walk body and collect all Name nodes; then filter out those that
            # appear as the object of an Attribute (i.e., inp.status → status is obj)
            bare_usages = []
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id in old_param_names:
                    bare_usages.append(child.id)

            # Filter out usages that appear as the object of an Attribute (inp.X)
            # by checking if parent is Attribute with child.id as value
            bad_usages = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id in old_param_names:
                    # Check if parent is Attribute — if so, this is inp.X not bare X
                    for parent in ast.walk(node):
                        if isinstance(parent, ast.Attribute) and isinstance(parent.value, ast.Name):
                            if parent.value.id == child.id:
                                break  # this is an attribute access, skip
                    else:
                        # Not inside inp.X, treat as bare usage
                        bad_usages.add(child.id)

            if bad_usages:
                violations.append({
                    "name": "_derive_live_kpi_from_input",
                    "reason": f"body loads bare old params (must use inp.attr): {sorted(bad_usages)}",
                })

    if not has_kpi_func:
        violations.append({
            "name": "_derive_live_kpi_from_input",
            "reason": "_derive_live_kpi_from_input not found in live_sprint_measurement.py",
        })

    return bool(violations), violations


# --------------------------------------------------------------------------
# Main guard logic
# --------------------------------------------------------------------------#

def run_guard(repo_root: Path) -> dict:
    """
    Run all extraction guard checks.
    Returns a dict with verdict, checks, and details.
    """
    benchmarks = repo_root / "benchmarks"
    runner = benchmarks / "live_sprint_measurement.py"
    schema = benchmarks / "live_measurement_schema.py"
    parser = benchmarks / "live_measurement_parser.py"
    markdown = benchmarks / "live_measurement_markdown.py"

    checks = []
    verdict = Verdict.PASS

    # 1. Check extracted modules exist
    for module_path, label in [(schema, "schema"), (parser, "parser"), (markdown, "markdown")]:
        exists = _check_extracted_module_exists(module_path)
        checks.append({
            "check": f"extracted_module_exists_{label}",
            "pass": exists,
            "detail": str(module_path),
        })
        if not exists:
            verdict = Verdict.FAIL_MISSING_MODULE

    if verdict == Verdict.FAIL_MISSING_MODULE:
        return {"verdict": verdict, "checks": checks}

    # 2. Check runner imports schema module correctly
    imports_schema, msg = _check_runner_imports_schema(runner)
    checks.append({
        "check": "runner_imports_schema",
        "pass": not imports_schema,
        "detail": msg or "OK",
    })
    if imports_schema:
        verdict = Verdict.FAIL_SCHEMA_DRIFT

    # 3. Check schema classes NOT defined in runner
    has_classes, found = _check_schema_classes_not_in_runner(runner)
    checks.append({
        "check": "schema_classes_not_in_runner",
        "pass": not has_classes,
        "detail": f"Found: {found}" if found else "OK",
    })
    if has_classes:
        verdict = Verdict.FAIL_SCHEMA_DRIFT

    # 4. Check _render_md delegates to extracted markdown
    not_delegated, msg = _check_render_md_delegation(runner)
    checks.append({
        "check": "render_md_delegation",
        "pass": not not_delegated,
        "detail": msg or "OK",
    })
    if not_delegated:
        verdict = Verdict.FAIL_MARKDOWN_DRIFT

    # 5. Check _parse_sprint_report delegates to extracted parser
    not_delegated, msg = _check_parse_sprint_report_delegation(runner)
    checks.append({
        "check": "parse_sprint_report_delegation",
        "pass": not not_delegated,
        "detail": msg or "OK",
    })
    if not_delegated:
        verdict = Verdict.FAIL_PARSER_DRIFT

    # 6. Check extracted modules do NOT import runtime
    for module_path, label in [(schema, "schema"), (parser, "parser"), (markdown, "markdown")]:
        has_violation, msg = _check_module_imports_runtime(module_path)
        checks.append({
            "check": f"{label}_no_runtime_import",
            "pass": not has_violation,
            "detail": msg or "OK",
        })
        if has_violation:
            verdict = Verdict.FAIL_RUNTIME_IMPORT

    # 7. Check required exports from extracted modules
    for module_path, required in [(parser, {"parse_sprint_report"}), (markdown, {"render_live_measurement_markdown"})]:
        missing_export, missing = _check_required_exports(module_path, required)
        checks.append({
            "check": f"{module_path.stem}_has_required_exports",
            "pass": not missing_export,
            "detail": f"Missing: {missing}" if missing else "OK",
        })
        if missing_export:
            # Only fail if module exists (already checked above)
            if module_path.exists():
                verdict = Verdict.FAIL_PARSER_DRIFT  # best-effort verdict

    # 8. F228G: Check quality helpers not shadowed (unless thin delegation)
    runner_source = _read_source(runner)
    has_quality_violations, quality_violations = _check_shadowed_helpers(
        runner, QUALITY_HELPERS, runner_source
    )
    checks.append({
        "check": "quality_helpers_not_shadowed",
        "pass": not has_quality_violations,
        "detail": f"Violations: {quality_violations}" if quality_violations else "OK",
    })
    if has_quality_violations:
        verdict = Verdict.FAIL_QUALITY_SHADOWING

    # 9. F228G: Check terminality helpers not shadowed (unless thin delegation)
    has_terminality_violations, terminality_violations = _check_shadowed_helpers(
        runner, TERMINALITY_HELPERS, runner_source
    )
    checks.append({
        "check": "terminality_helpers_not_shadowed",
        "pass": not has_terminality_violations,
        "detail": f"Violations: {terminality_violations}" if terminality_violations else "OK",
    })
    if has_terminality_violations:
        verdict = Verdict.FAIL_TERMINALITY_SHADOWING

    # 10. F228G: Check LiveKpiInput wiring
    has_kpi_violations, kpi_violations = _check_live_kpi_input_wiring(runner, runner_source)
    checks.append({
        "check": "live_kpi_input_wiring",
        "pass": not has_kpi_violations,
        "detail": f"Violations: {kpi_violations}" if kpi_violations else "OK",
    })
    if has_kpi_violations:
        verdict = Verdict.FAIL_KPI_INPUT_WIRING

    return {
        "verdict": verdict,
        "checks": checks,
        "schema_classes": list(SCHEMA_CLASSES),
        "extracted_modules": {
            "schema": str(schema.relative_to(repo_root)),
            "parser": str(parser.relative_to(repo_root)),
            "markdown": str(markdown.relative_to(repo_root)),
        },
    }


# --------------------------------------------------------------------------
# Output formatters
# --------------------------------------------------------------------------#

def format_json(result: dict) -> str:
    return json.dumps(result, indent=2, default=str)


def format_markdown(result: dict) -> str:
    # Detect which sprint lane this is based on checks present
    has_quality_check = any(c["check"] == "quality_helpers_not_shadowed" for c in result["checks"])
    has_kpi_check = any(c["check"] == "live_kpi_input_wiring" for c in result["checks"])

    title = "F227D/F228G Live Measurement Extraction Guard" if (has_quality_check or has_kpi_check) else "F227D Live Measurement Extraction Guard"

    lines = [
        f"# {title}",
        "",
        f"**Verdict:** `{result['verdict']}`",
        "",
        "## Checks",
        "",
        "| Check | Pass | Detail |",
        "| --- | --- | --- |",
    ]
    for check in result["checks"]:
        pass_str = "PASS" if check["pass"] else "FAIL"
        lines.append(f"| {check['check']} | {pass_str} | {check['detail']} |")

    lines.extend([
        "",
        "## Extracted Modules",
        "",
    ])
    for label, path in result["extracted_modules"].items():
        lines.append(f"- **{label}**: `{path}`")

    lines.extend([
        "",
        f"## Schema Classes (must stay in schema module)",
        "",
    ])
    for cls in result["schema_classes"]:
        lines.append(f"- `{cls}`")

    # F228G shadow guard section
    if has_quality_check or has_kpi_check:
        lines.extend([
            "",
            "## F228G Shadow Guard",
            "",
            "### Quality Helpers (from live_measurement_quality.py)",
            "- `_derive_run_quality_verdict`",
            "- `_uma_state_is_critical_or_emergency`",
            "- `_is_active_domain_query`",
            "",
            "### Terminality Helpers (from live_measurement_parser.py)",
            "- `_has_terminal_source_outcomes`",
            "- `_has_scheduler_exit_path`",
            "",
            "### LiveKpiInput Wiring Rules",
            "- `LiveKpiInput` dataclass must exist",
            "- `_derive_live_kpi_from_input` must have exactly one param: `inp`",
            "- Function body must use `inp.attr` not bare `attr`",
        ])

    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------#

def main() -> int:
    parser = argparse.ArgumentParser(description="F227D Live Measurement Extraction Guard")
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
    return 0 if result["verdict"] == Verdict.PASS else 1


if __name__ == "__main__":
    sys.exit(main())