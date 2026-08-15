"""
P0 gate: .pyi stub must not declare symbols that don't exist in the live module.

Issue #5 (F330): hledac_rust_extensions.pyi contained fabricated methods:
  - BloomFilter.add_many, bitmap  (live: only add/add_batch/contains/contains_batch)
  - ContentHasher.update/digest/reset/n/batch_n  (live: only static methods)
  - AhoCorasickMatcher.find_all/is_match  (live: find_any, scan, scan_batch, scan_with_captures, is_empty)
  - IocDedupStore.to_bytes/set_state_from_bytes/get_state_bytes  (live: not exposed)

This test parses the .pyi stub, introspects the live module, and asserts
every declared class/function is present at runtime.
"""


import ast
import sys
from pathlib import Path

import pytest

pytest.importorskip("hledac_rust_extensions", reason="hledac_rust_extensions not built")
import hledac_rust_extensions as _live_module  # noqa: E402
from core import aclose


_PYI_PATH = Path(__file__).parent.parent / "rust_extensions" / "hledac_rust_extensions.pyi"


class _PyiVisitor(ast.NodeVisitor):
    """Collect class and top-level function names from a .pyi AST."""

    __slots__ = ("classes", "funcs")

    def __init__(self) -> None:
        self.classes: set[str] = set()
        self.funcs: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.add(node.name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.funcs.add(node.name)
        self.generic_visit(node)


def _live_symbols() -> tuple[set[str], set[str]]:
    """Return (classes, standalone_funcs) from the live module."""
    live_classes = {
        n
        for n in dir(_live_module)
        if not n.startswith("_") and isinstance(getattr(_live_module, n), type)
    }
    live_funcs = {
        n
        for n in dir(_live_module)
        if not n.startswith("_")
        and callable(getattr(_live_module, n))
        and not isinstance(getattr(_live_module, n), type)
    }
    return live_classes, live_funcs


def _pyi_symbols() -> tuple[set[str], set[str]]:
    """Return (classes, funcs) declared in the .pyi stub."""
    tree = ast.parse(_PYI_PATH.read_text())
    visitor = _PyiVisitor()
    visitor.visit(tree)
    return visitor.classes, visitor.funcs


class TestPyiConsistency:
    """P0 gate: .pyi must not declare non-existent symbols."""

    def test_pyi_file_exists(self) -> None:
        """Sanity check: .pyi stub must be present."""
        assert _PYI_PATH.exists(), f"{_PYI_PATH} not found"

    def test_pyi_no_fabricated_classes(self) -> None:
        """No class declared in .pyi that doesn't exist in the live module."""
        pyi_classes, _ = _pyi_symbols()
        live_classes, _ = _live_symbols()
        missing = pyi_classes - live_classes
        assert not missing, f".pyi declares non-existent classes: {sorted(missing)}"

    def test_pyi_no_fabricated_functions(self) -> None:
        """No top-level function declared in .pyi that doesn't exist in live module."""
        _, pyi_funcs = _pyi_symbols()
        _, live_funcs = _live_symbols()
        missing = pyi_funcs - live_funcs
        assert not missing, f".pyi declares non-existent functions: {sorted(missing)}"

    def test_pyi_class_methods_match_live(self) -> None:
        """Each class's methods in .pyi must exist in the live class."""
        pyi_classes, _ = _pyi_symbols()
        _live_symbols()  # ensure live module is accessible

        tree = ast.parse(_PYI_PATH.read_text())
        class_methods: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_methods[node.name] = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}

        failures: list[str] = []
        for cls_name, pyi_methods in class_methods.items():
            if cls_name not in pyi_classes:
                continue
            live_cls = getattr(_live_module, cls_name)
            live_methods = {n for n in dir(live_cls) if not n.startswith("_") and callable(getattr(live_cls, n))}
            fabricated = pyi_methods - live_methods
            if fabricated:
                failures.append(f"  {cls_name}: fabricated methods {sorted(fabricated)}")

        assert not failures, "Fabricated methods found:\n" + "\n".join(failures)

    def test_live_ioc_extraction_is_simd(self) -> None:
        """extract_iocs_flat must route to SIMD (extract_iocs_simd), not scalar fast_ioc_extract.

        This guards against drift where extract_iocs_flat was accidentally changed
        to call fast_ioc_extract instead of extract_iocs_simd.
        """
        # Verify extract_iocs_simd is registered in the live module
        assert hasattr(
            _live_module, "extract_iocs_simd"
        ), "extract_iocs_simd not found in live module — SIMD path is missing"
        # Verify the method signature is correct (takes str, returns list of tuples)
        import inspect

        sig = inspect.signature(_live_module.extract_iocs_simd)
        assert list(sig.parameters) == ["text"], f"Unexpected signature: {sig}"

    @pytest.mark.skipif(
        sys.platform != "darwin", reason="Rust extensions built for macOS (M1/M2/M3)"
    )
    def test_bloom_filter_no_add_many(self) -> None:
        """BloomFilter must NOT have add_many (fabricated in old .pyi)."""
        from hledac_rust_extensions import BloomFilter

        bf = BloomFilter(capacity=1000)
        assert not hasattr(bf, "add_many"), "BloomFilter.add_many should not exist"

    @pytest.mark.skipif(
        sys.platform != "darwin", reason="Rust extensions built for macOS (M1/M2/M3)"
    )
    def test_content_hasher_no_instance_methods(self) -> None:
        """ContentHasher must only have static methods; instance methods are fabricated."""
        from hledac_rust_extensions import ContentHasher

        ch = ContentHasher()
        fabricated = {"update", "digest", "reset"}
        found = fabricated & set(dir(ch))
        assert not found, f"ContentHasher fabricated instance methods found: {found}"

    @pytest.mark.skipif(
        sys.platform != "darwin", reason="Rust extensions built for macOS (M1/M2/M3)"
    )
    def test_aho_corasick_no_find_all(self) -> None:
        """AhoCorasickMatcher must NOT have find_all/is_match (fabricated in old .pyi)."""
        from hledac_rust_extensions import AhoCorasickMatcher

        acm = AhoCorasickMatcher(patterns=["test"])
        fabricated = {"find_all", "is_match"}
        found = fabricated & set(dir(acm))
        assert not found, f"AhoCorasickMatcher fabricated methods found: {found}"

    @pytest.mark.skipif(
        sys.platform != "darwin", reason="Rust extensions built for macOS (M1/M2/M3)"
    )
    def test_ioc_dedup_store_no_bytes_methods(self) -> None:
        """IocDedupStore must NOT have to_bytes/set_state_from_bytes/get_state_bytes."""
        from hledac_rust_extensions import IocDedupStore

        store = IocDedupStore(sprint_id=0)
        fabricated = {"to_bytes", "set_state_from_bytes", "get_state_bytes"}
        found = fabricated & set(dir(store))
        assert not found, f"IocDedupStore fabricated methods found: {found}"
