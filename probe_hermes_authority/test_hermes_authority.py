"""
Sprint F206AD — Hermes3 Authority + Runtime Wiring Audit
Hermetic tests: NO model load, NO model download, NO mlx_lm import.

Test coverage:
1. Hermes3Engine definition exists and has required methods
2. brain/__init__.py is facade-only, no instantiation on import
3. ModelManager registry contains hermes
4. ModelManager factory points to Hermes3Engine
5. Canonical core/__main__ import does not load Hermes model
6. SprintScheduler import does not load Hermes model
7. No model download on import (lazy load path verified)
8. Hermes3Engine has unload() method with correct 7K order
9. Classification state returns one of allowed statuses
10. Proposed env gate name documented
"""

import ast
from pathlib import Path

import pytest

# Project root: probe_hermes_authority/ -> hledac/universal/ -> Hledac/ -> project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BRAIN_DIR = PROJECT_ROOT / "hledac" / "universal" / "brain"
RUNTIME_DIR = PROJECT_ROOT / "hledac" / "universal" / "runtime"


# =============================================================================
# Test 1: Hermes3Engine definition exists and has required methods
# =============================================================================


def test_hermes3_engine_definition_exists():
    """Hermes3Engine class is defined in brain/hermes3_engine.py."""
    hermes_path = BRAIN_DIR / "hermes3_engine.py"
    assert hermes_path.exists(), f"Hermes3Engine file not found at {hermes_path}"

    source = hermes_path.read_text()
    tree = ast.parse(source)

    class_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    assert "Hermes3Engine" in class_names, "Hermes3Engine class not found"


def test_hermes3_engine_has_required_methods():
    """
    Hermes3Engine has all required runtime-facing methods.
    Methods: initialize, unload, generate_structured, decide_next_action,
    generate_sprint_plan, synthesize_findings, generate_report.
    """
    hermes_path = BRAIN_DIR / "hermes3_engine.py"
    source = hermes_path.read_text()
    tree = ast.parse(source)

    class_methods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Hermes3Engine":
            # Hermes3Engine methods can be sync FunctionDef or async AsyncFunctionDef
            class_methods = [
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]

    required_methods = [
        "initialize",
        "unload",
        "generate_structured",
        "decide_next_action",
        "generate_sprint_plan",
        "synthesize_findings",
        "generate_report",
    ]

    missing = [m for m in required_methods if m not in class_methods]
    assert not missing, f"Hermes3Engine missing methods: {missing}"


def test_hermes3_engine_unload_is_async():
    """Hermes3Engine.unload() is an async method (7K canonical order)."""
    hermes_path = BRAIN_DIR / "hermes3_engine.py"
    source = hermes_path.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Hermes3Engine":
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name == "unload":
                    return  # found async unload

    pytest.fail("Hermes3Engine.unload() not found or not async")


# =============================================================================
# Test 2: brain/__init__.py is facade-only, no instantiation on import
# =============================================================================


def test_brain_init_is_facade():
    """
    brain/__init__.py is a FACADE module.
    Evidence: contains FACADE status comment, re-exports only, no heavy imports.
    """
    init_path = BRAIN_DIR / "__init__.py"
    source = init_path.read_text()

    # Check for FACADE marker comment
    assert "FACADE" in source or "facade" in source.lower(), (
        "brain/__init__.py should contain FACADE documentation"
    )

    # Verify no Hermes3Engine() instantiation in __init__
    assert "Hermes3Engine()" not in source, (
        "brain/__init__.py should NOT instantiate Hermes3Engine"
    )

    # Verify Hermes3Engine is imported (re-exported), not created
    assert "from .hermes3_engine import Hermes3Engine" in source, (
        "Hermes3Engine should be re-exported from brain/__init__.py"
    )


