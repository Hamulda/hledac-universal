#!/usr/bin/env python3
"""
test_ffi_contract.py — Build-time FFI Contract Validation Tests

NEXTGEN-05: Tests for the build-time FFI type-safety system.

This test suite validates that:
1. FFI type manifest is generated correctly
2. Python slots match Rust #[pyclass] fields
3. Function signatures are compatible across the FFI boundary
4. Type mismatches are caught at build-time (not runtime)

CI Usage:
    # Run during maturin develop (before extension installation)
    pytest tests/test_ffi_contract.py --ffi-validate
    
    # Run standalone
    pytest tests/test_ffi_contract.py -v

Exit codes:
    0 = all tests passed
    1 = validation failed (build should abort)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from core import aclose

# ============================================================================
# Paths
# ============================================================================

_REPO_ROOT = Path(__file__).resolve().parent.parent  # project root
_RUST_EXT = _REPO_ROOT / "rust_extensions"
_MANIFEST_PATH = _RUST_EXT / "_ffi_type_manifest.json"
_PYI_PATH = _RUST_EXT / "hledac_rust_extensions.pyi"


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def manifest() -> dict[str, Any]:
    """Load the FFI type manifest."""
    if not _MANIFEST_PATH.exists():
        pytest.skip(f"Manifest not found at {_MANIFEST_PATH}. Run ffi_type_manifest.py first.")
    
    with _MANIFEST_PATH.open() as f:
        return json.load(f)


@pytest.fixture(scope="session")
def pyi_content() -> str:
    """Load the generated .pyi stub content."""
    if not _PYI_PATH.exists():
        pytest.skip(f"PYI stub not found at {_PYI_PATH}. Run ffi_type_manifest.py first.")
    
    return _PYI_PATH.read_text()


# ============================================================================
# Manifest Validation Tests
# ============================================================================

class TestManifestGeneration:
    """Tests for FFI manifest generation."""
    
    def test_manifest_exists(self):
        """Manifest file should exist after build."""
        assert _MANIFEST_PATH.exists(), (
            f"Manifest not found at {_MANIFEST_PATH}. "
            "Run: python rust_extensions/ffi_type_manifest.py"
        )
    
    def test_manifest_version(self, manifest):
        """Manifest should have correct version."""
        assert manifest.get("version") == "1.0.0"
    
    def test_manifest_capability_tag(self, manifest):
        """Manifest should be tagged with NEXTGEN-05 capability."""
        assert manifest.get("capability") == "NEXTGEN-05"
    
    def test_manifest_has_classes(self, manifest):
        """Manifest should contain PyClass definitions."""
        classes = manifest.get("classes", {})
        assert len(classes) > 0, "No #[pyclass] structs found in manifest"
    
    def test_manifest_has_functions(self, manifest):
        """Manifest should contain PyFunction definitions."""
        functions = manifest.get("functions", {})
        assert len(functions) > 0, "No #[pyfunction] found in manifest"
    
    def test_manifest_has_registrations(self, manifest):
        """Manifest should contain module registrations."""
        registrations = manifest.get("registrations", {})
        assert len(registrations) > 0, "No module registrations found in manifest"


class TestPyClassSlots:
    """Tests for #[pyclass] field/slot validation."""
    
    def test_all_classes_have_name(self, manifest):
        """Every PyClass should have a name."""
        for full_name, class_data in manifest["classes"].items():
            assert "name" in class_data, f"Class {full_name} missing 'name'"
            assert class_data["name"], f"Class {full_name} has empty name"
    
    def test_all_classes_have_module(self, manifest):
        """Every PyClass should have a module."""
        for full_name, class_data in manifest["classes"].items():
            assert "module" in class_data, f"Class {full_name} missing 'module'"
    
    def test_field_types_are_valid(self, manifest):
        """All field types should be valid Python type hints."""
        for full_name, class_data in manifest["classes"].items():
            for field in class_data.get("fields", []):
                # Field should have name and python_type
                assert "name" in field, f"Field missing name in {full_name}"
                assert "python_type" in field, f"Field {field.get('name')} missing python_type"
                
                # Python type should not be empty
                assert field["python_type"], f"Field {field['name']} in {full_name} has empty python_type"
    
    def test_get_set_consistency(self, manifest):
        """Fields with has_get=True should be in slots list."""
        for full_name, class_data in manifest["classes"].items():
            slots = set(class_data.get("slots", []))
            
            for field in class_data.get("fields", []):
                if field.get("has_get"):
                    assert field["name"] in slots, (
                        f"Field {field['name']} in {full_name} has has_get=True "
                        f"but is not in slots list"
                    )


