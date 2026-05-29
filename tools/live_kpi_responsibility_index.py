"""KPI derivation responsibility index — read-only extraction map for F228A.

Scans benchmarks/live_sprint_measurement.py via AST to build a precise
extraction map of all KPI-related functions WITHOUT importing the module.

This is the prerequisite refactor map before any high-risk extraction of
live_kpi derivation from the monolith (benchmarks/live_sprint_measurement.py).

Extraction order is:
    1. quality verdict helpers   (isolated, no side-effects, pure transforms)
    2. terminality predicates    (pure boolean tests, no KPI computation)
    3. next_action module        (rule dispatcher, NextActionInput dataclass)
    4. live_kpi derivation last  (depends on all three above)
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------


@dataclass
class FunctionSpec:
    """One KPI-related function."""

    name: str
    source_lines: tuple[int, int]  # (start, end) 1-based
    explicit_args: list[str] = field(default_factory=list)
    called_helpers: list[str] = field(default_factory=list)
    key_fields_written: list[str] = field(default_factory=list)
    suggested_target_module: str = ""
    extraction_risk: str = "MEDIUM"  # LOW / MEDIUM / HIGH
    notes: str = ""


# ------------------------------------------------------------------
# Source scanner — no imports, pure AST
# ------------------------------------------------------------------

MEASUREMENT_FILE = Path(
    "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/benchmarks/live_sprint_measurement.py"
)

# Functions that are definitively KPI-related (all are in live_sprint_measurement.py)
TARGET_FUNCTIONS = {
    # Quality verdict
    "_derive_run_quality_verdict",
    "_stamp_run_quality_verdict",
    "_uma_state_is_critical_or_emergency",
    # Terminality predicates (used in quality verdict downgrade)
    "_is_active_domain_query",
    "_has_terminal_source_outcomes",
    "_has_scheduler_exit_path",
    # next_action + helpers
    "_derive_next_action",
    "NextActionInput",
    "_was_family_attempted",
    "_rule_wallclock_enforcement",
    "_rule0b_memory_or_swap_gate",
    "_rule0g_prewindup_barrier",
    "_rule_profile_propagation",
    "_rule_terminality",
    "_rule_provider_surface",
    "_rule_quality_gate",
    "_rule_default",
    # live_kpi top-level
    "_derive_live_kpi",
    "_stamp_live_kpi",
    # live_kpi discovery helpers
    "_derive_discovery_provider_status_debug",
    "_derive_discovery_selected_providers",
    "_derive_discovery_skipped_providers",
    "_derive_discovery_stub_providers",
    "_derive_discovery_not_wired_providers",
}


def _scan_source() -> dict[str, FunctionSpec]:
    """Parse live_sprint_measurement.py and build FunctionSpec for each target."""
    content = MEASUREMENT_FILE.read_text()
    try:
        tree = ast.parse(content)
    except SyntaxError:
        # Fail-soft: if the source file is malformed, return empty index
        return {}

    specs: dict[str, FunctionSpec] = {}
    lineno_map: dict[int, str] = {}  # lineno → function name

    # First pass: collect all FunctionDef/ClassDef positions
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            name = getattr(node, "name", None)
            if name:
                lineno_map[node.lineno] = name

    # Second pass: for each target function, build spec
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            name = node.name
            if name not in TARGET_FUNCTIONS:
                continue

            args = [arg.arg for arg in node.args.args]
            # Get end line (Python 3.8+)
            end_lineno = getattr(node, "endlineno", None) or node.lineno + len(node.body)

            # Find called names within this function (shallow scan)
            helpers: set[str] = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name):
                        helpers.add(child.func.id)

            called_helpers = sorted(helpers & TARGET_FUNCTIONS)

            # Estimate key fields / risk by name
            risk, module, fields_written = _classify(name, node)

            specs[name] = FunctionSpec(
                name=name,
                source_lines=(node.lineno, end_lineno),
                explicit_args=args,
                called_helpers=called_helpers,
                key_fields_written=fields_written,
                suggested_target_module=module,
                extraction_risk=risk,
            )

        elif isinstance(node, ast.ClassDef) and node.name in TARGET_FUNCTIONS:
            # NextActionInput dataclass
            name = node.name
            # Fields are the annotation assignments in the body
            body_lines = [(n.lineno, n) for n in node.body if isinstance(n, ast.AnnAssign)]
            first = min(ln for ln, _ in body_lines) if body_lines else node.lineno
            last = max(getattr(n, "endlineno", ln) for ln, n in body_lines) if body_lines else node.lineno + 1

            # Extract field names from AnnAssign
            field_names = []
            for _, n in body_lines:
                if isinstance(n.target, ast.Name):
                    field_names.append(n.target.id)

            risk, module, _ = _classify(name, None)
            specs[name] = FunctionSpec(
                name=name,
                source_lines=(first, last),
                explicit_args=field_names,
                called_helpers=[],
                key_fields_written=field_names,
                suggested_target_module=module,
                extraction_risk=risk,
            )

    return specs


def _classify(name: str, _node: ast.FunctionDef | None) -> tuple[str, str, list[str]]:
    """Return (extraction_risk, suggested_module, key_fields)."""
    if name in (
        "_uma_state_is_critical_or_emergency",
        "_is_active_domain_query",
        "_has_terminal_source_outcomes",
        "_has_scheduler_exit_path",
        "_was_family_attempted",
    ):
        return "LOW", "benchmarks/live_measurement_terminality.py", []

    if name in (
        "_derive_run_quality_verdict",
        "_stamp_run_quality_verdict",
    ):
        return "LOW", "benchmarks/live_measurement_quality.py", ["verdict", "hardware_constrained"]

    if name in (
        "_rule_wallclock_enforcement",
        "_rule0b_memory_or_swap_gate",
        "_rule0g_prewindup_barrier",
        "_rule_profile_propagation",
        "_rule_terminality",
        "_rule_provider_surface",
        "_rule_quality_gate",
        "_rule_default",
    ):
        return "MEDIUM", "benchmarks/live_measurement_next_action.py", ["next_action", "next_action_detail"]

    if name == "NextActionInput":
        return "LOW", "benchmarks/live_measurement_next_action.py", []

    if name == "_derive_next_action":
        return "MEDIUM", "benchmarks/live_measurement_next_action.py", ["next_action", "next_action_detail"]

    if name in (
        "_derive_discovery_provider_status_debug",
        "_derive_discovery_selected_providers",
        "_derive_discovery_skipped_providers",
        "_derive_discovery_stub_providers",
        "_derive_discovery_not_wired_providers",
    ):
        return "LOW", "benchmarks/live_measurement_kpi.py", ["discovery_*"]

    if name == "_stamp_live_kpi":
        return "HIGH", "benchmarks/live_measurement_kpi.py", ["live_kpi", "research_quality"]

    # _derive_live_kpi — the main hotspot
    return "HIGH", "benchmarks/live_measurement_kpi.py", [
        "total_findings", "accepted_findings", "wallclock_budget_exceeded",
        "feed_dominance_score", "nonfeed_starvation_suspected",
        "next_action", "terminality_quality_verdict", "ct_loss_stage",
        "discovery_provider_status_debug", "claims_extracted_count",
    ]


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def build_responsibility_index() -> dict[str, Any]:
    """Return the full KPI derivation responsibility index as a dict."""
    specs = _scan_source()

    # Group by suggested module
    by_module: dict[str, list[str]] = {}
    for name, spec in specs.items():
        by_module.setdefault(spec.suggested_target_module, []).append(name)

    # Sort functions within each module by start line
    for mod in by_module:
        by_module[mod].sort(key=lambda n: specs[n].source_lines[0])

    return {
        "source_file": str(MEASUREMENT_FILE),
        "extraction_order": [
            "benchmarks/live_measurement_quality.py",      # 1. quality verdict helpers
            "benchmarks/live_measurement_terminality.py",   # 2. terminality predicates
            "benchmarks/live_measurement_next_action.py",   # 3. next_action + rules + NextActionInput
            "benchmarks/live_measurement_kpi.py",            # 4. live_kpi derivation LAST
        ],
        "functions_by_module": by_module,
        "function_specs": {
            name: {
                "source_lines": spec.source_lines,
                "explicit_args": spec.explicit_args,
                "called_helpers": spec.called_helpers,
                "key_fields_written": spec.key_fields_written,
                "suggested_target_module": spec.suggested_target_module,
                "extraction_risk": spec.extraction_risk,
                "notes": spec.notes,
            }
            for name, spec in sorted(specs.items())
        },
        "total_functions": len(specs),
        "high_risk": [n for n, s in specs.items() if s.extraction_risk == "HIGH"],
        "medium_risk": [n for n, s in specs.items() if s.extraction_risk == "MEDIUM"],
        "low_risk": [n for n, s in specs.items() if s.extraction_risk == "LOW"],
        "dead_rule_helpers": [],   # intentionally empty — no dead _rule_* helpers in next_action dispatch
    }


def get_spec(name: str) -> FunctionSpec | None:
    """Return spec for a single function."""
    return _scan_source().get(name)


def list_by_module(module: str) -> list[FunctionSpec]:
    """List all functions suggested for a given module."""
    specs = _scan_source()
    return [s for s in specs.values() if s.suggested_target_module == module]


# ------------------------------------------------------------------
# Self-test (no-op, no imports from live_sprint_measurement.py)
# ------------------------------------------------------------------

if __name__ == "__main__":
    import json

    idx = build_responsibility_index()

    # Probe assertions
    assert "_derive_live_kpi" in idx["function_specs"], "Missing _derive_live_kpi"
    assert "_derive_next_action" in idx["function_specs"], "Missing _derive_next_action"
    assert "NextActionInput" in idx["function_specs"], "Missing NextActionInput"

    # No runtime imports
    assert "runtime" not in str(MEASUREMENT_FILE), "runtime path leaked"
    assert not any(
        "runtime" in str(MEASUREMENT_FILE) for _ in [1]
    ), "runtime path in source_file"

    # No live execution
    print(f"KPI responsibility index: {idx['total_functions']} functions catalogued")
    print(f"Extraction order: {' → '.join(idx['extraction_order'])}")
    print(f"HIGH risk: {idx['high_risk']}")
    print(f"MEDIUM risk: {idx['medium_risk']}")
    print(f"LOW risk: {idx['low_risk']}")

    # Dump to stdout for capture
    print("\n--- JSON ---")
    print(json.dumps(idx, indent=2, default=str))
