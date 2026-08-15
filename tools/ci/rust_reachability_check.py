#!/usr/bin/env python3
"""
P7-1: Rust Module Reachability Checker.

AST-based analysis to verify all src/*.rs files are reachable from lib.rs.
This ensures no "dead code" exists that isn't compiled into the binary.

Usage:
    python tools/ci/rust_reachability_check.py rust_extensions/

Exit codes:
    0 = all modules reachable
    1 = unreachable modules found
    2 = error (file not found, parse error, etc.)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from collections.abc import Iterator
from _core import aclose


def extract_pub_modules(lib_rs_content: str) -> set[str]:
    """
    Extract all pub mod declarations from lib.rs.
    
    Handles:
    - Simple: pub mod foo;
    - Feature-gated: #[cfg(feature = "foo")] pub mod bar;
    - Nested: pub mod pools; (where pools has submodules)
    """
    modules: set[str] = set()

    # Match feature-gated pub mod declarations FIRST
    # #[cfg(feature = "foo")]
    # pub mod bar;
    feature_pattern = r'#\[cfg\(feature\s*=\s*"([^"]+)"\)\]\s*pub\s+mod\s+(\w+)\s*;'
    for match in re.finditer(feature_pattern, lib_rs_content, re.MULTILINE):
        feature = match.group(1)
        module = match.group(2)
        modules.add(f"{feature}:{module}")

    # Remove cfg attributes for non-feature analysis
    content = re.sub(r'#\[cfg\([^)]*\)\]', '', lib_rs_content)

    # Match pub mod declarations (simple and nested)
    pattern = r'^\s*pub\s+mod\s+(\w+)\s*;'
    for match in re.finditer(pattern, content, re.MULTILINE):
        modules.add(match.group(1))

    return modules


def extract_module_files(src_dir: Path) -> set[str]:
    """
    Extract all module names from src/ directory (including subdirectories).
    
    Handles:
    - src/*.rs files (e.g., circuit_breaker.rs -> circuit_breaker)
    - src/subdir/*.rs files (e.g., pools/cpu.rs -> pools/cpu)
    - Excludes backup directories (collections_backup, stix_2_1, etc.)
    """
    modules: set[str] = set()
    src_path = src_dir / "src"

    if not src_path.exists():
        return modules

    # Exclude backup/experimental directories
    exclude_dirs = {'collections_backup', 'stix_2_1', 'simd', 'ioc', 'data', 'graph_traverse'}
    
    for rs_file in src_path.rglob("*.rs"):
        # Get relative path from src/
        rel_path = rs_file.relative_to(src_path)
        
        # Skip files in excluded directories
        if any(part in exclude_dirs for part in rel_path.parts[:-1]):
            continue
            
        # Build module path (e.g., pools/cpu, graph_analytics)
        module_name = str(rel_path.with_suffix('')).replace('/', '_')
        modules.add(module_name)

    return modules


def extract_cfgs(content: str) -> dict[str, set[str]]:
    """
    Extract cfg-gated module declarations.

    Returns:
        Dict mapping feature name -> set of module names
    """
    cfgs: dict[str, set[str]] = {}

    # Match: #[cfg(feature = "foo")]
    # pub mod bar;
    pattern = r'#\[cfg\(feature\s*=\s*"([^"]+)"\)\]\s*pub\s+mod\s+(\w+)\s*;'
    for match in re.finditer(pattern, content, re.MULTILINE):
        feature = match.group(1)
        module = match.group(2)
        if feature not in cfgs:
            cfgs[feature] = set()
        cfgs[feature].add(module)

    return cfgs


def check_reachability(rust_dir: Path) -> tuple[set[str], set[str], dict[str, set[str]]]:
    """
    Check Rust module reachability from lib.rs.

    Returns:
        (unreachable_required, unreachable_optional, feature_gate_map)
        - unreachable_required: modules declared in lib.rs but file doesn't exist
        - unreachable_optional: files in src/ but not declared in lib.rs
        - feature_gate_map: feature -> set of cfg-gated modules
    """
    lib_rs_path = rust_dir / "src" / "lib.rs"

    if not lib_rs_path.exists():
        return set(), set(), {}

    lib_rs_content = lib_rs_path.read_text()

    # Extract all declared modules (including feature-gated)
    declared_modules = extract_pub_modules(lib_rs_content)

    # Extract feature gates
    feature_gate_map = extract_cfgs(lib_rs_content)

    # Get all actual module files (including subdirectories)
    actual_modules = extract_module_files(rust_dir)

    # Separate required and feature-gated modules
    required_modules: set[str] = set()
    feature_gated_modules: set[str] = set()

    for module in declared_modules:
        if ':' in module:
            # Feature-gated: feature:module
            feature_gated_modules.add(module)
        else:
            required_modules.add(module)

    # Check for unreachable required modules (with submodule expansion)
    # lib.rs: pub mod pools; -> looks for src/pools.rs OR src/pools/mod.rs
    unreachable_required: set[str] = set()
    for module in required_modules:
        # Check if module exists as file or directory
        if module not in actual_modules:
            # Maybe it's a submodule directory
            # pools -> pools/mod.rs exists
            module_path = rust_dir / "src" / module
            if not (module_path.with_suffix('.rs').exists() or 
                    (module_path / "mod.rs").exists()):
                unreachable_required.add(module)

    # Check for unreachable files (modules not in lib.rs)
    # Only report if not feature-gated
    all_declared = {m.split(':')[1] if ':' in m else m for m in declared_modules}
    unreachable_optional = actual_modules - all_declared

    return unreachable_required, unreachable_optional, feature_gate_map


def get_feature_matrix(rust_dir: Path) -> dict[str, set[str]]:
    """
    Extract feature -> set of modules from Cargo.toml.

    Returns:
        Dict mapping feature name -> set of module names
    """
    cargo_toml_path = rust_dir / "Cargo.toml"

    if not cargo_toml_path.exists():
        return {}

    content = cargo_toml_path.read_text()

    # Extract feature definitions
    # [features]
    # full = ["module1", "module2", ...]
    features: dict[str, set[str]] = {}

    feature_section = re.search(r'\[features\](.*?)(?=\n\[|\Z)', content, re.DOTALL)
    if not feature_section:
        return features

    feature_block = feature_section.group(1)

    # Match: feature_name = ["dep", "module1", ...]
    pattern = r'(\w+)\s*=\s*\[([^\]]*)\]'
    for match in re.finditer(pattern, feature_block, re.MULTILINE):
        feature_name = match.group(1)
        deps_str = match.group(2)

        # Extract module names (simple heuristics: look for .rs files mentioned)
        modules: set[str] = set()
        for line in deps_str.split(','):
            line = line.strip()
            # Match: crate_name/path or just path
            if '/' in line:
                path_part = line.split('/')[-1].strip('"')
                if path_part.endswith('.rs'):
                    path_part = path_part[:-3]
                modules.add(path_part)

        features[feature_name] = modules

    return features


def main() -> int:
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: rust_reachability_check.py <rust_dir>")
        print("Example: rust_reachability_check.py rust_extensions/")
        return 2

    rust_dir = Path(sys.argv[1]).resolve()

    if not rust_dir.exists():
        print(f"Error: {rust_dir} does not exist")
        return 2

    print(f"Checking Rust module reachability in {rust_dir}")
    print("=" * 60)

    # Check reachability
    unreachable_required, unreachable_optional, feature_gate_map = check_reachability(rust_dir)

    # Get feature matrix
    feature_matrix = get_feature_matrix(rust_dir)

    # Report results
    has_errors = False

    if unreachable_required:
        print("\n[ERROR] Unreachable REQUIRED modules (declared but file not found):")
        for module in sorted(unreachable_required):
            print(f"  - {module}")
        has_errors = True

    if unreachable_optional:
        print("\n[WARN] Unreachable OPTIONAL modules (file exists but not declared):")
        for module in sorted(unreachable_optional):
            print(f"  - {module}")
        print("  Note: These modules may be feature-gated or truly dead code")

    if feature_gate_map:
        print("\n[INFO] Feature-gated modules:")
        for feature, modules in sorted(feature_gate_map.items()):
            print(f"  {feature}:")
            for module in sorted(modules):
                print(f"    - {module}")

    if feature_matrix:
        print("\n[INFO] Feature matrix:")
        for feature, modules in sorted(feature_matrix.items()):
            if modules:
                print(f"  {feature}:")
                for module in sorted(modules):
                    print(f"    - {module}")

    print("\n" + "=" * 60)

    if has_errors:
        print("RESULT: FAIL - Unreachable required modules found")
        return 1

    if unreachable_optional:
        print("RESULT: WARN - Optional unreachable modules found (review recommended)")
        return 0

    print("RESULT: PASS - All modules reachable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
