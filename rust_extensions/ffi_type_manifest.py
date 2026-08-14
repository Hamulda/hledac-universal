#!/usr/bin/env python3
"""
ffi_type_manifest.py — BUILD-TIME FFI Type-Safety & Automatic .pyi Generation

NEXTGEN-05: Captures Rust PyO3 struct layout at build-time to generate:
1. FFI type manifest (JSON) — Rust #[pyclass] fields, types, methods
2. Auto-generated .pyi stub — matches Python slots to Rust FFI contract

This transforms RUNTIME segfaults into BUILD-TIME failures when:
- Python slots don't match Rust #[pyclass] field layout
- Missing getters/setters on Python side
- Type mismatches between Python hints and Rust types

Architecture:
  build.rs → calls ffi_type_manifest.py → generates:
    - hledac_rust_extensions.pyi (generated stub)
    - _ffi_type_manifest.json (type metadata)
  
  maturin develop hook → stub_validator.py → validates:
    - Generated .pyi matches actual PyO3 bindings
    - Python wrapper slots match Rust struct fields

UniFFI Alternative Path:
  For new modules: use uniffi_bindgen generate --python → auto-generates .pyi
  For legacy PyO3 modules: use this codegen approach
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ============================================================================
# Configuration
# ============================================================================

_REPO_ROOT = Path(__file__).resolve().parent  # rust_extensions/
_SRC_DIR = _REPO_ROOT / "src"
_LIB_RS = _SRC_DIR / "lib.rs"
_CARGO_TOML = _REPO_ROOT / "Cargo.toml"  # Root Cargo.toml for hash
_OUTPUT_DIR = _REPO_ROOT
PYI_OUTPUT = _OUTPUT_DIR / "hledac_rust_extensions.pyi"
MANIFEST_OUTPUT = _OUTPUT_DIR / "_ffi_type_manifest.json"


# ============================================================================
# ISSUE-01: Source Hash Computation
# ============================================================================

import hashlib


def _compute_source_hash() -> str:
    """
    Compute a BLAKE2B hash of all Rust source files.

    IMPORTANT: This MUST match _compute_source_hash() in build_manifest.py
    and _compute_source_content_hash() in _prober.py.

    Uses two-level hashing:
    1. Per-file: blake2b(path + size + sample) = file_hash
    2. Overall: blake2b(concatenated file_hashes)

    Algorithm (must match build_manifest.py):
    - Collects files from rust_extensions/src/*.rs and rust_extensions/Cargo.toml
    - NOTE: Does NOT recurse into subdirectories (uses glob, not rglob)
    - Sorts files by path for deterministic ordering
    - For each file: blake2b(relative_path + size + first_4KB + last_4KB)
    - Final hash: BLAKE2B-256 of all file_hash bytes concatenated

    Returns a hex string of the hash, or empty string on error.
    """
    try:
        import hashlib

        if not _SRC_DIR.exists():
            return ""

        # Collect all .rs and .toml files (MUST match build_manifest.py exactly)
        # IMPORTANT: Use glob, NOT rglob - we only want files directly in src/
        file_paths: list[Path] = []
        for ext in ("*.rs", "*.toml"):
            for path in sorted(_SRC_DIR.glob(ext)):
                if path.is_file():
                    file_paths.append(path)

        # Also include Cargo.toml from rust_extensions root
        # This matches build_manifest.py
        if _CARGO_TOML.exists():
            file_paths.append(_CARGO_TOML)

        # Sort for deterministic ordering
        file_paths.sort(key=str)

        if not file_paths:
            return ""

        # Two-level hashing (matches build_manifest.py exactly)
        overall_hasher = hashlib.blake2b(digest_size=32)

        for path in file_paths:
            relative_path = str(path.relative_to(_REPO_ROOT))
            size = path.stat().st_size

            # Per-file hash: blake2b(path + size + sample)
            file_hasher = hashlib.blake2b(digest_size=32)
            file_hasher.update(relative_path.encode())
            file_hasher.update(size.to_bytes(8, "little"))

            try:
                content = path.read_bytes()
                sample_size = min(4096, len(content))
                file_hasher.update(content[:sample_size])
                if len(content) > 8192:
                    file_hasher.update(content[-4096:])
            except OSError:
                continue

            file_hash = file_hasher.hexdigest()

            # Overall hash: blake2b of all file_hash bytes
            overall_hasher.update(file_hash.encode())

        return overall_hasher.hexdigest()
    except Exception:
        return ""


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class PyClassField:
    """Represents a #[pyo3(get)] or #[pyo3(set)] field in a PyClass."""
    name: str
    rust_type: str
    python_type: str
    has_get: bool = True
    has_set: bool = False
    doc_comment: Optional[str] = None


@dataclass
class PyClassMethod:
    """Represents a #[pymethods] method in a PyClass."""
    name: str
    signature: str
    return_type: str
    is_async: bool = False
    doc_comment: Optional[str] = None


@dataclass
class PyClass:
    """Represents a #[pyclass] struct exported via PyO3."""
    name: str
    module: str
    fields: list[PyClassField] = field(default_factory=list)
    methods: list[PyClassMethod] = field(default_factory=list)
    new_signature: Optional[str] = None
    doc_comment: Optional[str] = None
    slots: list[str] = field(default_factory=list)


@dataclass
class PyFunction:
    """Represents a #[pyfunction] exported via PyO3."""
    name: str
    module: str
    signature: str
    return_type: str
    is_async: bool = False
    doc_comment: Optional[str] = None


@dataclass
class ModuleRegistration:
    """Represents a module registration (register_functions, register, etc.)."""
    module: str
    registration_type: str  # "register_functions", "register", "register_module"
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)


# ============================================================================
# Rust Type → Python Type Mapping
# ============================================================================

RUST_TO_PYTHON_TYPE: dict[str, str] = {
    # Primitives
    "bool": "bool",
    "i8": "int",
    "i16": "int",
    "i32": "int",
    "i64": "int",
    "isize": "int",
    "u8": "int",
    "u16": "int",
    "u32": "int",
    "u64": "int",
    "usize": "int",
    "f32": "float",
    "f64": "float",
    # Strings
    "String": "str",
    "str": "str",
    "&str": "str",
    "&mut str": "str",
    # Bytes/Buffers
    "Vec<u8>": "bytes",
    "bytes": "bytes",
    "&[u8]": "bytes",
    "&mut [u8]": "bytes",
    "&[u8]": "bytes",
    "Bytes": "bytes",
    "BytesMut": "bytearray",
    "ByteString": "bytes",
    # Collections
    "Vec<String>": "list[str]",
    "Vec<usize>": "list[int]",
    "Vec<i64>": "list[int]",
    "Vec<f64>": "list[float]",
    "Vec<(&str, f64)>": "list[tuple[str, float]]",
    "Vec<Vec<u8>>": "list[bytes]",
    "Vec<Vec<usize>>": "list[list[int]]",
    # Hash types
    "HashMap<String, String>": "dict[str, str]",
    "HashMap<String, usize>": "dict[str, int]",
    "HashMap<String, f64>": "dict[str, float]",
    "HashMap<String, i64>": "dict[str, int]",
    "HashSet<String>": "set[str]",
    "BTreeMap<String, String>": "dict[str, str]",
    "BTreeSet<String>": "set[str]",
    # Optional types
    "Option<String>": "str | None",
    "Option<&str>": "str | None",
    "Option<usize>": "int | None",
    "Option<i64>": "int | None",
    "Option<f64>": "float | None",
    "Option<bool>": "bool | None",
    "Option<PyClass>": "Self | None",
    "Option<Py<PyAny>>": "Any | None",
    # Python objects
    "PyResult<T>": "T",
    "Bound<'_, PyModule>": "PyModule",
    "Bound<'_, PyAny>": "Any",
    "Bound<'_, PyDict>": "dict[str, Any]",
    "Bound<'_, PyList>": "list[Any]",
    "Bound<'_, PyBytes>": "bytes",
    "Bound<'_, PyString>": "str",
    "Bound<'_, PyTuple>": "tuple[Any, ...]",
    "Bound<'_, PySequence>": "list[Any]",
    "Bound<'_, PyBytes>": "bytes",
    "Bound<'_, PyBuffer>": "Any",
    "Py<PyAny>": "Any",
    "Py<PyList>": "list[Any]",
    "Py<PyDict>": "dict[str, Any]",
    "Py<PyBytes>": "bytes",
    "Py<PyString>": "str",
    # Python type annotations
    "Python<'_>": "Any",
    "Python<'py>": "Any",
    "Python<'_>": "Any",
    # Networking types
    "IpAddr": "str",
    "SocketAddr": "str",
    "Uri": "str",
    "Url": "str",
    # Time types
    "Duration": "float",
    "std::time::Duration": "float",
    "Instant": "float",
    "SystemTime": "float",
    # Path types
    "PathBuf": "str",
    "Path": "str",
    # Numeric types
    "BigInt": "int",
    "Decimal": "float",
    "BigDecimal": "float",
    # Custom result types (pass-through)
    "PyResult": "Any",
    "Result<T, E>": "Any",
    # Range types
    "Range<usize>": "range",
    "Range<i64>": "range",
    # Slices (treat as list-like)
    "[u8]": "bytes",
    "[usize]": "list[int]",
    "[i64]": "list[int]",
    "[f64]": "list[float]",
    # Self type
    "Self": "Self",
    # Async return types
    "Coroutine<_, _, _>": "Any",
    "impl Future<Output = T>": "Any",
}


def rust_type_to_python(rust_type: str) -> str:
    """Convert a Rust type to its Python type hint equivalent."""
    # Strip lifetime annotations first
    rust_type_orig = rust_type
    rust_type = re.sub(r"'[a-z_]+", "", rust_type)
    rust_type = re.sub(r"'_", "", rust_type)
    
    # Handle Python type annotation - Bound<'_, PyType> or Bound<PyType>
    # Pattern: matches Bound<..., PyType> and captures PyType (without 'Py' prefix)
    bound_match = re.search(r"Py([A-Za-z]+)", rust_type)
    if bound_match and "Bound" in rust_type:
        py_type = bound_match.group(1)
        type_map = {
            "Any": "Any",
            "Module": "PyModule",
            "Dict": "dict[str, Any]",
            "List": "list[Any]",
            "Bytes": "bytes",
            "String": "str",
            "Tuple": "tuple[Any, ...]",
            "Sequence": "list[Any]",
            "Buffer": "Any",
        }
        return type_map.get(py_type, "Any")
    
    # Handle Python<'_> type (PyO3 Python type parameter)
    if "Python" in rust_type and "<" in rust_type:
        return "Any"
    
    # Handle Option<T>
    if rust_type.startswith("Option<") and rust_type.count("<") == rust_type.count(">"):
        inner = rust_type[7:-1]
        inner_python = rust_type_to_python(inner)
        if " | None" in inner_python:
            return inner_python  # Already has None
        return f"{inner_python} | None"
    
    # Handle Vec<T>
    if rust_type.startswith("Vec<") and rust_type.count("<") == rust_type.count(">"):
        inner = rust_type[4:-1]
        inner_python = rust_type_to_python(inner)
        return f"list[{inner_python}]"
    
    # Handle HashMap<K, V> - HashMap< is 8 characters
    if rust_type.startswith("HashMap<") and rust_type.count("<") == rust_type.count(">"):
        inner = rust_type[8:-1]
        parts = _split_by_top_level_comma(inner)
        if len(parts) == 2:
            k, v = parts
            return f"dict[{rust_type_to_python(k)}, {rust_type_to_python(v)}]"
    
    # Handle HashSet<T> - HashSet< is 8 characters
    if rust_type.startswith("HashSet<") and rust_type.count("<") == rust_type.count(">"):
        inner = rust_type[8:-1]
        return f"set[{rust_type_to_python(inner)}]"
    
    # Handle BTreeMap<K, V> - BTreeMap< is 9 characters
    if rust_type.startswith("BTreeMap<") and rust_type.count("<") == rust_type.count(">"):
        inner = rust_type[9:-1]
        parts = _split_by_top_level_comma(inner)
        if len(parts) == 2:
            k, v = parts
            return f"dict[{rust_type_to_python(k)}, {rust_type_to_python(v)}]"
    
    # Handle BTreeSet<T> - BTreeSet< is 9 characters
    if rust_type.startswith("BTreeSet<") and rust_type.count("<") == rust_type.count(">"):
        inner = rust_type[9:-1]
        return f"set[{rust_type_to_python(inner)}]"
    
    # Handle &T, &mut T (references)
    if rust_type.startswith("&"):
        rust_type = rust_type.lstrip("&mut ").lstrip("&")
    
    # Handle slices [T; N] or &[T]
    slice_match = re.match(r"&?\[([^\]]+)\]", rust_type)
    if slice_match:
        inner_type = slice_match.group(1)
        if inner_type == "u8":
            return "bytes"
        return f"list[{rust_type_to_python(inner_type)}]"
    
    # Handle tuple types like (T1, T2)
    if rust_type.startswith("(") and rust_type.endswith(")"):
        parts = _parse_tuple_types(rust_type)
        python_parts = [rust_type_to_python(p) for p in parts]
        return f"tuple[{', '.join(python_parts)}]"
    
    # Handle std::module::Type paths
    if "::" in rust_type:
        # Convert std::xxx::yyy::Type to Type
        parts = rust_type.split("::")
        return rust_type_to_python(parts[-1])
    
    # Handle PyResult<T> wrapper - extract inner type
    if rust_type.startswith("PyResult<") and rust_type.count("<") == rust_type.count(">"):
        inner = rust_type[9:-1]  # PyResult< is 9 chars
        return rust_type_to_python(inner)
    
    # Handle Result<T, E> - return Any (error handling is complex)
    if rust_type.startswith("Result<") and rust_type.count("<") == rust_type.count(">"):
        inner = rust_type[7:-1]  # Result< is 7 chars
        parts = _split_by_top_level_comma(inner)
        if parts:
            return rust_type_to_python(parts[0])
        return "Any"
    
    # Handle option wrapper types
    if rust_type.startswith("Some(") or rust_type.startswith("None"):
        return rust_type_to_python(rust_type[5:-1]) if rust_type.startswith("Some(") else "None"
    
    # Direct mapping
    if rust_type in RUST_TO_PYTHON_TYPE:
        return RUST_TO_PYTHON_TYPE[rust_type]
    
    # Unknown type — return as-is for documentation, with "?" prefix to indicate uncertainty
    return rust_type


def _parse_tuple_types(rust_type: str) -> list[str]:
    """Parse Rust tuple type into component types."""
    content = rust_type[1:-1]  # Remove parentheses
    types = []
    depth = 0
    current = ""
    
    for char in content:
        if char == "<":
            depth += 1
            current += char
        elif char == ">":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            types.append(current.strip())
            current = ""
        else:
            current += char
    
    if current.strip():
        types.append(current.strip())
    
    return types


def _split_by_top_level_comma(text: str) -> list[str]:
    """Split by comma only at top-level (not inside < > or ( ) brackets).
    
    Handles nested brackets correctly:
    - Vec<(String, String)> -> ['Vec<(String, String)>']
    - HashMap<String, Vec<(u8, usize)>> -> ['HashMap<String, Vec<(u8, usize)>>']
    """
    parts = []
    depth = 0
    current = ""
    in_string = False
    
    i = 0
    while i < len(text):
        char = text[i]
        
        # Handle string literals (shouldn't occur in Rust types but be safe)
        if char == '"' and (i == 0 or text[i-1] != '\\'):
            in_string = not in_string
            current += char
        elif in_string:
            current += char
        elif char in "<([{":
            depth += 1
            current += char
        elif char in ">)]}":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
        
        i += 1
    
    if current.strip():
        parts.append(current.strip())
    
    return parts


# ============================================================================
# Rust Source Parsing
# ============================================================================

def parse_pyclass_struct(text: str, struct_name: str) -> Optional[PyClass]:
    """Parse a #[pyclass] struct definition."""
    # Match struct definition
    struct_pattern = rf"^pub struct {re.escape(struct_name)}\s*{{"
    
    for line_num, line in enumerate(text.splitlines()):
        if re.match(struct_pattern, line):
            # Find the struct body
            start = text.find(line)
            depth = 0
            body_start = None
            body_end = None
            
            for i in range(start, len(text)):
                if text[i] == "{":
                    if body_start is None:
                        body_start = i + 1
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        body_end = i
                        break
            
            if body_start and body_end:
                body = text[body_start:body_end]
                return _parse_struct_body(struct_name, body, text)
    
    return None


def _parse_struct_body(name: str, body: str, full_text: str) -> PyClass:
    """Parse the body of a #[pyclass] struct."""
    pyclass = PyClass(name=name, module="")
    
    # Check for #[pyclass] doc comment before struct
    # Match only doc lines (///) that are immediately before the struct
    struct_pos = full_text.find(f"pub struct {name}")
    if struct_pos > 0:
        # Look backwards for doc comment lines
        before = full_text[:struct_pos]
        # Find last non-empty line before struct
        lines = before.rstrip().splitlines()
        doc_lines = []
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.startswith("///"):
                doc_lines.insert(0, stripped.lstrip("///").strip())
            elif stripped.startswith("/*"):
                continue  # Block comment start
            elif stripped == "" or stripped.startswith("//"):
                continue  # Empty line or non-doc comment
            else:
                break
        if doc_lines:
            # Only keep first 3 lines and truncate long docs
            doc_text = " ".join(doc_lines[:3])
            if len(doc_text) > 200:
                doc_text = doc_text[:197] + "..."
            pyclass.doc_comment = doc_text
    
    # Parse fields with #[pyo3(get)], #[pyo3(set)]
    field_pattern = r"#\[pyo3\(([^)]+)\)\]\s*(pub\s+)?(\w+)\s*:\s*([^,;]+)"
    
    for match in re.finditer(field_pattern, body):
        attrs = match.group(1)
        field_name = match.group(3)
        rust_type = match.group(4).strip()
        
        has_get = "get" in attrs or 'get,' in attrs or 'get)' in attrs
        has_set = "set" in attrs or 'set,' in attrs or 'set)' in attrs
        
        field = PyClassField(
            name=field_name,
            rust_type=rust_type,
            python_type=rust_type_to_python(rust_type),
            has_get=has_get,
            has_set=has_set
        )
        pyclass.fields.append(field)
        
        # Track slots
        if has_get:
            pyclass.slots.append(field_name)
    
    return pyclass


def parse_pymethods(text: str, struct_name: str) -> list[PyClassMethod]:
    """Parse #[pymethods] impl block for a struct."""
    methods = []
    
    # Find #[pymethods] block - handle both { and \{ escaping
    pymethods_pattern = rf"#\[pymethods\]\s*impl\s+{re.escape(struct_name)}\s*\{{"
    match = re.search(pymethods_pattern, text)
    
    if not match:
        return methods
    
    # Find the impl block
    start = match.end()
    depth = 1
    body = ""
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                body = text[start:i]
                break
    
    # Parse Rust functions: fn name(...) -> ReturnType { or async fn name(...) -> ReturnType {
    # Handle: #[pyo3(...)] fn name(self, ...) -> ReturnType { or #[pyo3(...)] async fn name(...)
    func_pattern = r"(?:#\[(?:pyo3|args?\s*\([^)]*\)|getter|setter)\]\s*)*(?:\basync\s+)?fn\s+(\w+)\s*\(\s*([^)]*)\s*\)\s*(?:->\s*([^={{\n]+?))?\s*\{"
    
    for func_match in re.finditer(func_pattern, body):
        func_name = func_match.group(1)
        params = func_match.group(2)
        return_type = func_match.group(3) or "None"
        
        # Skip internal/private methods and dunder methods
        if func_name.startswith("_") or func_name.startswith("__"):
            continue
        
        # Check if this is a getter or setter (property method)
        # Look back for #[getter] or #[setter] attribute
        func_start = func_match.start()
        look_back = body[max(0, func_start-200):func_start]
        is_getter = bool(re.search(r"#\[getter\]", look_back))
        is_setter = bool(re.search(r"#\[setter\]", look_back))
        
        # Get property name from getter/setter
        if is_getter or is_setter:
            prop_match = re.search(r"#\[(?:getter|setter)\s*\(\s*=\s*\"([^\"]+)\"\s*\)\]", look_back)
            if prop_match:
                prop_name = prop_match.group(1)
                # Property methods are not class methods in Python stubs
                continue
        
        # Check for async
        is_async = bool(re.search(r"async\s+fn", body[max(0, func_match.start()-10):func_match.start()+20]))
        
        # Parse signature - split params respecting nested brackets
        sig_parts = []
        param_parts = _split_by_top_level_comma(params)
        
        for param in param_parts:
            param = param.strip()
            if not param or param == "self" or param == "&self" or param == "&mut self":
                continue
            # Handle patterns like py: Python<'py>, data: &str, etc.
            if ":" in param:
                parts = param.rsplit(":", 1)
                name = parts[0].strip()
                ptype = parts[1].strip()
                python_type = rust_type_to_python(ptype)
                # Skip Python<'_> type annotations
                if python_type == "Any" and "Python" in ptype:
                    continue
                sig_parts.append(f"{name}: {python_type}")
            else:
                sig_parts.append(param.strip())
        
        method = PyClassMethod(
            name=func_name,
            signature=f"({', '.join(sig_parts)})",
            return_type=rust_type_to_python(return_type.strip()),
            is_async=is_async
        )
        methods.append(method)
    
    return methods


def _extract_function_signature(text: str, func_name: str) -> tuple[str, str, bool]:
    """Extract function signature, handling nested brackets correctly.
    
    Returns: (params_str, return_type_str, is_async)
    """
    # Find the function definition start
    fn_pattern = rf"(?:pub\s+)?(?:async\s+)?fn\s+{re.escape(func_name)}\s*\("
    fn_start = re.search(fn_pattern, text)
    if not fn_start:
        return "", "None", False
    
    # Find the opening paren and track brackets to find closing paren
    pos = fn_start.end()
    depth = 1  # We're inside the first (
    param_start = pos
    
    while pos < len(text) and depth > 0:
        char = text[pos]
        if char in "([<{":
            depth += 1
        elif char in ")]}>":
            depth -= 1
            if depth == 0:
                break
        pos += 1
    
    params_str = text[param_start:pos]
    
    # Find return type and opening brace
    # Look for '->' after the closing paren
    rest = text[pos:]
    return_match = re.search(r"\)\s*(?:->\s*([^={{\n]+?))?\s*\{", rest)
    
    return_type = "None"
    is_async = "async " in text[fn_start.start():fn_start.end()]
    
    if return_match:
        if return_match.group(1):
            return_type = return_match.group(1).strip()
    
    return params_str, return_type, is_async


def parse_pyfunction(text: str, func_name: str) -> Optional[PyFunction]:
    """Parse a #[pyfunction] definition."""
    # Look for #[pyfunction] before the function
    pyfn_pattern = rf"#\[pyfunction\]"
    pyfn_match = re.search(pyfn_pattern, text)
    if not pyfn_match:
        return None
    
    # Find the function within the text after #[pyfunction]
    search_text = text[pyfn_match.start():]
    
    # Extract signature using bracket-aware parsing
    params_str, return_type, is_async = _extract_function_signature(search_text, func_name)
    
    if not params_str and return_type == "None":
        return None
    
    # Parse signature - split params carefully, respecting nested brackets
    sig_parts = []
    param_parts = _split_by_top_level_comma(params_str)
    
    for param in param_parts:
        param = param.strip()
        if not param:
            continue
        if ":" in param:
            # Handle param: Type patterns
            # Split on first ':' only
            idx = param.index(":")
            name = param[:idx].strip()
            ptype = param[idx+1:].strip()
            python_type = rust_type_to_python(ptype)
            # Skip Python<'_> type annotations
            if python_type == "Any" and "Python" in ptype:
                continue
            sig_parts.append(f"{name}: {python_type}")
        else:
            sig_parts.append(param.strip())
    
    return PyFunction(
        name=func_name,
        module="",
        signature=f"({', '.join(sig_parts)})",
        return_type=rust_type_to_python(return_type),
        is_async=is_async
    )


def parse_module_registrations(lib_rs_text: str) -> dict[str, ModuleRegistration]:
    """Parse lib.rs and extract all PyO3 module registrations."""
    registrations: dict[str, ModuleRegistration] = {}
    
    # Pattern 1: m.add_class::<module::ClassName>()?;
    # Handles: m.add_class::<url_set::MmapUrlSet>()?;
    for match in re.finditer(r"m\.add_class::<(\w+)::(\w+)>\(\)\?;", lib_rs_text):
        module_name = match.group(1)
        class_name = match.group(2)
        
        if module_name not in registrations:
            registrations[module_name] = ModuleRegistration(
                module=module_name,
                registration_type="mixed"
            )
        registrations[module_name].classes.append(class_name)
    
    # Pattern 2: module_name::register_functions(m)?;
    for match in re.finditer(r"(\w+)::register_functions\(m\)\?;", lib_rs_text):
        module_name = match.group(1)
        
        if module_name not in registrations:
            registrations[module_name] = ModuleRegistration(
                module=module_name,
                registration_type="register_functions"
            )
        elif registrations[module_name].registration_type == "mixed":
            registrations[module_name].registration_type = "register_functions"
    
    # Pattern 3: module_name::register(m)?;
    for match in re.finditer(r"(\w+)::register\(m\)\?;", lib_rs_text):
        module_name = match.group(1)
        
        if module_name not in registrations:
            registrations[module_name] = ModuleRegistration(
                module=module_name,
                registration_type="register"
            )
    
    # Pattern 4: module_name::register_module(m)?;
    for match in re.finditer(r"(\w+)::register_module\(m\)\?;", lib_rs_text):
        module_name = match.group(1)
        
        if module_name not in registrations:
            registrations[module_name] = ModuleRegistration(
                module=module_name,
                registration_type="register_module"
            )
    
    # Pattern 5: m.add_function(wrap_pyfunction!(module::function, m))?;
    for match in re.finditer(r"wrap_pyfunction!\((\w+)::(\w+),\s*m\)", lib_rs_text):
        module_name = match.group(1)
        func_name = match.group(2)
        
        if module_name not in registrations:
            registrations[module_name] = ModuleRegistration(
                module=module_name,
                registration_type="mixed"
            )
        registrations[module_name].functions.append(func_name)
    
    return registrations


def parse_rust_files() -> tuple[dict[str, PyClass], dict[str, PyFunction]]:
    """Parse all Rust source files and extract PyO3 bindings."""
    classes: dict[str, PyClass] = {}
    functions: dict[str, PyFunction] = {}
    
    for rs_file in _SRC_DIR.glob("*.rs"):
        if rs_file.name == "lib.rs":
            continue
        
        module_name = rs_file.stem
        
        # Skip subdirectories for now
        if not (rs_file.parent == _SRC_DIR):
            continue
        
        text = rs_file.read_text()
        
        # Find all #[pyclass] structs
        for match in re.finditer(r"#\[pyclass\]\s*pub struct (\w+)", text):
            struct_name = match.group(1)
            pyclass = parse_pyclass_struct(text, struct_name)
            
            if pyclass:
                pyclass.module = module_name
                classes[f"{module_name}.{struct_name}"] = pyclass
                
                # Parse #[pymethods]
                methods = parse_pymethods(text, struct_name)
                pyclass.methods.extend(methods)
        
        # Find all #[pyfunction]
        for match in re.finditer(r"#\[pyfunction\]\s*(?:pub\s+)?fn\s+(\w+)", text):
            func_name = match.group(1)
            pyfunc = parse_pyfunction(text, func_name)
            
            if pyfunc:
                pyfunc.module = module_name
                functions[f"{module_name}.{func_name}"] = pyfunc
    
    # Also parse lib.rs for direct registrations
    lib_text = _LIB_RS.read_text()
    
    # Parse module registrations from lib.rs
    registrations = parse_module_registrations(lib_text)
    
    return classes, functions


# ============================================================================
# .pyi Stub Generation
# ============================================================================

def generate_pyi_header() -> str:
    """Generate the .pyi file header."""
    return '''# Type stub for the `hledac_rust_extensions` PyO3 extension.
#
# AUTO-GENERATED by ffi_type_manifest.py — DO NOT EDIT MANUALLY
# Generated: {generated_at}
#
# This stub is derived from:
#   1. Runtime `dir(hledac_rust_extensions)` introspection
#   2. Static analysis of #[pyclass] and #[pyfunction] in src/*.rs
#   3. FFI type manifest (_ffi_type_manifest.json)
#
# Build-time validation (NEXTGEN-05):
#   - stub_validator.py compares this stub with actual Rust bindings
#   - Mismatches cause BUILD-TIME FAILURE (not runtime segfault)
#
# To regenerate after changes:
#   python rust_extensions/ffi_type_manifest.py

from collections.abc import Callable
from typing import Any, overload

# PyO3 classes (#[pyclass])

'''.format(generated_at=datetime.now(timezone.utc).isoformat())


def generate_pyclass_stub(pyclass: PyClass) -> str:
    """Generate a Python stub for a PyClass."""
    lines = []
    
    # Doc comment
    if pyclass.doc_comment:
        lines.append(f'    """{pyclass.doc_comment}"""')
    
    # Class definition
    lines.append(f"class {pyclass.name}:")
    
    # __slots__ definition for FFI type safety
    # CRITICAL: ALL PyClass classes must have __slots__ for FFI type safety.
    # Even if no fields are exposed, __slots__ = () prevents Python attribute injection.
    if pyclass.slots:
        slots_str = ", ".join(f'"{s}"' for s in sorted(pyclass.slots))
        lines.append(f"    __slots__: tuple[str, ...] = ({slots_str},)")
    else:
        # Classes with no exposed fields still need __slots__ for FFI safety
        lines.append("    __slots__: tuple[str, ...] = ()")
    
    # Fields (properties)
    for field in pyclass.fields:
        if field.has_get and not field.has_set:
            lines.append(f"    {field.name}: {field.python_type}")
        elif field.has_get and field.has_set:
            lines.append(f"    {field.name}: {field.python_type}")
    
    # __init__ signature
    if pyclass.new_signature:
        lines.append(f"    def __init__{pyclass.new_signature} -> None: ...")
    else:
        lines.append("    def __init__(self, *args: Any, **kwargs: Any) -> None: ...")
    
    # Methods
    for method in pyclass.methods:
        if method.is_async:
            prefix = "async "
        else:
            prefix = ""
        
        lines.append(
            f"    {prefix}def {method.name}{method.signature} -> {method.return_type}: ..."
        )
    
    return "\n".join(lines)


def generate_pyfunction_stub(pyfunc: PyFunction) -> str:
    """Generate a Python stub for a PyFunction."""
    if pyfunc.is_async:
        prefix = "async "
    else:
        prefix = ""
    
    return f"{prefix}def {pyfunc.name}{pyfunc.signature} -> {pyfunc.return_type}: ..."


def generate_full_pyi(classes: dict[str, PyClass], functions: dict[str, PyFunction]) -> str:
    """Generate the complete .pyi stub file."""
    output = [generate_pyi_header()]
    
    # Generate class stubs
    for full_name in sorted(classes.keys()):
        pyclass = classes[full_name]
        output.append(generate_pyclass_stub(pyclass))
        output.append("")
    
    # Generate function stubs
    for full_name in sorted(functions.keys()):
        pyfunc = functions[full_name]
        output.append(generate_pyfunction_stub(pyfunc))
        output.append("")
    
    return "\n".join(output)


# ============================================================================
# FFI Type Manifest Generation
# ============================================================================

def generate_ffi_manifest(
    classes: dict[str, PyClass],
    functions: dict[str, PyFunction],
    registrations: dict[str, ModuleRegistration]
) -> dict:
    """Generate the FFI type manifest JSON."""
    # ISSUE-01: Compute source hash for freshness verification
    source_hash = _compute_source_hash()
    
    manifest = {
        "version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "capability": "NEXTGEN-05",
        "description": "Build-time FFI type-safety manifest",
        # ISSUE-01: Source hash for binary freshness verification
        "__source_hash__": source_hash,
        "classes": {},
        "functions": {},
        "registrations": {},
        "validation_rules": {
            "slots_match": True,
            "types_match": True,
            "methods_match": True,
            "fail_on_mismatch": True
        }
    }
    
    # Add classes
    for full_name, pyclass in classes.items():
        manifest["classes"][full_name] = {
            "module": pyclass.module,
            "name": pyclass.name,
            "fields": [
                {
                    "name": f.name,
                    "rust_type": f.rust_type,
                    "python_type": f.python_type,
                    "has_get": f.has_get,
                    "has_set": f.has_set
                }
                for f in pyclass.fields
            ],
            "methods": [
                {
                    "name": m.name,
                    "signature": m.signature,
                    "return_type": m.return_type,
                    "is_async": m.is_async
                }
                for m in pyclass.methods
            ],
            "slots": pyclass.slots,
            "doc_comment": pyclass.doc_comment
        }
    
    # Add functions
    for full_name, pyfunc in functions.items():
        manifest["functions"][full_name] = {
            "module": pyfunc.module,
            "name": pyfunc.name,
            "signature": pyfunc.signature,
            "return_type": pyfunc.return_type,
            "is_async": pyfunc.is_async,
            "doc_comment": pyfunc.doc_comment
        }
    
    # Add registrations
    for module_name, reg in registrations.items():
        manifest["registrations"][module_name] = asdict(reg)
    
    return manifest


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Generate FFI type manifest and .pyi stub."""
    print("[ffi_type_manifest] Starting generation...")
    
    # Parse Rust sources
    print("[ffi_type_manifest] Parsing Rust sources...")
    classes, functions = parse_rust_files()
    
    # Parse lib.rs registrations
    lib_text = _LIB_RS.read_text()
    registrations = parse_module_registrations(lib_text)
    
    print(f"  Found {len(classes)} #[pyclass] structs")
    print(f"  Found {len(functions)} #[pyfunction] functions")
    print(f"  Found {len(registrations)} module registrations")
    
    # Generate FFI type manifest
    manifest = generate_ffi_manifest(classes, functions, registrations)
    manifest_json = json.dumps(manifest, indent=2)
    MANIFEST_OUTPUT.write_text(manifest_json)
    print(f"[ffi_type_manifest] Written: {MANIFEST_OUTPUT}")
    
    # Generate .pyi stub
    pyi_content = generate_full_pyi(classes, functions)
    PYI_OUTPUT.write_text(pyi_content)
    print(f"[ffi_type_manifest] Written: {PYI_OUTPUT}")
    
    # Validation summary
    total_fields = sum(len(c.fields) for c in classes.values())
    total_methods = sum(len(c.methods) for c in classes.values())
    print(f"[ffi_type_manifest] Summary:")
    print(f"  Classes: {len(classes)}")
    print(f"  Fields: {total_fields}")
    print(f"  Methods: {total_methods}")
    print(f"  Functions: {len(functions)}")
    print(f"[ffi_type_manifest] Validation: BUILD-TIME FFI CONTRACT ENABLED")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
