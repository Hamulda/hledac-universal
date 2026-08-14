"""
VLMAnalyzer - Vision-Language Model interface.

Provides vision-language model capabilities via mlx-vlm.

On M1 8GB: no local VLM is configured by default.
OCR-first pipeline is canonical; VLM is deferred to future small model benchmark.

Sprint F216C: No default VLM on M1 8GB. VLM_MODEL_ID env var required for opt-in.
"""

import asyncio
import logging
import os
import tempfile
from typing import Any

from hledac.universal.utils._patterns import make_lazy_lock_classmethod  # F320-REFACTOR-2

logger = logging.getLogger(__name__)

# ISSUE-08 FIX: Import MLX_AVAILABLE from SSOT (zero-import detection)
# Uses importlib.metadata.version("mlx") — no mlx.core import at module load
from hledac.universal.utils.mlx_memory import MLX_AVAILABLE

# ISSUE-08 FIX: Lazy mlx_vlm import helpers — zero-cost until first VLM use
_vlm_generate: Any = None
_vlm_load: Any = None


def _is_mlx_vlm_available() -> bool:
    """Check if mlx_vlm is available (caches result)."""
    global _vlm_generate, _vlm_load
    if _vlm_generate is not None and _vlm_load is not None:
        return True
    if MLX_AVAILABLE:
        try:
            from mlx_vlm import generate as _gen
            from mlx_vlm import load as _load
            _vlm_generate = _gen
            _vlm_load = _load
            return True
        except ImportError:  # noqa: BLE001
            pass
    return False


def _get_vlm_generate():
    """Lazily get vlm_generate function."""
    if not _is_mlx_vlm_available():
        raise RuntimeError("mlx_vlm not available")
    return _vlm_generate


def _get_vlm_load():
    """Lazily get vlm_load function."""
    if not _is_mlx_vlm_available():
        raise RuntimeError("mlx_vlm not available")
    return _vlm_load


class VLMUnavailableError(Exception):
    """Raised when no local VLM is configured on M1 8GB."""
    pass


class VLMAnalyzer:
    """
    Vision-Language Model interface.

    On M1 8GB: No local VLM is configured by default.
    Use analyze() to attempt VLM analysis — returns empty string when unavailable.
    OCR-first pipeline remains canonical.

    To enable VLM: set VLM_MODEL_ID environment variable to an M1-safe model.
    No automatic loading occurs — explicit configuration required.
    """

    _model: Any | None = None
    _processor: Any | None = None
    _lock: asyncio.Lock | None = None

    # F320-REFACTOR-2: lazy lock factory
    _get_lock = classmethod(make_lazy_lock_classmethod("_lock"))

    @classmethod
    def _get_model_id(cls) -> str | None:
        """
        Get configured VLM model ID from environment.

        Returns None if no VLM is configured.
        Must be set explicitly — no default on M1 8GB.
        """
        return os.environ.get("VLM_MODEL_ID")

    @classmethod
    async def _ensure_loaded(cls) -> bool:
        """
        Ensure model is loaded if configured.

        Returns:
            True if model is loaded and available, False otherwise.
        """
        async with cls._get_lock():
            if cls._model is not None:
                return True

            model_id = cls._get_model_id()
            if model_id is None:
                logger.debug("[VLMAnalyzer] No VLM configured — set VLM_MODEL_ID to enable")
                return False

            if not MLX_AVAILABLE:
                logger.warning("[VLMAnalyzer] mlx-vlm requires MLX (not available)")
                return False

            if not _is_mlx_vlm_available():
                logger.warning("[VLMAnalyzer] mlx-vlm not available")
                return False

            try:
                cls._model, cls._processor = await asyncio.to_thread(
                    _get_vlm_load(), model_id
                )
                logger.info(f"[VLMAnalyzer] Model loaded: {model_id}")
                return True
            except Exception as e:
                logger.warning(f"[VLMAnalyzer] Model load failed: {e}")
                cls._model = None
                cls._processor = None
                return False

    @classmethod
    async def unload(cls) -> None:
        """Unload model to free memory (with safety wrapper)."""
        async with cls._get_lock():
            if cls._model is not None:
                try:
                    del cls._model
                    del cls._processor
                    cls._model = None
                    cls._processor = None
                    try:
                        import mlx.core as mx
                        mx.eval([])  # F300-MLX: barrier BEFORE gc.collect()
                        import gc
                        gc.collect()  # F183C: uvolni Python objekty PO GPU flush
                        if hasattr(mx, "clear_cache"):
                            mx.clear_cache()
                        if hasattr(mx.metal, "clear_cache"):
                            mx.metal.clear_cache()
                    except Exception:  # noqa: BLE001
                        pass
                    logger.info("[VLMAnalyzer] Model unloaded")
                except Exception as e:
                    logger.warning(f"[VLMAnalyzer] Unload failed: {e}")

    async def analyze(
        self,
        image_bytes: bytes,
        prompt: str = "Describe this image in detail for OSINT."
    ) -> str:
        """
        Analyze image bytes using VLM.

        Args:
            image_bytes: Raw image bytes.
            prompt: Prompt for the VLM.

        Returns:
            Generated description or empty string on failure.
        """
        # Memory check - skip if under pressure
        try:
            import psutil
            if psutil.Process().memory_info().rss > 5.0 * 1024**3:
                logger.warning("[VLMAnalyzer] Skipping due to memory pressure")
                return ""
        except ImportError:  # noqa: BLE001
            pass

        # Try to load model if configured
        loaded = await self._ensure_loaded()

        if not loaded:
            logger.debug("[VLMAnalyzer] No local VLM configured — OCR-first path is canonical")
            return ""

        # Write to temp file (mlx_vlm expects file path)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                f.write(image_bytes)
                tmp_path = f.name

            # Generate description
            # ISSUE-08 FIX: Use lazy _get_vlm_generate() instead of module-level vlm_generate
            result = await asyncio.to_thread(
                _get_vlm_generate(),
                self._model,
                self._processor,
                image=tmp_path,
                prompt=prompt,
                max_tokens=300
            )

            return result if result else ""

        except Exception as e:
            logger.warning(f"[VLMAnalyzer] Analysis failed: {e}")
            return ""

        finally:
            # Cleanup temp file
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:  # noqa: BLE001
                    pass


async def analyze_image_vlm(
    image_bytes: bytes,
    prompt: str = "Describe this image in detail for OSINT."
) -> str:
    """Async wrapper for VLM image analysis."""
    analyzer = VLMAnalyzer()
    return await analyzer.analyze(image_bytes, prompt)
