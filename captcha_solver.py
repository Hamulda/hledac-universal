"""
Vision Captcha Solver - Apple Vision/CoreML based CAPTCHA solving
=================================================================

CAPTCHA solver using YOLO CoreML model and VNCoreMLModel.
Designed for M1/Apple Silicon with ANE acceleration.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)

# Lazy imports for Apple Vision frameworks
_COREML_AVAILABLE = False
_VN_AVAILABLE = False
_YOLO_AVAILABLE = False

# CoreML tools version
_COREMLTOOLS_VERSION: Optional[float] = None

try:
    import coremltools as ct
    _COREML_AVAILABLE = True
    try:
        _COREMLTOOLS_VERSION = float(ct.__version__)
    except (ValueError, TypeError):
        _COREMLTOOLS_VERSION = 6.0  # Assume 6.0 if parsing fails
except ImportError:
    _COREML_AVAILABLE = False


def has_apple_intelligence() -> bool:
    """
    Check if Apple Intelligence (CoreML >= 6.0) is available.

    Returns:
        True if coremltools >= 6.0 is available
    """
    if not _COREML_AVAILABLE:
        return False

    return _COREMLTOOLS_VERSION >= 6.0


def _get_vn_core_ml_model():
    """Get VNCoreMLModel with lazy import."""
    global _VN_AVAILABLE
    if _VN_AVAILABLE:
        try:
            from Vision import VNCoreMLModel
            return VNCoreMLModel
        except ImportError:
            _VN_AVAILABLE = False
            return None
    return None


def _get_vn_request():
    """Get VNCoreMLRequest with lazy import."""
    global _VN_AVAILABLE
    if _VN_AVAILABLE:
        try:
            from Vision import VNCoreMLRequest
            return VNCoreMLRequest
        except ImportError:
            _VN_AVAILABLE = False
            return None
    return None


# Check Vision framework
try:
    from Vision import VNCoreMLModel, VNCoreMLRequest, VNImageRequestHandler
    _VN_AVAILABLE = True
except ImportError:
    _VN_AVAILABLE = False


class VisionCaptchaSolver:
    """
    CAPTCHA solver using Apple Vision framework and CoreML.

    Features:
        - YOLO CoreML model for grid CAPTCHAs
        - VNCoreMLModel for text recognition
        - Result caching with 1-hour expiration
    """

    # Class-level cache
    _result_cache: OrderedDict = OrderedDict()
    _cache_timestamps: dict[str, float] = {}
    CACHE_TTL = 3600  # 1 hour in seconds
    MAX_CACHE_SIZE = 100

    def __init__(
        self,
        model_path: Optional[str] = None,
        use_ane: bool = True
    ):
        """
        Initialize VisionCaptchaSolver.

        Args:
            model_path: Path to YOLO CoreML model (optional)
            use_ane: Whether to use ANE acceleration
        """
        self.model_path = model_path
        self.use_ane = use_ane and has_apple_intelligence()
        self._model = None
        self._vn_model = None

        logger.info(
            f"VisionCaptchaSolver initialized: model={model_path}, "
            f"ane={self.use_ane}"
        )

    def _load_model(self):
        """Load the CoreML model if not already loaded."""
        if self._model is not None:
            return

        if not _COREML_AVAILABLE:
            logger.warning("CoreML tools not available")
            return

        if self.model_path is None:
            logger.info("No model path provided, using text-only mode")
            return

        try:
            # Load CoreML model
            self._model = ct.models.MLModel(self.model_path)
            logger.info(f"Loaded CoreML model from {self.model_path}")

            # Try to create VNCoreMLModel for Vision framework
            if _VN_AVAILABLE:
                try:
                    self._vn_model = VNCoreMLModel.modelForMLModel(self._model)
                except Exception as e:
                    logger.warning(f"Failed to create VNCoreMLModel: {e}")

        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            self._model = None

    def _get_cache_key(self, data: bytes) -> str:
        """Generate cache key from data hash."""
        return hashlib.sha256(data).hexdigest()[:16]

    def _get_cached_result(self, cache_key: str) -> Optional[object]:
        """Get cached result if not expired."""
        if cache_key not in self._result_cache:
            return None

        # Check expiration
        timestamp = self._cache_timestamps.get(cache_key, 0)
        if time.time() - timestamp > self.CACHE_TTL:
            # Expired - remove from cache
            del self._result_cache[cache_key]
            del self._cache_timestamps[cache_key]
            return None

        # Move to end (most recently used)
        self._result_cache.move_to_end(cache_key)
        return self._result_cache[cache_key]

    def _set_cached_result(self, cache_key: str, result: object):
        """Cache result with timestamp."""
        # Evict oldest if at capacity
        while len(self._result_cache) >= self.MAX_CACHE_SIZE:
            oldest_key = next(iter(self._result_cache))
            del self._result_cache[oldest_key]
            self._cache_timestamps.pop(oldest_key, None)

        self._result_cache[cache_key] = result
        self._cache_timestamps[cache_key] = time.time()

    def solve_grid(
        self,
        image_bytes: bytes
    ) -> list[int]:
        """
        Solve grid CAPTCHA (e.g., "select all images with traffic lights").

        Args:
            image_bytes: Raw image data

        Returns:
            List of selected grid indices
        """
        # Check cache
        cache_key = self._get_cache_key(image_bytes)
        cached = self._get_cached_result(cache_key)
        if cached is not None:
            return cached

        result: list[int] = []

        if not _VN_AVAILABLE or self._model is None:
            logger.warning("Vision framework or model not available")
            return result

        try:
            self._load_model()

            if self._vn_model is None:
                logger.warning("VNCoreMLModel not available")
                return result

            # Create Vision request

            # For now, return empty - full implementation would:
            # 1. Convert image_bytes to CVPixelBuffer
            # 2. Create VNImageRequestHandler
            # 3. Perform request
            # 4. Parse results to grid indices
            logger.debug("Grid solving not fully implemented")

        except Exception as e:
            logger.error(f"Grid solving failed: {e}")

        # Cache result
        self._set_cached_result(cache_key, result)
        return result

    def solve_text(
        self,
        image_bytes: bytes
    ) -> str:
        """
        Solve text-based CAPTCHA.

        Args:
            image_bytes: Raw image data

        Returns:
            Recognized text string
        """
        # Check cache
        cache_key = self._get_cache_key(image_bytes)
        cached = self._get_cached_result(cache_key)
        if cached is not None:
            return cached

        result = ""

        if not _VN_AVAILABLE:
            logger.warning("Vision framework not available")
            return result

        try:
            self._load_model()

            # For now, return empty - full implementation would:
            # 1. Convert image_bytes to CVPixelBuffer
            # 2. Use VNRecognizeTextRequest
            # 3. Return recognized text
            logger.debug("Text recognition not fully implemented")

        except Exception as e:
            logger.error(f"Text solving failed: {e}")

        # Cache result
        self._set_cached_result(cache_key, result)
        return result

    def clear_cache(self):
        """Clear the result cache."""
        self._result_cache.clear()
        self._cache_timestamps.clear()
        logger.info("CAPTCHA solver cache cleared")

    @classmethod
    def get_cache_stats(cls) -> Dict:
        """Get cache statistics."""
        return {
            'size': len(cls._result_cache),
            'max_size': cls.MAX_CACHE_SIZE,
            'ttl_seconds': cls.CACHE_TTL
        }

    # ========================================================================
    # P7: OCR and 2Captcha integration
    # ========================================================================

    async def solve_image_captcha(self, image_bytes: bytes) -> Optional[str]:
        """
        OCR via pytesseract (free, local). Returns None if unavailable.

        Preprocessing for M1-optimized OCR accuracy:
        - Grayscale conversion
        - Thresholding to binary
        """
        try:
            import io

            import pytesseract
            from PIL import Image
        except ImportError:
            logger.debug("pytesseract not available, trying 2captcha")
            return None

        try:
            img = Image.open(io.BytesIO(image_bytes))
            # Preprocessing for better OCR accuracy on M1
            img = img.convert("L")  # grayscale
            img = img.point(lambda x: 0 if x < 128 else 255)  # threshold
            result = pytesseract.image_to_string(img, config="--psm 8").strip()
            if result:
                logger.debug(f"pytesseract OCR succeeded: {result[:50]}...")
            return result if result else None
        except Exception as e:
            logger.warning(f"pytesseract OCR failed: {e}")
            return None

    async def solve_via_2captcha(self, image_bytes: bytes) -> Optional[str]:
        """
        Cloud CAPTCHA solving via 2Captcha API. Only if API key configured.
        Polls with backoff (10 attempts, 3s interval).
        """
        api_key = getattr(self, "_2captcha_api_key", None)
        if not api_key:
            logger.debug("2Captcha API key not configured")
            return None

        try:
            import base64

            import aiohttp
        except ImportError:
            logger.warning("aiohttp not available for 2captcha")
            return None

        b64 = base64.b64encode(image_bytes).decode()
        try:
            async with aiohttp.ClientSession() as session:
                # Submit CAPTCHA
                async with session.post(
                    "http://2captcha.com/in.php",
                    data={"key": api_key, "method": "base64", "body": b64}
                ) as r:
                    result = await r.text()
                if not result.startswith("OK|"):
                    logger.warning(f"2Captcha submit failed: {result}")
                    return None
                captcha_id = result.split("|")[1]

                # Poll for result with backoff
                for _ in range(10):
                    await asyncio.sleep(3)
                    async with session.get(
                        f"http://2captcha.com/res.php?key={api_key}&action=get&id={captcha_id}"
                    ) as r:
                        res = await r.text()
                    if res.startswith("OK|"):
                        solution = res.split("|")[1]
                        logger.debug(f"2Captcha solved: {solution[:50]}...")
                        return solution
                    if res == "CAPCHA_NOT_READY":
                        continue
                    # Any other error
                    logger.warning(f"2Captcha poll error: {res}")
                    break
        except Exception as e:
            logger.warning(f"2Captcha request failed: {e}")
        return None

    async def solve(self, image_bytes: bytes) -> Optional[str]:
        """
        Unified CAPTCHA solving: OCR first (free), 2Captcha fallback (paid).

        Args:
            image_bytes: Raw CAPTCHA image data

        Returns:
            Solved CAPTCHA text or None if unsolved
        """
        # Check cache first
        cache_key = self._get_cache_key(image_bytes)
        cached = self._get_cached_result(cache_key)
        if cached is not None:
            return cached

        # Try local OCR first (free, no API key needed)
        result = await self.solve_image_captcha(image_bytes)
        if result:
            self._set_cached_result(cache_key, result)
            return result

        # Fallback to 2Captcha cloud service
        result = await self.solve_via_2captcha(image_bytes)
        if result:
            self._set_cached_result(cache_key, result)
        return result


# ========================================================================
# P7: Legacy function-based API for compatibility
# ========================================================================

async def solve_captcha(image_bytes: bytes, api_key: Optional[str] = None) -> Optional[str]:
    """
    Standalone CAPTCHA solver function.

    Args:
        image_bytes: CAPTCHA image data
        api_key: Optional 2Captcha API key

    Returns:
        Solved text or None
    """
    solver = VisionCaptchaSolver()
    if api_key:
        solver._2captcha_api_key = api_key
    return await solver.solve(image_bytes)
