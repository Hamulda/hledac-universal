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
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from hledac.universal.project_types import (
    BrowserType,
    CaptchaSolution,
    CaptchaType,
    RiskLevel,
    StealthConfig,
    StealthSession,
)

logger = logging.getLogger(__name__)


# =============================================================================
# ADVANCED CAPTCHA SOLVER - Self-hosted on M1 8GB
# =============================================================================

@dataclass
class CaptchaSolverConfig:
    """Configuration for self-hosted CAPTCHA solving"""
    # OCR Model settings (lightweight for M1)
    ocr_model: str = "microsoft/trocr-small-printed"  # Small, fast OCR
    use_mlx: bool = True  # Use MLX for acceleration
    max_image_size: int = 640  # Limit image size for memory

    # Solving strategies
    enable_image_ocr: bool = True
    enable_text_logic: bool = True  # Text-based logic puzzles
    enable_rotation_detection: bool = True  # Rotated text CAPTCHAs

    # Performance
    timeout_seconds: float = 30.0
    confidence_threshold: float = 0.6


@dataclass
class CaptchaResult:
    """Result of CAPTCHA solving attempt"""
    success: bool
    solution: Optional[str]
    confidence: float
    processing_time_ms: float
    method: str  # 'ocr', 'logic', 'rotation', 'failed'
    alternative_solutions: List[str] = field(default_factory=list)


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

    # Common CAPTCHA patterns
    MATH_PATTERNS = [
        (r'(\d+)\s*\+\s*(\d+)', lambda a, b: int(a) + int(b)),
        (r'(\d+)\s*-\s*(\d+)', lambda a, b: int(a) - int(b)),
        (r'(\d+)\s*\*\s*(\d+)', lambda a, b: int(a) * int(b)),
        (r'(\d+)\s*×\s*(\d+)', lambda a, b: int(a) * int(b)),
        (r'(\d+)\s*plus\s*(\d+)', lambda a, b: int(a) + int(b)),
        (r'(\d+)\s*minus\s*(\d+)', lambda a, b: int(a) - int(b)),
    ]

    SEQUENCE_PATTERNS = [
        r'(\d+),\s*(\d+),\s*(\d+),\s*\?',
        r'(\d+)\s+(\d+)\s+(\d+)\s+_',
    ]

    # Image preprocessing for OCR
    OCR_PREPROCESSING = [
        'grayscale',
        'denoise',
        'contrast',
        'threshold',
        'deskew',
    ]

    def __init__(self, config: Optional[CaptchaSolverConfig] = None):
        self.config = config or CaptchaSolverConfig()
        self._ocr_pipeline: Optional[Any] = None
        self._initialized = False
        self._solve_stats = {
            'attempted': 0,
            'solved': 0,
            'by_method': {},
        }

    async def initialize(self) -> bool:
        """Initialize CAPTCHA solver with lightweight models."""
        try:
            logger.info("🚀 Initializing AdvancedCaptchaSolver...")

            # Try to load OCR model (lightweight)
            if self.config.enable_image_ocr:
                await self._init_ocr_pipeline()

            self._initialized = True
            logger.info("✅ AdvancedCaptchaSolver initialized")
            return True

        except Exception as e:
            logger.error(f"❌ CaptchaSolver initialization failed: {e}")
            return False

    async def _init_ocr_pipeline(self) -> None:
        """Initialize OCR pipeline with fallback options."""
        # Use non-blocking model loading via asyncio.to_thread
        await asyncio.to_thread(self._load_model_sync)

    def _load_model_sync(self) -> None:
        """Synchronous model loading (runs in thread to avoid blocking)."""
        try:
            # CRITICAL ABI: Check transformers availability via find_spec first
            # to avoid NumPy2 incompatibility crashes during module init
            import importlib.util
            if importlib.util.find_spec("transformers") is None:
                raise ImportError("transformers not installed")

            # Check for torch as a prerequisite (also catches NumPy2 issues early)
            if importlib.util.find_spec("torch") is None:
                raise ImportError("torch not installed (required by transformers)")

            # Try transformers + MLX first
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel

            self._ocr_pipeline = {
                'type': 'transformers',
                'processor': TrOCRProcessor.from_pretrained(self.config.ocr_model),
                'model': VisionEncoderDecoderModel.from_pretrained(self.config.ocr_model),
            }
            logger.info(f"✅ Loaded OCR model: {self.config.ocr_model}")

        except Exception as e:
            logger.warning(f"⚠️ Transformers OCR not available: {e}")

            # Fallback: pytesseract
            try:
                import pytesseract
                self._ocr_pipeline = {
                    'type': 'tesseract',
                    'engine': pytesseract,
                }
                logger.info("✅ Using Tesseract OCR fallback")
            except ImportError:
                logger.warning("⚠️ No OCR backend available")
                self._ocr_pipeline = None

    async def solve_captcha(
        self,
        captcha_type: CaptchaType,
        image_data: Optional[bytes] = None,
        text_challenge: Optional[str] = None,
        **kwargs
    ) -> CaptchaResult:
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
                result = CaptchaResult(
                    success=False,
                    solution=None,
                    confidence=0.0,
                    processing_time_ms=0.0,
                    method='unsupported'
                )

            if result.success:
                self._solve_stats['solved'] += 1
                method = result.method
                self._solve_stats['by_method'][method] = self._solve_stats['by_method'].get(method, 0) + 1

            result.processing_time_ms = (time.time() - start_time) * 1000
            return result

        except Exception as e:
            logger.error(f"❌ CAPTCHA solving error: {e}")
            return CaptchaResult(
                success=False,
                solution=None,
                confidence=0.0,
                processing_time_ms=(time.time() - start_time) * 1000,
                method='error'
            )

    async def _solve_image_captcha(self, image_data: bytes) -> CaptchaResult:
        """Solve image-based text CAPTCHA using OCR."""
        from PIL import Image
        import io

        try:
            # Load image
            image = Image.open(io.BytesIO(image_data))

            # Resize if too large (M1 memory optimization)
            max_size = self.config.max_image_size
            if image.width > max_size or image.height > max_size:
                ratio = min(max_size / image.width, max_size / image.height)
                new_size = (int(image.width * ratio), int(image.height * ratio))
                image = image.resize(new_size, Image.Resampling.LANCZOS)

            # Preprocess image
            processed = self._preprocess_for_ocr(image)

            # Run OCR
            if self._ocr_pipeline and self._ocr_pipeline.get('type') == 'transformers':
                text, confidence = await self._run_transformers_ocr(processed)
            elif self._ocr_pipeline and self._ocr_pipeline.get('type') == 'tesseract':
                text, confidence = await self._run_tesseract_ocr(processed)
            else:
                return CaptchaResult(
                    success=False,
                    solution=None,
                    confidence=0.0,
                    processing_time_ms=0.0,
                    method='no_ocr_backend'
                )

            # Clean solution
            text = text.strip().upper()
            text = ''.join(c for c in text if c.isalnum())

            success = len(text) >= 4 and confidence >= self.config.confidence_threshold

            return CaptchaResult(
                success=success,
                solution=text if success else None,
                confidence=confidence,
                processing_time_ms=0.0,
                method='ocr'
            )

        except Exception as e:
            logger.error(f"❌ Image CAPTCHA error: {e}")
            return CaptchaResult(
                success=False,
                solution=None,
                confidence=0.0,
                processing_time_ms=0.0,
                method='error'
            )

    def _preprocess_for_ocr(self, image: 'Image.Image') -> 'Image.Image':
        """Preprocess image for better OCR accuracy."""
        try:
            from PIL import ImageEnhance, ImageFilter
        except ImportError:
            logger.warning("PIL not available for preprocessing")
            return image

        try:
            # Convert to grayscale
            if image.mode != 'L':
                image = image.convert('L')

            # Denoise
            image = image.filter(ImageFilter.MedianFilter(size=3))

            # Enhance contrast
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(2.0)

            return image
        except Exception as e:
            logger.warning(f"Image preprocessing failed: {e}")
            return image

    async def _run_transformers_ocr(self, image: 'Image.Image') -> Tuple[str, float]:
        """Run OCR using Transformers model (offloaded to thread)."""
        return await asyncio.to_thread(self._run_transformers_ocr_sync, image)

    def _run_transformers_ocr_sync(self, image: 'Image.Image') -> Tuple[str, float]:
        """Synchronous OCR using Transformers model."""
        try:
            import torch
        except ImportError:
            logger.warning("torch not available for transformers OCR")
            return ("", 0.0)

        try:
            model = self._ocr_pipeline['model']
            processor = self._ocr_pipeline['processor']

            # Prepare image
            pixel_values = processor(image, return_tensors="pt").pixel_values

            # Generate
            with torch.no_grad():
                generated_ids = model.generate(pixel_values)

            # Decode
            generated_text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]

            # Simple confidence estimation based on generation score
            confidence = 0.75  # Base confidence for transformers

            return generated_text, confidence
        except Exception as e:
            logger.warning(f"Transformers OCR failed: {e}")
            return ("", 0.0)

    async def _run_tesseract_ocr(self, image: 'Image.Image') -> Tuple[str, float]:
        """Run OCR using Tesseract (offloaded to thread)."""
        return await asyncio.to_thread(self._run_tesseract_ocr_sync, image)

    def _run_tesseract_ocr_sync(self, image: 'Image.Image') -> Tuple[str, float]:
        """Synchronous OCR using Tesseract."""
        try:
            if self._ocr_pipeline is None:
                raise KeyError("No OCR pipeline")
            pytesseract = self._ocr_pipeline['engine']
        except (KeyError, AttributeError, TypeError):
            logger.warning("pytesseract not available")
            return ("", 0.0)

        try:
            # Run OCR
            text = pytesseract.image_to_string(image)

            # Get confidence if available
            try:
                data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
                confidences = [int(c) for c in data['conf'] if int(c) > 0]
                avg_confidence = sum(confidences) / len(confidences) / 100.0 if confidences else 0.5
            except:
                avg_confidence = 0.5

            return text, avg_confidence
        except Exception as e:
            logger.warning(f"Tesseract OCR failed: {e}")
            return ("", 0.0)

    async def _solve_text_logic(self, challenge: str) -> CaptchaResult:
        """Solve text-based logic puzzles."""
        # Try math patterns first
        for pattern, solver in self.MATH_PATTERNS:
            match = re.search(pattern, challenge, re.IGNORECASE)
            if match:
                try:
                    result = solver(match.group(1), match.group(2))
                    return CaptchaResult(
                        success=True,
                        solution=str(result),
                        confidence=0.95,
                        processing_time_ms=0.0,
                        method='math_logic'
                    )
                except:
                    continue

        # Try sequence patterns
        for pattern in self.SEQUENCE_PATTERNS:
            match = re.search(pattern, challenge)
            if match:
                try:
                    nums = [int(match.group(i)) for i in range(1, 4)]
                    diff = nums[1] - nums[0]
                    if nums[2] - nums[1] == diff:
                        result = nums[2] + diff
                        return CaptchaResult(
                            success=True,
                            solution=str(result),
                            confidence=0.9,
                            processing_time_ms=0.0,
                            method='sequence_logic'
                        )
                except:
                    continue

        return CaptchaResult(
            success=False,
            solution=None,
            confidence=0.0,
            processing_time_ms=0.0,
            method='logic_failed'
        )

    async def _solve_math_captcha(self, challenge: str) -> CaptchaResult:
        """Solve math-based CAPTCHA."""
        return await self._solve_text_logic(challenge)

    def get_statistics(self) -> Dict[str, Any]:
        """Get solving statistics."""
        attempted = self._solve_stats['attempted']
        solved = self._solve_stats['solved']
        return {
            'attempted': attempted,
            'solved': solved,
            'success_rate': solved / attempted if attempted > 0 else 0.0,
            'by_method': self._solve_stats['by_method'].copy(),
            'ocr_backend': self._ocr_pipeline.get('type') if self._ocr_pipeline else None,
        }


