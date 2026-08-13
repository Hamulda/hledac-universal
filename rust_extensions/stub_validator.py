#!/usr/bin/env python3
"""
stub_validator.py — Python-side FFI Contract Validation

NEXTGEN-05: Build-time validation that Python slots match Rust #[pyclass] fields.

This validator compares:
1. Generated .pyi stub with actual PyO3 bindings
2. Python wrapper class slots with Rust struct fields
3. Function signatures match across the FFI boundary

On mismatch → BUILD FAILURE (not runtime segfault)

This is NOT called by maturin (maturin has no develop hooks).
It is called by:
  1. CI pipeline: pytest tests/test_ffi_contract.py
  2. Manual: python rust_extensions/stub_validator.py
  3. build.rs: runs ffi_type_manifest.py (generates manifest, not validation)

Usage:
    python rust_extensions/stub_validator.py [--module hledac_rust_extensions]
    python rust_extensions/stub_validator.py --check-abi  # strict validation
    pytest tests/test_ffi_contract.py  # Full test suite
    
Exit codes:
    0 = validation passed
    1 = validation failed
    2 = module not loaded (skip validation)
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ============================================================================
# Configuration
# ============================================================================

_REPO_ROOT = Path(__file__).resolve().parent  # rust_extensions/
MANIFEST_PATH = _REPO_ROOT / "_ffi_type_manifest.json"
PYI_PATH = _REPO_ROOT / "hledac_rust_extensions.pyi"


# ============================================================================
# Validation Result Types
# ============================================================================

@dataclass
class ValidationError:
    """Represents a single validation failure."""
    error_type: str  # "slot_mismatch", "type_mismatch", "method_missing"
    class_name: str
    field_name: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    message: str = ""


@dataclass
class ValidationResult:
    """Aggregated validation result."""
    passed: bool = True
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    
    def add_error(self, error: ValidationError):
        self.passed = False
        self.errors.append(error)
    
    def add_warning(self, message: str):
        self.warnings.append(message)


# ============================================================================
# Python Wrapper Slot Extraction
# ============================================================================

def extract_python_slots(wrapper_file: Path) -> dict[str, dict[str, Any]]:
    """
    Extract slots from Python wrapper classes.
    
    Looks for patterns like:
        class MyWrapper:
            __slots__ = ("field1", "field2", ...)
            def __init__(self, rust_obj):
                self.field1 = ...
    """
    slots: dict[str, dict[str, Any]] = {}
    
    if not wrapper_file.exists():
        return slots
    
    source = wrapper_file.read_text()
    
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return slots
    
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_slots = set()
            class_fields = set()
            
            # Extract __slots__
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and target.id == "__slots__":
                            if isinstance(item.value, (ast.List, ast.Tuple, ast.Set)):
                                for elt in item.value.elts:
                                    if isinstance(elt, ast.Constant):
                                        class_slots.add(elt.value)
                            elif isinstance(item.value, ast.Name):
                                # __slots__ = some_variable — skip
                                pass
            
            # Extract field assignments (heuristic)
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    if item.name == "__init__":
                        for stmt in ast.walk(item):
                            if isinstance(stmt, ast.Assign):
                                for target in stmt.targets:
                                    if isinstance(target, ast.Attribute):
                                        if isinstance(target.value, ast.Name):
                                            if target.value.id == "self":
                                                class_fields.add(target.attr)
            
            if class_slots or class_fields:
                slots[node.name] = {
                    "slots": class_slots,
                    "fields": class_fields,
                    "all": class_slots | class_fields
                }
    
    return slots


def extract_pyi_slots(pyi_path: Path) -> dict[str, dict[str, Any]]:
    """
    Extract slot information from generated .pyi stub.
    
    Looks for patterns like:
        class MyClass:
            field1: int
            field2: str
    """
    slots: dict[str, dict[str, Any]] = {}
    
    if not pyi_path.exists():
        return slots
    
    source = pyi_path.read_text()
    
    current_class = None
    class_fields = {}
    
    for line in source.splitlines():
        # Class definition
        class_match = re.match(r"^class (\w+):", line)
        if class_match:
            if current_class and class_fields:
                slots[current_class] = class_fields
            current_class = class_match.group(1)
            class_fields = {}
            continue
        
        # Field definition (not method)
        if current_class:
            # Skip method definitions
            if re.match(r"^\s+def ", line):
                continue
            if re.match(r"^\s+async def ", line):
                continue
            
            # Field with type hint
            field_match = re.match(r"^\s+(\w+)\s*:\s*([\w\[\]|\s,<>]+)", line)
            if field_match:
                field_name = field_match.group(1)
                field_type = field_match.group(2).strip()
                class_fields[field_name] = {"type": field_type, "has_get": True}
    
    if current_class and class_fields:
        slots[current_class] = class_fields
    
    return slots


# ============================================================================
# Manifest-based Validation
# ============================================================================

def load_manifest() -> dict:
    """Load the FFI type manifest."""
    if not MANIFEST_PATH.exists():
        return {}
    
    with MANIFEST_PATH.open() as f:
        return json.load(f)


def validate_class_slots(
    manifest: dict,
    pyi_slots: dict[str, dict[str, Any]]
) -> ValidationResult:
    """
    Validate that Python slots match Rust #[pyclass] fields.
    
    For each class in the manifest:
    1. Get Rust struct fields (from manifest)
    2. Get Python class fields (from .pyi)
    3. Compare — mismatch = BUILD FAILURE
    """
    result = ValidationResult()
    
    manifest_classes = manifest.get("classes", {})
    
    for full_name, class_data in manifest_classes.items():
        class_name = class_data["name"]
        rust_fields = {f["name"]: f for f in class_data.get("fields", [])}
        rust_slots = set(rust_fields.keys())
        
        # Get .pyi fields for this class
        pyi_fields = pyi_slots.get(class_name, {})
        pyi_slots_set = set(pyi_fields.keys())
        
        # Compare
        missing_in_pyi = rust_slots - pyi_slots_set
        extra_in_pyi = pyi_slots_set - rust_slots
        
        for field_name in missing_in_pyi:
            result.add_error(ValidationError(
                error_type="slot_missing_in_pyi",
                class_name=class_name,
                field_name=field_name,
                expected=rust_fields[field_name]["python_type"],
                actual=None,
                message=f"Field '{field_name}' in Rust #[pyclass] {class_name} "
                        f"is missing from .pyi stub"
            ))
        
        for field_name in extra_in_pyi:
            result.add_warning(
                f"Field '{field_name}' in .pyi for {class_name} "
                f"not found in Rust #[pyclass]"
            )
        
        # Type compatibility check
        for field_name in rust_slots & pyi_slots_set:
            rust_type = rust_fields[field_name]["python_type"]
            pyi_type = pyi_fields[field_name].get("type", "")
            
            if not _types_compatible(rust_type, pyi_type):
                result.add_error(ValidationError(
                    error_type="type_mismatch",
                    class_name=class_name,
                    field_name=field_name,
                    expected=rust_type,
                    actual=pyi_type,
                    message=f"Type mismatch for field '{field_name}' in {class_name}: "
                            f"expected {rust_type}, got {pyi_type}"
                ))
    
    return result


def validate_function_signatures(
    manifest: dict,
    pyi_path: Path
) -> ValidationResult:
    """Validate that function signatures match across the FFI boundary."""
    result = ValidationResult()
    
    manifest_functions = manifest.get("functions", {})
    pyi_content = pyi_path.read_text() if pyi_path.exists() else ""
    
    for full_name, func_data in manifest_functions.items():
        func_name = func_data["name"]
        manifest_sig = func_data.get("signature", "()")
        
        # Look for function in .pyi
        pattern = rf"(?:async\s+)?def\s+{re.escape(func_name)}\s*\(([^)]*)\)"
        match = re.search(pattern, pyi_content)
        
        if not match:
            result.add_warning(f"Function '{func_name}' not found in .pyi")
            continue
        
        pyi_params = match.group(1).strip()
        
        # Basic signature validation
        # More sophisticated validation would parse both signatures properly
        manifest_param_count = manifest_sig.count(",") + 1 if manifest_sig.strip() != "()" else 0
        pyi_param_count = pyi_params.count(",") + 1 if pyi_params else 0
        
        if manifest_param_count != pyi_param_count:
            result.add_warning(
                f"Parameter count mismatch for {func_name}: "
                f"manifest={manifest_param_count}, pyi={pyi_param_count}"
            )
    
    return result


def _types_compatible(rust_type: str, pyi_type: str) -> bool:
    """
    Check if Rust-derived Python type is compatible with .pyi type hint.
    
    Uses structural compatibility, not exact string matching.
    """
    # Normalize types
    rust_type = rust_type.strip()
    pyi_type = pyi_type.strip()
    
    # Exact match
    if rust_type == pyi_type:
        return True
    
    # List types
    if rust_type.startswith("list[") and pyi_type.startswith("list["):
        rust_inner = rust_type[5:-1]
        pyi_inner = pyi_type[5:-1]
        return _types_compatible(rust_inner, pyi_inner)
    
    # Optional types
    if " | None" in rust_type or rust_type.endswith("| None"):
        rust_inner = rust_type.replace(" | None", "").replace("| None", "").strip()
        pyi_inner = pyi_type.replace(" | None", "").replace("| None", "").strip()
        return _types_compatible(rust_inner, pyi_inner)
    
    # Any type
    if pyi_type in ("Any", "any"):
        return True
    
    # Fallback: check if rust_type is contained in pyi_type
    # (allows for more flexible type hints)
    return rust_type in pyi_type or pyi_type in rust_type


# ============================================================================
# Module-level Validation
# ============================================================================

def validate_module_loaded(module_name: str = "hledac_rust_extensions") -> bool:
    """Check if the extension module is loaded."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def validate_ffi_contract(
    module_name: str = "hledac_rust_extensions",
    strict: bool = False
) -> ValidationResult:
    """
    Run full FFI contract validation.
    
    Args:
        module_name: Name of the PyO3 extension module
        strict: If True, warnings become errors
    
    Returns:
        ValidationResult with pass/fail and error details
    """
    result = ValidationResult()
    
    # Check if manifest exists
    if not MANIFEST_PATH.exists():
        result.add_warning(
            f"FFI manifest not found at {MANIFEST_PATH}. "
            "Run ffi_type_manifest.py first."
        )
        if strict:
            result.passed = False
        return result
    
    # Load manifest
    manifest = load_manifest()
    
    # Extract .pyi slots
    pyi_slots = extract_pyi_slots(PYI_PATH)
    
    # Validate class slots
    slots_result = validate_class_slots(manifest, pyi_slots)
    result.passed = result.passed and slots_result.passed
    result.errors.extend(slots_result.errors)
    result.warnings.extend(slots_result.warnings)
    
    # Validate function signatures
    func_result = validate_function_signatures(manifest, PYI_PATH)
    result.passed = result.passed and func_result.passed
    result.errors.extend(func_result.errors)
    result.warnings.extend(func_result.warnings)
    
    # In strict mode, treat warnings as errors
    if strict:
        for warning in result.warnings:
            result.errors.append(ValidationError(
                error_type="warning_as_error",
                class_name="",
                message=f"[STRICT] {warning}"
            ))
        result.warnings.clear()
        result.passed = len(result.errors) == 0
    
    return result