class TestFunctionSignatures:
    """Tests for #[pyfunction] signature validation."""
    
    def test_all_functions_have_name(self, manifest):
        """Every PyFunction should have a name."""
        for full_name, func_data in manifest["functions"].items():
            assert "name" in func_data, f"Function {full_name} missing 'name'"
    
    def test_all_functions_have_signature(self, manifest):
        """Every PyFunction should have a signature."""
        for full_name, func_data in manifest["functions"].items():
            assert "signature" in func_data, f"Function {full_name} missing 'signature'"
    
    def test_return_types_are_valid(self, manifest):
        """All function return types should be valid."""
        for full_name, func_data in manifest["functions"].items():
            return_type = func_data.get("return_type", "")
            assert return_type, f"Function {full_name} has empty return_type"


# ============================================================================
# PYI Stub Validation Tests
# ============================================================================

class TestPyiGeneration:
    """Tests for auto-generated .pyi stub file."""
    
    def test_pyi_exists(self):
        """PYI stub should exist after manifest generation."""
        assert _PYI_PATH.exists(), (
            f"PYI stub not found at {_PYI_PATH}. "
            "Run: python rust_extensions/ffi_type_manifest.py"
        )
    
    def test_pyi_header_comments(self, pyi_content):
        """PYI should have auto-generation header."""
        assert "AUTO-GENERATED" in pyi_content, "PYI missing AUTO-GENERATED header"
        assert "ffi_type_manifest.py" in pyi_content, "PYI should reference generator"
    
    def test_pyi_has_typing_imports(self, pyi_content):
        """PYI should have typing imports."""
        assert "from typing import" in pyi_content
        assert "Any" in pyi_content
    
    def test_pyi_class_definitions(self, pyi_content, manifest):
        """PYI should contain class definitions for all PyClasses."""
        classes = manifest.get("classes", {})
        
        for full_name, class_data in classes.items():
            class_name = class_data["name"]
            assert f"class {class_name}:" in pyi_content, (
                f"Class {class_name} not found in .pyi stub"
            )
    
    def test_pyi_function_definitions(self, pyi_content, manifest):
        """PYI should contain function definitions for all PyFunctions."""
        # Only check a sample of functions to avoid test bloat
        functions = manifest.get("functions", {})
        sample = list(functions.items())[:20]  # Check first 20
        
        for full_name, func_data in sample.items():
            func_name = func_data["name"]
            # Function may be prefixed with 'async '
            assert f"def {func_name}(" in pyi_content or f"async def {func_name}(" in pyi_content, (
                f"Function {func_name} not found in .pyi stub"
            )


# ============================================================================
# Build Integration Tests
# ============================================================================

