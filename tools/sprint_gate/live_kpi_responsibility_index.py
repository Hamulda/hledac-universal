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
import ast
from dataclasses import dataclass, field
import msgspec
from pathlib import Path
from typing import Any
from _core import aclose
from compat.msgspec_gc_compat import Struct


class FunctionSpec(Struct):
    """One KPI-related function."""
    name: str
    source_lines: tuple[int, int]
    explicit_args: list[str] = field(default_factory=list)
    called_helpers: list[str] = field(default_factory=list)
    key_fields_written: list[str] = field(default_factory=list)
    suggested_target_module: str = ''
    extraction_risk: str = 'MEDIUM'
    notes: str = ''
MEASUREMENT_FILE = Path('/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/benchmarks/live_sprint_measurement.py')
TARGET_FUNCTIONS = {'_derive_run_quality_verdict', '_stamp_run_quality_verdict', '_uma_state_is_critical_or_emergency', '_is_active_domain_query', '_has_terminal_source_outcomes', '_has_scheduler_exit_path', '_derive_next_action', 'NextActionInput', '_was_family_attempted', '_rule_wallclock_enforcement', '_rule0b_memory_or_swap_gate', '_rule0g_prewindup_barrier', '_rule_profile_propagation', '_rule_terminality', '_rule_provider_surface', '_rule_quality_gate', '_rule_default', '_derive_live_kpi', '_stamp_live_kpi', '_derive_discovery_provider_status_debug', '_derive_discovery_selected_providers', '_derive_discovery_skipped_providers', '_derive_discovery_stub_providers', '_derive_discovery_not_wired_providers'}

def _scan_source() -> dict[str, FunctionSpec]:
    """Parse live_sprint_measurement.py and build FunctionSpec for each target."""
    content = MEASUREMENT_FILE.read_text()
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return {}
    specs: dict[str, FunctionSpec] = {}
    lineno_map: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            name = getattr(node, 'name', None)
            if name:
                lineno_map[node.lineno] = name
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            name = node.name
            if name not in TARGET_FUNCTIONS:
                continue
            args = [arg.arg for arg in node.args.args]
            end_lineno = getattr(node, 'endlineno', None) or node.lineno + len(node.body)
            helpers: set[str] = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name):
                        helpers.add(child.func.id)
            called_helpers = sorted(helpers & TARGET_FUNCTIONS)
            risk, module, fields_written = _classify(name, node)
            specs[name] = FunctionSpec(name=name, source_lines=(node.lineno, end_lineno), explicit_args=args, called_helpers=called_helpers, key_fields_written=fields_written, suggested_target_module=module, extraction_risk=risk)
        elif isinstance(node, ast.ClassDef) and node.name in TARGET_FUNCTIONS:
            name = node.name
            body_lines = [(n.lineno, n) for n in node.body if isinstance(n, ast.AnnAssign)]
            first = min((ln for ln, _ in body_lines)) if body_lines else node.lineno
            last = max((getattr(n, 'endlineno', ln) for ln, n in body_lines)) if body_lines else node.lineno + 1
            field_names = []
            for _, n in body_lines:
                if isinstance(n.target, ast.Name):
                    field_names.append(n.target.id)
            risk, module, _ = _classify(name, None)
            specs[name] = FunctionSpec(name=name, source_lines=(first, last), explicit_args=field_names, called_helpers=[], key_fields_written=field_names, suggested_target_module=module, extraction_risk=risk)
    return specs

