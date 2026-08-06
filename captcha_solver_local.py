"""
Local CAPTCHA Solver — Heavy OCR Models (OFF BY DEFAULT on M1 8GB)

M1 8GB: This module is OFF BY DEFAULT.


It requires: transformers + torch + pytesseract (heavy RAM, ~1-2 GB).

To enable: HLEDAC_ENABLE_CAPTCHA_LOCAL=1

For production on M1 8GB: Use CaptchaSolvingStrategy with 2captcha API
(primary) or Vision/CoreML fallback (secondary) — both are in captcha_solver.py.

This module exists for environments where:
- RAM > 16 GB (e.g., M4 MacBook Pro, cloud GPU)
- 2captcha API is not acceptable (cost/privacy)
- Users explicitly opt-in with HLEDAC_ENABLE_CAPTCHA_LOCAL=1
"""


import asyncio
import io
import logging
import time
from dataclasses import dataclass
import msgspec
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Guard: this module is off-by-default on M1 8GB
_LOCAL_ENABLED = False  # Set to True only if HLEDAC_ENABLE_CAPTCHA_LOCAL=1


def _check_enabled() -> bool:
    """Check if local OCR is enabled."""
    global _LOCAL_ENABLED
    if _LOCAL_ENABLED:
        return True
    import os
    _LOCAL_ENABLED = os.environ.get("HLEDAC_ENABLE_CAPTCHA_LOCAL", "0") == "1"
    return _LOCAL_ENABLED


# ─────────────────────────────────────────────────────────────────────────────
# Local OCR Config
# ─────────────────────────────────────────────────────────────────────────────

class LocalOcrConfig(msgspec.Struct, gc=False):
    """Configuration for local OCR CAPTCHA solving."""
    model_name: str = "microsoft/trocr-small-printed"
    use_mlx: bool = True  # Use MLX acceleration if available
    max_image_size: int = 640  # Max image dimension (pixels)
    confidence_threshold: float = 0.6
    timeout_seconds: float = 30.0


# ─────────────────────────────────────────────────────────────────────────────
# Local OCR Solver
# ─────────────────────────────────────────────────────────────────────────────

