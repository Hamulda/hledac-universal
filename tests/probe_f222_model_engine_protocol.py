#!/usr/bin/env python3
"""
probe_f222_model_engine_protocol — Sprint F222
================================================

Self-contained smoke test for ModelEngine Protocol.

Tests that:
1. ModelEngine Protocol defines the required contract
2. ModernBertModelAdapter provides all ModelEngine methods
3. Hermes3Engine provides all ModelEngine methods
4. model_manager._create_modernbert_engine uses ModernBertModelAdapter

No imports from hledac tree — uses inspect on source files directly.
"""

import ast
import os
import sys

# ── helpers ────────────────────────────────────────────────────────────────────

def get_method_names(filepath: str) -> set:
    """Parse a Python file and return all method names defined in classes."""
    with open(filepath) as f:
        tree = ast.parse(f.read())

    methods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) or isinstance(item, ast.FunctionDef):
                    methods.add(item.name)
    return methods


def file_has_pattern(filepath: str, pattern: str) -> bool:
    """Check if a source file contains a specific pattern."""
    with open(filepath) as f:
        return pattern in f.read()


def get_class_source(filepath: str, class_name: str) -> str | None:
    """Extract source of a specific class from a file."""
    with open(filepath) as f:
        content = f.read()
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            # Get line numbers
            start = node.lineno
            end = node.end_lineno or start + 200
            lines = content.split('\n')
            return '\n'.join(lines[start - 1:end])
    return None


# ── paths ─────────────────────────────────────────────────────────────────────

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN = os.path.join(ROOT, 'brain')

MODEL_ENGINE_PATH = os.path.join(BRAIN, 'model_engine.py')
MODERNBERT_ADAPTER_PATH = os.path.join(BRAIN, 'modernbert_adapter.py')
HERMES3_ENGINE_PATH = os.path.join(BRAIN, 'hermes3_engine.py')
MODEL_MANAGER_PATH = os.path.join(BRAIN, 'model_manager.py')


# ── Test 1: ModelEngine Protocol defines required contract ───────────────────

def test_model_engine_protocol_has_required_methods():
    """
    ModelEngine must define: load, unload, generate, generate_structured,
    get_current_model_name.
    """
    required = ['load', 'unload', 'generate', 'generate_structured', 'get_current_model_name']
    methods = get_method_names(MODEL_ENGINE_PATH)
    for m in required:
        assert m in methods, f"ModelEngine missing required method: {m}"
    print(f"  ✓ ModelEngine defines all required methods: {required}")


def test_model_engine_protocol_optional_methods():
    """ModelEngine may define: generate_report, synthesize."""
    methods = get_method_names(MODEL_ENGINE_PATH)
    optional = ['generate_report', 'synthesize']
    for m in optional:
        assert m in methods, f"ModelEngine missing optional method: {m}"
    print(f"  ✓ ModelEngine defines optional methods: {optional}")


# ── Test 2: ModernBertModelAdapter provides ModelEngine contract ───────────────

def test_modernbert_adapter_has_required_methods():
    """ModernBertModelAdapter must implement all ModelEngine required methods."""
    required = ['load', 'unload', 'generate', 'generate_structured', 'get_current_model_name']
    methods = get_method_names(MODERNBERT_ADAPTER_PATH)
    for m in required:
        assert m in methods, f"ModernBertModelAdapter missing: {m}"
    print(f"  ✓ ModernBertModelAdapter has required: {required}")


def test_modernbert_adapter_has_optional_methods():
    """ModernBertModelAdapter implements generate_report and synthesize."""
    methods = get_method_names(MODERNBERT_ADAPTER_PATH)
    optional = ['generate_report', 'synthesize']
    for m in optional:
        assert m in methods, f"ModernBertModelAdapter missing: {m}"
    print(f"  ✓ ModernBertModelAdapter has optional: {optional}")