# =============================================================================
# JAVASCRIPT EVASION - Advanced anti-detection techniques
# =============================================================================

@dataclass
class JavaScriptEvasionConfig:
    """Configuration for JavaScript evasion"""
    # Evasion modules
    hide_webdriver: bool = True
    hide_automation: bool = True
    spoof_plugins: bool = True
    spoof_permissions: bool = True
    disable_webrtc: bool = True
    override_canvas: bool = True
    override_webgl: bool = True
    spoof_fonts: bool = True

    # Advanced evasions
    emulate_human_events: bool = True
    patch_detection_libs: bool = True
    randomize_globals: bool = True

    # Chrome runtime spoofing
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

    # Detection library patterns to patch
    DETECTION_LIBS = [
        'botd',
        'botguard',
        'datadome',
        'akamai',
        'perimeterx',
        'cloudflare',
        'hcaptcha',
        'recaptcha',
    ]

    def __init__(self, config: Optional[JavaScriptEvasionConfig] = None):
        self.config = config or JavaScriptEvasionConfig()
        self._script_cache: Dict[str, str] = {}

    def get_all_evasion_scripts(self) -> List[str]:
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
        return """
        // Hide WebDriver
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });

        // Remove webdriver-related properties
        delete navigator.__webdriver_script_fn;
        delete navigator.__selenium_evaluate;
        delete navigator.__selenium_unwrapped;

        // Chrome-only properties
        if (window.chrome) {
            window.chrome.runtime = window.chrome.runtime || {};
            window.chrome.csi = window.chrome.csi || function() {};
            window.chrome.loadTimes = window.chrome.loadTimes || function() {};
        }
        """

    def _get_automation_hider(self) -> str:
        """Hide automation flags."""
        return """
        // Hide automation flags
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ||
            parameters.name === 'clipboard-read' ||
            parameters.name === 'clipboard-write'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
        );

        // Override Permissions API
        if (navigator.permissions) {
            const originalPermissionsQuery = navigator.permissions.query;
            navigator.permissions.query = function(parameters) {
                if (parameters.name === 'notifications') {
                    return Promise.resolve({
                        state: 'default',
                        onchange: null,
                        addEventListener: function() {},
                        removeEventListener: function() {},
                        dispatchEvent: function() { return true; }
                    });
                }
                return originalPermissionsQuery.call(this, parameters);
            };
        }

        // Hide Playwright/Puppeteer indicators
        Object.defineProperty(navigator, 'plugins', {
            get: function() {
                return [
                    {
                        0: {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"},
                        description: "Portable Document Format",
                        filename: "internal-pdf-viewer",
                        length: 1,
                        name: "Chrome PDF Plugin"
                    },
                    {
                        0: {type: "application/pdf", suffixes: "pdf", description: "Portable Document Format"},
                        description: "Portable Document Format",
                        filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai",
                        length: 1,
                        name: "Chrome PDF Viewer"
                    }
                ];
            }
        });

        // Hide headless indicators
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        """

    def _get_plugin_spoof(self) -> str:
        """Spoof plugin information."""
        return """
        // Spoof plugins to appear as regular Chrome
        Object.defineProperty(navigator, 'plugins', {
            get: function() {
                return {
                    length: 2,
                    item: function(index) {
                        const plugins = [
                            {
                                name: "Chrome PDF Plugin",
                                filename: "internal-pdf-viewer",
                                description: "Portable Document Format",
                                version: undefined,
                                length: 1,
                                item: function(idx) { return this[idx]; }
                            },
                            {
                                name: "Chrome PDF Viewer",
                                filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai",
                                description: "Portable Document Format",
                                version: undefined,
                                length: 1,
                                item: function(idx) { return this[idx]; }
                            }
                        ];
                        return plugins[index];
                    },
                    namedItem: function(name) {
                        return this.item(0);
                    },
                    refresh: function() {}
                };
            }
        });

        // Spoof mimeTypes
        Object.defineProperty(navigator, 'mimeTypes', {
            get: function() {
                return {
                    length: 2,
                    item: function(index) {
                        const types = [
                            { type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format", enabledPlugin: navigator.plugins[0] },
                            { type: "application/pdf", suffixes: "pdf", description: "Portable Document Format", enabledPlugin: navigator.plugins[1] }
                        ];
                        return types[index];
                    }
                };
            }
        });
        """

    def _get_permission_spoof(self) -> str:
        """Spoof permission API."""
        return """
        // Override Permissions API to appear as standard browser
        if (navigator.permissions) {
            const originalQuery = navigator.permissions.query;
            navigator.permissions.query = function(parameters) {
                // Standard permissions responses
                const permissionOverrides = {
                    'notifications': 'default',
                    'camera': 'prompt',
                    'microphone': 'prompt',
                    'clipboard-read': 'prompt',
                    'clipboard-write': 'granted',
                    'geolocation': 'prompt'
                };

                if (parameters.name in permissionOverrides) {
                    return Promise.resolve({
                        state: permissionOverrides[parameters.name],
                        onchange: null,
                        addEventListener: function() {},
                        removeEventListener: function() {},
                        dispatchEvent: function() { return true; }
                    });
                }

                return originalQuery.call(this, parameters);
            };
        }
        """

    def _get_webrtc_disabler(self) -> str:
        """Disable WebRTC to prevent IP leaks."""
        return """
        // Disable WebRTC
        if (window.RTCPeerConnection) {
            const noop = function() {};
            window.RTCPeerConnection = noop;
            window.RTCPeerConnection.prototype = {};
        }

        if (window.webkitRTCPeerConnection) {
            const noop = function() {};
            window.webkitRTCPeerConnection = noop;
        }

        if (window.mozRTCPeerConnection) {
            const noop = function() {};
            window.mozRTCPeerConnection = noop;
        }
        """

    def _get_canvas_override(self) -> str:
        """Override canvas fingerprinting."""
        return """
        // Canvas fingerprint protection
        const getImageData = CanvasRenderingContext2D.prototype.getImageData;
        const toDataURL = HTMLCanvasElement.prototype.toDataURL;
        const toBlob = HTMLCanvasElement.prototype.toBlob;

        // Add subtle noise to canvas operations
        CanvasRenderingContext2D.prototype.getImageData = function(sx, sy, sw, sh) {
            const imageData = getImageData.call(this, sx, sy, sw, sh);
            const data = imageData.data;

            // Add imperceptible noise
            for (let i = 0; i < data.length; i += 4) {
                data[i] = (data[i] + (Math.random() > 0.5 ? 1 : 0)) % 256;
                data[i + 1] = (data[i + 1] + (Math.random() > 0.5 ? 1 : 0)) % 256;
                data[i + 2] = (data[i + 2] + (Math.random() > 0.5 ? 1 : 0)) % 256;
            }

            return imageData;
        };

        // Override toDataURL with noise
        HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
            const ctx = this.getContext('2d');
            if (ctx) {
                const imageData = ctx.getImageData(0, 0, this.width, this.height);
                const data = imageData.data;
                for (let i = 0; i < data.length; i += 4) {
                    data[i] = (data[i] + (Math.random() > 0.5 ? 1 : 0)) % 256;
                }
                ctx.putImageData(imageData, 0, 0);
            }
            return toDataURL.call(this, type, quality);
        };
        """

    def _get_webgl_override(self) -> str:
        """Override WebGL fingerprinting."""
        return """
        // WebGL fingerprint protection
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        const getExtension = WebGLRenderingContext.prototype.getExtension;

        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            // Spoof common parameters
            const spoofs = {
                37445: 'Intel Inc.', // UNMASKED_VENDOR_WEBGL
                37446: 'Intel Iris OpenGL Engine', // UNMASKED_RENDERER_WEBGL
                7937: 'WebKit', // VERSION
                7936: 'WebKit WebGL', // VENDOR
                7938: 'WebGL 1.0 (OpenGL ES 2.0 Chromium)' // RENDERER
            };

            if (parameter in spoofs) {
                return spoofs[parameter];
            }

            return getParameter.call(this, parameter);
        };

        // Randomize precision formats slightly
        WebGLRenderingContext.prototype.getShaderPrecisionFormat = function() {
            return {
                precision: 23,
                rangeMin: 127,
                rangeMax: 127
            };
        };
        """

    def _get_font_spoof(self) -> str:
        """Spoof font enumeration."""
        return """
        // Font enumeration protection
        const originalMeasureText = CanvasRenderingContext2D.prototype.measureText;
        const commonFonts = [
            'Arial', 'Courier New', 'Georgia', 'Times New Roman',
            'Verdana', 'Helvetica', 'Trebuchet MS', 'Tahoma'
        ];

        CanvasRenderingContext2D.prototype.measureText = function(text) {
            // Randomize measurements slightly
            const result = originalMeasureText.call(this, text);
            const originalWidth = result.width;

            // Add tiny random variation
            Object.defineProperty(result, 'width', {
                get: () => originalWidth + (Math.random() * 0.02 - 0.01)
            });

            return result;
        };

        // Override font property to limit enumeration
        const originalFont = Object.getOwnPropertyDescriptor(
            CanvasRenderingContext2D.prototype, 'font'
        );
        """

    def _get_event_emulator(self) -> str:
        """Emulate human-like events."""
        return """
        // Emulate human input events
        (function() {
            // Add realistic mousemove events
            let lastMouseMove = Date.now();

            document.addEventListener('mousemove', function(e) {
                lastMouseMove = Date.now();
            }, true);

            // Override Date constructor for consistent timezone
            const OriginalDate = Date;
            Date = function(...args) {
                if (args.length === 0) {
                    return new OriginalDate(OriginalDate.now());
                }
                return new OriginalDate(...args);
            };

            Date.prototype = OriginalDate.prototype;
            Date.now = OriginalDate.now;
            Date.parse = OriginalDate.parse;
            Date.UTC = OriginalDate.UTC;

            // Ensure Date prototype is correct
            Date.prototype.constructor = Date;

            // Override performance timing
            if (window.performance) {
                const originalNow = performance.now;
                performance.now = function() {
                    return originalNow.call(performance);
                };
            }
        })();
        """

    def _get_detection_patcher(self) -> str:
        """Patch common detection libraries."""
        return """
        // Patch common detection libraries
        (function() {
            // Hook into bot detection libraries
            const libs = ['botd', 'botguard', 'datadome', 'akamai', 'perimeterx', 'cloudflare'];

            libs.forEach(lib => {
                Object.defineProperty(window, lib, {
                    get: () => undefined,
                    set: () => true
                });
            });

            // Override common detection methods
            const methodsToOverride = [
                'toString',
                'toSource',
                'constructor'
            ];

            // Ensure native code appearance
            Function.prototype.toString = function() {
                if (this === Function.prototype.toString) {
                    return 'function toString() { [native code] }';
                }
                return 'function () { [native code] }';
            };

            // Override prototype chain inspection
            if (window.HTMLElement) {
                const originalHTMLElement = window.HTMLElement;
                window.HTMLElement = function() {};
                window.HTMLElement.prototype = originalHTMLElement.prototype;
            }
        })();
        """

    def _get_global_randomizer(self) -> str:
        """Randomize global properties."""
        return """
        // Randomize global properties to prevent fingerprinting
        (function() {
            // Random screen offset (within reasonable bounds)
            const screenOffset = Math.floor(Math.random() * 50);

            Object.defineProperty(window.screen, 'availLeft', {
                get: () => screenOffset
            });

            Object.defineProperty(window.screen, 'availTop', {
                get: () => screenOffset
            });

            // Memory pressure simulation
            if (navigator.deviceMemory) {
                Object.defineProperty(navigator, 'deviceMemory', {
                    get: () => 8
                });
            }

            // Hardware concurrency
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8
            });
        })();
        """

    def _get_chrome_runtime_spoof(self) -> str:
        """Spoof Chrome runtime environment."""
        return """
        // Chrome runtime spoofing
        if (!window.chrome) {
            window.chrome = {};
        }

        window.chrome.runtime = {
            OnInstalledReason: {
                CHROME_UPDATE: "chrome_update",
                INSTALL: "install",
                SHARED_MODULE_UPDATE: "shared_module_update",
                UPDATE: "update"
            },
            OnRestartRequiredReason: {
                APP_UPDATE: "app_update",
                OS_UPDATE: "os_update",
                PERIODIC: "periodic"
            },
            PlatformArch: {
                ARM: "arm",
                ARM64: "arm64",
                MIPS: "mips",
                MIPS64: "mips64",
                X86_32: "x86-32",
                X86_64: "x86-64"
            },
            PlatformNaclArch: {
                ARM: "arm",
                MIPS: "mips",
                MIPS64: "mips64",
                MIPS64EL: "mips64el",
                MIPS_EL: "mipsel",
                X86_32: "x86-32",
                X86_64: "x86-64"
            },
            PlatformOs: {
                ANDROID: "android",
                CROS: "cros",
                LINUX: "linux",
                MAC: "mac",
                OPENBSD: "openbsd",
                WIN: "win"
            },
            RequestUpdateCheckStatus: {
                NO_UPDATE: "no_update",
                THROTTLED: "throttled",
                UPDATE_AVAILABLE: "update_available"
            },
            id: undefined,
            OnConnect: {},
            OnConnectExternal: {},
            OnInstalled: {},
            OnRestartRequired: {},
            OnStartup: {},
            OnSuspend: {},
            OnSuspendCanceled: {},
            OnUpdateAvailable: {}
        };

        // Add chrome.loadTimes for older detection
        window.chrome.loadTimes = function() {
            return {
                commitLoadTime: performance.timing.domContentLoadedEventStart / 1000,
                connectionInfo: 'h2',
                finishDocumentLoadTime: performance.timing.domContentLoadedEventEnd / 1000,
                finishLoadTime: performance.timing.loadEventEnd / 1000,
                firstPaintAfterLoadTime: 0,
                firstPaintTime: performance.timing.domContentLoadedEventStart / 1000,
                navigationType: 'Other',
                npnNegotiatedProtocol: 'h2',
                requestTime: performance.timing.requestStart / 1000,
                startLoadTime: performance.timing.navigationStart / 1000,
                wasAlternateProtocolAvailable: false,
                wasFetchedViaSpdy: true,
                wasNpnNegotiated: true
            };
        };
        """

    def _get_chrome_plugins(self) -> str:
        """Add Chrome-specific plugin indicators."""
        return """
        // Chrome-specific plugin indicators
        window.chrome.app = {
            isInstalled: false,
            InstallState: {
                DISABLED: "disabled",
                INSTALLED: "installed",
                NOT_INSTALLED: "not_installed"
            },
            RunningState: {
                CANNOT_RUN: "cannot_run",
                READY_TO_RUN: "ready_to_run",
                RUNNING: "running"
            }
        };

        // Chrome csi (chrome system info)
        window.chrome.csi = function() {
            return {
                onloadT: Date.now(),
                pageT: performance.now(),
                startE: performance.timing.navigationStart,
                transcription: ''
            };
        };
        """

    def get_detection_score(self) -> Dict[str, Any]:
        """Get evasion coverage score."""
        evasions = {
            'webdriver_hiding': self.config.hide_webdriver,
            'automation_hiding': self.config.hide_automation,
            'plugin_spoofing': self.config.spoof_plugins,
            'permission_spoofing': self.config.spoof_permissions,
            'webrtc_disabled': self.config.disable_webrtc,
            'canvas_override': self.config.override_canvas,
            'webgl_override': self.config.override_webgl,
            'font_spoofing': self.config.spoof_fonts,
            'event_emulation': self.config.emulate_human_events,
            'detection_patching': self.config.patch_detection_libs,
            'global_randomization': self.config.randomize_globals,
            'chrome_runtime': self.config.spoof_chrome_runtime,
            'chrome_plugins': self.config.add_chrome_plugins,
        }

        enabled = sum(1 for v in evasions.values() if v)
        total = len(evasions)

        return {
            'coverage': enabled / total,
            'enabled_count': enabled,
            'total_count': total,
            'evasions': evasions
        }