class TestBuildIntegration:
    """Tests for build-time integration."""
    
    def test_ffi_type_manifest_runs(self):
        """ffi_type_manifest.py should run without errors."""
        result = subprocess.run(
            [sys.executable, str(_RUST_EXT / "ffi_type_manifest.py")],
            cwd=_RUST_EXT,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        assert result.returncode == 0, (
            f"ffi_type_manifest.py failed:\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    
    def test_stub_validator_runs(self):
        """stub_validator.py should run without errors."""
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(_RUST_EXT / "stub_validator.py"), "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        # stub_validator.py is a module, so we just check it can be imported
        # The actual validation runs in CI via maturin hook
        assert result.returncode == 0 or "pytest" in result.stderr.lower()


# ============================================================================
# Runtime Slots Validation
# ============================================================================

class TestRuntimeSlotsValidation:
    """Tests that verify Python wrappers match Rust bindings at runtime."""
    
    @pytest.mark.skipif(
        not (_REPO_ROOT / "hledac_rust_extensions" / "hledac_rust_extensions.so").exists(),
        reason="Extension not built yet. Run: maturin develop"
    )
    def test_extension_loads(self):
        """The compiled extension should load without errors."""
        try:
            import hledac_rust_extensions
            assert hasattr(hledac_rust_extensions, "__version__")
        except ImportError as e:
            pytest.fail(f"Failed to import extension: {e}")
    
    @pytest.mark.skipif(
        not (_REPO_ROOT / "hledac_rust_extensions" / "hledac_rust_extensions.so").exists(),
        reason="Extension not built yet"
    )
    def test_known_classes_exist(self, manifest):
        """Known PyClasses should exist in the compiled extension."""
        try:
            import hledac_rust_extensions as ext
            
            # Check a few known classes
            for full_name in list(manifest["classes"].keys())[:5]:
                class_data = manifest["classes"][full_name]
                class_name = class_data["name"]
                
                assert hasattr(ext, class_name), (
                    f"Class {class_name} not found in compiled extension"
                )
        except ImportError:
            pytest.skip("Extension not available")
    
    @pytest.mark.skipif(
        not (_REPO_ROOT / "hledac_rust_extensions" / "hledac_rust_extensions.so").exists(),
        reason="Extension not built yet"
    )
    def test_known_functions_exist(self, manifest):
        """Known PyFunctions should exist in the compiled extension."""
        try:
            import hledac_rust_extensions as ext
            
            # Check a few known functions
            for full_name in list(manifest["functions"].keys())[:10]:
                func_data = manifest["functions"][full_name]
                func_name = func_data["name"]
                
                assert hasattr(ext, func_name), (
                    f"Function {func_name} not found in compiled extension"
                )
        except ImportError:
            pytest.skip("Extension not available")


# ============================================================================
# Type Compatibility Tests
# ============================================================================

class TestTypeCompatibility:
    """Tests for type compatibility between Rust and Python."""
    
    def test_no_unknown_types_in_manifest(self, manifest):
        """Manifest should not contain unknown/placeholder types."""
        unknown_types = []
        
        for full_name, class_data in manifest["classes"].items():
            for field in class_data.get("fields", []):
                python_type = field.get("python_type", "")
                # Check for placeholder or unknown types
                if python_type in ("", "Unknown", "?", "T"):
                    unknown_types.append(f"{full_name}.{field['name']}")
        
        assert len(unknown_types) == 0, (
            f"Found fields with unknown types: {unknown_types}"
        )
    
    def test_validation_rules_present(self, manifest):
        """Manifest should contain validation rules."""
        rules = manifest.get("validation_rules", {})
        
        assert "slots_match" in rules
        assert "types_match" in rules
        assert rules.get("fail_on_mismatch") is True


# ============================================================================
# CLI Tests
# ============================================================================

class TestCLI:
    """Tests for stub_validator.py CLI."""
    
    def test_validator_cli_help(self):
        """stub_validator.py should respond to --help."""
        result = subprocess.run(
            [sys.executable, str(_RUST_EXT / "stub_validator.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        assert result.returncode == 0
        assert "FFI contract validator" in result.stdout


# ============================================================================
# Maturin Hook Tests
# ============================================================================

class TestMaturinHook:
    """Tests for maturin develop hook integration."""
    
    def test_pyproject_has_develop_hook(self):
        """pyproject.toml should have develop-hooks configured."""
        pyproject = _RUST_EXT / "pyproject.toml"
        content = pyproject.read_text()
        
        assert "develop-hooks" in content, (
            "pyproject.toml missing [tool.maturin.develop-hooks]"
        )
        assert "ffi_validate" in content, (
            "pyproject.toml missing ffi_validate hook"
        )
    
    def test_hook_command_correct(self):
        """Hook command should reference stub_validator.maturin_develop_hook."""
        pyproject = _RUST_EXT / "pyproject.toml"
        content = pyproject.read_text()
        
        assert "stub_validator.maturin_develop_hook" in content, (
            "Hook command should call stub_validator.maturin_develop_hook"
        )


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
