#!/usr/bin/env python3
"""
build_ffi_manifest.py — FFI symbol manifest generator for hledac-rust-extensions.

Generates core/rust_backend/_ffi_manifest.json from lib.rs static analysis.
This manifest is the authoritative list of PyO3 symbols that Rust exports.

Output: _ffi_manifest.json with schema:

    {
      "version": "1.0.0",
      "generated_at": "<ISO8601>",
      "git_rev": "<git-rev>",
      "lib_rs_symbols": {<module>: [<symbol>, ...]},
      "all_lib_rs": [<symbol>, ...]
    }

CI usage:
    python rust_extensions/build_ffi_manifest.py [--check]
        --check: exit 1 if manifest is stale (lib.rs changed but manifest not regenerated)

Python-side usage in core/rust_backend.__init__:
    manifest = json.loads((ABI_DIR / "_ffi_manifest.json").read_text())
    rust_symbols = set(manifest["all_lib_rs"])
    # compare with self._ext.* calls...
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from _core import aclose

# rust_extensions/  →  project root
_REPO_ROOT = Path(__file__).resolve().parent  # .../hledac/universal/rust_extensions
REPO_ROOT = _REPO_ROOT.parent  # .../hledac/universal
ABI_OUT = REPO_ROOT / "core" / "rust_backend"
MANIFEST_PATH = ABI_OUT / "_ffi_manifest.json"
LIB_RS_PATH = _REPO_ROOT / "src" / "lib.rs"


# ---------------------------------------------------------------------------
# Static analysis — parse lib.rs
# ---------------------------------------------------------------------------

def _extract_class_name(line: str) -> tuple[str, str] | None:
    """
    Parse a 'm.add_class::<...::ClassName>()?;' line.

    Returns (module_name, class_name) or None if not an add_class line.
    Example: 'm.add_class::<bloom::BloomFilter>()?;' → ('bloom', 'BloomFilter')
    """
    gt = line.rfind("<")
    gt2 = line.find(">", gt)
    if gt < 0 or gt2 < 0:
        return None
    path = line[gt + 1:gt2]  # e.g. "bloom::BloomFilter"
    segments = path.split("::")
    if len(segments) < 2:
        return None
    module = segments[0]
    class_name = segments[-1]
    return (module, class_name)


def _extract_register_functions_body(module_name: str, lib_rs_text: str) -> list[str]:
    """
    Parse the body of a module's register_functions function.

    Two patterns exist:
      1. Block body with module prefix (defined in lib.rs):
             module_name::register_functions(m: &Bound<...>) -> PyResult<()> {
                 m.add_function(wrap_pyfunction!(module_name::SYM, m))?;
             }
      2. Block body WITHOUT module prefix (defined in module's own .rs file):
             pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
                 m.add_function(wrap_pyfunction!(content_hash_64, m))?;
             }
         In this case the module qualifier is NOT present in the .rs file,
         so we match on 'register_functions' followed by the opening brace.

    Returns a list of function names (SYM) registered inside the body.
    """
    # Try prefixed form first (lib.rs pattern)
    pattern = rf"({re.escape(module_name)}::register_functions\s*\([^)]*\)\s*(->\s*PyResult<[^>]*>\s*)?{{)"
    match = re.search(pattern, lib_rs_text)
    if not match:
        # Try bare form (module .rs file — no module_name:: prefix)
        # Match: 'register_functions(...) -> PyResult<()> {'  OR  'register_functions(...) {'
        pattern = rf"(register_functions\s*\([^)]*\)\s*(->\s*PyResult<[^>]*>\s*)?{{)"
        match = re.search(pattern, lib_rs_text)
        if not match:
            return []

    # Find the matching closing brace by counting braces from the opening {
    start = match.end() - 1  # position of opening {
    depth = 1
    pos = start + 1
    while pos < len(lib_rs_text) and depth > 0:
        c = lib_rs_text[pos]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        pos += 1
    body = lib_rs_text[start + 1:pos - 1]

    # Extract all wrap_pyfunction! calls within the body
    # Two forms:
    #   Qualified:  wrap_pyfunction!(module_name::func_name, m)  ← lib.rs
    #   Bare:      wrap_pyfunction!(func_name, m)              ← module .rs files
    symbols: list[str] = []
    for line in body.splitlines():
        if "wrap_pyfunction!" not in line:
            continue
        qualified = re.search(r"wrap_pyfunction!\((\w+)::(\w+)\s*,\s*m\)", line)
        if qualified:
            mod_name, func_name = qualified.group(1), qualified.group(2)
            if mod_name == module_name:
                symbols.append(func_name)
        else:
            bare = re.search(r"wrap_pyfunction!\((\w+)\s*,\s*m\)", line)
            if bare:
                symbols.append(bare.group(1))
    return symbols


def parse_lib_rs_symbols(lib_rs_text: str) -> dict[str, list[str]]:
    """
    Parse lib.rs and extract all PyO3-exported symbols per module.

    Handles five export patterns:
      1. m.add_class::<module::ClassName>()?  → (module, ClassName)
      2. m.add_function(wrap_pyfunction!(module::func, m))? → (module, func)
      3. module_name::register...(m)?           → (module_name, "module_name.register...")
         (catches register, register_functions, register_class, register_module)
      4. Inside register_functions body: extract individual function symbols
         for modules that expose many symbols via one register_functions call

    Returns: {module_name: [symbol, ...]}
    """
    result: dict[str, list[str]] = {}

    for line in lib_rs_text.splitlines():
        # Pattern 1: m.add_class
        if "m.add_class" in line:
            extracted = _extract_class_name(line)
            if extracted:
                module, class_name = extracted
                result.setdefault(module, []).append(class_name)
            continue

        # Pattern 2: m.add_function(wrap_pyfunction!(module::func, m))
        # e.g. m.add_function(wrap_pyfunction!(content_hasher::batch_content_hash, m))?;
        # Regex handles optional spaces around the comma: "func, m" or "func , m"
        if "wrap_pyfunction!" in line:
            m = re.search(r"wrap_pyfunction!\((\w+)::(\w+)\s*,\s*m\)", line)
            if m:
                mod_name, func_name = m.group(1), m.group(2)
                result.setdefault(mod_name, []).append(func_name)
            continue

        # Pattern 3: module_name::register...(m)?
        colon = line.find("::")
        if colon < 0:
            continue
        module = line[:colon].rsplit(maxsplit=1)[-1]
        if not module or not module[0].isalpha():
            continue

        if "register_functions(m)" in line:
            result.setdefault(module, []).append(f"{module}.register_functions")
            # Extract individual functions — first try lib.rs block body,
            # then (for one-liner calls) the module's own .rs file
            inner = _extract_register_functions_body(module, lib_rs_text)
            if not inner:
                # One-liner: body is in module_name.rs, not in lib.rs
                inner = _extract_register_functions_body(module, _read_rust_module(module))
            for sym in inner:
                if sym not in result.get(module, []):
                    result.setdefault(module, []).append(sym)
        elif "register_module(m)" in line:
            result.setdefault(module, []).append(f"{module}.register_module")
        elif "register_class(m)" in line:
            result.setdefault(module, []).append(f"{module}.register_class")
        elif "register(m)" in line:
            result.setdefault(module, []).append(f"{module}.register")

    return result


# ---------------------------------------------------------------------------
# Rust module file reader
# ---------------------------------------------------------------------------

_SRC_DIR = _REPO_ROOT / "src"  # _REPO_ROOT = rust_extensions/ dir


def _read_rust_module(module_name: str) -> str:
    """Read the .rs source file for a Rust module, or return empty string."""
    candidates = [
        _SRC_DIR / f"{module_name}.rs",
        _SRC_DIR / f"{module_name}_rs.rs",
    ]
    for path in candidates:
        if path.exists():
            try:
                return path.read_text()
            except Exception:  # noqa: BLE001
                pass
    return ""


# ---------------------------------------------------------------------------
# Git revision
# ---------------------------------------------------------------------------

def get_git_rev() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_manifest(check: bool = False) -> dict:
    lib_rs_text = LIB_RS_PATH.read_text()

    lib_rs_symbols = parse_lib_rs_symbols(lib_rs_text)

    # Flat set of all symbols derived from lib.rs
    all_lib_rs: list[str] = []
    for symbols in lib_rs_symbols.values():
        all_lib_rs.extend(symbols)
    all_lib_rs = sorted(set(all_lib_rs))

    manifest = {
        "version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_rev": get_git_rev(),
        "lib_rs_symbols": lib_rs_symbols,
        "all_lib_rs": all_lib_rs,
    }

    if check and MANIFEST_PATH.exists():
        old = json.loads(MANIFEST_PATH.read_text())
        old_rev = old.get("git_rev", "unknown")
        new_rev = manifest["git_rev"]
        if old_rev != new_rev:
            print(
                f"[build_ffi_manifest] MANIFEST STALE: git rev {old_rev} -> {new_rev}",
                file=sys.stderr,
            )
            print(
                f"  Run: python rust_extensions/build_ffi_manifest.py to regenerate",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            f"[build_ffi_manifest] MANIFEST OK: rev={new_rev}, "
            f"modules={len(lib_rs_symbols)}, symbols={len(all_lib_rs)}"
        )
    else:
        ABI_OUT.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
        print(f"[build_ffi_manifest] Written: {MANIFEST_PATH}")
        print(
            f"  modules={len(lib_rs_symbols)}, "
            f"symbols={len(all_lib_rs)}"
        )

    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FFI manifest generator")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if manifest git rev changed but manifest not regenerated",
    )
    args = parser.parse_args()
    generate_manifest(check=args.check)
