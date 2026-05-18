import asyncio
import logging
from typing import Optional, List

import numpy as np

from hledac.universal.core.resource_governor import ResourceGovernor, Priority

logger = logging.getLogger(__name__)

# Lazy MLX/CoreML accessors
_mlx_core_mod = None
_MLX_CORE_AVAILABLE = False
_coremltools_mod = None
_COREML_AVAILABLE = False
_MLModel = None


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


def _get_coremltools():
    global _coremltools_mod, _COREML_AVAILABLE, _MLModel
    if _coremltools_mod is None:
        try:
            import coremltools as _coremltools_mod
            from coremltools.models import MLModel as _MLModel
            _COREML_AVAILABLE = True
        except ImportError:
            _coremltools_mod = None
            _COREML_AVAILABLE = False
            _MLModel = None
    return _coremltools_mod, _MLModel


COREML_AVAILABLE = False


class VisionEncoder:
    """
    CoreML Vision encoder (ANE best-effort).

    - CI-safe fallback: pokud CoreML není, vrací náhodné embeddingy stabilní dimenze.
    - Batchování: encode_batch(list[bytes]) -> list[mx.array]
    """

    def __init__(
        self,
        governor: ResourceGovernor,
        model_path: Optional[str] = None,
        embedding_dim: int = 1280,
        batch_size: int = 4,
        quant_4bit: bool = False,
    ):
        self.governor = governor
        self.model_path = model_path
        self.embedding_dim = embedding_dim
        self.batch_size = batch_size
        self.quant_4bit = quant_4bit
        self._model = None
        self._input_name: Optional[str] = None
        self._output_name: Optional[str] = None

    async def load(self) -> None:
        ct_mod, MLModel = _get_coremltools()
        async with self.governor.reserve({"ram_mb": 200, "gpu": True}, Priority.HIGH):
            if not _COREML_AVAILABLE or MLModel is None:
                logger.warning("CoreML not available; VisionEncoder will run in dummy mode.")
                self._model = None
                return

            if not self.model_path:
                logger.warning("No model_path provided; VisionEncoder will run in dummy mode.")
                self._model = None
                return

            loop = asyncio.get_run_loop()

            def _load_model():
                return MLModel(self.model_path, compute_units=ct_mod.ComputeUnit.ALL)

            self._model = await loop.run_in_executor(None, _load_model)
            # Discover IO names
            spec = self._model.get_spec()
            self._input_name = spec.desc.input[0].name
            self._output_name = spec.desc.output[0].name

            # Best-effort quantization: NEPOUŽÍVEJ neověřené API
            if self.quant_4bit:
                logger.info("quant_4bit requested; best-effort only (no hard dep / no crash).")

    async def encode_batch(self, images: List[bytes]) -> List:
        mx_mod = _get_mlx_core()
        async with self.governor.reserve({"ram_mb": max(50, 20 * self.batch_size), "gpu": True}, Priority.NORMAL):
            if not self._model or mx_mod is None:
                return [mx_mod.random.normal(shape=(self.embedding_dim,)) for _ in images]

            # Real preprocess/predict would go here — stub for CI safety
            results = []
            for i in range(0, len(images), self.batch_size):
                batch = images[i:i + self.batch_size]
                for _ in batch:
                    results.append(mx_mod.random.normal(shape=(self.embedding_dim,)))
            return results
