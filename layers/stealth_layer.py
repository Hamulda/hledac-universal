"""
Stealth Layer - Stealth Browsing, Detection Evasion, CAPTCHA Solving
====================================================================

Integrates:
- StealthBrowser: Playwright wrapper with anti-detection
- DetectionEvader: 10+ evasion scripts, behavior simulation
- CaptchaSolver: Multi-provider CAPTCHA solving
- BehaviorSimulator: Human-like behavior simulation with Bézier curves
- Chameleon: Process masquerading and anti-debugging (macOS M1)

This is a thin wrapper that imports existing stealth modules
and adds integration logic for the universal orchestrator.

Architecture (Issue 6.3):
- 5 concerns decomposed into StealthStrategy Protocol → layers/stealth_strategies.py
- CaptchaSolvingStrategy: 2captcha API (primary) + Vision/CoreML (secondary)
- Local OCR (torch/transformers) OFF BY DEFAULT — enable HLEDAC_ENABLE_CAPTCHA_LOCAL=1
- AdvancedCaptchaSolver: lazy-loaded only when HLEDAC_ENABLE_CAPTCHA_LOCAL=1

CAPTCHA SOLVER OVERLAP NOTE (F360):
    This module's ``AdvancedCaptchaSolver`` (lines 81-467) is a *parallel
    implementation* to ``VisionCaptchaSolver`` in ``security/captcha_solver.py``.
    See that module's docstring for a full comparison table.  The
    ``stealth_layer`` path is canonical when ``HLEDAC_ENABLE_STEALTH_LAYER=1``.
"""
import asyncio
import logging
import math
import secrets
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import msgspec
import orjson
from hledac.universal.project_types import BrowserType, CaptchaSolution, CaptchaType, RiskLevel, StealthConfig, StealthSession
logger = logging.getLogger(__name__)

def _gauss(mu: float, sigma: float) -> float:
    """Box-Muller transform for Gaussian random numbers using crypto-safe RNG."""
    u1 = _RNG.random()
    u2 = _RNG.random()
    z0 = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    return mu + sigma * z0

class CaptchaSolverConfig(msgspec.Struct):
    """Configuration for self-hosted CAPTCHA solving"""
    ocr_model: str = 'microsoft/trocr-small-printed'
    use_mlx: bool = True
    max_image_size: int = 640
    enable_image_ocr: bool = True
    enable_text_logic: bool = True
    enable_rotation_detection: bool = True
    timeout_seconds: float = 30.0
    confidence_threshold: float = 0.6

class CaptchaResult(msgspec.Struct):
    """Result of CAPTCHA solving attempt"""
    success: bool
    solution: str | None
    confidence: float
    processing_time_ms: float
    method: str
    alternative_solutions: list[str] = field(default_factory=list)

# Crypto-safe RNG — F350M-R
_RNG = secrets.SystemRandom()

class AdvancedCaptchaSolver:
    """
    Self-hosted CAPTCHA solver optimized for M1 8GB.

    Solves common CAPTCHA types without external APIs:
    - Image-based text CAPTCHAs (OCR)
    - Simple logic puzzles (math, sequence)
    - Rotation-based challenges
    - Distorted text with noise

    M1 Optimized:
    - Uses lightweight models (<100MB)
    - MLX acceleration when available
    - Streaming image processing
    - Aggressive memory cleanup

    Example:
        >>> solver = AdvancedCaptchaSolver(config)
        >>> await solver.initialize()
        >>> result = await solver.solve_image_captcha(image_bytes)
        >>> print(f"Solution: {result.solution} (confidence: {result.confidence})")
    """
    MATH_PATTERNS = [('(\\d+)\\s*\\+\\s*(\\d+)', lambda a, b: int(a) + int(b)), ('(\\d+)\\s*-\\s*(\\d+)', lambda a, b: int(a) - int(b)), ('(\\d+)\\s*\\*\\s*(\\d+)', lambda a, b: int(a) * int(b)), ('(\\d+)\\s*×\\s*(\\d+)', lambda a, b: int(a) * int(b)), ('(\\d+)\\s*plus\\s*(\\d+)', lambda a, b: int(a) + int(b)), ('(\\d+)\\s*minus\\s*(\\d+)', lambda a, b: int(a) - int(b))]
    SEQUENCE_PATTERNS = ['(\\d+),\\s*(\\d+),\\s*(\\d+),\\s*\\?', '(\\d+)\\s+(\\d+)\\s+(\\d+)\\s+_']
    OCR_PREPROCESSING = ['grayscale', 'denoise', 'contrast', 'threshold', 'deskew']
    __slots__ = tuple(('_initialized', '_ocr_pipeline', '_solve_stats', 'config'))

    def __init__(self, config: CaptchaSolverConfig | None=None):
        self.config = config or CaptchaSolverConfig()
        self._ocr_pipeline: Any | None = None
        self._initialized = False
        self._solve_stats = {'attempted': 0, 'solved': 0, 'by_method': {}}

    async def initialize(self) -> bool:
        """Initialize CAPTCHA solver with lightweight models."""
        try:
            logger.info('🚀 Initializing AdvancedCaptchaSolver...')
            if self.config.enable_image_ocr:
                await self._init_ocr_pipeline()
            self._initialized = True
            logger.info('✅ AdvancedCaptchaSolver initialized')
            return True
        except Exception as e:
            logger.error(f'❌ CaptchaSolver initialization failed: {e}')
            return False

    async def _init_ocr_pipeline(self) -> None:
        """Initialize OCR pipeline with fallback options."""
        await asyncio.to_thread(self._load_model_sync)

    def _load_model_sync(self) -> None:
        """Synchronous model loading (runs in thread to avoid blocking)."""
        try:
            import importlib.util
            if importlib.util.find_spec('transformers') is None:
                raise ImportError('transformers not installed')
            if importlib.util.find_spec('torch') is None:
                raise ImportError('torch not installed (required by transformers)')
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
            self._ocr_pipeline = {'type': 'transformers', 'processor': TrOCRProcessor.from_pretrained(self.config.ocr_model), 'model': VisionEncoderDecoderModel.from_pretrained(self.config.ocr_model)}
            logger.info(f'✅ Loaded OCR model: {self.config.ocr_model}')
        except Exception as e:
            logger.warning(f'⚠️ Transformers OCR not available: {e}')
            try:
                import pytesseract
                self._ocr_pipeline = {'type': 'tesseract', 'engine': pytesseract}
                logger.info('✅ Using Tesseract OCR fallback')
            except ImportError:
                logger.warning('⚠️ No OCR backend available')
                self._ocr_pipeline = None

    async def solve_captcha(self, captcha_type: CaptchaType, image_data: bytes | None=None, text_challenge: str | None=None, **kwargs) -> CaptchaResult:
        """
        Solve CAPTCHA based on type.

        Args:
            captcha_type: Type of CAPTCHA
            image_data: Image bytes for image CAPTCHAs
            text_challenge: Text for logic/text CAPTCHAs
            **kwargs: Additional parameters

        Returns:
            CaptchaResult with solution
        """
        import time
        start_time = time.time()
        self._solve_stats['attempted'] += 1
        try:
            if captcha_type == CaptchaType.IMAGE and image_data:
                result = await self._solve_image_captcha(image_data)
            elif captcha_type == CaptchaType.TEXT and text_challenge:
                result = await self._solve_text_logic(text_challenge)
            elif captcha_type == CaptchaType.MATH and text_challenge:
                result = await self._solve_math_captcha(text_challenge)
            else:
                result = CaptchaResult(success=False, solution=None, confidence=0.0, processing_time_ms=0.0, method='unsupported')
            if result.success:
                self._solve_stats['solved'] += 1
                method = result.method
                self._solve_stats['by_method'][method] = self._solve_stats['by_method'].get(method, 0) + 1
            result.processing_time_ms = (time.time() - start_time) * 1000
            return result
        except Exception as e:
            logger.error(f'❌ CAPTCHA solving error: {e}')
            return CaptchaResult(success=False, solution=None, confidence=0.0, processing_time_ms=(time.time() - start_time) * 1000, method='error')

    async def _solve_image_captcha(self, image_data: bytes) -> CaptchaResult:
        """Solve image-based text CAPTCHA using OCR."""
        import io
        from PIL import Image
        try:
            image = Image.open(io.BytesIO(image_data))
            max_size = self.config.max_image_size
            if image.width > max_size or image.height > max_size:
                ratio = min(max_size / image.width, max_size / image.height)
                new_size = (int(image.width * ratio), int(image.height * ratio))
                image = image.resize(new_size, Image.Resampling.LANCZOS)
            processed = self._preprocess_for_ocr(image)
            if self._ocr_pipeline and self._ocr_pipeline.get('type') == 'transformers':
                text, confidence = await self._run_transformers_ocr(processed)
            elif self._ocr_pipeline and self._ocr_pipeline.get('type') == 'tesseract':
                text, confidence = await self._run_tesseract_ocr(processed)
            else:
                return CaptchaResult(success=False, solution=None, confidence=0.0, processing_time_ms=0.0, method='no_ocr_backend')
            text = text.strip().upper()
            text = ''.join((c for c in text if c.isalnum()))
            success = len(text) >= 4 and confidence >= self.config.confidence_threshold
            return CaptchaResult(success=success, solution=text if success else None, confidence=confidence, processing_time_ms=0.0, method='ocr')
        except Exception as e:
            logger.error(f'❌ Image CAPTCHA error: {e}')
            return CaptchaResult(success=False, solution=None, confidence=0.0, processing_time_ms=0.0, method='error')

    def _preprocess_for_ocr(self, image: Image.Image) -> Image.Image:
        """Preprocess image for better OCR accuracy."""
        try:
            from PIL import ImageEnhance, ImageFilter
        except ImportError:
            logger.warning('PIL not available for preprocessing')
            return image
        try:
            if image.mode != 'L':
                image = image.convert('L')
            image = image.filter(ImageFilter.MedianFilter(size=3))
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(2.0)
            return image
        except Exception as e:
            logger.warning(f'Image preprocessing failed: {e}')
            return image

    async def _run_transformers_ocr(self, image: Image.Image) -> tuple[str, float]:
        """Run OCR using Transformers model (offloaded to thread)."""
        return await asyncio.to_thread(self._run_transformers_ocr_sync, image)

    def _run_transformers_ocr_sync(self, image: Image.Image) -> tuple[str, float]:
        """Synchronous OCR using Transformers model."""
        try:
            import torch
        except ImportError:
            logger.warning('torch not available for transformers OCR')
            return ('', 0.0)
        if self._ocr_pipeline is None:
            logger.warning('OCR pipeline not initialized')
            return ('', 0.0)
        try:
            model = self._ocr_pipeline['model']
            processor = self._ocr_pipeline['processor']
            pixel_values = processor(image, return_tensors='pt').pixel_values
            with torch.no_grad():
                generated_ids = model.generate(pixel_values)
            generated_text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
            confidence = 0.75
            return (generated_text, confidence)
        except Exception as e:
            logger.warning(f'Transformers OCR failed: {e}')
            return ('', 0.0)

    async def _run_tesseract_ocr(self, image: Image.Image) -> tuple[str, float]:
        """Run OCR using Tesseract (offloaded to thread)."""
        return await asyncio.to_thread(self._run_tesseract_ocr_sync, image)

    def _run_tesseract_ocr_sync(self, image: Image.Image) -> tuple[str, float]:
        """Synchronous OCR using Tesseract."""
        try:
            if self._ocr_pipeline is None:
                raise KeyError('No OCR pipeline')
            pytesseract = self._ocr_pipeline['engine']
        except (KeyError, AttributeError, TypeError):
            logger.warning('pytesseract not available')
            return ('', 0.0)
        try:
            text = pytesseract.image_to_string(image)
            try:
                data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
                confidences = [int(c) for c in data['conf'] if int(c) > 0]
                avg_confidence = sum(confidences) / len(confidences) / 100.0 if confidences else 0.5
            except Exception:
                avg_confidence = 0.5
            return (text, avg_confidence)
        except Exception as e:
            logger.warning(f'Tesseract OCR failed: {e}')
            return ('', 0.0)

    async def _solve_text_logic(self, challenge: str) -> CaptchaResult:
        """Solve text-based logic puzzles."""
        for pattern, solver in self.MATH_PATTERNS:
            match = re.search(pattern, challenge, re.IGNORECASE)
            if match:
                try:
                    result = solver(match.group(1), match.group(2))
                    return CaptchaResult(success=True, solution=str(result), confidence=0.95, processing_time_ms=0.0, method='math_logic')
                except Exception:
                    continue
        for pattern in self.SEQUENCE_PATTERNS:
            match = re.search(pattern, challenge)
            if match:
                try:
                    nums = [int(match.group(i)) for i in range(1, 4)]
                    diff = nums[1] - nums[0]
                    if nums[2] - nums[1] == diff:
                        result = nums[2] + diff
                        return CaptchaResult(success=True, solution=str(result), confidence=0.9, processing_time_ms=0.0, method='sequence_logic')
                except Exception:
                    continue
        return CaptchaResult(success=False, solution=None, confidence=0.0, processing_time_ms=0.0, method='logic_failed')

    async def _solve_math_captcha(self, challenge: str) -> CaptchaResult:
        """Solve math-based CAPTCHA."""
        return await self._solve_text_logic(challenge)

    def get_statistics(self) -> dict[str, Any]:
        """Get solving statistics."""
        attempted = self._solve_stats['attempted']
        solved = self._solve_stats['solved']
        return {'attempted': attempted, 'solved': solved, 'success_rate': solved / attempted if attempted > 0 else 0.0, 'by_method': self._solve_stats['by_method'].copy(), 'ocr_backend': self._ocr_pipeline.get('type') if self._ocr_pipeline else None}

