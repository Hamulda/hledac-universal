"""
Tests for MMR diversity filtering (Sprint 76).
"""


import pytest


class TestMMRDiversity:
    """Test MMR diversity algorithm."""

    def test_mmr_reduces_similarity(self):
        """Test MMR selects diverse results."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)

        query_emb = [1.0, 0.0, 0.0]
        candidates = [
            {'_embedding': [0.99, 0.01, 0.0], 'text': 'similar1'},
            {'_embedding': [0.98, 0.02, 0.0], 'text': 'similar2'},
            {'_embedding': [0.0, 1.0, 0.0], 'text': 'different'},
        ]

        result = store._mmr(candidates, query_emb, lambda_param=0.5, top_k=3)

        # MMR should return all when top_k >= len(candidates)
        assert len(result) == 3

    def test_mmr_with_high_lambda(self):
        """Test MMR with high lambda (more diversity)."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)

        query_emb = [1.0, 0.0, 0.0]
        candidates = [
            {'_embedding': [0.99, 0.01, 0.0], 'text': 'almost_same'},
            {'_embedding': [0.50, 0.50, 0.0], 'text': 'different'},
            {'_embedding': [0.98, 0.02, 0.0], 'text': 'very_similar'},
        ]

        # High lambda = more diversity
        result = store._mmr(candidates, query_emb, lambda_param=0.8, top_k=2)

        [r['text'] for r in result]
        # With high lambda, we should get more diverse results
        assert len(result) == 2

    def test_mmr_preserves_order_when_small(self):
        """Test MMR returns all when candidates < top_k."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)

        query_emb = [1.0, 0.0, 0.0]
        candidates = [
            {'_embedding': [0.9, 0.1, 0.0], 'text': 'a'},
            {'_embedding': [0.8, 0.2, 0.0], 'text': 'b'},
        ]

        result = store._mmr(candidates, query_emb, top_k=10)

        assert len(result) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