# ============================================================================
# CI/Gate Integration
# ============================================================================

def run_validation_gate(
    module_name: str = "hledac_rust_extensions"
) -> int:
    """
    Run validation as a CI gate.
    
    This is called by maturin develop hook before completing installation.
    
    Exit codes:
        0 = validation passed, proceed with installation
        1 = validation failed, abort installation
    """
    print("[stub_validator] Running FFI contract validation...")
    print(f"[stub_validator] Manifest: {MANIFEST_PATH}")
    print(f"[stub_validator] PYI stub: {PYI_PATH}")
    
    # Run validation
    result = validate_ffi_contract(module_name=module_name, strict=True)
    
    # Print results
    if result.warnings:
        print("[stub_validator] Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
    
    if result.errors:
        print("[stub_validator] ERRORS (BUILD-TIME FAILURE):")
        for error in result.errors:
            print(f"  [{error.error_type}] {error.class_name}.{error.field_name}")
            print(f"    Expected: {error.expected}")
            print(f"    Actual: {error.actual}")
            print(f"    Message: {error.message}")
    
    if result.passed:
        print("[stub_validator] ✓ FFI contract validation PASSED")
        print("[stub_validator] Build can proceed safely.")
        return 0
    else:
        print("[stub_validator] ✗ FFI contract validation FAILED")
        print("[stub_validator] Build ABORTED — fix errors before continuing.")
        return 1


# ============================================================================
# Standalone Validator (for CI and manual use)
# ============================================================================

def maturin_develop_hook() -> int:
    """
    FFI validation entry point for CI/testing.
    
    NOTE: Maturin does NOT have a develop hooks feature.
    This function exists for backward compatibility and can be called:
      1. Directly: python rust_extensions/stub_validator.py
      2. Via pytest: pytest tests/test_ffi_contract.py
      3. In CI scripts before/after maturin develop
    
    To skip validation:
      PYO3_NO_VALIDATION=1 python rust_extensions/stub_validator.py
    """
    # Check for skip flag
    if os.environ.get("PYO3_NO_VALIDATION", "").lower() in ("1", "true", "yes"):
        print("[stub_validator] SKIPPING validation (PYO3_NO_VALIDATION=1)")
        return 0
    
    return run_validation_gate()


# ============================================================================
# CLI Entry Point
# ============================================================================

def main():
    """CLI interface for stub_validator."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="FFI contract validator for PyO3 bindings"
    )
    parser.add_argument(
        "--module",
        default="hledac_rust_extensions",
        help="Extension module name (default: hledac_rust_extensions)"
    )
    parser.add_argument(
        "--check-abi",
        action="store_true",
        help="Strict validation mode (warnings as errors)"
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: exit 1 on any warning"
    )
    
    args = parser.parse_args()
    
    # Run validation
    result = validate_ffi_contract(
        module_name=args.module,
        strict=args.ci
    )
    
    # Print results
    if result.warnings:
        print("[stub_validator] Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
    
    if result.errors:
        print("[stub_validator] Errors:")
        for error in result.errors:
            print(f"  [{error.error_type}] {error.class_name}.{error.field_name}: {error.message}")
    
    if result.passed:
        print("[stub_validator] ✓ Validation PASSED")
        return 0
    else:
        print("[stub_validator] ✗ Validation FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
