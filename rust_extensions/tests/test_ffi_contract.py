#!/usr/bin/env python3
"""
test_ffi_contract.py — NEXTGEN-05: FFI Contract Validation Tests

Tests for build-time FFI type-safety validation system:
1. Manifest generation from Rust source
2. .pyi stub generation
3. Type compatibility checking
4. Slot matching between Rust #[pyclass] and Python wrappers

Run:
    pytest tests/test_ffi_contract.py -v
    python rust_extensions/stub_validator.py  # Manual validation
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# Add rust_extensions to path for imports
RUST_EXT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(RUST_EXT_DIR))

import pytest

# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture(scope="module")
def repo_root() -> Path:
    """Get the rust_extensions directory."""
    return Path(__file__).parent.parent


@pytest.fixture(scope="module")
def manifest_path(repo_root: Path) -> Path:
    """Path to FFI type manifest."""
    return repo_root / "_ffi_type_manifest.json"


@pytest.fixture(scope="module")
def pyi_path(repo_root: Path) -> Path:
    """Path to generated .pyi stub."""
    return repo_root / "hledac_rust_extensions.pyi"


@pytest.fixture(scope="module")
def manifest(manifest_path: Path) -> dict[str, Any]:
    """Load FFI type manifest."""
    if not manifest_path.exists():
        pytest.skip(f"Manifest not found: {manifest_path}")
    with manifest_path.open() as f:
        return json.load(f)


@pytest.fixture(scope="module")
def pyi_content(pyi_path: Path) -> str:
    """Load .pyi stub content."""
    if not pyi_path.exists():
        pytest.skip(f"PYI stub not found: {pyi_path}")
    return pyi_path.read_text()


# ============================================================================
# Test: Manifest Generation
# ============================================================================


class TestManifestGeneration:
    """Tests for FFI manifest generation."""

    def test_manifest_exists(self, manifest_path: Path) -> None:
        """Manifest file should exist after build."""
        assert manifest_path.exists(), f"Manifest not found: {manifest_path}"

    def test_manifest_version(self, manifest: dict[str, Any]) -> None:
        """Manifest should have correct version."""
        assert manifest.get("version") == "1.0.0"
        assert manifest.get("capability") == "NEXTGEN-05"

    def test_manifest_has_classes(self, manifest: dict[str, Any]) -> None:
        """Manifest should contain classes section."""
        assert "classes" in manifest
        assert isinstance(manifest["classes"], dict)

    def test_manifest_has_functions(self, manifest: dict[str, Any]) -> None:
        """Manifest should contain functions section."""
        assert "functions" in manifest
        assert isinstance(manifest["functions"], dict)

    def test_manifest_has_registrations(self, manifest: dict[str, Any]) -> None:
        """Manifest should contain registrations section."""
        assert "registrations" in manifest
        assert isinstance(manifest["registrations"], dict)

    def test_class_structure(self, manifest: dict[str, Any]) -> None:
        """Each class should have required fields."""
        for _full_name, class_data in manifest.get("classes", {}).items():
            assert "name" in class_data
            assert "module" in class_data
            assert "fields" in class_data
            assert isinstance(class_data["fields"], list)

    def test_function_structure(self, manifest: dict[str, Any]) -> None:
        """Each function should have required fields."""
        for _full_name, func_data in manifest.get("functions", {}).items():
            assert "name" in func_data
            assert "module" in func_data
            assert "signature" in func_data
            assert "return_type" in func_data

    def test_field_has_rust_and_python_types(self, manifest: dict[str, Any]) -> None:
        """Each field should have both Rust and Python types."""
        for _full_name, class_data in manifest.get("classes", {}).items():
            for field_data in class_data.get("fields", []):
                assert "name" in field_data
                assert "rust_type" in field_data
                assert "python_type" in field_data
                assert "has_get" in field_data
                assert "has_set" in field_data


# ============================================================================
# Test: .pyi Stub Generation
# ============================================================================


class TestPyiGeneration:
    """Tests for .pyi stub generation."""

    def test_pyi_exists(self, pyi_path: Path) -> None:
        """PYI stub should exist after build."""
        assert pyi_path.exists(), f"PYI stub not found: {pyi_path}"

    def test_pyi_has_auto_generation_comment(self, pyi_content: str) -> None:
        """PYI should have auto-generation header."""
        assert "AUTO-GENERATED" in pyi_content
        assert "ffi_type_manifest.py" in pyi_content

    def test_pyi_imports(self, pyi_content: str) -> None:
        """PYI should have required imports."""
        assert "from typing import Any" in pyi_content or "Any" in pyi_content

    def test_pyi_has_classes(self, pyi_content: str, manifest: dict[str, Any]) -> None:
        """PYI should define all classes from manifest."""
        for full_name in manifest.get("classes", {}):
            class_name = full_name.split(".")[-1]
            assert f"class {class_name}:" in pyi_content

    def test_pyi_has_slots(self, pyi_content: str, manifest: dict[str, Any]) -> None:
        """PYI classes should have __slots__ for FFI safety."""
        slot_count = pyi_content.count("__slots__")
        classes_with_slots = sum(1 for c in manifest.get("classes", {}).values() if c.get("slots"))
        assert slot_count > 0, "No __slots__ found in .pyi"
        assert slot_count >= classes_with_slots, f"Expected at least {classes_with_slots} __slots__, found {slot_count}"

    def test_pyi_field_definitions(self, pyi_content: str, manifest: dict[str, Any]) -> None:
        """PYI should define fields with type hints."""
        for full_name, class_data in manifest.get("classes", {}).items():
            class_name = full_name.split(".")[-1]
            for field_data in class_data.get("fields", []):
                if field_data.get("has_get"):
                    field_name = field_data["name"]
                    # Field should be defined in class body
                    assert f"    {field_name}:" in pyi_content or f"{field_name}:" in pyi_content, (
                        f"Field {field_name} not found in {class_name}"
                    )


# ============================================================================
# Test: Type Compatibility
# ============================================================================


class TestTypeCompatibility:
    """Tests for type compatibility between Rust and Python."""

    def test_bool_type_mapping(self) -> None:
        """Rust bool should map to Python bool."""
        from ffi_type_manifest import rust_type_to_python

        assert rust_type_to_python("bool") == "bool"

    def test_string_type_mapping(self) -> None:
        """Rust String/&str should map to Python str."""
        from ffi_type_manifest import rust_type_to_python

        assert rust_type_to_python("String") == "str"
        assert rust_type_to_python("&str") == "str"

    def test_integer_type_mapping(self) -> None:
        """Rust integers should map to Python int."""
        from ffi_type_manifest import rust_type_to_python

        assert rust_type_to_python("i32") == "int"
        assert rust_type_to_python("i64") == "int"
        assert rust_type_to_python("usize") == "int"

    def test_float_type_mapping(self) -> None:
        """Rust floats should map to Python float."""
        from ffi_type_manifest import rust_type_to_python

        assert rust_type_to_python("f32") == "float"
        assert rust_type_to_python("f64") == "float"

    def test_vec_bytes_mapping(self) -> None:
        """Rust Vec<u8> should map to Python bytes."""
        from ffi_type_manifest import rust_type_to_python

        assert rust_type_to_python("Vec<u8>") == "bytes"

    def test_vec_string_mapping(self) -> None:
        """Rust Vec<String> should map to Python list[str]."""
        from ffi_type_manifest import rust_type_to_python

        assert rust_type_to_python("Vec<String>") == "list[str]"

    def test_option_type_mapping(self) -> None:
        """Rust Option<T> should map to T | None."""
        from ffi_type_manifest import rust_type_to_python

        assert "None" in rust_type_to_python("Option<String>")
        assert "str" in rust_type_to_python("Option<String>")

    def test_tuple_type_mapping(self) -> None:
        """Rust tuples should map to Python tuples."""
        from ffi_type_manifest import rust_type_to_python

        result = rust_type_to_python("(String, f64)")
        assert "tuple" in result
        assert "str" in result
        assert "float" in result


# ============================================================================
# Test: Slot Validation
# ============================================================================


class TestPyClassSlots:
    """Tests for PyClass slot validation."""

    def test_all_get_fields_are_slots(self, manifest: dict[str, Any]) -> None:
        """Fields with #[pyo3(get)] should be in slots list."""
        for full_name, class_data in manifest.get("classes", {}).items():
            get_fields = {f["name"] for f in class_data["fields"] if f.get("has_get")}
            slot_fields = set(class_data.get("slots", []))
            assert get_fields.issubset(slot_fields), f"{full_name}: get fields {get_fields - slot_fields} not in slots"

    def test_slots_are_get_fields(self, manifest: dict[str, Any]) -> None:
        """Slots should only contain fields that have get."""
        for full_name, class_data in manifest.get("classes", {}).items():
            slots = set(class_data.get("slots", []))
            all_fields = {f["name"] for f in class_data["fields"]}
            assert slots.issubset(all_fields), f"{full_name}: slots {slots - all_fields} not in field definitions"

    def test_pyi_slots_match_manifest(self, pyi_content: str, manifest: dict[str, Any]) -> None:
        """PYI __slots__ should match manifest slots."""
        import re

        for full_name, class_data in manifest.get("classes", {}).items():
            class_name = full_name.split(".")[-1]
            manifest_slots = set(class_data.get("slots", []))

            if not manifest_slots:
                continue

            # Find class in PYI and extract its __slots__
            class_pattern = rf"class {class_name}:(.*?)(?=class |\Z)"
            match = re.search(class_pattern, pyi_content, re.DOTALL)
            if match:
                class_body = match.group(1)
                slots_match = re.search(r"__slots__.*?=\s*\(([^)]+)\)", class_body)
                if slots_match:
                    pyi_slots_str = slots_match.group(1)
                    # Extract quoted slot names
                    pyi_slots = set(re.findall(r'"(\w+)"', pyi_slots_str))
                    assert manifest_slots == pyi_slots, (
                        f"{class_name}: slots mismatch. Manifest: {manifest_slots}, PYI: {pyi_slots}"
                    )


