"""
Sprint 65D: ModelStore Regression Tests

CI-safe tests for ModelStore serialization/deserialization.
"""


import numpy as np
import pytest


class TestModelStoreRegression:
    """Regression tests for ModelStore."""

    @pytest.fixture
    def store(self, tmp_path):
        """Create a ModelStore instance."""
        from hledac.universal.federated.model_store import ModelStore
        store = ModelStore(path=str(tmp_path / "models"))
        yield store
        store.close()

    def test_put_get_roundtrip(self, store):
        """Test basic put/get roundtrip."""
        weights = {"layer1": np.array([1.0, 2.0, 3.0], dtype=np.float32)}
        store.put_model(1, weights)

        loaded = store.get_model(1)
        assert loaded is not None
        assert "layer1" in loaded
        np.testing.assert_array_almost_equal(loaded["layer1"], weights["layer1"])

    def test_multiple_rounds(self, store):
        """Test multiple model rounds."""
        for round_num in range(1, 4):
            weights = {"layer1": np.full((10,), float(round_num), dtype=np.float32)}
            store.put_model(round_num, weights)

        for round_num in range(1, 4):
            loaded = store.get_model(round_num)
            assert loaded is not None
            expected = np.full((10,), float(round_num), dtype=np.float32)
            np.testing.assert_array_almost_equal(loaded["layer1"], expected)

    def test_different_shapes(self, store):
        """Test different array shapes."""
        weights = {
            "linear": np.array([1.0, 2.0], dtype=np.float32),
            "matrix": np.array([[1, 2], [3, 4]], dtype=np.float32),
            "tensor": np.ones((2, 3, 4), dtype=np.float32),
        }
        store.put_model(1, weights)

        loaded = store.get_model(1)
        assert loaded is not None
        for key in weights:
            np.testing.assert_array_almost_equal(loaded[key], weights[key])

    def test_max_payload_cap(self, store):
        """Test that payload cap is enforced."""
        # Create a large array that would exceed 256KB
        # 256KB = 262144 bytes = 65536 float32 values
        large_array = np.ones(70000, dtype=np.float32)  # ~280KB
        weights = {"large": large_array}

        with pytest.raises(ValueError, match="Payload too large"):
            store.put_model(1, weights)

    @pytest.mark.asyncio
    async def test_async_save_load(self, tmp_path):
        """Test async save/load."""
        from hledac.universal.federated.model_store import ModelStore

        store = ModelStore(path=str(tmp_path / "async_models"))

        # Async save
        weights = {"layer1": np.array([1.0, 2.0], dtype=np.float32)}
        await store.async_save_model("model:1", weights)

        # Async load
        loaded = await store.async_load_model("model:1")
        assert loaded is not None
        np.testing.assert_array_almost_equal(loaded["layer1"], weights["layer1"])

        store.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