class JavaScriptEvasionConfig(msgspec.Struct):
    """Configuration for JavaScript evasion"""
    hide_webdriver: bool = True
    hide_automation: bool = True
    spoof_plugins: bool = True
    spoof_permissions: bool = True
    disable_webrtc: bool = True
    override_canvas: bool = True
    override_webgl: bool = True
    spoof_fonts: bool = True
    emulate_human_events: bool = True
    patch_detection_libs: bool = True
    randomize_globals: bool = True
    spoof_chrome_runtime: bool = True
    add_chrome_plugins: bool = True

class JavaScriptEvasion:
    """
    Advanced JavaScript evasion techniques for bot detection bypass.

    Provides 15+ evasion scripts to defeat:
    - Webdriver detection
    - Automation flags
    - Headless detection
    - Plugin enumeration
    - Canvas fingerprinting
    - WebGL fingerprinting
    - Permission API probing
    - Chrome runtime detection

    M1 Optimized:
    - Scripts injected before page load
    - Minimal runtime overhead
    - Memory-efficient execution

    Example:
        >>> evasion = JavaScriptEvasion(config)
        >>> scripts = evasion.get_all_evasion_scripts()
        >>> for script in scripts:
        ...     await page.add_init_script(script)
    """
    DETECTION_LIBS = ['botd', 'botguard', 'datadome', 'akamai', 'perimeterx', 'cloudflare', 'hcaptcha', 'recaptcha']
    __slots__ = tuple(('_script_cache', 'config'))

    def __init__(self, config: JavaScriptEvasionConfig | None=None):
        self.config = config or JavaScriptEvasionConfig()
        self._script_cache: dict[str, str] = {}

    def get_all_evasion_scripts(self) -> list[str]:
        """Get all enabled evasion scripts."""
        scripts = []
        if self.config.hide_webdriver:
            scripts.append(self._get_webdriver_hider())
        if self.config.hide_automation:
            scripts.append(self._get_automation_hider())
        if self.config.spoof_plugins:
            scripts.append(self._get_plugin_spoof())
        if self.config.spoof_permissions:
            scripts.append(self._get_permission_spoof())
        if self.config.disable_webrtc:
            scripts.append(self._get_webrtc_disabler())
        if self.config.override_canvas:
            scripts.append(self._get_canvas_override())
        if self.config.override_webgl:
            scripts.append(self._get_webgl_override())
        if self.config.spoof_fonts:
            scripts.append(self._get_font_spoof())
        if self.config.emulate_human_events:
            scripts.append(self._get_event_emulator())
        if self.config.patch_detection_libs:
            scripts.append(self._get_detection_patcher())
        if self.config.randomize_globals:
            scripts.append(self._get_global_randomizer())
        if self.config.spoof_chrome_runtime:
            scripts.append(self._get_chrome_runtime_spoof())
        if self.config.add_chrome_plugins:
            scripts.append(self._get_chrome_plugins())
        return scripts

    def _get_webdriver_hider(self) -> str:
        """Hide webdriver properties."""
        return "\n        // Hide WebDriver\n        Object.defineProperty(navigator, 'webdriver', {\n            get: () => undefined\n        });\n\n        // Remove webdriver-related properties\n        delete navigator.__webdriver_script_fn;\n        delete navigator.__selenium_evaluate;\n        delete navigator.__selenium_unwrapped;\n\n        // Chrome-only properties\n        if (window.chrome) {\n            window.chrome.runtime = window.chrome.runtime || {};\n            window.chrome.csi = window.chrome.csi || function() {};\n            window.chrome.loadTimes = window.chrome.loadTimes || function() {};\n        }\n        "

    def _get_automation_hider(self) -> str:
        """Hide automation flags."""
        return '\n        // Hide automation flags\n        const originalQuery = window.navigator.permissions.query;\n        window.navigator.permissions.query = (parameters) => (\n            parameters.name === \'notifications\' ||\n            parameters.name === \'clipboard-read\' ||\n            parameters.name === \'clipboard-write\'\n            ? Promise.resolve({ state: Notification.permission })\n            : originalQuery(parameters)\n        );\n\n        // Override Permissions API\n        if (navigator.permissions) {\n            const originalPermissionsQuery = navigator.permissions.query;\n            navigator.permissions.query = function(parameters) {\n                if (parameters.name === \'notifications\') {\n                    return Promise.resolve({\n                        state: \'default\',\n                        onchange: null,\n                        addEventListener: function() {},\n                        removeEventListener: function() {},\n                        dispatchEvent: function() { return true; }\n                    });\n                }\n                return originalPermissionsQuery.call(this, parameters);\n            };\n        }\n\n        // Hide Playwright/Puppeteer indicators\n        Object.defineProperty(navigator, \'plugins\', {\n            get: function() {\n                return [\n                    {\n                        0: {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"},  # noqa: E501\n                        description: "Portable Document Format",\n                        filename: "internal-pdf-viewer",\n                        length: 1,\n                        name: "Chrome PDF Plugin"\n                    },\n                    {\n                        0: {type: "application/pdf", suffixes: "pdf", description: "Portable Document Format"},\n                        description: "Portable Document Format",\n                        filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai",\n                        length: 1,\n                        name: "Chrome PDF Viewer"\n                    }\n                ];\n            }\n        });\n\n        // Hide headless indicators\n        Object.defineProperty(navigator, \'languages\', {\n            get: () => [\'en-US\', \'en\']\n        });\n        '

    def _get_plugin_spoof(self) -> str:
        """Spoof plugin information."""
        return '\n        // Spoof plugins to appear as regular Chrome\n        Object.defineProperty(navigator, \'plugins\', {\n            get: function() {\n                return {\n                    length: 2,\n                    item: function(index) {\n                        const plugins = [\n                            {\n                                name: "Chrome PDF Plugin",\n                                filename: "internal-pdf-viewer",\n                                description: "Portable Document Format",\n                                version: undefined,\n                                length: 1,\n                                item: function(idx) { return this[idx]; }\n                            },\n                            {\n                                name: "Chrome PDF Viewer",\n                                filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai",\n                                description: "Portable Document Format",\n                                version: undefined,\n                                length: 1,\n                                item: function(idx) { return this[idx]; }\n                            }\n                        ];\n                        return plugins[index];\n                    },\n                    namedItem: function(name) {\n                        return this.item(0);\n                    },\n                    refresh: function() {}\n                };\n            }\n        });\n\n        // Spoof mimeTypes\n        Object.defineProperty(navigator, \'mimeTypes\', {\n            get: function() {\n                return {\n                    length: 2,\n                    item: function(index) {\n                        const types = [\n                            { type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format", enabledPlugin: navigator.plugins[0] },  # noqa: E501\n                            { type: "application/pdf", suffixes: "pdf", description: "Portable Document Format", enabledPlugin: navigator.plugins[1] }  # noqa: E501\n                        ];\n                        return types[index];\n                    }\n                };\n            }\n        });\n        '

    def _get_permission_spoof(self) -> str:
        """Spoof permission API."""
        return "\n        // Override Permissions API to appear as standard browser\n        if (navigator.permissions) {\n            const originalQuery = navigator.permissions.query;\n            navigator.permissions.query = function(parameters) {\n                // Standard permissions responses\n                const permissionOverrides = {\n                    'notifications': 'default',\n                    'camera': 'prompt',\n                    'microphone': 'prompt',\n                    'clipboard-read': 'prompt',\n                    'clipboard-write': 'granted',\n                    'geolocation': 'prompt'\n                };\n\n                if (parameters.name in permissionOverrides) {\n                    return Promise.resolve({\n                        state: permissionOverrides[parameters.name],\n                        onchange: null,\n                        addEventListener: function() {},\n                        removeEventListener: function() {},\n                        dispatchEvent: function() { return true; }\n                    });\n                }\n\n                return originalQuery.call(this, parameters);\n            };\n        }\n        "

    def _get_webrtc_disabler(self) -> str:
        """Disable WebRTC to prevent IP leaks."""
        return '\n        // Disable WebRTC\n        if (window.RTCPeerConnection) {\n            const noop = function() {};\n            window.RTCPeerConnection = noop;\n            window.RTCPeerConnection.prototype = {};\n        }\n\n        if (window.webkitRTCPeerConnection) {\n            const noop = function() {};\n            window.webkitRTCPeerConnection = noop;\n        }\n\n        if (window.mozRTCPeerConnection) {\n            const noop = function() {};\n            window.mozRTCPeerConnection = noop;\n        }\n        '

    def _get_canvas_override(self) -> str:
        """Override canvas fingerprinting."""
        return "\n        // Canvas fingerprint protection\n        const getImageData = CanvasRenderingContext2D.prototype.getImageData;\n        const toDataURL = HTMLCanvasElement.prototype.toDataURL;\n        const toBlob = HTMLCanvasElement.prototype.toBlob;\n\n        // Add subtle noise to canvas operations\n        CanvasRenderingContext2D.prototype.getImageData = function(sx, sy, sw, sh) {\n            const imageData = getImageData.call(this, sx, sy, sw, sh);\n            const data = imageData.data;\n\n            // Add imperceptible noise\n            for (let i = 0; i < data.length; i += 4) {\n                data[i] = (data[i] + (Math.random() > 0.5 ? 1 : 0)) % 256;\n                data[i + 1] = (data[i + 1] + (Math.random() > 0.5 ? 1 : 0)) % 256;\n                data[i + 2] = (data[i + 2] + (Math.random() > 0.5 ? 1 : 0)) % 256;\n            }\n\n            return imageData;\n        };\n\n        // Override toDataURL with noise\n        HTMLCanvasElement.prototype.toDataURL = function(type, quality) {\n            const ctx = this.getContext('2d');\n            if (ctx) {\n                const imageData = ctx.getImageData(0, 0, this.width, this.height);\n                const data = imageData.data;\n                for (let i = 0; i < data.length; i += 4) {\n                    data[i] = (data[i] + (Math.random() > 0.5 ? 1 : 0)) % 256;\n                }\n                ctx.putImageData(imageData, 0, 0);\n            }\n            return toDataURL.call(this, type, quality);\n        };\n        "

    def _get_webgl_override(self) -> str:
        """Override WebGL fingerprinting."""
        return "\n        // WebGL fingerprint protection\n        const getParameter = WebGLRenderingContext.prototype.getParameter;\n        const getExtension = WebGLRenderingContext.prototype.getExtension;\n\n        WebGLRenderingContext.prototype.getParameter = function(parameter) {\n            // Spoof common parameters\n            const spoofs = {\n                37445: 'Intel Inc.', // UNMASKED_VENDOR_WEBGL\n                37446: 'Intel Iris OpenGL Engine', // UNMASKED_RENDERER_WEBGL\n                7937: 'WebKit', // VERSION\n                7936: 'WebKit WebGL', // VENDOR\n                7938: 'WebGL 1.0 (OpenGL ES 2.0 Chromium)' // RENDERER\n            };\n\n            if (parameter in spoofs) {\n                return spoofs[parameter];\n            }\n\n            return getParameter.call(this, parameter);\n        };\n\n        // Randomize precision formats slightly\n        WebGLRenderingContext.prototype.getShaderPrecisionFormat = function() {\n            return {\n                precision: 23,\n                rangeMin: 127,\n                rangeMax: 127\n            };\n        };\n        "

    def _get_font_spoof(self) -> str:
        """Spoof font enumeration."""
        return "\n        // Font enumeration protection\n        const originalMeasureText = CanvasRenderingContext2D.prototype.measureText;\n        const commonFonts = [\n            'Arial', 'Courier New', 'Georgia', 'Times New Roman',\n            'Verdana', 'Helvetica', 'Trebuchet MS', 'Tahoma'\n        ];\n\n        CanvasRenderingContext2D.prototype.measureText = function(text) {\n            // Randomize measurements slightly\n            const result = originalMeasureText.call(this, text);\n            const originalWidth = result.width;\n\n            // Add tiny random variation\n            Object.defineProperty(result, 'width', {\n                get: () => originalWidth + (Math.random() * 0.02 - 0.01)\n            });\n\n            return result;\n        };\n\n        // Override font property to limit enumeration\n        const originalFont = Object.getOwnPropertyDescriptor(\n            CanvasRenderingContext2D.prototype, 'font'\n        );\n        "

    def _get_event_emulator(self) -> str:
        """Emulate human-like events."""
        return "\n        // Emulate human input events\n        (function() {\n            // Add realistic mousemove events\n            let lastMouseMove = Date.now();\n\n            document.addEventListener('mousemove', function(e) {\n                lastMouseMove = Date.now();\n            }, true);\n\n            // Override Date constructor for consistent timezone\n            const OriginalDate = Date;\n            Date = function(...args) {\n                if (args.length === 0) {\n                    return new OriginalDate(OriginalDate.now());\n                }\n                return new OriginalDate(...args);\n            };\n\n            Date.prototype = OriginalDate.prototype;\n            Date.now = OriginalDate.now;\n            Date.parse = OriginalDate.parse;\n            Date.UTC = OriginalDate.UTC;\n\n            // Ensure Date prototype is correct\n            Date.prototype.constructor = Date;\n\n            // Override performance timing\n            if (window.performance) {\n                const originalNow = performance.now;\n                performance.now = function() {\n                    return originalNow.call(performance);\n                };\n            }\n        })();\n        "

    def _get_detection_patcher(self) -> str:
        """Patch common detection libraries."""
        return "\n        // Patch common detection libraries\n        (function() {\n            // Hook into bot detection libraries\n            const libs = ['botd', 'botguard', 'datadome', 'akamai', 'perimeterx', 'cloudflare'];\n\n            libs.forEach(lib => {\n                Object.defineProperty(window, lib, {\n                    get: () => undefined,\n                    set: () => true\n                });\n            });\n\n            // Override common detection methods\n            const methodsToOverride = [\n                'toString',\n                'toSource',\n                'constructor'\n            ];\n\n            // Ensure native code appearance\n            Function.prototype.toString = function() {\n                if (this === Function.prototype.toString) {\n                    return 'function toString() { [native code] }';\n                }\n                return 'function () { [native code] }';\n            };\n\n            // Override prototype chain inspection\n            if (window.HTMLElement) {\n                const originalHTMLElement = window.HTMLElement;\n                window.HTMLElement = function() {};\n                window.HTMLElement.prototype = originalHTMLElement.prototype;\n            }\n        })();\n        "

    def _get_global_randomizer(self) -> str:
        """Randomize global properties."""
        return "\n        // Randomize global properties to prevent fingerprinting\n        (function() {\n            // Random screen offset (within reasonable bounds)\n            const screenOffset = Math.floor(Math.random() * 50);\n\n            Object.defineProperty(window.screen, 'availLeft', {\n                get: () => screenOffset\n            });\n\n            Object.defineProperty(window.screen, 'availTop', {\n                get: () => screenOffset\n            });\n\n            // Memory pressure simulation\n            if (navigator.deviceMemory) {\n                Object.defineProperty(navigator, 'deviceMemory', {\n                    get: () => 8\n                });\n            }\n\n            // Hardware concurrency\n            Object.defineProperty(navigator, 'hardwareConcurrency', {\n                get: () => 8\n            });\n        })();\n        "

    def _get_chrome_runtime_spoof(self) -> str:
        """Spoof Chrome runtime environment."""
        return '\n        // Chrome runtime spoofing\n        if (!window.chrome) {\n            window.chrome = {};\n        }\n\n        window.chrome.runtime = {\n            OnInstalledReason: {\n                CHROME_UPDATE: "chrome_update",\n                INSTALL: "install",\n                SHARED_MODULE_UPDATE: "shared_module_update",\n                UPDATE: "update"\n            },\n            OnRestartRequiredReason: {\n                APP_UPDATE: "app_update",\n                OS_UPDATE: "os_update",\n                PERIODIC: "periodic"\n            },\n            PlatformArch: {\n                ARM: "arm",\n                ARM64: "arm64",\n                MIPS: "mips",\n                MIPS64: "mips64",\n                X86_32: "x86-32",\n                X86_64: "x86-64"\n            },\n            PlatformNaclArch: {\n                ARM: "arm",\n                MIPS: "mips",\n                MIPS64: "mips64",\n                MIPS64EL: "mips64el",\n                MIPS_EL: "mipsel",\n                X86_32: "x86-32",\n                X86_64: "x86-64"\n            },\n            PlatformOs: {\n                ANDROID: "android",\n                CROS: "cros",\n                LINUX: "linux",\n                MAC: "mac",\n                OPENBSD: "openbsd",\n                WIN: "win"\n            },\n            RequestUpdateCheckStatus: {\n                NO_UPDATE: "no_update",\n                THROTTLED: "throttled",\n                UPDATE_AVAILABLE: "update_available"\n            },\n            id: undefined,\n            OnConnect: {},\n            OnConnectExternal: {},\n            OnInstalled: {},\n            OnRestartRequired: {},\n            OnStartup: {},\n            OnSuspend: {},\n            OnSuspendCanceled: {},\n            OnUpdateAvailable: {}\n        };\n\n        // Add chrome.loadTimes for older detection\n        window.chrome.loadTimes = function() {\n            return {\n                commitLoadTime: performance.timing.domContentLoadedEventStart / 1000,\n                connectionInfo: \'h2\',\n                finishDocumentLoadTime: performance.timing.domContentLoadedEventEnd / 1000,\n                finishLoadTime: performance.timing.loadEventEnd / 1000,\n                firstPaintAfterLoadTime: 0,\n                firstPaintTime: performance.timing.domContentLoadedEventStart / 1000,\n                navigationType: \'Other\',\n                npnNegotiatedProtocol: \'h2\',\n                requestTime: performance.timing.requestStart / 1000,\n                startLoadTime: performance.timing.navigationStart / 1000,\n                wasAlternateProtocolAvailable: false,\n                wasFetchedViaSpdy: true,\n                wasNpnNegotiated: true\n            };\n        };\n        '

    def _get_chrome_plugins(self) -> str:
        """Add Chrome-specific plugin indicators."""
        return '\n        // Chrome-specific plugin indicators\n        window.chrome.app = {\n            isInstalled: false,\n            InstallState: {\n                DISABLED: "disabled",\n                INSTALLED: "installed",\n                NOT_INSTALLED: "not_installed"\n            },\n            RunningState: {\n                CANNOT_RUN: "cannot_run",\n                READY_TO_RUN: "ready_to_run",\n                RUNNING: "running"\n            }\n        };\n\n        // Chrome csi (chrome system info)\n        window.chrome.csi = function() {\n            return {\n                onloadT: Date.now(),\n                pageT: performance.now(),\n                startE: performance.timing.navigationStart,\n                transcription: \'\'\n            };\n        };\n        '

    def get_detection_score(self) -> dict[str, Any]:
        """Get evasion coverage score."""
        evasions = {'webdriver_hiding': self.config.hide_webdriver, 'automation_hiding': self.config.hide_automation, 'plugin_spoofing': self.config.spoof_plugins, 'permission_spoofing': self.config.spoof_permissions, 'webrtc_disabled': self.config.disable_webrtc, 'canvas_override': self.config.override_canvas, 'webgl_override': self.config.override_webgl, 'font_spoofing': self.config.spoof_fonts, 'event_emulation': self.config.emulate_human_events, 'detection_patching': self.config.patch_detection_libs, 'global_randomization': self.config.randomize_globals, 'chrome_runtime': self.config.spoof_chrome_runtime, 'chrome_plugins': self.config.add_chrome_plugins}
        enabled = sum((1 for v in evasions.values() if v))
        total = len(evasions)
        return {'coverage': enabled / total, 'enabled_count': enabled, 'total_count': total, 'evasions': evasions}