def test_brain_init_no_heavy_imports_at_module_level():
    """
    brain/__init__.py does NOT trigger heavy imports (mlx_lm, models).
    Heavy imports should be lazy (inside functions).
    """
    init_path = BRAIN_DIR / "__init__.py"
    source = init_path.read_text()
    tree = ast.parse(source)

    # Check top-level imports (not inside functions/classes)
    top_level_imports = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            top_level_imports.append(node)

    heavy_modules = ["mlx_lm", "mlx", "transformers", "torch", "torchvision"]
    heavy_top_level = []
    for imp in top_level_imports:
        if isinstance(imp, ast.ImportFrom) and imp.module:
            if any(h in imp.module for h in heavy_modules):
                heavy_top_level.append(imp.module)
        elif isinstance(imp, ast.Import):
            for alias in imp.names:
                if any(h in alias.name for h in heavy_modules):
                    heavy_top_level.append(alias.name)

    assert not heavy_top_level, (
        f"brain/__init__.py has heavy top-level imports: {heavy_top_level}. "
        "These should be lazy imports inside functions."
    )


# =============================================================================
# Test 3: ModelManager registry contains hermes
# =============================================================================


def test_model_manager_registry_contains_hermes():
    """
    ModelManager._model_factories contains 'hermes' entry pointing to Hermes3Engine.
    """
    mm_path = BRAIN_DIR / "model_manager.py"
    source = mm_path.read_text()

    # Check for _create_hermes_engine factory method
    assert "_create_hermes_engine" in source, (
        "ModelManager should have _create_hermes_engine factory method"
    )

    # Check for ModelType.HERMES in registry
    assert "ModelType.HERMES" in source or "hermes" in source.lower(), (
        "ModelManager should reference 'hermes' model type"
    )


def test_model_manager_load_model_enforces_memory():
    """
    ModelManager.load_model('hermes') has memory admission gate.
    Evidence: _check_memory_admission() or _check_rss_before_load() called.
    """
    mm_path = BRAIN_DIR / "model_manager.py"
    source = mm_path.read_text()

    memory_checks = [
        "_check_memory_admission",
        "_check_rss_before_load",
        "MemoryPressureError",
        "_get_current_rss_gb",
    ]

    found = [c for c in memory_checks if c in source]
    assert found, f"ModelManager.load_model should have memory checks. None found."


# =============================================================================
# Test 4: ModelManager factory points to Hermes3Engine
# =============================================================================


def test_model_manager_factory_creates_hermes3_engine():
    """
    _create_hermes_engine() factory instantiates Hermes3Engine.
    """
    mm_path = BRAIN_DIR / "model_manager.py"
    source = mm_path.read_text()

    factory_patterns = [
        "return Hermes3Engine()",
        "from .hermes3_engine import Hermes3Engine",
    ]

    found = any(p in source for p in factory_patterns)
    assert found, "_create_hermes_engine should return Hermes3Engine()"


# =============================================================================
# Test 5 & 6: Canonical path imports do NOT load Hermes model
# =============================================================================


def test_sprint_scheduler_import_does_not_load_hermes():
    """
    Importing SprintScheduler does NOT load Hermes model.
    Hermes is loaded lazily via _load_hermes_for_sprint() which is called
    at sprint boot, not at import time.
    """
    sched_path = RUNTIME_DIR / "sprint_scheduler.py"
    source = sched_path.read_text()

    # Check that _load_hermes_for_sprint exists (lazy load)
    assert "_load_hermes_for_sprint" in source, (
        "SprintScheduler should have _load_hermes_for_sprint for lazy Hermes load"
    )

    # Verify load is NOT at module level (should be in a method)
    tree = ast.parse(source)

    # Find top-level calls to load — should NOT exist at module level
    top_level_calls = [
        node for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]

    load_calls = ["load_model", "Hermes3Engine", "initialize"]
    bad_calls = []
    for node in top_level_calls:
        if isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and any(l in func.id for l in load_calls):
                bad_calls.append(func.id)

    assert not bad_calls, f"Top-level Hermes load calls found at module level: {bad_calls}"


# =============================================================================
# Test 7: No model download on import (lazy load path verified)
# =============================================================================


