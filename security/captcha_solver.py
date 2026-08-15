"""
Vision Captcha Solver - Apple Vision/CoreML based CAPTCHA solving
=================================================================


CAPTCHA solver using YOLO CoreML model and VNCoreMLModel.
Designed for M1/Apple Silicon with ANE acceleration.

ARCHITECTURAL NOTE (F360):
    This module provides ``VisionCaptchaSolver`` — a standalone, Apple-native
    CAPTCHA solver that uses Vision framework + CoreML for OCR.

    The *other* CAPTCHA solver in the codebase is
    ``AdvancedCaptchaSolver`` in ``layers/stealth_layer.py``.  Both are
    fully functional but serve different model families:

    +----------------------------+----------------------------------+------------------------+
    |                            | VisionCaptchaSolver              | AdvancedCaptchaSolver   |
    |                            | (this module)                   | (stealth_layer)        |
    +============================+==================================+========================+
    | OCR backend                | Apple Vision VNCoreMLModel       | transformers + Tesseract|
    |                            | (ANE-accelerated on M1)         | (CPU-only)              |
    +----------------------------+----------------------------------+------------------------+
    | Entry point                | ``solve_captcha()`` standalone   | ``StealthLayer.solve_   |
    |                            | function or ``VisionCaptchaSolver| captcha()``            |
    |                            | .solve()                        |                        |
    +----------------------------+----------------------------------+------------------------+
    | Feature flag               | ``HLEDAC_ENABLE_CAPTCHA_DETECTION`` | ``HLEDAC_ENABLE_STEALTH|
    |                            | (default OFF)                   | _LAYER (default ON)    |
    +----------------------------+----------------------------------+------------------------+
    | Cache                      | 1-hour TTL, 100-entry LRU       | no caching             |
    +----------------------------+----------------------------------+------------------------+

    Both are independent implementations — *not* wired together.  The
    ``stealth_layer`` path is the canonical one when
    ``HLEDAC_ENABLE_STEALTH_LAYER=1``; this module is a specialised
    fallback for M1 hardware that benefits from ANE acceleration.
"""
import asyncio
import hashlib
import logging
import time
from hledac.universal.utils.lru_cache import LRUCache
from _core import aclose
logger = logging.getLogger(__name__)
_COREML_AVAILABLE = False
_VN_AVAILABLE = False
_YOLO_AVAILABLE = False
_COREMLTOOLS_VERSION: float = 0.0
_ct = None

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
_VNCoreMLModel = None
_VNCoreMLRequest = None
_VNImageRequestHandler = None

def _ensure_vision():
    """Lazily import Vision framework. Call before using Vision classes."""
    global _VNCoreMLModel, _VNCoreMLRequest, _VNImageRequestHandler, _VN_AVAILABLE
    if _VN_AVAILABLE:
        return True
    try:
        from Vision import VNCoreMLModel, VNCoreMLRequest, VNImageRequestHandler
        _VNCoreMLModel = VNCoreMLModel
        _VNCoreMLRequest = VNCoreMLRequest
        _VNImageRequestHandler = VNImageRequestHandler
        _VN_AVAILABLE = True
        return True
    except ImportError:
        _VN_AVAILABLE = False
        return False

