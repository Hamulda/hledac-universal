"""
tests/rust/test_ffi_parity.py

P3-2: FFI parity CI test — verifies Rust extension symbols match RustBackend expectations.

Architecture:
    1. parse_lib_rs_symbols() — mirrors build_ffi_manifest.py logic
    2. parse_rustbackend_specs() — extracts MethodSpec("name") from _Rust*Domain._spec
    3. Parity check — two-tier:
       a. Class names (from m.add_class): exact match required
       b. Module.register_functions: module must exist in manifest (individual
          function names are opaque to static analysis; verified via runtime
          introspection when extension is built)

CI gate:
    pytest tests/rust/test_ffi_parity.py -q
        exits 1 if manifest is stale or drift detected
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Project root: universal/
REPO_ROOT = Path(__file__).resolve().parents[2]
ABI_MANIFEST = REPO_ROOT / "core" / "rust_backend" / "_ffi_manifest.json"
LIB_RS = REPO_ROOT / "rust_extensions" / "src" / "lib.rs"
RUST_BACKEND_PY = REPO_ROOT / "core" / "rust_backend.py"


# ---------------------------------------------------------------------------
# Manifest parsing (mirrors build_ffi_manifest.py)
# ---------------------------------------------------------------------------

def _extract_class_name_from_add_class(line: str) -> tuple[str, str] | None:
    """Parse 'm.add_class::<...::ClassName>()?;' → (module, class_name)."""
    gt = line.rfind("<")
    gt2 = line.find(">", gt)
    if gt < 0 or gt2 < 0:
        return None
    path = line[gt + 1:gt2]
    segments = path.split("::")
    if len(segments) < 2:
        return None
    return (segments[0], segments[-1])


def parse_lib_rs_symbols(text: str) -> dict[str, list[str]]:
    """Parse lib.rs — extract all PyO3 export patterns."""
    result: dict[str, list[str]] = {}

    for line in text.splitlines():
        if "m.add_class" in line:
            extracted = _extract_class_name_from_add_class(line)
            if extracted:
                module, class_name = extracted
                result.setdefault(module, []).append(class_name)
            continue

        colon = line.find("::")
        if colon < 0:
            continue
        module = line[:colon].rsplit(maxsplit=1)[-1]
        if not module or not module[0].isalpha():
            continue

        if "register_functions(m)" in line:
            result.setdefault(module, []).append(f"{module}.register_functions")
        elif "register(m)" in line:
            result.setdefault(module, []).append(f"{module}.register")

    return result


def build_manifest_from_lib_rs() -> set[str]:
    """Compute expected manifest symbols from lib.rs source."""
    lib_rs_text = LIB_RS.read_text()
    lib_rs_symbols = parse_lib_rs_symbols(lib_rs_text)
    all_symbols: list[str] = []
    for symbols in lib_rs_symbols.values():
        all_symbols.extend(symbols)
    return set(all_symbols)


# ---------------------------------------------------------------------------
# RustBackend spec extraction
# ---------------------------------------------------------------------------

def parse_rustbackend_specs() -> dict[str, str]:
    """Extract MethodSpec names and their domain from _Rust*Domain._spec.

    Returns: {method_name: domain_module}  e.g. {"batch_entropy": "quality_gate"}
    This is needed because some modules export functions while others export classes.
    """
    src = RUST_BACKEND_PY.read_text()

    # Find each _Rust*Domain class block bounded by next class or end-of-file
    class_pattern = re.compile(
        r"^class\s+(_Rust\w+Domain)[^:]*:.*?"
        r"^(?=class\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )

    spec_map: dict[str, str] = {}  # method_name -> domain_hint

    for cls_match in class_pattern.finditer(src):
        cls_name = cls_match.group(1)
        block = cls_match.group(0)

        spec_match = re.search(r"_spec\s*=\s*\[(.*?)\]", block, re.DOTALL)
        if not spec_match:
            continue

        # Determine domain module for this class from docstring or class name
        # _RustBloomDomain → bloom, _RustQualityDomain → quality_gate, etc.
        domain_hint = _infer_domain_module(cls_name)

        for method_name in re.findall(r'MethodSpec\("(\w+)"', spec_match.group(1)):
            spec_map[method_name] = domain_hint

    return spec_map


def _infer_domain_module(cls_name: str) -> str:
    """Infer the Rust module name from _RustXxxDomain class name."""
    raw = cls_name.replace("_Rust", "").replace("Domain", "")
    snake = _camel_to_snake(raw)

    # Complete mapping: _RustXxxDomain → manifest module key
    # Some modules use _rs suffix (hot_edges_rs), some use _parse (ip_parse),
    # some have different names entirely (url → url_ops).
    KNOWN_REMAPPINGS = {
        # Correct snake-case conversions (from _RustXxxDomain)
        "url": "url_ops",
        "quality": "quality_gate",
        "ioc": "ioc_extract",
        "text": "text_norm",
        "xml": "xml_sanitize",
        "graph": "graph_traverse",
        "lsh": "lsh_index",
        "ioc_dedup": "ioc_dedup",
        "int_counter": "int_counter_layout",
        "simd": "simd_similarity",
        "evidence": "mpsc_pool",
        "memory": "memory",
        "json": "serde_json_rs",
        "spsc": "spsc_queue",
        "query": "async_query",
        "tls": "tls_metadata",
        "mlx": "mlx_bridge",
        "pool": "pool_run",
        "federated": "federated_qtable",
        "parquet": "parquet_reader",
        "pipeline": "pipeline_compose",
        "cooccurrence": "ioc_cooccurrence_rs",
        "signal": "signal_batch",
        "nvd": "rate_limit",
        "similarity": "text_similarity",
        "metal": "metal_pattern_matcher",
        "bloom": "bloom",
        "rolling_hash": "rolling_hash",
        # CamelCase-with-uppercase that snake_case mangles:
        "hot_edges": "hot_edges_rs",   # _RustHotEdgesDomain
        "ip": "ip_parse",              # _RustIpDomain
        "aho": "aho_corasick",         # _RustAhoDomain
        "simhash": "simhash_ext",      # _RustSimhashDomain
        # CamelCase that loses a letter:
        "html": "html_parse",          # _RustHtmlDomain  (Html → html not html_parse)
        "madvis": "madvise",          # _RustMadvisDomain (Madvis → madvis)
        "hash": "content_hasher",      # _RustHashDomain
    }

    return KNOWN_REMAPPINGS.get(snake, snake)


def _camel_to_snake(name: str) -> str:
    """Convert CamelCaseWithCaps to snake_case, preserving underscores."""
    result = []
    for i, c in enumerate(name):
        if c.isupper() and i > 0:
            result.append('_')
        result.append(c.lower())
    return "".join(result)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFFIParity:
    """P3-2: FFI parity tests for Rust symbols vs Python expectations."""

    def test_manifest_exists(self) -> None:
        """Manifest file must exist (generated by build_ffi_manifest.py)."""
        assert ABI_MANIFEST.exists(), (
            f"Manifest not found at {ABI_MANIFEST}. "
            "Run: python rust_extensions/build_ffi_manifest.py"
        )

    def test_manifest_schema(self) -> None:
        """Manifest must have required keys."""
        manifest = json.loads(ABI_MANIFEST.read_text())
        assert "version" in manifest
        assert "all_lib_rs" in manifest
        assert "lib_rs_symbols" in manifest
        assert "generated_at" in manifest

    def test_lib_rs_unchanged_since_manifest(self) -> None:
        """lib.rs must not have changed since manifest was generated."""
        manifest = json.loads(ABI_MANIFEST.read_text())
        saved_rev = manifest.get("git_rev", "unknown")

        try:
            actual_rev = subprocess.run(
                ["git", "rev-parse", "--short=8", "HEAD"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        except Exception:
            pytest.skip("git not available in test environment")

        if saved_rev != actual_rev:
            pytest.fail(
                f"lib.rs git rev changed: manifest={saved_rev}, current={actual_rev}. "
                "Run: python rust_extensions/build_ffi_manifest.py"
            )

    def test_no_class_name_drift(self) -> None:
        """Class names in _spec (from m.add_class) must be in manifest.

        This is the STRICT check: every MethodSpec("ClassName") where ClassName
        is a class (not a function) must be exported by Rust as a PyO3 class.
        These are directly verifiable via manifest.
        """
        manifest = json.loads(ABI_MANIFEST.read_text())
        rust_exports: set[str] = set(manifest["all_lib_rs"])

        # Only check MethodSpec names that look like classes (PascalCase)
        specs = parse_rustbackend_specs()
        class_names = {
            name for name in specs
            if name[0].isupper() and "_" not in name
        }

        missing = class_names - rust_exports
        if missing:
            pytest.fail(
                f"[FFI class-name DRIFT] {len(missing)} class name(s) in "
                f"_spec but NOT exported by Rust:\n"
                + "\n".join(f"  - {s}" for s in sorted(missing))
            )

    def test_register_functions_coverage(self) -> None:
        """Modules using register_functions must be present in manifest.

        Individual function names within a register_functions module cannot be
        statically verified (opaque to static analysis). This test only checks
        that the domain module has a register_functions entry in the manifest.

        NOTE: This is a LENIENT check. Drift here (function in spec but module
        missing) means the function has no Rust path at all — this is the
        primary "silent Python fallback" risk. But module present + function
        missing within it would NOT be caught here (use runtime introspection
        with HLEDAC_FORCE_RUST=1 in CI to catch that case).
        """
        manifest = json.loads(ABI_MANIFEST.read_text())

        # All modules that appear in the manifest (any registration mechanism):
        # All modules that appear in the manifest (any registration mechanism:
        # register_functions, register_class, register_module, register, wrap_pyfunction!)
        # These modules have at least one symbol available from Rust.
        manifest_modules: set[str] = set(manifest.get("lib_rs_symbols", {}).keys())

        specs = parse_rustbackend_specs()

        # For each function in specs, check its domain module exists in manifest
        missing_modules: list[tuple[str, str]] = []
        for func_name, domain_mod in sorted(specs.items()):
            if func_name[0].isupper():
                continue  # class names checked separately
            if domain_mod not in manifest_modules:
                missing_modules.append((func_name, domain_mod))

        if missing_modules:
            by_domain: dict[str, list[str]] = {}
            for func, mod in missing_modules:
                by_domain.setdefault(mod, []).append(func)

            pytest.fail(
                f"[FFI register_functions DRIFT] {len(missing_modules)} function(s) "
                "in _spec whose domain module is NOT in manifest (no Rust path at all):\n"
                + "\n".join(
                    f"  {mod}: {', '.join(funcs)}"
                    for mod, funcs in sorted(by_domain.items())
                )
            )

    def test_manifest_completeness(self) -> None:
        """Manifest must cover all symbols parseable from lib.rs."""
        manifest = json.loads(ABI_MANIFEST.read_text())
        manifest_syms: set[str] = set(manifest["all_lib_rs"])

        from_lib_rs = build_manifest_from_lib_rs()
        missing_in_manifest = from_lib_rs - manifest_syms

        if missing_in_manifest:
            pytest.fail(
                f"Manifest is stale — {len(missing_in_manifest)} lib.rs symbol(s) "
                "not in manifest:\n"
                + "\n".join(f"  - {s}" for s in sorted(missing_in_manifest))
                + "\n\nFix: python rust_extensions/build_ffi_manifest.py"
            )
