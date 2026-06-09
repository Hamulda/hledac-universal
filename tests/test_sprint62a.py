from unittest.mock import AsyncMock, MagicMock

import pytest

# mlx transitively required by vision_encoder.py and fusion.py imports
# — importorskip at module top is insufficient; skip at test level.
try:
    import mlx.core as mx
except ImportError:
    pytest.skip("mlx not available", allow_module_level=True)

from hledac.universal.core.resource_governor import ResourceGovernor
from hledac.universal.multimodal.fusion import MambaFusion
from hledac.universal.multimodal.vision_encoder import VisionEncoder


@pytest.fixture
def mock_governor():
    g = MagicMock(spec=ResourceGovernor)
    cm = AsyncMock()
    cm.__aenter__.return_value = None
    cm.__aexit__.return_value = None
    g.reserve.return_value = cm
    return g


@pytest.mark.asyncio
async def test_vision_encoder_dummy_mode(mock_governor):
    enc = VisionEncoder(mock_governor, model_path=None, embedding_dim=1280)
    await enc.load()
    out = await enc.encode_batch([b"img1", b"img2"])
    assert len(out) == 2
    assert out[0].shape == (1280,)


def test_mamba_fusion_forward():
    model = MambaFusion(vision_dim=16, text_dim=8, graph_dim=4, hidden=8, output_dim=6)
    v = mx.random.normal(shape=(16,))
    t = mx.random.normal(shape=(8,))
    g = mx.random.normal(shape=(4,))
    y = model(v, t, g)
    assert y.shape == (6,)


# F2.1 — VisionEncoder deterministic pHash fallback
class TestF21PhashFallback:
    """Verify deterministic pHash fallback when CoreML/torch unavailable."""

    @staticmethod
    def _make_image_bytes(width=200, height=200, color=(120, 80, 40)):
        import io

        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (width, height), color).save(buf, "PNG")
        return buf.getvalue()

    def test_phash_shape_1024(self):
        """pHash fallback produces LanceDB-compatible 1024d float32 vector."""
        v = VisionEncoder._phash_deterministic(self._make_image_bytes())
        assert v.shape == (1024,)
        assert v.dtype.name == "float32"

    def test_phash_deterministic(self):
        """Same bytes → same vector (no randomness in fallback path)."""
        b = self._make_image_bytes()
        v1 = VisionEncoder._phash_deterministic(b)
        v2 = VisionEncoder._phash_deterministic(b)
        import numpy as np
        assert np.array_equal(v1, v2), "pHash must be deterministic for dedup"

    def test_phash_in_unit_range(self):
        """pHash values are in {-1.0, +1.0} (binary tiling centered)."""
        v = VisionEncoder._phash_deterministic(self._make_image_bytes())
        assert float(v.min()) >= -1.0
        assert float(v.max()) <= 1.0
        unique_vals = set(v.tolist())
        assert unique_vals.issubset({-1.0, 1.0}), f"non-binary values: {unique_vals}"

    def test_phash_dissimilar_images_have_higher_hamming(self):
        """Visually distinct images have higher Hamming distance than similar ones."""
        import numpy as np
        similar_a = self._make_image_bytes(color=(120, 80, 40))
        similar_b = self._make_image_bytes(color=(130, 85, 45))  # small perturbation
        different = self._make_image_bytes(color=(10, 200, 10))  # far away
        va = VisionEncoder._phash_deterministic(similar_a)
        vb = VisionEncoder._phash_deterministic(similar_b)
        vd = VisionEncoder._phash_deterministic(different)
        hamming_similar = int(np.sum(va != vb))
        hamming_different = int(np.sum(va != vd))
        assert hamming_different > hamming_similar, (
            f"different pair ({hamming_different}) should exceed similar pair ({hamming_similar})"
        )

    @pytest.mark.asyncio
    async def test_encode_batch_fallback_uses_phash(self, mock_governor):
        """encode_batch() in dummy mode uses deterministic pHash, not random noise."""
        import io

        from PIL import Image
        enc = VisionEncoder(mock_governor, model_path=None, embedding_dim=1024)
        await enc.load()  # no model file → model stays None
        img_bytes = io.BytesIO()
        Image.new("RGB", (200, 200), (50, 100, 150)).save(img_bytes, "PNG")
        b = img_bytes.getvalue()
        out = await enc.encode_batch([b, b])  # same bytes twice
        import numpy as np
        assert len(out) == 2
        assert out[0].shape == (1024,)
        assert np.array_equal(out[0], out[1]), "encode_batch must be deterministic in fallback mode"

    def test_phash_corrupt_input_fails_soft(self):
        """Corrupt bytes raise (caller catches) but do not crash module."""
        # The pHash function raises on garbage — encode_batch catches it.
        # Direct call: should raise (not crash interpreter).
        with __import__("pytest").raises(Exception):
            VisionEncoder._phash_deterministic(b"\x00\x01\x02 not an image")