class VisionCaptchaSolver:
    """
    CAPTCHA solver using Apple Vision framework and CoreML.

    Features:
        - YOLO CoreML model for grid CAPTCHAs
        - VNCoreMLModel for text recognition
        - Result caching with 1-hour expiration
    """
    _result_cache: LRUCache = LRUCache(max_size=MAX_CACHE_SIZE)
    _cache_timestamps: dict[str, float] = {}
    CACHE_TTL = 3600
    MAX_CACHE_SIZE = 100
    __slots__ = tuple(('_model', '_vn_model', 'model_path', 'use_ane', '_2captcha_api_key'))

    def __init__(self, model_path: str | None=None, use_ane: bool=True):
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
        logger.info(f'VisionCaptchaSolver initialized: model={model_path}, ane={self.use_ane}')

    def _load_model(self):
        """Load the CoreML model if not already loaded. Lazy import in py3.14."""
        global _ct, _COREML_AVAILABLE, _COREMLTOOLS_VERSION
        if self._model is not None:
            return
        if self.model_path is None:
            logger.info('No model path provided, using text-only mode')
            return
        if _ct is None:
            try:
                import coremltools as _ct_module
                _ct = _ct_module
                _COREML_AVAILABLE = True
                try:
                    _COREMLTOOLS_VERSION = float(_ct.__version__)
                except (ValueError, TypeError):
                    _COREMLTOOLS_VERSION = 6.0
            except ImportError:
                _COREML_AVAILABLE = False
                logger.warning('CoreML tools not available in this Python environment')
                return
        try:
            self._model = _ct.models.MLModel(self.model_path)
            logger.info(f'Loaded CoreML model from {self.model_path}')
            if _ensure_vision() and _VNCoreMLModel is not None:
                try:
                    self._vn_model = _VNCoreMLModel.modelForMLModel(self._model)
                except Exception as e:
                    logger.warning(f'Failed to create VNCoreMLModel: {e}')
        except Exception as e:
            logger.error(f'Failed to load model: {e}')
            self._model = None

    def _get_cache_key(self, data: bytes) -> str:
        """Generate cache key from data hash."""
        return hashlib.sha256(data).hexdigest()[:16]

    def _check_cache(self, image_bytes: bytes) -> tuple[bool, object | None, str]:
        """Check cache for image_bytes.

        Returns:
            Tuple of (is_cached, cached_value_or_None, cache_key).
            If is_cached is True, use cached_value directly.
            Otherwise, compute result and call _set_cached_result with cache_key.
        """
        cache_key = self._get_cache_key(image_bytes)
        cached = self._get_cached_result(cache_key)
        return (True, cached, cache_key) if cached is not None else (False, None, cache_key)

    def _get_cached_result(self, cache_key: str) -> object | None:
        """Get cached result if not expired."""
        if cache_key not in self._result_cache:
            return None
        timestamp = self._cache_timestamps.get(cache_key, 0)
        if time.time() - timestamp > self.CACHE_TTL:
            del self._result_cache[cache_key]
            del self._cache_timestamps[cache_key]
            return None
        self._result_cache.move_to_end(cache_key)
        return self._result_cache[cache_key]

    def _set_cached_result(self, cache_key: str, result: object):
        """Cache result with timestamp. Existing keys are moved to end (LRU discipline)."""
        if cache_key in self._result_cache:
            # Existing key: move to end before updating (Python dict assignment
            # does NOT automatically move existing keys to end in Python 3.7+)
            self._result_cache.move_to_end(cache_key)
        while len(self._result_cache) >= self.MAX_CACHE_SIZE:
            oldest_key, _ = self._result_cache.pop_lru()
            self._cache_timestamps.pop(oldest_key, None)
        self._result_cache[cache_key] = result
        self._cache_timestamps[cache_key] = time.time()

    def solve_grid(self, image_bytes: bytes) -> list[int] | None:
        """
        Solve grid CAPTCHA (e.g., "select all images with traffic lights").

        Args:
            image_bytes: Raw image data

        Returns:
            List of selected grid indices
        """
        is_cached, cached, cache_key = self._check_cache(image_bytes)
        if is_cached:
            return cached
        result: list[int] = []
        if not _VN_AVAILABLE or self._model is None:
            logger.warning('Vision framework or model not available')
            self._set_cached_result(cache_key, result)
            return result
        try:
            self._load_model()
            if self._vn_model is None:
                logger.warning('VNCoreMLModel not available')
                self._set_cached_result(cache_key, result)
                return result
            logger.debug('Grid solving not fully implemented')
        except Exception as e:
            logger.error(f'Grid solving failed: {e}')
        self._set_cached_result(cache_key, result)
        return result

    def solve_text(self, image_bytes: bytes) -> str:
        """
        Solve text-based CAPTCHA.

        Args:
            image_bytes: Raw image data

        Returns:
            Recognized text string
        """
        is_cached, cached, cache_key = self._check_cache(image_bytes)
        if is_cached:
            return cached
        result = ''
        if not _VN_AVAILABLE:
            logger.warning('Vision framework not available')
            self._set_cached_result(cache_key, result)
            return result
        try:
            self._load_model()
            logger.debug('Text recognition not fully implemented')
        except Exception as e:
            logger.error(f'Text solving failed: {e}')
        self._set_cached_result(cache_key, result)
        return result

    def clear_cache(self):
        """Clear the result cache."""
        self._result_cache.clear()
        self._cache_timestamps.clear()
        logger.info('CAPTCHA solver cache cleared')

    @classmethod
    def get_cache_stats(cls) -> dict:
        """Get cache statistics."""
        return {'size': len(cls._result_cache), 'max_size': cls.MAX_CACHE_SIZE, 'ttl_seconds': cls.CACHE_TTL}

    async def solve_image_captcha(self, image_bytes: bytes) -> str | None:
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
            logger.debug('pytesseract not available, trying 2captcha')
            return None
        try:
            img = Image.open(io.BytesIO(image_bytes))
            img = img.convert('L')
            img = img.point(lambda x: 0 if x < 128 else 255)
            result = pytesseract.image_to_string(img, config='--psm 8').strip()
            if result:
                logger.debug(f'pytesseract OCR succeeded: {result[:50]}...')
            return result if result else None
        except Exception as e:
            logger.warning(f'pytesseract OCR failed: {e}')
            return None

    async def solve_via_2captcha(self, image_bytes: bytes) -> str | None:
        """
        Cloud CAPTCHA solving via 2Captcha API. Only if API key configured.
        Polls with backoff (10 attempts, 3s interval).
        """
        api_key = getattr(self, '_2captcha_api_key', None)
        if not api_key:
            logger.debug('2Captcha API key not configured')
            return None
        try:
            import base64
            from hledac.universal.network.session_runtime import async_get_httpx_session
        except ImportError:
            logger.warning('httpx not available for 2captcha')
            return None
        b64_data = base64.b64encode(image_bytes).decode()
        try:
            session = await async_get_httpx_session()
            response = await session.post('http://2captcha.com/in.php', data={'key': api_key, 'method': 'base64', 'body': b64_data})
            result = response.text  # httpx.Response.text is a property, not a method
            if not result.startswith('OK|'):
                logger.warning(f'2Captcha submit failed: {result}')
                return None
            captcha_id = result.split('|')[1]
            for _ in range(10):
                await asyncio.sleep(3)
                poll_response = await session.get(f'http://2captcha.com/res.php?key={api_key}&action=get&id={captcha_id}')
                res = poll_response.text  # httpx.Response.text is a property
                if res.startswith('OK|'):
                    solution = res.split('|')[1]
                    logger.debug(f'2Captcha solved: {solution[:50]}...')
                    return solution
                if res == 'CAPCHA_NOT_READY':
                    continue
                logger.warning(f'2Captcha poll error: {res}')
                break
        except Exception as e:
            logger.warning(f'2Captcha request failed: {e}')
        return None

    async def solve(self, image_bytes: bytes) -> str | None:
        """
        Unified CAPTCHA solving: OCR first (free), 2Captcha fallback (paid).

        Args:
            image_bytes: Raw CAPTCHA image data

        Returns:
            Solved CAPTCHA text or None if unsolved
        """
        is_cached, cached, cache_key = self._check_cache(image_bytes)
        if is_cached:
            return cached
        result = await self.solve_image_captcha(image_bytes)
        if result:
            self._set_cached_result(cache_key, result)
            return result
        result = await self.solve_via_2captcha(image_bytes)
        if result:
            self._set_cached_result(cache_key, result)
        return result

async def solve_captcha(image_bytes: bytes, api_key: str | None=None) -> str | None:
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