class BehaviorPattern(Enum):
    """Pre-defined behavior patterns"""
    CASUAL = 'casual'
    RESEARCHER = 'researcher'
    QUICK = 'quick'
    CAREFUL = 'careful'

class SimulationConfig(msgspec.Struct):
    """Configuration for behavior simulation"""
    pattern: BehaviorPattern = BehaviorPattern.RESEARCHER
    min_delay: float = 0.5
    max_delay: float = 3.0
    mouse_speed: float = 1.0
    scroll_min: int = 100
    scroll_max: int = 800
    scroll_pause: float = 0.1
    randomness: float = 0.3
    viewport_variation: bool = True

class MouseMovement(msgspec.Struct):
    """Mouse movement point"""
    x: float
    y: float
    timestamp: float

class ScrollAction(msgspec.Struct):
    """Scroll action"""
    delta_y: int
    duration: float
    pause_after: float

class BehaviorSimulator:
    """
    Simulate human-like web browsing behavior.

    M1-Optimized: Minimal CPU usage, efficient randomization

    Example:
        >>> simulator = BehaviorSimulator()
        >>> await simulator.simulate_reading(duration=30)
        >>> await simulator.simulate_scroll(direction='down')
        >>> await simulator.simulate_click(x=100, y=200)
    """
    PATTERNS: dict[BehaviorPattern, dict[str, Any]] = {BehaviorPattern.CASUAL: {'min_delay': 1.0, 'max_delay': 5.0, 'mouse_speed': 0.7, 'scroll_min': 200, 'scroll_max': 1000, 'scroll_pause': 0.2, 'randomness': 0.4}, BehaviorPattern.RESEARCHER: {'min_delay': 0.8, 'max_delay': 2.5, 'mouse_speed': 1.0, 'scroll_min': 300, 'scroll_max': 800, 'scroll_pause': 0.15, 'randomness': 0.25}, BehaviorPattern.QUICK: {'min_delay': 0.3, 'max_delay': 1.2, 'mouse_speed': 1.3, 'scroll_min': 400, 'scroll_max': 1200, 'scroll_pause': 0.05, 'randomness': 0.35}, BehaviorPattern.CAREFUL: {'min_delay': 2.0, 'max_delay': 8.0, 'mouse_speed': 0.5, 'scroll_min': 100, 'scroll_max': 400, 'scroll_pause': 0.3, 'randomness': 0.2}}
    __slots__ = tuple(('action_count', 'config', 'last_action_time', 'mouse_position', 'scroll_position', 'viewport_height', 'viewport_width'))

    def __init__(self, config: SimulationConfig | None=None):
        self.config = config or SimulationConfig()
        self._apply_pattern()
        self.last_action_time: float = time.time()
        self.mouse_position: tuple[int, int] = (0, 0)
        self.scroll_position: int = 0
        self.action_count: int = 0
        self.viewport_width: int = 1920
        self.viewport_height: int = 1080

    def _apply_pattern(self):
        """Apply pattern preset to config"""
        if self.config.pattern in self.PATTERNS:
            preset = self.PATTERNS[self.config.pattern]
            for key, value in preset.items():
                setattr(self.config, key, value)

    def _random_delay(self, min_mult: float=0.8, max_mult: float=1.2) -> float:
        """Generate random delay with variation"""
        base = _RNG.uniform(self.config.min_delay, self.config.max_delay)
        variation = _RNG.uniform(min_mult, max_mult)
        return base * variation

    def _apply_randomness(self, value: float) -> float:
        """Apply randomness factor to value"""
        if self.config.randomness <= 0:
            return value
        variation = value * self.config.randomness
        return value + _RNG.uniform(-variation, variation)

    def _bezier_curve(self, p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], t: float) -> tuple[float, float]:
        """Calculate quadratic Bézier curve point (M1-optimized)"""
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        return (x, y)

    def generate_mouse_path(self, start: tuple[int, int], end: tuple[int, int], num_points: int=20) -> list[MouseMovement]:
        """
        Generate human-like mouse path using Bézier curve.

        M1-Optimized: Efficient numpy-like operations using pure Python
        for minimal memory footprint on constrained systems.

        Args:
            start: Starting position (x, y)
            end: Ending position (x, y)
            num_points: Number of points in path

        Returns:
            List of mouse movement points
        """
        mid_x = (start[0] + end[0]) / 2
        mid_y = (start[1] + end[1]) / 2
        offset_range = abs(end[0] - start[0]) + abs(end[1] - start[1])
        offset_range *= 0.2 * self.config.randomness
        control = (mid_x + _RNG.uniform(-offset_range, offset_range), mid_y + _RNG.uniform(-offset_range, offset_range))
        points = []
        now = time.time()
        for i in range(num_points):
            t = i / (num_points - 1)
            x, y = self._bezier_curve(start, control, end, t)
            jitter = self.config.randomness * 2
            x += _RNG.uniform(-jitter, jitter)
            y += _RNG.uniform(-jitter, jitter)
            speed_variation = _RNG.uniform(0.8, 1.2) / self.config.mouse_speed
            timestamp = now + i * 0.01 * speed_variation
            points.append(MouseMovement(x=x, y=y, timestamp=timestamp))
        return points

    async def simulate_mouse_move(self, target_x: int, target_y: int, callback: Any | None=None) -> None:
        """
        Simulate mouse movement to target position.

        Args:
            target_x: Target X coordinate
            target_y: Target Y coordinate
            callback: Optional callback function for each point
        """
        path = self.generate_mouse_path(self.mouse_position, (target_x, target_y))
        for point in path:
            self.mouse_position = (int(point.x), int(point.y))
            if callback:
                await callback(self.mouse_position)
            await asyncio.sleep(0.005)
        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_click(self, x: int | None=None, y: int | None=None, callback: Any | None=None) -> None:
        """
        Simulate mouse click.

        Args:
            x: Click X coordinate (default: current)
            y: Click Y coordinate (default: current)
            callback: Optional callback function
        """
        if x is not None and y is not None:
            await self.simulate_mouse_move(x, y, callback)
        await asyncio.sleep(self._random_delay(0.1, 0.3))
        if callback:
            await callback(('click', self.mouse_position))
        logger.debug(f'Simulated click at {self.mouse_position}')
        await asyncio.sleep(self._random_delay(0.2, 0.5))
        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_scroll(self, direction: str='down', amount: int | None=None, callback: Any | None=None) -> None:
        """
        Simulate scrolling.

        Args:
            direction: 'up' or 'down'
            amount: Scroll amount in pixels (default: random)
            callback: Optional callback function
        """
        if amount is None:
            amount = _RNG.randint(self.config.scroll_min, self.config.scroll_max)
        if direction == 'up':
            amount = -amount
        chunk_size = 100
        remaining = amount
        while abs(remaining) > 0:
            chunk = min(chunk_size, abs(remaining))
            if remaining < 0:
                chunk = -chunk
            if callback:
                await callback(('scroll', chunk))
            self.scroll_position += chunk
            remaining -= chunk
            await asyncio.sleep(self._apply_randomness(self.config.scroll_pause))
        logger.debug(f'Simulated scroll {amount}px (total: {self.scroll_position})')
        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_typing(self, text: str, callback: Any | None=None, wpm: int=60) -> None:
        """
        Simulate human-like typing.

        Args:
            text: Text to type
            callback: Optional callback function
            wpm: Words per minute (typing speed)
        """
        chars_per_minute = wpm * 5
        base_delay = 60 / chars_per_minute
        for char in text:
            delay = base_delay * _RNG.uniform(0.7, 1.3)
            if callback:
                await callback(('type', char))
            await asyncio.sleep(delay)
            if _RNG.random() < 0.05:
                await asyncio.sleep(_RNG.uniform(0.2, 0.5))
        logger.debug(f'Simulated typing {len(text)} characters')
        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_reading(self, duration: float=10.0, scroll_probability: float=0.3) -> None:
        """
        Simulate reading a page (idle time with occasional scrolls).

        Args:
            duration: Reading duration in seconds
            scroll_probability: Probability of scrolling during reading
        """
        start_time = time.time()
        while time.time() - start_time < duration:
            await asyncio.sleep(self._random_delay(0.5, 1.5))
            if _RNG.random() < scroll_probability:
                direction = 'down' if _RNG.random() > 0.3 else 'up'
                await self.simulate_scroll(direction)
        logger.debug(f'Simulated reading for {duration}s')

    async def simulate_page_visit(self, num_scrolls: int=3, read_time: float=15.0) -> dict[str, Any]:
        """
        Simulate complete page visit behavior.

        Args:
            num_scrolls: Number of scroll actions
            read_time: Time spent reading

        Returns:
            Statistics about the simulated visit
        """
        start_time = time.time()
        await asyncio.sleep(self._random_delay(0.5, 1.5))
        await self.simulate_reading(duration=read_time, scroll_probability=0.4)
        for _ in range(num_scrolls):
            if _RNG.random() > 0.3:
                direction = _RNG.choice(['up', 'down'])
                await self.simulate_scroll(direction)
                await asyncio.sleep(self._random_delay(1.0, 3.0))
        duration = time.time() - start_time
        return {'duration': duration, 'actions': self.action_count, 'scroll_position': self.scroll_position, 'pattern': self.config.pattern.value}

    def get_statistics(self) -> dict[str, Any]:
        """Get simulation statistics"""
        return {'action_count': self.action_count, 'mouse_position': self.mouse_position, 'scroll_position': self.scroll_position, 'last_action_time': self.last_action_time, 'pattern': self.config.pattern.value, 'config': {'min_delay': self.config.min_delay, 'max_delay': self.config.max_delay, 'randomness': self.config.randomness}}

