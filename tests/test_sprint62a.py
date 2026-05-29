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
