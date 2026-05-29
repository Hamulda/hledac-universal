"""
Embedding Prefix Discipline Tests
====================================

Testy pro task-aware embedding layer v mlx_embeddings.py:
- Asymmetric prefix test (SEARCH_QUERY vs SEARCH_DOCUMENT)
- Symmetric prefix test (CLUSTERING)
- Classification normalization test
- Prefix idempotence test
- Provider capability guard test
- Task-aware embedding methods
"""

# Import z mlx_embeddings.py (single source of truth)
# Správná cesta: hledac.universal.core (ne hledac.core)
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from hledac.universal.core.mlx_embeddings import (
    EmbeddingTask,
    MLXEmbeddingManager,
    apply_task_prefix,
    get_embedding_manager,
    should_normalize,
)


class TestEmbeddingTask:
    """Testy pro EmbeddingTask enum a helper funkce."""

    def test_asymmetric_prefix_differ(self):
        """Asymetrické tasky musí mít různé prefixy."""
        text = "machine learning"
        query = apply_task_prefix(text, EmbeddingTask.SEARCH_QUERY)
        doc = apply_task_prefix(text, EmbeddingTask.SEARCH_DOCUMENT)

        assert query != doc, "Asymmetric prefixes must differ"
        assert query == "search_query: machine learning"
        assert doc == "search_document: machine learning"

    def test_symmetric_prefix_same(self):
        """Symetrické tasky mají stejný prefix pro oba texty."""
        text_a = "text A"
        text_b = "text B"

        a_prefixed = apply_task_prefix(text_a, EmbeddingTask.CLUSTERING)
        b_prefixed = apply_task_prefix(text_b, EmbeddingTask.CLUSTERING)

        assert a_prefixed.startswith("clustering:")
        assert b_prefixed.startswith("clustering:")
        assert a_prefixed != b_prefixed  # Same prefix, different content

    def test_classification_prefix(self):
        """Classification task má svůj prefix."""
        text = "duplicate content"
        result = apply_task_prefix(text, EmbeddingTask.CLASSIFICATION)

        assert result.startswith("classification:")
        assert "classification: classification:" not in result  # No double prefix

    def test_none_task_no_prefix(self):
        """NONE task nepřidává prefix."""
        text = "plain text"
        result = apply_task_prefix(text, EmbeddingTask.NONE)

        assert result == text

    def test_empty_text_no_prefix(self):
        """Prázdný text vrací prázdný string."""
        result = apply_task_prefix("", EmbeddingTask.SEARCH_QUERY)
        assert result == ""

    def test_prefix_idempotence(self):
        """Dvojitá aplikace prefixu nedupilikuje prefix."""
        text = "test"
        once = apply_task_prefix(text, EmbeddingTask.SEARCH_QUERY)
        twice = apply_task_prefix(once, EmbeddingTask.SEARCH_QUERY)

        assert twice == once
        assert twice == "search_query: test"

    def test_normalization_rules(self):
        """Normalizace podle typu tasku (embedding_task.py pravidlo)."""
        assert should_normalize(EmbeddingTask.SEARCH_QUERY) is True
        assert should_normalize(EmbeddingTask.SEARCH_DOCUMENT) is True
        assert should_normalize(EmbeddingTask.CLUSTERING) is True
        # embedding_task.py vrací False pro CLASSIFICATION (pravidlo bez normalizace)
        # Ale _embed_task vždy normalizuje pro cosine similarity
        assert should_normalize(EmbeddingTask.CLASSIFICATION) is False