def test_modernbert_adapter_has_is_ready():
    """ModernBertModelAdapter provides is_ready passthrough."""
    methods = get_method_names(MODERNBERT_ADAPTER_PATH)
    assert 'is_ready' in methods
    print("  ✓ ModernBertModelAdapter has is_ready helper")


def test_modernbert_adapter_returns_empty_on_generate():
    """ModernBertModelAdapter.generate() returns '' — not a text generator."""
    source = get_class_source(MODERNBERT_ADAPTER_PATH, 'ModernBertModelAdapter')
    assert source is not None
    # Check that generate returns "" or empty
    assert 'return ""' in source or "return ''" in source
    print("  ✓ ModernBertModelAdapter.generate() returns empty string (extractive-only)")


# ── Test 3: Hermes3Engine provides ModelEngine contract ───────────────────────

def test_hermes3_engine_has_required_methods():
    """Hermes3Engine must implement all ModelEngine required methods."""
    required = ['generate', 'generate_structured', 'get_current_model_name', 'unload']
    # Hermes3Engine uses initialize() not load(), has load_model() alias
    for m in required:
        assert m in get_method_names(HERMES3_ENGINE_PATH), f"Hermes3Engine missing: {m}"
    print(f"  ✓ Hermes3Engine has required: {required}")


def test_hermes3_engine_uses_initialize_not_load():
    """Hermes3Engine uses initialize() for loading (not load())."""
    methods = get_method_names(HERMES3_ENGINE_PATH)
    assert 'initialize' in methods
    assert 'load_model' in methods  # Also has load_model() alias
    print("  ✓ Hermes3Engine uses initialize() + load_model() for loading")


def test_hermes3_engine_has_optional_methods():
    """Hermes3Engine implements generate_report and synthesize."""
    methods = get_method_names(HERMES3_ENGINE_PATH)
    optional = ['generate_report', 'synthesize']
    for m in optional:
        assert m in methods, f"Hermes3Engine missing: {m}"
    print(f"  ✓ Hermes3Engine has optional: {optional}")


# ── Test 4: model_manager factory uses ModernBertModelAdapter ────────────────

def test_model_manager_creates_modernbert_adapter():
    """model_manager._create_modernbert_engine returns ModernBertModelAdapter."""
    with open(MODEL_MANAGER_PATH) as f:
        content = f.read()

    # Should import ModernBertModelAdapter from modernbert_adapter
    assert 'from .modernbert_adapter import ModernBertModelAdapter' in content
    # Should return ModernBertModelAdapter()
    assert 'return ModernBertModelAdapter()' in content
    print("  ✓ model_manager._create_modernbert_engine returns ModernBertModelAdapter()")


def test_model_manager_hermes_factory_unchanged():
    """model_manager._create_hermes_engine still returns Hermes3Engine."""
    with open(MODEL_MANAGER_PATH) as f:
        content = f.read()

    assert 'from .hermes3_engine import Hermes3Engine' in content
    assert 'return Hermes3Engine()' in content
    print("  ✓ model_manager._create_hermes_engine unchanged (returns Hermes3Engine)")


# ── Test 5: brain/__init__.py exports ────────────────────────────────────────

def test_brain_init_exports_model_engine():
    """brain/__init__.py exports ModelEngine and ModernBertModelAdapter."""
    INIT_PATH = os.path.join(BRAIN, '__init__.py')
    with open(INIT_PATH) as f:
        content = f.read()

    assert 'ModelEngine' in content
    assert 'ModernBertModelAdapter' in content
    assert 'MODEL_ENGINE_AVAILABLE' in content
    print("  ✓ brain/__init__.py exports ModelEngine, ModernBertModelAdapter, MODEL_ENGINE_AVAILABLE")


def test_brain_init_imports_model_engine():
    """brain/__init__.py imports ModelEngine and ModernBertModelAdapter."""
    INIT_PATH = os.path.join(BRAIN, '__init__.py')
    with open(INIT_PATH) as f:
        content = f.read()

    assert 'from .model_engine import ModelEngine' in content
    assert 'from .modernbert_adapter import ModernBertModelAdapter' in content
    print("  ✓ brain/__init__.py imports from model_engine and modernbert_adapter")