def _classify(name: str, _node: ast.FunctionDef | None) -> tuple[str, str, list[str]]:
    """Return (extraction_risk, suggested_module, key_fields)."""
    if name in ('_uma_state_is_critical_or_emergency', '_is_active_domain_query', '_has_terminal_source_outcomes', '_has_scheduler_exit_path', '_was_family_attempted'):
        return ('LOW', 'benchmarks/live_measurement_terminality.py', [])
    if name in ('_derive_run_quality_verdict', '_stamp_run_quality_verdict'):
        return ('LOW', 'benchmarks/live_measurement_quality.py', ['verdict', 'hardware_constrained'])
    if name in ('_rule_wallclock_enforcement', '_rule0b_memory_or_swap_gate', '_rule0g_prewindup_barrier', '_rule_profile_propagation', '_rule_terminality', '_rule_provider_surface', '_rule_quality_gate', '_rule_default'):
        return ('MEDIUM', 'benchmarks/live_measurement_next_action.py', ['next_action', 'next_action_detail'])
    if name == 'NextActionInput':
        return ('LOW', 'benchmarks/live_measurement_next_action.py', [])
    if name == '_derive_next_action':
        return ('MEDIUM', 'benchmarks/live_measurement_next_action.py', ['next_action', 'next_action_detail'])
    if name in ('_derive_discovery_provider_status_debug', '_derive_discovery_selected_providers', '_derive_discovery_skipped_providers', '_derive_discovery_stub_providers', '_derive_discovery_not_wired_providers'):
        return ('LOW', 'benchmarks/live_measurement_kpi.py', ['discovery_*'])
    if name == '_stamp_live_kpi':
        return ('HIGH', 'benchmarks/live_measurement_kpi.py', ['live_kpi', 'research_quality'])
    return ('HIGH', 'benchmarks/live_measurement_kpi.py', ['total_findings', 'accepted_findings', 'wallclock_budget_exceeded', 'feed_dominance_score', 'nonfeed_starvation_suspected', 'next_action', 'terminality_quality_verdict', 'ct_loss_stage', 'discovery_provider_status_debug', 'claims_extracted_count'])

def build_responsibility_index() -> dict[str, Any]:
    """Return the full KPI derivation responsibility index as a dict."""
    specs = _scan_source()
    by_module: dict[str, list[str]] = {}
    for name, spec in specs.items():
        by_module.setdefault(spec.suggested_target_module, []).append(name)
    for mod in by_module:
        by_module[mod].sort(key=lambda n: specs[n].source_lines[0])
    return {'source_file': str(MEASUREMENT_FILE), 'extraction_order': ['benchmarks/live_measurement_quality.py', 'benchmarks/live_measurement_terminality.py', 'benchmarks/live_measurement_next_action.py', 'benchmarks/live_measurement_kpi.py'], 'functions_by_module': by_module, 'function_specs': {name: {'source_lines': spec.source_lines, 'explicit_args': spec.explicit_args, 'called_helpers': spec.called_helpers, 'key_fields_written': spec.key_fields_written, 'suggested_target_module': spec.suggested_target_module, 'extraction_risk': spec.extraction_risk, 'notes': spec.notes} for name, spec in sorted(specs.items())}, 'total_functions': len(specs), 'high_risk': [n for n, s in specs.items() if s.extraction_risk == 'HIGH'], 'medium_risk': [n for n, s in specs.items() if s.extraction_risk == 'MEDIUM'], 'low_risk': [n for n, s in specs.items() if s.extraction_risk == 'LOW'], 'dead_rule_helpers': []}

def get_spec(name: str) -> FunctionSpec | None:
    """Return spec for a single function."""
    return _scan_source().get(name)

def list_by_module(module: str) -> list[FunctionSpec]:
    """List all functions suggested for a given module."""
    specs = _scan_source()
    return [s for s in specs.values() if s.suggested_target_module == module]
if __name__ == '__main__':
    import json
    idx = build_responsibility_index()
    assert '_derive_live_kpi' in idx['function_specs'], 'Missing _derive_live_kpi'
    assert '_derive_next_action' in idx['function_specs'], 'Missing _derive_next_action'
    assert 'NextActionInput' in idx['function_specs'], 'Missing NextActionInput'
    assert 'runtime' not in str(MEASUREMENT_FILE), 'runtime path leaked'
    assert not any(('runtime' in str(MEASUREMENT_FILE) for _ in [1])), 'runtime path in source_file'
    print(f"KPI responsibility index: {idx['total_functions']} functions catalogued")
    print(f"Extraction order: {' → '.join(idx['extraction_order'])}")
    print(f"HIGH risk: {idx['high_risk']}")
    print(f"MEDIUM risk: {idx['medium_risk']}")
    print(f"LOW risk: {idx['low_risk']}")
    print('\n--- JSON ---')
    print(json.dumps(idx, indent=2, default=str))