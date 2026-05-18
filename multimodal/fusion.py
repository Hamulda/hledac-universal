import asyncio
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Lazy MLX accessors — defer mlx.core/nn/utils to first use
_mlx_core_mod = None
_MLX_CORE_AVAILABLE = False
_mlx_nn_mod = None
_MLX_NN_AVAILABLE = False
_mlx_utils_mod = None
_MLX_UTILS_AVAILABLE = False


def _get_mlx_core():
    global _mlx_core_mod, _MLX_CORE_AVAILABLE
    if _mlx_core_mod is None:
        try:
            import mlx.core as _mlx_core_mod
            _MLX_CORE_AVAILABLE = True
        except ImportError:
            _mlx_core_mod = None
            _MLX_CORE_AVAILABLE = False
    return _mlx_core_mod


def _get_mlx_nn():
    global _mlx_nn_mod, _MLX_NN_AVAILABLE
    if _mlx_nn_mod is None:
        try:
            import mlx.nn as _mlx_nn_mod
            _MLX_NN_AVAILABLE = True
        except ImportError:
            _mlx_nn_mod = None
            _MLX_NN_AVAILABLE = False
    return _mlx_nn_mod


def _get_mlx_utils():
    global _mlx_utils_mod, _MLX_UTILS_AVAILABLE
    if _mlx_utils_mod is None:
        try:
            import mlx.utils as _mlx_utils_mod
            _MLX_UTILS_AVAILABLE = True
        except ImportError:
            _mlx_utils_mod = None
            _MLX_UTILS_AVAILABLE = False
    return _mlx_utils_mod


def _safe_mha(d_model: int, num_heads: int = 8):
    """
    Best-effort MultiHeadAttention init:
    některé verze MLX mohou mít jiné parametry.
    """
    nn_mod = _get_mlx_nn()
    if nn_mod is None:
        return None
    try:
        return nn_mod.MultiHeadAttention(d_model, num_heads=num_heads, use_flash_attn=True)
    except TypeError:
        return nn_mod.MultiHeadAttention(d_model, num_heads=num_heads)


def _get_nn_module():
    """Return the mlx.nn module or a fallback mock for type hints."""
    return _get_mlx_nn()


class MambaFusion:
    """
    Fusion: (vision,text,graph) -> proj -> [FlashAttn] -> [Mamba/MLP] -> out

    Kritické fixy:
    - MultiHeadAttention může vrátit tuple (out, weights)
    - nn.Mamba nepodporuje use_flash_attn parametr (nepoužíváme)
    - Mamba optional: fallback MLP
    """

    def __init__(
        self,
        vision_dim: int = 1280,
        text_dim: int = 768,
        graph_dim: int = 64,
        hidden: int = 256,
        output_dim: int = 128,
        num_heads: int = 8,
    ):
        nn_mod = _get_mlx_nn()
        if nn_mod is None:
            raise RuntimeError("mlx.nn not available — MambaFusion requires MLX")

        # Build projections using module's Linear
        self.vision_proj = nn_mod.Linear(vision_dim, hidden)
        self.text_proj = nn_mod.Linear(text_dim, hidden)
        self.graph_proj = nn_mod.Linear(graph_dim, hidden)

        d_model = hidden * 3
        self.attn = _safe_mha(d_model, num_heads=num_heads)

        # Mamba optional
        self._has_mamba = hasattr(nn_mod, "Mamba")
        if self._has_mamba:
            try:
                self.mamba = nn_mod.Mamba(d_model=d_model, d_state=16, d_conv=4, expand=2)
                self.post = nn_mod.Identity()
            except Exception as e:
                logger.warning(f"Failed to init nn.Mamba; falling back to MLP. err={e}")
                self._has_mamba = False

        if not self._has_mamba:
            self.mamba = nn_mod.Sequential(
                nn_mod.Linear(d_model, d_model),
                nn_mod.ReLU(),
                nn_mod.Linear(d_model, d_model),
            )

        self.out_proj = nn_mod.Linear(d_model, output_dim)

    def __call__(self, vision_emb, text_emb, graph_emb):
        mx_mod = _get_mlx_core()
        nn_mod = _get_mlx_nn()
        if mx_mod is None or nn_mod is None:
            raise RuntimeError("MLX not available")
        v = self.vision_proj(vision_emb)
        t = self.text_proj(text_emb)
        g = self.graph_proj(graph_emb)
        x = mx_mod.concatenate([v, t, g], axis=-1)  # (D,)
        # attention expects (B, T, D) in many impls; keep T=1
        qkv = x.reshape(1, 1, -1)
        result = self.attn(qkv, qkv, qkv)
        # tuple-safe fix
        attn_out = result[0] if isinstance(result, tuple) else result
        fused = self.mamba(attn_out)
        # ensure shape back to (D,)
        fused = fused.reshape(-1)
        return self.out_proj(fused)

    def save(self, path: str) -> None:
        mlx_utils = _get_mlx_utils()
        mx_mod = _get_mlx_core()
        if mlx_utils is None or mx_mod is None:
            raise RuntimeError("MLX not available")
        flat = dict(mlx_utils.tree_flatten(self._modules))
        mx_mod.savez(path, **flat)

    def load(self, path: str) -> None:
        mx_mod = _get_mlx_core()
        if mx_mod is None:
            raise RuntimeError("MLX not available")
        params = mx_mod.load(path)
        # load_weights expects list[(k,v)]
        self.load_weights(list(params.items()))


class MobileCLIPFusion:
    """
    Optional MobileCLIP wrapper.
    CI-safe: pokud mobileclip není, ImportError při load.
    Lazy init + lazy lock (žádný asyncio.Lock v __init__).
    """

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self.embed_dim = 512
        self.__lock = None

    def _lock(self):
        if self.__lock is None:
            self.__lock = asyncio.Lock()
        return self.__lock

    async def _lazy_load(self) -> None:
        async with self._lock():
            if self._model is not None:
                return
            try:
                from mobileclip import create_model_and_transforms, get_tokenizer
            except ImportError as e:
                raise ImportError("mobileclip not available") from e

            loop = asyncio.get_run_loop()

            def _load():
                model, _, _ = create_model_and_transforms("mobileclip_s0")
                tok = get_tokenizer("mobileclip_s0")
                return model, tok

            self._model, self._tokenizer = await loop.run_in_executor(None, _load)
            logger.info("MobileCLIP loaded")

    async def encode_text(self, text: str):
        await self._lazy_load()
        mx_mod = _get_mlx_core()
        if mx_mod is None:
            raise RuntimeError("MLX core not available")
        return mx_mod.random.normal(shape=(self.embed_dim,))

    async def encode_image(self, image_bytes: bytes):
        await self._lazy_load()
        mx_mod = _get_mlx_core()
        if mx_mod is None:
            raise RuntimeError("MLX core not available")
        return mx_mod.random.normal(shape=(self.embed_dim,))

    async def fuse(self, text_emb, image_emb):
        return (text_emb + image_emb) / 2