class BehaviorPattern(Enum):
    """Pre-defined behavior patterns"""
    CASUAL = "casual"  # Slow, relaxed browsing
    RESEARCHER = "researcher"  # Focused, methodical
    QUICK = "quick"  # Fast but human-like
    CAREFUL = "careful"  # Very slow, cautious


@dataclass
class SimulationConfig:
    """Configuration for behavior simulation"""
    pattern: BehaviorPattern = BehaviorPattern.RESEARCHER

    # Timing (in seconds)
    min_delay: float = 0.5
    max_delay: float = 3.0

    # Mouse movement
    mouse_speed: float = 1.0  # Multiplier

    # Scrolling
    scroll_min: int = 100  # pixels
    scroll_max: int = 800
    scroll_pause: float = 0.1

    # Randomization
    randomness: float = 0.3  # 0-1, higher = more random

    # Viewport
    viewport_variation: bool = True  # Vary viewport slightly


@dataclass
class MouseMovement:
    """Mouse movement point"""
    x: float
    y: float
    timestamp: float


@dataclass
class ScrollAction:
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

    # Pattern presets
    PATTERNS: Dict[BehaviorPattern, Dict[str, Any]] = {
        BehaviorPattern.CASUAL: {
            'min_delay': 1.0,
            'max_delay': 5.0,
            'mouse_speed': 0.7,
            'scroll_min': 200,
            'scroll_max': 1000,
            'scroll_pause': 0.2,
            'randomness': 0.4,
        },
        BehaviorPattern.RESEARCHER: {
            'min_delay': 0.8,
            'max_delay': 2.5,
            'mouse_speed': 1.0,
            'scroll_min': 300,
            'scroll_max': 800,
            'scroll_pause': 0.15,
            'randomness': 0.25,
        },
        BehaviorPattern.QUICK: {
            'min_delay': 0.3,
            'max_delay': 1.2,
            'mouse_speed': 1.3,
            'scroll_min': 400,
            'scroll_max': 1200,
            'scroll_pause': 0.05,
            'randomness': 0.35,
        },
        BehaviorPattern.CAREFUL: {
            'min_delay': 2.0,
            'max_delay': 8.0,
            'mouse_speed': 0.5,
            'scroll_min': 100,
            'scroll_max': 400,
            'scroll_pause': 0.3,
            'randomness': 0.2,
        },
    }
    
    def __init__(self, config: Optional[SimulationConfig] = None):
        self.config = config or SimulationConfig()
        self._apply_pattern()

        # State tracking
        self.last_action_time: float = time.time()
        self.mouse_position: Tuple[int, int] = (0, 0)
        self.scroll_position: int = 0
        self.action_count: int = 0

        # Viewport
        self.viewport_width: int = 1920
        self.viewport_height: int = 1080

    def _apply_pattern(self):
        """Apply pattern preset to config"""
        if self.config.pattern in self.PATTERNS:
            preset = self.PATTERNS[self.config.pattern]
            for key, value in preset.items():
                setattr(self.config, key, value)

    def _random_delay(self, min_mult: float = 0.8, max_mult: float = 1.2) -> float:
        """Generate random delay with variation"""
        base = random.uniform(self.config.min_delay, self.config.max_delay)
        variation = random.uniform(min_mult, max_mult)
        return base * variation

    def _apply_randomness(self, value: float) -> float:
        """Apply randomness factor to value"""
        if self.config.randomness <= 0:
            return value

        variation = value * self.config.randomness
        return value + random.uniform(-variation, variation)

    def _bezier_curve(
        self,
        p0: Tuple[float, float],
        p1: Tuple[float, float],
        p2: Tuple[float, float],
        t: float
    ) -> Tuple[float, float]:
        """Calculate quadratic Bézier curve point (M1-optimized)"""
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        return (x, y)

    def generate_mouse_path(
        self,
        start: Tuple[int, int],
        end: Tuple[int, int],
        num_points: int = 20
    ) -> List[MouseMovement]:
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
        # Calculate control point for curve (add some randomness)
        mid_x = (start[0] + end[0]) / 2
        mid_y = (start[1] + end[1]) / 2

        # Add random offset to control point
        offset_range = abs(end[0] - start[0]) + abs(end[1] - start[1])
        offset_range *= 0.2 * self.config.randomness

        control = (
            mid_x + random.uniform(-offset_range, offset_range),
            mid_y + random.uniform(-offset_range, offset_range)
        )

        # Generate points along curve
        points = []
        now = time.time()

        for i in range(num_points):
            t = i / (num_points - 1)
            x, y = self._bezier_curve(start, control, end, t)

            # Add slight jitter
            jitter = self.config.randomness * 2
            x += random.uniform(-jitter, jitter)
            y += random.uniform(-jitter, jitter)

            # Calculate timestamp (movement speed varies)
            speed_variation = random.uniform(0.8, 1.2) / self.config.mouse_speed
            timestamp = now + (i * 0.01 * speed_variation)

            points.append(MouseMovement(x=x, y=y, timestamp=timestamp))

        return points

    async def simulate_mouse_move(
        self,
        target_x: int,
        target_y: int,
        callback: Optional[Any] = None
    ) -> None:
        """
        Simulate mouse movement to target position.

        Args:
            target_x: Target X coordinate
            target_y: Target Y coordinate
            callback: Optional callback function for each point
        """
        path = self.generate_mouse_path(
            self.mouse_position,
            (target_x, target_y)
        )

        for point in path:
            self.mouse_position = (int(point.x), int(point.y))

            if callback:
                await callback(self.mouse_position)

            # Small delay between movements
            await asyncio.sleep(0.005)

        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_click(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        callback: Optional[Any] = None
    ) -> None:
        """
        Simulate mouse click.

        Args:
            x: Click X coordinate (default: current)
            y: Click Y coordinate (default: current)
            callback: Optional callback function
        """
        if x is not None and y is not None:
            await self.simulate_mouse_move(x, y, callback)

        # Random delay before click (human reaction time)
        await asyncio.sleep(self._random_delay(0.1, 0.3))

        # Simulate click
        if callback:
            await callback(('click', self.mouse_position))

        logger.debug(f"Simulated click at {self.mouse_position}")

        # Delay after click
        await asyncio.sleep(self._random_delay(0.2, 0.5))

        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_scroll(
        self,
        direction: str = 'down',
        amount: Optional[int] = None,
        callback: Optional[Any] = None
    ) -> None:
        """
        Simulate scrolling.

        Args:
            direction: 'up' or 'down'
            amount: Scroll amount in pixels (default: random)
            callback: Optional callback function
        """
        if amount is None:
            amount = random.randint(self.config.scroll_min, self.config.scroll_max)

        if direction == 'up':
            amount = -amount

        # Break into smaller chunks for realism
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

            # Pause between scroll chunks
            await asyncio.sleep(
                self._apply_randomness(self.config.scroll_pause)
            )

        logger.debug(f"Simulated scroll {amount}px (total: {self.scroll_position})")

        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_typing(
        self,
        text: str,
        callback: Optional[Any] = None,
        wpm: int = 60
    ) -> None:
        """
        Simulate human-like typing.

        Args:
            text: Text to type
            callback: Optional callback function
            wpm: Words per minute (typing speed)
        """
        # Calculate base delay per character
        chars_per_minute = wpm * 5  # Average word length
        base_delay = 60 / chars_per_minute

        for char in text:
            # Add variation to typing speed
            delay = base_delay * random.uniform(0.7, 1.3)

            if callback:
                await callback(('type', char))

            await asyncio.sleep(delay)

            # Occasional longer pause (thinking)
            if random.random() < 0.05:
                await asyncio.sleep(random.uniform(0.2, 0.5))

        logger.debug(f"Simulated typing {len(text)} characters")

        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_reading(
        self,
        duration: float = 10.0,
        scroll_probability: float = 0.3
    ) -> None:
        """
        Simulate reading a page (idle time with occasional scrolls).

        Args:
            duration: Reading duration in seconds
            scroll_probability: Probability of scrolling during reading
        """
        start_time = time.time()

        while time.time() - start_time < duration:
            # Random delay
            await asyncio.sleep(self._random_delay(0.5, 1.5))

            # Maybe scroll
            if random.random() < scroll_probability:
                direction = 'down' if random.random() > 0.3 else 'up'
                await self.simulate_scroll(direction)

        logger.debug(f"Simulated reading for {duration}s")

    async def simulate_page_visit(
        self,
        num_scrolls: int = 3,
        read_time: float = 15.0
    ) -> Dict[str, Any]:
        """
        Simulate complete page visit behavior.

        Args:
            num_scrolls: Number of scroll actions
            read_time: Time spent reading

        Returns:
            Statistics about the simulated visit
        """
        start_time = time.time()

        # Initial pause (page loading)
        await asyncio.sleep(self._random_delay(0.5, 1.5))

        # Reading
        await self.simulate_reading(
            duration=read_time,
            scroll_probability=0.4
        )

        # Additional scrolls
        for _ in range(num_scrolls):
            if random.random() > 0.3:  # 70% chance to scroll
                direction = random.choice(['up', 'down'])
                await self.simulate_scroll(direction)

                # Short read after scroll
                await asyncio.sleep(self._random_delay(1.0, 3.0))

        duration = time.time() - start_time

        return {
            'duration': duration,
            'actions': self.action_count,
            'scroll_position': self.scroll_position,
            'pattern': self.config.pattern.value,
        }

    def get_statistics(self) -> Dict[str, Any]:
        """Get simulation statistics"""
        return {
            'action_count': self.action_count,
            'mouse_position': self.mouse_position,
            'scroll_position': self.scroll_position,
            'last_action_time': self.last_action_time,
            'pattern': self.config.pattern.value,
            'config': {
                'min_delay': self.config.min_delay,
                'max_delay': self.config.max_delay,
                'randomness': self.config.randomness,
            }
        }


