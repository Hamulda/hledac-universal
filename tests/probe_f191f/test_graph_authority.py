"""
Sprint F191F — Graph Authority Matrix Verification
==================================================
Bytecode-only hermetic tests — reads .cpython-312.pyc files from
knowledge/__pycache__/ to avoid circular import issues.

This module is NOT auto-discovered by pytest due to hledac import chain.
Run via:
    python tests/probe_f191f/run_bytecode_tests.py
or:
    pytest tests/probe_f191f/run_bytecode_tests.py -v
"""
from __future__ import annotations

import sys
import marshal
import types
from pathlib import Path

import pytest

_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
sys.path.insert(0, str(_ROOT))
_KNOWLEDGE_PYCACHE = _ROOT / "knowledge" / "__pycache__"


def _load_code(mod_name: str) -> types.CodeType:
    """Load code object from .cpython-312.pyc in knowledge/__pycache__/."""
    pattern = f"{mod_name}.cpython-312.pyc"
    candidates = sorted(_KNOWLEDGE_PYCACHE.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No bytecode for {mod_name}")
    bc_path = candidates[0]
    with open(bc_path, "rb") as f:
        f.read(16)
        return marshal.load(f)


def _get_docstring(code: types.CodeType) -> str:
    """Extract module-level docstring from marshaled code object."""
    if code.co_consts and isinstance(code.co_consts[0], str):
        return code.co_consts[0]
    for const in code.co_consts:
        if isinstance(const, types.CodeType) and const.co_consts:
            if isinstance(const.co_consts[0], str):
                return const.co_consts[0]
    return ""


def _get_class_code(code: types.CodeType, class_name: str) -> types.CodeType | None:
    """Find a nested code object (class) by name."""
    for const in code.co_consts:
        if isinstance(const, types.CodeType) and const.co_name == class_name:
            return const
    return None


class TestSprintF191FGraphAuthority:
    """Authority matrix verification across the graph/entity storage cluster."""

    # === DF-1: ioc_graph — GRAPH TRUTH STORE winner ===

    def test_ioc_graph_declares_graph_truth_store(self):
        code = _load_code("ioc_graph")
        doc = _get_docstring(code).upper()
        assert "GRAPH TRUTH STORE" in doc, f"FAIL: {doc[:150]}"

    def test_ioc_graph_declares_authoritative_backend(self):
        code = _load_code("ioc_graph")
        doc = _get_docstring(code).lower()
        assert "authoritative" in doc and "ioc" in doc and "truth" in doc

    def test_ioc_graph_owns_key_methods(self):
        code = _load_code("ioc_graph")
        cls = _get_class_code(code, "IOCGraph")
        assert cls is not None, "IOCGraph class not found in bytecode"
        methods = set(cls.co_names)
        required = {"buffer_ioc", "flush_buffers", "upsert_ioc_batch", "pivot"}
        missing = required - methods
        assert not missing, f"missing: {missing}"

    def test_ioc_graph_export_stix_bundle_is_distinguishing_capability(self):
        code = _load_code("ioc_graph")
        cls = _get_class_code(code, "IOCGraph")
        assert cls is not None
        assert "export_stix_bundle" in cls.co_names

    # === DF-2: graph_rag — CONSUMER/ORCHESTRATOR (NOT backend owner) ===

    def test_graph_rag_declares_consumer_orchestrator(self):
        code = _load_code("graph_rag")
        doc = _get_docstring(code)
        assert "Consumer/Orchestrator" in doc, f"FAIL: {doc[:150]}"

    def test_graph_rag_denies_backend_ownership(self):
        code = _load_code("graph_rag")
        doc = _get_docstring(code)
        assert "(NOT backend owner)" in doc or "NOT backend owner" in doc

    def test_graph_rag_owns_multi_hop_search(self):
        code = _load_code("graph_rag")
        cls = _get_class_code(code, "GraphRAGOrchestrator")
        assert cls is not None
        assert "multi_hop_search" in cls.co_names

    # === DF-3: semantic_store — CONSUMER/ENRICHMENT ===

    def test_semantic_store_declares_consumer_enrichment(self):
        code = _load_code("semantic_store")
        doc = _get_docstring(code)
        assert "Consumer/Enrichment" in doc, f"FAIL: {doc[:150]}"

    def test_semantic_store_denies_granting_authority(self):
        code = _load_code("semantic_store")
        doc = _get_docstring(code).lower()
        assert "not backend owner" in doc
        assert "not grounding authority" in doc or "not backend owner" in doc

    def test_semantic_store_owns_consumer_methods(self):
        code = _load_code("semantic_store")
        cls = _get_class_code(code, "SemanticStore")
        assert cls is not None
        methods = set(cls.co_names)
        required = {"buffer_finding", "flush", "semantic_pivot"}
        missing = required - methods
        assert not missing, f"missing: {missing}"

    # === DF-4: lancedb_store — IDENTITY/ENTITY STORE ===

    def test_lancedb_store_declares_identity_entity_store(self):
        code = _load_code("lancedb_store")
        doc = _get_docstring(code)
        assert "Identity/Entity Store" in doc, f"FAIL: {doc[:150]}"

    def test_lancedb_store_denies_grounding_authority(self):
        code = _load_code("lancedb_store")
        doc = _get_docstring(code).lower()
        assert "not grounding authority" in doc

    def test_lancedb_store_owns_entity_operations(self):
        code = _load_code("lancedb_store")
        cls = _get_class_code(code, "LanceDBIdentityStore")
        assert cls is not None
        methods = set(cls.co_names)
        required = {"add_entity", "search_similar", "compute_similarity"}
        missing = required - methods
        assert not missing, f"missing: {missing}"

    # === DF-5: Cross-cluster containment ===

    def test_no_consumer_claims_ioc_graph_backend_ownership(self):
        for mod_name in ["graph_rag", "semantic_store", "lancedb_store"]:
            code = _load_code(mod_name)
            doc = _get_docstring(code).lower()
            has_claim = "ioc_graph" in doc and "owner" in doc
            assert not has_claim, f"{mod_name} claims ioc_graph ownership"

    def test_no_consumer_claims_kuzu_truth_store(self):
        for mod_name in ["graph_rag", "semantic_store", "lancedb_store"]:
            code = _load_code(mod_name)
            doc = _get_docstring(code).lower()
            has_claim = "kuzu" in doc and "truth" in doc
            assert not has_claim, f"{mod_name} claims Kuzu truth store"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