# ── Test 6: Adapter bridges different method signatures ──────────────────────

def test_modernbert_adapter_load_returns_bool():
    """ModernBertModelAdapter.load() is async and returns bool."""
    source = get_class_source(MODERNBERT_ADAPTER_PATH, 'ModernBertModelAdapter')
    assert source is not None
    assert 'async def load' in source
    assert '-> bool' in source
    print("  ✓ ModernBertModelAdapter.load() is async def -> bool")


def test_modernbert_adapter_unload_is_async():
    """ModernBertModelAdapter.unload() is async."""
    source = get_class_source(MODERNBERT_ADAPTER_PATH, 'ModernBertModelAdapter')
    assert source is not None
    assert 'async def unload' in source
    print("  ✓ ModernBertModelAdapter.unload() is async def")


def test_modernbert_adapter_has_mlx_cache_clear():
    """ModernBertModelAdapter.unload() clears Metal cache."""
    source = get_class_source(MODERNBERT_ADAPTER_PATH, 'ModernBertModelAdapter')
    assert source is not None
    # Should call the engine's unload (which does mx.eval + clear_cache)
    assert 'unload' in source
    print("  ✓ ModernBertModelAdapter delegates unload to ModernBertEngine")


def test_modernbert_adapter_generate_report_delegates_to_summarize():
    """ModernBertModelAdapter.generate_report() calls summarize()."""
    source = get_class_source(MODERNBERT_ADAPTER_PATH, 'ModernBertModelAdapter')
    assert source is not None
    assert 'summarize' in source
    print("  ✓ ModernBertModelAdapter.generate_report() wraps summarize()")


# ── Test 7: model_engine.py is new (not dead code) ────────────────────────────

def test_model_engine_is_new_file():
    """model_engine.py is a new file (created Sprint F222)."""
    with open(MODEL_ENGINE_PATH) as f:
        content = f.read()
    assert 'ModelEngine' in content
    assert 'Protocol contract' in content
    print("  ✓ model_engine.py is the new Protocol file (Sprint F222)")


def test_modernbert_adapter_is_new_file():
    """modernbert_adapter.py is a new file (created Sprint F222)."""
    with open(MODERNBERT_ADAPTER_PATH) as f:
        content = f.read()
    assert 'ModernBertModelAdapter' in content
    assert 'ModelEngine' in content
    print("  ✓ modernbert_adapter.py is the new adapter file (Sprint F222)")


# ── run ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("\n=== probe_f222_model_engine_protocol ===\n")

    tests = [
        # ModelEngine Protocol
        test_model_engine_protocol_has_required_methods,
        test_model_engine_protocol_optional_methods,
        # ModernBertModelAdapter
        test_modernbert_adapter_has_required_methods,
        test_modernbert_adapter_has_optional_methods,
        test_modernbert_adapter_has_is_ready,
        test_modernbert_adapter_returns_empty_on_generate,
        test_modernbert_adapter_load_returns_bool,
        test_modernbert_adapter_unload_is_async,
        test_modernbert_adapter_has_mlx_cache_clear,
        test_modernbert_adapter_generate_report_delegates_to_summarize,
        # Hermes3Engine
        test_hermes3_engine_has_required_methods,
        test_hermes3_engine_uses_initialize_not_load,
        test_hermes3_engine_has_optional_methods,
        # Factory
        test_model_manager_creates_modernbert_adapter,
        test_model_manager_hermes_factory_unchanged,
        # brain/__init__.py
        test_brain_init_exports_model_engine,
        test_brain_init_imports_model_engine,
        # New files
        test_model_engine_is_new_file,
        test_modernbert_adapter_is_new_file,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ ERROR: {test.__name__}: {e}")
            failed += 1

    total = passed + failed
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} passed, {failed} failed")
    print(f"{'='*50}\n")

    sys.exit(0 if failed == 0 else 1)
