import pytest
from unittest.mock import MagicMock, AsyncMock

pytest.importorskip("mlx")
import mlx.core as mx

from hledac.universal.core.resource_governor import ResourceGovernor
from hledac.universal.multimodal.vision_encoder import VisionEncoder
from hledac.universal.multimodal.fusion import MambaFusion


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