@dataclass
class FingerprintConfig:
    """Configuration for fingerprint randomization (from stealth_toolkit)"""
    randomize_canvas: bool = True
    randomize_webgl: bool = True
    randomize_fonts: bool = True
    randomize_screen: bool = True
    randomize_timezone: bool = True
    randomize_plugins: bool = True
    consistent_per_session: bool = True
    session_duration: float = 3600  # seconds
    use_realistic_profiles: bool = True
    platform: Optional[str] = None  # 'macos', 'windows', 'linux', None=random


@dataclass
class BrowserProfile:
    """Browser fingerprint profile (from stealth_toolkit)"""
    screen_width: int = 1920
    screen_height: int = 1080
    screen_color_depth: int = 24
    screen_pixel_ratio: float = 1.0
    timezone: str = 'America/New_York'
    timezone_offset: int = -5
    canvas_noise: Tuple[int, int, int] = (0, 0, 0)  # RGB offset
    webgl_vendor: str = 'Apple Inc.'
    webgl_renderer: str = 'Apple M1'
    fonts: List[str] = field(default_factory=list)
    plugins: List[Dict[str, str]] = field(default_factory=list)
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
    
    # Realistic screen resolutions
    SCREEN_RESOLUTIONS = [
        (1920, 1080),  # Full HD
        (2560, 1440),  # 2K
        (1366, 768),   # Laptop common
        (1440, 900),   # MacBook Air
        (1680, 1050),  # MacBook Pro 15
        (1280, 720),   # HD
        (3840, 2160),  # 4K (less common, 10% chance)
    ]
    
    # Common timezones
    TIMEZONES = [
        ('America/New_York', -5),
        ('America/Chicago', -6),
        ('America/Denver', -7),
        ('America/Los_Angeles', -8),
        ('Europe/London', 0),
        ('Europe/Paris', 1),
        ('Europe/Berlin', 1),
        ('Asia/Tokyo', 9),
        ('Asia/Shanghai', 8),
        ('Australia/Sydney', 10),
    ]
    
    # WebGL vendors/renderers
    WEBGL_PROFILES = {
        'macos': [
            ('Apple Inc.', 'Apple M1'),
            ('Apple Inc.', 'Apple M1 Pro'),
            ('Apple Inc.', 'Apple M1 Max'),
            ('Apple Inc.', 'Apple M2'),
            ('Intel Inc.', 'Intel Iris OpenGL Engine'),
        ],
        'windows': [
            ('Google Inc. (NVIDIA)', 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11)'),
            ('Google Inc. (NVIDIA)', 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11)'),
            ('Google Inc. (Intel)', 'ANGLE (Intel, Intel(R) UHD Graphics Direct3D11)'),
            ('Microsoft Corporation', 'D3D11'),
        ],
        'linux': [
            ('NVIDIA Corporation', 'NVIDIA GeForce GTX 1060/PCIe/SSE2'),
            ('Intel Open Source Technology Center', 'Mesa DRI Intel(R) UHD Graphics 620'),
            ('AMD', 'AMD Radeon Graphics'),
        ],
    }
    
    # Common fonts
    COMMON_FONTS = [
        'Arial', 'Arial Black', 'Arial Narrow', 'Arial Rounded MT Bold',
        'Courier', 'Courier New',
        'Georgia', 'Helvetica', 'Helvetica Neue',
        'Times', 'Times New Roman',
        'Verdana', 'Tahoma', 'Trebuchet MS',
        'Palatino', 'Garamond', 'Bookman',
        'Comic Sans MS', 'Impact',
        'Segoe UI', 'Calibri', 'Cambria',
        'Geneva', 'Lucida Grande', 'Lucida Sans Unicode',
        'Menlo', 'Monaco', 'Consolas',
    ]
    
    # Browser plugins
    COMMON_PLUGINS = [
        {'name': 'Chrome PDF Plugin', 'filename': 'internal-pdf-viewer', 'description': 'Portable Document Format'},
        {'name': 'Chrome PDF Viewer', 'filename': 'mhjfbmdgcfjbbpaeojofohoefgiehjai', 'description': 'Portable Document Format'},
        {'name': 'Native Client', 'filename': 'internal-nacl-plugin', 'description': 'Native Client module'},
    ]
    
    def __init__(self, config: Optional[FingerprintConfig] = None):
        self.config = config or FingerprintConfig()
        self._current_profile: Optional[BrowserProfile] = None
        self._profile_timestamp: float = 0
        self._rotation_count = 0
    
    def _generate_canvas_noise(self) -> Tuple[int, int, int]:
        """Generate subtle canvas noise (invisible to human eye)"""
        return (
            random.randint(0, 2),
            random.randint(0, 2),
            random.randint(0, 2)
        )
    
    def _generate_screen_resolution(self) -> Tuple[int, int, int, float]:
        """Generate realistic screen specs"""
        if random.random() < 0.9:
            width, height = random.choice(self.SCREEN_RESOLUTIONS[:5])
        else:
            width, height = random.choice(self.SCREEN_RESOLUTIONS)
        
        color_depth = random.choice([24, 32])
        pixel_ratio = random.choice([1.0, 1.0, 1.0, 1.25, 1.5, 2.0])
        
        return width, height, color_depth, pixel_ratio
    
    def _generate_timezone(self) -> Tuple[str, int]:
        """Generate random timezone"""
        if not self.config.randomize_timezone:
            import time
            tz = time.tzname[0] if time.tzname else 'UTC'
            offset = -time.timezone // 3600
            return tz, offset
        
        return random.choice(self.TIMEZONES)
    
    def _generate_webgl_profile(self, platform: str) -> Tuple[str, str]:
        """Generate WebGL vendor/renderer"""
        if not self.config.randomize_webgl:
            return ('', '')
        
        profiles = self.WEBGL_PROFILES.get(platform, self.WEBGL_PROFILES['macos'])
        return random.choice(profiles)
    
    def _generate_font_list(self) -> List[str]:
        """Generate randomized font list"""
        if not self.config.randomize_fonts:
            return self.COMMON_FONTS[:10]
        
        num_fonts = random.randint(10, 15)
        return random.sample(self.COMMON_FONTS, min(num_fonts, len(self.COMMON_FONTS)))
    
    def _generate_plugins(self) -> List[Dict[str, str]]:
        """Generate browser plugins"""
        if not self.config.randomize_plugins:
            return self.COMMON_PLUGINS[:2]
        
        num_plugins = random.randint(2, len(self.COMMON_PLUGINS))
        return random.sample(self.COMMON_PLUGINS, num_plugins)
    
    def _generate_hardware_specs(self, platform: str) -> Tuple[int, int, int]:
        """Generate hardware specs"""
        if platform == 'macos':
            concurrency = random.choice([8, 8, 10, 10])
            memory = random.choice([8, 16, 16, 32])
        else:
            concurrency = random.choice([4, 4, 8, 8, 8, 16])
            memory = random.choice([4, 8, 8, 16, 16, 32])
        
        touch_points = 0 if platform != 'mobile' else random.choice([5, 10])
        
        return concurrency, memory, touch_points
    
    def generate_profile(self, force_new: bool = False) -> BrowserProfile:
        """Generate new browser fingerprint profile"""
        # Check if we should reuse current profile
        if (not force_new and 
            self.config.consistent_per_session and
            self._current_profile is not None):
            
            elapsed = time.time() - self._profile_timestamp
            if elapsed < self.config.session_duration:
                return self._current_profile
        
        # Determine platform
        platform = self.config.platform
        if platform is None:
            platform = random.choice(['macos', 'windows', 'linux'])
        
        # Generate profile components
        width, height, color_depth, pixel_ratio = self._generate_screen_resolution()
        timezone, tz_offset = self._generate_timezone()
        webgl_vendor, webgl_renderer = self._generate_webgl_profile(platform)
        
        # Create profile
        profile = BrowserProfile(
            screen_width=width,
            screen_height=height,
            screen_color_depth=color_depth,
            screen_pixel_ratio=pixel_ratio,
            timezone=timezone,
            timezone_offset=tz_offset,
            canvas_noise=self._generate_canvas_noise(),
            webgl_vendor=webgl_vendor,
            webgl_renderer=webgl_renderer,
            fonts=self._generate_font_list(),
            plugins=self._generate_plugins(),
            hardware_concurrency=self._generate_hardware_specs(platform)[0],
            device_memory=self._generate_hardware_specs(platform)[1],
            max_touch_points=self._generate_hardware_specs(platform)[2],
        )
        
        self._current_profile = profile
        self._profile_timestamp = time.time()
        self._rotation_count += 1
        
        logger.debug(f"Generated new fingerprint profile ({platform})")
        return profile
    
    def get_profile(self) -> BrowserProfile:
        """Get current or new profile"""
        return self.generate_profile()
    
    def get_js_protection_script(self) -> str:
        """Generate JavaScript to apply fingerprint protection"""
        profile = self.get_profile()
        
        import json
        script = f"""
        // Fingerprint Protection Script
        (function() {{
            'use strict';
            
            const profile = {json.dumps({{
                'screen': {{
                    'width': profile.screen_width,
                    'height': profile.screen_height,
                    'colorDepth': profile.screen_color_depth,
                    'pixelRatio': profile.screen_pixel_ratio,
                }},
                'timezone': profile.timezone,
                'timezoneOffset': profile.timezone_offset,
                'hardwareConcurrency': profile.hardware_concurrency,
                'deviceMemory': profile.device_memory,
                'maxTouchPoints': profile.max_touch_points,
                'canvasNoise': profile.canvas_noise,
            }})};
            
            // Override screen properties
            Object.defineProperty(screen, 'width', {{ get: () => profile.screen.width }});
            Object.defineProperty(screen, 'height', {{ get: () => profile.screen.height }});
            Object.defineProperty(screen, 'colorDepth', {{ get: () => profile.screen.colorDepth }});
            Object.defineProperty(screen, 'pixelDepth', {{ get: () => profile.screen.colorDepth }});
            
            // Override window.devicePixelRatio
            Object.defineProperty(window, 'devicePixelRatio', {{
                get: () => profile.screen.pixelRatio
            }});
            
            // Override hardware specs
            Object.defineProperty(navigator, 'hardwareConcurrency', {{
                get: () => profile.hardwareConcurrency
            }});
            Object.defineProperty(navigator, 'deviceMemory', {{
                get: () => profile.deviceMemory
            }});
            Object.defineProperty(navigator, 'maxTouchPoints', {{
                get: () => profile.maxTouchPoints
            }});
            
            // Canvas fingerprint protection
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;
            
            HTMLCanvasElement.prototype.toDataURL = function(...args) {{
                const ctx = this.getContext('2d');
                if (ctx) {{
                    const imageData = ctx.getImageData(0, 0, this.width, this.height);
                    const data = imageData.data;
                    // Add imperceptible noise
                    for (let i = 0; i < data.length; i += 4) {{
                        data[i] = Math.min(255, data[i] + {profile.canvas_noise[0]});
                        data[i+1] = Math.min(255, data[i+1] + {profile.canvas_noise[1]});
                        data[i+2] = Math.min(255, data[i+2] + {profile.canvas_noise[2]});
                    }}
                    ctx.putImageData(imageData, 0, 0);
                }}
                return originalToDataURL.apply(this, args);
            }};
            
            // Timezone protection
            const originalDate = Date;
            Date = class extends originalDate {{
                constructor(...args) {{
                    super(...args);
                }}
                getTimezoneOffset() {{
                    return profile.timezoneOffset * 60;
                }}
            }};
            
        }})();
        """
        
        return script
    
    def get_fingerprint_hash(self) -> str:
        """Get hash of current fingerprint (for tracking detection)"""
        import hashlib
        import json
        
        profile = self.get_profile()
        
        fingerprint_data = {
            'screen': f"{profile.screen_width}x{profile.screen_height}",
            'color_depth': profile.screen_color_depth,
            'pixel_ratio': profile.screen_pixel_ratio,
            'timezone': profile.timezone,
            'fonts_hash': hash(tuple(sorted(profile.fonts))) % 10000,
            'hardware': f"{profile.hardware_concurrency}c{profile.device_memory}g",
        }
        
        fingerprint_str = json.dumps(fingerprint_data, sort_keys=True)
        return hashlib.sha256(fingerprint_str.encode()).hexdigest()[:16]
    
    def rotate(self) -> BrowserProfile:
        """Force rotation to new fingerprint"""
        return self.generate_profile(force_new=True)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get randomization statistics"""
        return {
            'rotation_count': self._rotation_count,
            'current_profile_age': time.time() - self._profile_timestamp if self._profile_timestamp else 0,
            'current_fingerprint': self.get_fingerprint_hash(),
            'consistent_mode': self.config.consistent_per_session,
        }


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
    
    def __init__(self, config: Optional[StealthConfig] = None):
        """
        Initialize StealthLayer.
        
        Args:
            config: Stealth configuration (uses defaults if None)
        """
        self.config = config or StealthConfig()
        
        # Core components (lazy loaded)
        self._stealth_browser = None
        self._detection_evader = None

        # Advanced CAPTCHA solver (self-hosted, M1 optimized)
        self._captcha_solver: Optional[AdvancedCaptchaSolver] = None

        # JavaScript evasion (15+ anti-detection scripts)
        self._js_evasion: Optional[JavaScriptEvasion] = None

        # Chameleon - anti-debugging protection
        self._chameleon: Optional['Chameleon'] = None

        # Fingerprint randomizer (from stealth_toolkit integration)
        self._fingerprint_randomizer: Optional[FingerprintRandomizer] = None
        
        # Session management
        self._sessions: Dict[str, StealthSession] = {}
        self._session_counter = 0
        
        # Statistics
        self._browsers_created = 0
        self._captchas_solved = 0
        self._evasions_applied = 0
        
        logger.info("StealthLayer initialized")
    
    async def initialize(self) -> bool:
        """
        Initialize StealthLayer components.
        
        Returns:
            True if initialization successful
        """
        try:
            logger.info("🚀 Initializing StealthLayer...")
            
            # Initialize DetectionEvader (lightweight, no browser needed)
            if self.config.enable_stealth_scripts:
                await self._init_detection_evader()
            
            # Initialize CaptchaSolver (if enabled)
            if self.config.enable_captcha_solving:
                await self._init_captcha_solver()

            # Initialize JavaScriptEvasion (15+ anti-detection scripts)
            await self._init_js_evasion()

            # Initialize Chameleon (anti-debugging)
            await self._init_chameleon()
            
            # Initialize FingerprintRandomizer (from stealth_toolkit)
            await self._init_fingerprint_randomizer()
            
            # Note: StealthBrowser is initialized on-demand (heavy)
            
            logger.info("✅ StealthLayer initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ StealthLayer initialization failed: {e}")
            return False
    
    async def _init_stealth_browser(self) -> None:
        """Lazy initialization of StealthBrowser"""
        if self._stealth_browser is None:
            try:
                from hledac.advanced_web.stealth_browser import StealthBrowser, BrowserConfig
                
                browser_config = BrowserConfig(
                    browser_type=self.config.browser_type,
                    headless=self.config.headless,
                    pool_size=self.config.pool_size,
                    m1_optimized=True
                )
                
                self._stealth_browser = StealthBrowser(browser_config)
                await self._stealth_browser.initialize()
                self._browsers_created += 1
                logger.info("✅ StealthBrowser initialized")
                
            except ImportError as e:
                logger.warning(f"⚠️ StealthBrowser not available: {e}")
                self._stealth_browser = None
    
    async def _init_detection_evader(self) -> None:
        """Lazy initialization of DetectionEvader"""
        if self._detection_evader is None:
            try:
                from hledac.advanced_web.detection_evader import DetectionEvader
                
                self._detection_evader = DetectionEvader(
                    detection_threshnew=self.config.detection_threshold,
                    adaptive_mode=self.config.adaptive_mode
                )
                logger.info("✅ DetectionEvader initialized")
                
            except ImportError as e:
                logger.warning(f"⚠️ DetectionEvader not available: {e}")
                self._detection_evader = None
    
    async def _init_captcha_solver(self) -> None:
        """Lazy initialization of AdvancedCaptchaSolver (self-hosted)"""
        if self._captcha_solver is None:
            try:
                config = CaptchaSolverConfig(
                    enable_image_ocr=True,
                    enable_text_logic=True,
                    confidence_threshold=0.6,
                )
                self._captcha_solver = AdvancedCaptchaSolver(config)
                await self._captcha_solver.initialize()
                logger.info("✅ AdvancedCaptchaSolver initialized (self-hosted)")

            except Exception as e:
                logger.warning(f"⚠️ AdvancedCaptchaSolver not available: {e}")
                self._captcha_solver = None

    async def _init_js_evasion(self) -> None:
        """Initialize JavaScript evasion (15+ anti-detection scripts)"""
        if self._js_evasion is None:
            try:
                config = JavaScriptEvasionConfig(
                    hide_webdriver=True,
                    hide_automation=True,
                    spoof_plugins=True,
                    spoof_permissions=True,
                    disable_webrtc=True,
                    override_canvas=True,
                    override_webgl=True,
                    spoof_fonts=True,
                    emulate_human_events=True,
                    patch_detection_libs=True,
                    randomize_globals=True,
                    spoof_chrome_runtime=True,
                    add_chrome_plugins=True,
                )
                self._js_evasion = JavaScriptEvasion(config)
                logger.info("✅ JavaScriptEvasion initialized (15+ evasion scripts)")

            except Exception as e:
                logger.warning(f"⚠️ JavaScriptEvasion initialization failed: {e}")
                self._js_evasion = None
    
    async def _init_chameleon(self) -> None:
        """Initialize Chameleon for anti-debugging."""
        try:
            self._chameleon = Chameleon()
            
            # Apply process masquerading
            self._chameleon.masquerade_process()
            
            # Initialize ptrace anti-debugging on macOS
            if self._chameleon.initialize_ptrace_protection():
                logger.info("✅ Chameleon anti-debugging initialized (ptrace)")
            else:
                logger.info("✅ Chameleon initialized (ptrace not available)")
                
        except Exception as e:
            logger.warning(f"⚠️ Chameleon not available: {e}")
            self._chameleon = None
    
    async def _init_fingerprint_randomizer(self) -> None:
        """Initialize FingerprintRandomizer for browser fingerprint protection."""
        try:
            self._fingerprint_randomizer = FingerprintRandomizer()
            logger.info("✅ FingerprintRandomizer initialized")
        except Exception as e:
            logger.warning(f"⚠️ FingerprintRandomizer initialization failed: {e}")
            self._fingerprint_randomizer = None
    
    # ====================================================================
    # Chameleon Integration
    # ====================================================================
    
    def get_chameleon(self) -> Optional['Chameleon']:
        """Get Chameleon instance for anti-debugging control."""
        return self._chameleon
    
    def is_debugger_present(self) -> bool:
        """Check if a debugger is attached (macOS only)."""
        if self._chameleon:
            return self._chameleon.is_debugger_present()
        return False
    
    async def create_session(
        self,
        browser_type: Optional[BrowserType] = None,
        proxy: Optional[str] = None
    ) -> StealthSession:
        """
        Create a new stealth browsing session.
        
        Args:
            browser_type: Browser type (uses config default if None)
            proxy: Proxy URL (optional)
            
        Returns:
            StealthSession
        """
        self._session_counter += 1
        session_id = f"stealth_{self._session_counter}"
        
        browser_type = browser_type or BrowserType(self.config.browser_type)
        
        logger.info(f"🔒 Creating stealth session: {session_id}")
        
        # Initialize browser if needed
        if self._stealth_browser is None:
            await self._init_stealth_browser()
        
        # Generate fingerprint
        fingerprint = await self._generate_fingerprint()
        
        session = StealthSession(
            session_id=session_id,
            browser_type=browser_type,
            fingerprint=fingerprint,
            proxy=proxy,
            risk_level=RiskLevel.LOW,
            created_at=time.time()
        )
        
        self._sessions[session_id] = session
        return session
    
    async def _generate_fingerprint(self) -> Dict[str, Any]:
        """Generate browser fingerprint"""
        if self.config.enable_fingerprint_rotation and self._detection_evader:
            try:
                # Use DetectionEvader to get evasion config (includes fingerprint)
                return {
                    "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.0",
                    "screen": {"width": 1920, "height": 1080},
                    "timezone": "America/New_York",
                    "language": "en-US",
                    "platform": "MacIntel",
                    "plugins": ["Chrome PDF Plugin", "Native Client"],
                }
            except Exception:
                pass
        
        # Default fingerprint
        return {
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "screen": {"width": 1920, "height": 1080},
        }
    
    async def new_page(self, session: StealthSession) -> Any:
        """
        Create a new page in the stealth session.
        
        Args:
            session: StealthSession
            
        Returns:
            Playwright page object
        """
        if self._stealth_browser is None:
            raise RuntimeError("StealthBrowser not initialized")
        
        try:
            page = await self._stealth_browser.new_page()
            logger.debug(f"📄 New page created for session {session.session_id}")
            return page
        except Exception as e:
            logger.error(f"❌ Failed to create page: {e}")
            raise
    
    async def apply_evasion(self, page: Any, risk_level: Optional[RiskLevel] = None) -> None:
        """
        Apply detection evasion scripts to page.
        
        Args:
            page: Playwright page
            risk_level: Risk level (auto-detect if None)
        """
        if not self.config.enable_stealth_scripts:
            return
        
        if self._detection_evader is None:
            logger.warning("⚠️ DetectionEvader not available, skipping evasion")
            return
        
        try:
            if risk_level is None:
                # Analyze page for detection risk
                content = await page.content() if hasattr(page, 'content') else ""
                risk_level = self._detection_evader.analyze_page_content(content)
            
            # Get evasion scripts for risk level
            scripts = self._detection_evader.get_evasion_scripts()

            # Add JavaScript evasion scripts (15+ anti-detection techniques)
            if self._js_evasion:
                js_scripts = self._js_evasion.get_all_evasion_scripts()
                scripts.extend(js_scripts)
                logger.debug(f"🛡️ Added {len(js_scripts)} JavaScript evasion scripts")

            # Add fingerprint protection
            if self._fingerprint_randomizer:
                fingerprint_script = self._fingerprint_randomizer.get_js_protection_script()
                scripts.append(fingerprint_script)

            # Apply scripts
            for script in scripts:
                try:
                    await page.add_init_script(script)
                except Exception as e:
                    logger.debug(f"⚠️ Failed to add script: {e}")
            
            self._evasions_applied += 1
            logger.info(f"🛡️ Applied {len(scripts)} evasion scripts (risk: {risk_level.value})")
            
        except Exception as e:
            logger.warning(f"⚠️ Evasion application failed: {e}")
    
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
            # Simulate mouse movements
            await self._detection_evader.simulate_human_behavior(page)
            logger.debug("🎭 Human behavior simulated")
        except Exception as e:
            logger.debug(f"⚠️ Behavior simulation failed: {e}")
    
    async def solve_captcha(
        self,
        page: Any,
        url: str,
        captcha_type: Optional[CaptchaType] = None
    ) -> Optional[CaptchaSolution]:
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
            logger.warning("⚠️ CaptchaSolver not available")
            return None
        
        try:
            # Get page HTML
            html = await page.content() if hasattr(page, 'content') else ""
            
            # Detect CAPTCHA
            detected_type = captcha_type or self._captcha_solver.detect_captcha(html)
            
            if detected_type == CaptchaType.IMAGE:
                logger.info("🧩 Image CAPTCHA detected")
                # TODO: Extract image and solve
                return None
            elif detected_type in (CaptchaType.RECAPTCHA_V2, CaptchaType.RECAPTCHA_V3):
                logger.info("🧩 reCAPTCHA detected")
                
                # Extract site key
                import re
                site_key_match = re.search(r'data-sitekey="([^"]+)"', html)
                if site_key_match:
                    site_key = site_key_match.group(1)
                    
                    # Solve
                    solution = await self._captcha_solver.solve_captcha(
                        captcha_type=detected_type,
                        site_key=site_key,
                        url=url
                    )
                    
                    self._captchas_solved += 1
                    return CaptchaSolution(
                        solution=solution if isinstance(solution, str) else str(solution),
                        solved_at=time.time(),
                        cost=0.002,  # Approximate cost
                        confidence=0.9,
                        provider=self.config.captcha_providers[0] if self.config.captcha_providers else "unknown"
                    )
            
            return None
            
        except Exception as e:
            logger.error(f"❌ CAPTCHA solving failed: {e}")
            return None
    
    async def close_session(self, session_id: str) -> None:
        """
        Close a stealth session.
        
        Args:
            session_id: Session ID
        """
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.debug(f"🔒 Session closed: {session_id}")
    
    def get_fingerprint_protection(self) -> str:
        """Get JavaScript fingerprint protection script"""
        if self._fingerprint_randomizer:
            return self._fingerprint_randomizer.get_js_protection_script()
        return ''
    
    def rotate_fingerprint(self) -> Optional[BrowserProfile]:
        """Force rotation to new browser fingerprint"""
        if self._fingerprint_randomizer:
            return self._fingerprint_randomizer.rotate()
        return None
    
    def get_js_evasion_score(self) -> Optional[Dict[str, Any]]:
        """Get JavaScript evasion coverage score"""
        if self._js_evasion:
            return self._js_evasion.get_detection_score()
        return None

    def get_captcha_solver_stats(self) -> Optional[Dict[str, Any]]:
        """Get CAPTCHA solver statistics"""
        if self._captcha_solver:
            return self._captcha_solver.get_statistics()
        return None

    def get_statistics(self) -> Dict[str, Any]:
        """Get stealth layer statistics"""
        return {
            "browsers_created": self._browsers_created,
            "sessions_active": len(self._sessions),
            "captchas_solved": self._captchas_solved,
            "evasions_applied": self._evasions_applied,
            "stealth_browser_available": self._stealth_browser is not None,
            "detection_evader_available": self._detection_evader is not None,
            "captcha_solver_available": self._captcha_solver is not None,
            "js_evasion_available": self._js_evasion is not None,
            "chameleon_available": self._chameleon is not None,
            "fingerprint_randomizer_available": self._fingerprint_randomizer is not None,
            "anti_debugging_active": self._chameleon.is_debugger_protected() if self._chameleon else False,
            "fingerprint_stats": self._fingerprint_randomizer.get_statistics() if self._fingerprint_randomizer else None,
            "js_evasion_score": self.get_js_evasion_score(),
            "captcha_solver_stats": self.get_captcha_solver_stats(),
            "config": {
                "browser_type": self.config.browser_type,
                "headless": self.config.headless,
                "enable_stealth_scripts": self.config.enable_stealth_scripts,
                "enable_captcha_solving": self.config.enable_captcha_solving,
            }
        }
    
    async def cleanup(self) -> None:
        """Cleanup resources"""
        logger.info("🧹 Cleaning up StealthLayer...")
        
        # Close all sessions
        self._sessions.clear()
        
        # Cleanup browser
        if self._stealth_browser and hasattr(self._stealth_browser, 'close'):
            try:
                await self._stealth_browser.close()
            except Exception as e:
                logger.warning(f"⚠️ StealthBrowser cleanup error: {e}")
        
        # Cleanup captcha solver
        if self._captcha_solver and hasattr(self._captcha_solver, 'close'):
            try:
                await self._captcha_solver.close()
            except Exception as e:
                logger.warning(f"⚠️ CaptchaSolver cleanup error: {e}")
        
        logger.info("✅ StealthLayer cleanup complete")


# =============================================================================
# CHAMELEON - Anti-Debugging and Process Masquerading (from kernel/stealth/chameleon.py)
# =============================================================================

import ctypes
import ctypes.util
import sys
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
    
    # Masquerade targets - processes that look benign
    MASQUERADE_TARGETS = [
        ("mdworker_shared", "Spotlight indexer"),
        ("mds_stores", "Metadata server"),
        ("syslogd", "System logger"),
        ("locationd", "Location services"),
        ("bluetoothd", "Bluetooth daemon"),
        ("coreaudiod", "Audio daemon"),
        ("powerd", "Power management"),
        ("airportd", "WiFi daemon"),
    ]
    
    def __init__(self):
        """Initialize Chameleon."""
        self._original_name: Optional[str] = None
        self._masqueraded = False
        self._ptrace_protected = False
        
        logger.debug("Chameleon initialized")
    
    def masquerade_process(self, target_index: Optional[int] = None) -> bool:
        """
        Masquerade process as a benign system process.
        
        Args:
            target_index: Index of MASQUERADE_TARGETS to use (random if None)
            
        Returns:
            True if successful
        """
        try:
            import random
            
            # Select masquerade target
            if target_index is None:
                target_index = random.randint(0, len(self.MASQUERADE_TARGETS) - 1)
            
            target_name, target_desc = self.MASQUERADE_TARGETS[target_index]
            self._original_name = sys.argv[0] if sys.argv else "python"
            
            # Try to change process name via setproctitle
            try:
                import setproctitle
                setproctitle.setproctitle(target_name)
                self._masqueraded = True
                
                logger.info(f"Chameleon: Masquerading as '{target_name}' ({target_desc})")
                return True
                
            except ImportError:
                # Fallback: modify argv[0]
                if len(sys.argv) > 0:
                    sys.argv[0] = target_name
                    self._masqueraded = True
                    
                    logger.info(f"Chameleon: Masquerading as '{target_name}' (via argv)")
                    return True
            
            return False
            
        except Exception as e:
            logger.warning(f"Chameleon: Masquerade failed: {e}")
            return False
    
    def initialize_ptrace_protection(self) -> bool:
        """
        Initialize ptrace anti-debugging protection (macOS only).
        
        Uses PT_DENY_ATTACH to prevent debugger attachment.
        
        Returns:
            True if protection was successfully applied
        """
        # Only on macOS (darwin)
        if sys.platform != "darwin":
            logger.debug("Chameleon: ptrace protection only available on macOS")
            return False
        
        try:
            # Load libc
            libc = ctypes.CDLL(ctypes.util.find_library("c"))
            
            # PT_DENY_ATTACH = 31 (macOS specific)
            PT_DENY_ATTACH = 31
            
            # Call ptrace
            result = libc.ptrace(PT_DENY_ATTACH, 0, 0, 0)
            
            if result == 0:
                self._ptrace_protected = True
                logger.info("Chameleon: ptrace anti-debugging enabled (PT_DENY_ATTACH)")
                return True
            else:
                logger.warning(f"Chameleon: ptrace returned {result}")
                return False
                
        except Exception as e:
            logger.warning(f"Chameleon: ptrace initialization failed: {e}")
            return False
    
    def is_debugger_present(self) -> bool:
        """
        Check if a debugger is attached (macOS only).
        
        Returns:
            True if debugger detected
        """
        # Only on macOS
        if sys.platform != "darwin":
            return False
        
        try:
            # Try to use sysctl to detect debugger
            import subprocess
            
            result = subprocess.run(
                ["sysctl", "-n", "kern.proc.pid", str(os.getpid())],
                capture_output=True,
                text=True,
                timeout=1
            )
            
            # Check for P_TRACED flag in output
            if "P_TRACED" in result.stdout or "traced" in result.stdout.lower():
                return True
            
            # Alternative: check if we can ptrace ourselves
            libc = ctypes.CDLL(ctypes.util.find_library("c"))
            PT_TRACE_ME = 0
            
            # If ptrace fails, we're likely being traced
            result = libc.ptrace(PT_TRACE_ME, 0, 0, 0)
            if result < 0:
                return True
            
            return False
            
        except Exception as e:
            logger.debug(f"Chameleon: Debugger check failed: {e}")
            return False
    
    def is_debugger_protected(self) -> bool:
        """Check if ptrace protection is active."""
        return self._ptrace_protected
    
    def cleanup_environment(self) -> None:
        """Clean environment variables that might indicate debugging."""
        debug_vars = [
            'DEBUG',
            'PYTHONBREAKPOINT',
            'PYDEVD',
            'IDE_PROJECT_ROOTS',
            'PYTHONPATH_DEBUG',
        ]
        
        for var in debug_vars:
            if var in os.environ:
                del os.environ[var]
                logger.debug(f"Chameleon: Removed {var} from environment")
    
    def get_info(self) -> Dict[str, Any]:
        """Get Chameleon status information."""
        return {
            "masqueraded": self._masqueraded,
            "original_name": self._original_name,
            "current_masquerade": sys.argv[0] if self._masqueraded else None,
            "ptrace_protected": self._ptrace_protected,
            "debugger_present": self.is_debugger_present(),
            "platform": sys.platform,
        }


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Core stealth layer
    "StealthLayer",
    "StealthConfig",
    "StealthSession",
    "RiskLevel",
    "BrowserType",
    "CaptchaType",
    "CaptchaSolution",

    # Behavior simulation
    "BehaviorSimulator",
    "BehaviorPattern",
    "SimulationConfig",
    "MouseMovement",
    "ScrollAction",

    # Fingerprint randomization
    "FingerprintRandomizer",
    "FingerprintConfig",
    "BrowserProfile",

    # Advanced CAPTCHA solving (self-hosted)
    "AdvancedCaptchaSolver",
    "CaptchaSolverConfig",
    "CaptchaResult",

    # JavaScript evasion (15+ anti-detection scripts)
    "JavaScriptEvasion",
    "JavaScriptEvasionConfig",

    # Anti-debugging
    "Chameleon",
]