class FingerprintConfig(msgspec.Struct):
    """Configuration for fingerprint randomization (from stealth_toolkit)"""
    randomize_canvas: bool = True
    randomize_webgl: bool = True
    randomize_fonts: bool = True
    randomize_screen: bool = True
    randomize_timezone: bool = True
    randomize_plugins: bool = True
    consistent_per_session: bool = True
    session_duration: float = 3600
    use_realistic_profiles: bool = True
    platform: str | None = None

class BrowserProfile(msgspec.Struct):
    """Browser fingerprint profile (from stealth_toolkit)"""
    screen_width: int = 1920
    screen_height: int = 1080
    screen_color_depth: int = 24
    screen_pixel_ratio: float = 1.0
    timezone: str = 'America/New_York'
    timezone_offset: int = -5
    canvas_noise: tuple[int, int, int] = (0, 0, 0)
    webgl_vendor: str = 'Apple Inc.'
    webgl_renderer: str = 'Apple M1'
    fonts: list[str] = field(default_factory=list)
    plugins: list[dict[str, str]] = field(default_factory=list)
    hardware_concurrency: int = 8
    device_memory: int = 8
    max_touch_points: int = 0

class FingerprintRandomizer:
    """
    Browser fingerprint randomization (from stealth_toolkit).

    Randomizes browser fingerprints to avoid tracking:
    - Canvas fingerprinting protection
    - WebGL fingerprint randomization
    - Font list variation
    - Screen resolution spoofing
    - Timezone rotation

    Example:
        >>> randomizer = FingerprintRandomizer()
        >>> profile = randomizer.get_profile()
        >>> js_protection = randomizer.get_js_protection_script()
    """
    SCREEN_RESOLUTIONS = [(1920, 1080), (2560, 1440), (1366, 768), (1440, 900), (1680, 1050), (1280, 720), (3840, 2160)]
    TIMEZONES = [('America/New_York', -5), ('America/Chicago', -6), ('America/Denver', -7), ('America/Los_Angeles', -8), ('Europe/London', 0), ('Europe/Paris', 1), ('Europe/Berlin', 1), ('Asia/Tokyo', 9), ('Asia/Shanghai', 8), ('Australia/Sydney', 10)]
    WEBGL_PROFILES = {'macos': [('Apple Inc.', 'Apple M1'), ('Apple Inc.', 'Apple M1 Pro'), ('Apple Inc.', 'Apple M1 Max'), ('Apple Inc.', 'Apple M2'), ('Intel Inc.', 'Intel Iris OpenGL Engine')], 'windows': [('Google Inc. (NVIDIA)', 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11)'), ('Google Inc. (NVIDIA)', 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11)'), ('Google Inc. (Intel)', 'ANGLE (Intel, Intel(R) UHD Graphics Direct3D11)'), ('Microsoft Corporation', 'D3D11')], 'linux': [('NVIDIA Corporation', 'NVIDIA GeForce GTX 1060/PCIe/SSE2'), ('Intel Open Source Technology Center', 'Mesa DRI Intel(R) UHD Graphics 620'), ('AMD', 'AMD Radeon Graphics')]}
    COMMON_FONTS = ['Arial', 'Arial Black', 'Arial Narrow', 'Arial Rounded MT Bold', 'Courier', 'Courier New', 'Georgia', 'Helvetica', 'Helvetica Neue', 'Times', 'Times New Roman', 'Verdana', 'Tahoma', 'Trebuchet MS', 'Palatino', 'Garamond', 'Bookman', 'Comic Sans MS', 'Impact', 'Segoe UI', 'Calibri', 'Cambria', 'Geneva', 'Lucida Grande', 'Lucida Sans Unicode', 'Menlo', 'Monaco', 'Consolas']
    COMMON_PLUGINS = [{'name': 'Chrome PDF Plugin', 'filename': 'internal-pdf-viewer', 'description': 'Portable Document Format'}, {'name': 'Chrome PDF Viewer', 'filename': 'mhjfbmdgcfjbbpaeojofohoefgiehjai', 'description': 'Portable Document Format'}, {'name': 'Native Client', 'filename': 'internal-nacl-plugin', 'description': 'Native Client module'}]
    __slots__ = tuple(('_current_profile', '_profile_timestamp', '_rotation_count', 'config'))

    def __init__(self, config: FingerprintConfig | None=None):
        self.config = config or FingerprintConfig()
        self._current_profile: BrowserProfile | None = None
        self._profile_timestamp: float = 0
        self._rotation_count = 0

    def _generate_canvas_noise(self) -> tuple[int, int, int]:
        """Generate subtle canvas noise (invisible to human eye)"""
        return (_RNG.randint(0, 2), _RNG.randint(0, 2), _RNG.randint(0, 2))

    def _generate_screen_resolution(self) -> tuple[int, int, int, float]:
        """Generate realistic screen specs"""
        if _RNG.random() < 0.9:
            width, height = _RNG.choice(self.SCREEN_RESOLUTIONS[:5])
        else:
            width, height = _RNG.choice(self.SCREEN_RESOLUTIONS)
        color_depth = _RNG.choice([24, 32])
        pixel_ratio = _RNG.choice([1.0, 1.0, 1.0, 1.25, 1.5, 2.0])
        return (width, height, color_depth, pixel_ratio)

    def _generate_timezone(self) -> tuple[str, int]:
        """Generate random timezone"""
        if not self.config.randomize_timezone:
            import time
            tz = time.tzname[0] if time.tzname else 'UTC'
            offset = -time.timezone // 3600
            return (tz, offset)
        return _RNG.choice(self.TIMEZONES)

    def _generate_webgl_profile(self, platform: str) -> tuple[str, str]:
        """Generate WebGL vendor/renderer"""
        if not self.config.randomize_webgl:
            return ('', '')
        profiles = self.WEBGL_PROFILES.get(platform, self.WEBGL_PROFILES['macos'])
        return _RNG.choice(profiles)

    def _generate_font_list(self) -> list[str]:
        """Generate randomized font list"""
        if not self.config.randomize_fonts:
            return self.COMMON_FONTS[:10]
        num_fonts = _RNG.randint(10, 15)
        return _RNG.sample(self.COMMON_FONTS, min(num_fonts, len(self.COMMON_FONTS)))

    def _generate_plugins(self) -> list[dict[str, str]]:
        """Generate browser plugins"""
        if not self.config.randomize_plugins:
            return self.COMMON_PLUGINS[:2]
        num_plugins = _RNG.randint(2, len(self.COMMON_PLUGINS))
        return _RNG.sample(self.COMMON_PLUGINS, num_plugins)

    def _generate_hardware_specs(self, platform: str) -> tuple[int, int, int]:
        """Generate hardware specs"""
        if platform == 'macos':
            concurrency = _RNG.choice([8, 8, 10, 10])
            memory = _RNG.choice([8, 16, 16, 32])
        else:
            concurrency = _RNG.choice([4, 4, 8, 8, 8, 16])
            memory = _RNG.choice([4, 8, 8, 16, 16, 32])
        touch_points = 0 if platform != 'mobile' else _RNG.choice([5, 10])
        return (concurrency, memory, touch_points)

    def generate_profile(self, force_new: bool=False) -> BrowserProfile:
        """Generate new browser fingerprint profile"""
        if not force_new and self.config.consistent_per_session and (self._current_profile is not None):
            elapsed = time.time() - self._profile_timestamp
            if elapsed < self.config.session_duration:
                return self._current_profile
        platform = self.config.platform
        if platform is None:
            platform = _RNG.choice(['macos', 'windows', 'linux'])
        width, height, color_depth, pixel_ratio = self._generate_screen_resolution()
        timezone, tz_offset = self._generate_timezone()
        webgl_vendor, webgl_renderer = self._generate_webgl_profile(platform)
        profile = BrowserProfile(screen_width=width, screen_height=height, screen_color_depth=color_depth, screen_pixel_ratio=pixel_ratio, timezone=timezone, timezone_offset=tz_offset, canvas_noise=self._generate_canvas_noise(), webgl_vendor=webgl_vendor, webgl_renderer=webgl_renderer, fonts=self._generate_font_list(), plugins=self._generate_plugins(), hardware_concurrency=self._generate_hardware_specs(platform)[0], device_memory=self._generate_hardware_specs(platform)[1], max_touch_points=self._generate_hardware_specs(platform)[2])
        self._current_profile = profile
        self._profile_timestamp = time.time()
        self._rotation_count += 1
        logger.debug(f'Generated new fingerprint profile ({platform})')
        return profile

    def get_profile(self) -> BrowserProfile:
        """Get current or new profile"""
        return self.generate_profile()

    def get_js_protection_script(self) -> str:
        """Generate JavaScript to apply fingerprint protection"""
        profile = self.get_profile()
        import msgspec.json as _json
        script = f"\n        // Fingerprint Protection Script\n        (function() {{\n            'use strict';\n\n            const profile = {_json.encode({'screen': {{'width': profile.screen_width, 'height': profile.screen_height, 'colorDepth': profile.screen_color_depth, 'pixelRatio': profile.screen_pixel_ratio}}, 'timezone': profile.timezone, 'timezoneOffset': profile.timezone_offset, 'hardwareConcurrency': profile.hardware_concurrency, 'deviceMemory': profile.device_memory, 'maxTouchPoints': profile.max_touch_points, 'canvasNoise': profile.canvas_noise}).decode('utf-8')};\n\n            // Override screen properties\n            Object.defineProperty(screen, 'width', {{ get: () => profile.screen.width }});\n            Object.defineProperty(screen, 'height', {{ get: () => profile.screen.height }});\n            Object.defineProperty(screen, 'colorDepth', {{ get: () => profile.screen.colorDepth }});\n            Object.defineProperty(screen, 'pixelDepth', {{ get: () => profile.screen.colorDepth }});\n\n            // Override window.devicePixelRatio\n            Object.defineProperty(window, 'devicePixelRatio', {{\n                get: () => profile.screen.pixelRatio\n            }});\n\n            // Override hardware specs\n            Object.defineProperty(navigator, 'hardwareConcurrency', {{\n                get: () => profile.hardwareConcurrency\n            }});\n            Object.defineProperty(navigator, 'deviceMemory', {{\n                get: () => profile.deviceMemory\n            }});\n            Object.defineProperty(navigator, 'maxTouchPoints', {{\n                get: () => profile.maxTouchPoints\n            }});\n\n            // AudioContext fingerprint protection - override to return fake values\n            // Prevents advanced servers from detecting headless browser via AudioContext\n            const _origAudioContext = window.AudioContext || window.webkitAudioContext;\n            if (_origAudioContext) {{\n                const _fakeAudioCtx = _origAudioContext;\n                window.AudioContext = function() {{\n                    const ctx = new _fakeAudioCtx();\n                    // Override analyser methods that expose headless fingerprint\n                    const _origCreateAnalyser = ctx.createAnalyser;\n                    if (_origCreateAnalyser) {{\n                        ctx.createAnalyser = function() {{\n                            const analyser = _origCreateAnalyser.call(this);\n                            // Fake the frequency data to look like real browser\n                            const _origGetByteFrequencyData = analyser.getByteFrequencyData;\n                            analyser.getByteFrequencyData = function(array) {{\n                                const result = _origGetByteFrequencyData.call(this, array);\n                                // Add slight random variation to simulate real browser\n                                for (let i = 0; i < array.length; i++) {{\n                                    array[i] = Math.max(0, Math.min(255, array[i] + Math.floor(Math.random() * 8 - 4)));\n                                }}\n                                return array;\n                            }};\n                            return analyser;\n                        }};\n                    }}\n                    return ctx;\n                }};\n                window.AudioContext.prototype = _origAudioContext.prototype;\n                window.webkitAudioContext = window.AudioContext;\n            }}\n\n            // Canvas fingerprint protection\n            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;\n            const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;\n\n            HTMLCanvasElement.prototype.toDataURL = function(...args) {{\n                const ctx = this.getContext('2d');\n                if (ctx) {{\n                    const imageData = ctx.getImageData(0, 0, this.width, this.height);\n                    const data = imageData.data;\n                    // Add imperceptible noise\n                    for (let i = 0; i < data.length; i += 4) {{\n                        data[i] = Math.min(255, data[i] + {profile.canvas_noise[0]});\n                        data[i+1] = Math.min(255, data[i+1] + {profile.canvas_noise[1]});\n                        data[i+2] = Math.min(255, data[i+2] + {profile.canvas_noise[2]});\n                    }}\n                    ctx.putImageData(imageData, 0, 0);\n                }}\n                return originalToDataURL.apply(this, args);\n            }};\n\n            // Timezone protection\n            const originalDate = Date;\n            Date = class extends originalDate {{\n                constructor(...args) {{\n                    super(...args);\n                }}\n                getTimezoneOffset() {{\n                    return profile.timezoneOffset * 60;\n                }}\n            }};\n\n        }})();\n        "
        return script

    def get_fingerprint_hash(self) -> str:
        """Get hash of current fingerprint (for tracking detection)"""
        import hashlib
        profile = self.get_profile()
        fingerprint_data = {'screen': f'{profile.screen_width}x{profile.screen_height}', 'color_depth': profile.screen_color_depth, 'pixel_ratio': profile.screen_pixel_ratio, 'timezone': profile.timezone, 'fonts_hash': hash(tuple(sorted(profile.fonts))) % 10000, 'hardware': f'{profile.hardware_concurrency}c{profile.device_memory}g'}
        fingerprint_str = orjson.dumps(fingerprint_data, option=orjson.OPT_SORT_KEYS).decode()
        return hashlib.sha256(fingerprint_str.encode()).hexdigest()[:16]

    def rotate(self) -> BrowserProfile:
        """Force rotation to new fingerprint"""
        return self.generate_profile(force_new=True)

    def get_statistics(self) -> dict[str, Any]:
        """Get randomization statistics"""
        return {'rotation_count': self._rotation_count, 'current_profile_age': time.time() - self._profile_timestamp if self._profile_timestamp else 0, 'current_fingerprint': self.get_fingerprint_hash(), 'consistent_mode': self.config.consistent_per_session}