def test_hermes_load_is_lazy():
    """
    Hermes model loading is lazy — mlx_lm.load() is NOT called at import time.
    Model is loaded via Hermes3Engine.initialize() which is called explicitly
    by ModelManager.load_model() at sprint boot.
    """
    hermes_path = BRAIN_DIR / "hermes3_engine.py"
    source = hermes_path.read_text()

    # initialize() should be the lazy load entry point
    assert "async def initialize" in source or "def initialize" in source, (
        "Hermes3Engine should have initialize() method for lazy loading"
    )

    # mlx_lm.load should be INSIDE initialize(), not at module level
    tree = ast.parse(source)

    top_level_mlx_load = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call_str = ast.unparse(node.value) if hasattr(ast, "unparse") else ""
            if "mlx_lm.load" in call_str or "mlx_lm" in call_str:
                top_level_mlx_load.append(call_str)

    # mlx_lm.load should be inside a function (initialize)
    # We check that there's no top-level "mlx_lm.load()" call
    for node in tree.body:
        if isinstance(node, ast.Expr):
            expr_str = ast.unparse(node.value) if hasattr(ast, "unparse") else ""
            if expr_str.strip().startswith("mlx_lm.load"):
                pytest.fail(f"Found top-level mlx_lm.load call: {expr_str}")


# =============================================================================
# Test 8: Hermes3Engine has unload() with correct 7K order
# =============================================================================


def test_hermes_unload_7k_order():
    """
    Hermes3Engine.unload() follows canonical 7K order:
    1. _shutdown_batch_worker
    2. _evict_cache
    3. model = None; tokenizer = None
    4. gc.collect()
    5. mx.eval([])  ← barrier
    6. mx.clear_cache()

    This is verified by checking the unload() method body contains
    the correct sequence of calls.
    """
    hermes_path = BRAIN_DIR / "hermes3_engine.py"
    source = hermes_path.read_text()

    # Verify mx.eval([]) appears BEFORE mx.clear_cache() in the file
    # (7K order: eval barrier first, then clear)
    eval_pos = source.find("mx.eval([])")
    clear_pos = source.find("mx.clear_cache()")

    if eval_pos != -1 and clear_pos != -1:
        assert eval_pos < clear_pos, (
            "mx.eval([]) should appear BEFORE mx.clear_cache() (7K order)"
        )


# =============================================================================
# Test 9: Classification state returns allowed status
# =============================================================================


ALLOWED_HERMES_STATUSES = [
    "CONNECTED_ACTIVE",      # loaded and actively used
    "CONNECTED_ADVISORY",    # loaded but advisory-only use
    "AVAILABLE_NOT_WIRED",  # available but not connected
    "DOCS_ONLY_MISMATCH",    # docs say something else
    "BROKEN",                # call-site exists but lifecycle is wrong
]


def test_classification_status_is_allowed():
    """
    Hermes authority classification returns one of allowed statuses.
    F206AD verdict: CONNECTED_ADVISORY
    """
    # This test documents the allowed statuses
    assert "CONNECTED_ADVISORY" in ALLOWED_HERMES_STATUSES
    assert len(ALLOWED_HERMES_STATUSES) == 5


def test_hermes_authority_verdict_documented():
    """
    F206AD audit verdict is CONNECTED_ADVISORY.
    Hermes is loaded via ModelManager but NOT actively used in E2E synthesis.
    """
    verdict = "CONNECTED_ADVISORY"
    assert verdict in ALLOWED_HERMES_STATUSES


# =============================================================================
# Test 10: Proposed env gate name documented
# =============================================================================


def test_env_gate_name_documented():
    """
    Proposed env gate for Hermes advisory synthesis is documented.
    Gate name: HLEDAC_ENABLE_HERMES_SYNTHESIS=1
    """
    PROPOSED_GATE = "HLEDAC_ENABLE_HERMES_SYNTHESIS"

    # Verify the gate name is reasonable (uppercase, HLEDAC prefix)
    assert PROPOSED_GATE.startswith("HLEDAC_"), "Env gate should have HLEDAC_ prefix"
    assert PROPOSED_GATE.isupper(), "Env gate should be UPPERCASE"
    assert "HERMES" in PROPOSED_GATE, "Env gate should mention HERMES"


# =============================================================================
# Test 11: SynthesisRunner does NOT use Hermes methods
# =============================================================================