# ============================================================================
# Test: CLI Validation
# ============================================================================


class TestCLI:
    """Tests for stub_validator CLI."""

    def test_validator_runs(self, repo_root: Path) -> None:
        """Validator should run without errors."""
        result = subprocess.run([sys.executable, "stub_validator.py"], cwd=repo_root, capture_output=True, text=True)
        # Should not crash (exit code 0 or 1 is acceptable)
        assert result.returncode in (0, 1), f"Validator crashed: {result.stderr}"

    def test_validator_with_strict_mode(self, repo_root: Path) -> None:
        """Validator should accept --ci flag."""
        result = subprocess.run(
            [sys.executable, "stub_validator.py", "--ci"], cwd=repo_root, capture_output=True, text=True
        )
        assert result.returncode in (0, 1), f"Validator crashed: {result.stderr}"


# ============================================================================
# Test: Build Integration
# ============================================================================


class TestBuildIntegration:
    """Tests for build-time integration."""

    def test_ffi_manifest_generated_at_build_time(self, repo_root: Path) -> None:
        """Manifest should be generated during build (by build.rs)."""
        # Check that build.rs contains ffi_type_manifest call
        build_rs = repo_root / "build.rs"
        assert build_rs.exists()
        content = build_rs.read_text()
        assert "ffi_type_manifest" in content

    def test_manifest_regeneration_on_rust_change(self, repo_root: Path) -> None:
        """build.rs should rerun on Rust source changes."""
        build_rs = repo_root / "build.rs"
        content = build_rs.read_text()
        assert "cargo:rerun-if-changed=src/" in content
        assert "cargo:rerun-if-changed=src/lib.rs" in content