logger = logging.getLogger(__name__)

class StealthLayer:
    """
    Stealth layer for web browsing with anti-detection and CAPTCHA solving.

    This layer:
    1. Manages stealth browser instances
    2. Applies detection evasion techniques
    3. Solves CAPTCHAs when detected
    4. Simulates human behavior
    5. Protects against debugging (Chameleon)

    Example:
        stealth = StealthLayer(config)
        await stealth.initialize()

        # Create stealth session
        session = await stealth.create_session()

        # Browse with evasion
        page = await stealth.new_page(session)
        await stealth.apply_evasion(page)

        # Solve CAPTCHA if detected
        solution = await stealth.solve_captcha(page, "https://example.com")
    """
    __slots__ = tuple(('_browsers_created', '_captcha_solver', '_captchas_solved', '_chameleon', '_ctx', '_detection_evader', '_evasions_applied', '_fingerprint_randomizer', '_js_evasion', '_session_counter', '_sessions', '_stealth_browser', 'config', 'layer_name'))

    def __init__(self, config: StealthConfig | None=None):
        """
        Initialize StealthLayer.

        Args:
            config: Stealth configuration (uses defaults if None)
        """
        self.config = config or StealthConfig()
        self._stealth_browser = None
        self._detection_evader = None
        self._captcha_solver: AdvancedCaptchaSolver | None = None
        self._js_evasion: JavaScriptEvasion | None = None
        self._chameleon: Chameleon | None = None
        self._fingerprint_randomizer: FingerprintRandomizer | None = None
        self._sessions: dict[str, StealthSession] = {}
        self._session_counter = 0
        self.layer_name: str = 'stealth'
        self._ctx: Any | None = None
        self._browsers_created = 0
        self._captchas_solved = 0
        self._evasions_applied = 0
        logger.info('StealthLayer initialized')

    async def mount(self, ctx: Any) -> None:
        """Layer Protocol: mount."""
        self._ctx = ctx
        await self.initialize()
        ctx.set('stealth', self)

    async def unmount(self, ctx: Any) -> None:
        """Layer Protocol: unmount."""
        await self.cleanup()

    async def on_event(self, ctx: Any, event: Any) -> Any:
        """Layer Protocol: handle stealth events."""
        return event

    def get_timing_jitter(self) -> float:
        """Return random jitter delay in seconds for fetch timing.

        Uses Gaussian distribution to simulate human-like inter-request timing.
        Returns 0.0 if stealth is disabled or unavailable.

        Jitter is NON-BLOCKING when used with asyncio.sleep() — safe for async.
        """
        if not getattr(self, '_enabled', True):
            return 0.0
        try:
            return max(0.0, min(2.0, _gauss(0.5, 0.3)))
        except Exception:
            return 0.0

    async def initialize(self) -> bool:
        """
        Initialize StealthLayer components.

        Returns:
            True if initialization successful
        """
        try:
            logger.info('🚀 Initializing StealthLayer...')
            if self.config.enable_stealth_scripts:
                await self._init_detection_evader()
            if self.config.enable_captcha_solving:
                await self._init_captcha_solver()
            await self._init_js_evasion()
            await self._init_chameleon()
            await self._init_fingerprint_randomizer()
            logger.info('✅ StealthLayer initialized successfully')
            return True
        except Exception as e:
            logger.error(f'❌ StealthLayer initialization failed: {e}')
            return False

    async def _init_stealth_browser(self) -> None:
        """Lazy initialization of StealthBrowser"""
        if self._stealth_browser is None:
            try:
                from hledac.universal.advanced_web.stealth_browser import BrowserConfig, StealthBrowser
                browser_config = BrowserConfig(browser_type=self.config.browser_type, headless=self.config.headless, pool_size=self.config.pool_size, m1_optimized=True)
                self._stealth_browser = StealthBrowser(browser_config)
                await self._stealth_browser.initialize()
                self._browsers_created += 1
                logger.info('✅ StealthBrowser initialized')
            except ImportError as e:
                logger.warning(f'⚠️ StealthBrowser not available: {e}')
                self._stealth_browser = None

    async def _init_detection_evader(self) -> None:
        """Lazy initialization of DetectionEvader"""
        if self._detection_evader is None:
            try:
                from hledac.universal.advanced_web.detection_evader import DetectionEvader
                self._detection_evader = DetectionEvader(detection_threshnew=self.config.detection_threshold, adaptive_mode=self.config.adaptive_mode)
                logger.info('✅ DetectionEvader initialized')
            except ImportError as e:
                logger.warning(f'⚠️ DetectionEvader not available: {e}')
                self._detection_evader = None

    async def _init_captcha_solver(self) -> None:
        """Lazy initialization of AdvancedCaptchaSolver (self-hosted, OFF BY DEFAULT on M1 8GB).

        M1 8GB: Local OCR (torch/transformers) is HEAVY.
        Default: use CaptchaSolvingStrategy (2captcha API primary, Vision/CoreML secondary).
        Only load AdvancedCaptchaSolver if HLEDAC_ENABLE_CAPTCHA_LOCAL=1.
        """
        import os
        if self._captcha_solver is not None:
            return
        if not self.config.enable_captcha_local:
            logger.debug('AdvancedCaptchaSolver: disabled (enable_captcha_local=False)')
            return
        try:
            config = CaptchaSolverConfig(enable_image_ocr=True, enable_text_logic=True, confidence_threshold=0.6)
            self._captcha_solver = AdvancedCaptchaSolver(config)
            await self._captcha_solver.initialize()
            logger.info('✅ AdvancedCaptchaSolver initialized (local OCR, enable_captcha_local=True)')
        except Exception as e:
            logger.warning(f'⚠️ AdvancedCaptchaSolver not available: {e}')
            self._captcha_solver = None

    async def _init_js_evasion(self) -> None:
        """Initialize JavaScript evasion (15+ anti-detection scripts)"""
        if self._js_evasion is None:
            try:
                config = JavaScriptEvasionConfig(hide_webdriver=True, hide_automation=True, spoof_plugins=True, spoof_permissions=True, disable_webrtc=True, override_canvas=True, override_webgl=True, spoof_fonts=True, emulate_human_events=True, patch_detection_libs=True, randomize_globals=True, spoof_chrome_runtime=True, add_chrome_plugins=True)
                self._js_evasion = JavaScriptEvasion(config)
                logger.info('✅ JavaScriptEvasion initialized (15+ evasion scripts)')
            except Exception as e:
                logger.warning(f'⚠️ JavaScriptEvasion initialization failed: {e}')
                self._js_evasion = None

    async def _init_chameleon(self) -> None:
        """Initialize Chameleon for anti-debugging."""
        try:
            self._chameleon = Chameleon()
            self._chameleon.masquerade_process()
            if self._chameleon.initialize_ptrace_protection():
                logger.info('✅ Chameleon anti-debugging initialized (ptrace)')
            else:
                logger.info('✅ Chameleon initialized (ptrace not available)')
        except Exception as e:
            logger.warning(f'⚠️ Chameleon not available: {e}')
            self._chameleon = None

    async def _init_fingerprint_randomizer(self) -> None:
        """Initialize FingerprintRandomizer for browser fingerprint protection."""
        try:
            self._fingerprint_randomizer = FingerprintRandomizer()
            logger.info('✅ FingerprintRandomizer initialized')
        except Exception as e:
            logger.warning(f'⚠️ FingerprintRandomizer initialization failed: {e}')
            self._fingerprint_randomizer = None

    def get_chameleon(self) -> Chameleon | None:
        """Get Chameleon instance for anti-debugging control."""
        return self._chameleon

    def is_debugger_present(self) -> bool:
        """Check if a debugger is attached (macOS only)."""
        if self._chameleon:
            return self._chameleon.is_debugger_present()
        return False

    async def create_session(self, browser_type: BrowserType | None=None, proxy: str | None=None) -> StealthSession:
        """
        Create a new stealth browsing session.

        Args:
            browser_type: Browser type (uses config default if None)
            proxy: Proxy URL (optional)

        Returns:
            StealthSession
        """
        self._session_counter += 1
        session_id = f'stealth_{self._session_counter}'
        browser_type = browser_type or BrowserType(self.config.browser_type)
        logger.info(f'🔒 Creating stealth session: {session_id}')
        if self._stealth_browser is None:
            await self._init_stealth_browser()
        fingerprint = await self._generate_fingerprint()
        session = StealthSession(session_id=session_id, browser_type=browser_type, fingerprint=fingerprint, proxy=proxy, risk_level=RiskLevel.LOW, created_at=time.time())
        self._sessions[session_id] = session
        return session

    async def _generate_fingerprint(self) -> dict[str, Any]:
        """Generate browser fingerprint"""
        if self.config.enable_fingerprint_rotation and self._detection_evader:
            try:
                return {'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.0', 'screen': {'width': 1920, 'height': 1080}, 'timezone': 'America/New_York', 'language': 'en-US', 'platform': 'MacIntel', 'plugins': ['Chrome PDF Plugin', 'Native Client']}
            except Exception:
                pass
        return {'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'screen': {'width': 1920, 'height': 1080}}

    async def new_page(self, session: StealthSession) -> Any:
        """
        Create a new page in the stealth session.

        Args:
            session: StealthSession

        Returns:
            Playwright page object
        """
        if self._stealth_browser is None:
            raise RuntimeError('StealthBrowser not initialized')
        try:
            page = await self._stealth_browser.new_page()
            logger.debug(f'📄 New page created for session {session.session_id}')
            return page
        except Exception as e:
            logger.error(f'❌ Failed to create page: {e}')
            raise

    async def apply_evasion(self, page: Any, risk_level: RiskLevel | None=None) -> None:
        """
        Apply detection evasion scripts to page.

        Args:
            page: Playwright page
            risk_level: Risk level (auto-detect if None)
        """
        if not self.config.enable_stealth_scripts:
            return
        if self._detection_evader is None:
            logger.warning('⚠️ DetectionEvader not available, skipping evasion')
            return
        try:
            if risk_level is None:
                content = await page.content() if hasattr(page, 'content') else ''
                risk_level = self._detection_evader.analyze_page_content(content)
            scripts = self._detection_evader.get_evasion_scripts()
            if self._js_evasion:
                js_scripts = self._js_evasion.get_all_evasion_scripts()
                scripts.extend(js_scripts)
                logger.debug(f'🛡️ Added {len(js_scripts)} JavaScript evasion scripts')
            if self._fingerprint_randomizer:
                fingerprint_script = self._fingerprint_randomizer.get_js_protection_script()
                scripts.append(fingerprint_script)
            for script in scripts:
                try:
                    await page.add_init_script(script)
                except Exception as e:
                    logger.debug(f'⚠️ Failed to add script: {e}')
            self._evasions_applied += 1
            logger.info(f'🛡️ Applied {len(scripts)} evasion scripts (risk: {risk_level.value})')
        except Exception as e:
            logger.warning(f'⚠️ Evasion application failed: {e}')

    async def simulate_human_behavior(self, page: Any) -> None:
        """
        Simulate human-like behavior on page.

        Args:
            page: Playwright page
        """
        if not self.config.enable_behavior_simulation:
            return
        if self._detection_evader is None:
            return
        try:
            await self._detection_evader.simulate_human_behavior(page)
            logger.debug('🎭 Human behavior simulated')
        except Exception as e:
            logger.debug(f'⚠️ Behavior simulation failed: {e}')

    async def solve_captcha(self, page: Any, url: str, captcha_type: CaptchaType | None=None) -> CaptchaSolution | None:
        """
        Detect and solve CAPTCHA on page.

        Args:
            page: Playwright page
            url: Page URL
            captcha_type: CAPTCHA type (auto-detect if None)

        Returns:
            CaptchaSolution or None if no CAPTCHA
        """
        if not self.config.enable_captcha_solving:
            return None
        if self._captcha_solver is None:
            logger.warning('⚠️ CaptchaSolver not available')
            return None
        try:
            html = await page.content() if hasattr(page, 'content') else ''
            detected_type = captcha_type or self._captcha_solver.detect_captcha(html)
            if detected_type == CaptchaType.IMAGE:
                logger.info('🧩 Image CAPTCHA detected')
                import re
                img_match = re.search('<img[^>]+src=["\\\']([^"\\\']+)["\\\'][^>]*>', html, re.IGNORECASE)
                if img_match:
                    img_url = img_match.group(1)
                    if img_url.startswith('/'):
                        from urllib.parse import urljoin
                        img_url = urljoin(url, img_url)
                    try:
                        if img_url.startswith('data:'):
                            import base64
                            img_data = img_url.split(',', 1)[1]
                            image_bytes = base64.b64decode(img_data)
                        else:
                            try:
                                img_response = await page.evaluate('(async () => { const resp = await fetch(arguments[0]); return resp.ok ? await resp.arrayBuffer() : null; })()', img_url)
                                if img_response:
                                    image_bytes = bytes(img_response)
                                else:
                                    image_bytes = None
                            except Exception:
                                image_bytes = None
                        if image_bytes:
                            result = await self.solve_captcha(captcha_type=CaptchaType.IMAGE, image_data=image_bytes)
                            if result.success:
                                self._captchas_solved += 1
                                return CaptchaSolution(solution=result.solution or '', solved_at=time.time(), cost=0.0, confidence=result.confidence, provider='internal_ocr')
                    except Exception as e:
                        logger.warning(f'Image CAPTCHA fetch/solve failed: {e}')
                return None
            elif detected_type in (CaptchaType.RECAPTCHA_V2, CaptchaType.RECAPTCHA_V3):
                logger.info('🧩 reCAPTCHA detected')
                import re
                site_key_match = re.search('data-sitekey="([^"]+)"', html)
                if site_key_match:
                    site_key = site_key_match.group(1)
                    solution = await self._captcha_solver.solve_captcha(captcha_type=detected_type, site_key=site_key, url=url)
                    self._captchas_solved += 1
                    return CaptchaSolution(solution=solution if isinstance(solution, str) else str(solution), solved_at=time.time(), cost=0.002, confidence=0.9, provider=self.config.captcha_providers[0] if self.config.captcha_providers else 'unknown')
            return None
        except Exception as e:
            logger.error(f'❌ CAPTCHA solving failed: {e}')
            return None

    async def close_session(self, session_id: str) -> None:
        """
        Close a stealth session.

        Args:
            session_id: Session ID
        """
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.debug(f'🔒 Session closed: {session_id}')

    def get_fingerprint_protection(self) -> str:
        """Get JavaScript fingerprint protection script"""
        if self._fingerprint_randomizer:
            return self._fingerprint_randomizer.get_js_protection_script()
        return ''

    def rotate_fingerprint(self) -> BrowserProfile | None:
        """Force rotation to new browser fingerprint.

        Rotates both HTTP headers (via ZeroAttributionEngine) and browser JA3
        fingerprint (via FingerprintRandomizer) for full layer-7 anonymity.
        Fail-soft: never break stealth rotation on any exception.
        """
        from hledac.universal.security.zero_attribution_engine import ZeroAttributionEngine
        try:
            _header_engine = ZeroAttributionEngine()
            _header_engine.fingerprint_rotate_headers()
        except Exception:
            pass
        if self._fingerprint_randomizer:
            return self._fingerprint_randomizer.rotate()
        return None

    def get_js_evasion_score(self) -> dict[str, Any] | None:
        """Get JavaScript evasion coverage score"""
        if self._js_evasion:
            return self._js_evasion.get_detection_score()
        return None

    def get_captcha_solver_stats(self) -> dict[str, Any] | None:
        """Get CAPTCHA solver statistics"""
        if self._captcha_solver:
            return self._captcha_solver.get_statistics()
        return None

    def get_statistics(self) -> dict[str, Any]:
        """Get stealth layer statistics"""
        return {'browsers_created': self._browsers_created, 'sessions_active': len(self._sessions), 'captchas_solved': self._captchas_solved, 'evasions_applied': self._evasions_applied, 'stealth_browser_available': self._stealth_browser is not None, 'detection_evader_available': self._detection_evader is not None, 'captcha_solver_available': self._captcha_solver is not None, 'js_evasion_available': self._js_evasion is not None, 'chameleon_available': self._chameleon is not None, 'fingerprint_randomizer_available': self._fingerprint_randomizer is not None, 'anti_debugging_active': self._chameleon.is_debugger_protected() if self._chameleon else False, 'fingerprint_stats': self._fingerprint_randomizer.get_statistics() if self._fingerprint_randomizer else None, 'js_evasion_score': self.get_js_evasion_score(), 'captcha_solver_stats': self.get_captcha_solver_stats(), 'config': {'browser_type': self.config.browser_type, 'headless': self.config.headless, 'enable_stealth_scripts': self.config.enable_stealth_scripts, 'enable_captcha_solving': self.config.enable_captcha_solving}}

    async def cleanup(self) -> None:
        """Cleanup resources"""
        logger.info('🧹 Cleaning up StealthLayer...')
        self._sessions.clear()
        if self._stealth_browser and hasattr(self._stealth_browser, 'close'):
            try:
                await self._stealth_browser.close()
            except Exception as e:
                logger.warning(f'⚠️ StealthBrowser cleanup error: {e}')
        if self._captcha_solver and hasattr(self._captcha_solver, 'close'):
            try:
                await self._captcha_solver.close()
            except Exception as e:
                logger.warning(f'⚠️ CaptchaSolver cleanup error: {e}')
        logger.info('✅ StealthLayer cleanup complete')
import ctypes
import ctypes.util
import os

class Chameleon:
    """
    Chameleon - Anti-debugging and process masquerading for macOS M1.

    Integrated from kernel/stealth/chameleon.py - Provides protection
    against debugging and process masquerading for stealth operations.

    Features:
    - Process masquerading (change process name to appear benign)
    - ptrace(PT_DENY_ATTACH) anti-debugging (macOS only)
    - Environment cleanup to remove debugging indicators

    Example:
        chameleon = Chameleon()

        # Apply process masquerading
        chameleon.masquerade_process()

        # Initialize anti-debugging
        chameleon.initialize_ptrace_protection()

        # Check if debugger is present
        if chameleon.is_debugger_present():
            print("Debugger detected!")
    """
    MASQUERADE_TARGETS = [('mdworker_shared', 'Spotlight indexer'), ('mds_stores', 'Metadata server'), ('syslogd', 'System logger'), ('locationd', 'Location services'), ('bluetoothd', 'Bluetooth daemon'), ('coreaudiod', 'Audio daemon'), ('powerd', 'Power management'), ('airportd', 'WiFi daemon')]
    __slots__ = tuple(('_masqueraded', '_original_name', '_ptrace_protected'))

    def __init__(self):
        """Initialize Chameleon."""
        self._original_name: str | None = None
        self._masqueraded = False
        self._ptrace_protected = False
        logger.debug('Chameleon initialized')

    def masquerade_process(self, target_index: int | None=None) -> bool:
        """
        Masquerade process as a benign system process.

        Args:
            target_index: Index of MASQUERADE_TARGETS to use (random if None)

        Returns:
            True if successful
        """
        try:
            if target_index is None:
                target_index = _RNG.randint(0, len(self.MASQUERADE_TARGETS) - 1)
            target_name, target_desc = self.MASQUERADE_TARGETS[target_index]
            self._original_name = sys.argv[0] if sys.argv else 'python'
            try:
                import setproctitle
                setproctitle.setproctitle(target_name)
                self._masqueraded = True
                logger.info(f"Chameleon: Masquerading as '{target_name}' ({target_desc})")
                return True
            except ImportError:
                if sys.argv:
                    sys.argv[0] = target_name
                    self._masqueraded = True
                    logger.info(f"Chameleon: Masquerading as '{target_name}' (via argv)")
                    return True
            return False
        except Exception as e:
            logger.warning(f'Chameleon: Masquerade failed: {e}')
            return False

    def initialize_ptrace_protection(self) -> bool:
        """
        Initialize ptrace anti-debugging protection (macOS only).

        Uses PT_DENY_ATTACH to prevent debugger attachment.

        Returns:
            True if protection was successfully applied
        """
        if sys.platform != 'darwin':
            logger.debug('Chameleon: ptrace protection only available on macOS')
            return False
        try:
            libc = ctypes.CDLL(ctypes.util.find_library('c'))
            PT_DENY_ATTACH = 31
            result = libc.ptrace(PT_DENY_ATTACH, 0, 0, 0)
            if result == 0:
                self._ptrace_protected = True
                logger.info('Chameleon: ptrace anti-debugging enabled (PT_DENY_ATTACH)')
                return True
            else:
                logger.warning(f'Chameleon: ptrace returned {result}')
                return False
        except Exception as e:
            logger.warning(f'Chameleon: ptrace initialization failed: {e}')
            return False

    def is_debugger_present(self) -> bool:
        """
        Check if a debugger is attached (macOS only).

        Returns:
            True if debugger detected
        """
        if sys.platform != 'darwin':
            return False
        try:
            import subprocess
            result = subprocess.run(['sysctl', '-n', 'kern.proc.pid', str(os.getpid())], capture_output=True, text=True, timeout=1)
            if 'P_TRACED' in result.stdout or 'traced' in result.stdout.lower():
                return True
            libc = ctypes.CDLL(ctypes.util.find_library('c'))
            PT_TRACE_ME = 0
            result = libc.ptrace(PT_TRACE_ME, 0, 0, 0)
            if result < 0:
                return True
            return False
        except Exception as e:
            logger.debug(f'Chameleon: Debugger check failed: {e}')
            return False

    def is_debugger_protected(self) -> bool:
        """Check if ptrace protection is active."""
        return self._ptrace_protected

    def cleanup_environment(self) -> None:
        """Clean environment variables that might indicate debugging."""
        debug_vars = ['DEBUG', 'PYTHONBREAKPOINT', 'PYDEVD', 'IDE_PROJECT_ROOTS', 'PYTHONPATH_DEBUG']
        for var in debug_vars:
            if var in os.environ:
                del os.environ[var]
                logger.debug(f'Chameleon: Removed {var} from environment')

    def get_info(self) -> dict[str, Any]:
        """Get Chameleon status information."""
        return {'masqueraded': self._masqueraded, 'original_name': self._original_name, 'current_masquerade': sys.argv[0] if self._masqueraded else None, 'ptrace_protected': self._ptrace_protected, 'debugger_present': self.is_debugger_present(), 'platform': sys.platform}
__all__ = ['StealthLayer', 'StealthConfig', 'StealthSession', 'RiskLevel', 'BrowserType', 'CaptchaType', 'CaptchaSolution', 'BehaviorSimulator', 'BehaviorPattern', 'SimulationConfig', 'MouseMovement', 'ScrollAction', 'FingerprintRandomizer', 'FingerprintConfig', 'BrowserProfile', 'AdvancedCaptchaSolver', 'CaptchaSolverConfig', 'CaptchaResult', 'JavaScriptEvasion', 'JavaScriptEvasionConfig', 'Chameleon']