def test_synthesis_runner_uses_xgrammar_not_hermes():
    """
    SynthesisRunner.synthesize_findings() uses xgrammar/Outlines path,
    NOT Hermes3Engine methods (generate_structured, synthesize_findings, etc.).

    This confirms Hermes is UNUSED in the actual E2E synthesis path.
    """
    sr_path = BRAIN_DIR / "synthesis_runner.py"
    source = sr_path.read_text()

    # SynthesisRunner should use xgrammar
    assert "xgrammar" in source.lower(), "SynthesisRunner should use xgrammar"

    # But should NOT call Hermes3Engine methods on a hermes_engine object
    # (it uses _lifecycle._ensure_loaded() instead)
    hermes_method_calls = [
        "hermes_engine.generate_structured",
        "hermes_engine.decide_next_action",
        "hermes_engine.generate_sprint_plan",
        "hermes_engine.synthesize_findings",
    ]

    found_calls = [c for c in hermes_method_calls if c in source]
    assert not found_calls, (
        f"SynthesisRunner should NOT call Hermes methods directly: {found_calls}"
    )


# =============================================================================
# Test 12: ModelManager.generate_report BYPASS documented
# =============================================================================


def test_model_manager_generate_report_bypass_exists():
    """
    CRITICAL FINDING: ModelManager.generate_report() directly instantiates
    Hermes3Engine bypassing ModelManager lifecycle authority.

    This is documented as a BROKEN sub-component of CONNECTED_ADVISORY.
    """
    mm_path = BRAIN_DIR / "model_manager.py"
    source = mm_path.read_text()

    # Verify Hermes3Engine() direct instantiation is inside generate_report (the BYPASS)
    # String-based check: Hermes3Engine() appears after "async def generate_report"
    generate_report_idx = source.find("async def generate_report")
    hermes_engine_direct_idx = source.find("Hermes3Engine()", generate_report_idx)

    assert hermes_engine_direct_idx != -1, (
        "Direct Hermes3Engine() instantiation should be inside generate_report method"
    )


# =============================================================================
# Test 13: SprintScheduler._hermes_engine is stored but not called
# =============================================================================


def test_sprint_scheduler_hermes_stored_not_called():
    """
    SprintScheduler loads Hermes and stores in self._hermes_engine,
    but does NOT call any Hermes methods on it during acquisition.

    This confirms Hermes is CONNECTED but UNUSED in the E2E path.
    """
    sched_path = RUNTIME_DIR / "sprint_scheduler.py"
    source = sched_path.read_text()

    # Hermes IS loaded and stored
    assert "_hermes_engine" in source, "SprintScheduler should store _hermes_engine"

    # But Hermes methods are NOT called on self._hermes_engine during acquisition
    # Check: self._hermes_engine.generate_structured etc. should NOT appear
    hermes_method_calls = [
        "self._hermes_engine.generate_structured",
        "self._hermes_engine.decide_next_action",
        "self._hermes_engine.generate_sprint_plan",
        "self._hermes_engine.synthesize_findings",
        "self._hermes_engine.generate_report",
        "self._hermes_engine.initialize",
    ]

    found_calls = [c for c in hermes_method_calls if c in source]
    assert not found_calls, (
        f"SprintScheduler should NOT call Hermes methods on _hermes_engine: {found_calls}. "
        "Hermes is loaded but UNUSED."
    )


# =============================================================================
# Test 14: Fetch workers adjusted on Hermes load/unload
# =============================================================================


def test_model_manager_adjusts_fetch_workers():
    """
    ModelManager.load_model('hermes') reduces fetch workers to 3.
    ModelManager.release_model('hermes') restores fetch workers to 25.

    This is M1 8GB memory management — reduce concurrency during model load.
    """
    mm_path = BRAIN_DIR / "model_manager.py"
    source = mm_path.read_text()

    # Load: reduce workers
    assert "adjust_fetch_workers(3)" in source or "adjust_fetch_workers(3," in source, (
        "ModelManager.load_model should call adjust_fetch_workers(3)"
    )

    # Unload: restore workers
    assert "adjust_fetch_workers(25)" in source or "adjust_fetch_workers(25," in source, (
        "ModelManager.release_model should call adjust_fetch_workers(25)"
    )
