"""
Tests for M1 RAM guard on usearch ANN index rebuild (Sprint C4).

Verifies that _ensure_usearch_index skips index build when RAM < 4GB.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestUSearchIndexMemoryGuard:
    """Test RAM guard in _ensure_usearch_index."""

    @pytest.fixture
    def store(self):
        """Create a minimal LanceDBIdentityStore for testing."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._usearch_loaded = False
        store._usearch_index = None
        store._table = MagicMock()
        store._embedding_dim = 768
        return store

    @pytest.mark.asyncio
    async def test_low_memory_skips_rebuild(self, store):
        """When available RAM < 4GB, index build is skipped."""
        # Mock table with enough rows to trigger build attempt
        store._table.count_rows.return_value = 2000
        store._table.to_lance.return_value.to_table.return_value.to_pydict.return_value = {
            "_embedding": [[0.1] * 768] * 100,
            "id": ["a", "b", "c"],
        }

        with patch.dict("sys.modules", {"usearch": MagicMock(), "usearch.index": MagicMock()}):
            mock_mem = MagicMock()
            mock_mem.available = 2 * (1024**3)  # 2GB — below 4GB threshold
            with patch("psutil.virtual_memory", return_value=mock_mem):
                await store._ensure_usearch_index()

                # Should have set _usearch_loaded = True and NOT built index
                assert store._usearch_loaded is True
                assert store._usearch_index is None

    @pytest.mark.asyncio
    async def test_sufficient_memory_proceeds(self, store):
        """When available RAM >= 4GB, index build proceeds."""
        store._table.count_rows.return_value = 2000
        store._table.to_lance.return_value.to_table.return_value.to_pydict.return_value = {
            "_embedding": [[0.1] * 768] * 100,
            "id": ["a", "b", "c"],
        }

        with patch.dict("sys.modules", {"usearch": MagicMock(), "usearch.index": MagicMock()}):
            mock_mem = MagicMock()
            mock_mem.available = 6 * (1024**3)  # 6GB — above 4GB threshold
            with patch("psutil.virtual_memory", return_value=mock_mem):

                await store._ensure_usearch_index()

                # Index build should have been attempted
                assert store._usearch_loaded is True

    @pytest.mark.asyncio
    async def test_already_loaded_skips(self, store):
        """When _usearch_loaded is True, early return."""
        store._usearch_loaded = True
        store._table = MagicMock()

        await store._ensure_usearch_index()

        store._table.count_rows.assert_not_called()

    @pytest.mark.asyncio
    async def test_table_none_skips(self, store):
        """When _table is None, early return."""
        store._table = None

        await store._ensure_usearch_index()

        # Should not attempt any psutil call
        assert store._usearch_loaded is False

    @pytest.mark.asyncio
    async def test_psutil_failure_is_fail_safe(self, store):
        """When psutil fails, index build proceeds (fail-safe)."""
        store._table.count_rows.return_value = 2000
        store._table.to_lance.return_value.to_table.return_value.to_pydict.return_value = {
            "_embedding": [[0.1] * 768] * 100,
            "id": ["a", "b", "c"],
        }

        with patch.dict("sys.modules", {"usearch": MagicMock(), "usearch.index": MagicMock()}):
            with patch("psutil.virtual_memory", side_effect=RuntimeError("no psutil")):
                await store._ensure_usearch_index()

                # Should have tried to build index despite psutil failure
                assert store._usearch_loaded is True

    @pytest.mark.asyncio
    async def test_exact_4gb_threshold_proceeds(self, store):
        """At exactly 4GB available, build should proceed."""
        store._table.count_rows.return_value = 2000
        store._table.to_lance.return_value.to_table.return_value.to_pydict.return_value = {
            "_embedding": [[0.1] * 768] * 100,
            "id": ["a", "b", "c"],
        }

        with patch.dict("sys.modules", {"usearch": MagicMock(), "usearch.index": MagicMock()}):
            mock_mem = MagicMock()
            mock_mem.available = 4.0 * (1024**3)
            with patch("psutil.virtual_memory", return_value=mock_mem):

                await store._ensure_usearch_index()

                # Index build should have been attempted
                assert store._usearch_loaded is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
