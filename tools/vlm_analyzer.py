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

logger = logging.getLogger(__name__)

# Lazy import guard — mlx-vlm is optional
MLX_VLM_AVAILABLE = False
try:
    from mlx_vlm import generate as vlm_generate
    from mlx_vlm import load as vlm_load
    MLX_VLM_AVAILABLE = True
except ImportError:
    logger.debug("mlx-vlm not available")


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

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """Get or create the class-level lock."""
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

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

            if not MLX_VLM_AVAILABLE:
                logger.warning("[VLMAnalyzer] mlx-vlm not available")
                return False

            try:
                cls._model, cls._processor = await asyncio.to_thread(
                    vlm_load, model_id
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
                    import gc
                    gc.collect()
                    try:
                        import mlx.core as mx
                        mx.eval([])  # Flush pending lazy ops before clearing cache (M1 / MLX invariant)
                        mx.metal.clear_cache()
                    except Exception:
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
        except ImportError:
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
            result = await asyncio.to_thread(
                vlm_generate,
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
                except Exception:
                    pass


async def analyze_image_vlm(
    image_bytes: bytes,
    prompt: str = "Describe this image in detail for OSINT."
) -> str:
    """Async wrapper for VLM image analysis."""
    analyzer = VLMAnalyzer()
    return await analyzer.analyze(image_bytes, prompt)