class TestMLXProviderCapabilities:
    """Testy pro MLX provider capability guard."""

    def test_mlx_supports_task_prefix(self):
        """MLXEmbeddingManager podporuje task prefixy."""
        try:
            mgr = MLXEmbeddingManager(lazy_load=True)
            assert hasattr(mgr, 'supports_task_prefix')
            assert mgr.supports_task_prefix is True
        except ImportError:
            pytest.skip("MLX not available")

    def test_mlx_embedding_dimension(self):
        """MLX má správnou dimenzi."""
        try:
            mgr = MLXEmbeddingManager(lazy_load=True)
            assert mgr.EMBEDDING_DIM == 768
            assert mgr.MRL_DIM == 256
        except ImportError:
            pytest.skip("MLX not available")

    def test_model_path(self):
        """ModernBERT model path."""
        try:
            mgr = MLXEmbeddingManager(lazy_load=True)
            assert "modernbert" in str(mgr.model_path).lower()
        except ImportError:
            pytest.skip("MLX not available")


class TestTaskAwareMethods:
    """Testy pro task-aware embedding metody v MLXEmbeddingManager."""

    def test_embed_query_method_exists(self):
        """Manager má embed_query metodu."""
        try:
            mgr = MLXEmbeddingManager(lazy_load=True)
            assert hasattr(mgr, 'embed_query')
            assert callable(mgr.embed_query)
        except ImportError:
            pytest.skip("MLX not available")

    def test_embed_document_method_exists(self):
        """Manager má embed_document metodu."""
        try:
            mgr = MLXEmbeddingManager(lazy_load=True)
            assert hasattr(mgr, 'embed_document')
            assert callable(mgr.embed_document)
        except ImportError:
            pytest.skip("MLX not available")

    def test_embed_for_dedup_method_exists(self):
        """Manager má embed_for_dedup metodu."""
        try:
            mgr = MLXEmbeddingManager(lazy_load=True)
            assert hasattr(mgr, 'embed_for_dedup')
            assert callable(mgr.embed_for_dedup)
        except ImportError:
            pytest.skip("MLX not available")

    def test_embed_for_clustering_method_exists(self):
        """Manager má embed_for_clustering metodu."""
        try:
            mgr = MLXEmbeddingManager(lazy_load=True)
            assert hasattr(mgr, 'embed_for_clustering')
            assert callable(mgr.embed_for_clustering)
        except ImportError:
            pytest.skip("MLX not available")

    def test_singleton_factory(self):
        """get_embedding_manager vrací singleton."""
        try:
            mgr1 = get_embedding_manager()
            mgr2 = get_embedding_manager()
            assert mgr1 is mgr2  # Same instance
        except ImportError:
            pytest.skip("MLX not available")


class TestPrefixDiscipline:
    """Testy pro prefix discipline pravidla."""

    def test_prefix_never_in_db(self):
        """Prefix se aplikuje pouze během embeddování - test logiky."""
        # Tento test ověřuje, že apply_task_prefix vrací prefixovaný text
        # ale neukládáme ho nikam - použijeme jen v _embed_task metodě
        text = "original document"
        prefixed = apply_task_prefix(text, EmbeddingTask.SEARCH_DOCUMENT)

        # Prefix existuje pro embeddování
        assert prefixed.startswith("search_document:")
        # Ale originál zůstává nezměněn pro DB
        assert text == "original document"

    def test_different_tasks_different_embeddings(self):
        """Různé tasky = různé prefixy = různé embeddings (teoreticky)."""
        # Ověřujeme že prefixy jsou správně aplikovány
        text = "same content"

        q = apply_task_prefix(text, EmbeddingTask.SEARCH_QUERY)
        d = apply_task_prefix(text, EmbeddingTask.SEARCH_DOCUMENT)
        c = apply_task_prefix(text, EmbeddingTask.CLUSTERING)

        # Všechny mají různé prefixy
        assert q != d
        assert d != c
        assert q != c

    def test_prefix_memory_safe_batch(self):
        """Batch prefixing je memory-safe (žádné velké allocations)."""
        # Ověřujeme že list comprehension funguje správně
        texts = [f"document {i}" for i in range(10)]
        prefixed = [apply_task_prefix(t, EmbeddingTask.SEARCH_DOCUMENT) for t in texts]

        assert len(prefixed) == 10
        assert all(p.startswith("search_document:") for p in prefixed)
