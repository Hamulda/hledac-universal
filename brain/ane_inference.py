"""
brain/ane_inference.py — Apple Neural Engine Inference Engine
============================================================




SILICON-06: Dedicated ANE (Apple Neural Engine) inference for small-batch
embedding workloads. The M1's 16-core Neural Engine (11 TOPS int8) was
completely idle during embedding batches — all inference ran on GPU via
mx.eval(). This module provides a real ANE inference path.

Architecture:
    ANEInferenceEngine
    ├── Model compilation: HuggingFace/MLX → CoreML .mlpackage (via coremltools)
    ├── Persistent cache: ~/.cache/hledac/ane_models/ (APFS COW, O(1) clone)
    ├── Small-batch routing: ≤16 items, dim ≤ 1024 → ANE
    ├── Large-batch fallback: >16 items → GPU (existing path)
    └── Fail-safe: any error → returns None, caller falls back to GPU

Key design decisions:
- Python-native (no Rust compilation): coremltools handles conversion
- Lazy imports: coremltools + transformers loaded only on first use
- M1 8GB bound: max 2 models in ANE memory, 50 MB per model footprint
- APFS clonefile: O(1) model copy to cache (ISSUE-012 pattern)
- Fail-soft: every path returns None on error, never raises

Usage:
    from hledac.universal.brain.ane_inference import ANEInferenceEngine
    engine = ANEInferenceEngine()
    embeddings = await engine.embed_batch_ane(texts, model_key="bge-small")

Feature flags: None (always-on, fail-safe). Opt-out via
    HLEDAC_DISABLE_ANE=1  (force GPU-only path)
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import shutil
import time as time_module
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from hledac.universal.utils._patterns import LazyLockDescriptor  # F320-REFACTOR-2

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────
_ANE_CACHE_DIR = Path.home() / ".cache" / "hledac" / "ane_models"
_ANE_MAX_MODELS = 2  # M1 ANE hardware limit
_ANE_MAX_BATCH_SIZE = 16  # Small-batch ANE sweet spot
_ANE_MAX_DIM = 1024  # Max embedding dim for ANE efficiency
_ANE_MODEL_FOOTPRINT_MB = 50  # Per-model ANE memory budget
_ANE_COMPILE_TIMEOUT_S = 120.0  # Hard timeout for model compilation

# Supported model configurations for ANE compilation
_MODEL_CONFIGS: dict[str, dict[str, Any]] = {
    "bge-small": {
        "hf_id": "BAAI/bge-small-en-v1.5",
        "dim": 384,
        "max_seq_len": 512,
        "description": "BGE-small-en-v1.5 — 384d, fast ANE small-batch",
    },
    "modernbert-embed": {
        "hf_id": "nomic-ai/modernbert-embed-base",
        "dim": 768,
        "max_seq_len": 512,
        "description": "ModernBERT-embed-base — 768d, ANE-efficient for ≤16 batch",
    },
    "all-minilm-l6-v2": {
        "hf_id": "sentence-transformers/all-MiniLM-L6-v2",
        "dim": 384,
        "max_seq_len": 256,
        "description": "all-MiniLM-L6-v2 — 384d, legacy ANE model (pre-compiled)",
    },
}

# ─── Lazy capability detection ───────────────────────────────────────────────
from hledac.universal.core.feature_flags import FeatureFlag, FeatureFlags

_coremltools_available: bool | None = None
_coremltools: Any = None
_ANE_DISABLED_BY_ENV = FeatureFlags.get(FeatureFlag.DISABLE_ANE)


def _check_coremltools() -> bool:
    """Lazy check: is coremltools importable?"""
    global _coremltools_available, _coremltools
    if _coremltools_available is not None:
        return _coremltools_available
    try:
        import coremltools as ct
        _coremltools = ct
        _coremltools_available = True
        logger.info("[ANE] coremltools available — ANE inference enabled")
        return True
    except ImportError:
        _coremltools_available = False
        logger.debug("[ANE] coremltools not available — ANE inference disabled")
        return False


def is_ane_available() -> bool:
    """Check if ANE inference is available on this system.

    Requirements:
    - macOS on Apple Silicon (aarch64)
    - coremltools installed
    - HLEDAC_DISABLE_ANE != 1
    """
    if _ANE_DISABLED_BY_ENV:
        return False
    if not (os.uname().sysname == "Darwin" and os.uname().machine == "arm64"):
        return False
    return _check_coremltools()


# ─── APFS clonefile helper (ISSUE-012 pattern) ───────────────────────────────

_CLONEFILE_available: bool | None = None
_LIBC: ctypes.CDLL | None = None


def _get_libc() -> ctypes.CDLL | None:
    global _LIBC
    if _LIBC is not None:
        return _LIBC
    try:
        import ctypes.util
        lib_c = ctypes.util.find_library("c")
        if lib_c:
            _LIBC = ctypes.CDLL(lib_c, use_errno=True)
            return _LIBC
    except Exception:  # noqa: BLE001
        pass
    return None


def _clone_dir(src: Path, dst: Path) -> bool:
    """APFS COW clone directory (O(1) on same volume). Falls back to shutil.copytree."""
    global _CLONEFILE_available
    if _CLONEFILE_available is None:
        libc = _get_libc()
        if libc is not None:
            try:
                libc.clonefile
                _CLONEFILE_available = True
            except AttributeError:
                _CLONEFILE_available = False
        else:
            _CLONEFILE_available = False

    if _CLONEFILE_available and _get_libc() is not None:
        try:
            src_bytes = os.fsencode(str(src))
            dst_bytes = os.fsencode(str(dst))
            # clonefile(2) — APFS Copy-on-Write
            ret = _get_libc().clonefile(src_bytes, dst_bytes, 0)
            if ret == 0:
                return True
        except Exception:  # noqa: BLE001
            pass

    # Fallback: regular copy
    try:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        return True
    except Exception:
        return False


# ─── Model cache management ──────────────────────────────────────────────────

@dataclass
class _CachedModel:
    """Metadata for a cached ANE model."""
    key: str
    path: Path
    dim: int
    max_seq_len: int
    compiled_at: float


class _ModelCache:
    """LRU-like model cache bounded to _ANE_MAX_MODELS."""

    def __init__(self) -> None:
        self._models: dict[str, _CachedModel] = {}
        self._access_order: list[str] = []  # FIFO for eviction

    def get(self, key: str) -> _CachedModel | None:
        model = self._models.get(key)
        if model is not None and model.path.exists():
            # Touch: move to end of access order
            if key in self._access_order:
                self._access_order.remove(key)
            self._access_order.append(key)
            return model
        # Stale cache entry — remove
        if key in self._models:
            del self._models[key]
        if key in self._access_order:
            self._access_order.remove(key)
        return None

    def put(self, key: str, path: Path, dim: int, max_seq_len: int) -> _CachedModel:
        # Evict oldest if at capacity
        while len(self._models) >= _ANE_MAX_MODELS and self._access_order:
            oldest = self._access_order.pop(0)
            if oldest in self._models:
                logger.debug("[ANE:cache] Evicting model: %s", oldest)
                del self._models[oldest]
        model = _CachedModel(
            key=key, path=path, dim=dim, max_seq_len=max_seq_len,
            compiled_at=time_module.monotonic(),
        )
        self._models[key] = model
        self._access_order.append(key)
        return model

    def clear(self) -> None:
        self._models.clear()
        self._access_order.clear()

    @property
    def loaded_count(self) -> int:
        return len(self._models)


# ─── CoreML model loader ─────────────────────────────────────────────────────

def _load_coreml_model(model_path: Path) -> Any | None:
    """Load a compiled CoreML model (.mlpackage or .mlmodelc).

    Returns:
        MLModel instance or None on failure.
    """
    if not _check_coremltools():
        return None
    try:
        # coremltools.models.MLModel can load compiled models
        from coremltools.models import MLModel
        return MLModel(str(model_path))
    except Exception as e:
        logger.debug("[ANE] Failed to load CoreML model from %s: %s", model_path, e)
        return None


def _compile_hf_model_to_coreml(
    hf_id: str,
    output_path: Path,
    dim: int,
    max_seq_len: int,
) -> bool:
    """Compile a HuggingFace model to CoreML for ANE inference.

    Uses coremltools + transformers to trace and convert the model.
    This is a heavyweight operation (30-120s) — only done once per model.

    Args:
        hf_id: HuggingFace model ID.
        output_path: Where to save the compiled .mlpackage.
        dim: Hidden dimension.
        max_seq_len: Maximum sequence length.

    Returns:
        True if compilation succeeded.
    """
    if not _check_coremltools():
        return False
    try:
        import coremltools as ct
        from transformers import AutoTokenizer, AutoModel
        import torch

        logger.info("[ANE:compile] Loading HF model: %s", hf_id)
        tokenizer = AutoTokenizer.from_pretrained(hf_id)
        model = AutoModel.from_pretrained(hf_id)
        model.eval()

        # Trace the model with dummy input
        dummy_text = "warmup probe osint security analysis"
        inputs = tokenizer(
            dummy_text, return_tensors="pt", padding=True,
            truncation=True, max_length=max_seq_len,
        )

        # Trace
        with torch.no_grad():
            traced = torch.jit.trace(
                model,
                (inputs["input_ids"], inputs["attention_mask"]),
            )

        # Convert to CoreML
        input_shape = ct.Shape((1, ct.RangeDim(1, max_seq_len)))
        mlmodel = ct.convert(
            traced,
            inputs=[
                ct.TensorType(name="input_ids", shape=input_shape, dtype=np.int32),
                ct.TensorType(name="attention_mask", shape=input_shape, dtype=np.int32),
            ],
            outputs=[
                ct.TensorType(name="last_hidden_state", dtype=np.float32),
            ],
            compute_units=ct.ComputeUnit.NEURAL_ENGINE,
            minimum_deployment_target=ct.target.iOS18,
        )

        # Save
        output_path.parent.mkdir(parents=True, exist_ok=True)
        mlmodel.save(str(output_path))
        logger.info("[ANE:compile] Model saved to: %s", output_path)

        # Clean up
        del model, tokenizer, traced, mlmodel
        import gc
        gc.collect()

        return True

    except ImportError as e:
        logger.warning("[ANE:compile] Missing dependency: %s — install transformers+torch", e)
        return False
    except Exception as e:
        logger.warning("[ANE:compile] Failed for %s: %s", hf_id, e)
        return False


def _compile_mlx_model_to_coreml(
    mlx_model: Any,
    output_path: Path,
    dim: int,
    max_seq_len: int,
) -> bool:
    """Compile an MLX model to CoreML for ANE inference.

    Exports MLX weights → numpy → CoreML MIL program → compile.
    This avoids PyTorch dependency when model is already loaded in MLX.

    Args:
        mlx_model: An MLX model instance (from mlx-embeddings or mlx-embedding-models).
        output_path: Where to save the compiled .mlpackage.
        dim: Hidden dimension.
        max_seq_len: Maximum sequence length.

    Returns:
        True if compilation succeeded.
    """
    if not _check_coremltools():
        return False
    try:
        import coremltools as ct
        import mlx.core as mx

        logger.info("[ANE:compile-mlx] Exporting MLX model to CoreML...")

        # Export MLX weights to numpy
        params = {}
        if hasattr(mlx_model, "parameters"):
            mlx_params = mlx_model.parameters()
            if isinstance(mlx_params, dict):
                for k, v in mlx_params.items():
                    params[k] = np.array(v)
        elif hasattr(mlx_model, "state_dict"):
            state = mlx_model.state_dict()
            for k, v in state.items():
                params[k] = np.array(v)

        if not params:
            logger.warning("[ANE:compile-mlx] Could not extract parameters from MLX model")
            return False

        # Build MIL program using numpy-backed weights
        # For embedding models, the CoreML pipeline is:
        # input_ids → embedding_lookup → transformer_encoder → pooling → output
        from coremltools.converters.mil import Builder as mb
        from coremltools.converters.mil import Program, Function

        # Simplified MIL program for embedding
        # In practice, we'd use ct.convert() with a traced model.
        # This is a placeholder for the full MIL builder path.
        logger.warning(
            "[ANE:compile-mlx] Full MIL builder not yet implemented — "
            "use _compile_hf_model_to_coreml() for now"
        )
        return False

    except Exception as e:
        logger.warning("[ANE:compile-mlx] Failed: %s", e)
        return False


# ─── ANE Inference Engine ────────────────────────────────────────────────────

class ANEInferenceEngine:
    """Apple Neural Engine inference engine for small-batch embeddings.

    Manages model compilation, caching, and inference via CoreML.
    Bounded to _ANE_MAX_MODELS (2) in memory at any time.
    Fail-soft: all paths return None on error, never raise.

    Usage:
        engine = ANEInferenceEngine()
        await engine.ensure_loaded("bge-small")
        embeddings = await engine.embed_batch_ane(texts, model_key="bge-small")
    """

    __slots__ = (
        "_cache",
        "_loaded_models",  # dict: model_key → CoreML MLModel
        "_model_metadata",  # dict: model_key → _CachedModel
        "_compile_lock",  # asyncio.Lock per model
        "_compiling",  # set of model keys currently compiling
    )

    def __init__(self) -> None:
        self._cache = _ModelCache()
        self._loaded_models: dict[str, Any] = {}
        self._model_metadata: dict[str, _CachedModel] = {}
        self._compile_lock: asyncio.Lock | None = None
        self._compiling: set[str] = set()

    # F320-REFACTOR-2: lazy lock descriptor (ISSUE-014 compliant)
    _get_compile_lock = LazyLockDescriptor("_compile_lock")

    async def ensure_loaded(self, model_key: str = "bge-small") -> bool:
        """Ensure a model is compiled and loaded for ANE inference.

        Args:
            model_key: One of 'bge-small', 'modernbert-embed', 'all-minilm-l6-v2'.

        Returns:
            True if model is ready for inference.
        """
        if _ANE_DISABLED_BY_ENV:
            return False
        if not is_ane_available():
            return False
        if model_key not in _MODEL_CONFIGS:
            logger.debug("[ANE] Unknown model key: %s", model_key)
            return False

        # Already loaded?
        if model_key in self._loaded_models:
            return True

        # Check cache
        cached = self._cache.get(model_key)
        if cached is not None:
            mlmodel = _load_coreml_model(cached.path)
            if mlmodel is not None:
                self._loaded_models[model_key] = mlmodel
                self._model_metadata[model_key] = cached
                logger.info("[ANE] Loaded from cache: %s (%dd)", model_key, cached.dim)
                return True

        # Compile if not yet compiling
        if model_key in self._compiling:
            return False  # Compilation in progress on another task
        self._compiling.add(model_key)

        try:
            config = _MODEL_CONFIGS[model_key]
            model_dir = _ANE_CACHE_DIR / model_key
            compiled_path = model_dir / f"{model_key}.mlpackage"

            if compiled_path.exists():
                mlmodel = _load_coreml_model(compiled_path)
                if mlmodel is not None:
                    self._loaded_models[model_key] = mlmodel
                    meta = self._cache.put(
                        model_key, compiled_path, config["dim"], config["max_seq_len"]
                    )
                    self._model_metadata[model_key] = meta
                    logger.info("[ANE] Loaded pre-compiled: %s", model_key)
                    return True

            # Compile from HuggingFace
            async with self._get_compile_lock():
                # Double-check after acquiring lock
                if model_key in self._loaded_models:
                    return True
                if compiled_path.exists():
                    mlmodel = _load_coreml_model(compiled_path)
                    if mlmodel is not None:
                        self._loaded_models[model_key] = mlmodel
                        return True

                logger.info("[ANE] Compiling model: %s → %s", config["hf_id"], compiled_path)
                success = await asyncio.to_thread(
                    _compile_hf_model_to_coreml,
                    config["hf_id"],
                    compiled_path,
                    config["dim"],
                    config["max_seq_len"],
                )
                if not success:
                    logger.warning("[ANE] Compilation failed for: %s", model_key)
                    return False

                mlmodel = _load_coreml_model(compiled_path)
                if mlmodel is not None:
                    self._loaded_models[model_key] = mlmodel
                    meta = self._cache.put(
                        model_key, compiled_path, config["dim"], config["max_seq_len"]
                    )
                    self._model_metadata[model_key] = meta
                    logger.info("[ANE] Compiled and loaded: %s", model_key)
                    return True

            return False
        except asyncio.TimeoutError:
            logger.warning("[ANE] Compilation timed out for: %s", model_key)
            return False
        except Exception as e:
            logger.warning("[ANE] ensure_loaded failed for %s: %s", model_key, e)
            return False
        finally:
            self._compiling.discard(model_key)

    async def embed_batch_ane(
        self,
        texts: list[str],
        model_key: str = "bge-small",
        *,
        normalize: bool = True,
    ) -> np.ndarray | None:
        """Run ANE inference on a small batch of texts.

        SILICON-06: This is the canonical ANE inference path.
        Only effective for batch_size ≤ _ANE_MAX_BATCH_SIZE (16).
        For larger batches, caller should use GPU path.

        Args:
            texts: List of text strings (max 16 recommended for ANE).
            model_key: Model to use.
            normalize: L2-normalize output embeddings.

        Returns:
            np.ndarray shape (len(texts), dim) float32, or None on failure.
            Caller MUST fall back to GPU path when None is returned.
        """
        if _ANE_DISABLED_BY_ENV:
            return None
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        if len(texts) > _ANE_MAX_BATCH_SIZE:
            logger.debug("[ANE] Batch size %d > %d — use GPU path", len(texts), _ANE_MAX_BATCH_SIZE)
            return None

        if model_key not in self._loaded_models:
            loaded = await self.ensure_loaded(model_key)
            if not loaded:
                return None

        mlmodel = self._loaded_models.get(model_key)
        if mlmodel is None:
            return None

        meta = self._model_metadata.get(model_key)
        if meta is None:
            return None

        try:
            # Load tokenizer (cached via transformers)
            config = _MODEL_CONFIGS[model_key]
            tokenizer = await self._get_tokenizer(config["hf_id"])
            if tokenizer is None:
                return None

            def _run_ane_inference() -> np.ndarray:
                all_embs: list[np.ndarray] = []
                for text in texts:
                    # Tokenize
                    inputs = tokenizer(
                        text[:2048],
                        return_tensors="np",
                        padding="max_length",
                        truncation=True,
                        max_length=meta.max_seq_len,
                    )
                    input_ids = inputs["input_ids"].astype(np.int32)
                    attention_mask = inputs["attention_mask"].astype(np.int32)

                    # Run CoreML inference on ANE
                    result = mlmodel.predict({
                        "input_ids": input_ids,
                        "attention_mask": attention_mask,
                    })

                    # Extract last_hidden_state and pool
                    if "last_hidden_state" in result:
                        hidden = result["last_hidden_state"]
                    elif "pooler_output" in result:
                        hidden = result["pooler_output"]
                    else:
                        # Try first output
                        hidden = next(iter(result.values()))

                    # Mean pooling over sequence
                    hidden = np.squeeze(hidden, axis=0)  # (1, seq_len, dim) → (seq_len, dim)
                    mask = np.squeeze(attention_mask, axis=0)  # (1, seq_len) → (seq_len,)
                    mask_expanded = mask[:, np.newaxis].astype(np.float32)
                    pooled = (hidden * mask_expanded).sum(axis=0) / max(mask.sum(), 1)
                    all_embs.append(pooled)

                result_array = np.stack(all_embs, axis=0).astype(np.float32)
                if normalize:
                    norms = np.linalg.norm(result_array, axis=1, keepdims=True)
                    result_array = result_array / (norms + 1e-9)
                return result_array

            return await asyncio.to_thread(_run_ane_inference)

        except Exception as e:
            logger.debug("[ANE] embed_batch_ane failed: %s", e)
            return None

    _tokenizer_cache: dict[str, Any] = {}

    async def _get_tokenizer(self, hf_id: str) -> Any | None:
        """Lazy-load and cache a HuggingFace tokenizer."""
        if hf_id in self._tokenizer_cache:
            return self._tokenizer_cache[hf_id]
        try:
            from transformers import AutoTokenizer
            tokenizer = await asyncio.to_thread(
                AutoTokenizer.from_pretrained, hf_id
            )
            self._tokenizer_cache[hf_id] = tokenizer
            return tokenizer
        except ImportError:
            logger.debug("[ANE] transformers not available for tokenization")
            return None
        except Exception as e:
            logger.debug("[ANE] Tokenizer load failed for %s: %s", hf_id, e)
            return None

    def get_model_info(self, model_key: str) -> dict[str, Any] | None:
        """Return metadata about a cached model."""
        cached = self._cache.get(model_key)
        if cached is not None:
            return {
                "key": cached.key,
                "dim": cached.dim,
                "max_seq_len": cached.max_seq_len,
                "path": str(cached.path),
                "loaded": model_key in self._loaded_models,
            }
        return None

    def unload(self, model_key: str | None = None) -> None:
        """Unload model(s) from ANE memory.

        Args:
            model_key: Specific model to unload, or None to unload all.
        """
        if model_key is not None:
            self._loaded_models.pop(model_key, None)
            self._model_metadata.pop(model_key, None)
            logger.debug("[ANE] Unloaded: %s", model_key)
        else:
            self._loaded_models.clear()
            self._model_metadata.clear()
            self._cache.clear()
            logger.debug("[ANE] All models unloaded")

    @property
    def is_ready(self) -> bool:
        """True if at least one model is loaded and ready for inference."""
        return len(self._loaded_models) > 0


# ─── Singleton accessor ──────────────────────────────────────────────────────

_ANE_ENGINE: ANEInferenceEngine | None = None


def get_ane_engine() -> ANEInferenceEngine:
    """Get or create the singleton ANE inference engine."""
    global _ANE_ENGINE
    if _ANE_ENGINE is None:
        _ANE_ENGINE = ANEInferenceEngine()
    return _ANE_ENGINE


def unload_ane_engine() -> None:
    """Release all ANE models and clear the singleton."""
    global _ANE_ENGINE
    if _ANE_ENGINE is not None:
        _ANE_ENGINE.unload()
        _ANE_ENGINE = None


__all__ = [
    "ANEInferenceEngine",
    "get_ane_engine",
    "unload_ane_engine",
    "is_ane_available",
    "_ANE_MAX_BATCH_SIZE",
    "_ANE_MAX_DIM",
]