class LocalCaptchaSolver:
    """
    Local OCR-based CAPTCHA solver using transformers/torch or pytesseract.

    OFF BY DEFAULT on M1 8GB. Enable with HLEDAC_ENABLE_CAPTCHA_LOCAL=1.

    Falls back gracefully:
        1. transformers/torch (TrOCR) — best accuracy, highest RAM
        2. pytesseract (Tesseract) — moderate accuracy, moderate RAM
        3. fail (return None)
    """

    __slots__ = ('_config', '_ocr_pipeline', '_initialized', '_stats')

    def __init__(self, config: LocalOcrConfig | None = None) -> None:
        self._config = config or LocalOcrConfig()
        self._ocr_pipeline: object | None = None  # lazy init
        self._initialized = False
        self._stats = {"attempted": 0, "solved": 0, "method": "none"}

    async def initialize(self) -> bool:
        """Lazily initialize the OCR pipeline."""
        if self._initialized:
            return True

        if not _check_enabled():
            logger.debug("LocalCaptchaSolver: disabled (HLEDAC_ENABLE_CAPTCHA_LOCAL != 1)")
            return False

        try:
            await self._init_ocr_pipeline()
            self._initialized = True
            return True
        except Exception as e:
            logger.warning(f"LocalCaptchaSolver init failed: {e}")
            return False

    async def _init_ocr_pipeline(self) -> None:
        """Initialize OCR pipeline (async to avoid blocking event loop)."""
        import os

        use_mlx = self._config.use_mlx and "MLX_AVAILABLE" in os.environ

        if use_mlx:
            try:
                await self._init_mlx_pipeline()
                self._stats["method"] = "mlx"
                return
            except Exception as e:
                logger.debug(f"MLX pipeline init failed, falling back: {e}")

        # Fallback: try transformers (CPU, heavy)
        try:
            await self._init_transformers_pipeline()
            self._stats["method"] = "transformers"
            return
        except Exception as e:
            logger.debug(f"Transformers pipeline init failed, falling back: {e}")

        # Last resort: pytesseract
        try:
            self._init_tesseract()
            self._stats["method"] = "tesseract"
            return
        except Exception as e:
            logger.debug(f"Tesseract init failed: {e}")
            raise RuntimeError("All local OCR methods failed") from e

    async def _init_mlx_pipeline(self) -> None:
        """Initialize MLX-accelerated OCR pipeline (lazy).

        MLX path is a placeholder — falls through to transformers if unavailable.
        Real MLX implementation would use mlx-transformers when available.
        """
        # Dynamically import MLX pipeline — only if MLX is available
        # Real impl would use: from mlx.transformers import pipeline
        raise ImportError("MLX pipeline not implemented")

    async def _init_transformers_pipeline(self) -> None:
        """Initialize transformers OCR pipeline (CPU, heavy ~1-2 GB RAM)."""
        # Heavy: transformers + torch loaded eagerly here
        from transformers import AutoProcessor, AutoModelForVision2Seq
        import torch

        processor = AutoProcessor.from_pretrained(self._config.model_name)
        model = AutoModelForVision2Seq.from_pretrained(
            self._config.model_name,
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        )
        # No GPU on M1 — use float32
        if hasattr(torch, 'mps') and torch.backends.mps.is_available():
            model = model.to("mps")

        self._ocr_pipeline = (processor, model)
        logger.info(f"LocalCaptchaSolver: transformers pipeline loaded ({self._config.model_name})")

    def _init_tesseract(self) -> None:
        """Initialize pytesseract (lightweight fallback)."""
        import pytesseract
        # Verify tesseract is installed
        pytesseract.get_tesseract_version()
        self._ocr_pipeline = True  # marker: tesseract available
        logger.info("LocalCaptchaSolver: pytesseract available")

    async def solve(self, image_bytes: bytes) -> str | None:
        """
        Solve CAPTCHA using local OCR.

        Returns:
            Solved text or None if unsolved.
        """
        if not _check_enabled():
            return None

        if not self._initialized:
            await self.initialize()
            if not self._initialized:
                return None

        self._stats["attempted"] += 1

        # Preprocess image
        try:
            from PIL import Image, ImageEnhance
        except ImportError:
            return None

        try:
            img = Image.open(io.BytesIO(image_bytes))
        except Exception:
            return None

        # Resize if too large
        max_size = self._config.max_image_size
        if max(img.size) > max_size:
            ratio = max_size / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

        # Preprocess for better OCR
        img = img.convert("L")
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(2.0)

        # Route to available method
        method = self._stats["method"]
        if method == "transformers" and self._ocr_pipeline:
            result = await self._solve_transformers(img)
        elif method == "tesseract":
            result = self._solve_tesseract(img)
        else:
            return None

        if result and len(result.strip()) > 0:
            self._stats["solved"] += 1
            return result.strip()

        return None

    async def _solve_transformers(self, image: Image.Image) -> str | None:
        """Solve using TrOCR (transformers)."""
        if not self._ocr_pipeline:
            return None

        processor, model = self._ocr_pipeline  # type: ignore[assignment]

        try:
            from transformers import AutoProcessor, AutoModelForVision2Seq
            import torch

            # Run in thread to avoid blocking
            def _run():
                inputs = processor(images=image, return_tensors="pt")
                if hasattr(torch, 'mps') and torch.backends.mps.is_available():
                    inputs = {k: v.to("mps") for k, v in inputs.items()}
                with torch.no_grad():
                    generated_ids = model.generate(
                        **inputs,
                        max_new_tokens=50,
                    )
                return processor.batch_decode(generated_ids, skip_special_tokens=True)[0]

            return await asyncio.to_thread(_run)

        except Exception as e:
            logger.debug(f"Transformers OCR failed: {e}")
            return None

    def _solve_tesseract(self, image: Image.Image) -> str | None:
        """Solve using pytesseract (lightweight)."""
        try:
            import pytesseract
            return pytesseract.image_to_string(
                image,
                config="--psm 8",
            ).strip()
        except Exception as e:
            logger.debug(f"Tesseract OCR failed: {e}")
            return None

    def get_stats(self) -> dict:
        return dict(self._stats)


# ─────────────────────────────────────────────────────────────────────────────
# Standalone function API (for compatibility)
# ─────────────────────────────────────────────────────────────────────────────

async def solve_local(image_bytes: bytes) -> str | None:
    """
    Solve CAPTCHA using local OCR (off-by-default on M1 8GB).

    Enable with HLEDAC_ENABLE_CAPTCHA_LOCAL=1.
    """
    if not _check_enabled():
        return None

    solver = LocalCaptchaSolver()
    if not await solver.initialize():
        return None

    return await solver.solve(image_bytes)
