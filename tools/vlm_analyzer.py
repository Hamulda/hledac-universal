"""
VLMAnalyzer - MLX-VLM for complex image understanding.

Provides vision-language model capabilities using mlx-vlm for detailed
image description and understanding. Optimized for M1 Mac with fail-safe
memory management.

Sprint 71: Singleton pattern, lazy loading, memory pressure handling.
"""

import asyncio
import logging
import os
import tempfile
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Lazy import guard
MLX_VLM_AVAILABLE = False
try:
    from mlx_vlm import load as vlm_load, generate as vlm_generate
    MLX_VLM_AVAILABLE = True
except ImportError:
    logger.debug("mlx-vlm not available")


class VLMAnalyzer:
    """
    Vision-Language Model analyzer using MLX-VLM.

    Features:
    - Singleton model loading (memory efficiency)
    - Lazy initialization
    - Memory pressure handling
    - Automatic cleanup

    Sprint 71: Singleton pattern with proper unload safety.
    """

    _model: Optional[Any] = None
    _processor: Optional[Any] = None
    _lock: Optional[asyncio.Lock] = None

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """Get or create the class-level lock."""
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    async def _ensure_loaded(cls) -> None:
        """Ensure model is loaded (singleton pattern)."""
        async with cls._get_lock():
            if cls._model is not None:
                return

            if not MLX_VLM_AVAILABLE:
                logger.warning("[VLMAnalyzer] mlx-vlm not available")
                return

            try:
                cls._model, cls._processor = await asyncio.to_thread(
                    vlm_load, "mlx-community/llava-1.5-7b-4bit"
                )
                logger.info("[VLMAnalyzer] Model loaded successfully")
            except Exception as e:
                logger.warning(f"[VLMAnalyzer] Model load failed: {e}")
                cls._model = None
                cls._processor = None

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

        # Ensure model loaded
        await self._ensure_loaded()

        if self._model is None or self._processor is None:
            logger.warning("[VLMAnalyzer] Model not available")
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