# ============================================================================
# Test: Validation Rules
# ============================================================================


class TestValidationRules:
    """Tests for validation rule configuration."""

    def test_validation_rules_present(self, manifest: dict[str, Any]) -> None:
        """Manifest should have validation rules."""
        rules = manifest.get("validation_rules", {})
        assert rules.get("slots_match") is True
        assert rules.get("types_match") is True
        assert rules.get("fail_on_mismatch") is True


# ============================================================================
# Test: Comprehensive Type Mapping
# ============================================================================


class TestTypeMappingCompleteness:
    """Tests for type mapping coverage."""

    def test_common_types_mapped(self) -> None:
        """All common Rust types should have Python mappings."""
        from ffi_type_manifest import RUST_TO_PYTHON_TYPE

        required_types = [
            "bool",
            "i32",
            "i64",
            "u32",
            "u64",
            "usize",
            "f32",
            "f64",
            "String",
            "&str",
            "Vec<u8>",
            "Vec<String>",
            "Option<String>",
            "Option<usize>",
        ]

        for rust_type in required_types:
            assert rust_type in RUST_TO_PYTHON_TYPE, f"Missing type mapping for {rust_type}"

    def test_type_mapping_function(self) -> None:
        """rust_type_to_python should handle all mapped types."""
        from ffi_type_manifest import RUST_TO_PYTHON_TYPE, rust_type_to_python

        for rust_type in RUST_TO_PYTHON_TYPE:
            result = rust_type_to_python(rust_type)
            assert isinstance(result, str)
            assert len(result) > 0


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
